"""Step 1 — build the ETF master universe.

Source 1: TWSE OpenAPI /opendata/t187ap47_L (基金基本資料彙總表)
    → all TWSE-listed funds (~256 rows as of 2026-05).

Source 2: scripts/etf_benchmark/seed_tpex_etfs.csv
    → hand-curated TPEx bond ETFs (00679B, 00687B, ...). The TWSE list
      does NOT contain TPEx-listed bond ETFs, so we seed them manually.
      step3 will verify each ticker against Yahoo Finance.

Output: data/etf_bench/universe.csv  (one row per ETF, UTF-8 with BOM)

Run:
    python -m scripts.etf_benchmark.step1_universe
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import requests

# Fixed benchmark window so the 2Y UI preset always has enough room even when
# "today" is a weekend and the latest market close is a prior trading day.
BENCH_START = "2024-01-01"

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DATA_DIR = ROOT_DIR / "data" / "etf_bench"
SEED_TPEX = Path(__file__).parent / "seed_tpex_etfs.csv"
OUT_CSV = DATA_DIR / "universe.csv"

TWSE_FUND_LIST_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"


# ─── ROC date → ISO date ──────────────────────────────────────────────────
def roc_to_iso(roc_yyymmdd: str) -> str | None:
    """Convert ROC date like '0920625' or '1150409' to '2003-06-25' / '2026-04-09'.

    Empty/garbage returns None.
    """
    s = (roc_yyymmdd or "").strip()
    if not s.isdigit() or len(s) not in (6, 7):
        return None
    if len(s) == 6:
        s = "0" + s
    try:
        roc_year = int(s[:3])
        month = int(s[3:5])
        day = int(s[5:7])
        return date(roc_year + 1911, month, day).isoformat()
    except (ValueError, TypeError):
        return None


# ─── Category → simple type ───────────────────────────────────────────────
# Keywords that — if found in the tracked index name — strongly imply
# a bond ETF, even when TWSE's category_raw says "證券指數股票型基金".
# (TWSE doesn't have a separate "債券型" category for overseas-listed
# bond ETFs like 00865B; we have to infer it from the index name.)
_BOND_INDEX_KEYWORDS = (
    "公債", "債券", "債指數", "投資級", "投資等級", "投等",
    "高評級", "金融債", "公司債", "Treasury", "treasury", "Bond", "bond",
    "Aggregate", "aggregate", "Corporate", "corporate",
)


def classify(category_zh: str, levinv_flag: str, tracked_index: str = "",
             ticker: str = "", name: str = "") -> str:
    """Reduce TWSE's 11 fund categories + the leveraged/inverse flag to a
    simple type used by the benchmark engine.

    Possible outputs:
        passive_equity   ETF tracking an equity index (e.g., 0050)
        active_equity    Active-managed equity ETF (e.g., 00981A)
        bond             Fixed-income ETF (e.g., 00865B, 00679B)
        commodity        Futures-based commodity ETF (e.g., 00635U)
        leveraged        Leveraged or inverse (毎日重置 — flagged separately)
        other            Anything else (catch-all)

    Detection order matters: leveraged > bond > commodity > active > passive.
    """
    cat = (category_zh or "").strip()
    idx = (tracked_index or "").strip()
    t   = (ticker or "").upper()
    n   = (name or "")

    # 1) Leveraged / inverse (check first — these can be equity, bond, or commodity)
    if "槓桿" in cat or "反向" in cat or "槓桿" in n or "反向" in n or "正2" in n or "反1" in n:
        return "leveraged"
    if len(t) >= 2 and t[-1] in ("L", "R") and t[:-1].replace("0","").isdigit() is False:
        # Tickers like 00633L, 00632R
        return "leveraged"

    # 2) Bond — TWSE category contains "債" OR tracked index name signals bond
    if "債" in cat:
        return "bond"
    if any(kw in idx for kw in _BOND_INDEX_KEYWORDS):
        return "bond"

    # 3) Commodity / futures (gold, oil, etc.)
    if "期貨" in cat or "商品" in cat:
        return "commodity"

    # 4) Active managed
    if "主動式" in cat:
        return "active_equity"

    # 5) Default passive equity
    if "股票" in cat or "指數" in cat:
        return "passive_equity"
    return "other"


def is_leveraged_or_inverse(ticker: str, name: str) -> bool:
    """Most TW leveraged/inverse ETFs end with 'L' (2x long) or 'R' (-1x).
    Also catch '正2' / '反1' in the Chinese name as a backup.
    """
    t = (ticker or "").upper()
    n = name or ""
    if t.endswith(("L", "R")) and t[:-1].isdigit() is False:
        # Tickers like 00633L, 00632R
        pass
    if len(t) >= 2 and t[-1] in ("L", "R") and t[:-1].isdigit() is False:
        # e.g. 00675L
        return True
    if "正2" in n or "反1" in n or "槓桿" in n or "反向" in n:
        return True
    return False


# ─── Fetchers ─────────────────────────────────────────────────────────────
def fetch_twse_funds() -> list[dict]:
    print(f"[step1] GET {TWSE_FUND_LIST_URL}")
    r = requests.get(TWSE_FUND_LIST_URL, timeout=20)
    r.raise_for_status()
    raw = r.json()
    if not raw:
        raise RuntimeError("TWSE returned empty fund list")
    keys = list(raw[0].keys())
    # Field positions verified 2026-05 against the live API:
    #   1 = 證券代號, 2 = 基金簡稱, 3 = 基金類別, 4 = 基金名稱,
    #   5 = 基金英文名稱, 6 = 標的指數中文名稱,
    #  12 = 是否為槓桿/反向/商品期貨/單一外國股權證券指數,
    #  14 = 成立日期(ROC), 15 = 上市/櫃日期(ROC),
    #  19 = 經理公司簡稱, 24 = 發行單位數
    k_ticker     = keys[1]
    k_short      = keys[2]
    k_category   = keys[3]
    k_fullname   = keys[4]
    k_enname     = keys[5]
    k_index      = keys[6]
    k_levinv     = keys[12]
    k_inception  = keys[14]
    k_listing    = keys[15]
    k_issuer     = keys[19]
    k_units      = keys[24]

    out = []
    for row in raw:
        ticker = (row[k_ticker] or "").strip().upper()
        if not ticker:
            continue
        name = (row[k_short] or "").strip()
        out.append({
            "ticker":          ticker,
            "name":            name,
            "full_name":       (row[k_fullname] or "").strip(),
            "en_name":         (row[k_enname] or "").strip(),
            "issuer":          (row[k_issuer] or "").strip(),
            "category_raw":    (row[k_category] or "").strip(),
            "tracked_index":   (row[k_index] or "").strip(),
            "twse_levinv_flag": (row[k_levinv] or "").strip(),
            "inception_date":  roc_to_iso(row[k_inception]),
            "listing_date":    roc_to_iso(row[k_listing]),
            "units_issued":    (row[k_units] or "").strip(),
            "market":          "TWSE",
            "source":          "twse_opendata",
        })
    return out


def load_tpex_seed() -> list[dict]:
    """Hand-curated TPEx ETFs (bond + a few leveraged). step3 will validate
    each ticker against yfinance using the .TWO suffix.
    """
    if not SEED_TPEX.exists():
        print(f"[step1] (no TPEx seed at {SEED_TPEX}, skipping)")
        return []
    out = []
    with SEED_TPEX.open("r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            out.append({
                "ticker":          ticker,
                "name":            (row.get("name") or "").strip(),
                "full_name":       (row.get("name") or "").strip(),
                "en_name":         "",
                "issuer":          (row.get("issuer") or "").strip(),
                "category_raw":    (row.get("category_raw") or "債券型").strip(),
                "tracked_index":   (row.get("tracked_index") or "").strip(),
                "twse_levinv_flag": "",
                "inception_date":  (row.get("inception_date") or "").strip() or None,
                "listing_date":    (row.get("listing_date") or "").strip() or None,
                "units_issued":    "",
                "market":          "TPEx",
                "source":          "tpex_seed",
            })
    return out


# ─── Verification ─────────────────────────────────────────────────────────
def verify(rows: list[dict]) -> list[str]:
    """Return list of WARNING / FAIL strings. Empty == clean."""
    issues: list[str] = []
    n = len(rows)

    if n < 200:
        issues.append(f"FAIL: only {n} rows (expected ~250+)")
    elif n > 400:
        issues.append(f"WARN: {n} rows (expected ~250-300)")

    # Required known tickers must exist
    required = ["0050", "0056", "00878", "00891", "00865B", "00635U", "00403A", "00981A", "00988A"]
    by_t = {r["ticker"]: r for r in rows}
    for t in required:
        if t not in by_t:
            issues.append(f"FAIL: required ticker {t} missing")

    # No row should have empty ticker, name, or inception_date
    for r in rows:
        if not r["ticker"]:
            issues.append("FAIL: row with empty ticker")
        if not r["name"]:
            issues.append(f"WARN: {r['ticker']} has empty name")
        if not r["inception_date"] and r["source"] == "twse_opendata":
            issues.append(f"WARN: {r['ticker']} has no inception_date")

    # Duplicate tickers
    seen: set[str] = set()
    for r in rows:
        t = r["ticker"]
        if t in seen:
            issues.append(f"FAIL: duplicate ticker {t}")
        seen.add(t)

    return issues


# ─── Main ─────────────────────────────────────────────────────────────────
def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    twse_rows = fetch_twse_funds()
    print(f"[step1] TWSE: {len(twse_rows)} funds")

    tpex_rows = load_tpex_seed()
    print(f"[step1] TPEx seed: {len(tpex_rows)} funds")

    # Merge — TWSE takes precedence on collision
    merged: dict[str, dict] = {}
    for r in tpex_rows:
        merged[r["ticker"]] = r
    for r in twse_rows:
        merged[r["ticker"]] = r
    rows = sorted(merged.values(), key=lambda r: r["ticker"])

    # Add derived fields
    for r in rows:
        r["fund_type"] = classify(
            r["category_raw"],
            r["twse_levinv_flag"],
            tracked_index=r.get("tracked_index", ""),
            ticker=r["ticker"],
            name=r["name"],
        )
        r["is_leveraged_inverse"] = is_leveraged_or_inverse(r["ticker"], r["name"])
        # data_start_date = max(fixed benchmark window start, inception)
        inc = r.get("inception_date")
        r["data_start_date"] = max(BENCH_START, inc) if inc else BENCH_START

    # Verify
    issues = verify(rows)
    fails = [i for i in issues if i.startswith("FAIL")]
    warns = [i for i in issues if i.startswith("WARN")]

    print(f"\n[step1] verification: {len(fails)} FAIL, {len(warns)} WARN")
    for i in issues:
        print(f"  {i}")

    if fails:
        print(f"\n[step1] ABORT — fix FAILs before writing universe.csv")
        return 1

    # Write
    fieldnames = [
        "ticker", "name", "market", "fund_type", "is_leveraged_inverse",
        "issuer", "tracked_index", "inception_date", "listing_date",
        "data_start_date", "category_raw", "twse_levinv_flag",
        "units_issued", "full_name", "en_name", "source",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"\n[step1] wrote {len(rows)} rows → {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
