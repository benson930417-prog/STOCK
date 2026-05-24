"""市場脈動 tab — pure market-state dashboard.

Design after code-review refactor (06-2026)
───────────────────────────────────────────
Earlier versions had 6+ overlapping panels that all measured the same
underlying trend through different lenses (percentile, MA stretch, 30d
return, 60d return, acceleration, distance from high). Clustered together
they created false confidence — the eye reads five red bars as five
independent confirmations when they're really one signal in costume.

This version groups metrics by what they ACTUALLY MEASURE, normalises
cross-asset comparisons, and ends with a single plain-text interpretation
instead of stacked emoji alerts.

Sections
────────
    1. 市場水平 Market Level
       Headline: current price, day change, ZigZag regime, distance from
       1y high, distance from 60d low. No percentile (covered in §2).

    2. 趨勢拉伸 Stretch (normalised across assets)
       Each index's MA200 distance + its OWN 2y z-score of historical
       stretches. Apples-to-apples: SOX +59% might be only z=1.2 (normal
       for SOX), TAIEX +40% might be z=2.3 (unusual for TAIEX). Includes
       breadth tally — how many indices are simultaneously stretched.

    3. 動能與波動 Speed & Volatility
       Single combined card with 30d return, 60d return, acceleration,
       and 20d realised volatility. One composite tag, not four.

    4. TAIEX 2y chart with ZigZag regime overlay (today marked).

    5. 📋 綜合解讀 Summary card
       Plain text combining all four section signals into one neutral
       interpretation. No 🚨, no "warning" language — this page is
       context, not call.

Philosophy unchanged
────────────────────
Tactical backtests (strategy_experiment/) proved no rule combo beats DCA
or static rebalance on Sharpe across 5y of data. So this tab gives no
buy/sell signals — only context to suppress emotional trading decisions.
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


CROSS_ASSET_INDICES: list[tuple[str, str]] = [
    ("^TWII", "加權指數"),
    ("^SOX",  "費城半導體"),
    ("^IXIC", "NASDAQ"),
    ("^GSPC", "S&P 500"),
    ("^DJI",  "道瓊"),
]


# ─── helpers ──────────────────────────────────────────────────────────────
def _format_distance_from_high(dist_pct: float | None) -> str:
    """Distance-from-high is naturally ≤ 0. Render 0 as 'at the high' to
    avoid the semantically-odd '+0.0%' display."""
    if dist_pct is None:
        return "—"
    if dist_pct >= -0.05:    # essentially at the high
        return "持平高點"
    return f"低 {abs(dist_pct):.1f}%"


def _format_distance_from_low(dist_pct: float | None) -> str:
    if dist_pct is None:
        return "—"
    if dist_pct <= 0.05:
        return "持平低點"
    return f"高 {dist_pct:.1f}%"


def _stretch_zscore(prices: pd.Series,
                    ma_window: int = 200,
                    lookback: int = 504) -> tuple[float | None, float | None]:
    """Returns (current_stretch_pct, z_score_of_current_stretch_vs_history).

    Normalises by the asset's OWN historical stretch distribution so SOX
    (naturally high-vol) and DJI (low-vol) live on the same emotional
    scale. A z of +2 means "current stretch is 2 standard deviations
    above this asset's typical stretch" — genuinely unusual regardless
    of the raw percentage.
    """
    if prices is None or len(prices) < ma_window + 30:
        return None, None
    ma     = prices.rolling(ma_window).mean()
    stretch = (prices - ma) / ma * 100.0
    cur_stretch = float(stretch.iloc[-1])

    hist = stretch.iloc[-lookback:].dropna() if len(stretch) > lookback else stretch.dropna()
    if len(hist) < 30:
        return cur_stretch, None
    mean = float(hist.mean())
    std  = float(hist.std())
    if std <= 0:
        return cur_stretch, None
    return cur_stretch, (cur_stretch - mean) / std


def _zscore_label(z: float | None) -> str:
    """Soft descriptive label — no emoji, no alarm."""
    if z is None: return "—"
    if z >=  2.0: return "高位"
    if z >=  1.0: return "偏高"
    if z >= -1.0: return "中性"
    if z >= -2.0: return "偏低"
    return "低位"


def _zscore_style(z: float | None) -> str:
    """Subtle pandas Styler color — softer than the old red/green walls."""
    if z is None or pd.isna(z): return ""
    if z >=  2.0: return "background-color: rgba(220,38,38,0.18); font-weight: 600"
    if z >=  1.0: return "background-color: rgba(249,115,22,0.13)"
    if z >= -1.0: return ""
    if z >= -2.0: return "background-color: rgba(14,165,233,0.12)"
    return "background-color: rgba(30,64,175,0.18)"


def _rolling_vol(prices: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility from daily returns."""
    rets = prices.pct_change().dropna()
    return rets.rolling(window).std() * (252 ** 0.5) * 100.0


# ─── composite tags ────────────────────────────────────────────────────────
def _momentum_composite(ret_30: float, ret_60: float, accel: float) -> tuple[str, str]:
    """Collapse three momentum numbers into one descriptive tag.

    Returns (tag, color_hex). Tone is descriptive, not alarming —
    'high speed + accelerating' instead of '🚨 parabolic warning'.
    """
    if ret_30 > 8 and accel > 2:
        return ("高速且加速", "#dc2626")
    if ret_30 > 8 and accel < -2:
        return ("高速但減速",  "#f97316")
    if ret_30 > 8:
        return ("穩定高速",   "#facc15")
    if ret_30 < -8 and accel < -5:
        return ("急跌且加速",  "#dc2626")
    if ret_30 < -8 and accel > 2:
        return ("急跌但減速",  "#0ea5e9")
    if ret_30 < -8:
        return ("穩定下行",   "#0ea5e9")
    if accel > 5:
        return ("加速中",      "#f97316")
    if accel < -5:
        return ("減速中",      "#0ea5e9")
    return ("穩定",            "#22c55e")


# ─── rendering blocks ─────────────────────────────────────────────────────
def _render_headline(taiex: pd.Series) -> None:
    """Market Level — current price, regime, distance from recent extremes."""
    if taiex is None or len(taiex) < 2:
        st.warning("TAIEX 資料不足，無法顯示。")
        return

    current = float(taiex.iloc[-1])
    prev    = float(taiex.iloc[-2])
    day_pct = (current - prev) / prev * 100.0

    regimes_df = _compute_regimes_live(threshold_pct=4.0)
    cur_regime_label = "—"
    cur_regime_days  = None
    if not regimes_df.empty:
        today_ts = taiex.index[-1]
        ongoing = regimes_df[
            (regimes_df["start_date"] <= today_ts) & (regimes_df["end_date"] >= today_ts)
        ]
        leg = ongoing.iloc[-1] if not ongoing.empty else regimes_df.iloc[-1]
        cur_regime_label = REGIME_LABELS_ZH.get(leg["regime"], leg["regime"])
        cur_regime_days  = (today_ts - pd.Timestamp(leg["start_date"])).days

    h_252 = float(taiex.iloc[-252:].max()) if len(taiex) >= 252 else float(taiex.max())
    l_60  = float(taiex.iloc[-60:].min())  if len(taiex) >=  60 else float(taiex.min())
    dist_1y_hi  = (current - h_252) / h_252 * 100.0 if h_252 > 0 else None
    dist_60_lo  = (current - l_60)  / l_60  * 100.0 if l_60  > 0 else None

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
        "距 1 年高點",
        _format_distance_from_high(dist_1y_hi),
        delta_color="off",
    )
    c4.metric(
        "距 60 日低點",
        _format_distance_from_low(dist_60_lo),
        delta_color="off",
    )


def _breadth_narrative(stretched_names: list[str],
                       neutral_names:  list[str],
                       compressed_names: list[str]) -> str:
    """Build a fully data-driven breadth sentence from the actual index
    names — no hardcoded "TAIEX/SOX". If those cool down and越南 heats up,
    the sentence updates itself."""
    total = len(stretched_names) + len(neutral_names) + len(compressed_names)
    n_stretched = len(stretched_names)
    n_compressed = len(compressed_names)

    def _join(names: list[str]) -> str:
        return "、".join(names)

    if total == 0:
        return "無可用資料。"

    # Compression cases (low z-score — rare in bull, but cover for completeness)
    if n_compressed >= 2 and n_stretched == 0:
        return (f"**{_join(compressed_names)}** 處於自身歷史低位區間；"
                f"其餘 {total - n_compressed} 個指數中性 — 全球大盤偏冷。")

    # Stretch cases — the common ones
    if n_stretched == 0:
        return f"{total} 個指數全部位於中性區間 — 全球大盤未過熱。"
    if n_stretched == total:
        return f"{total} 個指數同步拉伸 — 全面過熱，無一倖免。"
    if n_stretched == 1:
        return (f"**僅 {_join(stretched_names)}** 處於自身歷史高位區間；"
                f"其餘 {total - 1} 個指數仍中性 — **局部現象，非全球同步**。")
    if n_stretched <= total // 2:
        return (f"**熱點集中於 {_join(stretched_names)}**；"
                f"**{_join(neutral_names)}** 仍在中性區間 — "
                f"**全球大盤尚未全面過熱**。")
    return (f"**{_join(stretched_names)}** 已拉伸；"
            f"僅 {_join(neutral_names) or '少數'} 仍中性 — 接近全面偏熱。")


def _render_stretch_normalized() -> dict:
    """Cross-asset stretch table normalised by each asset's own 2y history.

    Returns a dict summary {n_stretched, n_compressed, n_total} so the
    summary card downstream can build a breadth statement without
    re-running the math.
    """
    st.markdown("### 🌍 趨勢拉伸（跨資產，自身分布標準化）")
    st.caption(
        "**距 MA200** = 與 200 日均線的距離。"
        "**z-score** = 該距離在自身過去 2 年分布中的標準差位置。"
        "因為 SOX 天生比道瓊波動大，比較原始百分比不公平 — "
        "z-score 把它們放在同一個量尺：**>+2 = 該資產自己歷史上的高位**。"
    )

    rows: list[dict] = []
    stretched_names:  list[str] = []   # z >= +1.5
    neutral_names:    list[str] = []   # -1.5 < z < +1.5
    compressed_names: list[str] = []   # z <= -1.5
    for ticker, name in CROSS_ASSET_INDICES:
        df = db.get_prices(ticker)
        if df.empty:
            rows.append({"指數": f"{name} ({ticker})",
                         "距 MA200": None, "z-score": None, "判斷": "—"})
            continue
        prices = df.set_index("date")["close"].dropna()
        stretch, z = _stretch_zscore(prices, ma_window=200, lookback=504)
        rows.append({
            "指數":     f"{name} ({ticker})",
            "距 MA200": stretch,
            "z-score":  z,
            "判斷":     _zscore_label(z),
        })
        if z is None:
            continue
        if z >=  1.5:
            stretched_names.append(name)
        elif z <= -1.5:
            compressed_names.append(name)
        else:
            neutral_names.append(name)

    df_show = pd.DataFrame(rows)
    # Sort by z-score desc so most-stretched float to the top; nones at bottom
    df_show["_sort"] = df_show["z-score"].fillna(-9999)
    df_show = df_show.sort_values("_sort", ascending=False).drop(columns="_sort")

    styled = (
        df_show.style
        .format({
            "距 MA200": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "z-score":  lambda v: f"{v:+.2f}"  if pd.notna(v) else "—",
        })
        .map(_zscore_style, subset=["z-score"])
    )
    st.dataframe(styled, hide_index=True, width="stretch")

    # Breadth narrative — fully data-driven from the actual stretched names
    narrative = _breadth_narrative(stretched_names, neutral_names, compressed_names)
    st.markdown(f"📊 **廣度**：{narrative}")

    return {
        "n_stretched":      len(stretched_names),
        "n_compressed":     len(compressed_names),
        "n_total":          len(stretched_names) + len(neutral_names) + len(compressed_names),
        "stretched_names":  stretched_names,
        "neutral_names":    neutral_names,
        "compressed_names": compressed_names,
        "rows":             rows,
    }


def _render_speed_panel(taiex: pd.Series) -> dict:
    """Single combined card: 30d return, 60d return, acceleration, 20d vol.

    Returns a dict summary so the summary card can use it.
    """
    st.markdown("### 🚀 動能與波動（TAIEX）")
    st.caption(
        "**動能**：最近 N 天的累積報酬。**加速度** = 近 30 天比前 30 天快多少。"
        "**20 日波動率**：年化的最近 20 天標準差。"
        "三者組成一張動能卡片 — 同一個底層趨勢的三個面向，給你一個綜合標籤。"
    )

    if taiex is None or len(taiex) < 61:
        st.info("資料不足計算動能（需 60 個交易日以上）。")
        return {}

    current  = float(taiex.iloc[-1])
    p_30     = float(taiex.iloc[-31])
    p_60     = float(taiex.iloc[-61])
    ret_30   = (current / p_30  - 1) * 100
    ret_60   = (current / p_60  - 1) * 100
    ret_prior_30 = (p_30 / p_60 - 1) * 100
    accel    = ret_30 - ret_prior_30

    vol_series = _rolling_vol(taiex, 20)
    cur_vol = vol_pct = None
    if len(vol_series.dropna()) >= 30:
        cur_vol = float(vol_series.iloc[-1])
        vol_window = vol_series.dropna().iloc[-252:] if len(vol_series.dropna()) > 252 else vol_series.dropna()
        vol_pct = float((vol_window <= cur_vol).sum()) / len(vol_window) * 100

    tag, color = _momentum_composite(ret_30, ret_60, accel)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("30 日報酬",  f"{ret_30:+.2f}%",  delta_color="off")
    c2.metric("60 日報酬",  f"{ret_60:+.2f}%",  delta_color="off")
    c3.metric("加速度",     f"{accel:+.2f}%",   "近30天 vs 前30天", delta_color="off")
    if cur_vol is not None:
        c4.metric("20 日年化波動",
                  f"{cur_vol:.1f}%",
                  f"1年內 {vol_pct:.0f} 分位",
                  delta_color="off")
    else:
        c4.metric("20 日年化波動", "—", delta_color="off")

    # Single composite tag — replaces the four separate emoji alarms
    st.markdown(
        f"<div style='margin-top:0.5rem; padding:0.6rem 1rem; "
        f"border-left:4px solid {color}; background:rgba(255,255,255,0.03); "
        f"border-radius:4px;'>"
        f"<span style='color:#aaa; font-size:0.9rem'>綜合動能標籤：</span>"
        f"<span style='color:{color}; font-weight:600; font-size:1.1rem'>{tag}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    return {"ret_30": ret_30, "ret_60": ret_60, "accel": accel,
            "vol": cur_vol, "vol_pct": vol_pct, "tag": tag}


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


def _render_summary_card(taiex: pd.Series,
                         stretch_info: dict,
                         speed_info: dict) -> None:
    """Single plain-text interpretation combining all section signals.

    No alarms, no 🚨 — descriptive only. The page deliberately stops
    short of saying "buy" or "sell"; it leaves that to the reader.
    """
    st.markdown("### 📋 綜合解讀")

    # Pull TAIEX's own z-score out of the stretch table
    tw_z = None
    tw_stretch = None
    for r in stretch_info.get("rows", []):
        if "TWII" in r["指數"]:
            tw_z = r["z-score"]
            tw_stretch = r["距 MA200"]
            break

    # Compose state line + interpretation
    parts: list[str] = []

    # Level descriptor
    h_252 = float(taiex.iloc[-252:].max()) if len(taiex) >= 252 else float(taiex.max())
    cur   = float(taiex.iloc[-1])
    dist_1y = (cur - h_252) / h_252 * 100 if h_252 > 0 else 0
    if dist_1y >= -0.5:
        parts.append("接近 1 年高點")
    elif dist_1y >= -5:
        parts.append("位於 1 年高位附近")
    elif dist_1y >= -15:
        parts.append("距 1 年高點有距離")
    else:
        parts.append("遠離 1 年高點")

    # Speed descriptor — pull from the composite tag
    tag = speed_info.get("tag")
    if tag:
        parts.append(tag.lower())

    # TAIEX stretch descriptor
    if tw_z is not None:
        if tw_z >= 2:
            parts.append(f"TAIEX 拉伸 z={tw_z:+.1f}（自身高位）")
        elif tw_z >= 1:
            parts.append(f"TAIEX 拉伸 z={tw_z:+.1f}（自身偏高）")
        elif tw_z >= -1:
            parts.append(f"TAIEX 拉伸 z={tw_z:+.1f}（中性）")
        else:
            parts.append(f"TAIEX 拉伸 z={tw_z:+.1f}（偏低）")

    # Breadth descriptor — use the same data-driven names as the section above
    stretched = stretch_info.get("stretched_names", [])
    neutral   = stretch_info.get("neutral_names", [])
    ns = stretch_info.get("n_stretched", 0)
    nt = stretch_info.get("n_total", 0)
    if ns == 0:
        parts.append(f"全球 {nt} 指數普遍正常")
    elif ns == nt:
        parts.append("全球指數全面拉伸")
    elif ns == 1:
        parts.append(f"僅 {stretched[0]} 拉伸（局部）")
    elif ns <= nt // 2:
        parts.append(f"熱點集中於 {'、'.join(stretched)}")
    else:
        parts.append(f"{'、'.join(stretched)} 多市場同步拉伸")

    state_line = "**市場狀態**：" + "　·　".join(parts)

    # Interpretation paragraph — graded based on how many signals align
    accel = speed_info.get("accel", 0) or 0
    high_stretch    = tw_z is not None and tw_z >=  1.5
    extreme_stretch = tw_z is not None and tw_z >=  2.0
    low_stretch     = tw_z is not None and tw_z <= -1.5
    fast            = accel >  2
    declining_fast  = accel < -5
    breadth_ok      = ns >= 2

    if extreme_stretch and fast and breadth_ok:
        interp = (
            "**解讀**：拉伸高位 + 加速上漲 + 多市場同步三者俱備。"
            "歷史上類似情境的拉回風險顯著升高，但**並非單一賣出訊號**，"
            "拋物線階段通常仍可維持數週至數月。"
        )
    elif (extreme_stretch and fast) or (high_stretch and fast and breadth_ok):
        interp = (
            "**解讀**：拉伸與動能多項偏熱（廣度亦有支撐）。"
            "拉回風險偏高，但**並非單一賣出訊號**。"
        )
    elif high_stretch and breadth_ok:
        interp = (
            "**解讀**：TAIEX 偏高且多市場同步拉伸，"
            "但動能未顯著加速。屬於成熟趨勢階段，**非賣出訊號**。"
        )
    elif high_stretch:
        interp = (
            "**解讀**：TAIEX 處於自身歷史偏高位，但**廣度未確認全球同步**，"
            "可能為單一市場現象。**非賣出訊號**。"
        )
    elif low_stretch and declining_fast:
        interp = (
            "**解讀**：拉伸低位且仍在加速下行，趨勢尚未止穩。"
            "歷史上逢低布局時機通常出現在減速之後，**非立即買入訊號**。"
        )
    elif low_stretch:
        interp = (
            "**解讀**：TAIEX 處於自身歷史低位，逢低布局的歷史回報通常較高，"
            "但**並非單一買入訊號**——趨勢方向仍需個別判斷。"
        )
    else:
        interp = "**解讀**：當前各項指標處於中性區間，無特別偏多或偏空跡象。"

    with st.container(border=True):
        st.markdown(state_line)
        st.markdown(interp)
        st.caption(
            "本頁所有分析皆為**歷史資料的客觀描述**，不構成投資建議。"
            "回測證明戰術擇時規則在此市場史料中無法跑贏 DCA 或固定再平衡。"
        )


# ─── main entry ──────────────────────────────────────────────────────────
def render_market_pulse_tab(*, lang=None, T=None, DATA_DIR=None, **kwargs) -> None:
    st.subheader("📊 市場脈動")
    st.caption(
        "即時市場狀態儀表板 — **給你資料，由你判斷**。"
        "四個區塊分別測量**水平 / 拉伸 / 速度 / 廣度**，"
        "最後以一段中性文字綜合解讀。本頁不提供買賣訊號。"
    )

    taiex_df = db.get_prices("^TWII")
    if taiex_df.empty:
        st.error("資料庫無 TAIEX (^TWII) 價格。請先執行 step3_backfill。")
        return
    taiex = taiex_df.set_index("date")["close"].dropna()

    # 1. Market Level
    _render_headline(taiex)
    st.markdown("---")

    # 2. Stretch (normalised) + breadth tally
    stretch_info = _render_stretch_normalized()
    st.markdown("---")

    # 3. Speed (momentum + volatility, combined card)
    speed_info = _render_speed_panel(taiex)
    st.markdown("---")

    # 4. Chart with regime overlay
    _render_chart_with_regimes(taiex)
    st.markdown("---")

    # 5. Summary card — combines all section signals into plain text
    _render_summary_card(taiex, stretch_info, speed_info)
