"""Generate cached market-pulse summary JPGs for LINE.

This intentionally does not open Streamlit or click the 市場脈動 tab.  The
daily job already has the SQLite benchmark DB, so this script renders a small
standalone HTML report from the same data and screenshots that HTML to JPG.
"""
from __future__ import annotations

import html
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from PIL import Image
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.etf_benchmark.step6_regimes import classify_leg, zigzag_pivots

SUMMARY_DIR = ROOT_DIR / "data" / "summaries"
DB_PATH = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"

CROSS_ASSET_INDICES: list[tuple[str, str]] = [
    ("^SOX", "費城半導體"),
    ("^TWII", "加權指數"),
    ("^IXIC", "NASDAQ"),
    ("^GSPC", "S&P 500"),
    ("^DJI", "道瓊"),
]

REGIME_LABELS = {
    "bull": "多頭",
    "correction": "小熊",
    "mini_bear": "中熊",
    "bear": "大熊",
}

COLORS = {
    "red": "#ef4444",
    "orange": "#f97316",
    "yellow": "#eab308",
    "green": "#22c55e",
    "blue": "#38bdf8",
    "gray": "#9ca3af",
}


def get_prices(ticker: str) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT date, close
            FROM prices
            WHERE ticker = ?
              AND close IS NOT NULL
            ORDER BY date
            """,
            conn,
            params=[ticker],
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"])


def latest_taiex_date() -> str:
    df = get_prices("^TWII")
    if df.empty:
        raise RuntimeError("No ^TWII price date found in etf_bench DB")
    return df["date"].max().date().isoformat()


def pct_change(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    prev = float(series.iloc[-periods - 1])
    cur = float(series.iloc[-1])
    if prev <= 0:
        return None
    return (cur / prev - 1.0) * 100.0


def rolling_vol(series: pd.Series, window: int = 20) -> pd.Series:
    return series.pct_change().dropna().rolling(window).std() * (252 ** 0.5) * 100.0


def stretch_zscore(series: pd.Series, ma_window: int = 200, lookback: int = 504) -> tuple[float | None, float | None]:
    if len(series) < ma_window + 30:
        return None, None
    ma = series.rolling(ma_window).mean()
    stretch = (series - ma) / ma * 100.0
    cur = float(stretch.iloc[-1])
    hist = stretch.iloc[-lookback:].dropna() if len(stretch) > lookback else stretch.dropna()
    if len(hist) < 30:
        return cur, None
    std = float(hist.std())
    if std <= 0:
        return cur, None
    return cur, (cur - float(hist.mean())) / std


def latest_regime(series: pd.Series, threshold_pct: float = 4.0) -> tuple[str, int | None]:
    """Match the 市場脈動 tab: compute the current ZigZag regime live at 4%.

    The persisted regimes table is produced by step6 and may use a different
    threshold/history snapshot, so using it here can drift from the web tab.
    """
    if series is None or len(series) < 2:
        return "—", None

    prices = series.to_numpy(dtype=float)
    dates = pd.to_datetime(series.index).tolist()
    pivot_idxs = zigzag_pivots(prices, threshold_pct)
    rows = []
    for start_idx, end_idx in zip(pivot_idxs[:-1], pivot_idxs[1:]):
        p0 = float(prices[start_idx])
        p1 = float(prices[end_idx])
        if p0 <= 0:
            continue
        mag = (p1 - p0) / p0 * 100.0
        rows.append(
            {
                "start_date": pd.Timestamp(dates[start_idx]),
                "end_date": pd.Timestamp(dates[end_idx]),
                "regime": classify_leg(mag, threshold_pct),
            }
        )
    if not rows:
        return "—", None

    df = pd.DataFrame(rows)
    date = pd.Timestamp(series.index[-1])
    active = df[(df["start_date"] <= date) & (df["end_date"] >= date)]
    row = active.iloc[-1] if not active.empty else df.iloc[-1]
    label = REGIME_LABELS.get(str(row["regime"]), str(row["regime"]))
    start = pd.Timestamp(row["start_date"])
    days = int(((series.index >= start) & (series.index <= date)).sum())
    return label, days


def classify_regime(label: str, days: int | None, dist_1y: float | None) -> tuple[str, str, str]:
    if label == "多頭":
        near_high = dist_1y is not None and dist_1y >= -1.0
        mature = days is not None and days >= 180
        moderate = days is not None and days >= 90
        if near_high and mature:
            return COLORS["red"], "成熟多頭近高點", "多頭近高點+持續>180天 → 拉回機率明顯升高"
        if near_high:
            return COLORS["orange"], "多頭近高點", "多頭近 1 年高點 → 拉回風險偏高"
        if mature:
            return COLORS["yellow"], "延長多頭", "多頭持續超過 180 天屬延長階段"
        if moderate:
            return COLORS["green"], "成熟多頭", "多頭通常持續 50~200 天"
        return COLORS["green"], "健康上升", "多頭通常持續 50~200 天"

    if label == "小熊":
        return COLORS["yellow"], "短期修正", "小熊通常 5~20 天"
    if label == "中熊":
        return COLORS["orange"], "中期回檔", "中熊通常 20~60 天"
    if label == "大熊":
        return COLORS["red"], "深度熊市", "大熊通常 30~150 天"
    return COLORS["gray"], label, "ZigZag 4%：描述目前波段，不等於預測"


def classify_day_change(pct: float) -> tuple[str, str]:
    if pct >= 2.5:
        return COLORS["red"], "大漲"
    if pct >= 1.0:
        return COLORS["orange"], "上漲偏大"
    if pct <= -2.5:
        return COLORS["red"], "大跌"
    if pct <= -1.0:
        return COLORS["yellow"], "下跌偏大"
    if abs(pct) < 0.3:
        return COLORS["gray"], "持平"
    return COLORS["green"], "正常波動"


def classify_high_distance(dist: float) -> tuple[str, str]:
    if dist >= -0.5:
        return COLORS["orange"], "近高點"
    if dist >= -1.5:
        return COLORS["yellow"], "高位區"
    if dist >= -8.5:
        return COLORS["green"], "常態區"
    if dist >= -16:
        return COLORS["blue"], "回檔區"
    return COLORS["blue"], "低位區"


def classify_low_distance(dist: float) -> tuple[str, str]:
    if dist >= 30:
        return COLORS["red"], "急漲"
    if dist >= 25:
        return COLORS["orange"], "強漲"
    if dist >= 20:
        return COLORS["yellow"], "偏高反彈"
    if dist >= 8:
        return COLORS["green"], "常態反彈"
    return COLORS["blue"], "近低位"


def classify_return(value: float | None, window: str) -> tuple[str, str]:
    if value is None:
        return COLORS["gray"], "資料不足"
    if window == "30d":
        if value >= 18:
            return COLORS["red"], "急漲"
        if value >= 9:
            return COLORS["orange"], "大漲"
        if value <= -18:
            return COLORS["red"], "重挫"
        if value <= -10:
            return COLORS["orange"], "大跌"
    else:
        if value >= 25:
            return COLORS["red"], "急漲"
        if value >= 16:
            return COLORS["orange"], "大漲"
        if value <= -25:
            return COLORS["red"], "重挫"
        if value <= -18:
            return COLORS["orange"], "大跌"
    return COLORS["green"], "常態"


def classify_acceleration(value: float | None) -> tuple[str, str]:
    if value is None:
        return COLORS["gray"], "資料不足"
    if value >= 20:
        return COLORS["red"], "強烈加速"
    if value >= 10:
        return COLORS["orange"], "明顯加速"
    if value <= -16:
        return COLORS["blue"], "急轉弱"
    if value <= -9:
        return COLORS["blue"], "明顯減速"
    return COLORS["green"], "穩定"


def classify_vol(value: float | None) -> tuple[str, str]:
    if value is None:
        return COLORS["gray"], "資料不足"
    if value >= 35:
        return COLORS["red"], "高波動"
    if value >= 24:
        return COLORS["orange"], "偏高"
    if value >= 13:
        return COLORS["green"], "正常"
    return COLORS["blue"], "低波動"


def classify_zscore(z: float | None) -> tuple[str, str]:
    if z is None:
        return COLORS["gray"], "資料不足"
    if z >= 2.0:
        return COLORS["red"], "高位"
    if z >= 1.5:
        return COLORS["orange"], "偏高"
    if z <= -2.0:
        return COLORS["blue"], "低位"
    if z <= -1.0:
        return COLORS["blue"], "偏低"
    return COLORS["green"], "中性"


def fmt_pct(value: float | None, digits: int = 1, suffix: str = "%") -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}{suffix}"


def metric_card(label: str, value: str, color: str, tag: str, ref: str) -> str:
    return f"""
    <div class="metric" style="border-left-color:{color}">
      <div class="label">{html.escape(label)}</div>
      <div class="value" style="color:{color}">{value}</div>
      <div class="tag" style="color:{color}">● {html.escape(tag)}</div>
      <div class="ref">{html.escape(ref)}</div>
    </div>
    """


def build_snapshot() -> dict:
    taiex_df = get_prices("^TWII")
    if taiex_df.empty or len(taiex_df) < 61:
        raise RuntimeError("Not enough ^TWII price data for market pulse summary")

    taiex = taiex_df.set_index("date")["close"].astype(float)
    latest_date = taiex.index[-1].date().isoformat()
    current = float(taiex.iloc[-1])
    prev = float(taiex.iloc[-2])
    day_pct = (current / prev - 1.0) * 100.0
    regime, regime_days = latest_regime(taiex, threshold_pct=4.0)

    high_1y = float(taiex.iloc[-252:].max()) if len(taiex) >= 252 else float(taiex.max())
    low_60 = float(taiex.iloc[-60:].min()) if len(taiex) >= 60 else float(taiex.min())
    dist_high = (current / high_1y - 1.0) * 100.0
    dist_low = (current / low_60 - 1.0) * 100.0

    ret_30 = pct_change(taiex, 30)
    ret_60 = pct_change(taiex, 60)
    prior_30 = None
    if len(taiex) >= 61:
        p60 = float(taiex.iloc[-61])
        p30 = float(taiex.iloc[-31])
        prior_30 = (p30 / p60 - 1.0) * 100.0 if p60 > 0 else None
    accel = ret_30 - prior_30 if ret_30 is not None and prior_30 is not None else None
    vol_series = rolling_vol(taiex, 20).dropna()
    vol_20 = float(vol_series.iloc[-1]) if not vol_series.empty else None

    stretch_rows = []
    stretched = []
    for ticker, name in CROSS_ASSET_INDICES:
        df = get_prices(ticker)
        if df.empty:
            stretch, z = None, None
        else:
            prices = df.set_index("date")["close"].astype(float)
            stretch, z = stretch_zscore(prices)
        color, tag = classify_zscore(z)
        if z is not None and z >= 2:
            stretched.append(name)
        stretch_rows.append(
            {
                "ticker": ticker,
                "name": name,
                "stretch": stretch,
                "z": z,
                "color": color,
                "tag": tag,
            }
        )
    stretch_rows.sort(key=lambda row: row["z"] if row["z"] is not None else -999, reverse=True)

    return {
        "latest_date": latest_date,
        "current": current,
        "day_pct": day_pct,
        "regime": regime,
        "regime_days": regime_days,
        "dist_high": dist_high,
        "dist_low": dist_low,
        "ret_30": ret_30,
        "ret_60": ret_60,
        "accel": accel,
        "vol_20": vol_20,
        "stretch_rows": stretch_rows,
        "stretched": stretched,
    }


def render_html(snapshot: dict) -> str:
    day_color, day_tag = classify_day_change(snapshot["day_pct"])
    high_color, high_tag = classify_high_distance(snapshot["dist_high"])
    low_color, low_tag = classify_low_distance(snapshot["dist_low"])
    r30_color, r30_tag = classify_return(snapshot["ret_30"], "30d")
    r60_color, r60_tag = classify_return(snapshot["ret_60"], "60d")
    acc_color, acc_tag = classify_acceleration(snapshot["accel"])
    vol_color, vol_tag = classify_vol(snapshot["vol_20"])

    regime_days = snapshot["regime_days"]
    regime_suffix = f"{snapshot['regime']}已 {regime_days} 交易日" if regime_days is not None else ""
    regime_color, regime_tag, regime_ref = classify_regime(snapshot["regime"], regime_days, snapshot["dist_high"])
    breadth = f"{len(snapshot['stretched'])} / {len(snapshot['stretch_rows'])} 個指數處於自身高位"
    if snapshot["stretched"]:
        breadth += "：" + "、".join(snapshot["stretched"])

    rows = "\n".join(
        f"""
        <tr>
          <td><b>{html.escape(row['name'])}</b><span>{html.escape(row['ticker'])}</span></td>
          <td>{fmt_pct(row['stretch'])}</td>
          <td style="color:{row['color']}; font-weight:800">{fmt_pct(row['z'], 2, '')}</td>
          <td style="color:{row['color']}">{html.escape(row['tag'])}</td>
        </tr>
        """
        for row in snapshot["stretch_rows"]
    )

    summary = (
        f"加權指數今日 {snapshot['day_pct']:+.2f}%（{day_tag}），"
        f"目前規制為 {snapshot['regime']}，"
        f"距 1 年高點 {fmt_pct(snapshot['dist_high'])}，"
        f"近 30 日報酬 {fmt_pct(snapshot['ret_30'])}。"
        f"廣度顯示 {breadth}。"
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      width: 1080px;
      min-height: 1500px;
      background: #0b0f17;
      color: #f8fafc;
      font-family: "Noto Sans TC", "Microsoft JhengHei", "PingFang TC", Arial, sans-serif;
      padding: 46px;
    }}
    .title {{ display:flex; align-items:flex-end; justify-content:space-between; gap:24px; }}
    h1 {{ margin:0; font-size:56px; letter-spacing:0; }}
    .date {{ color:#94a3b8; font-size:24px; font-weight:700; }}
    .subtitle {{ margin:18px 0 34px; color:#a3aab8; font-size:22px; line-height:1.5; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    .metric {{
      background:#121821;
      border:1px solid #273244;
      border-left:7px solid;
      border-radius:10px;
      padding:22px 24px;
      min-height:170px;
    }}
    .label {{ color:#a7b0c0; font-size:22px; font-weight:800; }}
    .value {{ margin-top:10px; font-size:42px; font-weight:900; line-height:1.08; }}
    .tag {{ margin-top:10px; font-size:22px; font-weight:850; }}
    .ref {{ margin-top:12px; color:#7f8899; font-size:17px; line-height:1.35; }}
    .section {{ margin-top:36px; }}
    h2 {{ margin:0 0 16px; font-size:34px; }}
    table {{
      width:100%;
      border-collapse:collapse;
      overflow:hidden;
      border-radius:10px;
      background:#111720;
      border:1px solid #263244;
    }}
    th, td {{ padding:16px 18px; text-align:left; border-bottom:1px solid #263244; font-size:22px; }}
    th {{ color:#9ca3af; background:#171d28; font-size:18px; }}
    td span {{ display:block; color:#7e8797; font-size:15px; margin-top:3px; }}
    .summary {{
      margin-top:34px;
      border-left:7px solid #6366f1;
      background:#121827;
      border-radius:10px;
      padding:24px 28px;
      color:#dbe4f0;
      font-size:25px;
      line-height:1.55;
      font-weight:700;
    }}
  </style>
</head>
<body>
  <div class="title">
    <h1>市場脈動</h1>
    <div class="date">資料截至 {html.escape(snapshot['latest_date'])}</div>
  </div>
  <div class="subtitle">數字是市場體溫計，不是買賣訊號。</div>

  <div class="grid">
    {metric_card("今日漲跌（加權指數）", f"{snapshot['current']:,.0f}　<span style='font-size:28px'>{snapshot['day_pct']:+.2f}%</span>", day_color, day_tag, "今日漲跌單獨看：正常約 ±1%，±2.5% 以上才算少見")}
    {metric_card("目前規制", f"{html.escape(snapshot['regime'])}　<span style='font-size:28px'>{html.escape(regime_suffix)}</span>", regime_color, regime_tag, regime_ref)}
    {metric_card("距 1 年高點", fmt_pct(snapshot['dist_high']), high_color, high_tag, "接近 0 代表貼近一年高點；越負代表離高點越遠")}
    {metric_card("距 60 日低點", fmt_pct(snapshot['dist_low']), low_color, low_tag, "反彈幅度；過高代表短期速度偏快")}
    {metric_card("30 日報酬", fmt_pct(snapshot['ret_30'], 2), r30_color, r30_tag, "30 日：> +18% 屬急漲區")}
    {metric_card("60 日報酬", fmt_pct(snapshot['ret_60'], 2), r60_color, r60_tag, "60 日：> +25% 屬急漲區")}
    {metric_card("加速度", fmt_pct(snapshot['accel'], 2, 'pp'), acc_color, acc_tag, "近 30 天報酬減前 30 天報酬；> +20pp 才極端")}
    {metric_card("20 日年化波動", fmt_pct(snapshot['vol_20'], 1), vol_color, vol_tag, "加權指數常態約 13% 到 24%，>35% 才高波動")}
  </div>

  <div class="section">
    <h2>跨資產拉伸</h2>
    <table>
      <thead><tr><th>指數</th><th>距 MA200</th><th>z-score</th><th>判斷</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div class="summary">{html.escape(summary)}</div>
</body>
</html>"""


def compress_image(path: Path) -> None:
    img = Image.open(path).convert("RGB")
    max_width = 1200
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
    img.save(path, format="JPEG", quality=90, optimize=True, progressive=True)


def generate() -> tuple[str, Path, Path]:
    snapshot = build_snapshot()
    latest_date = snapshot["latest_date"]
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = SUMMARY_DIR / "market_pulse_latest.jpg"
    dated_path = SUMMARY_DIR / f"market_pulse_{latest_date}.jpg"
    html_doc = render_html(snapshot)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 1500}, device_scale_factor=1)
        page.set_content(html_doc, wait_until="load")
        page.screenshot(path=str(latest_path), type="jpeg", quality=90, full_page=True)
        browser.close()

    compress_image(latest_path)
    shutil.copyfile(latest_path, dated_path)
    print(f"Saved {latest_path.relative_to(ROOT_DIR)}")
    print(f"Saved {dated_path.relative_to(ROOT_DIR)}")
    return latest_date, latest_path, dated_path


if __name__ == "__main__":
    try:
        generate()
    except Exception as exc:
        print(f"generate_market_pulse_summary failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
