#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tivora_build.py — build a clean, verified, categorised playlist from
https://aspaltvpasti.top/tivora/get.php

Steps
  1. fetch source
  2. parse every entry with its #EXTVLCOPT headers + #KODIPROP licence
  3. de-duplicate by URL, then by (group, name) case-insensitively
  4. probe every entry (player headers, retries) and keep ONLY what really plays
  5. normalise categories, sort each category fastest-first, HLS before DASH
  6. write: tivora/MASTER.m3u, tivora/<Kategori>.m3u, tivora/kids.m3u,
            tivora/health.json, tivora/manifest.json

Only channels that answered with a real manifest are kept. Widevine entries
that cannot be played without a CDM (403) are dropped, because the point is a
playlist that simply works.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict

import health_check as hc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tivora")
SOURCE = "https://aspaltvpasti.top/tivora/get.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")

# tidy up the messy group names coming from the source
GROUP_MAP = {
    "LOKAL": "Lokal",
    "Lokal": "Lokal",
    "SPORTS 2": "Sports",
    "SPORTS": "Sports",
    "Sport": "Sports",
    "INDONESIA": "Indonesia",
    "MOVIES": "Movies",
    "KIDS": "Kids",
    "MUSIC": "Music",
    "MALAYSIA": "Malaysia",
    "ENTERTAIMENT": "Entertainment",
    "ENTERTAINMENT": "Entertainment",
    "MUSLIM": "Muslim",
    "FIGHT": "Fight",
    "KNOWLEDGE & LIFESTYLE": "Knowledge & Lifestyle",
    "MURATTAL 30 JUZ": "Murattal 30 Juz",
    "": "Lainnya",
    "(kosong)": "Lainnya",
}
GROUP_ORDER = [
    "Indonesia", "Sports", "Movies", "Kids", "Entertainment", "Music",
    "Knowledge & Lifestyle", "Muslim", "Murattal 30 Juz", "Fight",
    "Lokal", "Malaysia", "Lainnya",
]
KIDS_RE = re.compile(r"kids|anak|child|junior|cartoon|nick|disney|baby|zoomoo|cbeebies|moonbug|mentari", re.I)


def fetch(url=SOURCE, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse(text):
    ents, cur = [], None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("#EXTINF"):
            if cur and cur["url"]:
                ents.append(cur)
            g = re.search(r'group-title="([^"]*)"', line)
            logo = re.search(r'tvg-logo="([^"]*)"', line)
            tvg = re.search(r'tvg-id="([^"]*)"', line)
            cur = {"name": line.rsplit(",", 1)[-1].strip(),
                   "group": (g.group(1) if g else "").strip(),
                   "extinf": line, "meta": [], "url": None,
                   "logo": logo.group(1) if logo else "",
                   "tvgid": tvg.group(1) if tvg else ""}
        elif cur is not None:
            if line.startswith(("http://", "https://")):
                cur["url"] = line.strip()
            elif line.startswith("#"):
                cur["meta"].append(line.strip())
    if cur and cur["url"]:
        ents.append(cur)
    return ents


def dedupe(ents):
    """Drop duplicate URLs, then duplicate (group,name). Keep the first seen,
    preferring an entry that carries more metadata (licence/headers)."""
    seen_url, seen_name, out = {}, {}, []
    n_url = n_name = 0
    for e in ents:
        u = e["url"]
        key = (GROUP_MAP.get(e["group"], e["group"] or "Lainnya").lower(),
               re.sub(r"\s+", " ", e["name"].lower()).strip())
        if u in seen_url:
            n_url += 1
            continue
        if key in seen_name:
            n_name += 1
            continue
        seen_url[u] = e
        seen_name[key] = e
        e["cat"] = GROUP_MAP.get(e["group"], e["group"] or "Lainnya")
        out.append(e)
    print(f"[+] dedupe: -{n_url} URL ganda, -{n_name} nama ganda -> {len(out)} unik")
    return out


def to_hc_entry(e):
    hdr, props = {}, []
    for m in e["meta"]:
        if m.startswith("#EXTVLCOPT:http-user-agent="):
            hdr["User-Agent"] = m.split("=", 1)[1]
        elif m.startswith("#EXTVLCOPT:http-referrer="):
            hdr["Referer"] = m.split("=", 1)[1]
        elif m.startswith("#KODIPROP:"):
            props.append(m)
    return {"name": e["name"], "group": e["cat"], "url": e["url"],
            "headers": hdr, "props": props}


def _clean(v):
    """Strip characters that would break the M3U line."""
    return re.sub(r'["\r\n]+', "", str(v or "")).strip()


def _norm_extinf(e):
    """Rebuild the #EXTINF line from parsed fields with the tidy category.

    Some source lines are malformed (an unterminated group-title quote), so
    regex-patching them produces broken output; rebuilding is deterministic.
    """
    name = _clean(e.get("name"))
    cat = _clean(e.get("cat") or "Lainnya")
    parts = ["#EXTINF:-1"]
    if e.get("tvgid"):
        parts.append(f'tvg-id="{_clean(e["tvgid"])}"')
    if name:
        parts.append(f'tvg-name="{name}"')
    if e.get("logo"):
        parts.append(f'tvg-logo="{_clean(e["logo"])}"')
    parts.append(f'group-title="{cat}"')
    return " ".join(parts) + "," + name


def emit(path, entries, title):
    lines = ["#EXTM3U"]
    for e in entries:
        lines.append(_norm_extinf(e))
        lines.extend(e["meta"])
        lines.append(e["url"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"    {os.path.relpath(path, HERE):28} {len(entries):4} channel  {title}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--reuse-health", action="store_true",
                    help="skip probing, reuse tivora/health.json")
    a = ap.parse_args()

    print(f"[*] fetch {a.source}")
    text = fetch(a.source)
    ents = parse(text)
    print(f"[+] parsed: {len(ents)} entri, {len(set(e['group'] for e in ents))} grup mentah")
    ents = dedupe(ents)

    hc_entries = [to_hc_entry(e) for e in ents]
    os.makedirs(OUT, exist_ok=True)
    hj = os.path.join(OUT, "health.json")
    if a.reuse_health and os.path.exists(hj):
        print(f"\n[*] pakai hasil probe sebelumnya: {os.path.relpath(hj, HERE)}")
        report = json.load(open(hj))
    else:
        print(f"\n[*] probe {len(hc_entries)} channel (workers={a.workers}, retries={a.retries})")
        report = hc.run(_write_tmp(hc_entries), hj, workers=a.workers, retries=a.retries)

    res = {r["url"]: r for r in report["results"]}
    alive = []
    for e in ents:
        r = res.get(e["url"])
        if not r or r["state"] not in ("LIVE", "LIVE?"):
            continue
        e["ms"] = r.get("ms") or 999999
        e["kind"] = r.get("kind") or "other"
        alive.append(e)

    rank = {"hls": 0, "ts": 0, "audio": 1, "other": 2, "dash": 3}
    alive.sort(key=lambda e: (rank.get(e["kind"], 3), e["ms"]))
    print(f"\n[+] hidup: {len(alive)} / {len(ents)}  "
          f"(dibuang {len(ents)-len(alive)} mati/DRM-403)")

    # master + per category
    emit(os.path.join(OUT, "MASTER.m3u"), alive, "semua channel hidup")
    by_cat = OrderedDict()
    for e in alive:
        by_cat.setdefault(e["cat"], []).append(e)
    order = [c for c in GROUP_ORDER if c in by_cat] + [c for c in by_cat if c not in GROUP_ORDER]
    for cat in order:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", cat).strip("_")
        emit(os.path.join(OUT, slug + ".m3u"), by_cat[cat], cat)

    kids = [e for e in alive if e["cat"] == "Kids" or KIDS_RE.search(e["name"])]
    emit(os.path.join(OUT, "kids.m3u"), kids, "khusus anak")

    # Clearkey-protected channels answer 403 to a bare probe but DO play in
    # Kodi/OTT with inputstream.adaptive (the KID:KEY is inline). Kept apart so
    # MASTER stays "just works" while power users still get them.
    ck = []
    for e in ents:
        e.setdefault("cat", GROUP_MAP.get(e["group"], e["group"] or "Lainnya"))
        r = res.get(e["url"])
        if not r or r["state"] != "DRM":
            continue
        if any("clearkey" in p for p in r.get("props", [])):
            e["ms"] = r.get("ms") or 999999
            e["kind"] = r.get("kind") or "other"
            ck.append(e)
    ck.sort(key=lambda e: (rank.get(e["kind"], 4), e["ms"]))
    emit(os.path.join(OUT, "DRM_Clearkey.m3u"), ck, "butuh clearkey (Kodi/OTT)")

    # manifest
    man = {
        "source": a.source,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parsed": len(ents),
        "alive": len(alive),
        "counts": report["counts"],
        "categories": {c: len(v) for c, v in by_cat.items()},
        "kinds": dict(Counter(e["kind"] for e in alive)),
    }
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
    print(f"\n[+] kategori: {man['categories']}")
    print(f"[+] jenis   : {man['kinds']}")


def _write_tmp(hc_entries):
    """health_check.run() reads a file, so materialise the deduped list."""
    path = os.path.join(OUT, "_probe.m3u")
    os.makedirs(OUT, exist_ok=True)
    lines = ["#EXTM3U"]
    for e in hc_entries:
        lines.append(f'#EXTINF:-1 group-title="{e["group"]}",{e["name"]}')
        for k, v in e["headers"].items():
            key = "http-user-agent" if k == "User-Agent" else "http-referrer"
            lines.append(f"#EXTVLCOPT:{key}={v}")
        lines.extend(e["props"])
        lines.append(e["url"])
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    main()
