"""
Sales Insight — backend server
Kedai Rasa Kita | IBM SkillsBuild x Hacktiv8 Capstone
Run: python server.py
"""

import os, json, csv, io, math, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, date
from calendar import monthrange
from collections import defaultdict

DEFAULT_CSV_PATH = os.path.join("data", "sales_insight_simulasi.csv")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# ── In-memory state ───────────────────────────────────────────────────────────
_active_rows: list[dict] = []   # validated rows
_dataset_meta: dict = {}        # date range, categories, channels, etc.

# ── CSV helpers ───────────────────────────────────────────────────────────────
REQUIRED_COLS = [
    "id_transaksi","tanggal","id_produk","nama_produk","kategori",
    "jumlah","harga_satuan","diskon","total_penjualan",
    "kanal_penjualan","metode_pembayaran","sumber_data",
]

def parse_csv_bytes(raw: bytes) -> tuple[list[dict], list[str]]:
    """Parse raw CSV bytes → (rows, errors). rows empty on any error."""
    # strip UTF-8 BOM
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # check columns
    if reader.fieldnames is None:
        return [], ["File CSV kosong atau tidak dapat dibaca."]
    missing = [c for c in REQUIRED_COLS if c not in reader.fieldnames]
    if missing:
        return [], [f"Kolom wajib tidak ditemukan: {', '.join(missing)}"]

    errors = []
    rows = []
    seen_pairs: dict[tuple, int] = {}        # (id_transaksi, id_produk) → csv line
    trx_meta: dict[str, dict] = {}           # id_transaksi → {tanggal, kanal, metode}

    for i, raw_row in enumerate(reader):
        line_no = i + 2  # 1-based, header is line 1

        def err(msg):
            errors.append(f"Baris {line_no}: {msg}")

        # strip whitespace
        row = {k: (v.strip() if v else "") for k, v in raw_row.items()}

        # --- required non-empty ----
        for col in REQUIRED_COLS:
            if row.get(col, "") == "":
                err(f"Kolom '{col}' kosong.")

        if errors:
            continue

        # --- tanggal ----
        tanggal_str = row["tanggal"]
        try:
            tanggal = date.fromisoformat(tanggal_str)
        except ValueError:
            err(f"Format tanggal tidak valid: '{tanggal_str}' (diharapkan YYYY-MM-DD).")
            continue

        # --- numerik ----
        def parse_int(col):
            val = row[col]
            if not re.fullmatch(r"-?\d+", val):
                err(f"Kolom '{col}' harus bilangan bulat, ditemukan: '{val}'.")
                return None
            return int(val)

        jumlah        = parse_int("jumlah")
        harga_satuan  = parse_int("harga_satuan")
        diskon        = parse_int("diskon")
        total_penjualan = parse_int("total_penjualan")

        if any(v is None for v in [jumlah, harga_satuan, diskon, total_penjualan]):
            continue

        if jumlah <= 0:
            err(f"'jumlah' harus positif, ditemukan: {jumlah}.")
        if harga_satuan <= 0:
            err(f"'harga_satuan' harus positif, ditemukan: {harga_satuan}.")
        if diskon < 0:
            err(f"'diskon' tidak boleh negatif, ditemukan: {diskon}.")

        nilai_sebelum_diskon = jumlah * harga_satuan
        if diskon > nilai_sebelum_diskon:
            err(f"'diskon' ({diskon}) melebihi nilai sebelum diskon ({nilai_sebelum_diskon}).")

        expected_total = nilai_sebelum_diskon - diskon
        if total_penjualan != expected_total:
            err(
                f"'total_penjualan' tidak sesuai rumus: "
                f"{jumlah}×{harga_satuan}−{diskon}={expected_total}, "
                f"tercatat {total_penjualan}."
            )

        if errors:
            continue

        # --- duplikasi pasangan id_transaksi + id_produk ----
        pair = (row["id_transaksi"], row["id_produk"])
        if pair in seen_pairs:
            err(
                f"Duplikasi pasangan id_transaksi+id_produk "
                f"'{row['id_transaksi']}' + '{row['id_produk']}' "
                f"(duplikat dari baris {seen_pairs[pair]})."
            )
            continue
        seen_pairs[pair] = line_no

        # --- konsistensi dalam satu transaksi ----
        trx_id = row["id_transaksi"]
        if trx_id in trx_meta:
            meta = trx_meta[trx_id]
            if meta["tanggal"] != tanggal_str:
                err(
                    f"Tanggal tidak konsisten dalam transaksi '{trx_id}': "
                    f"baris sebelumnya '{meta['tanggal']}', baris ini '{tanggal_str}'."
                )
            if meta["kanal"] != row["kanal_penjualan"]:
                err(
                    f"Kanal penjualan tidak konsisten dalam transaksi '{trx_id}': "
                    f"baris sebelumnya '{meta['kanal']}', baris ini '{row['kanal_penjualan']}'."
                )
            if meta["metode"] != row["metode_pembayaran"]:
                err(
                    f"Metode pembayaran tidak konsisten dalam transaksi '{trx_id}': "
                    f"baris sebelumnya '{meta['metode']}', baris ini '{row['metode_pembayaran']}'."
                )
            if errors:
                continue
        else:
            trx_meta[trx_id] = {
                "tanggal": tanggal_str,
                "kanal":   row["kanal_penjualan"],
                "metode":  row["metode_pembayaran"],
            }

        rows.append({
            "id_transaksi":     row["id_transaksi"],
            "tanggal":          tanggal_str,
            "id_produk":        row["id_produk"],
            "nama_produk":      row["nama_produk"],
            "kategori":         row["kategori"],
            "jumlah":           jumlah,
            "harga_satuan":     harga_satuan,
            "diskon":           diskon,
            "total_penjualan":  total_penjualan,
            "kanal_penjualan":  row["kanal_penjualan"],
            "metode_pembayaran": row["metode_pembayaran"],
            "sumber_data":      row["sumber_data"],
        })

    return (rows, errors) if not errors else ([], errors)


def build_meta(rows: list[dict]) -> dict:
    if not rows:
        return {}
    dates      = [r["tanggal"] for r in rows]
    categories = sorted({r["kategori"] for r in rows})
    channels   = sorted({r["kanal_penjualan"] for r in rows})
    products   = sorted({r["nama_produk"] for r in rows})
    return {
        "total_rows":  len(rows),
        "date_min":    min(dates),
        "date_max":    max(dates),
        "categories":  categories,
        "channels":    channels,
        "products":    products,
    }

# ── Filter helper ─────────────────────────────────────────────────────────────
def apply_filters(rows, date_from=None, date_to=None, kategori=None, kanal=None):
    out = rows
    if date_from:
        out = [r for r in out if r["tanggal"] >= date_from]
    if date_to:
        out = [r for r in out if r["tanggal"] <= date_to]
    if kategori:
        out = [r for r in out if r["kategori"] == kategori]
    if kanal:
        out = [r for r in out if r["kanal_penjualan"] == kanal]
    return out

# ── Analysis functions ────────────────────────────────────────────────────────
def get_sales_summary(rows, date_from=None, date_to=None, kategori=None, kanal=None):
    filtered = apply_filters(rows, date_from, date_to, kategori, kanal)
    if not filtered:
        return {
            "has_data": False,
            "total_penjualan": 0,
            "jumlah_transaksi": 0,
            "jumlah_unit": 0,
            "rata_rata_transaksi": 0,
            "filter": {"date_from": date_from, "date_to": date_to,
                       "kategori": kategori, "kanal": kanal},
        }
    total = sum(r["total_penjualan"] for r in filtered)
    trx   = len({r["id_transaksi"] for r in filtered})
    units = sum(r["jumlah"] for r in filtered)
    avg   = total / trx if trx else 0
    return {
        "has_data": True,
        "total_penjualan": total,
        "jumlah_transaksi": trx,
        "jumlah_unit": units,
        "rata_rata_transaksi": round(avg, 2),
        "filter": {"date_from": date_from, "date_to": date_to,
                   "kategori": kategori, "kanal": kanal},
    }


def get_top_products(rows, top_n=5, date_from=None, date_to=None,
                     kategori=None, kanal=None, by="unit"):
    filtered = apply_filters(rows, date_from, date_to, kategori, kanal)
    agg: dict[str, dict] = {}
    for r in filtered:
        nm = r["nama_produk"]
        if nm not in agg:
            agg[nm] = {"nama_produk": nm, "jumlah_unit": 0, "total_penjualan": 0}
        agg[nm]["jumlah_unit"]    += r["jumlah"]
        agg[nm]["total_penjualan"] += r["total_penjualan"]
    sort_key = "total_penjualan" if by == "penjualan" else "jumlah_unit"
    ranked = sorted(agg.values(), key=lambda x: x[sort_key], reverse=True)[:top_n]
    return {
        "products": ranked,
        "top_n": top_n,
        "sorted_by": sort_key,
        "filter": {"date_from": date_from, "date_to": date_to,
                   "kategori": kategori, "kanal": kanal},
    }


def get_sales_trend(rows, granularity="harian", date_from=None, date_to=None,
                    kategori=None, kanal=None):
    filtered = apply_filters(rows, date_from, date_to, kategori, kanal)
    bucket: dict[str, dict] = {}
    for r in filtered:
        if granularity == "bulanan":
            key = r["tanggal"][:7]   # YYYY-MM
        else:
            key = r["tanggal"]       # YYYY-MM-DD
        if key not in bucket:
            bucket[key] = {"periode": key, "total_penjualan": 0,
                           "jumlah_unit": 0, "transaksi": set()}
        bucket[key]["total_penjualan"] += r["total_penjualan"]
        bucket[key]["jumlah_unit"]     += r["jumlah"]
        bucket[key]["transaksi"].add(r["id_transaksi"])
    result = []
    for k in sorted(bucket):
        b = bucket[k]
        result.append({
            "periode":          k,
            "total_penjualan":  b["total_penjualan"],
            "jumlah_unit":      b["jumlah_unit"],
            "jumlah_transaksi": len(b["transaksi"]),
        })
    return {
        "trend": result,
        "granularity": granularity,
        "filter": {"date_from": date_from, "date_to": date_to,
                   "kategori": kategori, "kanal": kanal},
    }


def get_category_contribution(rows, date_from=None, date_to=None,
                              kategori=None, kanal=None):
    filtered = apply_filters(rows, date_from, date_to, kategori, kanal)
    agg: dict[str, int] = defaultdict(int)
    for r in filtered:
        agg[r["kategori"]] += r["total_penjualan"]
    grand_total = sum(agg.values())
    result = []
    for cat in sorted(agg, key=lambda c: agg[c], reverse=True):
        pct = round(agg[cat] / grand_total * 100, 2) if grand_total else 0
        result.append({"kategori": cat, "total_penjualan": agg[cat], "persen": pct})
    return {
        "categories": result,
        "grand_total": grand_total,
        "filter": {"date_from": date_from, "date_to": date_to,
                   "kategori": kategori, "kanal": kanal},
    }


def compare_periods(rows, period1_from, period1_to, period2_from, period2_to,
                    kategori=None, kanal=None):
    def _period_data(df, dt):
        filtered = apply_filters(rows, df, dt, kategori, kanal)
        if not filtered:
            return None   # no data at all for this period
        total = sum(r["total_penjualan"] for r in filtered)
        trx   = len({r["id_transaksi"] for r in filtered})
        units = sum(r["jumlah"] for r in filtered)
        return {"total_penjualan": total, "jumlah_transaksi": trx, "jumlah_unit": units}

    p1 = _period_data(period1_from, period1_to)
    p2 = _period_data(period2_from, period2_to)

    def pct_change(v1, v2):
        if v1 is None or v2 is None:
            return None
        if v1 == 0:
            return None
        return round((v2 - v1) / v1 * 100, 2)

    def safe_pct(v1, v2, key):
        if v1 is None or v2 is None:
            return "Persentase perubahan tidak tersedia (salah satu periode tidak memiliki data)"
        a, b = v1.get(key, 0), v2.get(key, 0)
        if a == 0:
            return "Persentase perubahan tidak tersedia (nilai periode pertama nol)"
        return round((b - a) / a * 100, 2)

    return {
        "periode_1": {"from": period1_from, "to": period1_to, "data": p1},
        "periode_2": {"from": period2_from, "to": period2_to, "data": p2},
        "perubahan": {
            "total_penjualan":  safe_pct(p1, p2, "total_penjualan"),
            "jumlah_transaksi": safe_pct(p1, p2, "jumlah_transaksi"),
            "jumlah_unit":      safe_pct(p1, p2, "jumlah_unit"),
        },
        "filter": {"kategori": kategori, "kanal": kanal},
    }

# ── Rule-based assistant ───────────────────────────────────────────────────────

# Indonesian month names → month number
BULAN_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

def _fmt_rp(n: int) -> str:
    return f"Rp{n:,}".replace(",", ".")

def _fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")

def _period_label(date_from: str, date_to: str) -> str:
    if date_from == date_to:
        return date_from
    return f"{date_from} s/d {date_to}"

def _filter_label(context_filter: dict) -> str:
    parts = []
    if context_filter.get("date_from") or context_filter.get("date_to"):
        parts.append(f"periode {_period_label(context_filter.get('date_from',''), context_filter.get('date_to',''))}")
    if context_filter.get("kategori"):
        parts.append(f"kategori {context_filter['kategori']}")
    if context_filter.get("kanal"):
        parts.append(f"kanal {context_filter['kanal']}")
    return ", ".join(parts) if parts else "seluruh dataset"


def _parse_month_year(text: str) -> tuple[str, str] | None:
    """
    Extract a month+year from text, returning (date_from, date_to) as YYYY-MM-DD strings.
    Supports: "Agustus 2026", "agustus 2026", "2026-08", "08/2026".
    Returns None if not found.
    """
    text = text.lower().strip()
    # format: nama_bulan tahun
    for name, num in BULAN_ID.items():
        m = re.search(rf"\b{name}\s+(\d{{4}})\b", text)
        if m:
            year = int(m.group(1))
            last_day = monthrange(year, num)[1]
            return (f"{year:04d}-{num:02d}-01", f"{year:04d}-{num:02d}-{last_day:02d}")
    # format YYYY-MM
    m = re.search(r"\b(\d{4})-(\d{2})\b", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            last_day = monthrange(year, month)[1]
            return (f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}")
    # format MM/YYYY
    m = re.search(r"\b(\d{1,2})/(\d{4})\b", text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            last_day = monthrange(year, month)[1]
            return (f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}")
    return None


def _parse_two_months(text: str) -> tuple[tuple, tuple] | None:
    """
    Find exactly two month+year mentions in text.
    Handles patterns like:
      - "Agustus 2026 dan September 2026"
      - "Agustus dan September 2026"  (shared trailing year)
      - "2026-07 dan 2026-08"
    Returns (period1, period2) each as (date_from, date_to), or None.
    """
    text_low = text.lower()
    found = []
    # track character positions of month-name matches that already have a year
    captured_month_positions: set[int] = set()

    # Pass 1: exact "month_name year" pairs
    for name, num in BULAN_ID.items():
        for m in re.finditer(rf"\b{name}\s+(\d{{4}})\b", text_low):
            year = int(m.group(1))
            last_day = monthrange(year, num)[1]
            found.append((m.start(), f"{year:04d}-{num:02d}-01",
                          f"{year:04d}-{num:02d}-{last_day:02d}"))
            captured_month_positions.add(m.start())

    # Pass 2: ISO YYYY-MM pairs
    for m in re.finditer(r"\b(\d{4})-(\d{2})\b", text_low):
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            last_day = monthrange(year, month)[1]
            found.append((m.start(), f"{year:04d}-{month:02d}-01",
                          f"{year:04d}-{month:02d}-{last_day:02d}"))

    # Pass 3: standalone month names — borrow nearest year
    # (handles "Agustus dan September 2026")
    year_tokens = [(m.start(), int(m.group(1)))
                   for m in re.finditer(r"\b(\d{4})\b", text_low)
                   if 2020 <= int(m.group(1)) <= 2099]

    for name, num in BULAN_ID.items():
        for m in re.finditer(rf"\b{name}\b", text_low):
            pos = m.start()
            # skip if this position was already captured with an explicit year
            if pos in captured_month_positions:
                continue
            # borrow the nearest year token
            if year_tokens:
                nearest = min(year_tokens, key=lambda t: abs(t[0] - pos))
                year = nearest[1]
                last_day = monthrange(year, num)[1]
                found.append((pos, f"{year:04d}-{num:02d}-01",
                              f"{year:04d}-{num:02d}-{last_day:02d}"))

    # sort by position, deduplicate by date range
    found.sort(key=lambda x: x[0])
    unique = []
    seen: set[tuple] = set()
    for pos, df, dt in found:
        key = (df, dt)
        if key not in seen:
            seen.add(key)
            unique.append((df, dt))

    if len(unique) >= 2:
        return (unique[0], unique[1])
    return None


def _extract_top_n(text: str, default: int = 5) -> int:
    """Extract a number like '5', '10', 'lima', 'sepuluh' from text."""
    text_low = text.lower()
    # digit
    m = re.search(r"\b(\d+)\b", text_low)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 50:
            return n
    # Indonesian words
    word_map = {"satu":1,"dua":2,"tiga":3,"empat":4,"lima":5,
                "enam":6,"tujuh":7,"delapan":8,"sembilan":9,"sepuluh":10}
    for word, val in word_map.items():
        if re.search(rf"\b{word}\b", text_low):
            return val
    return default


def _classify_question(text: str) -> str:
    """
    Classify the question into one of:
    total_penjualan, jumlah_transaksi, jumlah_unit,
    top_products, tren_penjualan, kontribusi_kategori,
    perbandingan_periode, unsupported
    """
    t = text.lower().strip()
    # perbandingan: must be checked before individual metrics
    compare_kw = ["banding", "kompar", "dibanding", "versus", " vs ", "periode 1", "periode 2",
                  "antara bulan", "bandingkan"]
    if any(kw in t for kw in compare_kw) and _parse_two_months(t):
        return "perbandingan_periode"

    # tren
    trend_kw = ["tren", "trend", "grafik", "perkembangan", "evolusi",
                "naik turun", "dari waktu ke waktu", "per bulan", "per hari",
                "tiap bulan", "setiap bulan", "tiap hari", "setiap hari",
                "bulanan", "harian", "time series"]
    if any(kw in t for kw in trend_kw):
        return "tren_penjualan"

    # produk terlaris
    product_kw = ["produk", "terlaris", "best seller", "paling laku",
                  "paling banyak", "ranking produk", "urutan produk"]
    if any(kw in t for kw in product_kw):
        return "top_products"

    # kategori
    category_kw = ["kategori", "kontribusi", "segmen", "jenis produk", "kelompok"]
    if any(kw in t for kw in category_kw):
        return "kontribusi_kategori"

    # jumlah unit
    unit_kw = ["unit", "jumlah terjual", "banyak terjual", "item terjual",
               "berapa item", "berapa unit"]
    if any(kw in t for kw in unit_kw):
        return "jumlah_unit"

    # jumlah transaksi
    trx_kw = ["transaksi", "berapa kali", "frekuensi", "jumlah order",
              "jumlah pembelian", "berapa order"]
    if any(kw in t for kw in trx_kw):
        return "jumlah_transaksi"

    # total penjualan
    revenue_kw = ["omzet", "omset", "total penjualan", "pendapatan", "pemasukan",
                  "revenue", "berapa penjualan", "penjualan", "hasil penjualan"]
    if any(kw in t for kw in revenue_kw):
        return "total_penjualan"

    return "unsupported"


def _build_followup(intent: str, result: dict, period: tuple | None) -> list[str]:
    """Generate two relevant follow-up question suggestions."""
    suggestions = {
        "total_penjualan": [
            "Produk apa yang paling banyak terjual?",
            "Apa kontribusi penjualan setiap kategori?",
        ],
        "jumlah_transaksi": [
            "Berapa total penjualan pada periode yang sama?",
            "Produk apa yang paling banyak terjual berdasarkan unit?",
        ],
        "jumlah_unit": [
            "Berapa total penjualan (omzet) pada periode yang sama?",
            "Apa 5 produk terlaris berdasarkan total penjualan?",
        ],
        "top_products": [
            "Apa kontribusi penjualan setiap kategori?",
            "Bagaimana tren penjualan bulanan?",
        ],
        "tren_penjualan": [
            "Berapa total penjualan secara keseluruhan?",
            "Produk apa yang paling banyak terjual?",
        ],
        "kontribusi_kategori": [
            "Apa 5 produk terlaris berdasarkan unit terjual?",
            "Bagaimana tren penjualan bulanan?",
        ],
        "perbandingan_periode": [
            "Berapa total penjualan secara keseluruhan?",
            "Produk apa yang paling banyak terjual?",
        ],
        "unsupported": [
            "Berapa total penjualan (omzet)?",
            "Apa 5 produk terlaris berdasarkan unit terjual?",
        ],
    }
    # if a period was found, make follow-up period-aware
    if period and intent in ("total_penjualan", "jumlah_transaksi", "jumlah_unit"):
        month_str = period[0][:7]  # YYYY-MM
        suggestions[intent][1] = f"Apa 5 produk terlaris pada {month_str}?"
    return suggestions.get(intent, suggestions["unsupported"])


def run_rule_agent(question: str, context_filter: dict) -> dict:
    """
    Rule-based question answering. No external model required.
    Returns { answer, steps, followup, data_ready }
    """
    if not _active_rows:
        return {
            "answer": "Belum ada dataset yang dimuat. Silakan muat dataset terlebih dahulu.",
            "steps": [],
            "followup": [],
            "data_ready": False,
        }

    meta       = _dataset_meta
    q_original = question.strip()
    q_low      = q_original.lower()

    # -- Detect period in question --
    period = _parse_month_year(q_low)

    # Determine effective date range
    if period:
        df, dt = period
    else:
        df = context_filter.get("date_from") or None
        dt = context_filter.get("date_to")   or None

    kategori = context_filter.get("kategori") or None
    kanal    = context_filter.get("kanal")    or None

    period_desc = _period_label(df or meta.get("date_min",""), dt or meta.get("date_max",""))
    filter_desc = []
    if period:
        filter_desc.append(f"periode {period_desc}")
    else:
        filter_desc.append(f"filter aktif ({period_desc})")
    if kategori:
        filter_desc.append(f"kategori {kategori}")
    if kanal:
        filter_desc.append(f"kanal {kanal}")
    filter_str = ", ".join(filter_desc)

    intent = _classify_question(q_low)
    steps  = [{"function": intent, "params": {"date_from": df, "date_to": dt,
                                               "kategori": kategori, "kanal": kanal}}]

    # ── Total penjualan ──────────────────────────────────────────────────────
    if intent == "total_penjualan":
        r = get_sales_summary(_active_rows, df, dt, kategori, kanal)
        if not r["has_data"]:
            answer = f"Tidak ada data penjualan untuk {filter_str}."
        else:
            answer = (
                f"Total penjualan (omzet setelah diskon) untuk {filter_str} "
                f"adalah **{_fmt_rp(r['total_penjualan'])}**. "
                f"Diperoleh dari {_fmt_num(r['jumlah_transaksi'])} transaksi unik "
                f"dengan {_fmt_num(r['jumlah_unit'])} unit terjual. "
                f"Rata-rata nilai per transaksi: {_fmt_rp(round(r['rata_rata_transaksi']))}."
            )
        followup = _build_followup(intent, r, period)

    # ── Jumlah transaksi ─────────────────────────────────────────────────────
    elif intent == "jumlah_transaksi":
        r = get_sales_summary(_active_rows, df, dt, kategori, kanal)
        if not r["has_data"]:
            answer = f"Tidak ada data transaksi untuk {filter_str}."
        else:
            answer = (
                f"Jumlah transaksi unik untuk {filter_str} "
                f"adalah **{_fmt_num(r['jumlah_transaksi'])}** transaksi. "
                f"Total penjualan pada periode ini: {_fmt_rp(r['total_penjualan'])}. "
                f"Total unit terjual: {_fmt_num(r['jumlah_unit'])} unit."
            )
        followup = _build_followup(intent, r, period)

    # ── Jumlah unit ──────────────────────────────────────────────────────────
    elif intent == "jumlah_unit":
        r = get_sales_summary(_active_rows, df, dt, kategori, kanal)
        if not r["has_data"]:
            answer = f"Tidak ada data unit terjual untuk {filter_str}."
        else:
            answer = (
                f"Jumlah unit terjual untuk {filter_str} "
                f"adalah **{_fmt_num(r['jumlah_unit'])}** unit. "
                f"Angka ini berasal dari {_fmt_num(r['jumlah_transaksi'])} transaksi unik "
                f"dengan total penjualan {_fmt_rp(r['total_penjualan'])}."
            )
        followup = _build_followup(intent, r, period)

    # ── Produk terlaris ──────────────────────────────────────────────────────
    elif intent == "top_products":
        by = "penjualan" if any(kw in q_low for kw in
                                ["omzet","pendapatan","total penjualan","penjualan terbesar"]) \
             else "unit"
        top_n = _extract_top_n(q_low, default=5)
        r = get_top_products(_active_rows, top_n=top_n, date_from=df, date_to=dt,
                             kategori=kategori, kanal=kanal, by=by)
        products = r.get("products", [])
        sort_label = "total penjualan" if by == "penjualan" else "jumlah unit terjual"
        if not products:
            answer = f"Tidak ada data produk untuk {filter_str}."
        else:
            lines = [f"**{top_n} produk terlaris** berdasarkan {sort_label} ({filter_str}):"]
            for i, p in enumerate(products, 1):
                lines.append(
                    f"{i}. {p['nama_produk']} — "
                    f"{_fmt_num(p['jumlah_unit'])} unit, "
                    f"{_fmt_rp(p['total_penjualan'])}"
                )
            answer = "\n".join(lines)
        followup = _build_followup(intent, r, period)

    # ── Tren penjualan ───────────────────────────────────────────────────────
    elif intent == "tren_penjualan":
        gran = "harian" if any(kw in q_low for kw in ["harian","per hari","setiap hari","tiap hari"]) \
               else "bulanan"
        r = get_sales_trend(_active_rows, granularity=gran, date_from=df, date_to=dt,
                            kategori=kategori, kanal=kanal)
        trend = r.get("trend", [])
        gran_label = "harian" if gran == "harian" else "bulanan"
        if not trend:
            answer = f"Tidak ada data tren {gran_label} untuk {filter_str}."
        else:
            # summarise: total periods, highest, lowest
            top_p = max(trend, key=lambda x: x["total_penjualan"])
            bot_p = min(trend, key=lambda x: x["total_penjualan"])
            total_all = sum(x["total_penjualan"] for x in trend)
            answer = (
                f"Tren penjualan **{gran_label}** ({filter_str}):\n"
                f"- Total {len(trend)} periode data ditemukan.\n"
                f"- Penjualan tertinggi: **{_fmt_rp(top_p['total_penjualan'])}** "
                f"pada {top_p['periode']} ({_fmt_num(top_p['jumlah_transaksi'])} transaksi).\n"
                f"- Penjualan terendah: **{_fmt_rp(bot_p['total_penjualan'])}** "
                f"pada {bot_p['periode']}.\n"
                f"- Total keseluruhan periode: {_fmt_rp(total_all)}.\n"
                f"Lihat grafik Tren Penjualan di halaman Dashboard untuk visualisasi lengkap."
            )
        followup = _build_followup(intent, r, period)

    # ── Kontribusi kategori ──────────────────────────────────────────────────
    elif intent == "kontribusi_kategori":
        r = get_category_contribution(_active_rows, df, dt, kategori, kanal)
        cats = r.get("categories", [])
        grand = r.get("grand_total", 0)
        if not cats:
            answer = f"Tidak ada data kategori untuk {filter_str}."
        else:
            lines = [f"**Kontribusi kategori** terhadap total penjualan ({filter_str}):"]
            for c in cats:
                lines.append(
                    f"- {c['kategori']}: {_fmt_rp(c['total_penjualan'])} "
                    f"({c['persen']}%)"
                )
            lines.append(f"\nTotal keseluruhan: {_fmt_rp(grand)}.")
            answer = "\n".join(lines)
        followup = _build_followup(intent, r, period)

    # ── Perbandingan periode ─────────────────────────────────────────────────
    elif intent == "perbandingan_periode":
        two = _parse_two_months(q_low)
        if not two:
            answer = (
                "Untuk perbandingan periode, sebutkan dua bulan secara eksplisit. "
                "Contoh: \"Bandingkan penjualan Agustus 2026 dan September 2026.\""
            )
            followup = _build_followup("unsupported", {}, None)
        else:
            (df1, dt1), (df2, dt2) = two
            r = compare_periods(_active_rows, df1, dt1, df2, dt2, kategori, kanal)
            p1d = r["periode_1"]["data"]
            p2d = r["periode_2"]["data"]
            ch  = r["perubahan"]

            def _pct_str(val) -> str:
                if val is None or isinstance(val, str):
                    return "tidak tersedia"
                sign = "+" if val >= 0 else ""
                return f"{sign}{val:.2f}%"

            def _val_str(d, key, fmt_fn) -> str:
                return fmt_fn(d[key]) if d else "tidak ada data"

            p1_label = _period_label(df1, dt1)
            p2_label = _period_label(df2, dt2)

            answer = (
                f"**Perbandingan penjualan**: {p1_label} vs {p2_label}"
                + (f" (kategori: {kategori})" if kategori else "")
                + (f" (kanal: {kanal})" if kanal else "")
                + "\n\n"
                + f"**Total Penjualan:**\n"
                + f"- Periode 1 ({p1_label}): {_val_str(p1d,'total_penjualan',_fmt_rp)}\n"
                + f"- Periode 2 ({p2_label}): {_val_str(p2d,'total_penjualan',_fmt_rp)}\n"
                + f"- Perubahan: {_pct_str(ch.get('total_penjualan'))}\n\n"
                + f"**Jumlah Transaksi Unik:**\n"
                + f"- Periode 1: {_val_str(p1d,'jumlah_transaksi',_fmt_num)}\n"
                + f"- Periode 2: {_val_str(p2d,'jumlah_transaksi',_fmt_num)}\n"
                + f"- Perubahan: {_pct_str(ch.get('jumlah_transaksi'))}\n\n"
                + f"**Jumlah Unit Terjual:**\n"
                + f"- Periode 1: {_val_str(p1d,'jumlah_unit',_fmt_num)}\n"
                + f"- Periode 2: {_val_str(p2d,'jumlah_unit',_fmt_num)}\n"
                + f"- Perubahan: {_pct_str(ch.get('jumlah_unit'))}"
            )
            if not p1d or not p2d:
                answer += "\n\n⚠️ Salah satu periode tidak memiliki data dalam dataset."
            followup = _build_followup(intent, r, None)

    # ── Tidak didukung ───────────────────────────────────────────────────────
    else:
        answer = (
            "Maaf, pertanyaan ini belum didukung oleh asisten berbasis aturan. "
            "Berikut jenis pertanyaan yang dapat dijawab:\n"
            "- **Total penjualan / omzet**: \"Berapa total penjualan Agustus 2026?\"\n"
            "- **Jumlah transaksi**: \"Berapa transaksi bulan Juli 2026?\"\n"
            "- **Jumlah unit terjual**: \"Berapa unit terjual bulan September 2026?\"\n"
            "- **Produk terlaris**: \"Apa 5 produk terlaris berdasarkan unit?\"\n"
            "- **Tren penjualan**: \"Bagaimana tren penjualan bulanan?\"\n"
            "- **Kontribusi kategori**: \"Apa kontribusi setiap kategori?\"\n"
            "- **Perbandingan periode**: \"Bandingkan penjualan Agustus dan September 2026.\""
        )
        followup = _build_followup("unsupported", {}, None)
        steps = []

    return {
        "answer":     answer,
        "steps":      steps,
        "followup":   followup,
        "data_ready": True,
    }


# ── Export helpers ────────────────────────────────────────────────────────────
def export_csv(rows) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REQUIRED_COLS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def export_summary_txt(summary: dict, filters: dict) -> bytes:
    lines = [
        "=== Ringkasan Analisis Penjualan ===",
        "Toko        : Kedai Rasa Kita",
        "Status data : Data Simulasi (bukan transaksi usaha nyata)",
        f"Periode     : {filters.get('date_from','—')} s/d {filters.get('date_to','—')}",
        f"Kategori    : {filters.get('kategori') or 'Semua'}",
        f"Kanal       : {filters.get('kanal') or 'Semua'}",
        "",
        f"Total Penjualan (setelah diskon) : Rp{summary.get('total_penjualan', 0):,}",
        f"Jumlah Transaksi Unik            : {summary.get('jumlah_transaksi', 0)}",
        f"Jumlah Unit Terjual              : {summary.get('jumlah_unit', 0)}",
        f"Rata-rata Nilai Transaksi        : Rp{summary.get('rata_rata_transaksi', 0):,.0f}",
        "",
        "Catatan: Total penjualan adalah pendapatan kotor setelah diskon.",
        "         Angka ini BUKAN laba karena biaya produksi tidak tersedia.",
        "",
        f"Diekspor pada: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines).encode("utf-8")


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # suppress default logs
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, filename: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        def qp(key, default=None):
            vals = qs.get(key)
            return vals[0] if vals else default

        # ── serve index.html ──
        if path in ("/", "/index.html"):
            try:
                with open("index.html", "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._send_json({"error": "index.html tidak ditemukan."}, 404)
            return

        if path == "/api/status":
            self._send_json({
                "has_data":   bool(_active_rows),
                "meta":       _dataset_meta,
                "data_ready": bool(_active_rows),
            })
            return

        if path == "/api/load_default":
            self._load_default_csv()
            return

        if path == "/api/summary":
            result = get_sales_summary(
                _active_rows,
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
            )
            self._send_json(result)
            return

        if path == "/api/top_products":
            try:
                top_n = int(qp("top_n", 5))
            except ValueError:
                top_n = 5
            result = get_top_products(
                _active_rows,
                top_n=top_n,
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
                by=qp("by", "unit"),
            )
            self._send_json(result)
            return

        if path == "/api/trend":
            result = get_sales_trend(
                _active_rows,
                granularity=qp("granularity", "harian"),
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
            )
            self._send_json(result)
            return

        if path == "/api/category_contribution":
            result = get_category_contribution(
                _active_rows,
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
            )
            self._send_json(result)
            return

        if path == "/api/rows":
            # paginated preview
            try:
                page = max(1, int(qp("page", 1)))
                page_size = min(100, max(1, int(qp("page_size", 50))))
            except ValueError:
                page, page_size = 1, 50
            search = qp("search", "").lower()
            filtered = apply_filters(
                _active_rows,
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
            )
            if search:
                filtered = [
                    r for r in filtered
                    if any(search in str(v).lower() for v in r.values())
                ]
            total = len(filtered)
            start = (page - 1) * page_size
            page_rows = filtered[start : start + page_size]
            self._send_json({
                "rows": page_rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": math.ceil(total / page_size) if total else 1,
            })
            return

        if path == "/api/export_csv":
            filtered = apply_filters(
                _active_rows,
                date_from=qp("date_from"),
                date_to=qp("date_to"),
                kategori=qp("kategori"),
                kanal=qp("kanal"),
            )
            self._send_bytes(export_csv(filtered), "text/csv", "data_filter.csv")
            return

        if path == "/api/export_summary":
            filters = {
                "date_from": qp("date_from"),
                "date_to":   qp("date_to"),
                "kategori":  qp("kategori"),
                "kanal":     qp("kanal"),
            }
            summary = get_sales_summary(_active_rows, **filters)
            self._send_bytes(export_summary_txt(summary, filters), "text/plain", "ringkasan.txt")
            return

        self._send_json({"error": "Endpoint tidak ditemukan."}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/api/upload_csv":
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_UPLOAD_BYTES:
                self._send_json({"ok": False, "errors": [
                    f"File terlalu besar (maks 5 MB, diterima {length/1024/1024:.1f} MB)."
                ]})
                return
            raw = self.rfile.read(length)
            self._process_csv(raw)
            return

        if path == "/api/agent":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            question = str(body.get("question", "")).strip()
            context  = body.get("filter", {})
            if not question:
                self._send_json({"error": "Pertanyaan kosong."}, 400)
                return
            result = run_rule_agent(question, context)
            self._send_json(result)
            return

        if path == "/api/compare":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            result = compare_periods(
                _active_rows,
                period1_from=body.get("period1_from",""),
                period1_to=body.get("period1_to",""),
                period2_from=body.get("period2_from",""),
                period2_to=body.get("period2_to",""),
                kategori=body.get("kategori"),
                kanal=body.get("kanal"),
            )
            self._send_json(result)
            return

        self._send_json({"error": "Endpoint tidak ditemukan."}, 404)

    def _process_csv(self, raw: bytes):
        global _active_rows, _dataset_meta
        rows, errors = parse_csv_bytes(raw)
        if errors:
            self._send_json({"ok": False, "errors": errors})
        else:
            _active_rows  = rows
            _dataset_meta = build_meta(rows)
            self._send_json({"ok": True, "meta": _dataset_meta})

    def _load_default_csv(self):
        global _active_rows, _dataset_meta
        if not os.path.exists(DEFAULT_CSV_PATH):
            self._send_json({"ok": False, "errors": [
                f"File default tidak ditemukan: {DEFAULT_CSV_PATH}"
            ]})
            return
        with open(DEFAULT_CSV_PATH, "rb") as f:
            raw = f.read()
        rows, errors = parse_csv_bytes(raw)
        if errors:
            self._send_json({"ok": False, "errors": errors})
        else:
            _active_rows  = rows
            _dataset_meta = build_meta(rows)
            self._send_json({"ok": True, "meta": _dataset_meta})


if __name__ == "__main__":
    host, port = "localhost", 8000
    server = HTTPServer((host, port), Handler)
    print(f"✅  Sales Insight berjalan di  http://{host}:{port}")
    print(f"   Asisten      : Berbasis Aturan (tidak memerlukan model AI eksternal)")
    print(f"   Dataset path : {DEFAULT_CSV_PATH}")
    print("   Tekan Ctrl+C untuk menghentikan.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
