import os
import urllib.request
import requests
import datetime
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
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
    """Fetch 10 days of high-detail 5m data and normalize to Taipei time."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 10d range ensures we have enough data to identify the last 5 trading days
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=5m'
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
    Generate a Gap-Free (Ordinal) 1-week chart for each symbol.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    raw_data_list = []
    fetch_errors = []
    for sym, title, prec in symbols_data:
        try:
            df = get_weekly_data(sym)
            raw_data_list.append((df, title, prec))
        except Exception as e:
            msg = f"{sym}: {e}"
            print(f"Failed fetching {msg}")
            fetch_errors.append(msg)
            continue

    if not raw_data_list:
        raise RuntimeError("All symbols failed:\n" + "\n".join(fetch_errors))

    # Identify the LAST 5 TRADING DAYS globally
    all_dates = pd.concat([d[0]['time'].dt.date for d in raw_data_list]).unique()
    last_5_dates = sorted(all_dates)[-5:]
    
    # Filter and prepare final data
    data_list = []
    for df, title, prec in raw_data_list:
        df_filtered = df[df['time'].dt.date.isin(last_5_dates)].copy()
        if df_filtered.empty: continue
        
        # Add ordinal index for plotting (skips gaps)
        df_filtered = df_filtered.reset_index(drop=True)
        week_open = df_filtered.iloc[0]['close']
        data_list.append((df_filtered, week_open, title, prec))

    if not data_list:
        raise RuntimeError("No data found for the last 5 trading days after filtering.")

    num_plots = len(data_list)
    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 5 * num_plots), dpi=150, sharex=True)
    if num_plots == 1: axes = [axes]

    fig.patch.set_facecolor('#0a0e17')
    fig.subplots_adjust(hspace=0.4, right=0.88)

    def format_ax(ax, df, week_open, title, prec):
        ax.set_facecolor('#0a0e17')
        last_val = df.iloc[-1]['close']
        last_idx = len(df) - 1
        pct_diff = ((last_val - week_open) / week_open) * 100
        sign = "+" if pct_diff >= 0 else ""

        theme_color = '#EF4444' if pct_diff >= 0 else '#10B981'

        # --- Plot line (Against integer index to skip time gaps) ---
        indices = np.arange(len(df))
        ax.plot(indices, df['close'], color=theme_color, linewidth=2, zorder=3)
        
        # Subtle area fill
        ax.fill_between(indices, df['close'], y2=df['close'].min() * 0.98, color=theme_color, alpha=0.1, zorder=1)

        # Markers for sparse symbols (Bonds)
        if len(df) < 300 or '^TNX' in title or 'Yield' in title:
            ax.scatter(indices, df['close'], color=theme_color, s=10, zorder=4, alpha=0.4)

        # Dot on last value
        ax.plot(last_idx, last_val, marker='o', markersize=6, color=theme_color, zorder=5)

        # Horizontal price lines
        ax.axhline(last_val, color=theme_color, linestyle=':', linewidth=0.8, alpha=0.9, zorder=0)
        ax.axhline(week_open, color='#787b86', linestyle='--', linewidth=1.0, alpha=0.6, zorder=0)

        # --- Date Separators (Vertical Lines at start of each day) ---
        # Find indices where the date changes
        df['date'] = df['time'].dt.date
        date_starts = df.drop_duplicates('date').index
        
        for idx in date_starts:
            ax.axvline(idx, color='white', linestyle='-', linewidth=1.0, alpha=0.2, zorder=2)

        # --- Ticks and Scaling ---
        ax.set_xlim(-1, len(df))
        
        # Prep Ticks (Mid-day for labels)
        tick_pos = []
        tick_labels = []
        for d in sorted(df['date'].unique()):
            day_indices = df[df['date'] == d].index
            # Place label at the center of the day's data segment
            mid_idx = day_indices[len(day_indices) // 2]
            tick_pos.append(mid_idx)
            tick_labels.append(d.strftime('%m/%d'))

        ax.xaxis.set_major_locator(mticker.FixedLocator(tick_pos))
        ax.xaxis.set_major_formatter(mticker.FixedFormatter(tick_labels))

        # --- Labels and Styling ---
        trans = ax.get_yaxis_transform()
        y_min, y_max = min(df['close'].min(), week_open), max(df['close'].max(), week_open)
        y_span = max(y_max - y_min, 0.001)

        va_price, va_open = 'center', 'center'
        if abs(last_val - week_open) < y_span * 0.15:
            va_price, va_open = ('bottom', 'top') if last_val >= week_open else ('top', 'bottom')

        fmt = f"{{:.{prec}f}}"
        ax.text(1.0, last_val, f' {fmt.format(last_val)} \n {sign}{pct_diff:.2f}%\n(5日) ',
                color='white', va=va_price, ha='left', fontsize=12, fontweight='bold', 
                bbox=dict(boxstyle="round,pad=0.3", fc=theme_color, ec=theme_color), transform=trans, clip_on=False)

        ax.text(1.0, week_open, f' 5日前開盤 \n {fmt.format(week_open)} ',
                color='white', va=va_open, ha='left', fontsize=12, fontweight='bold',
                bbox=dict(boxstyle="round,pad=0.4", fc="#4a4a5a", ec="#4a4a5a"), transform=trans, clip_on=False)

        ax.tick_params(labelbottom=True, bottom=True, labelsize=12, labelcolor='white')
        plt.setp(ax.xaxis.get_majorticklabels(), fontweight='bold', fontsize=14)
        
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')
        for spine in ['top', 'right', 'left', 'bottom']: ax.spines[spine].set_visible(False)
        ax.grid(axis='y', color='gray', alpha=0.1, linestyle='--')
        ax.set_title(f'{title}  {fmt.format(last_val)} ({sign}{pct_diff:.2f}%)', color=theme_color, loc='left', fontsize=22, fontweight='bold', pad=20)

    for i, (df, week_open, title, prec) in enumerate(data_list):
        format_ax(axes[i], df, week_open, title, prec)

    plt.savefig(output_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
