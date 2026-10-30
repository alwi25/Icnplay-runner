#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_build.py — satukan semua sumber jadi satu playlist rapi.

Sumber:
  ICNPLAY.m3u            (hasil iCN Play, sudah terverifikasi)
  tivora/MASTER.m3u      (hasil aspaltvpasti/tivora)
  iptv-org: countries/id, countries/my, countries/sg,
            categories/kids, categories/sports

Alur: parse -> dedupe URL -> probe -> buang mati -> dedupe nama -> kategori
      -> urut tercepat (HLS/TS, audio, lalu DASH) -> tulis per kategori.
"""
# Dedup rules: URL first, then (category, normalised name) preferring HLS.
import json, os, re, sys, time, urllib.request
from collections import Counter, OrderedDict
import health_check as hc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "merged")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36"

# (nama, url, file lokal)  -- file lokal dipakai lebih dulu bila ada, supaya
# CI tidak bergantung pada cache CDN raw.githubusercontent.com (max-age 300).
SOURCES = [
    ("icn",     "https://raw.githubusercontent.com/alwi25/Icnplay-runner/main/icn/MASTER.m3u",
                os.path.join(HERE, "icn", "MASTER.m3u")),
    ("tivora",  "https://raw.githubusercontent.com/alwi25/Icnplay-runner/main/tivora/MASTER.m3u",
                os.path.join(HERE, "tivora", "MASTER.m3u")),
    ("org-id",  "https://iptv-org.github.io/iptv/countries/id.m3u"),
    ("org-my",  "https://iptv-org.github.io/iptv/countries/my.m3u"),
    ("org-sg",  "https://iptv-org.github.io/iptv/countries/sg.m3u"),
    ("org-kids","https://iptv-org.github.io/iptv/categories/kids.m3u"),
    ("org-sport","https://iptv-org.github.io/iptv/categories/sports.m3u"),
]

# grup mentah -> kategori rapi (cek substring, urutan penting)
CAT_RULES = [
    (("fight", "sport", "football", "soccer", "basket", "motogp", "racing", "dazn", "bein", "nba", "nfl", "ufc", "boxing", "liga", "bola", "fifa", "hylo", "badminton", "voli"), "Sports"),
    (("kids", "anak", "child", "junior", "cartoon", "nick", "disney", "baby", "toon", "boomerang", "cbeebies", "moonbug"), "Kids"),
    (("movie", "film", "cinema", "series"), "Movies"),
    (("music",), "Music"),
    (("radio",), "Radio"),
    (("murattal", "quran", "islam", "muslim", "relig", "ngaji", "dakwah", "sholat"), "Muslim"),
    (("news", "berita"), "News"),
    (("knowledge", "documentary", "education", "edukasi", "science", "culture"), "Knowledge & Lifestyle"),
    (("entertain", "hiburan"), "Entertainment"),
    (("cctv",), "CCTV"),
    (("shop", "belanja"), "Shopping"),
    (("kbs", "sbs", "ebs", "mbc", "tvn", "jtbc", "ytn", "mbn", "onstyle",
      "on cine", "on info", "on e channel", "gcn", "cj ", "korea"), "Korea"),
    (("dltv", "thai"), "Thailand"),
    (("china", "cctv-", "chinese", "中国"), "China"),
    (("vietnam", "vn ", "viet"), "Vietnam"),
    (("japan", "japanese", "nhk"), "Japan"),
    (("india", "indian"), "India"),
    (("philippin", "filipino"), "Philippines"),
    (("taiwan", "hong kong", "hongkong"), "Asia"),
    (("australia", "aussie"), "Australia"),
    (("arab", "uae", "saudi", "qatar", "egypt"), "Arab"),
    (("lokal", "local", "daerah", "city"), "Lokal"),
    (("malaysia", "malays"), "Malaysia"),
    (("singapor", "singapur"), "Singapore"),
    (("indonesia", "nasional", "general", "tv nasional", "national"), "Indonesia"),
]
NEWS_BRANDS = ("al jazeera", "dw", "france 24", "arirang", "trt", "nhk world",
               "cnn", "bbc", "bloomberg", "reuters", "cnbc", "sky news",
               "euronews", "cgtn", "i24", "abc news", "cbsn", "nbc news",
               "metro tv", "tvone", "inews", "kompas tv", "idntimes")
LIFE_BRANDS = ("food", "travel", "lifestyle", "motor", "auto", "cooking",
               "home", "garden", "fashion", "stingray", "qwest", "classica",
               "showbiz", "lifeandstyle")
ID_LOCAL = ("jak tv", "rtv", "dim tv", "tbn", "tvri", "jowo", "rodja",
            "elshinta", "btv", "daai", "nusantara", "magna", "cltv",
            "abn tv", "sunna", "crema", "jawa", "selaparang", "salam tv",
            "astha", "ugtv", "usm tv", "smtv", "parijschannel", "ctv")
INTL_BRANDS = ("wion", "cna", "nhk world", "ntd", "cgtn", "trt", "rtb",
               "tv5monde", "france 24", "dw", "arirang", "al jazeera",
               "euronews", "channel news asia", "abc australia")
CAT_ORDER = ["Indonesia", "Lokal", "Sports", "Kids", "Movies", "Music", "Radio",
             "Muslim", "News", "Knowledge & Lifestyle", "Entertainment",
             "Shopping", "CCTV", "China", "Vietnam", "Japan", "India",
             "Philippines", "Asia", "Australia", "Arab",
             "Malaysia", "Singapore", "Korea", "Thailand",
             "International", "Lainnya"]
RANK = {"hls": 0, "ts": 0, "audio": 1, "other": 2, "dash": 3}


def categorise(group, name, tvgid=""):
    g = (group or "").lower()
    n = (name or "").lower()
    for keys, cat in CAT_RULES:
        if any(k in g for k in keys):
            return cat
    for keys, cat in CAT_RULES:
        if any(k in n for k in keys):
            return cat
    if any(b in n for b in ID_LOCAL):
        return "Lokal"
    if any(b in n for b in INTL_BRANDS):
        return "International"
    if any(b in n for b in NEWS_BRANDS):
        return "News"
    if any(b in n for b in LIFE_BRANDS):
        return "Knowledge & Lifestyle"
    # tvg-id carries the country suffix (RCTI.id@SD -> Indonesia)
    t = (tvgid or "").lower()
    if ".id" in t:
        return "Lokal"
    if ".my" in t:
        return "Malaysia"
    if ".sg" in t:
        return "Singapore"
    return "Lainnya"


def norm_name(n):
    n = (n or "").lower()
    n = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", n)
    n = re.sub(r"\b(hd|fhd|uhd|sd|4k|8k|1080p|720p|480p|360p|hevc|h265|h264|avc)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return re.sub(r"\s+", " ", n)


def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse(text, src):
    ents, cur = [], None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("#EXTINF"):
            if cur and cur["url"]:
                ents.append(cur)
            g = re.search(r'group-title="([^"]*)"', line)
            logo = re.search(r'tvg-logo="([^"]*)"', line)
            tvg = re.search(r'tvg-id="([^"]*)"', line)
            nm = line.rsplit(",", 1)[-1].strip()
            cur = {"name": nm, "raw_group": (g.group(1) if g else "").strip(),
                   "logo": logo.group(1) if logo else "",
                   "tvgid": tvg.group(1) if tvg else "",
                   "meta": [], "url": None, "src": src}
        elif cur is not None:
            if line.startswith(("http://", "https://")):
                cur["url"] = line.strip()
            elif line.startswith("#"):
                cur["meta"].append(line.strip())
    if cur and cur["url"]:
        ents.append(cur)
    for e in ents:
        e["cat"] = categorise(e["raw_group"], e["name"], e.get("tvgid", ""))
        e["key"] = norm_name(e["name"])
    return ents


def headers_props(e):
    hdr, props = {}, []
    for m in e["meta"]:
        if m.startswith("#EXTVLCOPT:http-user-agent="):
            hdr["User-Agent"] = m.split("=", 1)[1]
        elif m.startswith("#EXTVLCOPT:http-referrer="):
            hdr["Referer"] = m.split("=", 1)[1]
        elif m.startswith("#KODIPROP:"):
            props.append(m)
    return hdr, props


def to_probe(e):
    hdr, props = headers_props(e)
    return {"name": e["name"], "group": e["cat"], "url": e["url"],
            "headers": hdr, "props": props}


def build_extinf(e):
    def clean(v):
        return re.sub(r'["\r\n]+', "", str(v or "")).strip()
    parts = ["#EXTINF:-1"]
    if e.get("tvgid"):
        parts.append(f'tvg-id="{clean(e["tvgid"])}"')
    if e.get("name"):
        parts.append(f'tvg-name="{clean(e["name"])}"')
    if e.get("logo"):
        parts.append(f'tvg-logo="{clean(e["logo"])}"')
    parts.append(f'group-title="{clean(e["cat"])}"')
    return " ".join(parts) + "," + clean(e["name"])


def emit(path, ents, title=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["#EXTM3U"]
    for e in ents:
        lines.append(build_extinf(e))
        lines.extend(e["meta"])
        lines.append(e["url"])
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    if title:
        print(f"    {os.path.relpath(path, HERE):30} {len(ents):4}  {title}")


def publish():
    """Salin hasil merge ke tata letak repo yang dipakai player."""
    import shutil
    pairs = [("ALL.m3u", "ICNPLAY.m3u"),
             ("FULL.m3u", "ICNPLAY_full.m3u"),
             ("kids.m3u", "kids.m3u"),
             ("DRM_Clearkey.m3u", "DRM_Clearkey.m3u")]
    for src, dst in pairs:
        sp = os.path.join(OUT, src)
        if os.path.exists(sp):
            shutil.copyfile(sp, os.path.join(HERE, dst))
            print(f"    publish {dst}")
    catdir = os.path.join(HERE, "categories")
    os.makedirs(catdir, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(OUT)):
        if not f.endswith(".m3u"):
            continue
        if f in ("ALL.m3u", "FULL.m3u", "kids.m3u", "DRM_Clearkey.m3u") or f.startswith("_"):
            continue
        shutil.copyfile(os.path.join(OUT, f), os.path.join(catdir, f))
        n += 1
    print(f"    publish categories/ -> {n} file")


def main():
    reuse = "--reuse" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    allents = []
    for item in SOURCES:
        src, url = item[0], item[1]
        local = item[2] if len(item) > 2 else None
        try:
            if local and os.path.exists(local) and os.path.getsize(local) > 1000:
                txt = open(local, errors="replace").read()
                print(f"[+] {src:10} {len(txt)} bytes (lokal)")
            else:
                txt = fetch(url)
            got = parse(txt, src)
            print(f"[+] {src:10} {len(got):5} entri")
            allents.extend(got)
        except Exception as ex:                                  # noqa: BLE001
            print(f"[!] {src:10} GAGAL: {type(ex).__name__}")
    print(f"\n[=] total mentah: {len(allents)}")

    # dedupe by URL
    seen, uniq = {}, []
    for e in allents:
        if e["url"] in seen:
            continue
        seen[e["url"]] = e
        uniq.append(e)
    print(f"[=] unik URL: {len(uniq)} (buang {len(allents)-len(uniq)})")

    hj = os.path.join(OUT, "health.json")
    if reuse and os.path.exists(hj):
        print("[*] pakai health.json lama")
        report = json.load(open(hj))
    else:
        probe_file = os.path.join(OUT, "_probe.m3u")
        lines = ["#EXTM3U"]
        for e in uniq:
            hdr, props = headers_props(e)
            lines.append(f'#EXTINF:-1 group-title="{e["cat"]}",{e["name"]}')
            for k, v in hdr.items():
                key = "http-user-agent" if k == "User-Agent" else "http-referrer"
                lines.append(f"#EXTVLCOPT:{key}={v}")
            lines.extend(props)
            lines.append(e["url"])
        open(probe_file, "w").write("\n".join(lines) + "\n")
        print(f"[*] probe {len(uniq)} channel ...")
        report = hc.run(probe_file, hj, workers=48, retries=2)

    res = {r["url"]: r for r in report["results"]}
    alive, drm_ck = [], []
    for e in uniq:
        r = res.get(e["url"])
        if not r:
            continue
        if r["state"] in ("LIVE", "LIVE?"):
            e["ms"] = r.get("ms") or 999999
            e["kind"] = r.get("kind") or "other"
            alive.append(e)
        elif r["state"] == "DRM" and any("clearkey" in p for p in r.get("props", [])):
            e["ms"] = r.get("ms") or 999999
            e["kind"] = r.get("kind") or "other"
            drm_ck.append(e)
    print(f"[=] hidup: {len(alive)} | clearkey DRM: {len(drm_ck)}")

    # dedupe by (category, normalised name) among alive — keep best
    best = {}
    for e in alive:
        k = (e["cat"], e["key"])
        cur = best.get(k)
        if cur is None:
            best[k] = e
            continue
        a = (RANK.get(e["kind"], 4), e["ms"])
        b = (RANK.get(cur["kind"], 4), cur["ms"])
        if a < b:
            best[k] = e
    final = list(best.values())
    print(f"[=] setelah dedupe nama: {len(final)} (buang {len(alive)-len(final)})")

    final.sort(key=lambda e: (CAT_ORDER.index(e["cat"]) if e["cat"] in CAT_ORDER else 99,
                              RANK.get(e["kind"], 4), e["ms"]))
    emit(os.path.join(OUT, "ALL.m3u"), final, "semua channel hidup")
    # FULL = semua URL unik tanpa filter (arsip / pilih manual)
    full = sorted(uniq, key=lambda e: (CAT_ORDER.index(e["cat"]) if e["cat"] in CAT_ORDER else 99,
                                       e["name"].lower()))
    emit(os.path.join(OUT, "FULL.m3u"), full, "semua (tanpa filter)")

    by = OrderedDict()
    for e in final:
        by.setdefault(e["cat"], []).append(e)
    order = [c for c in CAT_ORDER if c in by] + [c for c in by if c not in CAT_ORDER]
    for cat in order:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", cat).strip("_")
        emit(os.path.join(OUT, slug + ".m3u"), by[cat], cat)

    kids = [e for e in final if e["cat"] == "Kids"]
    emit(os.path.join(OUT, "kids.m3u"), kids, "khusus anak")
    drm_ck.sort(key=lambda e: (RANK.get(e["kind"], 4), e["ms"]))
    emit(os.path.join(OUT, "DRM_Clearkey.m3u"), drm_ck, "butuh clearkey (Kodi)")

    man = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "raw": len(allents), "unique_url": len(uniq), "alive": len(alive),
           "final": len(final), "drm_clearkey": len(drm_ck),
           "counts": report["counts"],
           "categories": {c: len(v) for c, v in by.items()},
           "kinds": dict(Counter(e["kind"] for e in final)),
           "by_source": dict(Counter(e["src"] for e in final))}
    json.dump(man, open(os.path.join(OUT, "manifest.json"), "w"), indent=2)
    print(f"\n[+] kategori: {man['categories']}")
    print(f"[+] sumber  : {man['by_source']}")
    print(f"[+] jenis   : {man['kinds']}")
    if "--publish" in sys.argv:
        print("\n[*] publish ke layout repo")
        publish()


if __name__ == "__main__":
    main()

# pipeline: iCN -> tivora -> merge (single workflow, no concurrency race)
