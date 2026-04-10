import os
import requests
import datetime
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

# Use non-interactive backend for server environments
matplotlib.use('Agg')

# Configure font for Traditional Chinese support
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'HarmonyOS Sans SC', 'Noto Sans CJK TC', 'Heiti TC', 'Arial Unicode MS', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False # Fix minus sign display issues

plt.style.use('dark_background')

def get_intraday_data(symbol):
    headers = {'User-Agent': 'Mozilla/5.0'}
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m'
    r = requests.get(url, headers=headers)
    data = r.json()['chart']['result'][0]
    timestamps = pd.to_datetime(data['timestamp'], unit='s')
    closes = pd.Series(data['indicators']['quote'][0]['close'])
    prev_close = data['meta']['chartPreviousClose']
    
    df = pd.DataFrame({'time': timestamps, 'close': closes}).dropna()
    # Lock absolutely to User's Local Time (Taiwan / UTC+8)
    df['time'] = df['time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    return df, prev_close, 'UTC+8'

def generate_tv_chart(symbols_data, output_path):
    """
    symbols_data: list of tuples (symbol, title, precision)
    Example: [('CL=F', 'WTI 輕原油', 2), ('BZ=F', '布蘭特原油', 2)]
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    data_list = []
    for sym, title, prec in symbols_data:
        try:
            df, prev, tz = get_intraday_data(sym)
            data_list.append((df, prev, tz, title, prec))
        except Exception as e:
            print(f"Failed fetching {sym}: {e}")
            continue

    if not data_list:
        raise RuntimeError(f"Chart generation aborted: All symbols failed to fetch data from Yahoo API.")

    num_plots = len(data_list)
    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 4 * num_plots), dpi=150, sharex=True)
    if num_plots == 1: axes = [axes]
    
    fig.patch.set_facecolor('#0a0e17')
    fig.subplots_adjust(hspace=0.2, right=0.88)

    def format_ax(ax, df, prev_val, title, prec):
        ax.set_facecolor('#0a0e17')
        last_val = df.iloc[-1]['close']
        last_time = df.iloc[-1]['time']
        pct_diff = ((last_val - prev_val) / prev_val) * 100
        sign = "+" if pct_diff >= 0 else ""

        # Determine theme color based on TW conventions (Red=Up, Green=Down)
        theme_color = '#EF4444' if pct_diff >= 0 else '#10B981'

        # Plot line
        ax.plot(df['time'], df['close'], color=theme_color, linewidth=1.5)
        
        # Fill gradient
        for alpha in np.linspace(0, 0.4, 20):
            ax.fill_between(df['time'], df['close'], y2=df['close'].min()*0.995, color=theme_color, alpha=0.015, zorder=1)
        
        # physical dot
        ax.plot(last_time, last_val, marker='o', markersize=4, color=theme_color, zorder=5)
        
        ax.set_xlim(df['time'].min(), df['time'].max() + pd.Timedelta(minutes=15))
        ax.axhline(last_val, color=theme_color, linestyle=':', linewidth=0.8, alpha=0.9, zorder=0)

        trans = ax.get_yaxis_transform()
        
        # Prevent label collision using Vertical Alignment Repulsion
        y_min, y_max = min(df['close'].min(), prev_val), max(df['close'].max(), prev_val)
        y_span = max(y_max - y_min, 0.001)
        
        va_price, va_prev = 'center', 'center'
        
        if abs(last_val - prev_val) < y_span * 0.15:
            if last_val >= prev_val:
                va_price, va_prev = 'bottom', 'top'
            else:
                va_price, va_prev = 'top', 'bottom'
        
        # Formatter template using precision
        fmt = f"{{:.{prec}f}}"
        
        # Live Price Box
        bbox_props_pt = dict(boxstyle="round,pad=0.2", fc=theme_color, ec=theme_color, alpha=1.0)
        ax.text(1.0, last_val, f' {fmt.format(last_val)} \n {sign}{pct_diff:.2f}% ', color='white', 
                va=va_price, ha='left', fontsize=9, fontweight='bold', 
                bbox=bbox_props_pt, transform=trans, clip_on=False, zorder=10)

        # Prev Close Line & Box
        ax.axhline(prev_val, color='#787b86', linestyle='--', linewidth=0.8, alpha=0.6, zorder=0)
        
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#4a4a5a", ec="#4a4a5a", alpha=1.0)
        ax.text(1.0, prev_val, f' 昨日收盤 \n {fmt.format(prev_val)} ', color='white', 
                va=va_prev, ha='left', fontsize=9, fontweight='bold',
                bbox=bbox_props, transform=trans, clip_on=False, zorder=10)

        # Formatting basic ticks
        ax.tick_params(labelbottom=True, bottom=True, color='gray', labelsize=8)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')

        for spine in ['top', 'right', 'left', 'bottom']: 
            ax.spines[spine].set_visible(False)
        ax.grid(axis='y', color='gray', alpha=0.1, linestyle='--')
        
        # Header/Title separation
        ax.plot([0, 1], [1.0, 1.0], color='gray', alpha=0.1, linewidth=0.5, transform=ax.transAxes, clip_on=False)
        ax.set_title(f'{title}  {fmt.format(last_val)} ({sign}{pct_diff:.2f}%)', color=theme_color, loc='left', fontsize=12, fontweight='bold', pad=10)

    active_tz = "UTC+8"
    for i, (df, prev, tz, title, prec) in enumerate(data_list):
        format_ax(axes[i], df, prev, title, prec)
        active_tz = tz

    # Shared X-Axis formatting logic
    locator = mdates.HourLocator(interval=3)
    formatter = mdates.DateFormatter('%H:%M')
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(formatter)
    
    # Add dynamic Timezone watermark
    axes[-1].set_xlabel(f'時間 ({active_tz.replace("_", " ")})', color='gray', fontsize=9, alpha=0.7, labelpad=10)

    plt.savefig(output_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
