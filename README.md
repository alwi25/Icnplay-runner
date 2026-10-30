# iCN Play — Playlist Runner + Auto-Checked M3U (GitHub-ready)

Repo portabel, **stdlib-only Python** (tanpa dependency). Re-derive endpoint dari
obfuscation app `com.icnplay.id` (iCN Play v1.1 build 17), unduh semua source,
susun **playlist flat** yang siap di-load Tivimate / IPTV-org / VLC / OTT Navigator,
lalu **auto-check** tiap channel dan buang hanya yang benar-benar mati.

## Link player (langsung pakai)
```
https://raw.githubusercontent.com/alwi25/Icnplay-runner/main/ICNPLAY.m3u
```
Cadangan bila raw kena cache CDN:
```
https://cdn.jsdelivr.net/gh/alwi25/Icnplay-runner@main/ICNPLAY.m3u
```
Playlist lengkap tanpa filter: `ICNPLAY_full.m3u`.

## Isi repo
```
├── icn_runner.py                 # pipeline utama
├── health_check.py               # prober + klasifikasi channel
├── config.json                   # endpoint + kunci + UA + provenance decode
├── ICNPLAY.m3u                   # ★ playlist hasil health-check (dipakai player)
├── ICNPLAY_full.m3u              # semua channel, tanpa filter (fallback)
├── playlist/                     # copy per-source + MASTER.m3u
├── manifest.json                 # metadata run (source, stale, health)
├── health.json                   # laporan probe per channel + per grup
└── .github/workflows/refresh-playlists.yml   # auto-refresh tiap 6 jam
```

## Cara menjalankan
```bash
python3 icn_runner.py                    # fetch + build playlist (tanpa probe penuh)
python3 icn_runner.py --check            # + auto-check, buang DEAD
python3 icn_runner.py --check --workers 48 --retries 2
python3 icn_runner.py --check-only       # cek ulang ICNPLAY.m3u yang ada
python3 icn_runner.py --list-only        # cetak daftar source
python3 health_check.py ICNPLAY.m3u --json health.json   # laporan saja
```

## Auto-check stream
Probe tiap channel **dengan header `#EXTVLCOPT` yang dipakai player** (UA +
referrer), lalu validasi isi respons benar-benar manifest — bukan halaman HTML error.

| Status | Arti | Aksi |
|---|---|---|
| `LIVE` | 200 + manifest valid (HLS `#EXT-X-` / DASH `<MPD` / MPEG-TS `0x47`) | disimpan |
| `LIVE?` | 200 tapi isi tidak bisa dipastikan | disimpan |
| `DRM` | 401/403 **dan** ada `license_key` clearkey | **disimpan** — player putar pakai clearkey |
| `AUTH` | 401/403 tanpa license | disimpan, ditandai |
| `FLAKY` | reset/TLS/timeout (sudah di-retry 2x) | disimpan — host bisa pulih |
| `DEAD` | 404/410/400/DNS | **dibuang** |

**Hanya `DEAD` yang dibuang.** Channel DRM kerap balas 403 ke probe urllib kosong
tapi tetap jalan di player karena handshake clearkey — memangkasnya akan
menghapus ratusan channel sehat.

### Pengaman (supaya tidak pernah merusak playlist)
1. **Batas aman 40%** — kalau probe menandai >40% channel mati, pemangkasan
   **dibatalkan**. Itu tanda jaringan probe-nya yang bermasalah, bukan playlist.
2. **Retry FLAKY** 2x dengan backoff, jadi timeout sesaat tidak dihitung mati.
3. **Fallback source** — kalau satu host gagal diunduh, runner memakai salinan
   terakhir yang sudah ada di repo (dicatat `stale: true` di `manifest.json`),
   bukan membuang seluruh source.
4. **CI verifikasi** lewat GitHub API (bukan raw URL yang kena cache CDN) dan
   memastikan `EXTINF == jumlah URL`.

## Playlist varian (pilih sesuai kebutuhan)
| File | Isi | Kapan dipakai |
|---|---|---|
| `ICNPLAY.m3u` | semua channel sehat (LIVE/LIVE?/DRM/AUTH/FLAKY) | default, player lengkap (Kodi/OTT) |
| **`kids.m3u`** | **hanya channel anak yang benar-benar hidup** (36 ch), HLS dulu lalu DASH, urut tercepat | kalau channel Kids terasa lama |
| `ICNPLAY_fast.m3u` | hanya yang live & TTFB <= 3 dtk (950+ ch), HLS dulu | player yang lambat menggantung |
| `ICNPLAY_full.m3u` | semua tanpa filter (1522 ch) | arsip / pilih manual |

### Kenapa channel Kids dulu terasa "lama sekali"
Dari pengukuran nyata: 66 entri Kids, **hanya 36 yang hidup**; sisanya 403
(Transvision/Vision+ butuh header khusus) atau URLError (IndiHome geo-block).
Player mencoba entri rusak itu satu per satu (retry + timeout) sebelum sampai ke
yang hidup — itulah jeda panjangnya. `kids.m3u` cuma memuat yang hidup, dan
**HLS ditaruh lebih dulu** karena langsung main di semua player tanpa
`inputstream.adaptive`.

## Tivora (sumber kedua) — `tivora/`
Dibangun dari `https://aspaltvpasti.top/tivora/get.php` oleh `tivora_build.py`:
dedupe (URL + nama), probe semua, buang mati/DRM-403, kategori dirapikan,
urut tercepat dengan HLS di depan.

| File | Isi |
|---|---|
| `tivora/MASTER.m3u` | **481 channel hidup**, semua kategori |
| `tivora/Indonesia.m3u` | 34 |
| `tivora/Sports.m3u` | 122 |
| `tivora/Murattal_30_Juz.m3u` | 114 (audio MP3) |
| `tivora/Lokal.m3u` | 74 |
| `tivora/Music.m3u` | 23 |
| `tivora/Knowledge_Lifestyle.m3u` | 22 |
| `tivora/Malaysia.m3u` | 21 |
| `tivora/Kids.m3u` + `tivora/kids.m3u` | 18 |
| `tivora/Movies.m3u` | 17 |
| `tivora/Muslim.m3u` | 16 |
| `tivora/Entertainment.m3u` | 11 |
| `tivora/Fight.m3u` | 8 |
| `tivora/DRM_Clearkey.m3u` | 16 (butuh Kodi/OTT + inputstream.adaptive) |

Dari 750 entri: 42 URL ganda + 19 nama ganda dibuang → 689 unik → **481 hidup**.
Kategori mentah yang berantakan (`SPORTS 2`, `LOKAL` vs `Lokal`, `ENTERTAIMENT`)
sudah dinormalkan.

## Playlist gabungan (utama) — `ICNPLAY.m3u`
`merge_build.py` menyatukan **tujuh sumber**, membuang duplikat, mem-probe semua
channel, membuang yang mati, lalu menyusun ulang kategori:

| Sumber | Peran |
|---|---|
| iCN Play (hasil `icn_runner.py`) | channel Indonesia + live TV |
| `tivora/MASTER.m3u` (aspaltvpasti) | sports, murattal, lokal |
| iptv-org `countries/id` | channel Indonesia terkurasi |
| iptv-org `countries/my`, `countries/sg` | Malaysia, Singapura |
| iptv-org `categories/kids` | channel anak |
| iptv-org `categories/sports` | sport internasional |

Hasil: 3.311 entri mentah → 2.157 URL unik → **1.727 channel hidup**, 19 kategori.

| Kategori | Ch | Kategori | Ch |
|---|---|---|---|
| Sports | 502 | Movies | 71 |
| Kids | 309 | Radio | 60 |
| Muslim | 140 | Lainnya | 60 |
| Lokal | 120 | Knowledge & Lifestyle | 42 |
| Indonesia | 118 | Thailand | 40 |
| CCTV | 110 | Music | 35 |
| Korea | 30 | News | 28 |
| Malaysia | 28 | Entertainment | 27 |
| Singapore | 6 | Shopping | 1 |

File kategori ada di `categories/*.m3u`; master di `ICNPLAY.m3u`;
tanpa filter di `ICNPLAY_full.m3u`; `DRM_Clearkey.m3u` untuk Kodi.

## Cara load di player
| Player | Cara pakai |
|---|---|
| **Tivimate / IPTV-org / OTT Navigator** | Add Playlist → tempel URL `ICNPLAY.m3u` |
| **VLC** | Media → Open Network Stream → URL tersebut |
| **KODI / Stalkware (inputstream)** | URL yang sama; channel Vision+ pakai clearkey bawaan `#KODIPROP` |
| **Lokal** | `curl -o ICNPLAY.m3u <url>` lalu import |

> Stream Vision+ (CloudFront `.mpd`) butuh **clearkey** yang sudah terbawa di
> `#KODIPROP:inputstream.adaptive.license_key` — KODI/OTT otomatis pakai;
> player HLS polos mungkin tidak bisa decode.

## Jadwal
Workflow jalan tiap **6 jam** (`20 */6 * * *`), bisa manual dari tab Actions.
Setiap run: refresh → health-check → commit `ICNPLAY.m3u`, `ICNPLAY_full.m3u`,
`health.json`, `manifest.json`.

## Provenance / rahasia (untuk audit)
| Item | Nilai |
|---|---|
| Obfuscation | `SplashActivity.t()` = Base64 + XOR-13 |
| XOR key | `1CNPL4Y_CNL86` (bytes di `config.json.decoded_from`) |
| Endpoint | `https://my.idetimur.id/icnplay` |
| App UA | `iCNPL4Y` |
| Kunci | `Panca86` (muncul di redirect `?kunci=`) |
| Verifikasi ulang dari APK | `python3 icn_runner.py --apk /path/icnplay.apk` |

## Keandalan
Semua angka (jumlah channel, status, health) berasal dari request nyata.
Stream agregator memang flapping: playlist hidup ≠ semua channel nyala di jam
yang sama. `health.json` menyimpan hasil per-channel supaya bisa diaudit.
