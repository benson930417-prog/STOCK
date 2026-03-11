import pandas as pd
import app
from collections import defaultdict, deque

df = pd.read_csv('data/master_trades.csv')
df = app.normalize_raw_trades(df)

inventory = defaultdict(deque)

# We want to record the total cost of inventory at the END of each date
daily_inventory_cost = {}

for stock, sdf in df.groupby("股名", sort=False):
    sdf = sdf.sort_values("日期")
    for date, ddf in sdf.groupby(sdf["日期"].dt.date, sort=False):
        for pool in ["board", "odd"]:
            dpool = ddf[ddf["成交股數"].apply(lambda q: app.pool_of(q) == pool)].copy()
            if dpool.empty:
                continue

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
                if b["qty"] == 0: day_buy_lots.popleft()
                if s["qty"] == 0: day_sell_lots.popleft()

            # 2) Remaining buys -> inventory 
            for lot in list(day_buy_lots):
                if lot["qty"] > 0:
                    inventory[stock].append({
                        "qty": int(lot["qty"]), 
                        "cps": float(lot["cps"]),
                        "fee_per_share": float(lot["fee_per_share"])
                    })

            # 3) Remaining sells -> consume inventory
            for lot in list(day_sell_lots):
                if lot["qty"] > 0:
                    remaining = int(lot["qty"])
                    while remaining > 0:
                        if not inventory[stock]: break
                        inv_lot = inventory[stock][0]
                        take = min(remaining, inv_lot["qty"])
                        inv_lot["qty"] -= take
                        remaining -= take
                        if inv_lot["qty"] == 0:
                            inventory[stock].popleft()
                            
        # End of processing all pools for this stock on this date
        # Calculate the total value of this stock's inventory
        cost = sum(lot['qty'] * lot['cps'] for lot in inventory[stock])
        if date not in daily_inventory_cost:
            daily_inventory_cost[date] = 0.0
        daily_inventory_cost[date] += cost

# The tricky part is the above loop evaluates per stock per day, 
# so `daily_inventory_cost` only has the cost for stocks TRADED on that date. 
# We need an absolute running total of ALL held stocks on each date.

# Better Approach: maintain the full inventory map date by date.
all_dates = sorted(df["日期"].dt.date.unique())
master_inventory = defaultdict(deque)

date_to_total_cost = {}

for date in all_dates:
    ddf = df[df["日期"].dt.date == date]
    
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
                        if inv_lot["qty"] == 0:
                            master_inventory[stock].popleft()
    
    # End of Date
    total_cost = sum(lot['qty'] * lot['cps'] for q in master_inventory.values() for lot in q)
    date_to_total_cost[date] = total_cost

max_cost = max(date_to_total_cost.values())
print(f"Max End-Of-Day Inventory Cost: {max_cost:,.0f} TWD")
for d, v in list(date_to_total_cost.items())[-5:]:
    print(f"{d}: {v:,.0f} TWD")
