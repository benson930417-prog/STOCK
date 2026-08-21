"""Read-only access to the sole ARM market.db."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


DB_PATH = Path(os.environ.get("STOCK_GLOBAL_MARKET_DB", "/var/lib/stock/market/market.db"))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(
        f"file:{DB_PATH.resolve().as_posix()}?mode=ro", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        yield connection
    finally:
        connection.close()


def _symbols(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


_MARKET_PRIORITY_SQL = """CASE market
    WHEN 'TWSE' THEN 0 WHEN 'TPEX' THEN 1 WHEN 'INDEX_TW' THEN 2
    WHEN 'EQUITY_US' THEN 10 WHEN 'ETF_US' THEN 11 WHEN 'INDEX_US' THEN 12
    WHEN 'ETF_JP' THEN 20 WHEN 'FX' THEN 30 WHEN 'FUTURES' THEN 31
    WHEN 'RATES' THEN 32 ELSE 99 END"""


def _resolve_markets(
    connection: sqlite3.Connection, symbols: Iterable[str]
) -> dict[str, str]:
    """Choose one deterministic canonical market for each bare symbol.

    Public consumers still identify products by their historical symbol-only
    contract. Resolution happens once here, then every price/action query uses
    the full ``market + symbol`` key so a future cross-market duplicate cannot
    mix two instruments into one time series.
    """

    requested = _symbols(symbols)
    if not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    rows = connection.execute(
        f"""SELECT market,symbol,active FROM instruments
              WHERE symbol IN ({placeholders})
              ORDER BY symbol,active DESC,{_MARKET_PRIORITY_SQL},market""",
        requested,
    ).fetchall()
    resolved: dict[str, str] = {}
    for row in rows:
        resolved.setdefault(str(row["symbol"]).upper(), str(row["market"]))

    missing = [symbol for symbol in requested if symbol not in resolved]
    if missing:
        missing_placeholders = ",".join("?" for _ in missing)
        fallback = connection.execute(
            f"""SELECT market,symbol,MAX(date) AS newest FROM daily_bars
                  WHERE symbol IN ({missing_placeholders})
                  GROUP BY market,symbol
                  ORDER BY symbol,newest DESC,{_MARKET_PRIORITY_SQL},market""",
            missing,
        ).fetchall()
        for row in fallback:
            resolved.setdefault(str(row["symbol"]).upper(), str(row["market"]))
    return resolved


def _details_object(value: object) -> dict:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_taiwan_instruments() -> list[dict]:
    """Return the current official Taiwan universe from the sole market DB."""

    if not DB_PATH.is_file():
        return []
    with _connect() as connection:
        rows = connection.execute(
            """SELECT market,symbol,name,asset_type
                 FROM instruments
                WHERE market IN ('TWSE','TPEX') AND active=1
                  AND asset_type IN ('stock','etf')
                ORDER BY symbol,market"""
        ).fetchall()
    return [dict(row) for row in rows]


def load_holding_history(ticker: str) -> dict[str, dict]:
    if not DB_PATH.is_file():
        return {}
    with _connect() as connection:
        snapshots = connection.execute(
            """SELECT s.* FROM etf_holding_snapshots s
                 JOIN (
                   SELECT as_of_date,MAX(fetched_at_utc) AS fetched
                     FROM etf_holding_snapshots
                    WHERE etf_symbol=? AND complete=1 GROUP BY as_of_date
                 ) latest
                   ON latest.as_of_date=s.as_of_date AND latest.fetched=s.fetched_at_utc
                WHERE s.etf_symbol=? AND s.complete=1 ORDER BY s.as_of_date""",
            (ticker, ticker),
        ).fetchall()
        result: dict[str, dict] = {}
        for snapshot in snapshots:
            report = json.loads(snapshot["report_json"] or "{}")
            # Preserve the legacy consumer contract while sourcing it from the
            # sole DB: issuer metadata is nested under ``meta`` and holdings
            # are a sibling list.  Market prices are intentionally absent from
            # issuer metadata and must be read from daily_bars.
            day = {
                "meta": dict(report.get("meta") or {}),
                "source": str(snapshot["source"] or ""),
            }
            holdings = []
            for row in connection.execute(
                "SELECT * FROM etf_holdings WHERE snapshot_id=? ORDER BY rank",
                (snapshot["snapshot_id"],),
            ):
                # Issuer-specific details remain available, but they can never
                # override the canonical identity and numeric columns.
                item = _details_object(row["details_json"])
                item.update(
                    {
                        "id": row["component_symbol"],
                        "name": row["component_name"] or "",
                        "weight_pct": row["weight_pct"],
                        "shares": row["shares"],
                    }
                )
                holdings.append(item)
            day["holdings"] = holdings
            result[str(snapshot["as_of_date"])] = day
        return result


def daily_close_map(
    symbol: str, *, start: str | None = None, end: str | None = None
) -> dict[str, float]:
    """Return canonical closes keyed by date for one exact market symbol."""

    normalized = str(symbol or "").strip().upper()
    if not DB_PATH.is_file() or not normalized:
        return {}
    with _connect() as connection:
        market = _resolve_markets(connection, [normalized]).get(normalized)
        if not market:
            return {}
        sql = "SELECT date,close FROM daily_bars WHERE market=? AND symbol=?"
        parameters: list[object] = [market, normalized]
        if start:
            sql += " AND date>=?"
            parameters.append(start)
        if end:
            sql += " AND date<=?"
            parameters.append(end)
        sql += " ORDER BY date"
        return {
            str(row["date"]): float(row["close"])
            for row in connection.execute(sql, parameters)
        }


def latest_holding_payload(ticker: str) -> tuple[str | None, dict]:
    history = load_holding_history(ticker)
    if not history:
        return None, {}
    day = max(history)
    return day, history[day]


def _quote_candidates(value: str) -> list[str]:
    """Map issuer/Yahoo-style identifiers onto canonical market.db symbols."""

    raw = str(value or "").strip().upper()
    if not raw:
        return []
    candidates: list[str] = []

    def add(symbol: str) -> None:
        symbol = symbol.strip().upper()
        if symbol and symbol not in candidates:
            candidates.append(symbol)

    add(raw)
    parts = raw.replace("/", " ").split()
    base = parts[0]
    venue = parts[1] if len(parts) > 1 else ""
    if base.endswith((".TW", ".TWO")):
        add(base.rsplit(".", 1)[0])
    if venue in {"TW", "TT", "TWO"}:
        add(base.removesuffix(".TW").removesuffix(".TWO"))
    elif venue in {"US", "UW", "UN", "UA"}:
        add(base)
    elif venue in {"JP", "JT"}:
        add(f"{base}.T")
    elif venue in {"KS", "KQ"}:
        add(f"{base}.{venue}")
    elif venue in {"HK", "HKEX"}:
        add(f"{base.lstrip('0') or '0'}.HK")
        add(f"{base.zfill(4)}.HK")
    add(base)
    return candidates


def latest_quote_map(symbols: Iterable[str]) -> dict[str, dict]:
    """Return latest and previous daily closes from the sole market.db.

    Keys are the exact normalized identifiers requested by the caller.  The
    function never performs a network request and never reads a JSON cache.
    """

    requested = _symbols(symbols)
    if not DB_PATH.is_file() or not requested:
        return {}
    candidates = {symbol: _quote_candidates(symbol) for symbol in requested}
    flat = sorted({candidate for values in candidates.values() for candidate in values})
    if not flat:
        return {}
    placeholders = ",".join("?" for _ in flat)
    sql = f"""
        WITH ranked AS (
            SELECT market,symbol,date,open,high,low,close,volume,source,
                   ROW_NUMBER() OVER (PARTITION BY market,symbol ORDER BY date DESC) AS rn
              FROM daily_bars
             WHERE symbol IN ({placeholders})
        )
        SELECT r.market AS bar_market,r.symbol,r.date,r.open,r.high,r.low,r.close,
               r.volume,r.source,r.rn,i.name
          FROM ranked r
          LEFT JOIN instruments i ON i.market=r.market AND i.symbol=r.symbol
         WHERE r.rn<=2
         ORDER BY r.symbol,r.rn
    """
    by_instrument: dict[tuple[str, str], list[sqlite3.Row]] = {}
    with _connect() as connection:
        market_by_symbol = _resolve_markets(connection, flat)
        for row in connection.execute(sql, flat):
            key = (str(row["bar_market"]), str(row["symbol"]).upper())
            by_instrument.setdefault(key, []).append(row)

    result: dict[str, dict] = {}
    for requested_symbol, options in candidates.items():
        matched_key = next(
            (
                (market_by_symbol[option], option)
                for option in options
                if option in market_by_symbol
                and (market_by_symbol[option], option) in by_instrument
            ),
            None,
        )
        if not matched_key:
            continue
        market, matched = matched_key
        rows = by_instrument[matched_key]
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        price = float(latest["close"])
        previous_close = float(previous["close"]) if previous else None
        change_pct = (
            (price / previous_close - 1.0) * 100.0
            if previous_close not in (None, 0.0)
            else None
        )
        market = str(market or "")
        if market in {"TWSE", "TPEX", "INDEX_TW"}:
            country = "TW"
        elif market == "ETF_JP":
            country = "JP"
        elif market.startswith(("EQUITY_US", "ETF_US", "INDEX_US")):
            country = "US"
        else:
            country = market or None
        result[requested_symbol] = {
            "requested_symbol": requested_symbol,
            "symbol": matched,
            "name": latest["name"] or "",
            "country": country,
            "market": market,
            "price": price,
            "previous_close": previous_close,
            "day_change_pct": change_pct,
            "as_of_date": str(latest["date"]),
            "open": float(latest["open"]),
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "volume": float(latest["volume"]),
            "source": str(latest["source"]),
            "market_session": "DAILY_CLOSE",
            "is_live_market": False,
        }
    return result


_MARKET_TEXT_SPECS = {
    "oil": ("CL=F", "WTI 原油", 2, "USD"),
    "brent": ("BZ=F", "布蘭特原油", 2, "USD"),
    "bond": ("^TNX", "美國 10 年期公債殖利率", 3, "%"),
    "gold": ("GC=F", "黃金期貨", 2, "USD"),
    "usdtwd": ("TWD=X", "美元兌台幣", 3, "TWD"),
    "usdjpy": ("JPY=X", "美元兌日圓", 3, "JPY"),
    "usdchf": ("CHF=X", "美元兌瑞郎", 4, "CHF"),
    "nasdaq": ("^IXIC", "NASDAQ Composite", 2, "點"),
}


def market_text(keys: Iterable[str]) -> str:
    """Build LINE market text exclusively from canonical daily bars."""

    normalized = [str(key).strip().lower() for key in keys]
    specs = [(key, _MARKET_TEXT_SPECS[key]) for key in normalized if key in _MARKET_TEXT_SPECS]
    quotes = latest_quote_map(spec[0] for _key, spec in specs)
    blocks: list[str] = []
    for _key, (symbol, label, precision, unit) in specs:
        quote = quotes.get(symbol)
        if not quote:
            blocks.append(f"{label}\n資料庫尚無資料")
            continue
        change = quote["day_change_pct"]
        change_text = "--" if change is None else f"{change:+.2f}%"
        blocks.append(
            "\n".join(
                [
                    label,
                    f"收盤 {quote['price']:,.{precision}f} {unit}",
                    f"日漲跌 {change_text}",
                    f"資料日 {quote['as_of_date']}",
                    f"來源 {quote['source']}（ARM market.db）",
                ]
            )
        )
    return "\n\n".join(blocks) or "ARM market.db 尚無對應商品。"


def etf_holding_quote_text(ticker: str) -> str:
    """Build a DB-only issuer-holding summary; missing quotes stay explicit."""

    as_of_date, payload = latest_holding_payload(str(ticker).upper())
    holdings = list(payload.get("holdings") or [])
    if not holdings:
        raise RuntimeError(f"market.db has no sealed issuer holdings for {ticker}")
    quotes = latest_quote_map(str(row.get("id") or "") for row in holdings)
    covered_weight = 0.0
    weighted_move = 0.0
    up = down = flat = 0
    price_dates: set[str] = set()
    for holding in holdings:
        key = str(holding.get("id") or "").strip().upper()
        quote = quotes.get(key)
        weight = holding.get("weight_pct")
        if not quote or weight is None or quote.get("day_change_pct") is None:
            continue
        weight_value = float(weight)
        move = float(quote["day_change_pct"])
        covered_weight += weight_value
        weighted_move += weight_value * move
        price_dates.add(str(quote["as_of_date"]))
        if move > 0:
            up += 1
        elif move < 0:
            down += 1
        else:
            flat += 1
    composite = weighted_move / covered_weight if covered_weight else None
    source = str(payload.get("source") or payload.get("meta", {}).get("source") or "ISSUER")
    lines = [
        f"{str(ticker).upper()} ETF 成分",
        f"持股日 {as_of_date}",
        f"官方成分 {len(holdings)} 檔｜大庫有行情 {len(quotes)} 檔",
        f"報價覆蓋權重 {covered_weight:.1f}%",
    ]
    if composite is not None:
        lines.append(f"覆蓋成分加權日漲跌 {composite:+.2f}%")
        lines.append(f"上漲 {up}｜下跌 {down}｜平盤 {flat}")
    else:
        lines.append("大庫尚無足夠成分行情，不估算漲跌。")
    if price_dates:
        lines.append(f"行情日 {min(price_dates)} 至 {max(price_dates)}")
    lines.append(f"來源 {source} holdings + ARM market.db OHLCV")
    return "\n".join(lines)


def holding_status(ticker: str) -> dict:
    if not DB_PATH.is_file():
        return {}
    with _connect() as connection:
        row = connection.execute(
            """SELECT as_of_date,fetched_at_utc,row_count,total_weight_pct,source
                 FROM etf_holding_snapshots
                WHERE etf_symbol=? AND complete=1
                ORDER BY as_of_date DESC,fetched_at_utc DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
    if not row:
        return {}
    return {
        "status": "VERIFIED",
        "last_checked_utc": row["fetched_at_utc"],
        "last_updated_utc": row["fetched_at_utc"],
        "latest_date": row["as_of_date"],
        "holdings_count": row["row_count"],
        "total_weight_pct": row["total_weight_pct"],
        "source": row["source"],
    }


def load_daily_ohlcv_payload(
    symbols: Iterable[str], *, start: str | None = None,
    end: str | None = None, benchmark: str = "0050",
) -> dict:
    requested = _symbols([*symbols, benchmark])
    if not DB_PATH.is_file() or not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    sql = (
        "SELECT market,symbol,date,open,high,low,close,volume,source "
        f"FROM daily_bars WHERE symbol IN ({placeholders})"
    )
    parameters: list[object] = list(requested)
    if start:
        sql += " AND date>=?"
        parameters.append(start)
    if end:
        sql += " AND date<=?"
        parameters.append(end)
    sql += " ORDER BY symbol,date"
    payload: dict[str, list[dict]] = {symbol: [] for symbol in requested}
    sources: set[str] = set()
    with _connect() as connection:
        market_by_symbol = _resolve_markets(connection, requested)
        for row in connection.execute(sql, parameters):
            symbol = str(row["symbol"]).upper()
            if market_by_symbol.get(symbol) != str(row["market"]):
                continue
            sources.add(str(row["source"]))
            payload[symbol].append(
                {
                    "date": str(row["date"]), "open": float(row["open"]),
                    "high": float(row["high"]), "low": float(row["low"]),
                    "close": float(row["close"]), "volume": float(row["volume"]),
                }
            )
    populated = {symbol: rows for symbol, rows in payload.items() if rows}
    missing = sorted(set(requested) - set(populated))
    return {
        "schema_version": 1,
        "source": "ARM market.db raw OHLCV",
        "source_ids": sorted(sources),
        "period": "daily",
        "requested_start": start,
        "requested_end": end,
        "benchmark": benchmark,
        "symbol_count": len(requested),
        "symbols": populated,
        "failures": {symbol: "missing OHLCV" for symbol in missing},
        "successful_symbols": len(populated),
        "failed_symbols": len(missing),
        "complete": not missing,
        "fetched_at_utc": datetime.fromtimestamp(
            DB_PATH.stat().st_mtime, timezone.utc
        ).isoformat().replace("+00:00", "Z"),
    }


def load_corporate_action_payload(
    symbols: Iterable[str], *, start: str | None = None, end: str | None = None
) -> dict:
    requested = _symbols(symbols)
    if not DB_PATH.is_file() or not requested:
        return {}
    placeholders = ",".join("?" for _ in requested)
    sql = (
        "SELECT market,symbol,ex_date,action_type,value,source FROM corporate_actions "
        f"WHERE symbol IN ({placeholders})"
    )
    parameters: list[object] = list(requested)
    if start:
        sql += " AND ex_date>=?"
        parameters.append(start)
    if end:
        sql += " AND ex_date<=?"
        parameters.append(end)
    sql += " ORDER BY symbol,ex_date,action_type"
    merged: dict[tuple[str, str], dict] = {}
    sources: set[str] = set()
    with _connect() as connection:
        market_by_symbol = _resolve_markets(connection, requested)
        for row in connection.execute(sql, parameters):
            symbol = str(row["symbol"]).upper()
            if market_by_symbol.get(symbol) != str(row["market"]):
                continue
            ex_date = str(row["ex_date"])
            sources.add(str(row["source"]))
            event = merged.setdefault(
                (symbol, ex_date),
                {"ex_date": ex_date, "kind": "", "cash_dividend": 0.0, "share_multiplier": 1.0},
            )
            if row["action_type"] == "CASH_DIVIDEND":
                event["cash_dividend"] += float(row["value"])
                event["kind"] += "cash_dividend "
            elif row["action_type"] == "SPLIT_RATIO":
                event["share_multiplier"] *= float(row["value"])
                event["kind"] += "split "
    events: dict[str, list[dict]] = {}
    for (symbol, _day), event in sorted(merged.items()):
        event["kind"] = event["kind"].strip()
        events.setdefault(symbol, []).append(event)
    return {
        "schema_version": 1,
        "source": "ARM market.db corporate actions",
        "source_ids": sorted(sources),
        "range": {"start": start, "end": end},
        "symbol_count": len(events),
        "event_count": len(merged),
        "events": events,
    }
