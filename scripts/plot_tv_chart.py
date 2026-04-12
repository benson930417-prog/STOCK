import os
import urllib.request
import requests
import datetime
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import pytz

# Use non-interactive backend for server environments
matplotlib.use('Agg')

# Auto-download and inject Chinese fonts for the Oracle Server
font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'fonts')
font_path = os.path.join(font_dir, 'NotoSansTC-Regular.otf')

if not os.path.exists(font_path):
    os.makedirs(font_dir, exist_ok=True)
    try:
        url = 'https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf'
        urllib.request.urlretrieve(url, font_path)
    except Exception as e:
        print(f"Failed to auto-download CJK font: {e}")

if os.path.exists(font_path):
    font_manager.fontManager.addfont(font_path)
    prop = font_manager.FontProperties(fname=font_path)
    plt.rcParams['font.sans-serif'] = [prop.get_name(), 'sans-serif']
else:
    plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'Noto Sans CJK TC', 'sans-serif']

plt.rcParams['axes.unicode_minus'] = False  # Fix minus sign display issues
plt.style.use('dark_background')


def get_weekly_data(symbol):
    """Fetch enough data (10d) and normalize to Taipei time."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    # Fetch 10 days to ensure we have at least 5 trading days
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=60m'
    r = requests.get(url, headers=headers, timeout=10)

    payload = r.json()
    chart = payload.get('chart', {})
    error = chart.get('error')
    if error:
        raise RuntimeError(f"Yahoo API error for {symbol}: {error}")

    result = chart.get('result')
    if not result:
        raise RuntimeError(f"Yahoo API returned no result for {symbol}. HTTP {r.status_code}")

    data = result[0]
    timestamps = pd.to_datetime(data['timestamp'], unit='s')
    closes = pd.Series(data['indicators']['quote'][0]['close'])
    
    df = pd.DataFrame({'time': timestamps, 'close': closes}).dropna()
    # Convert to Taiwan Time (UTC+8)
    df['time'] = df['time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    
    return df


def generate_tv_chart(symbols_data, output_path):
    """
    Generate a chart showing the last 5 trading days for all symbols.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    raw_data = []
    fetch_errors = []
    for sym, title, prec in symbols_data:
        try:
            df = get_weekly_data(sym)
            raw_data.append((df, title, prec))
        except Exception as e:
            msg = f"{sym}: {e}"
            print(f"Failed fetching {msg}")
            fetch_errors.append(msg)
            continue

    if not raw_data:
        raise RuntimeError("All symbols failed:\n" + "\n".join(fetch_errors))

    # Identify the LAST 5 TRADING DAYS based on the FIRST symbol's data
    # (Usually they share the same trading calendar for Oil/Forex/Bonds)
    # We use the symbols' union of dates to be robust.
    all_dates = pd.concat([d[0]['time'].dt.date for d in raw_data]).unique()
    last_5_dates = sorted(all_dates)[-5:]
    
    global_start = pd.Timestamp(last_5_dates[0])
    global_end = pd.Timestamp(last_5_dates[-1]) + pd.Timedelta(days=1) # End of the last day

    data_list = []
    for df, title, prec in raw_data:
        # Filter each df to only include the last 5 dates
        mask = (df['time'] >= global_start) & (df['time'] < global_end)
        df_filtered = df[mask]
        
        if df_filtered.empty:
            continue
            
        week_open = df_filtered.iloc[0]['close']
        data_list.append((df_filtered, week_open, title, prec))

    if not data_list:
        raise RuntimeError("No data found for the last 5 trading days after filtering.")

    num_plots = len(data_list)
    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 5 * num_plots), dpi=150, sharex=True)
    if num_plots == 1: axes = [axes]

    fig.patch.set_facecolor('#0a0e17')
    fig.subplots_adjust(hspace=0.4, right=0.88)

    def format_ax(ax, df, week_open, title, prec, x_min, x_max):
        ax.set_facecolor('#0a0e17')
        last_val = df.iloc[-1]['close']
        last_time = df.iloc[-1]['time']
        pct_diff = ((last_val - week_open) / week_open) * 100
        sign = "+" if pct_diff >= 0 else ""

        theme_color = '#EF4444' if pct_diff >= 0 else '#10B981'

        # --- Plot line & area fill ---
        ax.plot(df['time'], df['close'], color=theme_color, linewidth=2, zorder=3)
        
        # Sparse data handling
        if len(df) < 50 or '^TNX' in title or 'Yield' in title:
            ax.scatter(df['time'], df['close'], color=theme_color, s=20, zorder=4, alpha=0.6)

        ax.fill_between(df['time'], df['close'], y2=df['close'].min() * 0.98, color=theme_color, alpha=0.1, zorder=1)

        # Current Price horizontal line
        ax.axhline(last_val, color=theme_color, linestyle=':', linewidth=0.8, alpha=0.9, zorder=0)

        # Week-open reference Line
        ax.axhline(week_open, color='#787b86', linestyle='--', linewidth=1.0, alpha=0.6, zorder=0)

        # --- Date Separators (Midnight lines for each day) ---
        for d in last_5_dates:
            ax.axvline(pd.Timestamp(d), color='white', linestyle='-', linewidth=0.8, alpha=0.15, zorder=0)

        # Ensure the chart covers all 5 days fully
        ax.set_xlim(x_min, x_max)

        # --- Labels ---
        trans = ax.get_yaxis_transform()
        y_min, y_max = min(df['close'].min(), week_open), max(df['close'].max(), week_open)
        y_span = max(y_max - y_min, 0.001)

        va_price, va_open = 'center', 'center'
        if abs(last_val - week_open) < y_span * 0.15:
            va_price, va_open = ('bottom', 'top') if last_val >= week_open else ('top', 'bottom')

        fmt = f"{{:.{prec}f}}"
        ax.text(1.0, last_val, f' {fmt.format(last_val)} \n {sign}{pct_diff:.2f}%\n(5日漲跌) ',
                color='white', va=va_price, ha='left', fontsize=12, fontweight='bold', 
                bbox=dict(boxstyle="round,pad=0.3", fc=theme_color, ec=theme_color), transform=trans, clip_on=False)

        ax.text(1.0, week_open, f' 5日前開盤 \n {fmt.format(week_open)} ',
                color='white', va=va_open, ha='left', fontsize=12, fontweight='bold',
                bbox=dict(boxstyle="round,pad=0.4", fc="#4a4a5a", ec="#4a4a5a"), transform=trans, clip_on=False)

        ax.tick_params(labelbottom=True, bottom=True, labelsize=12)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')
        for spine in ['top', 'right', 'left', 'bottom']: ax.spines[spine].set_visible(False)
        ax.grid(axis='y', color='gray', alpha=0.1, linestyle='--')
        ax.set_title(f'{title}  {fmt.format(last_val)} ({sign}{pct_diff:.2f}%)', color=theme_color, loc='left', fontsize=22, fontweight='bold', pad=20)

    for i, (df, week_open, title, prec) in enumerate(data_list):
        format_ax(axes[i], df, week_open, title, prec, global_start, global_end)

    axes[-1].xaxis.set_major_locator(mdates.DayLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=0, ha='center', color='white', fontsize=14, fontweight='bold')

    plt.savefig(output_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
