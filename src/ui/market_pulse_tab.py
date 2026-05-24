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


# ─── "lab report" health-metric helper ────────────────────────────────────
HEALTH_COLORS = {
    "red":         "#dc2626",   # 高位/急漲/急跌
    "orange":      "#f97316",   # 偏高/偏熱
    "yellow":      "#facc15",   # 注意/略偏
    "green":       "#22c55e",   # 正常
    "blue":        "#0ea5e9",   # 偏冷/減速
    "deep_blue":   "#1e40af",   # 低位/嚴重偏冷
    "gray":        "#9ca3af",   # 中性
}


def _render_health_metric(label: str, value: str, color: str,
                          status: str, ref_range: str) -> None:
    """Lab-report style metric block: colored value, 2-character tag, ref range.

    Layout (vertical, ~85px tall):
        Label              (gray, small)
        Value              (colored, large bold)
        ● Status           (colored, small)
        範圍：ref_range    (gray, tiny)

    Left border in the metric's color for instant glanceability.
    """
    st.markdown(
        f"""<div style="border-left:3px solid {color}; padding:0.55rem 0.85rem;
                       margin-bottom:0.4rem; background:rgba(255,255,255,0.025);
                       border-radius:3px;">
              <div style="color:#9ca3af; font-size:0.82rem; line-height:1.2">{label}</div>
              <div style="color:{color}; font-size:1.45rem; font-weight:650; line-height:1.25;
                          margin-top:0.15rem">{value}</div>
              <div style="color:{color}; font-size:0.85rem; margin-top:0.15rem">● {status}</div>
              <div style="color:#6b7280; font-size:0.72rem; margin-top:0.3rem">範圍：{ref_range}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def _section_insight(text: str) -> None:
    """End-of-section interpretation paragraph — the 'doctor's summary'."""
    st.markdown(
        f"""<div style="margin-top:0.5rem; padding:0.7rem 1rem;
                       border-left:3px solid #6366f1; background:rgba(99,102,241,0.06);
                       border-radius:3px; font-size:0.93rem; line-height:1.5">
              <span style="color:#a5b4fc; font-weight:600">💡 解讀：</span>{text}
            </div>""",
        unsafe_allow_html=True,
    )


# ─── per-metric classifiers (value → color, status tag, reference range) ──
def _classify_day_change(pct: float) -> tuple[str, str, str]:
    if pct >= 1.5:   return HEALTH_COLORS["red"],    "大漲", "日內 ±1.5% 為正常範圍"
    if pct >= 0.3:   return HEALTH_COLORS["green"],  "上漲", "日內 ±1.5% 為正常範圍"
    if pct > -0.3:   return HEALTH_COLORS["gray"],   "持平", "日內 ±1.5% 為正常範圍"
    if pct > -1.5:   return HEALTH_COLORS["yellow"], "下跌", "日內 ±1.5% 為正常範圍"
    return HEALTH_COLORS["red"], "大跌", "日內 ±1.5% 為正常範圍"


def _classify_regime(label_zh: str, days: int | None) -> tuple[str, str, str]:
    color_by_regime = {
        "多頭": (HEALTH_COLORS["green"],  "上升趨勢"),
        "小熊": (HEALTH_COLORS["yellow"], "短期修正"),
        "中熊": (HEALTH_COLORS["orange"], "中期回檔"),
        "大熊": (HEALTH_COLORS["red"],    "深度熊市"),
    }
    color, status = color_by_regime.get(label_zh, (HEALTH_COLORS["gray"], label_zh))
    return color, status, "多頭通常持續 50~200 個交易日"


def _classify_distance_from_high(dist_pct: float) -> tuple[str, str, str]:
    if dist_pct >= -0.5:   return HEALTH_COLORS["red"],    "持平高點", "與 1 年高點 -5%~-15% 屬正常波動"
    if dist_pct >= -5:     return HEALTH_COLORS["orange"], "高位區",   "與 1 年高點 -5%~-15% 屬正常波動"
    if dist_pct >= -15:    return HEALTH_COLORS["green"],  "正常回檔", "與 1 年高點 -5%~-15% 屬正常波動"
    if dist_pct >= -25:    return HEALTH_COLORS["yellow"], "深度回檔", "與 1 年高點 -5%~-15% 屬正常波動"
    return HEALTH_COLORS["blue"], "熊市區間", "與 1 年高點 -5%~-15% 屬正常波動"


def _classify_distance_from_low(dist_pct: float) -> tuple[str, str, str]:
    if dist_pct >= 30:     return HEALTH_COLORS["red"],    "急漲",   "近 60 日反彈 +10%~+25% 為健康範圍"
    if dist_pct >= 15:     return HEALTH_COLORS["orange"], "強漲",   "近 60 日反彈 +10%~+25% 為健康範圍"
    if dist_pct >= 5:      return HEALTH_COLORS["green"],  "穩健反彈", "近 60 日反彈 +10%~+25% 為健康範圍"
    if dist_pct >= 0:      return HEALTH_COLORS["blue"],   "築底中", "近 60 日反彈 +10%~+25% 為健康範圍"
    return HEALTH_COLORS["deep_blue"], "破底", "近 60 日反彈 +10%~+25% 為健康範圍"


def _classify_return(pct: float, window: str) -> tuple[str, str, str]:
    """Classify a multi-day return. window='30d' uses tighter thresholds, '60d' wider."""
    if window == "30d":
        ref = "正常 ±3% / 偏熱 >+8% / 急漲 >+15%"
        thresholds = [(15, "red", "急漲"), (8, "orange", "大漲"), (3, "green", "穩健上漲"),
                      (-3, "gray", "盤整"), (-8, "yellow", "下跌"), (-15, "red", "急跌")]
    else:  # 60d
        ref = "正常 ±6% / 偏熱 >+15% / 急漲 >+25%"
        thresholds = [(25, "red", "急漲"), (15, "orange", "大漲"), (6, "green", "穩健上漲"),
                      (-6, "gray", "盤整"), (-15, "yellow", "下跌"), (-25, "red", "急跌")]
    for thresh, color_key, tag in thresholds:
        if pct >= thresh:
            return HEALTH_COLORS[color_key], tag, ref
    return HEALTH_COLORS["red"], "重挫", ref


def _classify_acceleration(accel: float) -> tuple[str, str, str]:
    ref = "正常 ±2pp / 加速 >+5pp / 減速 <-5pp"
    if accel >=  5:  return HEALTH_COLORS["red"],       "加速中",  ref
    if accel >=  2:  return HEALTH_COLORS["orange"],    "略加速",  ref
    if accel >= -2:  return HEALTH_COLORS["green"],     "穩定",    ref
    if accel >= -5:  return HEALTH_COLORS["blue"],      "減速",    ref
    return HEALTH_COLORS["deep_blue"], "急轉", ref


def _classify_volatility(vol_pct: float, vol_percentile: float | None) -> tuple[str, str, str]:
    ref = "TAIEX 歷史中位 ~15% / 偏高 >25% / 低波動 <12%"
    if vol_pct >= 30:   return HEALTH_COLORS["red"],    "高波動",     ref
    if vol_pct >= 20:   return HEALTH_COLORS["orange"], "偏高",       ref
    if vol_pct >= 12:   return HEALTH_COLORS["green"],  "正常",       ref
    # Low vol — check percentile for "complacent top" warning
    if vol_percentile is not None and vol_percentile < 25:
        return HEALTH_COLORS["yellow"], "低波動（複雜頂風險）", ref
    return HEALTH_COLORS["blue"], "低波動", ref


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
def _render_headline(taiex: pd.Series) -> dict:
    """Market Level — colored lab-report metrics + end-of-section insight.
    Returns a dict so the summary card can reuse the computed values."""
    if taiex is None or len(taiex) < 2:
        st.warning("TAIEX 資料不足，無法顯示。")
        return {}

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
    dist_1y_hi = (current - h_252) / h_252 * 100.0 if h_252 > 0 else 0.0
    dist_60_lo = (current - l_60)  / l_60  * 100.0 if l_60  > 0 else 0.0

    # Per-metric classification
    c_day,    s_day,    r_day    = _classify_day_change(day_pct)
    c_reg,    s_reg,    r_reg    = _classify_regime(cur_regime_label, cur_regime_days)
    c_hi,     s_hi,     r_hi     = _classify_distance_from_high(dist_1y_hi)
    c_lo,     s_lo,     r_lo     = _classify_distance_from_low(dist_60_lo)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _render_health_metric(
            "加權指數 (TAIEX)",
            f"{current:,.0f}　<span style='font-size:1.0rem'>{day_pct:+.2f}%</span>",
            c_day, s_day, r_day,
        )
    with c2:
        days_str = f"已 {cur_regime_days} 天" if cur_regime_days is not None else "—"
        _render_health_metric(
            "當前規制 (ZigZag 4%)",
            f"{cur_regime_label}　<span style='font-size:1.0rem'>{days_str}</span>",
            c_reg, s_reg, r_reg,
        )
    with c3:
        _render_health_metric(
            "距 1 年高點",
            _format_distance_from_high(dist_1y_hi),
            c_hi, s_hi, r_hi,
        )
    with c4:
        _render_health_metric(
            "距 60 日低點",
            _format_distance_from_low(dist_60_lo),
            c_lo, s_lo, r_lo,
        )

    # End-of-section insight — synthesize what these 4 numbers mean together
    insight_parts = []
    if dist_1y_hi >= -0.5:
        insight_parts.append("TAIEX 正在創 1 年新高")
    elif dist_1y_hi >= -5:
        insight_parts.append(f"TAIEX 距 1 年高點僅 {abs(dist_1y_hi):.1f}%")
    elif dist_1y_hi <= -20:
        insight_parts.append(f"TAIEX 較高點回落 {abs(dist_1y_hi):.0f}%（已進入熊市區間）")
    if cur_regime_label == "多頭" and cur_regime_days and cur_regime_days >= 30:
        insight_parts.append(f"處於多頭第 {cur_regime_days} 天，趨勢延續中")
    elif cur_regime_label in ("中熊", "大熊"):
        insight_parts.append(f"處於{cur_regime_label}規制，趨勢偏空")
    if dist_60_lo >= 25:
        insight_parts.append(f"近 60 日已反彈 {dist_60_lo:.0f}%（漲幅偏大）")

    if insight_parts:
        _section_insight("，".join(insight_parts) + "。")

    return {
        "current": current, "day_pct": day_pct,
        "regime_label": cur_regime_label, "regime_days": cur_regime_days,
        "dist_1y_hi": dist_1y_hi, "dist_60_lo": dist_60_lo,
    }


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

    # End-of-section insight — what does the cross-asset picture say?
    n_str = len(stretched_names)
    n_tot = len(stretched_names) + len(neutral_names) + len(compressed_names)
    if n_str == 0:
        _section_insight("全球指數普遍處於各自的歷史中性區間，**並無系統性過熱**。")
    elif n_str == n_tot:
        _section_insight(
            "**全球同步進入自身歷史高位區**——這在歷史上非常罕見，"
            "通常領先系統性風險事件。資產配置應降低風險偏好。"
        )
    elif n_str == 1:
        _section_insight(
            f"僅 **{stretched_names[0]}** 偏熱，全球其他指數仍正常。"
            "屬單一市場的局部過熱，**不構成全球週期頂部訊號**。"
        )
    else:
        _section_insight(
            f"**{'、'.join(stretched_names)}** 處於自身歷史高位，"
            f"但 **{'、'.join(neutral_names)}** 仍中性——"
            "屬局部過熱（區域或產業集中），尚未蔓延為全球同步偏熱。"
        )

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

    tag, _composite_color = _momentum_composite(ret_30, ret_60, accel)

    c_30,   s_30,   r_30   = _classify_return(ret_30, "30d")
    c_60,   s_60,   r_60   = _classify_return(ret_60, "60d")
    c_acc,  s_acc,  r_acc  = _classify_acceleration(accel)
    if cur_vol is not None:
        c_vol, s_vol, r_vol = _classify_volatility(cur_vol, vol_pct)
    else:
        c_vol, s_vol, r_vol = HEALTH_COLORS["gray"], "—", "TAIEX 歷史中位 ~15%"

    c1, c2, c3, c4 = st.columns(4)
    with c1: _render_health_metric("30 日報酬",  f"{ret_30:+.2f}%", c_30, s_30, r_30)
    with c2: _render_health_metric("60 日報酬",  f"{ret_60:+.2f}%", c_60, s_60, r_60)
    with c3: _render_health_metric("加速度（近30天 vs 前30天）", f"{accel:+.2f}pp", c_acc, s_acc, r_acc)
    with c4:
        if cur_vol is not None:
            _render_health_metric(
                "20 日年化波動",
                f"{cur_vol:.1f}%　<span style='font-size:1.0rem'>(1年內 {vol_pct:.0f} 分位)</span>",
                c_vol, s_vol, r_vol,
            )
        else:
            _render_health_metric("20 日年化波動", "—", c_vol, s_vol, r_vol)

    # One-glance composite tag — kept because it's a useful synthesis line
    st.markdown(
        f"<div style='margin-top:0.5rem; padding:0.5rem 1rem; "
        f"border-left:3px solid {_composite_color}; background:rgba(255,255,255,0.025); "
        f"border-radius:3px;'>"
        f"<span style='color:#9ca3af; font-size:0.85rem'>綜合動能標籤：</span>"
        f"<span style='color:{_composite_color}; font-weight:650; font-size:1.05rem'>{tag}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # End-of-section insight — synthesize the four momentum metrics
    speed_parts = []
    if ret_30 > 15:
        speed_parts.append(f"近 30 天 +{ret_30:.1f}% 屬急漲區間")
    elif ret_30 > 8:
        speed_parts.append(f"近 30 天 +{ret_30:.1f}% 漲幅偏大")
    elif ret_30 < -8:
        speed_parts.append(f"近 30 天 {ret_30:+.1f}% 跌幅偏大")
    if accel > 5:
        speed_parts.append(f"加速度 +{accel:.1f}pp 顯示拋物線型上漲（近期比前期更快）")
    elif accel < -5:
        speed_parts.append(f"加速度 {accel:+.1f}pp 顯示動能急速轉弱")
    if cur_vol is not None and vol_pct is not None and cur_vol < 12 and vol_pct < 25:
        speed_parts.append("波動率位於 1 年低位，市場集體鬆懈（複雜頂風險）")
    elif cur_vol is not None and cur_vol > 25:
        speed_parts.append(f"波動率 {cur_vol:.0f}% 偏高，市場意見分歧")

    if speed_parts:
        _section_insight("；".join(speed_parts) + "。")
    else:
        _section_insight("動能與波動皆處於中性區間，無特殊型態。")
    return {"ret_30": ret_30, "ret_60": ret_60, "accel": accel,
            "vol": cur_vol, "vol_pct": vol_pct, "tag": tag}


def _render_chart_with_regimes(taiex: pd.Series) -> None:
    st.markdown("### 📊 TAIEX 2 年走勢（含規制色塊）")
    regimes_df = _compute_regimes_live(threshold_pct=4.0)

    # End-of-chart insight — describe what the regime breakdown shows
    if not regimes_df.empty:
        n_bull   = (regimes_df["regime"] == "bull").sum()
        n_corr   = (regimes_df["regime"] == "correction").sum()
        n_minib  = (regimes_df["regime"] == "mini_bear").sum()
        n_bigb   = (regimes_df["regime"] == "bear").sum()
        # Find current (last) leg
        last = regimes_df.iloc[-1]
        cur_label = REGIME_LABELS_ZH.get(last["regime"], last["regime"])
        cur_mag   = float(last["severity"])
        days = (pd.Timestamp(last["end_date"]) - pd.Timestamp(last["start_date"])).days

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

    # End-of-chart insight — describe the regime breakdown
    if not regimes_df.empty:
        _section_insight(
            f"過去 2 年 TAIEX 共經歷 **{n_bull} 段多頭 / {n_corr} 段小熊 / "
            f"{n_minib} 段中熊 / {n_bigb} 段大熊**。"
            f"目前處於最新的 **{cur_label}** 段（已 {days} 天，振幅 {cur_mag:+.1f}%）。"
        )


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
        "市場狀態儀表板 — **給你資料，由你判斷**。"
        "四個區塊分別測量**水平 / 拉伸 / 速度 / 廣度**，"
        "最後以一段中性文字綜合解讀。本頁不提供買賣訊號。"
    )

    taiex_df = db.get_prices("^TWII")
    if taiex_df.empty:
        st.error("資料庫無 TAIEX (^TWII) 價格。請先執行 step3_backfill。")
        return
    taiex = taiex_df.set_index("date")["close"].dropna()

    # "As of" date banner — important because viewing on a weekend / holiday
    # could mislead readers into thinking this is intraday data
    as_of_date = taiex.index[-1].date()
    today      = pd.Timestamp.today().date()
    days_stale = (today - as_of_date).days
    if days_stale == 0:
        freshness = "今日收盤"
        bg_color, text_color = "rgba(34,197,94,0.10)", "#86efac"
    elif days_stale == 1:
        freshness = "昨日收盤"
        bg_color, text_color = "rgba(99,102,241,0.10)", "#a5b4fc"
    elif days_stale <= 4:
        freshness = f"{days_stale} 天前收盤（週末/假日延遲）"
        bg_color, text_color = "rgba(99,102,241,0.10)", "#a5b4fc"
    else:
        freshness = f"⚠ {days_stale} 天前收盤（資料可能未更新，請檢查 step3 排程）"
        bg_color, text_color = "rgba(249,115,22,0.15)", "#fdba74"
    st.markdown(
        f"""<div style="padding:0.55rem 1rem; margin:0.4rem 0 1rem 0;
                      background:{bg_color}; border-radius:4px;
                      font-size:0.92rem; color:{text_color};">
              📅 <b>資料截至</b>　{as_of_date.strftime('%Y-%m-%d')}　({as_of_date.strftime('%A')})　·　{freshness}
            </div>""",
        unsafe_allow_html=True,
    )

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
