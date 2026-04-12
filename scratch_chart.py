import requests
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

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
    df['time'] = df['time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei')
    return df, prev_close

df_wti, prev_wti = get_intraday_data('CL=F')
try: df_brent, prev_brent = get_intraday_data('BZ=F')
except: df_brent, prev_brent = None, None

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), dpi=150, sharex=True)
fig.patch.set_facecolor('#0a0e17')
fig.subplots_adjust(hspace=0.2)
ax1.set_facecolor('#0a0e17')
ax2.set_facecolor('#0a0e17')

def format_ax(ax, df, prev_val, color, title):
    last_val = df.iloc[-1]['close']
    last_time = df.iloc[-1]['time']
    pct_diff = ((last_val - prev_val) / prev_val) * 100
    sign = "+" if pct_diff >= 0 else ""

    lbl_color = color
    if "Brent" in title and pct_diff < 0: lbl_color = '#EF4444'
    if "WTI" in title and pct_diff < 0: lbl_color = '#EF4444'

    ax.plot(df['time'], df['close'], color=color, linewidth=1.5, label=f'{title}  {last_val:.2f} ({sign}{pct_diff:.2f}%)')
    
    for alpha in np.linspace(0, 0.4, 20):
        ax.fill_between(df['time'], df['close'], y2=df['close'].min()*0.995, color=color, alpha=0.015, zorder=1)
    
    # physical dot
    ax.plot(last_time, last_val, marker='o', markersize=4, color=color, zorder=5)
    
    # Extend X axis to fit line, no massive padding needed inside the graph anymore
    ax.set_xlim(df['time'].min(), df['time'].max() + pd.Timedelta(minutes=15))

    # Y-axis dashed line going precisely to the y-axis (1.0 in axis coords)
    ax.axhline(last_val, color=lbl_color, linestyle=':', linewidth=0.8, alpha=0.9, zorder=0)

    # TRADINGVIEW STYLE Y-AXIS LABELS
    # Use axes coordinates for X (1.0 is the exact right spine) and data coordinates for Y
    trans = ax.get_yaxis_transform()
    
    # 1. Live Price Box on Y-axis
    bbox_props_pt = dict(boxstyle="round,pad=0.2", fc=lbl_color, ec=lbl_color, alpha=1.0)
    ax.text(1.0, last_val, f' {last_val:.2f} \n {sign}{pct_diff:.2f}% ', color='white', 
            va='center', ha='left', fontsize=9, fontweight='bold', 
            bbox=bbox_props_pt, transform=trans, clip_on=False, zorder=10)

    # 2. Prev Close Line & Box on Y-axis
    ax.axhline(prev_val, color='gray', linestyle='--', linewidth=0.8, alpha=0.6, zorder=0)
    
    bbox_props = dict(boxstyle="square,pad=0.2", fc="#4a4a5a", ec="#4a4a5a", alpha=1.0)
    ax.text(1.0, prev_val, f' Prev close \n {prev_val:.2f} ', color='white', 
            va='center', ha='left', fontsize=8, 
            bbox=bbox_props, transform=trans, clip_on=False, zorder=10)

    # Formatting basic ticks
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position('right')

    for spine in ['top', 'right', 'left', 'bottom']: ax.spines[spine].set_visible(False)
    ax.grid(axis='y', color='gray', alpha=0.1, linestyle='--')
    ax.legend(loc='upper left', frameon=False, labelcolor='linecolor', fontsize=12)

# WTI
format_ax(ax1, df_wti, prev_wti, '#10B981', 'WTI Crude')
# Brent
if df_brent is not None:
    format_ax(ax2, df_brent, prev_brent, '#3B82F6', 'Brent Crude')

# Shared X-Axis formatting
locator = mdates.HourLocator(interval=3)
formatter = mdates.DateFormatter('%H:%M')
ax2.xaxis.set_major_locator(locator)
ax2.xaxis.set_major_formatter(formatter)

plt.tight_layout()
# Give figure extra massive right margin padding so boxes aren't cut off by figure edge
fig.subplots_adjust(right=0.88)
plt.savefig(r'C:\Users\benso\.gemini\antigravity\brain\da4dc641-7a0d-4504-9127-4a71baf49610\oil_chart_demo_v6.png', bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
