"""Render the cached Taiwan financing-risk estimate as a LINE-ready JPG."""
from __future__ import annotations

import html
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
from PIL import Image
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.margin_risk import (
    LEGAL_CALL_REFERENCE,
    LEGAL_CURE_REFERENCE,
    load_margin_cache,
    make_snapshot,
    tone_color,
)


SUMMARY_DIR = ROOT_DIR / "data" / "summaries"
LINE_IMAGE_WIDTH = 720
LINE_SAFE_TOP_HEIGHT = 92


def _fmt_change(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f} 點"


def _svg_points(values: pd.Series, width: float, height: float, y_min: float, y_max: float) -> str:
    if len(values) <= 1 or y_max <= y_min:
        return ""
    coords = []
    for i, value in enumerate(values.astype(float)):
        x = i / (len(values) - 1) * width
        y = height - (value - y_min) / (y_max - y_min) * height
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def _chart_svg(df: pd.DataFrame) -> str:
    view = df.iloc[-126:].copy()
    chart_w, chart_h = 616.0, 250.0
    ratio = view["estimate_pct"].astype(float)
    y_min = min(float(ratio.min()) - 4.0, LEGAL_CALL_REFERENCE - 3.0)
    y_max = max(float(ratio.max()) + 4.0, LEGAL_CURE_REFERENCE + 3.0)
    ratio_points = _svg_points(ratio, chart_w, chart_h, y_min, y_max)

    index_points = ""
    taiex = view["taiex_close"].dropna()
    if len(taiex) >= 2 and float(taiex.max()) > float(taiex.min()):
        aligned = view["taiex_close"].interpolate().bfill().ffill()
        index_points = _svg_points(aligned, chart_w, chart_h, float(aligned.min()), float(aligned.max()))

    def y(value: float) -> float:
        return chart_h - (value - y_min) / (y_max - y_min) * chart_h

    ticks = sorted({round(y_min / 10) * 10, 130, 150, 166, round(y_max / 10) * 10})
    grid = "".join(
        f'<line x1="0" x2="{chart_w}" y1="{y(t):.1f}" y2="{y(t):.1f}" '
        f'stroke="#263244" stroke-width="1"/><text x="8" y="{y(t)-8:.1f}" '
        f'fill="#718096" font-size="18">{t}%</text>'
        for t in ticks
        if y_min <= t <= y_max
    )
    legal = (
        f'<line x1="0" x2="{chart_w}" y1="{y(LEGAL_CALL_REFERENCE):.1f}" '
        f'y2="{y(LEGAL_CALL_REFERENCE):.1f}" stroke="#22c55e" stroke-width="2" stroke-dasharray="8 8"/>'
        f'<line x1="0" x2="{chart_w}" y1="{y(LEGAL_CURE_REFERENCE):.1f}" '
        f'y2="{y(LEGAL_CURE_REFERENCE):.1f}" stroke="#f59e0b" stroke-width="2" stroke-dasharray="8 8"/>'
    )
    index_line = (
        f'<polyline points="{index_points}" fill="none" stroke="#ef4444" '
        'stroke-width="3" stroke-linejoin="round" opacity=".65"/>'
        if index_points
        else ""
    )
    return f"""
    <svg viewBox="0 0 {chart_w:.0f} {chart_h:.0f}" role="img" aria-label="融資擔保估算率趨勢">
      {grid}{legal}{index_line}
      <polyline points="{ratio_points}" fill="none" stroke="#38bdf8" stroke-width="5"
                stroke-linejoin="round" stroke-linecap="round"/>
    </svg>
    """


def _financing_svg(df: pd.DataFrame) -> str:
    view = df.iloc[-63:].copy()
    values = view["financing_balance_billion"].astype(float)
    chart_w, chart_h = 616.0, 190.0
    if values.empty:
        return ""
    low = float(values.min())
    high = float(values.max())
    padding = max((high - low) * 0.18, high * 0.008, 1.0)
    y_min, y_max = low - padding, high + padding
    points = _svg_points(values, chart_w, chart_h, y_min, y_max)
    area_points = f"0,{chart_h:.0f} {points} {chart_w:.0f},{chart_h:.0f}"
    latest = float(values.iloc[-1])
    change_20 = None if len(values) <= 20 else latest - float(values.iloc[-21])
    change_label = "資料累積中" if change_20 is None else f"近 20 日 {change_20:+,.0f} 億"
    return f"""
    <div class="mini-chart-head">
      <span>近 3 個月融資餘額</span>
      <b>{latest:,.0f} 億 · {change_label}</b>
    </div>
    <svg viewBox="0 0 {chart_w:.0f} {chart_h:.0f}" role="img" aria-label="融資餘額趨勢">
      <defs>
        <linearGradient id="financingFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#818cf8" stop-opacity=".42"/>
          <stop offset="100%" stop-color="#818cf8" stop-opacity=".03"/>
        </linearGradient>
      </defs>
      <line x1="0" x2="{chart_w}" y1="{chart_h}" y2="{chart_h}" stroke="#263244" stroke-width="2"/>
      <polygon points="{area_points}" fill="url(#financingFill)"/>
      <polyline points="{points}" fill="none" stroke="#818cf8" stroke-width="5"
                stroke-linejoin="round" stroke-linecap="round"/>
    </svg>
    """


def render_html(df: pd.DataFrame) -> str:
    snapshot = make_snapshot(df)
    latest = df.iloc[-1]
    color = tone_color(snapshot.tone)
    percentile = "—" if snapshot.percentile_1y is None else f"{snapshot.percentile_1y:.0f}%"
    excluded_etf = latest.get("excluded_etf_collateral_billion")
    excluded_etf_story = (
        "本日排除金額待新快取補齊"
        if pd.isna(excluded_etf)
        else f"本日排除約 {float(excluded_etf):,.0f} 億元"
    )

    financing_20d = None if len(df) <= 20 else float(
        latest["financing_balance_billion"] - df.iloc[-21]["financing_balance_billion"]
    )
    financing_story = "資料累積中" if financing_20d is None else f"{financing_20d:+,.0f} 億"

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing:border-box; }}
    body {{ margin:0; width:{LINE_IMAGE_WIDTH}px; padding:0 28px 30px; background:#080d16;
            color:#f8fafc; font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",Arial,sans-serif; }}
    .safe-top {{ height:{LINE_SAFE_TOP_HEIGHT}px; background:#000; margin:0 -28px 22px; }}
    .eyebrow {{ color:#64748b; font-size:14px; font-weight:900; letter-spacing:.12em; }}
    .top {{ display:flex; justify-content:space-between; align-items:flex-end; gap:20px; }}
    h1 {{ font-size:42px; margin:6px 0 0; letter-spacing:-1px; }}
    .date {{ color:#94a3b8; font-size:16px; font-weight:800; white-space:nowrap; }}
    .banner {{ margin-top:20px; padding:20px 22px; border-radius:14px; background:#111827;
               border:1px solid #334155; border-left:7px solid {color}; }}
    .banner strong {{ display:block; color:{color}; font-size:30px; line-height:1.2; }}
    .banner span {{ display:block; margin-top:7px; color:#cbd5e1; font-size:17px; line-height:1.45; }}
    .hero {{ margin-top:18px; display:grid; grid-template-columns:1fr; gap:14px; }}
    .big,.metric,.balance {{ background:#101722; border:1px solid #263244; border-radius:14px; padding:21px; }}
    .big {{ display:grid; grid-template-columns:1fr auto; gap:20px; align-items:center; }}
    .label {{ color:#94a3b8; font-size:16px; font-weight:800; }}
    .number {{ color:#38bdf8; font-size:62px; font-weight:950; line-height:1; margin-top:10px; }}
    .sub {{ color:#94a3b8; font-size:15px; margin-top:10px; line-height:1.45; }}
    .balance-number {{ color:#f8fafc; font-size:37px; font-weight:950; text-align:right; }}
    .balance-change {{ color:#a5b4fc; font-size:16px; font-weight:800; margin-top:8px; text-align:right; }}
    .metrics {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }}
    .metric {{ min-height:112px; padding:17px; }}
    .metric b {{ display:block; margin-top:8px; font-size:25px; }}
    .chart,.balance {{ margin-top:18px; padding:20px 22px 16px; }}
    .section-title {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; gap:14px; }}
    h2 {{ margin:0; font-size:25px; }}
    .legend {{ color:#94a3b8; font-size:13px; font-weight:800; white-space:nowrap; }}
    .legend i {{ display:inline-block; width:16px; height:3px; margin:0 4px 3px 10px; }}
    .mini-chart-head {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin-bottom:10px; }}
    .mini-chart-head span {{ font-size:24px; font-weight:900; }}
    .mini-chart-head b {{ color:#a5b4fc; font-size:16px; }}
    .insights {{ margin-top:18px; display:grid; grid-template-columns:1fr; gap:10px; }}
    .insight {{ padding:16px 18px; background:#101722; border:1px solid #263244; border-radius:13px; display:grid; grid-template-columns:132px 1fr; gap:14px; }}
    .insight b {{ font-size:17px; color:#e2e8f0; }}
    .insight span {{ color:#94a3b8; font-size:15px; line-height:1.45; }}
    .formula {{ margin-top:18px; padding:17px 19px; border-radius:13px; background:#0d1420; color:#aab5c5;
                font-size:14px; line-height:1.55; border:1px solid #223047; }}
    .formula b {{ color:#e2e8f0; }}
    .footer {{ margin-top:14px; color:#64748b; font-size:12px; line-height:1.5; }}
  </style>
</head>
<body>
  <div class="safe-top" aria-hidden="true"></div>
  <div class="top">
    <div><div class="eyebrow">MASTER WU · DAILY RISK RADAR</div><h1>融資餘額與風險</h1></div>
    <div class="date">資料截至 {html.escape(snapshot.date)}</div>
  </div>

  <div class="banner">
    <strong>{html.escape(snapshot.headline)}</strong>
    <span>{html.escape(snapshot.status)} · 近 5 日 {html.escape(snapshot.direction)}。先看變化速度，再看絕對數字。</span>
  </div>

  <div class="hero">
    <div class="big">
      <div>
        <div class="label">全市場融資擔保估算率</div>
        <div class="number">{snapshot.estimate_pct:.1f}%</div>
        <div class="sub">非 ETF 擔保市值 ÷ 官方融資餘額；公開資料估算，非個別帳戶維持率</div>
      </div>
      <div>
        <div class="label" style="text-align:right">融資餘額</div>
        <div class="balance-number">{snapshot.financing_billion:,.0f} 億</div>
        <div class="balance-change">近 20 日 {financing_story}</div>
      </div>
    </div>
    <div class="metrics">
      <div class="metric"><div class="label">近 1 日</div><b>{_fmt_change(snapshot.change_1d)}</b></div>
      <div class="metric"><div class="label">近 5 日</div><b style="color:{color}">{_fmt_change(snapshot.change_5d)}</b></div>
      <div class="metric"><div class="label">距 130% 參考</div><b>{snapshot.distance_to_call:+.1f} 點</b></div>
    </div>
  </div>

  <div class="chart">
    <div class="section-title"><h2>近 6 個月趨勢</h2><div class="legend"><i style="background:#38bdf8"></i>估算率<i style="background:#ef4444"></i>加權指數</div></div>
    {_chart_svg(df)}
  </div>

  <div class="balance">{_financing_svg(df)}</div>

  <div class="insights">
    <div class="insight"><b>怎麼判斷</b><span>估算率下滑且融資餘額上升，代表槓桿緩衝正在變薄；兩者要一起看。</span></div>
    <div class="insight"><b>目前方向</b><span>近 5 日 {_fmt_change(snapshot.change_5d)}，判斷為「{html.escape(snapshot.direction)}」；近一年位置 {percentile}。</span></div>
    <div class="insight"><b>上市 / 上櫃</b><span>{float(latest['twse_estimate_pct']):.1f}% / {float(latest['tpex_estimate_pct']):.1f}%，避免單一市場遮住風險。</span></div>
  </div>

  <div class="formula"><b>公開資料估算：</b>Σ（上市 + 上櫃非 ETF 融資張數 × 收盤價）÷ 官方全市場融資金額餘額。ETF 只從分子排除；{html.escape(excluded_etf_story)}。
    130% / 166% 是個別信用帳戶的法規參考，不是這條全市場估算線的精準斷頭門檻；本卡不等同 MacroMicro 專有序列。</div>
  <div class="footer">來源：TWSE MI_MARGN / MI_INDEX、TPEx 融資融券餘額 / 每日收盤行情。分子不含 ETF；每日 18:30 快取。紅色＝改善，綠色＝惡化。本卡僅供風險觀察，不是買賣訊號。</div>
</body>
</html>"""


def _compress(path: Path) -> None:
    image = Image.open(path).convert("RGB")
    image.save(path, format="JPEG", quality=90, optimize=True, progressive=True)


def generate() -> tuple[str, Path, Path]:
    df = load_margin_cache()
    if df.empty:
        raise RuntimeError("Missing margin cache; run update_margin_maintenance.py first")
    snapshot = make_snapshot(df)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = SUMMARY_DIR / "margin_maintenance_latest.jpg"
    dated_path = SUMMARY_DIR / f"margin_maintenance_{snapshot.date}.jpg"
    with sync_playwright() as playwright:
        launch_args = {"headless": True}
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if browser_path:
            launch_args["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": LINE_IMAGE_WIDTH, "height": 1200}, device_scale_factor=1)
        page.set_content(render_html(df), wait_until="load")
        page.screenshot(path=str(latest_path), type="jpeg", quality=90, full_page=True)
        browser.close()
    _compress(latest_path)
    shutil.copyfile(latest_path, dated_path)
    print(f"Saved {latest_path.relative_to(ROOT_DIR)}")
    print(f"Saved {dated_path.relative_to(ROOT_DIR)}")
    return snapshot.date, latest_path, dated_path


if __name__ == "__main__":
    try:
        generate()
    except Exception as exc:
        print(f"generate_margin_maintenance_summary failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
