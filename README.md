# iCN Play — Playlist Runner + Master M3U (GitHub-ready)

Repo portabel, **stdlib-only Python** (tanpa dependency). Re-derive endpoint
dari obfuscation app `com.icnplay.id` (iCN Play v1.1 build 17), unduh 6 source
M3U, susun **MASTER.m3u flat** (siap di-load Tivimate / IPTV-org / VLC / OTT Navigator).

## Isi repo
```
icnplay-runner/
├── icn_runner.py                 # pipeline utama
├── config.json                   # endpoint + kunci + UA + provenance decode
├── requirements.txt              # (kosong — stdlib only)
├── playlist/
│   ├── MASTER.m3u                # ★ flat playable, 1520 stream, 70+ grup
│   ├── LIVE-TV.m3u … RELAX.m3u   # 6 playlist mentah
├── manifest.json                 # metadata hasil run
└── .github/workflows/refresh-playlists.yml   # auto-refresh daily
```

## Cara menjalankan (lokal / server)
```bash
python3 icn_runner.py            # fetch + download + build MASTER flat
python3 icn_runner.py --no-probe # tanpa probe stream (lebih cepat)
python3 icn_runner.py --list-only # hanya cetak daftar source hidup
```

## Cara load di player (raw URL MASTER.m3u)
Setelah repo di-push & **public**, raw URL-nya:
```
https://raw.githubusercontent.com/<USER>/<REPO>/main/playlist/MASTER.m3u
```
| Player | Cara pakai |
|---|---|
| **Tivimate / IPTV-org / OTT Navigator** | Add Playlist → ketik raw URL di atas |
| **VLC** | Media → Open Network Stream → raw URL, set UA `iCNPL4Y` |
| **Stalkware / KODI (inputstream)** | raw URL, channel pakai clearkey (lihat `#KODIPROP` di playlist) |
| **Download lokal** | `curl -o MASTER.m3u <raw URL>` lalu import |

> Catatan: stream Vision+ (CloudFront `.mpd`) butuh **clearkey** yang sudah
> terbawa di `#KODIPROP:inputstream.adaptive.license_key` — player KODI/OTT
> otomatis pakai; player yang cuma HLS polos mungkin tidak bisa decode.

## Langkah upload ke GitHub
```bash
cd icnplay-runner
git init
git add .
git commit -m "iCN Play playlist runner + flat MASTER"
git branch -M main
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```
Repo **public** → raw URL MASTER.m3u bisa langsung di-load semua player.

## Otomatis
`.github/workflows/refresh-playlists.yml` men-commit ulang playlist + manifest
tiap hari (04:15 UTC) + selasa/jumat siang, jadi raw URL selalu fresh.

## Provenance / rahasia (untuk audit)
| Item | Nilai |
|---|---|
| Obfuscation | `SplashActivity.t()` = Base64 + XOR-13 |
| XOR key | `1CNPL4Y_CNL86` (bytes di `config.json.decoded_from`) |
| Endpoint | `https://my.idetimur.id/icnplay` |
| App UA | `iCNPL4Y` |
| Kunci | `Panca86` (di redirect `?kunci=`) |
| Verifikasi ulang dari APK | `python3 icn_runner.py --apk /path/icnplay.apk` |

## Keandalan
Channel count & status di atas hasil tool nyata (HTTP 200 + hitung `#EXTINF`).
Stream individu flapping normal untuk agregator — playlist hidup ≠ semua channel
nyala di jam yang sama.
