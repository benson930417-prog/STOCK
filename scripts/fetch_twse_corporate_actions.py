"""Fetch TWSE ex-dividend / ex-rights results so the backtest can pay dividends.

Yuanta ``GetKLine`` returns raw (unadjusted) OHLC, so every ex-dividend day looks
like a price drop that no cash ever compensates for. Taiwan concentrates its
dividend season in June-August, which is exactly the window the ETF strategy
backtest covers, so ignoring this understates both the strategy and the 0050
benchmark by whole percentage points.

Source: TWSE 除權除息計算結果表 (TWT49U), the official post-event table.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request
from datetime import datetime, timezone

TWSE_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
OUTPUT = DATA_DIR / "twse_corporate_actions.json"


def _roc_to_iso(value: str) -> str | None:
    """Convert 民國 '115年06月18日' to '2026-06-18'."""
    text = str(value).strip()
    for year_sep, month_sep, day_sep in (("年", "月", "日"),):
        if year_sep in text and month_sep in text and day_sep in text:
            year = int(text.split(year_sep)[0]) + 1911
            month = int(text.split(year_sep)[1].split(month_sep)[0])
            day = int(text.split(month_sep)[1].split(day_sep)[0])
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _number(value: str) -> float | None:
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def fetch_range(start: str, end: str) -> list[dict]:
    """Fetch one TWSE window. Dates are ISO; the API wants YYYYMMDD."""
    url = (
        f"{TWSE_URL}?startDate={start.replace('-', '')}"
        f"&endDate={end.replace('-', '')}&response=json"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read())
    if payload.get("stat") != "OK":
        raise RuntimeError(f"TWSE returned stat={payload.get('stat')!r}")
    return payload.get("data") or []


def build_events(rows: list[list[str]]) -> dict[str, list[dict]]:
    """Turn raw TWSE rows into per-symbol corporate action records.

    Pure 息 rows carry the whole ``權值+息值`` as cash. Rows containing 權 also
    move shares, and TWSE publishes only the combined value, so those use the
    ``prev_close / reference_price`` share multiplier — exact in total-return
    terms because ``權值+息值`` always equals ``prev_close - reference_price``.
    """
    events: dict[str, list[dict]] = {}
    for row in rows:
        ex_date = _roc_to_iso(row[0])
        symbol = str(row[1]).strip()
        prev_close = _number(row[3])
        reference = _number(row[4])
        value = _number(row[5])
        kind = str(row[6]).strip()
        if not ex_date or not symbol or prev_close is None or reference is None:
            continue
        if value is None or reference <= 0 or prev_close <= 0:
            continue
        has_shares = "權" in kind
        record = {
            "ex_date": ex_date,
            "kind": kind,
            "prev_close": prev_close,
            "reference_price": reference,
            "value": value,
            "cash_dividend": 0.0 if has_shares else value,
            "share_multiplier": (prev_close / reference) if has_shares else 1.0,
            "name": str(row[2]).strip(),
        }
        events.setdefault(symbol, []).append(record)
    for records in events.values():
        records.sort(key=lambda item: item["ex_date"])
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    rows = fetch_range(args.start, args.end)
    events = build_events(rows)
    payload = {
        "schema_version": 1,
        "source": "TWSE 除權除息計算結果表 (TWT49U)",
        "fetched_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "range": {"start": args.start, "end": args.end},
        "symbol_count": len(events),
        "event_count": sum(len(items) for items in events.values()),
        "events": events,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Wrote {payload['event_count']} corporate actions for "
        f"{payload['symbol_count']} symbols to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
