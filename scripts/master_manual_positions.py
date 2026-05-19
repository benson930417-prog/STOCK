"""Manual (non-trade-derived) holdings for the 吳大師 master view.

The master holding pipeline is fed by `data/master_trades.csv` (FIFO derived).
Some assets are not natural fits for that pipeline:

- Cash (TWD)            — not a "trade", just a value to track for % weighting.
- ETFs without trade history available yet, or ones the user wants to
  declare as a position without uploading a broker CSV (e.g. 00635U gold,
  00865B short-term US treasuries).

This module owns `data/master_manual_positions.json`, which has the shape:

    {
        "cash_twd": 1580000,
        "positions": [
            {"code": "00635U", "name": "期街口S&P黃金", "shares": 5000, "cost": 381000},
            {"code": "00865B", "name": "國泰US短期公債", "shares": 50000, "cost": 2000000}
        ],
        "updated_at_utc": "2026-05-19T..."
    }

- `cash_twd` materialises as a synthetic post-enrich position row labelled
  "現金" with `market_value = cash_twd`, zero P/L, and no Yahoo lookup.
- Each `positions` entry materialises as a pre-enrich row identical in
  shape to FIFO output — `enrich_positions_with_quotes` then fetches the
  TW Yahoo quote for `{code}.TW` and fills in `price`, `market_value`,
  `day_change_pct`, etc.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
MANUAL_PATH = DATA_DIR / "master_manual_positions.json"

CASH_LABEL = "現金"

# Default sell-tax rate for ETFs (matches scripts.master_holding_quote_card).
_DEFAULT_ETF_SELL_TAX_RATE = 0.001


def _empty_payload() -> dict:
    return {"cash_twd": 0, "positions": [], "updated_at_utc": None}


def load_manual_positions() -> dict:
    """Return the parsed manual-positions payload (empty defaults if missing)."""
    if not MANUAL_PATH.exists():
        return _empty_payload()
    try:
        data = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _empty_payload()
    if not isinstance(data, dict):
        return _empty_payload()
    data.setdefault("cash_twd", 0)
    data.setdefault("positions", [])
    return data


def save_manual_positions(cash_twd: float, positions: Iterable[dict]) -> dict:
    """Persist the payload locally. Returns the saved dict."""
    cleaned = []
    for entry in positions or []:
        code = str(entry.get("code") or "").strip().upper()
        name = str(entry.get("name") or "").strip()
        try:
            shares = int(float(entry.get("shares") or 0))
        except (TypeError, ValueError):
            shares = 0
        try:
            cost = float(entry.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0.0
        if not code or shares <= 0:
            continue
        cleaned.append({
            "code": code,
            "name": name or code,
            "shares": shares,
            "cost": round(cost, 2),
        })

    payload = {
        "cash_twd": max(0, int(round(float(cash_twd or 0)))),
        "positions": cleaned,
        "updated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    MANUAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def manual_positions_as_open_position_rows(manual: dict | None = None) -> list[dict]:
    """Convert manual ETF positions into rows matching FIFO open-positions schema.

    These rows go in BEFORE `enrich_positions_with_quotes`, so they receive
    live Yahoo prices like any other position.
    """
    manual = manual if manual is not None else load_manual_positions()
    rows = []
    for entry in manual.get("positions") or []:
        code = str(entry.get("code") or "").strip().upper()
        if not code:
            continue
        shares = int(entry.get("shares") or 0)
        if shares <= 0:
            continue
        cost = float(entry.get("cost") or 0)
        rows.append({
            "stock": entry.get("name") or code,
            "shares": shares,
            "cost": cost,
            "avg_cost": cost / shares if shares else 0.0,
            "ticker": code,
            "bank_sell_tax_rate": None,
        })
    return rows


def cash_row(cash_twd: float | None) -> dict | None:
    """Synthesize a fully-enriched position row for cash. Skip when zero.

    Returned schema matches whatever `enrich_positions_with_quotes` produces,
    so this row can be concatenated AFTER enrichment without a re-pass.
    """
    try:
        amount = float(cash_twd or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return None
    return {
        "stock": CASH_LABEL,
        "code": CASH_LABEL,
        "ticker": None,
        "symbol": None,
        "country": "TW",
        "shares": 1,
        "cost": amount,
        "avg_cost": amount,
        "price": amount,
        "previous_close": amount,
        "day_change_pct": 0.0,
        "quote_time_utc": None,
        "market_session": "CLOSE",
        "is_live_market": False,
        "market_value": amount,
        "est_sell_fee": 0.0,
        "sell_tax_rate": 0.0,
        "est_sell_tax": 0.0,
        "liquidation_value": amount,
        "unrealized_pnl": 0.0,
        "unrealized_pct": 0.0,
        "bank_sell_tax_rate": 0.0,
        "proxy": None,
    }
