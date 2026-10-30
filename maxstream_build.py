#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maxstream_build.py — petakan daftar channel MAXstream ke stream yang sudah
terbukti jalan di playlist kita, lalu tulis MAXSTREAM.m3u.

Kenapa begini (dan bukan ambil langsung):
  * api.maxstream.tv           -> 403 dari luar Indonesia (WAF/geo)
  * cdn-telkomsel-01.akamaized -> 400 "Invalid URL" (butuh Host khusus + EdgeAuth token)
  * vmp-cms.telkomsel.com      -> 432 (Akamai bot protection)
  Stream MAXstream butuh akun (login OTP) + IP Indonesia + DRM.
  Jadi yang bisa dilakukan jujur: ambil daftar channel resminya, lalu cocokkan
  ke stream lain yang sudah kita verifikasi bisa diputar.

Sumber daftar: cache publik worker (117 channel live: id, nama, logo, grup).

Usage: python3 maxstream_build.py [--publish]
"""
import html
import json
import os
import re
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "maxstream")
LIST_URL = "https://maxstream.rustynail.workers.dev/?type=live&source=cache"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36"


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_channels():
    """117 channel live MAXstream dari cache worker."""
    t = http_get(LIST_URL)
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 5 and cells[0].isdigit():
            logo = re.search(r'href="(https://vmp-cms[^"]+)"', row)
            out.append({"id": cells[1], "name": cells[2],
                        "logo": logo.group(1) if logo else "",
                        "group": cells[4]})
    return out


def parse_playlist(path):
    ents, cur = [], None
    for line in open(path, errors="replace"):
        line = line.strip()
        if line.startswith("#EXTINF"):
            cur = {"line": line, "name": line.rsplit(",", 1)[-1].strip(),
                   "meta": [], "url": None}
        elif cur is not None:
            if line.startswith(("http://", "https://")):
                cur["url"] = line
                ents.append(cur)
                cur = None
            elif line.startswith("#"):
                cur["meta"].append(line)
    return [e for e in ents if e["url"]]


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", s)
    s = re.sub(r"\b(fhd|uhd|hd|sd|hevc|h265|h264|avc)\b", " ", s)
    s = re.sub(r"\b(1080|720|480|360|240)p\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\s+", " ", s)


GROUP_MAP = {
    "Local TV": "Lokal",
    "Movies and Entertainment": "Movies",
    "Kids": "Kids",
    "Sports Channel": "Sports",
    "-": "Indonesia",
}


def main():
    publish = "--publish" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    try:
        ms = fetch_channels()
    except Exception as e:                                   # noqa: BLE001
        print(f"[!] gagal ambil daftar MAXstream: {type(e).__name__}: {e}")
        print("[!] MAXSTREAM.m3u lama dibiarkan apa adanya")
        return 1
    print(f"[+] channel MAXstream: {len(ms)}")

    src = os.path.join(HERE, "ICNPLAY.m3u")
    if not os.path.exists(src):
        print("[!] ICNPLAY.m3u belum ada — jalankan merge_build.py dulu")
        return 1
    ents = parse_playlist(src)
    exact = {}
    for e in ents:
        exact.setdefault(norm(e["name"]), []).append(e)

    matched, missing = [], []
    for m in ms:
        k = norm(m["name"])
        toks = [w for w in k.split() if len(w) > 1]
        hit = None
        if exact.get(k):
            hit = exact[k][0]
        elif toks:
            for e in ents:
                etoks = norm(e["name"]).split()
                if all(w in etoks for w in toks) and abs(len(etoks) - len(toks)) <= 1:
                    hit = e
                    break
        (matched if hit else missing).append({**m, "stream": hit} if hit else m)

    print(f"[+] cocok: {len(matched)}/{len(ms)} | tanpa padanan: {len(missing)}")

    lines = ["#EXTM3U"]
    for m in matched:
        e = m["stream"]
        grp = GROUP_MAP.get(m["group"], "Indonesia")
        name = m["name"].replace(",", " ")
        parts = ["#EXTINF:-1"]
        if m["id"]:
            parts.append(f'tvg-id="{m["id"]}"')
        parts.append(f'tvg-name="{name}"')
        if m["logo"]:
            parts.append(f'tvg-logo="{m["logo"]}"')
        parts.append(f'group-title="{grp}"')
        lines.append(" ".join(parts) + "," + name)
        lines.extend(e["meta"])
        lines.append(e["url"])

    path = os.path.join(OUT, "MAXSTREAM.m3u")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[+] tulis {os.path.relpath(path, HERE)}  ({len(matched)} channel)")
    print(f"    grup: {dict(Counter(GROUP_MAP.get(m['group'], 'Indonesia') for m in matched))}")

    man = {"source_list": LIST_URL, "maxstream_total": len(ms),
           "matched": len(matched), "missing": len(missing),
           "missing_names": [m["name"] for m in missing]}
    json.dump(man, open(os.path.join(OUT, "manifest.json"), "w"), indent=2, ensure_ascii=False)

    if publish:
        dst = os.path.join(HERE, "MAXSTREAM.m3u")
        with open(dst, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"[+] publish {os.path.basename(dst)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
