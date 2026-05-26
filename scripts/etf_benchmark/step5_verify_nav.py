"""Step 5 — cross-check Yahoo's raw close against the issuer's official NAV.

Uses the daily NAV snapshots already collected by the existing ETF fetchers
(data/passive_*.json, data/etf_*.json) as ground truth. For each (ticker, date)
pair where we have both:

    diff_pct = (yahoo_close − nav) / nav × 100

The difference should be close to the 折溢價 (premium/discount), typically
< 1% for liquid TW ETFs. Anything > 2% likely indicates a Yahoo data error
(wrong split adjustment, wrong dividend pickup, etc.) and is flagged FAIL.

Why this matters:
    step4 verified our DB's adj_close == close × ∏(dividend factors). That
    only proves Yahoo is internally consistent — not that the raw close is
    actually right. step5 closes the loop by anchoring close to the issuer's
    real NAV.

Run:
    python -m scripts.etf_benchmark.step5_verify_nav
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DB_PATH  = DATA_DIR / "etf_bench" / "etf_bench.sqlite"

WARN_THRESHOLD_PCT = 1.0
FAIL_THRESHOLD_PCT = 2.0

# These funds hold mostly foreign assets. Market close, NAV timestamp, and FX
# timing can naturally differ by more than 2%, so NAV-vs-close is informational
# for them rather than a data-correctness failure.
STRUCTURAL_NAV_DIFF_TICKERS = {"00830", "00891", "009805", "009820", "00988A"}


def find_history_files() -> list[tuple[str, Path]]:
    """Return list of (ticker, json_path) for every issuer-NAV file we have."""
    out: list[tuple[str, Path]] = []
    # passive_<ticker>_history.json
    for path in DATA_DIR.glob("passive_*_history.json"):
        # e.g. "passive_0050_history.json" → 0050
        ticker = path.stem.replace("passive_", "").replace("_history", "")
        out.append((ticker.upper(), path))
    # etf_<ticker>_history.json (active ETFs)
    for path in DATA_DIR.glob("etf_*_history.json"):
        ticker = path.stem.replace("etf_", "").replace("_history", "")
        out.append((ticker.upper(), path))
    return sorted(out)


def extract_nav_snapshots(path: Path) -> list[tuple[str, float, float | None]]:
    """Return list of (date_iso, nav, closing_price_from_issuer_or_none) from one JSON.
    closing_price is what the fetcher saw on the issuer's site (might be Yahoo-sourced)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    if not isinstance(data, dict):
        return out
    for date_key, payload in data.items():
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta") or {}
        nav = meta.get("nav")
        if nav is None:
            continue
        try:
            nav_f = float(nav)
        except (TypeError, ValueError):
            continue
        if nav_f <= 0:
            continue
        cp = meta.get("closing_price")
        try:
            cp_f = float(cp) if cp is not None else None
        except (TypeError, ValueError):
            cp_f = None
        out.append((date_key, nav_f, cp_f))
    return sorted(out)


def fetch_db_close(conn: sqlite3.Connection, ticker: str, date_iso: str) -> float | None:
    row = conn.execute(
        "SELECT close FROM prices WHERE ticker = ? AND date = ?",
        (ticker, date_iso),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def status_for_diff(ticker: str, abs_diff_pct: float) -> str:
    if ticker in STRUCTURAL_NAV_DIFF_TICKERS:
        return "info"
    if abs_diff_pct > FAIL_THRESHOLD_PCT:
        return "fail"
    if abs_diff_pct > WARN_THRESHOLD_PCT:
        return "warn"
    return "pass"


def log_verification(conn, check_name, results):
    for r in results:
        if r["status"] == "skip":
            continue
        conn.execute(
            "INSERT INTO verification_log "
            "(check_name, ticker, date, expected, actual, delta_pct, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (check_name, r["ticker"], r["date"],
             r.get("nav"), r.get("db_close"), r.get("diff_pct"),
             r["status"], r.get("notes", "")),
        )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None)
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    files = find_history_files()
    if args.tickers:
        wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        files = [(t, p) for t, p in files if t in wanted]
    if not files:
        print("[step5] no issuer NAV history JSON files found in data/")
        return 0

    all_results: list[dict] = []
    print(f"[step5] checking {len(files)} ETF(s) against issuer NAVs:")
    print()

    with sqlite3.connect(DB_PATH) as conn:
        for ticker, path in files:
            snapshots = extract_nav_snapshots(path)
            if not snapshots:
                print(f"  {ticker:8s}  no usable NAV snapshots in {path.name}")
                continue

            per_etf_max_diff = 0.0
            n_pass = n_info = n_warn = n_fail = n_no_db = 0
            results: list[dict] = []
            for date_iso, nav, issuer_cp in snapshots:
                db_close = fetch_db_close(conn, ticker, date_iso)
                if db_close is None:
                    n_no_db += 1
                    results.append({
                        "ticker": ticker, "date": date_iso, "status": "skip",
                        "nav": nav, "db_close": None, "diff_pct": None,
                        "notes": "no row in DB for this date",
                    })
                    continue
                diff_pct = (db_close - nav) / nav * 100.0
                abs_d = abs(diff_pct)
                per_etf_max_diff = max(per_etf_max_diff, abs_d)
                status = status_for_diff(ticker, abs_d)
                if status == "fail":
                    n_fail += 1
                elif status == "warn":
                    n_warn += 1
                elif status == "info":
                    n_info += 1
                else:
                    n_pass += 1
                results.append({
                    "ticker": ticker, "date": date_iso, "status": status,
                    "nav": nav, "db_close": db_close, "diff_pct": round(diff_pct, 4),
                    "notes": (
                        f"issuer_close={issuer_cp}; "
                        + (
                            "structural foreign-market NAV timing difference"
                            if status == "info"
                            else "domestic NAV-vs-close check"
                        )
                    ),
                })
            all_results.extend(results)
            if n_fail:
                overall = "FAIL"
            elif n_warn:
                overall = "WARN"
            elif n_info:
                overall = "INFO"
            else:
                overall = "PASS"
            print(f"  {ticker:8s}  [{overall}] checked {len(snapshots)} dates  "
                  f"max diff = {per_etf_max_diff:.3f}%  "
                  f"(pass={n_pass}, info={n_info}, warn={n_warn}, fail={n_fail}, no_db={n_no_db})")

        log_verification(conn, "yahoo_close_vs_issuer_nav", all_results)

    # Detail print for any non-PASS rows
    bad = [r for r in all_results if r["status"] in ("warn", "fail")]
    if bad:
        print()
        print("  non-PASS dates:")
        for r in bad:
            print(f"    {r['ticker']:8s} {r['date']}  nav={r['nav']:.4f}  "
                  f"db_close={r['db_close']:.4f}  diff={r['diff_pct']:+.3f}%  [{r['status']}]")
    else:
        print()
        print("  ALL actionable dates within issuer NAV thresholds  [PASS]")

    print()
    print(f"[step5] logged {len(all_results)} rows to verification_log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
