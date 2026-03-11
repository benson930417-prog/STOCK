import pandas as pd
import app

df = pd.read_csv('data/master_trades.csv')
df = app.normalize_raw_trades(df)

# We want the simple net cash flow: sum of all 淨收付金額 (Net Receipt/Payment Amount) over time
# A negative number indicates cash leaving the bank (being invested).
# A positive number indicates cash returning to the bank (being withdrawn).

# Sort chronologically by the actual date and index
df['date'] = pd.to_datetime(df['日期'])
# Use a stable sort to ensure trades on the same day are somewhat ordered, 
# although we only really care about the end-of-day cumulative cash flow.
df = df.sort_values(['date', '股名']).reset_index(drop=True)

df['cash_flow'] = df['淨收付金額']
df['cumulative_cash_flow'] = df['cash_flow'].cumsum()

# The amount invested is the negation of the cumulative cash flow.
# If cumulative is -4.8M, the invested capital base is 4.8M.
df['invested_capital'] = -df['cumulative_cash_flow']

# Let's see the end-of-day invested capital over time.
daily_invested = df.groupby(df['date'].dt.date)['invested_capital'].last().reset_index()

print("Daily Net Invested Capital (Cumulative Cash Flow):")
print(daily_invested.tail(20).to_string())

print(f"\nMax Invested Capital (Max Drawdown from Bank): {daily_invested['invested_capital'].max():,.0f} TWD")
print(f"Current Invested Capital (Latest Bank Balance): {daily_invested['invested_capital'].iloc[-1]:,.0f} TWD")
