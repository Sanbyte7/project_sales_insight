# Sales Insight: Aplikasi Web Analisis Penjualan UMKM dengan Asisten Berbasis Aturan

**IBM SkillsBuild University Education × Hacktiv8 | Jalur: AI Agent for Data Analysis**

Toko fiktif: **Kedai Rasa Kita** | Status: **Data Simulasi** (bukan transaksi usaha nyata)

> **Catatan pengembangan:** Aplikasi ini dibangun dengan bantuan **IBM Bob** (AI coding assistant). Pemanfaatan AI ada pada proses pengembangan, bukan pada runtime aplikasi — fitur "Tanya Data" menggunakan asisten berbasis aturan yang tidak memerlukan model LLM saat digunakan.

---

## Cara Menjalankan

### Prasyarat
- Python 3.9+ (tidak membutuhkan library eksternal)
- Browser modern (Chrome, Firefox, Edge)

### Langkah

```bash
# 1. Pastikan struktur folder seperti di bawah ini
#    (file CSV sudah tersedia di data/)

# 2. Jalankan server
python server.py

# 3. Buka browser
#    http://localhost:8000
```

Server berjalan di **http://localhost:8000**.  
Tekan `Ctrl+C` untuk menghentikan server.

---

## Struktur File

```
project_sales_insight/
├── index.html                   # Seluruh UI, CSS inline, JS inline
├── server.py                    # Backend: CSV, analisis, asisten berbasis aturan
├── README.md                    # Dokumentasi ini
└── data/
    └── sales_insight_simulasi.csv   # Dataset simulasi
```

---

## Fitur Utama

| Halaman | Fitur |
|---------|-------|
| **Dashboard** | Kartu metrik (total penjualan, transaksi unik, unit, rata-rata nilai transaksi), grafik tren harian/bulanan, grafik produk terlaris, grafik kontribusi kategori, perbandingan periode |
| **Data** | Preview tabel, pencarian, paginasi, unduh CSV terfilter, unduh ringkasan TXT |
| **Tanya Data** | Asisten berbasis aturan: kenali jenis pertanyaan → hitung dari dataset aktif → tampilkan jawaban dengan angka nyata → saran dua pertanyaan lanjutan |

---

## Asisten Berbasis Aturan — Cara Kerja

Fitur **Tanya Data** menggunakan pengenalan pertanyaan berbasis aturan, tanpa model LLM eksternal. Alurnya:

```
Pertanyaan pengguna (teks bebas)
    → Normalisasi huruf kecil dan spasi
    → Pengenalan periode (nama bulan + tahun, YYYY-MM, MM/YYYY)
    → Klasifikasi intent berdasarkan kata kunci
    → Pemanggilan fungsi analisis di server (get_sales_summary, get_top_products, dll.)
    → Pengisian template kalimat dengan angka dari dataset
    → Jawaban + dua saran pertanyaan lanjutan
```

### Jenis Pertanyaan yang Didukung

| Jenis | Kata Kunci Pemicu | Contoh Pertanyaan |
|-------|-------------------|-------------------|
| **Total penjualan** | omzet, omset, total penjualan, pendapatan, pemasukan, revenue, penjualan | "Berapa total penjualan Agustus 2026?" |
| **Jumlah transaksi** | transaksi, berapa kali, frekuensi, jumlah order, jumlah pembelian | "Berapa transaksi bulan Juli 2026?" |
| **Jumlah unit terjual** | unit, jumlah terjual, banyak terjual, item terjual | "Berapa unit terjual September 2026?" |
| **Produk terlaris** | produk, terlaris, best seller, paling laku, paling banyak | "Apa 5 produk terlaris berdasarkan unit?" |
| **Tren penjualan** | tren, trend, grafik, perkembangan, bulanan, harian, per bulan | "Bagaimana tren penjualan bulanan?" |
| **Kontribusi kategori** | kategori, kontribusi, segmen, jenis produk, kelompok | "Kategori mana yang kontribusi penjualannya terbesar?" |
| **Perbandingan periode** | banding, bandingkan, versus, vs (+ dua bulan tersebut) | "Bandingkan penjualan Agustus dan September 2026." |

### Format Periode yang Dikenali

- **Nama bulan Indonesia + tahun:** `Agustus 2026`, `agustus 2026`
- **Format ISO bulan:** `2026-08`
- **Format MM/YYYY:** `08/2026`

Jika tidak ada periode dalam pertanyaan, filter tanggal yang aktif di dashboard digunakan.

### Aturan Pengenalan

1. Urutan prioritas: perbandingan_periode → tren → produk → kategori → unit → transaksi → penjualan.
2. Perbandingan periode hanya dipicu jika pertanyaan mengandung kata perbandingan **dan** dua periode yang dapat dikenali.
3. Produk terlaris by "unit" secara default; berubah ke "penjualan" jika kata kunci "omzet", "pendapatan", atau "total penjualan" ditemukan.
4. Tren "bulanan" secara default; berubah ke "harian" jika kata "harian", "per hari", dll. ditemukan.
5. Top-N dibaca dari angka dalam pertanyaan (digit atau kata bilangan Indonesia); default 5.

### Jawaban Tidak Didukung

Jika pertanyaan tidak cocok dengan jenis di atas, asisten menampilkan penjelasan singkat dan tujuh contoh pertanyaan yang dapat dijawab.

---

## Penjelasan Kolom CSV

| Kolom               | Tipe    | Keterangan |
|---------------------|---------|------------|
| `id_transaksi`      | string  | ID unik transaksi; boleh muncul beberapa kali jika pelanggan membeli >1 produk |
| `tanggal`           | string  | Format `YYYY-MM-DD` |
| `id_produk`         | string  | ID produk |
| `nama_produk`       | string  | Nama produk |
| `kategori`          | string  | Kategori produk (Kopi, Non-Kopi, Makanan, dll.) |
| `jumlah`            | integer | Jumlah unit dalam baris ini; harus > 0 |
| `harga_satuan`      | integer | Harga per unit (Rp); harus > 0 |
| `diskon`            | integer | Potongan harga total untuk baris ini (Rp); ≥ 0 |
| `total_penjualan`   | integer | = `jumlah × harga_satuan − diskon` |
| `kanal_penjualan`   | string  | Contoh: Toko, Pesan Online |
| `metode_pembayaran` | string  | Contoh: QRIS, Tunai, Transfer |
| `sumber_data`       | string  | Selalu `simulasi` pada dataset ini |

> **Catatan:** Pasangan `id_transaksi` + `id_produk` harus unik dalam satu dataset.

---

## Rumus Metrik

| Metrik | Rumus |
|--------|-------|
| Total Penjualan | `SUM(total_penjualan)` pada baris yang lolos filter |
| Jumlah Transaksi Unik | `COUNT(DISTINCT id_transaksi)` |
| Jumlah Unit Terjual | `SUM(jumlah)` |
| Rata-rata Nilai Transaksi | `Total Penjualan ÷ Jumlah Transaksi Unik` |
| Persentase Perubahan | `(Periode 2 − Periode 1) / Periode 1 × 100%` |

> Total penjualan adalah **pendapatan kotor setelah diskon**, **bukan laba**, karena dataset tidak memiliki data biaya produksi.

---

## Alur Aplikasi

### Frontend (`index.html`)
```
Pengguna → Navigasi (Dashboard / Data / Tanya Data)
         → Filter (tanggal, kategori, kanal)
         → API call ke server.py (fetch JSON)
         → Render: kartu metrik, grafik SVG, tabel, chat asisten
```

### Backend (`server.py`)
```
HTTPServer (localhost:8000)
├── GET  /api/status              → status dataset
├── GET  /api/load_default        → baca data/sales_insight_simulasi.csv
├── POST /api/upload_csv          → validasi & simpan dataset baru
├── GET  /api/summary             → get_sales_summary()
├── GET  /api/top_products        → get_top_products()
├── GET  /api/trend               → get_sales_trend()
├── GET  /api/category_contribution → get_category_contribution()
├── GET  /api/rows                → data terpaginasi + pencarian
├── POST /api/compare             → compare_periods()
├── POST /api/agent               → run_rule_agent() (berbasis aturan)
├── GET  /api/export_csv          → unduh CSV terfilter
└── GET  /api/export_summary      → unduh ringkasan TXT
```

### Fungsi Analisis
| Fungsi | Deskripsi |
|--------|-----------|
| `get_sales_summary` | Ringkasan: total, transaksi, unit, rata-rata |
| `get_top_products` | Produk terlaris (by unit atau penjualan) |
| `get_sales_trend` | Tren harian atau bulanan |
| `get_category_contribution` | Kontribusi tiap kategori |
| `compare_periods` | Perbandingan dua rentang tanggal |

### Alur Asisten Berbasis Aturan
```
Pertanyaan pengguna
    → _classify_question()      — tentukan intent dari kata kunci
    → _parse_month_year()       — ekstrak periode jika ada
    → fungsi analisis           — hitung dari dataset aktif di memori
    → template kalimat          — isi dengan angka hasil perhitungan
    → _build_followup()         — buat dua saran pertanyaan lanjutan
    → JSON response ke frontend
```

> Semua angka dalam jawaban berasal dari fungsi analisis yang dijalankan server. Tidak ada angka yang di-hardcode atau dikarang.

---

## Validasi CSV

Server memeriksa:
- Kelengkapan 12 kolom wajib
- Format tanggal `YYYY-MM-DD`
- Nilai kosong pada setiap kolom
- `jumlah` dan `harga_satuan` harus bilangan bulat positif
- `diskon` ≥ 0 dan ≤ `jumlah × harga_satuan`
- `total_penjualan == jumlah × harga_satuan − diskon`
- Tidak ada duplikasi pasangan `id_transaksi` + `id_produk`
- Konsistensi `tanggal`, `kanal_penjualan`, `metode_pembayaran` dalam satu `id_transaksi`

Jika ada kesalahan: **dataset baru ditolak**, dataset sebelumnya tetap aktif, dan semua masalah ditampilkan beserta nomor baris CSV.

---

## Angka Kontrol Pengujian (CSV Default, Tanpa Filter)

| Metrik | Nilai |
|--------|-------|
| Baris item penjualan | 1.200 |
| Transaksi unik | 800 |
| Produk unik | 12 |
| Rentang tanggal | 1 Juli 2026 – 30 September 2026 |
| Total penjualan | Rp 33.404.750 |
| Total penjualan Juli | Rp 11.318.650 |
| Total penjualan Agustus | Rp 13.715.450 |
| Total penjualan September | Rp 8.370.650 |

Angka kontrol digunakan untuk memverifikasi perhitungan, bukan ditanam sebagai jawaban di aplikasi.

---

## Keterbatasan Asisten Berbasis Aturan

- Pengenalan pertanyaan bergantung pada kata kunci; pertanyaan dengan frasa yang tidak terdaftar akan menghasilkan respons "tidak didukung".
- Perbandingan periode hanya mendukung dua bulan yang disebutkan eksplisit dalam pertanyaan; perbandingan dua tanggal spesifik (YYYY-MM-DD) tidak dikenali otomatis melalui teks.
- Asisten tidak mengenali nama produk atau kategori secara langsung dari teks pertanyaan untuk filtering otomatis; filter kategori/kanal hanya diambil dari filter aktif di dashboard.
- Tidak ada pemahaman konteks percakapan — setiap pertanyaan diproses secara independen.

---

## Keterbatasan Data Simulasi

- Data dibuat secara acak untuk tujuan pembelajaran, **bukan** data transaksi bisnis nyata.
- Label "Data Simulasi" ditampilkan di seluruh halaman aplikasi dan dalam setiap unduhan.
- Tren, pola, dan angka **tidak mencerminkan kondisi pasar nyata**.
- Dataset tidak memiliki data biaya produksi; angka yang disebut "penjualan" adalah **pendapatan kotor setelah diskon**, bukan laba.

---

## Keamanan & Batasan

- Tidak ada API key atau kredensial eksternal yang digunakan.
- CSV tidak meninggalkan server; hanya metadata dan hasil fungsi analisis yang dikirim ke frontend.
- `eval()` dan eksekusi kode bebas **tidak digunakan**.
- Ukuran unggahan CSV dibatasi **5 MB**.
- Server hanya mendengarkan `localhost`; tidak dapat diakses dari jaringan luar secara default.
