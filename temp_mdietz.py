import pandas as pd
import app
from collections import defaultdict, deque

df = pd.read_csv('data/master_trades.csv')
df = app.normalize_raw_trades(df)

all_dates = sorted(df["日期"].dt.date.unique())
master_inventory = defaultdict(deque)

date_to_total_cost = {}
date_to_realized_pnl = {}
date_to_net_cash_flow = {}

for date in all_dates:
    ddf = df[df["日期"].dt.date == date]
    daily_realized_pnl = 0.0
    
    # Net cash flow is the total "new money" injected or extracted into/from inventory today
    net_cf_today = 0.0
    
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
                
                # Money explicitly spent to buy shares
                net_cf_today += cash_out

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
                cash_in_lot = take * s["pps"]
                daily_realized_pnl += (cash_in_lot - allocated_cost)
                
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
                        cash_in_lot = take * lot["pps"]
                        daily_realized_pnl += (cash_in_lot - allocated_cost)
                        
                        # When selling from inventory, the original capital is freed up
                        net_cf_today -= allocated_cost
                        
                        if inv_lot["qty"] == 0:
                            master_inventory[stock].popleft()
    
    # End of Date
    total_cost = sum(lot['qty'] * lot['cps'] for q in master_inventory.values() for lot in q)
    date_to_total_cost[date] = total_cost
    date_to_realized_pnl[date] = daily_realized_pnl
    date_to_net_cash_flow[date] = net_cf_today

history = pd.DataFrame({
    "date": list(date_to_total_cost.keys()),
    "end_cost": list(date_to_total_cost.values()),
    "realized_pnl": list(date_to_realized_pnl.values()),
    "cf": list(date_to_net_cash_flow.values()) # net new capital injected into inventory today
})

# Modified Dietz Approximation: Portfolio-level
# Total Return = Total PNL / Average Adjusted Capital Base.
# Since the dashboard calculates cumulative PNL from the beginning, we can use 
# a modified Dietz approach from t=0.

# 1. Total Gain: The sum of all realized_pnl
total_gain = history["realized_pnl"].sum()

# 2. To get average capital base across the whole history, we weight every cash flow 
# by the amount of time it spent in the market relative to the total duration.
# For a daily aggregation, the capital engaged each day is `end_cost`.
# Actually, the average capital is just the average of the daily `end_cost` over the active market days!

total_days = len(history)
average_capital = history["end_cost"].mean()

if average_capital > 0:
    md_return = (total_gain / average_capital) * 100
else:
    md_return = 0.0

print(f"Total Realized PNL: {total_gain:,.0f} TWD")
print(f"Average Invested Capital (Time-Weighted Base): {average_capital:,.0f} TWD")
print(f"Modified Dietz Cumulative Return: {md_return:.2f}%")

# Let's say we want a specific line for the chart (the cumulative percentage over time).
# For any given day 't', what is the cumulative percent return up to 't'?
# CUM_RETURN(t) = CUM_PNL(t) / AVG_CAPITAL(from 0 to t)
history["cum_pnl"] = history["realized_pnl"].cumsum()
history["avg_capital_so_far"] = history["end_cost"].expanding().mean()
history["cum_pct_return"] = (history["cum_pnl"] / history["avg_capital_so_far"]) * 100

print("\nDaily Equity Curve approximation:")
print(history[["date", "end_cost", "cum_pnl", "avg_capital_so_far", "cum_pct_return"]].tail(15).to_string())
