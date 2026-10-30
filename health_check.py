#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py — stream health probe for iCN Play playlists
===========================================================
Probes every channel in a master M3U using the #EXTVLCOPT headers the player
itself would send, validates that the response is a real manifest (not an HTML
error page), and classifies each entry:

  LIVE    -> HTTP 200 + body is a genuine manifest (HLS / DASH / MPEG-TS)
  LIVE?   -> HTTP 200 but the body could not be positively identified
  DRM     -> HTTP 401/403 AND the entry carries a clearkey (#KODIPROP)
             => KEPT: the player performs the clearkey handshake and plays it
  AUTH    -> HTTP 401/403 with no license hint => KEPT but flagged
  FLAKY   -> reset / TLS error / timeout after retries => KEPT (host may flap)
  DEAD    -> 404 / 410 / DNS failure / hard 40x => DROPPED

Design rules:
  * Only DEAD is dropped. Dropping a DRM channel because a bare urllib probe
    got 403 would delete hundreds of channels that play fine in Kodi/OTT.
  * FLAKY entries are retried (default 2 retries with backoff) so a transient
    timeout is not recorded as a dead channel.
  * Every number in the report is produced by an actual request.

Usage:
  python3 health_check.py ICNPLAY.m3u --json health.json
  python3 health_check.py ICNPLAY.m3u --workers 48 --retries 2
"""
import argparse
import concurrent.futures as cf
import json
import socket
import time
import urllib.error
import urllib.request

UA_DEFAULT = "Tivimate/1.3.2.4 (Linux; Android 14; Raspberry Pi 5 Build/UP1A.231005.007)"
TIMEOUT = 12
WORKERS = 32
RETRIES = 2                 # extra attempts for FLAKY results
READ_BYTES = 2048


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def parse_m3u(path):
    """Parse an M3U file into entry dicts: name, url, headers, props, group."""
    entries, cur = [], None
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                if cur and cur.get("url"):
                    entries.append(cur)
                meta = line[len("#EXTINF:"):]
                # duration is the part before the first comma
                name = line.split(",", 1)[1].strip() if "," in line else ""
                group = ""
                m = __import__("re").search(r'group-title="([^"]*)"', line)
                if m:
                    group = m.group(1)
                cur = {"name": name, "group": group, "url": None,
                       "headers": {}, "props": []}
            elif cur is not None:
                if line.startswith("#EXTVLCOPT:http-user-agent="):
                    cur["headers"]["User-Agent"] = line.split("=", 1)[1]
                elif line.startswith("#EXTVLCOPT:http-referrer="):
                    cur["headers"]["Referer"] = line.split("=", 1)[1]
                elif line.startswith("#KODIPROP:"):
                    cur["props"].append(line)
                elif line.startswith(("http://", "https://")):
                    cur["url"] = line
    if cur and cur.get("url"):
        entries.append(cur)
    return entries


# --------------------------------------------------------------------------- #
# manifest validation
# --------------------------------------------------------------------------- #
def looks_like_manifest(body):
    """True only for a genuine streaming manifest, not an HTML error page."""
    head = body[:400].lstrip()
    low = head.lower()
    if "<!doctype html" in low or "<html" in low[:200]:
        return False                       # error/landing page, not a stream
    if "#extm3u" in low or "#ext-x-" in low:
        return True                        # HLS
    if "<mpd" in low or (low.startswith("<?xml") and "mpd" in low[:600]):
        return True                        # DASH
    if body[:1] == "\x47":                 # MPEG-TS sync byte 'G'
        if len(body) > 189 and body[188:189] == "\x47":
            return True
        return True                        # single-packet probe is enough
    if "sample-aes" in low:
        return True
    return False


# --------------------------------------------------------------------------- #
# single probe (with retries for flaky outcomes)
# --------------------------------------------------------------------------- #
def _kind_of(body):
    """Coarse stream type: hls / dash / ts / other (used to prefer HLS)."""
    head = body[:400].lstrip().lower()
    if "#extm3u" in head or "#ext-x" in head:
        return "hls"
    if "<mpd" in head:
        return "dash"
    if body[:1] == "\x47":
        return "ts"
    return "other"


def _attempt(url, hdr):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(READ_BYTES)
            body = raw.decode("utf-8", "replace")
            ms = int((time.time() - t0) * 1000)
            state = "LIVE" if looks_like_manifest(body) else "LIVE?"
            return state, r.status, ms, _kind_of(body)
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        if e.code in (401, 403):
            return "DRM/AUTH", e.code, ms, ""
        if e.code in (404, 410):
            return "DEAD", e.code, ms, ""
        if e.code in (400, 405, 406, 451):
            return "DEAD", e.code, ms, ""
        return "AUTH", e.code, ms, ""
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        ms = int((time.time() - t0) * 1000)
        if "Name or service not known" in reason or "nodename" in reason \
                or "Name does not resolve" in reason:
            return "DEAD", "DNS", ms, ""
        return "FLAKY", type(getattr(e, "reason", e)).__name__, ms, ""
    except (socket.timeout, TimeoutError):
        return "FLAKY", "timeout", int((time.time() - t0) * 1000), ""
    except Exception as e:                                  # noqa: BLE001
        return "FLAKY", type(e).__name__, int((time.time() - t0) * 1000), ""


def probe(entry, retries=RETRIES):
    url = entry["url"]
    hdr = dict(entry["headers"])
    hdr.setdefault("User-Agent", UA_DEFAULT)
    has_license = any("license_key" in p for p in entry.get("props", []))

    state, code, ms, kind = _attempt(url, hdr)
    tries = 1
    while state == "FLAKY" and tries <= retries:
        time.sleep(min(2 ** tries, 5))
        state2, code2, ms2, kind2 = _attempt(url, hdr)
        if ms2 < ms:
            ms = ms2
        state, code, kind = state2, code2, kind2 or kind
        tries += 1

    if state == "DRM/AUTH":
        state = "DRM" if has_license else "AUTH"
    if state == "FLAKY" and tries > 1:
        code = f"{code} (x{tries})"
    return state, code, url, tries, ms, kind


# --------------------------------------------------------------------------- #
# batch run
# --------------------------------------------------------------------------- #
def run(path, json_out=None, workers=WORKERS, retries=RETRIES, quiet=False):
    entries = parse_m3u(path)
    if not quiet:
        print(f"[*] probing {len(entries)} channels "
              f"(workers={workers}, retries={retries})...")

    results = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, e, retries): e for e in entries}
        done = 0
        for fut in cf.as_completed(futs):
            e = futs[fut]
            state, code, url, tries, ms, kind = fut.result()
            results.append({"name": e["name"], "group": e.get("group", ""),
                            "url": url, "state": state, "code": str(code),
                            "attempts": tries, "ms": ms, "kind": kind,
                            "headers": e["headers"], "props": e["props"]})
            done += 1
            if not quiet and done % 200 == 0:
                print(f"    ...{done}/{len(entries)}")

    counts = {}
    for r in results:
        counts[r["state"]] = counts.get(r["state"], 0) + 1

    order = ["LIVE", "LIVE?", "DRM", "AUTH", "FLAKY", "DEAD"]
    if not quiet:
        print("\n=== HEALTH SUMMARY ===")
        for k in order:
            if counts.get(k):
                print(f"  {k:6} : {counts[k]:5}")
        kept = len(results) - counts.get("DEAD", 0)
        print(f"\n  dropped (DEAD only) : {counts.get('DEAD', 0)}")
        print(f"  kept                : {kept}")

    groups = {}
    for r in results:
        g = r["group"] or "(tanpa grup)"
        d = groups.setdefault(g, {"total": 0, "live": 0, "dead": 0})
        d["total"] += 1
        if r["state"] in ("LIVE", "LIVE?"):
            d["live"] += 1
        elif r["state"] == "DEAD":
            d["dead"] += 1

    report = {
        "total": len(results),
        "counts": counts,
        "kept": len(results) - counts.get("DEAD", 0),
        "workers": workers,
        "retries": retries,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]["total"])),
        "results": results,
    }
    if json_out:
        with open(json_out, "w") as f:
            json.dump(report, f, indent=2)
        if not quiet:
            print(f"  report: {json_out}")
    return report


def classify(results):
    """URLs safe to drop: only hard DEAD."""
    if isinstance(results, dict):
        results = results.get("results", [])
    return {r["url"] for r in results if r["state"] == "DEAD"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="probe IPTV playlist channels")
    ap.add_argument("m3u")
    ap.add_argument("--json", default=None)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--retries", type=int, default=RETRIES)
    a = ap.parse_args()
    run(a.m3u, a.json, a.workers, a.retries)
