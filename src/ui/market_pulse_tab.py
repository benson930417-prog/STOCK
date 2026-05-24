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
    """TAIEX multi-window check — price percentile AND stretch from that
    window's own moving average. Stretch matters because in a strong
    trending market, every day is at percentile 100 — that alone is not
    bubble, it's just an uptrend. Bubble = high percentile + parabolic
    distance from longer-term mean."""
    st.markdown("### 🌡️ TAIEX 多視窗檢查")
    st.caption(
        "**分位數**：當前價格在該視窗內的位置（趨勢市中經常 100，本身不算過熱）。　"
        "**距視窗均線**：與該視窗均線的距離，反映「離趨勢有多遠」。　"
        "兩個都偏高才是真正過熱（不只是處於上升趨勢）。"
    )

    if taiex is None or len(taiex) < 60:
        st.info("TAIEX 資料不足。")
        return

    current = float(taiex.iloc[-1])
    windows = [("60 日 (~3M)", 60), ("1 年 (252d)", 252), ("2 年 (504d)", 504)]
    rows: list[dict] = []
    for label, lb in windows:
        win = taiex.iloc[-lb:] if len(taiex) >= lb else taiex
        pct = _percentile_in_window(taiex, lb)
        win_ma = float(win.mean())
        stretch = (current - win_ma) / win_ma * 100.0 if win_ma > 0 else None
        tag, severity = _composite_judgment(pct, stretch)
        rows.append({
            "視窗":      label,
            "分位數":    pct,
            "距視窗均線": stretch,
            "綜合判斷":  tag,
            "_sort":     severity,
        })

    df_show = pd.DataFrame(rows).drop(columns="_sort")

    def _color_pct(v):
        if v is None or pd.isna(v): return ""
        if v >= 95: return "color: #dc2626; font-weight: 700"
        if v >= 80: return "color: #f97316"
        if v >= 60: return "color: #facc15"
        if v <  20: return "color: #1e40af"
        return ""

    def _color_stretch(v):
        if v is None or pd.isna(v): return ""
        if v >=  25: return "background-color: rgba(220, 38, 38, 0.25); font-weight: 700"
        if v >=  15: return "background-color: rgba(249, 115, 22, 0.20)"
        if v >=   5: return "background-color: rgba(250, 204, 21, 0.15)"
        if v <= -15: return "background-color: rgba(30, 64, 175, 0.20); color: #93c5fd"
        if v <=  -5: return "background-color: rgba(14, 165, 233, 0.15)"
        return ""

    styled = (
        df_show.style
        .format({
            "分位數":     lambda v: f"{v:.0f}"  if pd.notna(v) else "—",
            "距視窗均線": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
        })
        .map(_color_pct,     subset=["分位數"])
        .map(_color_stretch, subset=["距視窗均線"])
    )
    st.dataframe(styled, hide_index=True, width="stretch")


def _composite_judgment(pct: float | None, stretch: float | None) -> tuple[str, str]:
    """Combine 1y percentile with MA200 stretch into a meaningful overheat tag.

    Percentile alone is misleading in a trending market — every day of a
    steady uptrend reads as 100%. Pairing it with MA200 stretch
    distinguishes "trending up healthily" from "parabolic / bubble."

    Returns (tag, severity_for_sort) where lower severity = more overheated.
    """
    if pct is None or stretch is None:
        return ("— 無資料", "9")
    if pct >= 90 and stretch >= 25:
        return ("🚨 嚴重過熱", "0")
    if pct >= 90 and stretch >= 15:
        return ("⚠ 偏熱",     "1")
    if pct >= 90 and stretch >= 5:
        return ("🟡 溫和上揚", "2")
    if pct >= 80:
        return ("🟢 接近趨勢", "3")
    if pct >= 40:
        return ("⚪ 中性",     "4")
    if pct >= 20 and stretch <= -5:
        return ("🔵 偏冷",     "5")
    if pct < 20 and stretch <= -15:
        return ("❄️ 嚴重低估", "6")
    return ("⚪ 中性",        "4")


def _render_cross_asset(lookback_days: int = 252) -> None:
    """Cross-asset overheat panel — TWO metrics so percentile-in-a-trend
    doesn't fool you. Price percentile says "where in range." MA200 stretch
    says "how far from trend." Both needed to identify real overheat."""
    st.markdown("### 🌍 跨資產過熱檢查")
    st.caption(
        "**分位數** = 當前價格在 1 年區間的位置（高 = 接近年內高點）。　"
        "**距 MA200** = 與 200 日均線的距離（>+25% = 拋物線型過熱）。　"
        "**單看分位數會在趨勢市中失真**：S&P 平穩走多時每天都是 100%，但離 MA200 只有 +10% "
        "就不算泡沫；TAIEX 100% 但離 MA200 +40% 才是真的高位。"
    )

    rows: list[dict] = []
    for ticker, name in CROSS_ASSET_INDICES:
        df = db.get_prices(ticker)
        if df.empty or len(df) < 200:
            rows.append({
                "指數": f"{name} ({ticker})",
                "1年分位": None, "距 MA200": None,
                "綜合判斷": "— 無資料", "_sort": "9",
            })
            continue
        prices = df["close"].dropna()
        pct = _percentile_in_window(prices, lookback_days)
        cur = float(prices.iloc[-1])
        ma200 = float(prices.iloc[-200:].mean())
        stretch = (cur - ma200) / ma200 * 100.0
        tag, severity = _composite_judgment(pct, stretch)
        rows.append({
            "指數": f"{name} ({ticker})",
            "1年分位": pct, "距 MA200": stretch,
            "綜合判斷": tag, "_sort": severity,
        })

    df_show = pd.DataFrame(rows).sort_values("_sort").drop(columns="_sort")

    def _color_pct(v):
        if v is None or pd.isna(v): return ""
        if v >= 95: return "color: #dc2626; font-weight: 700"
        if v >= 80: return "color: #f97316"
        if v >= 60: return "color: #facc15"
        if v <  20: return "color: #1e40af"
        return ""

    def _color_stretch(v):
        if v is None or pd.isna(v): return ""
        if v >=  25: return "background-color: rgba(220, 38, 38, 0.25); font-weight: 700"
        if v >=  15: return "background-color: rgba(249, 115, 22, 0.20)"
        if v >=   5: return "background-color: rgba(250, 204, 21, 0.15)"
        if v <= -15: return "background-color: rgba(30, 64, 175, 0.20); color: #93c5fd"
        if v <=  -5: return "background-color: rgba(14, 165, 233, 0.15)"
        return ""

    styled = (
        df_show.style
        .format({
            "1年分位": lambda v: f"{v:.0f}"  if pd.notna(v) else "—",
            "距 MA200": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
        })
        .map(_color_pct,     subset=["1年分位"])
        .map(_color_stretch, subset=["距 MA200"])
    )
    st.dataframe(styled, hide_index=True, width="stretch")

    # Composite alert — only fires on TRUE overheat (high pct + high stretch),
    # not on "trending up but normal stretch"
    n_severe = sum(1 for r in rows if r["_sort"] == "0")
    n_hot    = sum(1 for r in rows if r["_sort"] in ("0", "1"))
    if n_severe >= 2:
        st.error(
            f"🚨 {n_severe} 個指數同時嚴重過熱（>+25% above MA200 且 90+ 分位）— "
            "**真正的全球高位警示**，不是普通上升趨勢。歷史上這種組合常領先重大修正。"
        )
    elif n_hot >= 2:
        st.warning(
            f"⚠ {n_hot} 個指數偏熱。注意是否惡化為嚴重過熱（距 MA200 >25%）。"
        )
    elif n_hot == 1:
        st.info(f"ℹ️ {n_hot} 個指數偏熱，其他大致正常 — 局部市場現象。")


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
