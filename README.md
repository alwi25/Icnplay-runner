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
