import pandas as pd
import app
from collections import defaultdict, deque

df = pd.read_csv('data/master_trades.csv')
df = app.normalize_raw_trades(df)

all_dates = sorted(df["日期"].dt.date.unique())
master_inventory = defaultdict(deque)

date_to_total_cost = {}
date_to_realized_pnl = {}

for date in all_dates:
    ddf = df[df["日期"].dt.date == date]
    daily_realized_pnl = 0.0
    
    for stock, sdf in ddf.groupby("股名", sort=False):
        for pool in ["board", "odd"]:
            dpool = sdf[sdf["成交股數"].apply(lambda q: app.pool_of(q) == pool)].copy()
            if dpool.empty: continue
            
            buys = dpool[dpool["淨收付金額"] < 0].copy()
            sells = dpool[dpool["淨收付金額"] > 0].copy()

            day_buy_lots = deque()
            for _, r in buys.iterrows():
                qty = int(r["成交股數"])
                cash_out = -float(r["淨收付金額"])
                fee = float(r["手續費"])
                cps = cash_out / qty
                fps = fee / qty
                day_buy_lots.append({"qty": qty, "cps": cps, "fee_per_share": fps})

            day_sell_lots = deque()
            for _, r in sells.iterrows():
                qty = int(r["成交股數"])
                cash_in = float(r["淨收付金額"])
                tax = float(r["交易稅"])
                pps = cash_in / qty
                tps = tax / qty
                day_sell_lots.append({"qty": qty, "pps": pps, "tax_per_share": tps})

            # 1) Same-day match (Day Trades)
            while day_buy_lots and day_sell_lots:
                b = day_buy_lots[0]
                s = day_sell_lots[0]
                take = min(b["qty"], s["qty"])
                b["qty"] -= take
                s["qty"] -= take
                
                allocated_cost = take * b["cps"]
                cash_in = take * s["pps"]
                daily_realized_pnl += (cash_in - allocated_cost)
                
                if b["qty"] == 0: day_buy_lots.popleft()
                if s["qty"] == 0: day_sell_lots.popleft()

            # 2) Remaining buys -> inventory 
            for lot in list(day_buy_lots):
                if lot["qty"] > 0:
                    master_inventory[stock].append({
                        "qty": int(lot["qty"]), 
                        "cps": float(lot["cps"]),
                        "fee_per_share": float(lot["fee_per_share"])
                    })

            # 3) Remaining sells -> consume inventory
            for lot in list(day_sell_lots):
                if lot["qty"] > 0:
                    remaining = int(lot["qty"])
                    while remaining > 0:
                        if not master_inventory[stock]: break
                        inv_lot = master_inventory[stock][0]
                        take = min(remaining, inv_lot["qty"])
                        inv_lot["qty"] -= take
                        remaining -= take
                        
                        allocated_cost = take * inv_lot["cps"]
                        cash_in = take * lot["pps"]
                        daily_realized_pnl += (cash_in - allocated_cost)
                        
                        if inv_lot["qty"] == 0:
                            master_inventory[stock].popleft()
    
    # End of Date
    total_cost = sum(lot['qty'] * lot['cps'] for q in master_inventory.values() for lot in q)
    date_to_total_cost[date] = total_cost
    date_to_realized_pnl[date] = daily_realized_pnl

history = pd.DataFrame({
    "date": list(date_to_total_cost.keys()),
    "end_cost": list(date_to_total_cost.values()),
    "realized_pnl": list(date_to_realized_pnl.values())
})

# Time-Weighted Return (TWR) Approximation
# For TWR, each sub-period return = (Ending Value - Beginning Value + Cash Flows? Wait.
# HPR = (Pnl_for_period) / Beginning_Value_of_Period
# Since our "beginning value" for a day's realization is technically the previous day's end cost + today's new buys
# A simpler daily approximation for equity curves:
# Daily Return = realized_pnl / (previous_end_cost + new_money_deposited_today)

history["prev_end_cost"] = history["end_cost"].shift(1).fillna(0)
# Net Cash Flow today (rough proxy to know if money was added)
# Since we only cleanly track 'end_cost', the difference in end_cost minus realized_pnl is net new capital
# end_cost_today = prev_end_cost + net_capital_added_today - basis_of_sold_shares_today + basis_of_bought_shares_today
# It's actually easier to just use the day's total capital tied up.

# Simple average capital Dietz method for the day:
history["capital_base"] = history["prev_end_cost"] 
# If capital_base is 0 (i.e., first trade or after full liquidation), use end_cost.
history["capital_base"] = history["capital_base"].replace(0, pd.NA).fillna(history["end_cost"])

# Prevent division by zero if both are somehow 0 but pnl happened (e.g. day trade on empty account)
# Day trades require capital during the day, so technically their base is the cost of the day trade.
history["daily_ret"] = history["realized_pnl"] / history["capital_base"].replace(0, 1)

history["twr_factor"] = 1.0 + history["daily_ret"]
history["cum_twr"] = history["twr_factor"].cumprod()
history["cum_twr_pct"] = (history["cum_twr"] - 1.0) * 100.0

print(history.tail(10).to_string())
print(f"Final TWR Return: {history['cum_twr_pct'].iloc[-1]:.2f}%")
