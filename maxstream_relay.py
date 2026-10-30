#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maxstream_relay.py — jalankan DI JARINGAN INDONESIA (IndiHome/Wi-Fi rumah).

Kenapa di rumah: api.maxstream.tv memblokir IP luar Indonesia (Cloudflare 403),
sedangkan preprod hanya membuka /home. Jadi relay harus jalan dari jaringan yang
sama dengan akun IndiHome kamu.

Fitur
  --discover        probe endpoint & cetak respons mentah (untuk menyesuaikan)
  --login <nomor>   login nomor IndiHome, lalu masukkan OTP
  --token <bearer>  langsung pakai token (kalau sudah punya)
  --serve           buka server lokal: /playlist.m3u, /mpd/<id>, /license/<id>

Tanpa dependency (stdlib saja). Uji: python3 maxstream_relay.py --serve --demo

Contoh
  python3 maxstream_relay.py --discover
  python3 maxstream_relay.py --login 12212345678
  python3 maxstream_relay.py --serve --port 8888

Setelah jalan, di TiviMate/VLC tambahkan:
  http://<IP-PC-kamu>:8888/playlist.m3u
"""
import argparse
import http.server
import json
import os
import re
import socketserver
import ssl
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "maxstream_token.json")

DEFAULT_BASE = "https://api.maxstream.tv"
UA = "okhttp/4.12.0"
COMMON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Device-Platform": "android",
    "X-App-Version": "4.2.2",
    "X-Device-Type": "mobile",
}

# kandidat path (bentuk pastinya belum terverifikasi dari luar Indonesia)
P_HOME = ["/home", "/v1/home", "/api/home"]
P_LOGIN_IH = ["/auth/ih-number/get-account", "/auth/ih/get-account", "/auth/ih-number"]
P_OTP_REQ = ["/auth/otp/request", "/auth/otp/send"]
P_OTP_VER = ["/auth/otp/verify", "/auth/verify", "/auth/login"]
P_CHANNELS = ["/channel/list", "/v1/channel/list", "/channels", "/v1/channels",
              "/live/channel/list", "/content/channel/list"]
P_ENTRY = ["/entry/{id}", "/v1/entry/{id}", "/content/entry/{id}",
           "/channel/{id}", "/v1/channel/{id}", "/live/{id}"]


# --------------------------------------------------------------------------
def api_call(method, url, token=None, payload=None, timeout=25, base=None):
    """Return (status, headers, body_text)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(COMMON_HEADERS))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")
    except Exception as e:                                     # noqa: BLE001
        return 0, {}, f"{type(e).__name__}: {e}"


def save_token(tok, extra=None):
    json.dump({"token": tok, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
               **(extra or {})}, open(TOKEN_FILE, "w"))
    print(f"[+] token disimpan: {TOKEN_FILE}")


def load_token():
    if os.path.exists(TOKEN_FILE):
        try:
            return json.load(open(TOKEN_FILE)).get("token")
        except Exception:                                      # noqa: BLE001
            return None
    return None


# --------------------------------------------------------------------------
def discover(base):
    """Probe semua kandidat endpoint, cetak status + potongan respons."""
    print(f"[*] discover pada {base}\n")
    groups = [("HOME", "GET", P_HOME, None), ("CHANNELS", "GET", P_CHANNELS, None),
              ("LOGIN/IH", "POST", P_LOGIN_IH, {"ih_number": "000"}),
              ("OTP REQ", "POST", P_OTP_REQ, {"msisdn": "000"})]
    works = []
    for label, method, paths, payload in groups:
        print(f"— {label}")
        for p in paths:
            st, hd, body = api_call(method, base + p, payload=payload, base=base)
            short = body[:110].replace("\n", " ")
            print(f"   {method:4} {p:32} {st:4} {short}")
            if st in (200, 400, 401, 403, 422) and "<html" not in body[:200].lower():
                works.append((label, method, p, st))
        print()
    if works:
        print("[+] endpoint yang menjawab JSON (kandidat kuat):")
        for w in works:
            print("   ", w)
    else:
        print("[!] semua balas HTML/403 — cek base URL atau jalankan dari IP Indonesia.")


def login_indihome(base, number):
    """Alur: get-account -> minta OTP -> verifikasi -> token."""
    print(f"[*] cek nomor IndiHome {number}")
    st, hd, body = api_call("POST", base + P_LOGIN_IH[0], payload={"ih_number": number})
    if st == 0 or "<html" in body[:200].lower():
        print(f"[!] gagal hubungi API (status {st}). Jalankan dari jaringan IndiHome.")
        return None
    print(f"   {st} {body[:200]}")

    print("[*] minta OTP")
    otp_sent = False
    for p in P_OTP_REQ:
        for payload in ({"ih_number": number}, {"msisdn": number},
                        {"phone": number}, {"identifier": number}):
            st, hd, body = api_call("POST", base + p, payload=payload)
            if st in (200, 201, 202):
                print(f"   OTP terkirim via {p} -> {body[:120]}")
                otp_sent = True
                break
        if otp_sent:
            break
    if not otp_sent:
        print("[!] permintaan OTP gagal. Coba --token kalau sudah punya token dari app.")
        return None

    code = input("Masukkan kode OTP: ").strip()
    tok = None
    for p in P_OTP_VER:
        for payload in ({"ih_number": number, "otp": code, "code": code},
                        {"identifier": number, "otp": code},
                        {"msisdn": number, "otp": code}):
            st, hd, body = api_call("POST", base + p, payload=payload)
            if st in (200, 201):
                try:
                    j = json.loads(body)
                except Exception:                              # noqa: BLE001
                    j = {}
                tok = (j.get("access_token") or j.get("token")
                       or (j.get("data") or {}).get("access_token")
                       or (j.get("data") or {}).get("token"))
                if tok:
                    print(f"[+] login OK via {p}")
                    save_token(tok, {"base": base, "number": number})
                    return tok
            print(f"   {p} {st} {body[:100]}")
    print("[!] verifikasi OTP gagal. Coba --token.")
    return None


# --------------------------------------------------------------------------
def _walk(node, out, depth=0):
    """Kumpulkan dict yang mirip channel dari JSON bentuk apa pun."""
    if depth > 8:
        return
    if isinstance(node, dict):
        keys = {k.lower() for k in node}
        has_id = any(k in keys for k in ("id", "entryid", "entry_id", "channelid", "channel_id"))
        has_name = any(k in keys for k in ("name", "title", "channelname", "channel_name"))
        if has_id and has_name:
            out.append(node)
        for v in node.values():
            _walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out, depth + 1)


def _pick(d, *names, default=""):
    for n in names:
        for k, v in d.items():
            if k.lower() == n and isinstance(v, (str, int)):
                return v
    return default


def fetch_channels(base, token):
    for p in P_CHANNELS:
        st, hd, body = api_call("GET", base + p, token=token)
        if st != 200:
            continue
        try:
            j = json.loads(body)
        except Exception:                                      # noqa: BLE001
            continue
        found = []
        _walk(j, found)
        chans = []
        seen = set()
        for c in found:
            cid = str(_pick(c, "id", "entryid", "entry_id", "channelid", "channel_id"))
            name = str(_pick(c, "name", "title", "channelname", "channel_name"))
            if not cid or not name or cid in seen:
                continue
            seen.add(cid)
            logo = None
            for k, v in c.items():
                if "logo" in k.lower() and isinstance(v, str):
                    logo = v
                    break
            grp = None
            for k, v in c.items():
                if "group" in k.lower() or "categor" in k.lower():
                    if isinstance(v, str):
                        grp = v
                    elif isinstance(v, dict):
                        grp = _pick(v, "name", "title")
                    break
            chans.append({"id": cid, "name": name, "logo": logo or "",
                          "group": grp or "MAXstream"})
        if chans:
            print(f"[+] {len(chans)} channel dari {p}")
            return chans, p
    print("[!] daftar channel tidak ditemukan — jalankan --discover lalu sesuaikan P_CHANNELS")
    return [], None


def resolve_stream(base, token, ch):
    """Cari URL playback (m3u8/mpd) + license untuk satu channel."""
    for tmpl in P_ENTRY:
        p = tmpl.format(id=ch["id"])
        st, hd, body = api_call("GET", base + p, token=token)
        if st != 200:
            continue
        m3u8 = re.findall(r"https?://[^\"'\s]+\.m3u8[^\"'\s]*", body)
        mpd = re.findall(r"https?://[^\"'\s]+\.mpd[^\"'\s]*", body)
        lic = re.findall(r"https?://[^\"'\s]*(?:license|licensing|wv|drm)[^\"'\s]*", body)
        if m3u8 or mpd:
            return {"url": (m3u8 or mpd)[0], "type": "hls" if m3u8 else "dash",
                    "license": lic[0] if lic else "", "path": p}
    return None


# --------------------------------------------------------------------------
# mode demo: pakai stream tes publik supaya relay bisa divalidasi di player
# sebelum token MAXstream didapat.
FAKE = [
    {"id": "0_demo1", "name": "DEMO Mux Test", "logo": "", "group": "Demo",
     "url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "type": "hls"},
    {"id": "0_demo2", "name": "DEMO Big Buck Bunny", "logo": "", "group": "Demo",
     "url": "https://test-streams.mux.dev/pts_shift/master.m3u8", "type": "hls"},
]


class Handler(http.server.BaseHTTPRequestHandler):
    channels = []
    base = DEFAULT_BASE
    token = None
    cache = {}

    def log_message(self, fmt, *a):                            # noqa: D401
        sys.stderr.write("  %s\n" % (fmt % a))

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                      # noqa: BLE001
            pass

    def do_GET(self):                                          # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, self._index(), "text/html; charset=utf-8")
        if path == "/playlist.m3u":
            return self._send(200, self._playlist())
        if path.startswith("/mpd/") or path.startswith("/stream/"):
            cid = path.rsplit("/", 1)[-1]
            return self._send(200, self._playlist_channel(cid))
        if path.startswith("/info/"):
            cid = path.rsplit("/", 1)[-1]
            info = self._info(cid)
            return self._send(200 if info else 404,
                              json.dumps(info, indent=2) if info else "not found",
                              "application/json; charset=utf-8")
        return self._send(404, "not found")

    def do_POST(self):                                         # noqa: N802
        if self.path.startswith("/license/"):
            cid = self.path.rsplit("/", 1)[-1]
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            info = self._info(cid)
            if not info or not info.get("license"):
                return self._send(404, "no license url")
            req = urllib.request.Request(info["license"], data=body, method="POST",
                                         headers={"Content-Type": "application/octet-stream",
                                                  "User-Agent": UA})
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    return self._send(r.status, r.read(),
                                      r.headers.get("Content-Type", "application/octet-stream"))
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read())
            except Exception as e:                             # noqa: BLE001
                return self._send(502, str(e))
        return self._send(404, "not found")

    # ---- render ----
    def _index(self):
        rows = "\n".join(
            f"<tr><td>{c['id']}</td><td>{c['name']}</td><td>{c['group']}</td>"
            f"<td><a href='/info/{c['id']}'>info</a></td></tr>"
            for c in self.channels)
        return f"""<!doctype html><meta charset=utf-8><title>MAXstream relay</title>
<style>body{{font:14px system-ui;margin:24px}}td,th{{padding:4px 10px;border-bottom:1px solid #ddd}}</style>
<h1>MAXstream relay</h1>
<p>{len(self.channels)} channel · playlist: <a href="/playlist.m3u">/playlist.m3u</a></p>
<table><tr><th>ID</th><th>Nama</th><th>Grup</th><th></th></tr>{rows}</table>"""

    def _playlist(self):
        out = ["#EXTM3U"]
        for c in self.channels:
            name = str(c["name"]).replace(",", " ")
            logo = f' tvg-logo="{c["logo"]}"' if c.get("logo") else ""
            grp = str(c.get("group") or "MAXstream").replace('"', "")
            out.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-name="{name}"{logo} '
                       f'group-title="MAXstream · {grp}",{name}')
            out.append(f'#EXTVLCOPT:http-user-agent={UA}')
            out.append(f"http://{self.headers.get('Host', 'localhost')}/mpd/{c['id']}")
        return "\n".join(out) + "\n"

    def _playlist_channel(self, cid):
        info = self._info(cid)
        if not info:
            return "#EXTM3U\n# tidak ada stream\n"
        host = self.headers.get("Host", "localhost")
        out = ["#EXTM3U"]
        if info.get("license"):
            out.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
            out.append(f"#KODIPROP:inputstream.adaptive.license_key="
                       f"http://{host}/license/{cid}|||{'R{SSM}|'}")
        out.append(info["url"])
        return "\n".join(out) + "\n"

    def _info(self, cid):
        if cid in self.cache:
            return self.cache[cid]
        ch = next((c for c in self.channels if str(c["id"]) == cid), None)
        if not ch:
            return None
        if ch.get("url"):                    # mode demo / entry sudah lengkap
            info = {"url": ch["url"], "type": ch.get("type", "hls"),
                    "license": ch.get("license", ""), "channel": ch}
            self.cache[cid] = info
            return info
        info = resolve_stream(self.base, self.token, ch)
        if info:
            info["channel"] = ch
            self.cache[cid] = info
        return info


class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(args):
    Handler.base = args.base
    Handler.token = args.token or load_token()
    if args.demo:
        Handler.channels = FAKE
        print("[i] mode demo: 2 channel palsu (uji server, tanpa API)")
    else:
        if not Handler.token:
            print("[!] belum ada token. Pakai --login atau --token dulu.")
        chans, _ = fetch_channels(args.base, Handler.token)
        Handler.channels = chans
        if not chans:
            print("[!] tidak ada channel; server tetap jalan dengan playlist kosong")
    srv = Threaded((args.host, args.port), Handler)
    print(f"\n[+] relay jalan: http://{args.host}:{args.port}/playlist.m3u")
    print(f"    daftar channel: http://{args.host}:{args.port}/")
    print("    Ctrl+C untuk berhenti\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] berhenti")


def main():
    ap = argparse.ArgumentParser(description="MAXstream → M3U relay (jalankan dari jaringan Indonesia)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"base API (default {DEFAULT_BASE})")
    ap.add_argument("--discover", action="store_true", help="probe endpoint & cetak respons")
    ap.add_argument("--login", metavar="NOMOR", help="nomor IndiHome untuk login OTP")
    ap.add_argument("--token", help="bearer token siap pakai")
    ap.add_argument("--serve", action="store_true", help="jalankan server playlist")
    ap.add_argument("--demo", action="store_true", help="mode demo (channel palsu)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8888)
    a = ap.parse_args()

    if a.discover:
        return discover(a.base)
    if a.login:
        tok = login_indihome(a.base, a.login)
        if not tok:
            return 1
        a.token = tok
    if a.serve:
        return serve(a)
    if a.token:
        save_token(a.token, {"base": a.base})
        print("[+] token disimpan. Jalankan --serve untuk mulai.")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
