"""Refresh the Market Pulse TWSE volume cache.

The Streamlit page must stay read-only and fast, so it reads the local
``data/market_pulse_volume.csv`` cache instead of calling TWSE during render.

Daily use:
    python scripts/update_market_pulse_volume.py --months 4

One-time server history init:
    python scripts/update_market_pulse_volume.py --backfill-years 5
"""
from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd
import requests
import urllib3


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_PATH = ROOT_DIR / "data" / "market_pulse_volume.csv"
TWSE_FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def roc_to_date(text: str) -> pd.Timestamp:
    year, month, day = str(text).split("/")
    return pd.Timestamp(int(year) + 1911, int(month), int(day))


def month_anchors(months: int) -> list[pd.Timestamp]:
    today = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None).normalize()
    first = today.replace(day=1)
    return [first - pd.DateOffset(months=i) for i in range(max(1, int(months)))]


def fetch_month(anchor: pd.Timestamp, timeout: int = 20) -> pd.DataFrame:
    ymd = anchor.strftime("%Y%m01")
    response = requests.get(
        TWSE_FMTQIK_URL,
        params={"date": ymd, "response": "json"},
        headers=HEADERS,
        timeout=timeout,
        verify=False,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("stat") != "OK":
        return pd.DataFrame(columns=["date", "close", "turnover", "volume"])

    rows = []
    for row in payload.get("data") or []:
        try:
            rows.append(
                {
                    "date": roc_to_date(row[0]).date().isoformat(),
                    "close": float(str(row[4]).replace(",", "")),
                    "turnover": float(str(row[2]).replace(",", "")),
                    "volume": float(str(row[1]).replace(",", "")),
                }
            )
        except Exception:
            continue
    return pd.DataFrame(rows, columns=["date", "close", "turnover", "volume"])


def load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "close", "turnover", "volume"])
    df = pd.read_csv(path)
    return df[[c for c in ["date", "close", "turnover", "volume"] if c in df.columns]]


def atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8-sig", dir=path.parent, delete=False, newline="") as tmp:
        tmp_path = Path(tmp.name)
        df.to_csv(tmp, index=False)
    tmp_path.replace(path)


def refresh_cache(path: Path, months: int) -> pd.DataFrame:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    frames = []
    existing = load_existing(path)
    if not existing.empty:
        frames.append(existing)
    for anchor in month_anchors(months):
        fetched = fetch_month(anchor)
        if not fetched.empty:
            frames.append(fetched)
    if not frames:
        return pd.DataFrame(columns=["date", "close", "turnover", "volume"])
    combined = pd.concat(frames, ignore_index=True)
    if combined.empty:
        return combined
    combined = (
        combined.dropna(subset=["date"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    atomic_write_csv(path, combined)
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh cached TWSE volume data for Market Pulse.")
    parser.add_argument("--months", type=int, default=4, help="Recent calendar months to refresh.")
    parser.add_argument(
        "--backfill-years",
        type=int,
        default=0,
        help="One-time history init; overrides --months with years * 12 months.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args()

    months = args.backfill_years * 12 if args.backfill_years else args.months
    df = refresh_cache(args.output, months=months)
    if df.empty:
        print(f"[market-volume] no rows written to {args.output}")
        return 1
    print(
        f"[market-volume] rows={len(df):,} range={df['date'].min()}..{df['date'].max()} "
        f"path={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
