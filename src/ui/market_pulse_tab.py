"""市場脈動 tab — pure market-state dashboard. No buy/sell signals.

Philosophy
──────────
After running 1080-config tactical backtests across 2y and 5y windows
on multiple core ETFs, we found tactical timing rules add NO Sharpe
alpha vs simple DCA or static rebalancing. So this tab does not pretend
to give signals.

It gives you OBJECTIVE DATA about where the market sits right now:
    • TAIEX percentile in 60d / 1y / 2y windows
    • Cross-asset overheat panel (TAIEX + SOX + NASDAQ + S&P 500)
    • Distance from MA20 / MA50 / MA200 + recent extremes
    • Realised volatility regime
    • Chart with regime overlay marking today

You make the call. The dashboard surfaces the questions; it doesn't
answer them.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scripts.etf_benchmark import db
from src.ui.etf_compare_tab import (
    REGIME_COLORS,
    REGIME_LABELS_ZH,
    _compute_regimes_live,
)

# Indices that show up in the cross-asset overheat panel. All present in
# the etf_bench DB via step3_backfill REFERENCE_INDICES.
CROSS_ASSET_INDICES: list[tuple[str, str]] = [
    ("^TWII", "加權指數"),
    ("^SOX",  "費城半導體"),
    ("^IXIC", "NASDAQ"),
    ("^GSPC", "S&P 500"),
    ("^DJI",  "道瓊"),
]


# ─── helpers ──────────────────────────────────────────────────────────────
def _percentile_in_window(prices: pd.Series, lookback: int) -> float | None:
    """Where does the latest price sit in the last `lookback` days?
    0 = lowest in window, 100 = highest."""
    if prices is None or len(prices) < 2:
        return None
    window = prices.iloc[-lookback:] if len(prices) > lookback else prices
    current = float(prices.iloc[-1])
    return float((window <= current).sum()) / len(window) * 100.0


def _heat_color(pct: float | None) -> str:
    """Cold-to-hot color for percentile values."""
    if pct is None:
        return "rgba(150,150,150,0.5)"
    if pct >= 95: return "#dc2626"   # extreme heat (red)
    if pct >= 80: return "#f97316"   # hot (orange)
    if pct >= 60: return "#facc15"   # warm (yellow)
    if pct >= 40: return "#22c55e"   # neutral (green)
    if pct >= 20: return "#0ea5e9"   # cool (light blue)
    return "#1e40af"                 # cold (deep blue)


def _heat_label(pct: float | None) -> str:
    if pct is None:    return "—"
    if pct >= 95:      return "🚨 極熱"
    if pct >= 80:      return "⚠ 偏熱"
    if pct >= 60:      return "🟡 偏高"
    if pct >= 40:      return "🟢 中性"
    if pct >= 20:      return "🔵 偏低"
    return "❄️ 極冷"


def _pct_distance(price_now: float, reference: float) -> float | None:
    if reference is None or reference <= 0:
        return None
    return (price_now - reference) / reference * 100.0


def _rolling_vol(prices: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility from daily log returns."""
    rets = prices.pct_change().dropna()
    return rets.rolling(window).std() * (252 ** 0.5) * 100.0


# ─── rendering blocks ─────────────────────────────────────────────────────
def _render_headline(taiex: pd.Series) -> None:
    if taiex is None or len(taiex) < 2:
        st.warning("TAIEX 資料不足，無法顯示。")
        return

    current = float(taiex.iloc[-1])
    prev    = float(taiex.iloc[-2])
    day_pct = (current - prev) / prev * 100.0

    # Regime context — reuse the same ZigZag engine the compare tab uses
    regimes_df = _compute_regimes_live(threshold_pct=4.0)
    cur_regime_label = "—"
    cur_regime_days  = None
    if not regimes_df.empty:
        today_ts = taiex.index[-1]
        # Find the leg that contains today (or the latest leg if open)
        ongoing = regimes_df[
            (regimes_df["start_date"] <= today_ts) & (regimes_df["end_date"] >= today_ts)
        ]
        leg = ongoing.iloc[-1] if not ongoing.empty else regimes_df.iloc[-1]
        cur_regime_label = REGIME_LABELS_ZH.get(leg["regime"], leg["regime"])
        cur_regime_days  = (today_ts - pd.Timestamp(leg["start_date"])).days

    # Distance from 60d / 1y high
    h_60d  = float(taiex.iloc[-60:].max()) if len(taiex) >= 60  else float(taiex.max())
    h_252  = float(taiex.iloc[-252:].max()) if len(taiex) >= 252 else float(taiex.max())
    dist_60_hi  = _pct_distance(current, h_60d)
    dist_252_hi = _pct_distance(current, h_252)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "加權指數 (TAIEX)",
        f"{current:,.2f}",
        f"{day_pct:+.2f}%",
        delta_color="normal" if day_pct >= 0 else "inverse",
    )
    c2.metric(
        "當前規制 (ZigZag 4%)",
        cur_regime_label,
        f"已 {cur_regime_days} 天" if cur_regime_days is not None else "—",
        delta_color="off",
    )
    c3.metric(
        "距 60 日高點",
        f"{dist_60_hi:+.1f}%" if dist_60_hi is not None else "—",
        delta_color="off",
    )
    c4.metric(
        "距 1 年高點",
        f"{dist_252_hi:+.1f}%" if dist_252_hi is not None else "—",
        delta_color="off",
    )


def _render_taiex_timeframes(taiex: pd.Series) -> None:
    """TAIEX percentile across multiple lookback windows — horizontal bars."""
    st.markdown("### 🌡️ TAIEX 在歷史區間的位置")
    st.caption("數字越高 = 越接近區間高點。看到 90+ 多個視窗都偏熱 → 進入歷史極端區。")

    windows = [("60 日 (~3M)", 60), ("1 年 (252d)", 252), ("2 年 (504d)", 504)]
    rows = []
    for label, lb in windows:
        pct = _percentile_in_window(taiex, lb)
        rows.append({
            "label": label,
            "pct": pct or 0.0,
            "color": _heat_color(pct),
            "tag": _heat_label(pct),
        })

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=[r["label"] for r in rows],
        x=[r["pct"]   for r in rows],
        marker=dict(color=[r["color"] for r in rows]),
        text=[f"{r['pct']:.0f}  {r['tag']}" for r in rows],
        textposition="outside",
        cliponaxis=False,
        orientation="h",
    ))
    fig.update_layout(
        xaxis=dict(range=[0, 110], showticklabels=True, tickformat=".0f",
                   ticksuffix=" pct", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(showgrid=False),
        height=200, margin=dict(l=10, r=80, t=10, b=10),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
    )
    # Reference lines at 20 and 80 (rough "extreme" markers)
    fig.add_vline(x=20, line=dict(color="rgba(14,165,233,0.4)",  width=1, dash="dot"))
    fig.add_vline(x=80, line=dict(color="rgba(249,115,22,0.4)",  width=1, dash="dot"))
    st.plotly_chart(fig, width="stretch")


def _render_cross_asset(lookback_days: int = 252) -> None:
    """Cross-asset overheat panel — same percentile math across global indices."""
    st.markdown("### 🌍 跨資產過熱檢查（1 年分位數）")
    st.caption("如果只有 TAIEX 偏熱 → 台股獨秀。多個指數同時 90+ → 全球都在歷史高位，"
               "典型「全球週期頂部」訊號。")

    rows = []
    for ticker, name in CROSS_ASSET_INDICES:
        df = db.get_prices(ticker)
        if df.empty:
            rows.append({"label": f"{name} ({ticker})", "pct": None,
                         "color": _heat_color(None), "tag": "無資料"})
            continue
        prices = df["close"].dropna()
        pct = _percentile_in_window(prices, lookback_days)
        rows.append({
            "label": f"{name} ({ticker})",
            "pct":   pct or 0.0,
            "color": _heat_color(pct),
            "tag":   _heat_label(pct),
        })

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=[r["label"] for r in rows],
        x=[r["pct"]   for r in rows],
        marker=dict(color=[r["color"] for r in rows]),
        text=[f"{r['pct']:.0f}  {r['tag']}" for r in rows],
        textposition="outside",
        cliponaxis=False,
        orientation="h",
    ))
    fig.update_layout(
        xaxis=dict(range=[0, 110], showticklabels=True, tickformat=".0f",
                   ticksuffix=" pct", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(showgrid=False, autorange="reversed"),
        height=220, margin=dict(l=10, r=80, t=10, b=10),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.add_vline(x=20, line=dict(color="rgba(14,165,233,0.4)",  width=1, dash="dot"))
    fig.add_vline(x=80, line=dict(color="rgba(249,115,22,0.4)",  width=1, dash="dot"))
    st.plotly_chart(fig, width="stretch")

    # Overheat tally — quick alert at the bottom
    n_extreme = sum(1 for r in rows if r["pct"] is not None and r["pct"] >= 90)
    if n_extreme >= 3:
        st.error(f"🚨 {n_extreme} 個指數同時在 90+ 分位 — 全球高位警示，"
                 "歷史上這種狀態通常領先重大修正。")
    elif n_extreme >= 1:
        st.warning(f"⚠ {n_extreme} 個指數在 90+ 分位。檢查是否為單一市場現象。")


def _render_technicals(taiex: pd.Series) -> None:
    """MA distance + recent extremes + volatility regime."""
    if taiex is None or len(taiex) < 200:
        st.info("MA 資料不足（需要至少 200 天）。")
        return

    current = float(taiex.iloc[-1])
    ma20  = float(taiex.iloc[-20:].mean())
    ma50  = float(taiex.iloc[-50:].mean())
    ma200 = float(taiex.iloc[-200:].mean())

    def _ma_status(price, ma):
        delta = (price - ma) / ma * 100
        if   delta >  10: tag, color = "🚨 嚴重乖離", "#dc2626"
        elif delta >   5: tag, color = "⚠ 偏離",   "#f97316"
        elif delta >  -5: tag, color = "🟢 接近",   "#22c55e"
        elif delta > -10: tag, color = "🔵 偏低",   "#0ea5e9"
        else:             tag, color = "❄️ 嚴重偏低", "#1e40af"
        return delta, tag, color

    rows_left = []
    for label, ma in [("MA20",  ma20), ("MA50",  ma50), ("MA200", ma200)]:
        d, tag, color = _ma_status(current, ma)
        rows_left.append((label, d, tag, color))

    # Volatility regime
    vol_series = _rolling_vol(taiex, 20)
    if len(vol_series.dropna()) >= 30:
        cur_vol = float(vol_series.iloc[-1])
        vol_window = vol_series.dropna().iloc[-252:] if len(vol_series.dropna()) > 252 else vol_series.dropna()
        vol_pct = float((vol_window <= cur_vol).sum()) / len(vol_window) * 100
    else:
        cur_vol = vol_pct = None

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🎯 距離技術指標")
        for label, d, tag, _color in rows_left:
            sign = "+" if d >= 0 else ""
            st.markdown(f"- **{label}**：`{sign}{d:.1f}%`　{tag}")
        st.caption("距離 200 日均線 >+15% 通常代表強趨勢但短線高位；"
                   "<-15% 則為強趨勢下的短線低位。")

    with c2:
        st.markdown("### 📈 波動率（20 日年化）")
        if cur_vol is None:
            st.info("資料不足計算波動率。")
        else:
            tag = _heat_label(vol_pct)
            st.markdown(f"- **當前波動率**：`{cur_vol:.1f}%`")
            st.markdown(f"- **位於 1 年範圍**：`{vol_pct:.0f} 分位`　{tag}")
            if vol_pct < 30:
                st.caption("⚠ 低波動 + 高分位 = 典型「複雜頂」訊號（市場集體掉以輕心）。")
            elif vol_pct > 70:
                st.caption("ℹ️ 高波動代表市場意見分歧，常見於趨勢轉折期。")
            else:
                st.caption("波動率處於中性區間。")


def _render_chart_with_regimes(taiex: pd.Series) -> None:
    st.markdown("### 📊 TAIEX 2 年走勢（含規制色塊）")
    regimes_df = _compute_regimes_live(threshold_pct=4.0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=taiex.index, y=taiex.values,
        mode="lines",
        line=dict(color="rgba(220,220,220,0.95)", width=1.6),
        name="加權指數",
        hovertemplate="%{x|%Y-%m-%d}: %{y:,.2f}<extra></extra>",
    ))

    # Regime overlays (same code path as compare tab — single ZigZag source of truth)
    for _, row in regimes_df.iterrows():
        s = pd.Timestamp(row["start_date"])
        e = pd.Timestamp(row["end_date"])
        color = REGIME_COLORS.get(row["regime"], "rgba(128,128,128,0.08)")
        label = REGIME_LABELS_ZH.get(row["regime"], row["regime"])
        show_label = (e - s).days >= 15
        fig.add_vrect(
            x0=s, x1=e, fillcolor=color, layer="below", line_width=0,
            annotation_text=label if show_label else "",
            annotation_position="top left",
            annotation_font=dict(size=11, color="rgba(240,240,240,0.95)"),
        )

    # Mark today's price prominently
    if len(taiex) > 0:
        last_x = taiex.index[-1]
        last_y = float(taiex.iloc[-1])
        fig.add_trace(go.Scatter(
            x=[last_x], y=[last_y],
            mode="markers+text",
            marker=dict(size=14, color="#fbbf24", line=dict(color="#000", width=2)),
            text=["今"], textposition="top center",
            textfont=dict(color="#fbbf24", size=14),
            showlegend=False,
            hovertemplate=f"今日：{last_x.date()}<br>%{{y:,.2f}}<extra></extra>",
        ))

    fig.update_layout(
        height=380, margin=dict(l=10, r=20, t=10, b=10),
        xaxis=dict(title="", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(title="加權指數", showgrid=True, gridcolor="rgba(255,255,255,0.06)"),
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


# ─── main entry ──────────────────────────────────────────────────────────
def render_market_pulse_tab(*, lang=None, T=None, DATA_DIR=None, **kwargs) -> None:
    st.subheader("📊 市場脈動")
    st.caption(
        "即時市場狀態儀表板 — **給你資料，由你判斷**。"
        "本頁刻意不提供買賣訊號（回測證明戰術規則無法跑贏 DCA），"
        "而是把當前市場相對歷史的位置擺給你看。"
    )

    # Pull TAIEX once (db cache handles repeats)
    taiex_df = db.get_prices("^TWII")
    if taiex_df.empty:
        st.error("資料庫無 TAIEX (^TWII) 價格。請先執行 step3_backfill。")
        return
    taiex = taiex_df.set_index("date")["close"].dropna()

    # 1. Top metric row
    _render_headline(taiex)

    st.markdown("---")

    # 2. TAIEX percentile across timeframes
    _render_taiex_timeframes(taiex)

    # 3. Cross-asset overheat
    _render_cross_asset()

    st.markdown("---")

    # 4. Technicals + Volatility
    _render_technicals(taiex)

    st.markdown("---")

    # 5. Big chart with regimes
    _render_chart_with_regimes(taiex)

    st.markdown("---")

    # Footer — restate the philosophy so you don't talk yourself into signals
    with st.container(border=True):
        st.markdown(
            "**🧊 冷血提醒**　"
            "本頁不告訴你「現在該買還是該賣」。它只告訴你**目前的客觀位置**。"
            "歷史回測顯示：在這個市場史料中，任何 fire/retrieve 規則都跑不贏 "
            "DCA 或固定再平衡。最佳策略仍是**定期定額**或**固定權重再平衡**。"
            "這個頁面的價值是讓你避免在 95+ 分位 FOMO 加碼，"
            "或在 5- 分位恐慌減碼——僅此而已。"
        )
