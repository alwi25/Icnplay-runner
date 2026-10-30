#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py — stream health probe for iCN Play playlists
===========================================================
Probes every channel in a master M3U (using the #EXTVLCOPT headers that the
player itself would send), classifies each entry, and reports:

  LIVE    -> HTTP 200 and body looks like a real manifest (m3u8/MPD/TS)
  DRM     -> HTTP 401/403 AND entry carries a clearkey/license (#KODIPROP)
             => kept: works in Kodi/OTT with the bundled clearkey
  AUTH    -> HTTP 401/403 without any license hint => kept but flagged
  DEAD    -> 404/410/DNS/timeout/reset => candidate for removal
  FLAKY   -> connection reset / TLS error / timeout => kept (host may flap)

Only DEAD is dropped by the runner. Everything else is preserved, because
dropping a DRM channel just because a bare urllib probe got 403 is wrong:
the player sends the clearkey handshake and plays it fine.

Usage:
  python3 health_check.py ICNPLAY.m3u                 # report to stdout
  python3 health_check.py ICNPLAY.m3u --json h.json   # save machine report
  from health_check import classify; (used by icn_runner.py --check)
"""
import argparse, json, re, sys, urllib.request, urllib.error, socket
import concurrent.futures as cf

UA_DEFAULT = "Tivimate/1.3.2.4 (Linux; Android 14; Raspberry Pi 5 Build/UP1A.231005.007)"
TIMEOUT = 12
WORKERS = 32


def parse_m3u(path):
    """Parse an M3U into entries: dict(name, url, headers, props)."""
    entries, cur = [], None
    for raw in open(path, errors="replace").read().splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            if cur and cur.get("url"):
                entries.append(cur)
            name = line.split(",")[-1].strip()
            cur = {"name": name, "url": None, "headers": {}, "props": []}
        elif cur is not None:
            if line.startswith("#EXTVLCOPT:http-user-agent="):
                cur["headers"]["User-Agent"] = line.split("=", 1)[1]
            elif line.startswith("#EXTVLCOPT:http-referrer="):
                cur["headers"]["Referer"] = line.split("=", 1)[1]
            elif line.startswith("#KODIPROP:"):
                cur["props"].append(line)
            elif line.startswith("http"):
                cur["url"] = line
    if cur and cur.get("url"):
        entries.append(cur)
    return entries


def probe(entry):
    url = entry["url"]
    hdr = dict(entry["headers"])
    hdr.setdefault("User-Agent", UA_DEFAULT)
    has_license = any("license_key" in p for p in entry.get("props", []))
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            head = r.read(600).decode("utf-8", "replace")
            marker = ("#EXTM3U" in head or "#EXTINF" in head or "<MPD" in head
                      or "Manifest" in head or "SAMPLE-AES" in head
                      or head[:1] == "\x47")  # MPEG-TS sync byte 'G'
            return "LIVE" if marker else "LIVE?", r.status, url
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ("DRM" if has_license else "AUTH"), e.code, url
        if e.code in (404, 410):
            return "DEAD", e.code, url
        return "AUTH", e.code, url
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "Name or service not known" in reason or "nodename" in reason:
            return "DEAD", "DNS", url
        return "FLAKY", type(e.reason).__name__ if getattr(e, "reason", None) else "URLError", url
    except (socket.timeout, TimeoutError):
        return "FLAKY", "timeout", url
    except Exception as e:
        return "FLAKY", type(e).__name__, url


def run(path, json_out=None, workers=WORKERS):
    entries = parse_m3u(path)
    print(f"[*] probing {len(entries)} channels with {workers} workers...")
    results = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, e): e for e in entries}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            e = futs[fut]
            state, code, url = fut.result()
            results.append({"name": e["name"], "url": url, "state": state, "code": str(code),
                            "headers": e["headers"], "props": e["props"]})
            if i % 100 == 0:
                print(f"    ...{i}/{len(entries)}")

    counts = {}
    for r in results:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    order = ["LIVE", "LIVE?", "DRM", "AUTH", "FLAKY", "DEAD"]
    print("\n=== HEALTH SUMMARY ===")
    for k in order:
        if counts.get(k):
            print(f"  {k:6} : {counts[k]}")
    dead = [r for r in results if r["state"] == "DEAD"]
    print(f"\n  -> dropped candidates (DEAD only): {len(dead)}")
    print(f"  -> kept: {len(results) - len(dead)}")

    if json_out:
        with open(json_out, "w") as f:
            json.dump({"total": len(results), "counts": counts, "results": results}, f, indent=2)
        print(f"  report: {json_out}")
    return results, counts


def classify(results):
    """Return set of URLs considered DEAD (safe to drop)."""
    return {r["url"] for r in results if r["state"] == "DEAD"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("m3u")
    ap.add_argument("--json", default=None)
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()
    run(a.m3u, a.json, a.workers)
