"""Shared realized-P/L total for the master trade ledger.

Mirrors the dashboard's `realized_match_first_then_fifo_separate_pools_from_raw_trades`
(app.py): same-day 沖↔沖 / 沖↔現 intraday pairing first, then FIFO against
inventory, with a sell that has no matching buy treated as neutral (0 P/L)
"opening basis". Realized P/L is computed from net cash (淨收付金額), which
already embeds fees and taxes — identical to the dashboard.

This module has no Streamlit dependency so headless scripts (the 吳大師 LINE
card) can show the same 已實現獲利 the dashboard does. Only the scalar total is
returned; the dashboard keeps its own per-trade table.
"""
from collections import defaultdict, deque

import pandas as pd

REQUIRED = ["股名", "日期", "成交股數", "淨收付金額", "買賣別"]


def _to_int(v):
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except Exception:
        return 0


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _take_from_lot(lot, take, fields):
    """Integer-precise proportional allocation; the final take absorbs rounding."""
    out = {}
    for f in fields:
        out[f] = int(lot[f]) if lot["qty"] == take else int(round(lot[f] * take / lot["qty"]))
        lot[f] -= out[f]
    return out


def compute_realized_total(raw_trades):
    """Returns (realized_pnl_total, trade_volume). trade_volume = cumulative
    matched cost, identical to the dashboard's 交易量 (allocated_cost sum)."""
    df = raw_trades.copy()
    if df.empty or any(c not in df.columns for c in REQUIRED):
        return 0.0, 0.0

    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df["成交股數"] = df["成交股數"].apply(_to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(_to_float)
    df["買賣別"] = df["買賣別"].astype(str).str.strip()
    df["股名"] = df["股名"].astype(str).str.strip()
    df = df.sort_values(["股名", "日期"]).reset_index(drop=True)

    inventory = defaultdict(deque)  # stock -> deque of {qty, cost}
    realized = 0.0
    volume = 0.0  # cumulative matched cost (= dashboard 交易量 = allocated_cost sum)

    def _is_day(label):
        return str(label or "").startswith("沖")

    for stock, sdf in df.groupby("股名", sort=False):
        sdf = sdf.sort_values("日期")
        for _date, ddf in sdf.groupby(sdf["日期"].dt.date, sort=False):
            buys = ddf[ddf["淨收付金額"] < 0]
            sells = ddf[ddf["淨收付金額"] > 0]

            day_buy_dt, day_buy_cash = deque(), deque()
            for _, r in buys.iterrows():
                lot = {"qty": int(r["成交股數"]), "cost": int(round(-float(r["淨收付金額"])))}
                (day_buy_dt if _is_day(r["買賣別"]) else day_buy_cash).append(lot)

            day_sell_dt, day_sell_cash = deque(), deque()
            for _, r in sells.iterrows():
                lot = {"qty": int(r["成交股數"]), "cash": int(round(float(r["淨收付金額"])))}
                (day_sell_dt if _is_day(r["買賣別"]) else day_sell_cash).append(lot)

            intraday_cost = intraday_cash = 0

            def _pair(buy_q, sell_q):
                nonlocal intraday_cost, intraday_cash
                while buy_q and sell_q:
                    b, s = buy_q[0], sell_q[0]
                    take = min(b["qty"], s["qty"])
                    intraday_cost += _take_from_lot(b, take, ("cost",))["cost"]
                    intraday_cash += _take_from_lot(s, take, ("cash",))["cash"]
                    b["qty"] -= take
                    s["qty"] -= take
                    if b["qty"] == 0:
                        buy_q.popleft()
                    if s["qty"] == 0:
                        sell_q.popleft()

            _pair(day_buy_dt, day_sell_dt)      # 沖 ↔ 沖
            _pair(day_buy_dt, day_sell_cash)    # leftover 沖買 ↔ 現賣
            _pair(day_buy_cash, day_sell_dt)    # 現買 ↔ leftover 沖賣

            if intraday_cost or intraday_cash:
                realized += intraday_cash - intraday_cost
                volume += intraday_cost

            for lot in list(day_buy_dt) + list(day_buy_cash):
                if lot["qty"] > 0:
                    inventory[stock].append({"qty": int(lot["qty"]), "cost": int(lot["cost"])})

            for lot in list(day_sell_dt) + list(day_sell_cash):
                if lot["qty"] <= 0:
                    continue
                remaining = int(lot["qty"])
                sell_lot = {"qty": int(lot["qty"]), "cash": int(lot["cash"])}
                matched_cash = allocated_cost = 0
                while remaining > 0:
                    if not inventory[stock]:
                        # Sell with no buy in data: neutral basis (cost = proceeds).
                        op = _take_from_lot(sell_lot, remaining, ("cash",))
                        matched_cash += op["cash"]
                        allocated_cost += op["cash"]
                        break
                    inv = inventory[stock][0]
                    take = min(remaining, inv["qty"])
                    allocated_cost += _take_from_lot(inv, take, ("cost",))["cost"]
                    matched_cash += _take_from_lot(sell_lot, take, ("cash",))["cash"]
                    sell_lot["qty"] -= take
                    inv["qty"] -= take
                    remaining -= take
                    if inv["qty"] == 0:
                        inventory[stock].popleft()
                realized += matched_cash - allocated_cost
                volume += allocated_cost

    return float(realized), float(volume)
