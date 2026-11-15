# Social API v2.3.0

Halaman utama di `/` menyediakan input URL, pilihan platform dan layanan, serta tombol submit yang terhubung ke satu endpoint POST tanpa API key atau autentikasi. Body respons berisi `message`, `request_id`, dan `data` (`url`, `platform`, `service`); hasil ditentukan melalui HTTP status code. Detail penyedia, respons mentah, cookie, token, link dan exception internal tidak diteruskan ke pengguna.

## Halaman utama

Buka `http://SERVER_IP:3005/`. Form mengirim JSON ke `/api/claim` pada origin yang sama dan menampilkan HTTP status serta respons `message`, `request_id`, dan `data`. Ada indikator proses, pencegahan submit ganda, dan informasi cooldown dari header `Retry-After`. Halaman menyesuaikan layar ponsel. Pilihan kombinasi yang belum terintegrasi diberi keterangan dan tetap menampilkan respons HTTP 422 dari API.

Halaman disertakan dalam image Docker; tidak memerlukan build frontend, API key, atau hosting terpisah. Respons ditampilkan sebagai teks, bukan HTML. Jika koneksi terputus atau server mengirim format yang tidak dikenali, halaman menampilkan pesan lokal tanpa mengarang hasil klaim atau request_id. Tidak ada retry otomatis.

## Request

```bash
curl -i -X POST http://SERVER_IP:3005/api/claim \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://vt.tiktok.com/CONTOH/","platform":"tiktok","service":"views"}'
```

Ketiga field wajib. `service`: `views`, `likes`, atau `followers`; satu layanan per request. `both` tidak diterima. `platform` dan `service` dinormalisasi menjadi huruf kecil. URL HTTPS harus sesuai allowlist domain platform, tanpa kredensial dan tanpa port selain 443. Link pendek TikTok diterima. Server tidak membuka URL target secara langsung.

## Response

HTTP 200:

```json
{
  "message": "Permintaan berhasil dikonfirmasi oleh layanan. Penambahan metrik dapat memerlukan waktu.",
  "request_id": "f2b218aad9db408bb8d85c749c97e496",
  "data": {
    "url": "https://vt.tiktok.com/CONTOH/",
    "platform": "tiktok",
    "service": "views"
  }
}
```

HTTP 429:

```json
{
  "message": "Batas layanan tercapai. Tunggu sebelum mengirim permintaan berikutnya.",
  "request_id": "d45bde83e69c481591f74d49c56425c9",
  "data": {
    "url": "https://vt.tiktok.com/CONTOH/",
    "platform": "tiktok",
    "service": "views"
  }
}
```

Tidak ada field `success`, `status`, `code`, atau `meta`. Field `data` mengembalikan target request; nilai yang tidak tersedia/salah tipe pada error validasi bernilai `null`. `X-Request-ID` cocok dengan `request_id` di body. `Cache-Control: no-store` disertakan. Jika cooldown dalam detik diketahui secara eksplisit, header `Retry-After` dikirim; jika tidak diketahui, header dihilangkan. Pesan publik berasal dari daftar tetap di aplikasi.

| HTTP status | Arti |
| --- | --- |
| 200 | Permintaan dikonfirmasi sukses oleh layanan |
| 202 | Permintaan diterima/antre/diproses; belum selesai |
| 422 | Input tidak valid, kombinasi belum didukung, atau permintaan ditolak; lihat message |
| 429 | Batas layanan tercapai atau server sedang memproses permintaan lain; lihat message |
| 503 | Layanan sementara tidak tersedia |
| 502 | Hasil klaim belum dapat dipastikan atau respons tidak dikenali |
| 500 | Kesalahan internal tersanitasi |
| 404 | Endpoint tidak ditemukan |
| 405 | Metode HTTP tidak tersedia |

HTTP 200 dari layanan eksternal saja tidak cukup untuk menyatakan sukses: adapter perlu konfirmasi eksplisit. Konfirmasi sukses bukan verifikasi penambahan metrik target. Jangan retry otomatis pada HTTP 202 atau 502 karena permintaan dapat sudah diterima. Tidak ada polling/antrean job dalam versi ini. `/health` juga memakai struktur respons yang sama (field target bernilai null karena healthcheck tidak memiliki target) dan HTTP 200; healthcheck hanya memeriksa proses web.

## Integrasi yang tersedia

- TikTok views dan likes: memakai alur adapter awal.
- TikTok followers, Instagram, YouTube, Facebook, dan X: belum terintegrasi; menerima HTTP 422 dengan pesan kombinasi belum tersedia.

Kontrak input sudah menerima followers. Untuk mengaktifkan integrasi baru, tambahkan adapter yang telah diverifikasi di `provider.py` dan allowlist domain di `HOSTS` pada `app.py`. Jangan menebak endpoint atau format respons. Adapter hanya mengembalikan status internal dan durasi retry, tanpa respons mentah. Alamat penyedia tetap berada dalam source server; source/image tersebut untuk pengelola server.

## Portainer Docker Standalone

1. Build image pada mesin Docker yang dikelola Portainer:

   ```bash
   docker build -t social-api:2.3.0 .
   ```

2. Di Portainer pilih **Stacks → Add stack → Web editor** dan tempel isi `portainer-stack.yaml`.
3. Set variabel opsional: `API_PORT=3005`, `API_IMAGE=social-api:2.3.0`, `ENABLE_DOCS=false`.
4. Deploy stack. Tidak perlu API key. Port akses default 3005 dipetakan ke port internal container 8000 (`3005:8000`). Jika stack atau `.env` lama memiliki `API_PORT`, ubah nilainya menjadi `3005`, lalu redeploy.

Web editor memerlukan image yang sudah dibangun karena tidak memiliki source/build context. Bila Docker endpoint berada di mesin lain, push image ke registry yang dapat diakses endpoint dan set `API_IMAGE`, atau gunakan `docker save` / `docker load` untuk transfer offline. Untuk Swarm gunakan `portainer-swarm.yaml` dengan `API_IMAGE` dari registry yang dapat dijangkau node; pertahankan satu replika. Sintaks Swarm telah divalidasi, tetapi deployment Swarm belum diuji.

## Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Image berjalan sebagai user non-root, filesystem read-only, satu worker, dengan healthcheck, restart policy dan rotasi log. `API_KEY` tidak digunakan, termasuk bila ada variabel lama tersebut. `/docs` dan `/openapi.json` tersedia hanya jika `ENABLE_DOCS=true`; tidak berisi detail penyedia. Untuk domain publik gunakan reverse proxy HTTPS dengan timeout client/proxy minimal 75 detik. Tidak ada autentikasi di aplikasi.

Lock klaim berada di memori, sehingga jalankan satu container/satu worker/satu replika. Lock menolak permintaan bersamaan, bukan kuota per pelanggan. Tidak ada database atau volume wajib. Tidak ada retry otomatis terhadap penyedia.

## Python dan pengujian

Python 3.12 direkomendasikan.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
uvicorn app:app --host 0.0.0.0 --port 3005 --workers 1 --no-server-header
```

Pengujian memakai respons tiruan, tanpa klaim sungguhan. Respons eksternal aktual belum diverifikasi dalam sesi ini; format yang tidak dikenali menghasilkan HTTP 502 dengan pesan hasil belum dapat dipastikan.

Pengujian logika form (Node.js 18+ tanpa dependensi): `node --test test_frontend.cjs`.

## Hasil audit v2.3.0 dan kompatibilitas

- `compose.yaml`: build source lokal dengan Docker Compose v2; `API_IMAGE` menentukan nama image yang dibangun.
- `portainer-stack.yaml`: Docker Standalone, memakai image yang telah dibangun/diunggah ke Docker endpoint. Portainer Web editor tidak membangun source dari ZIP secara otomatis.
- `portainer-swarm.yaml`: image dari registry, satu replika, update stop-first. Sintaks diperiksa; deployment Swarm belum diuji.
- Runtime diverifikasi di Linux amd64. ARM64 belum diuji; jangan menganggap image amd64 bisa langsung dijalankan pada ARM64. Build image pada arsitektur target atau gunakan buildx dengan pengujian terpisah.
- Root filesystem read-only, user 10001, /tmp sementara, healthcheck, rotasi log, dan graceful shutdown tersedia pada stack Standalone.
- Satu worker/replika wajib karena lock berada dalam memori. Healthcheck mengukur proses aplikasi, bukan ketersediaan penyedia. Docker Standalone tidak otomatis me-restart container hanya karena healthcheck unhealthy; restart policy berlaku ketika proses keluar.
- Batas tunggu browser 90 detik; ketika terputus, status klaim tidak pasti dan tidak diulang otomatis.

Upgrade: build `social-api:2.3.0`, ubah `API_IMAGE` stack ke tag tersebut, kemudian redeploy. Jika `.env` lama menyetel tag sebelumnya, perbarui juga nilai tersebut. Untuk Compose lokal jalankan `docker compose up -d --build` setelah memperbarui source dan `.env`.
