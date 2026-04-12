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
    """Fetch 1 week (5 days) of 60-minute intraday data."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    # Use 7d range to ensure we capture Monday even on weekends
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=60m'
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
    
    # week_open is the first valid price of this 5-day window
    valid_closes = closes.dropna()
    if valid_closes.empty:
        raise RuntimeError(f"No valid price data for {symbol}")
        
    week_open = data['meta'].get('chartPreviousClose', valid_closes.iloc[0])

    df = pd.DataFrame({'time': timestamps, 'close': closes}).dropna()
    
    # Convert to Taiwan Time (UTC+8) for consistent date separation
    df['time'] = df['time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    
    return df, week_open


def generate_tv_chart(symbols_data, output_path):
    """
    Generate a 1-week intraday chart for each symbol.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    data_list = []
    fetch_errors = []
    for sym, title, prec in symbols_data:
        try:
            df, week_open = get_weekly_data(sym)
            data_list.append((df, week_open, title, prec))
        except Exception as e:
            msg = f"{sym}: {type(e).__name__} - {e}"
            print(f"Failed fetching {msg}")
            fetch_errors.append(msg)
            continue

    if not data_list:
        raise RuntimeError("All symbols failed:\n" + "\n".join(fetch_errors))

    num_plots = len(data_list)
    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 5 * num_plots), dpi=150, sharex=True)
    if num_plots == 1:
        axes = [axes]

    fig.patch.set_facecolor('#0a0e17')
    fig.subplots_adjust(hspace=0.4, right=0.88)

    def format_ax(ax, df, week_open, title, prec):
        ax.set_facecolor('#0a0e17')
        last_val = df.iloc[-1]['close']
        last_time = df.iloc[-1]['time']
        pct_diff = ((last_val - week_open) / week_open) * 100
        sign = "+" if pct_diff >= 0 else ""

        # Taiwan convention: Red = Up, Green = Down
        theme_color = '#EF4444' if pct_diff >= 0 else '#10B981'

        # --- Plot line & area fill ---
        ax.plot(df['time'], df['close'], color=theme_color, linewidth=2, zorder=3)
        ax.fill_between(
            df['time'], df['close'],
            y2=df['close'].min() * 0.99,
            color=theme_color, alpha=0.1, zorder=1
        )

        # Dot on last value
        ax.plot(last_time, last_val, marker='o', markersize=5, color=theme_color, zorder=5)

        # Horizontal dashed line at current price
        ax.axhline(last_val, color=theme_color, linestyle=':', linewidth=0.8, alpha=0.9, zorder=0)

        # --- Week-open reference line ---
        ax.axhline(week_open, color='#787b86', linestyle='--', linewidth=1.0, alpha=0.6, zorder=0)

        # --- Date separator vertical lines ---
        # Draw a line at the start of each unique date in the dataset
        unique_dates = pd.Series(df['time'].dt.date.unique())
        for d in unique_dates:
            # Find the first timestamp of that day
            day_start = df[df['time'].dt.date == d]['time'].iloc[0]
            ax.axvline(day_start, color='white', linestyle='-', linewidth=0.8, alpha=0.15, zorder=0)

        # --- Right-hand price labels ---
        trans = ax.get_yaxis_transform()
        y_min = min(df['close'].min(), week_open)
        y_max = max(df['close'].max(), week_open)
        y_span = max(y_max - y_min, 0.001)

        va_price, va_open = 'center', 'center'
        if abs(last_val - week_open) < y_span * 0.15:
            if last_val >= week_open:
                va_price, va_open = 'bottom', 'top'
            else:
                va_price, va_open = 'top', 'bottom'

        fmt = f"{{:.{prec}f}}"

        # Current price box
        bbox_price = dict(boxstyle="round,pad=0.3", fc=theme_color, ec=theme_color, alpha=1.0)
        ax.text(
            1.0, last_val,
            f' {fmt.format(last_val)} \n {sign}{pct_diff:.2f}% ',
            color='white', va=va_price, ha='left',
            fontsize=12, fontweight='bold',
            bbox=bbox_price, transform=trans, clip_on=False, zorder=10
        )

        # Week-open reference box
        bbox_open = dict(boxstyle="round,pad=0.4", fc="#4a4a5a", ec="#4a4a5a", alpha=1.0)
        ax.text(
            1.0, week_open,
            f' 週初開盤 \n {fmt.format(week_open)} ',
            color='white', va=va_open, ha='left',
            fontsize=12, fontweight='bold',
            bbox=bbox_open, transform=trans, clip_on=False, zorder=10
        )

        # --- Axis styling ---
        ax.tick_params(labelbottom=True, bottom=True, color='gray', labelsize=12)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')

        for spine in ['top', 'right', 'left', 'bottom']:
            ax.spines[spine].set_visible(False)
        ax.grid(axis='y', color='gray', alpha=0.1, linestyle='--')

        # Header title
        ax.set_title(
            f'{title}  {fmt.format(last_val)} ({sign}{pct_diff:.2f}%)',
            color=theme_color, loc='left', fontsize=22, fontweight='bold', pad=20
        )

    for i, (df, week_open, title, prec) in enumerate(data_list):
        format_ax(axes[i], df, week_open, title, prec)

    # --- Shared X-axis: Dates at daily intervals ---
    axes[-1].xaxis.set_major_locator(mdates.DayLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=0, ha='center', color='white', fontsize=14, fontweight='bold')

    plt.savefig(output_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
