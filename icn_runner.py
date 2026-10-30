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
    """Download one source playlist. On failure, KEEP the previous copy on disk
    instead of dropping a whole source just because a host hiccupped — a stale
    playlist still serves thousands of working channels."""
    url  = s["path"].strip()
    ua   = (s.get("ua") or cfg.get("default_ua", "Mozilla/5.0")).strip()
    name = (s.get("name") or "src").replace("/", "_")
    dest = os.path.join(PL_DIR, f"{name}.m3u")
    try:
        body, ct = http_get(url, ua)
        if "#EXTM3U" not in body and "#EXTINF" not in body:
            raise ValueError(f"bukan playlist (content-type={ct})")
        with open(dest, "w") as f:
            f.write(body)
        n = body.count("#EXTINF")
        s["_file"]  = os.path.relpath(dest, HERE)
        s["_count"] = n
        s["_live"]  = True
        s["_stale"] = False
        print(f"  {name:9} -> HTTP 200  {n:5d} ch   {s['_file']}")
    except Exception as e:                                  # noqa: BLE001
        # Fallback: reuse the last good copy already committed in the repo.
        if os.path.exists(dest):
            n = open(dest, errors="replace").read().count("#EXTINF")
            s["_file"]  = os.path.relpath(dest, HERE)
            s["_count"] = n
            s["_live"]  = n > 0
            s["_stale"] = True
            print(f"  {name:9} -> GAGAL ({e}) — pakai salinan lama ({n} ch)")
        else:
            s["_live"]  = False
            s["_stale"] = True
            print(f"  {name:9} -> GAGAL ({e}) — tidak ada salinan lama")


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

    # Root-level files published on GitHub.
    #   ICNPLAY.m3u      -> channels that passed the health check (players use this)
    #   ICNPLAY_full.m3u -> every channel, unfiltered (fallback / manual pick)
    root_master = os.path.join(HERE, "ICNPLAY.m3u")
    root_full   = os.path.join(HERE, "ICNPLAY_full.m3u")
    payload = "\n".join(blocks) + "\n"
    for target in (root_master, root_full):
        with open(target, "w") as f:
            f.write(payload)
    print(f"[+] player playlist : {os.path.relpath(root_master, HERE)}  ({n_total} channels, flat)")
    print(f"[+] full playlist   : {os.path.relpath(root_full, HERE)}  ({n_total} channels, unfiltered)")


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


def filter_blocks(blocks, drop_urls):
    """Remove channel blocks whose stream URL is in drop_urls.
    A block = #EXTINF + optional meta (#EXTVLCOPT/#KODIPROP) + url."""
    out, cur = [], []
    removed = 0
    for line in blocks:
        if line.startswith("#EXTINF"):
            if cur:
                url = next((l for l in cur if l.startswith("http")), None)
                if url and url in drop_urls:
                    removed += 1
                else:
                    out.extend(cur)
            cur = [line]
        elif cur:
            cur.append(line)
        else:
            out.append(line)
    if cur:
        url = next((l for l in cur if l.startswith("http")), None)
        if url and url in drop_urls:
            removed += 1
        else:
            out.extend(cur)
    return out, removed


def apply_health_check(cfg, drop_dead=True, workers=None, retries=None, max_drop_ratio=0.40):
    """Probe ICNPLAY.m3u and rewrite it (+ MASTER) without DEAD channels.

    Safety guard: if the probe wants to drop more than `max_drop_ratio` of all
    channels, something is wrong on OUR side (CI network outage, DNS failure),
    not the playlist. In that case nothing is dropped — a bad probe must never
    wipe the published playlist.
    """
    import health_check
    root = os.path.join(HERE, "ICNPLAY.m3u")
    if not os.path.exists(root):
        print("[!] health check skipped: ICNPLAY.m3u not found")
        return None

    kw = {}
    if workers:
        kw["workers"] = workers
    if retries is not None:
        kw["retries"] = retries
    report = health_check.run(root, os.path.join(HERE, "health.json"), **kw)
    counts = report["counts"]
    total = report["total"] or 1
    dead = health_check.classify(report)
    n_dead = len(dead)

    if not drop_dead or n_dead == 0:
        print("[+] health check: nothing to drop")
        return counts

    ratio = n_dead / total
    if ratio > max_drop_ratio:
        print(f"[!] ABORT drop: {n_dead}/{total} ({ratio:.0%}) flagged DEAD — "
              f"melebihi batas aman {max_drop_ratio:.0%}. Playlist dibiarkan utuh "
              f"(kemungkinan gangguan jaringan saat probe).")
        return counts

    for target in (root, os.path.join(PL_DIR, "MASTER.m3u")):
        if not os.path.exists(target):
            continue
        blocks = open(target, errors="replace").read().splitlines()
        kept, removed = filter_blocks(blocks, dead)
        with open(target, "w") as f:
            f.write("\n".join(kept) + "\n")
        print(f"[+] {os.path.basename(target)}: dropped {removed} DEAD, kept {len(kept)} lines")
    return counts


KIDS_RE = re.compile(
    r"kids|anak|child|junior|boomerang|cartoon|nick|disney|baby|zoomoo|cbeebies|moonbug|mentari",
    re.I)


def _split_blocks(lines):
    """Split an M3U into [ (extinf_line, [meta...], url) ] channel blocks."""
    blocks, cur = [], None
    for line in lines:
        if line.startswith("#EXTINF"):
            if cur:
                blocks.append(cur)
            cur = [line, [], None]
        elif cur is not None:
            if line.startswith(("http://", "https://")):
                cur[2] = line
            elif line.startswith("#"):
                cur[1].append(line)
    if cur:
        blocks.append(cur)
    return [b for b in blocks if b[2]]


def _is_kids(extinf):
    return bool(KIDS_RE.search(extinf))


def write_variants(report):
    """Emit curated playlists sorted by measured latency (fastest first).

    kids.m3u          -> channel anak yang benar-benar hidup, urut tercepat.
                         Ini yang dipakai kalau player terasa "lama sekali":
                         32/66 channel anak adalah DRM/403 yang membuat player
                         retry & timeout sebelum jatuh ke channel berikutnya.
    ICNPLAY_fast.m3u  -> semua channel LIVE/LIVE? saja, urut TTFB.
    """
    if not isinstance(report, dict):
        return None
    res = {r["url"]: r for r in report.get("results", [])}
    src = os.path.join(HERE, "ICNPLAY.m3u")
    if not os.path.exists(src):
        return None
    blocks = _split_blocks(open(src, errors="replace").read().splitlines())

    def emit(name, pred, title, max_ms=None):
        picked, skipped_slow = [], 0
        for extinf, meta, url in blocks:
            r = res.get(url)
            if not r or r["state"] not in ("LIVE", "LIVE?"):
                continue
            if not pred(extinf, r):
                continue
            ms = r.get("ms") or 999999
            if max_ms is not None and ms > max_ms:
                skipped_slow += 1
                continue
            name_kind = (r.get("kind") or "other").lower()
            # HLS/TS dahulu (didukung semua player), DASH belakangan.
            rank = 0 if name_kind in ("hls", "ts") else (1 if name_kind == "other" else 2)
            picked.append((rank, ms, extinf, meta, url))
        picked.sort(key=lambda t: (t[0], t[1]))
        out = ["#EXTM3U"]
        for rank, ms, extinf, meta, url in picked:
            out.append(extinf)
            out.extend(meta)
            out.append(url)
        path = os.path.join(HERE, name)
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
        worst = picked[-1][1] if picked else 0
        med = sorted(x[1] for x in picked)[len(picked) // 2] if picked else 0
        cap = f", buang {skipped_slow} >{max_ms}ms" if max_ms else ""
        print(f"[+] {name:16}: {len(picked):4} channel {title} "
              f"(median {med} ms, terburuk {worst} ms{cap})")
        return len(picked)

    # Ambang batas: channel >3 dtk di playlist "fast" membuat player terasa
    # menggantung (retry/timeout). Channel anak >2.5 dtk sama.
    n_fast = emit("ICNPLAY_fast.m3u", lambda e, r: True, "live, urut tercepat", max_ms=3000)
    n_kids = emit("kids.m3u", lambda e, r: _is_kids(e), "anak, urut tercepat", max_ms=2500)
    return {"fast": n_fast, "kids": n_kids}


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
             "stale": s.get("_stale", False),
             "local_file": s.get("_file", "")}
            for s in srcs
        ],
        "total_channels": sum(s.get("_count", 0) for s in srcs),
        "stale_sources": [s.get("name") for s in srcs if s.get("_stale")],
        "stream_probe": probe,
    }
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
    stale = man["stale_sources"]
    print(f"[+] manifest        : manifest.json"
          + (f"  (STALE: {', '.join(stale)})" if stale else ""))
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
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the quick sample probe")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="probe every channel, drop DEAD ones, write health.json")
    ap.add_argument("--check-only", action="store_true",
                    help="only run the health check on the existing ICNPLAY.m3u")
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel probes for --check (default 32)")
    ap.add_argument("--retries", type=int, default=None,
                    help="extra attempts for FLAKY channels (default 2)")
    ap.add_argument("--apk", help="APK path to re-verify constants against")
    a = ap.parse_args()

    cfg = load_cfg()
    if a.apk:
        verify_against_apk(a.apk, cfg)

    hckw = {}
    if a.workers:
        hckw["workers"] = a.workers
    if a.retries is not None:
        hckw["retries"] = a.retries

    if a.check_only:
        counts = apply_health_check(cfg, **hckw)
        print(f"[+] health counts: {counts}")
        return

    os.makedirs(PL_DIR, exist_ok=True)
    srcs = fetch_sources(cfg)
    if a.list_only:
        for s in srcs:
            print(f"  {s.get('name'):9} | {s.get('path'):55} | ua={s.get('ua','')!r} | active={s.get('active')}")
        return

    for s in srcs:
        download_source(s, cfg)

    probe = None
    if not a.no_probe:
        probe = probe_streams(srcs)
    man = build_manifest(cfg, srcs, probe)
    build_master(cfg, srcs)
    if a.check:
        print("\n[*] running health check (drop DEAD only)...")
        counts = apply_health_check(cfg, **hckw)
        man["health"] = counts
        try:
            import health_check as _hc
            rep = json.load(open(os.path.join(HERE, "health.json")))
            man["variants"] = write_variants(rep)
        except Exception as e:                              # noqa: BLE001
            print(f"[!] variant playlists dilewati: {e}")
        with open(os.path.join(HERE, "manifest.json"), "w") as f:
            json.dump(man, f, indent=2)
    print("\n=== READY ===")
    print(f"Total channels (unfiltered): {man['total_channels']}")
    if man.get("stale_sources"):
        print(f"Stale sources (pakai salinan lama): {', '.join(man['stale_sources'])}")
    print("Player playlist: ICNPLAY.m3u (health-checked) + ICNPLAY_full.m3u")


if __name__ == "__main__":
    main()
