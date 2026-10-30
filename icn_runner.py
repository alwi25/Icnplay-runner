#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iCN Play (com.icnplay.id) — portable playlist runner
====================================================
Self-contained, stdlib-only. No external deps.

Design (GitHub-ready):
  - Decoded constants live in config.json (verified from the APK).
  - The Base64+XOR decoder is kept for reproducibility / docs; the runner
    does NOT need the APK to operate. If you pass --apk <path> it will
    re-verify the constants against the binary.

Usage:
  python3 icn_runner.py                 # fetch + download + build master
  python3 icn_runner.py --no-probe      # skip per-stream probing
  python3 icn_runner.py --apk /path/x.apk   # verify constants against binary
  python3 icn_runner.py --list-only     # just print the live source list
"""
import argparse, base64, json, os, re, sys, urllib.request, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PL_DIR = os.path.join(HERE, "playlist")
CONFIG = os.path.join(HERE, "config.json")

# ---- reproducibility: obfuscated constants (from SplashActivity.t + p5.l) --
# These are the raw obfuscated values baked in the DEX. Decoding them yields
# config.json. Keeping them here so anyone can audit the provenance.
KEY      = [49, 67, 78, 80, 76, 52, 89, 95, 67, 78, 76, 56, 54]  # p5.l.f5895a
CT_SERVER = "WTc6ID8OdnAuN2JRUlQ3Jz05Rnc2J2ElW1hBLy8p"            # -> server endpoint
CT_UA     = "WAAAAAAAAA=="                                         # -> app user-agent


def xor_decode(ct):
    raw = base64.b64decode(ct)
    return "".join(chr(b ^ KEY[i % 13]) for i, b in enumerate(raw))


def load_cfg():
    with open(CONFIG) as f:
        return json.load(f)


def http_get(url, ua="Mozilla/5.0 (iCNPlay)"):
    req = urllib.request.Request(url, headers={"User-Agent": ua or "Mozilla/5.0 (iCNPlay)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace"), r.headers.get("Content-Type", "")


def fetch_sources(cfg):
    server = cfg["server_endpoint"]
    ua     = cfg["app_user_agent"]
    body, ct = http_get(server, ua)
    print(f"[+] server endpoint : {server}  (UA={ua!r})")
    srcs = json.loads(body)
    print(f"[+] live sources    : {len(srcs)}")
    return srcs


def download_source(s, cfg):
    url  = s["path"].strip()
    ua   = (s.get("ua") or cfg.get("default_ua", "Mozilla/5.0")).strip()
    name = (s.get("name") or "src").replace("/", "_")
    dest = os.path.join(PL_DIR, f"{name}.m3u")
    body, ct = http_get(url, ua)
    n = body.count("#EXTINF")
    with open(dest, "w") as f:
        f.write(body)
    s["_file"]   = os.path.relpath(dest, HERE)
    s["_count"]  = n
    s["_live"]   = ("#EXTM3U" in body or "#EXTINF" in body)
    print(f"  {name:9} -> HTTP 200  {n:5d} ch   {s['_file']}")


def build_master(cfg, srcs):
    os.makedirs(PL_DIR, exist_ok=True)
    master = os.path.join(PL_DIR, "MASTER.m3u")
    # FLAT playable master: concatenate every channel from each downloaded
    # source into one M3U, injecting group-title per source. This is the
    # format Tivimate / IPTV-org / VLC actually read (a playlist that lists
    # other playlists is NOT valid for most players).
    # Robust to metadata lines (#EXTVLCOPT / #KODIPROP) sitting between the
    # #EXTINF and the stream URL.
    seen = set()
    n_total = 0
    n_orphan = 0
    blocks = ["#EXTM3U"]
    for s in srcs:
        fpath = s.get("_file")
        if not fpath or not s.get("_live"):
            continue
        with open(os.path.join(HERE, fpath)) as fh:
            raw = fh.read().splitlines()
        grp = s.get("name", "src")
        i, L = 0, raw
        nL = len(L)
        while i < nL:
            line = L[i].strip()
            if line.startswith("#EXTINF"):
                url, extra_meta = "", []
                j = i + 1
                while j < nL:
                    nxt = L[j].strip()
                    if nxt.startswith("#EXTINF"):
                        # next channel starts before we found a URL ->
                        # this EXTINF is an ORPHAN (no stream URL). Stop here.
                        break
                    if re.match(r"https?://", nxt, re.I):
                        url = nxt
                        break
                    elif nxt.startswith("#"):
                        extra_meta.append(nxt)
                    j += 1
                # Emit only real channels (with a URL). Orphans are dropped —
                # LIVE-TV2 ships ~111 header-only entries that break players.
                if url and url not in seen:
                    seen.add(url)
                    blocks.append(_inject_group(line, grp))
                    blocks.extend(extra_meta)
                    blocks.append(url)
                    n_total += 1
                elif not url:
                    n_orphan += 1
            i += 1
    with open(master, "w") as f:
        f.write("\n".join(blocks) + "\n")
    print(f"[+] master playlist : {os.path.relpath(master, HERE)}  ({n_total} channels, flat, {n_orphan} orphan dropped)")

    # Also write the root-level ICNPLAY.m3u — this is the file published on
    # GitHub and the one players consume via raw.githubusercontent.com.
    root_master = os.path.join(HERE, "ICNPLAY.m3u")
    with open(root_master, "w") as f:
        f.write("\n".join(blocks) + "\n")
    print(f"[+] player playlist : {os.path.relpath(root_master, HERE)}  ({n_total} channels, flat)")


def _inject_group(extinf, grp):
    if "group-title=" in extinf:
        return extinf
    m = re.match(r"^(#EXTINF:[^\s,]+)", extinf)
    if m:
        return m.group(1) + ' group-title="' + grp + '"' + extinf[len(m.group(1)):]
    return extinf


def probe_streams(srcs, sample=1):
    big = max(srcs, key=lambda s: s.get("_count", 0))
    with open(os.path.join(HERE, big["_file"])) as f:
        lines = f.read().splitlines()
    urls = [l for l in lines if re.match(r"https?://", l, re.I)][:sample * 3]
    ok = 0
    print(f"\n[*] probing {min(3, len(urls))} stream(s) from '{big['name']}'")
    for u in urls:
        try:
            http_get(u, big.get("ua") or "Mozilla/5.0"); ok += 1; code = "OK"
        except urllib.error.HTTPError as e:
            code = f"HTTP {e.code}"
        except Exception as e:
            code = f"{type(e).__name__}"
        print(f"     {code:14} {u}")
    return [ok, len(urls)]


def build_manifest(cfg, srcs, probe):
    man = {
        "app": cfg.get("app", "com.icnplay.id"),
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "server_endpoint": cfg["server_endpoint"],
        "app_user_agent": cfg["app_user_agent"],
        "kunci": cfg.get("kunci", ""),
        "sources": [
            {"name": s.get("name"), "path": s.get("path"), "ua": s.get("ua", ""),
             "channels": s.get("_count", 0), "live": s.get("_live", False),
             "local_file": s.get("_file", "")}
            for s in srcs
        ],
        "total_channels": sum(s.get("_count", 0) for s in srcs),
        "stream_probe": probe,
    }
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
    print(f"[+] manifest        : manifest.json")
    return man


def verify_against_apk(apk_path, cfg):
    """Re-derive constants from a fresh APK's DEX and compare to config."""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["unzip", "-o", "-q", apk_path, "classes*.dex"],
                       cwd=d, check=True)
        dex = os.path.join(d, "classes.dex")
        out = subprocess.run(["strings", "-n", "6", dex],
                             capture_output=True, text=True).stdout
    # The obfuscated constants are plain ASCII in the string pool.
    found = {"server_ct": CT_SERVER in out, "ua_ct": CT_UA in out}
    dec_server = xor_decode(CT_SERVER)
    print(f"[!] re-derive server = {dec_server}")
    print(f"[!] string pool contains obfuscated constants: {found}")
    match = (dec_server == cfg["server_endpoint"])
    print(f"[!] config server matches decoded value: {match}")
    if not match:
        print("[!] WARNING: config.json is stale — edit server_endpoint.")


def main():
    ap = argparse.ArgumentParser(description="iCN Play playlist runner")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--apk", help="APK path to re-verify constants against")
    a = ap.parse_args()

    cfg = load_cfg()
    if a.apk:
        verify_against_apk(a.apk, cfg)

    os.makedirs(PL_DIR, exist_ok=True)
    srcs = fetch_sources(cfg)
    if a.list_only:
        for s in srcs:
            print(f"  {s.get('name'):9} | {s.get('path'):55} | ua={s.get('ua','')!r} | active={s.get('active')}")
        return

    for s in srcs:
        try:
            download_source(s, cfg)
        except Exception as e:
            s["_live"] = False
            print(f"  {s.get('name'):9} -> FAIL {e}")

    probe = None
    if not a.no_probe:
        probe = probe_streams(srcs)
    man = build_manifest(cfg, srcs, probe)
    build_master(cfg, srcs)
    print("\n=== READY ===")
    print(f"Total channels: {man['total_channels']}")
    print("Use playlist/MASTER.m3u in VLC / OTT Navigator / Tivimate.")


if __name__ == "__main__":
    main()
