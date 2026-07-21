"""Build the daily TWSE + TPEx public-data financing-collateral estimate.

This is deliberately *not* labelled as the official market-wide account
maintenance rate.  Exchanges publish each security's margin balance, closing
price, and the market's outstanding financing amount; they do not publish a
daily open-data series containing every client's supplementary collateral.

Estimate used here::

    sum(non-ETF margin balance units * closing price * 1,000)
    ------------------------------------------------ * 100
        aggregate outstanding financing amount

Both numerator and denominator combine TWSE and TPEx official daily reports.
The Streamlit tab and LINE webhook only read the resulting local cache.

Daily use (refresh a small overlap so corrections are picked up):
    python scripts/update_margin_maintenance.py --days 10

One-time server history init:
    python scripts/update_margin_maintenance.py --backfill-years 1
"""
from __future__ import annotations

import argparse
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd
import requests
import urllib3


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_PATH = ROOT_DIR / "data" / "margin_maintenance.csv"
MARKET_DATES_PATH = ROOT_DIR / "data" / "market_pulse_volume.csv"

TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_PRICE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_MARGIN_URL = (
    "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/"
    "margin_bal_result.php"
)
TPEX_PRICE_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/"
    "stk_quote_result.php"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; STOCK-dashboard/1.0; daily official-data cache)",
    "Accept": "application/json,text/plain,*/*",
    # The legacy TPEx endpoint occasionally truncates compressed/chunked
    # responses. Asking for identity encoding makes its small JSON payloads
    # substantially more reliable from the production host.
    "Accept-Encoding": "identity",
    "Connection": "close",
}
OFFICIAL_RETRIES = 4
COLUMNS = [
    "date",
    "estimate_pct",
    "twse_estimate_pct",
    "tpex_estimate_pct",
    "collateral_value_billion",
    "financing_balance_billion",
    "twse_collateral_billion",
    "twse_financing_billion",
    "tpex_collateral_billion",
    "tpex_financing_billion",
    "excluded_etf_collateral_billion",
    "excluded_etf_count",
    "taiex_close",
    "twse_matched",
    "twse_total",
    "tpex_matched",
    "tpex_total",
]


@dataclass(frozen=True)
class MarketSlice:
    collateral_thousand: float
    financing_thousand: float
    matched: int
    total: int
    excluded_etf_thousand: float
    excluded_etf_count: int

    @property
    def estimate_pct(self) -> float:
        if self.financing_thousand <= 0:
            return math.nan
        return self.collateral_thousand / self.financing_thousand * 100.0


def _is_etf_code(code: str) -> bool:
    """TWSE/TPEx ETF symbols use the 00-prefixed exchange code family."""
    return code.startswith("00")


def _number(value: Any) -> float:
    """Parse exchange-formatted numbers, returning NaN for non-numbers."""
    if value is None:
        return math.nan
    text = re.sub(r"<[^>]+>", "", str(value)).replace(",", "").strip()
    if text in {"", "--", "---", "-", "N/A"}:
        return math.nan
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return math.nan
    try:
        return float(match.group(0))
    except ValueError:
        return math.nan


def _get_json(url: str, params: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(OFFICIAL_RETRIES):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
                verify=False,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"Unexpected payload type from {url}")
            return payload
        except Exception as exc:  # exchanges occasionally reset a connection
            last_error = exc
            if attempt < OFFICIAL_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(
        f"Official endpoint failed after {OFFICIAL_RETRIES} attempts: {url}: {last_error}"
    )


def _table(payload: dict[str, Any], *, field: str) -> dict[str, Any]:
    for table in payload.get("tables") or []:
        if field in (table.get("fields") or []):
            return table
    raise ValueError(f"Missing table field {field!r}")


def _row_map(table: dict[str, Any], row: list[Any]) -> dict[str, Any]:
    return dict(zip(table.get("fields") or [], row))


def _twse_slice(margin_payload: dict[str, Any], price_payload: dict[str, Any]) -> tuple[MarketSlice, float]:
    margin_table = next(
        (
            table
            for table in margin_payload.get("tables") or []
            if "今日餘額" in (table.get("fields") or [])
            and ({"代號", "股票代號", "證券代號"} & set(table.get("fields") or []))
        ),
        None,
    )
    if margin_table is None:
        raise ValueError("Missing TWSE per-security margin table")
    price_table = _table(price_payload, field="收盤價")

    prices: dict[str, float] = {}
    for raw in price_table.get("data") or []:
        row = _row_map(price_table, raw)
        price = _number(row.get("收盤價"))
        code = str(row.get("證券代號") or "").strip()
        if code and math.isfinite(price) and price > 0:
            prices[code] = price

    collateral_thousand = 0.0
    excluded_etf_thousand = 0.0
    excluded_etf_count = 0
    total = 0
    matched = 0
    margin_fields = margin_table.get("fields") or []
    code_index = next(
        (margin_fields.index(label) for label in ("代號", "股票代號", "證券代號") if label in margin_fields),
        None,
    )
    balance_index = margin_fields.index("今日餘額")
    if code_index is None:
        raise ValueError("TWSE security-code column is unavailable")
    for raw in margin_table.get("data") or []:
        code = str(raw[code_index]).strip()
        # MI_MARGN repeats the label 今日餘額 for financing and shorting.
        # The first occurrence is the financing balance that belongs here.
        units = _number(raw[balance_index])
        if not code or not math.isfinite(units) or units < 0:
            continue
        price = prices.get(code)
        if _is_etf_code(code):
            excluded_etf_count += 1
            if price is not None:
                excluded_etf_thousand += units * price
            continue
        total += 1
        if price is None:
            continue
        # One exchange trading unit is normally 1,000 shares.  Multiplying
        # units by price therefore gives thousand TWD, matching the official
        # aggregate financing denominator's unit.
        collateral_thousand += units * price
        matched += 1

    aggregate = _table(margin_payload, field="項目")
    financing_thousand = math.nan
    for raw in aggregate.get("data") or []:
        row = _row_map(aggregate, raw)
        if str(row.get("項目") or "").strip() == "融資金額(仟元)":
            financing_thousand = _number(row.get("今日餘額"))
            break
    if not math.isfinite(financing_thousand) or financing_thousand <= 0:
        raise ValueError("TWSE aggregate financing amount is unavailable")

    taiex_close = math.nan
    for table in price_payload.get("tables") or []:
        fields = table.get("fields") or []
        if "指數" not in fields or "收盤指數" not in fields:
            continue
        for raw in table.get("data") or []:
            row = _row_map(table, raw)
            label = str(row.get("指數") or "")
            if label == "發行量加權股價指數":
                taiex_close = _number(row.get("收盤指數"))
                break
        if math.isfinite(taiex_close):
            break

    return MarketSlice(
        collateral_thousand,
        financing_thousand,
        matched,
        total,
        excluded_etf_thousand,
        excluded_etf_count,
    ), taiex_close


def _tpex_slice(margin_payload: dict[str, Any], price_payload: dict[str, Any]) -> MarketSlice:
    margin_table = _table(margin_payload, field="資餘額")
    price_table = _table(price_payload, field="收盤")

    prices: dict[str, float] = {}
    for raw in price_table.get("data") or []:
        row = _row_map(price_table, raw)
        price = _number(row.get("收盤"))
        code = str(row.get("代號") or "").strip()
        if code and math.isfinite(price) and price > 0:
            prices[code] = price

    collateral_thousand = 0.0
    excluded_etf_thousand = 0.0
    excluded_etf_count = 0
    total = 0
    matched = 0
    for raw in margin_table.get("data") or []:
        row = _row_map(margin_table, raw)
        code = str(row.get("代號") or "").strip()
        units = _number(row.get("資餘額"))
        if not code or not math.isfinite(units) or units < 0:
            continue
        price = prices.get(code)
        if _is_etf_code(code):
            excluded_etf_count += 1
            if price is not None:
                excluded_etf_thousand += units * price
            continue
        total += 1
        if price is None:
            continue
        collateral_thousand += units * price
        matched += 1

    financing_thousand = math.nan
    for raw in margin_table.get("summary") or []:
        if any("融資金" in str(value) for value in raw):
            row = _row_map(margin_table, raw)
            financing_thousand = _number(row.get("資餘額"))
            break
    if not math.isfinite(financing_thousand) or financing_thousand <= 0:
        raise ValueError("TPEx aggregate financing amount is unavailable")
    return MarketSlice(
        collateral_thousand,
        financing_thousand,
        matched,
        total,
        excluded_etf_thousand,
        excluded_etf_count,
    )


def _roc_date(date: pd.Timestamp) -> str:
    return f"{date.year - 1911:03d}/{date.month:02d}/{date.day:02d}"


def fetch_date(date: pd.Timestamp) -> dict[str, Any] | None:
    """Fetch and calculate one trading date. Weekends/holidays return None."""
    date = pd.Timestamp(date).normalize()
    ymd = date.strftime("%Y%m%d")
    roc = _roc_date(date)

    twse_margin = _get_json(
        TWSE_MARGIN_URL,
        {"date": ymd, "selectType": "ALL", "response": "json"},
    )
    if str(twse_margin.get("stat") or "").upper() != "OK":
        return None
    twse_prices = _get_json(
        TWSE_PRICE_URL,
        {"date": ymd, "type": "ALLBUT0999", "response": "json"},
    )
    if str(twse_prices.get("stat") or "").upper() != "OK":
        return None

    tpex_margin = _get_json(
        TPEX_MARGIN_URL,
        {"l": "zh-tw", "o": "json", "d": roc, "s": "0,asc,0"},
    )
    tpex_prices = _get_json(
        TPEX_PRICE_URL,
        {"l": "zh-tw", "o": "json", "d": roc, "s": "0,asc,0"},
    )
    if str(tpex_margin.get("stat") or "").lower() != "ok":
        return None
    if str(tpex_prices.get("stat") or "").lower() != "ok":
        return None

    twse, taiex_close = _twse_slice(twse_margin, twse_prices)
    tpex = _tpex_slice(tpex_margin, tpex_prices)
    collateral = twse.collateral_thousand + tpex.collateral_thousand
    excluded_etf = twse.excluded_etf_thousand + tpex.excluded_etf_thousand
    financing = twse.financing_thousand + tpex.financing_thousand
    if financing <= 0 or collateral <= 0:
        raise ValueError("Official reports returned non-positive aggregate values")

    # Exchange denominator is thousand TWD. Divide by 100,000 to display 億元.
    to_billion = 1.0 / 100_000.0
    return {
        "date": date.date().isoformat(),
        "estimate_pct": collateral / financing * 100.0,
        "twse_estimate_pct": twse.estimate_pct,
        "tpex_estimate_pct": tpex.estimate_pct,
        "collateral_value_billion": collateral * to_billion,
        "financing_balance_billion": financing * to_billion,
        "twse_collateral_billion": twse.collateral_thousand * to_billion,
        "twse_financing_billion": twse.financing_thousand * to_billion,
        "tpex_collateral_billion": tpex.collateral_thousand * to_billion,
        "tpex_financing_billion": tpex.financing_thousand * to_billion,
        "excluded_etf_collateral_billion": excluded_etf * to_billion,
        "excluded_etf_count": twse.excluded_etf_count + tpex.excluded_etf_count,
        "taiex_close": taiex_close,
        "twse_matched": twse.matched,
        "twse_total": twse.total,
        "tpex_matched": tpex.matched,
        "tpex_total": tpex.total,
    }


def load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path)
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = math.nan
    return df[COLUMNS]


def atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8-sig", dir=path.parent, delete=False, newline=""
    ) as tmp:
        tmp_path = Path(tmp.name)
        df.to_csv(tmp, index=False)
    tmp_path.replace(path)


def target_dates(days: int, backfill_years: int) -> list[pd.Timestamp]:
    today = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None).normalize()
    span_days = max(1, int(backfill_years)) * 366 if backfill_years else max(1, int(days))
    start = today - pd.Timedelta(days=span_days - 1)

    # Reuse the official monthly TWSE market cache as the trading calendar when
    # available. This avoids four needless holiday requests per date.
    if MARKET_DATES_PATH.exists():
        calendar = pd.read_csv(MARKET_DATES_PATH, usecols=["date"])
        parsed = pd.to_datetime(calendar["date"], errors="coerce").dropna()
        dates = sorted({d.normalize() for d in parsed if start <= d.normalize() <= today})
        if dates:
            # The market cache can be stale on a dev machine. Fill weekdays
            # after its last known session so this updater can repair itself;
            # exchange holiday responses are safely skipped by fetch_date().
            tail_start = max(start, dates[-1] + pd.Timedelta(days=1))
            tail = list(pd.bdate_range(start=tail_start, end=today)) if tail_start <= today else []
            return sorted(set(dates + tail))
    return list(pd.bdate_range(start=start, end=today))


def refresh_cache(path: Path, dates: list[pd.Timestamp], workers: int = 2) -> tuple[pd.DataFrame, int, int]:
    existing = load_existing(path)
    rows: list[dict[str, Any]] = []
    errors = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 6))) as pool:
        futures = {pool.submit(fetch_date, date): date for date in dates}
        for future in as_completed(futures):
            date = futures[future]
            completed += 1
            try:
                row = future.result()
                if row:
                    rows.append(row)
            except Exception as exc:
                errors += 1
                print(f"[margin-risk] WARN {date.date()}: {type(exc).__name__}: {exc}")
            if len(dates) >= 30 and (completed % 25 == 0 or completed == len(dates)):
                print(
                    f"[margin-risk] progress={completed}/{len(dates)} "
                    f"fetched={len(rows)} errors={errors}"
                )

    frames: list[pd.DataFrame] = []
    if not existing.empty:
        frames.append(existing)
    if rows:
        frames.append(pd.DataFrame(rows, columns=COLUMNS))
    if not frames:
        return pd.DataFrame(columns=COLUMNS), 0, errors
    combined = pd.concat(frames, ignore_index=True)
    if not combined.empty:
        combined = (
            combined.dropna(subset=["date", "estimate_pct"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
        atomic_write_csv(path, combined[COLUMNS])
    return combined[COLUMNS], len(rows), errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the official-data Taiwan margin collateral estimate cache."
    )
    parser.add_argument("--days", type=int, default=10, help="Recent calendar days to refresh.")
    parser.add_argument(
        "--backfill-years",
        type=int,
        default=0,
        help="One-time history init; overrides --days.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --backfill-years, refetch dates already present in the cache.",
    )
    parser.add_argument("--workers", type=int, default=2, help="Concurrent dates (max 6).")
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE_PATH)
    args = parser.parse_args()

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    dates = target_dates(args.days, args.backfill_years)
    if args.backfill_years and not args.force and args.output.exists():
        existing = load_existing(args.output)
        cached_dates = set(pd.to_datetime(existing["date"], errors="coerce").dt.date.dropna())
        dates = [date for date in dates if date.date() not in cached_dates]
        print(f"[margin-risk] backfill missing_dates={len(dates)} cached={len(cached_dates)}")
    # A one-year initialization can run for tens of minutes against the two
    # official exchanges. Checkpoint it in small atomic batches so a dropped
    # SSH session or a transient endpoint failure never discards all progress.
    if args.backfill_years and dates:
        df = load_existing(args.output)
        fetched = 0
        errors = 0
        batch_size = 25
        for start in range(0, len(dates), batch_size):
            batch = dates[start : start + batch_size]
            df, batch_fetched, batch_errors = refresh_cache(
                args.output, batch, workers=args.workers
            )
            fetched += batch_fetched
            errors += batch_errors
            print(
                f"[margin-risk] checkpoint={min(start + batch_size, len(dates))}/{len(dates)} "
                f"cached={len(df)} fetched={fetched} errors={errors}"
            )
    else:
        df, fetched, errors = refresh_cache(args.output, dates, workers=args.workers)
    if df.empty:
        print(f"[margin-risk] no rows written path={args.output} errors={errors}")
        return 1
    latest = df.iloc[-1]
    coverage = (
        int(latest["twse_matched"]) + int(latest["tpex_matched"]),
        int(latest["twse_total"]) + int(latest["tpex_total"]),
    )
    print(
        f"[margin-risk] rows={len(df):,} range={df['date'].min()}..{df['date'].max()} "
        f"latest={float(latest['estimate_pct']):.2f}% "
        f"financing={float(latest['financing_balance_billion']):,.0f}億 "
        f"etf_excluded={float(latest['excluded_etf_collateral_billion']):,.0f}億 "
        f"coverage={coverage[0]}/{coverage[1]} fetched={fetched} errors={errors} "
        f"path={args.output}"
    )
    # Any exhausted official-endpoint retry should be visible as PARTIAL_FAIL
    # in the daily admin email. Existing cached rows are preserved either way.
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
