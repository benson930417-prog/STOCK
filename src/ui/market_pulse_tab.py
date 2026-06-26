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
Prior tactical backtests found no rule combo that beat DCA or static
rebalance on Sharpe across 5y of data. So this tab gives no
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
    ("^IXIC", "那斯達克"),
    ("^GSPC", "S&P 500"),       # user-preference: keep S&P 500 in English
    ("^DJI",  "道瓊"),
]


# ─── threshold constants — SINGLE SOURCE OF TRUTH ─────────────────────────
# All classifiers, composite tags, narratives, and summary-card interpretation
# read from THIS block. If you change a threshold here it propagates everywhere;
# previously the three layers had drifted apart (e.g. acceleration RED at +5pp
# in composite but +20pp in classifier, causing one tile to glow red while the
# next paragraph called it "stable").

# Z-score (normalised MA200 stretch) — used by table label, breadth count, summary
Z_ELEVATED          =  1.5   # cell labelled "偏高"; counted as stretched in breadth tally
Z_EXTREME           =  2.5   # cell labelled "高位"
Z_DEPRESSED         = -1.5   # cell labelled "偏低"; counted as compressed
Z_VERY_DEPRESSED    = -2.5   # cell labelled "低位"

# 30-day return %  (TAIEX 2y: p5=-8.6, p25=-0.3, p75=+9, p95=+18)
RET_30_STEADY_UP    =  3.0
RET_30_STEADY_DOWN  = -3.0
RET_30_BIG_UP       =  9.0
RET_30_EXTREME_UP   = 18.0
RET_30_BIG_DOWN     = -9.0
RET_30_EXTREME_DOWN = -18.0

# 60-day return %  (TAIEX 2y: p5=-10, p25=-0.2, p75=+15.6, p95=+24.4)
RET_60_STEADY_UP    =  6.0
RET_60_STEADY_DOWN  = -6.0
RET_60_BIG_UP       = 16.0
RET_60_EXTREME_UP   = 25.0
RET_60_BIG_DOWN     = -18.0
RET_60_EXTREME_DOWN = -25.0

# Acceleration (pp)  (TAIEX 2y: p25=-6, p75=+8.5, p90=+16, p95=+21)
ACCEL_NOTABLE       = 10.0    # orange "明顯加速"; ALSO the composite-tag trigger
ACCEL_EXTREME       = 20.0    # red "強烈加速"
ACCEL_NOTABLE_DOWN  = -10.0
ACCEL_EXTREME_DOWN  = -20.0

# Volatility (20d annualised %)  (TAIEX 2y: p25=15.6, p50=18, p75=24, p95=47)
VOL_LOW_ABS         = 13.0    # absolute vol below this = quiet market
VOL_HIGH_ABS        = 24.0    # orange
VOL_EXTREME_ABS     = 35.0    # red

# Day change %  (TAIEX 2y: ±1.5% on 24% of days, ±2.5% on ~7% of days)
DAY_NOTABLE_ABS     = 1.0
DAY_EXTREME_ABS     = 2.5

# Distance from 1y high %  (TAIEX 2y: p25=-8.4, p50=-4.9, p75=-1.2, at-high ~15% of days)
DIST_HIGH_AT         = -0.5
DIST_HIGH_NEAR       = -1.5
DIST_HIGH_NORMAL_LO  = -8.5
DIST_HIGH_CORRECTION = -16.0

# Distance from 60d low %
DIST_LOW_AT_LOW   = 2.0
DIST_LOW_NORMAL_HI = 20.0
DIST_LOW_STRONG    = 25.0
DIST_LOW_EXTREME   = 30.0


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
                    ma_window:    int = 200,
                    lookback_max: int = 504) -> tuple[float | None, float | None]:
    """Returns (current_stretch_pct, z_score_of_current_stretch_vs_history,
                effective_sample_size).

    Wait — for backwards compatibility I'm still returning just 2 values.
    The effective sample size is implicit: max(0, len(prices) - ma_window).

    Normalises by the asset's OWN historical stretch distribution so SOX
    (naturally high-vol) and DJI (low-vol) live on the same emotional
    scale. A z of +2 means "current stretch is 2 standard deviations
    above this asset's typical stretch" — genuinely unusual regardless
    of the raw percentage.

    Two correctness details:
    1. The current day is EXCLUDED from its own historical distribution.
    2. `lookback_max` is an upper bound, not a guaranteed sample size.
       With a 2y price DB the first 199 days are NaN for MA200, so the
       effective historical sample is ~305 obs (~14 months of stretches),
       narrower than the 504-day max suggests. Renamed from `lookback`
       to make this honest.
    """
    if prices is None or len(prices) < ma_window + 30:
        return None, None
    ma      = prices.rolling(ma_window).mean()
    stretch = (prices - ma) / ma * 100.0
    cur_stretch = float(stretch.iloc[-1])

    # Exclude today from the historical reference distribution
    hist_series = stretch.iloc[:-1]
    if len(hist_series) > lookback_max:
        hist = hist_series.iloc[-lookback_max:].dropna()
    else:
        hist = hist_series.dropna()
    if len(hist) < 30:
        return cur_stretch, None
    mean = float(hist.mean())
    std  = float(hist.std())
    if std <= 0:
        return cur_stretch, None
    return cur_stretch, (cur_stretch - mean) / std


def _zscore_label(z: float | None) -> str:
    """Soft descriptive label — uses module-level Z_* constants."""
    if z is None:                return "—"
    if z >= Z_EXTREME:           return "高位"
    if z >= Z_ELEVATED:          return "偏高"
    if z >  Z_DEPRESSED:         return "中性"
    if z >  Z_VERY_DEPRESSED:    return "偏低"
    return "低位"


def _zscore_style(z: float | None) -> str:
    """Subtle pandas Styler color — softer than the old red/green walls."""
    if z is None or pd.isna(z):  return ""
    if z >= Z_EXTREME:           return "background-color: rgba(220,38,38,0.18); font-weight: 600"
    if z >= Z_ELEVATED:          return "background-color: rgba(249,115,22,0.13)"
    if z >  Z_DEPRESSED:         return ""
    if z >  Z_VERY_DEPRESSED:    return "background-color: rgba(14,165,233,0.12)"
    return "background-color: rgba(30,64,175,0.18)"


def _rolling_vol(prices: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility from daily returns."""
    rets = prices.pct_change().dropna()
    return rets.rolling(window).std() * (252 ** 0.5) * 100.0


def _rolling_stretch_zscore(
    prices: pd.Series,
    ma_window: int = 200,
    lookback_max: int = 504,
) -> tuple[pd.Series, pd.Series]:
    """Daily MA stretch and trailing z-score, excluding each day from its own reference."""
    ma = prices.rolling(ma_window).mean()
    stretch = (prices - ma) / ma * 100.0
    ref = stretch.shift(1)
    mean = ref.rolling(lookback_max, min_periods=30).mean()
    std = ref.rolling(lookback_max, min_periods=30).std().where(lambda s: s > 0)
    return stretch, (stretch - mean) / std


def _bucket_stretch(z: float | None) -> str | None:
    if z is None or pd.isna(z):
        return None
    if z >= Z_EXTREME:
        return "extreme_high"
    if z >= Z_ELEVATED:
        return "elevated"
    if z <= Z_VERY_DEPRESSED:
        return "extreme_low"
    if z <= Z_DEPRESSED:
        return "depressed"
    return "neutral"


def _bucket_momentum(ret_30: float | None) -> str | None:
    if ret_30 is None or pd.isna(ret_30):
        return None
    if ret_30 >= RET_30_BIG_UP:
        return "strong_up"
    if ret_30 <= RET_30_BIG_DOWN:
        return "strong_down"
    if ret_30 >= RET_30_STEADY_UP:
        return "up"
    if ret_30 <= RET_30_STEADY_DOWN:
        return "down"
    return "flat"


def _bucket_accel(accel: float | None) -> str | None:
    if accel is None or pd.isna(accel):
        return None
    if accel >= ACCEL_NOTABLE:
        return "accelerating"
    if accel <= ACCEL_NOTABLE_DOWN:
        return "decelerating"
    return "stable"


def _bucket_breadth(n_stretched: int | None, n_total: int | None) -> str | None:
    if not n_total:
        return None
    if n_stretched <= 0:
        return "none"
    if n_stretched == 1:
        return "local"
    if n_stretched <= n_total // 2:
        return "clustered"
    if n_stretched < n_total:
        return "broad"
    return "full"


def _build_market_signal_history(taiex: pd.Series) -> pd.DataFrame:
    """Build one daily signal table for historical-analog mining.

    The forward-return columns intentionally stay NaN near the end, because
    those days do not yet have enough future observations to evaluate.
    """
    prices = taiex.dropna().copy()
    out = pd.DataFrame({"close": prices})
    out["ret_30"] = (prices / prices.shift(30) - 1.0) * 100.0
    out["ret_60"] = (prices / prices.shift(60) - 1.0) * 100.0
    out["prior_30"] = (prices.shift(30) / prices.shift(60) - 1.0) * 100.0
    out["accel"] = out["ret_30"] - out["prior_30"]
    out["vol_20"] = _rolling_vol(prices, 20)
    _stretch, out["tw_z"] = _rolling_stretch_zscore(prices)

    z_cols: list[pd.Series] = []
    for ticker, _name in CROSS_ASSET_INDICES:
        df = db.get_prices(ticker)
        if df.empty:
            continue
        series = df.set_index("date")["close"].dropna()
        _s, z = _rolling_stretch_zscore(series)
        z_cols.append(z.rename(ticker))
    if z_cols:
        zdf = pd.concat(z_cols, axis=1).reindex(out.index)
        out["breadth_total"] = zdf.notna().sum(axis=1)
        out["breadth_stretched"] = (zdf >= Z_ELEVATED).sum(axis=1)
    else:
        out["breadth_total"] = 0
        out["breadth_stretched"] = 0

    out["z_bucket"] = out["tw_z"].map(_bucket_stretch)
    out["momentum_bucket"] = out["ret_30"].map(_bucket_momentum)
    out["accel_bucket"] = out["accel"].map(_bucket_accel)
    out["breadth_bucket"] = [
        _bucket_breadth(n, t) for n, t in zip(out["breadth_stretched"], out["breadth_total"])
    ]

    for horizon in (20, 60):
        out[f"fwd_{horizon}"] = (prices.shift(-horizon) / prices - 1.0) * 100.0
    return out


def _nearest_analogs(history: pd.DataFrame, current: pd.Series, max_rows: int = 30) -> tuple[pd.DataFrame, str]:
    eligible = history.dropna(subset=["tw_z", "ret_30", "accel", "vol_20", "fwd_20", "fwd_60"]).copy()
    eligible = eligible[eligible.index < current.name]
    if eligible.empty:
        return eligible, "no_sample"

    strict = eligible[
        (eligible["z_bucket"] == current.get("z_bucket"))
        & (eligible["momentum_bucket"] == current.get("momentum_bucket"))
        & (eligible["breadth_bucket"] == current.get("breadth_bucket"))
    ].copy()
    if len(strict) >= 8:
        return strict.tail(max_rows), "same_buckets"

    semi = eligible[
        (eligible["z_bucket"] == current.get("z_bucket"))
        & (eligible["momentum_bucket"] == current.get("momentum_bucket"))
    ].copy()
    if len(semi) >= 8:
        return semi.tail(max_rows), "same_stretch_momentum"

    features = ["tw_z", "ret_30", "accel", "vol_20", "breadth_stretched"]
    norm = eligible[features].std().replace(0, pd.NA).fillna(1.0)
    dist = (((eligible[features] - current[features]) / norm) ** 2).sum(axis=1) ** 0.5
    nearest = eligible.assign(_distance=dist).nsmallest(min(max_rows, len(eligible)), "_distance")
    return nearest, "nearest"


def _render_historical_analogs(taiex: pd.Series) -> dict:
    """Data-mining panel: similar past states and their future-return distribution."""
    st.markdown("### 🔎 歷史相似情境（資料探勘）")
    st.caption(
        "用目前的 MA200 z-score、30 日動能、加速度、波動與跨市場拉伸寬度，"
        "尋找過去相似日期，觀察之後 20/60 個交易日的報酬分布。"
        "**這是歷史相似樣本，不是買賣訊號。**"
    )

    history = _build_market_signal_history(taiex)
    current_candidates = history.dropna(subset=["tw_z", "ret_30", "accel", "vol_20"])
    if current_candidates.empty:
        st.info("資料尚不足以建立相似情境（需要 MA200 與至少 60 個交易日動能資料）。")
        return {"n": 0, "method": "insufficient_current"}
    current = current_candidates.iloc[-1]
    analogs, method = _nearest_analogs(history, current)
    if len(analogs) < 5:
        st.info("可用歷史樣本太少，暫不顯示相似情境統計。")
        return {"n": int(len(analogs)), "method": method}

    rows = []
    for horizon in (20, 60):
        vals = analogs[f"fwd_{horizon}"].dropna()
        if vals.empty:
            continue
        rows.append({
            "觀察期": f"未來 {horizon} 交易日",
            "樣本數": int(len(vals)),
            "上漲機率": float((vals > 0).mean() * 100.0),
            "中位數": float(vals.median()),
            "平均": float(vals.mean()),
            "25分位": float(vals.quantile(0.25)),
            "75分位": float(vals.quantile(0.75)),
        })

    if not rows:
        st.info("相似情境尚無足夠未來報酬可供統計。")
        return {"n": int(len(analogs)), "method": method}

    method_label = {
        "same_buckets": "同拉伸/同動能/同寬度分組",
        "same_stretch_momentum": "同拉伸/同動能分組",
        "nearest": "最近鄰相似度",
    }.get(method, method)
    st.caption(f"比對方式：{method_label}；樣本 {len(analogs)} 筆。樣本越少，參考性越低。")
    df_show = pd.DataFrame(rows)
    styled = df_show.style.format({
        "上漲機率": "{:.0f}%",
        "中位數": "{:+.2f}%",
        "平均": "{:+.2f}%",
        "25分位": "{:+.2f}%",
        "75分位": "{:+.2f}%",
    })
    st.dataframe(styled, hide_index=True, width="stretch")

    with st.expander("查看最相似日期", expanded=False):
        cols = ["close", "tw_z", "ret_30", "accel", "vol_20", "breadth_stretched", "breadth_total", "fwd_20", "fwd_60"]
        detail = analogs[cols].copy().reset_index(names="日期")
        detail = detail.sort_values("日期", ascending=False).head(12)
        st.dataframe(
            detail.style.format({
                "close": "{:,.0f}",
                "tw_z": "{:+.2f}",
                "ret_30": "{:+.2f}%",
                "accel": "{:+.2f}pp",
                "vol_20": "{:.1f}%",
                "fwd_20": "{:+.2f}%",
                "fwd_60": "{:+.2f}%",
            }),
            hide_index=True,
            width="stretch",
        )

    return {"n": int(len(analogs)), "method": method, "rows": rows}


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
    """Symmetric color: same magnitude up/down gets same severity color.
    Calibrated to TAIEX 2y: |daily| ≤ 1% on 50% of days, ±2.5% ~ p95."""
    ref = f"正常 ±{DAY_NOTABLE_ABS:.0f}% / 偏大 ±{DAY_NOTABLE_ABS:.0f}~{DAY_EXTREME_ABS:.1f}% / 極端 >±{DAY_EXTREME_ABS:.1f}%（基於加權指數 2 年分布）"
    abs_pct = abs(pct)
    if abs_pct >= DAY_EXTREME_ABS:
        tag = "大漲" if pct > 0 else "大跌"
        return HEALTH_COLORS["red"], tag, ref
    if abs_pct >= DAY_NOTABLE_ABS:
        tag = "上漲偏大" if pct > 0 else "下跌偏大"
        return HEALTH_COLORS["orange"], tag, ref
    if abs_pct >= 0.3:
        tag = "上漲" if pct > 0 else "下跌"
        return HEALTH_COLORS["gray"], tag, ref     # gray = small daily noise, no signal
    return HEALTH_COLORS["gray"], "持平", ref


def _classify_regime(label_zh: str, days: int | None,
                     dist_from_1y_high: float | None = None) -> tuple[str, str, str]:
    """Regime color reflects BOTH the regime type AND its risk context, so
    the headline reads consistently:
      • 多頭 而且近高點 → yellow/orange (pullback risk),不是green
      • 多頭 fresh and away from high → green (healthy uptrend)
      • 小熊/中熊/大熊 → progressive yellow→red by severity
    """
    if label_zh == "多頭":
        near_high = (dist_from_1y_high is not None and dist_from_1y_high >= -1.0)
        mature    = (days is not None and days >= 180)
        moderate  = (days is not None and days >= 90)
        if near_high and mature:
            return HEALTH_COLORS["red"],    "成熟多頭近高點", "多頭近高點 + 持續 >180 交易日 → 拉回機率明顯升高"
        if near_high:
            return HEALTH_COLORS["orange"], "多頭近高點",     "多頭接近 1 年高點 → 拉回風險偏高"
        if mature:
            return HEALTH_COLORS["yellow"], "延長多頭",       "多頭持續超過 180 交易日屬延長階段"
        if moderate:
            return HEALTH_COLORS["green"],  "成熟多頭",       "多頭通常持續 50~200 個交易日"
        return HEALTH_COLORS["green"],      "健康上升",       "多頭通常持續 50~200 個交易日"

    if label_zh == "小熊":
        return HEALTH_COLORS["yellow"], "短期修正", "小熊通常 5~20 個交易日"
    if label_zh == "中熊":
        return HEALTH_COLORS["orange"], "中期回檔", "中熊通常 20~60 個交易日"
    if label_zh == "大熊":
        return HEALTH_COLORS["red"],    "深度熊市", "大熊通常 30~150 個交易日"
    return HEALTH_COLORS["gray"], label_zh, ""


def _classify_distance_from_high(dist_pct: float) -> tuple[str, str, str]:
    """Calibrated to TAIEX 2y: p25=-8.4%, p50=-4.9%, p75=-1.2%, at-high ~15% of days."""
    ref = (f"常態約 {DIST_HIGH_NORMAL_LO:.0f}%~{DIST_HIGH_NEAR:.1f}% / "
           f"近高點 ≥{DIST_HIGH_AT:.1f}% / "
           f"回檔 <{DIST_HIGH_NORMAL_LO:.0f}% / 低位 <{DIST_HIGH_CORRECTION:.0f}%（基於加權指數 2 年分布）")
    if dist_pct >= DIST_HIGH_AT:           return HEALTH_COLORS["orange"], "近高點",   ref
    if dist_pct >= DIST_HIGH_NEAR:         return HEALTH_COLORS["yellow"], "高位區",   ref
    if dist_pct >= DIST_HIGH_NORMAL_LO:    return HEALTH_COLORS["green"],  "常態區",   ref
    if dist_pct >= DIST_HIGH_CORRECTION:   return HEALTH_COLORS["blue"],   "回檔區",   ref
    return HEALTH_COLORS["deep_blue"], "低位區", ref


def _classify_distance_from_low(dist_pct: float) -> tuple[str, str, str]:
    """Calibrated to TAIEX 2y: p25=+8.7%, p50=+14.2%, p75=+19.8%, p95=+28.7%."""
    ref = (f"常態約 8%~{DIST_LOW_NORMAL_HI:.0f}% / "
           f"強漲 >{DIST_LOW_STRONG:.0f}% / 急漲 >{DIST_LOW_EXTREME:.0f}%（基於加權指數 2 年分布）")
    if dist_pct >= DIST_LOW_EXTREME:   return HEALTH_COLORS["red"],    "急漲",     ref
    if dist_pct >= DIST_LOW_STRONG:    return HEALTH_COLORS["orange"], "強漲",     ref
    if dist_pct >= DIST_LOW_NORMAL_HI: return HEALTH_COLORS["yellow"], "偏高反彈", ref
    if dist_pct >= 8:                  return HEALTH_COLORS["green"],  "常態反彈", ref
    if dist_pct >= DIST_LOW_AT_LOW:    return HEALTH_COLORS["blue"],   "近低位",   ref
    return HEALTH_COLORS["deep_blue"], "貼近低點", ref


def _classify_return(pct: float, window: str) -> tuple[str, str, str]:
    """Symmetric coloring: same-magnitude up/down get same severity color.
    Removed the old 'GREEN appears on BOTH sides of GRAY' bug.

    Color sequence (descending value):
        red → orange → green → gray → yellow → orange → red
    """
    if window == "30d":
        big_up, extreme_up   = RET_30_BIG_UP,   RET_30_EXTREME_UP
        big_down, extreme_dn = RET_30_BIG_DOWN, RET_30_EXTREME_DOWN
        steady_up, steady_down = RET_30_STEADY_UP, RET_30_STEADY_DOWN
        ref = (f"常態 -3%~+{RET_30_BIG_UP:.0f}% / 大漲 >+{RET_30_BIG_UP:.0f}% / "
               f"急漲 >+{RET_30_EXTREME_UP:.0f}%（基於加權指數 2 年分布）")
    else:  # 60d
        big_up, extreme_up   = RET_60_BIG_UP,   RET_60_EXTREME_UP
        big_down, extreme_dn = RET_60_BIG_DOWN, RET_60_EXTREME_DOWN
        steady_up, steady_down = RET_60_STEADY_UP, RET_60_STEADY_DOWN
        ref = (f"常態 -6%~+{RET_60_BIG_UP:.0f}% / 大漲 >+{RET_60_BIG_UP:.0f}% / "
               f"急漲 >+{RET_60_EXTREME_UP:.0f}%（基於加權指數 2 年分布）")

    if pct >= extreme_up:     return HEALTH_COLORS["red"],    "急漲",     ref
    if pct >= big_up:         return HEALTH_COLORS["orange"], "大漲",     ref
    if pct >= steady_up:      return HEALTH_COLORS["green"],  "穩健上漲", ref
    if pct >  steady_down:    return HEALTH_COLORS["gray"],   "盤整",     ref
    if pct >   big_down:      return HEALTH_COLORS["yellow"], "下跌",     ref      # was green (bug)
    if pct >   extreme_dn:    return HEALTH_COLORS["orange"], "大跌",     ref
    return HEALTH_COLORS["red"], "重挫", ref


def _classify_acceleration(accel: float) -> tuple[str, str, str]:
    """Uses ACCEL_* constants (same ones consumed by composite tag + insight)."""
    ref = (f"常態 ±10pp / 明顯加速 >+{ACCEL_NOTABLE:.0f}pp / "
           f"強烈加速 >+{ACCEL_EXTREME:.0f}pp（基於加權指數 2 年分布）")
    if accel >= ACCEL_EXTREME:        return HEALTH_COLORS["red"],       "強烈加速", ref
    if accel >= ACCEL_NOTABLE:        return HEALTH_COLORS["orange"],    "明顯加速", ref
    if accel >  ACCEL_NOTABLE_DOWN:   return HEALTH_COLORS["green"],     "穩定",     ref
    if accel >  ACCEL_EXTREME_DOWN:   return HEALTH_COLORS["blue"],      "明顯減速", ref
    return HEALTH_COLORS["deep_blue"], "急轉", ref


def _classify_volatility(vol_pct: float, vol_percentile: float | None) -> tuple[str, str, str]:
    """Uses VOL_* constants. Insight thresholds match classifier exactly."""
    ref = (f"加權指數中位 ~18% / 偏高 >{VOL_HIGH_ABS:.0f}% / "
           f"高波動 >{VOL_EXTREME_ABS:.0f}%（基於 2 年分布）")
    if vol_pct >= VOL_EXTREME_ABS:    return HEALTH_COLORS["red"],    "高波動",     ref
    if vol_pct >= VOL_HIGH_ABS:       return HEALTH_COLORS["orange"], "偏高",       ref
    if vol_pct >= VOL_LOW_ABS:        return HEALTH_COLORS["green"],  "正常",       ref
    # Low vol — check percentile for "complacent top" warning
    if vol_percentile is not None and vol_percentile < 25:
        return HEALTH_COLORS["yellow"], "低波動（複雜頂風險）", ref
    return HEALTH_COLORS["blue"], "低波動", ref


# ─── composite tags ────────────────────────────────────────────────────────
def _momentum_composite(ret_30: float, ret_60: float, accel: float) -> tuple[str, str]:
    """Collapse three momentum numbers into one descriptive tag.

    Uses the SAME thresholds as _classify_return / _classify_acceleration so
    the composite tag can never contradict the per-metric tiles. (Old version
    used standalone +2pp / +5pp thresholds while the classifier had moved to
    +10pp / +20pp — same accel value would show GREEN '穩定' in the tile and
    RED '加速中' in the composite.)
    """
    big_up   = ret_30 >  RET_30_BIG_UP
    big_down = ret_30 <  RET_30_BIG_DOWN
    accel_up   = accel >  ACCEL_NOTABLE
    accel_down = accel <  ACCEL_NOTABLE_DOWN

    if big_up and accel_up:                  return ("高速且加速", "#dc2626")
    if big_up and accel_down:                return ("高速但減速", "#f97316")
    if big_up:                               return ("穩定高速",   "#facc15")
    if big_down and accel_down:              return ("急跌且加速", "#dc2626")
    if big_down and accel_up:                return ("急跌但減速", "#0ea5e9")
    if big_down:                             return ("穩定下行",   "#0ea5e9")
    if accel_up:                             return ("加速中",     "#f97316")
    if accel_down:                           return ("減速中",     "#0ea5e9")
    return ("穩定",                                                 "#22c55e")


# ─── rendering blocks ─────────────────────────────────────────────────────
def _render_headline(taiex: pd.Series, regimes_df: pd.DataFrame) -> dict:
    """Market Level — colored lab-report metrics + end-of-section insight.
    Returns a dict so the summary card can reuse the computed values.
    `regimes_df` is computed once upstream and passed in (Bug 13 fix:
    was being recomputed in every render function)."""
    if taiex is None or len(taiex) < 2:
        st.warning("加權指數資料不足，無法顯示。")
        return {}

    current = float(taiex.iloc[-1])
    prev    = float(taiex.iloc[-2])
    day_pct = (current - prev) / prev * 100.0

    cur_regime_label = "—"
    cur_regime_days  = None
    if regimes_df is not None and not regimes_df.empty:
        today_ts = taiex.index[-1]
        ongoing = regimes_df[
            (regimes_df["start_date"] <= today_ts) & (regimes_df["end_date"] >= today_ts)
        ]
        leg = ongoing.iloc[-1] if not ongoing.empty else regimes_df.iloc[-1]
        cur_regime_label = REGIME_LABELS_ZH.get(leg["regime"], leg["regime"])
        # Bug 18 fix: count TRADING days (not calendar days) by intersecting
        # the leg window with the price-series index. Aligns with how
        # _classify_regime's "mature ≥ 180" threshold was actually meant.
        leg_start_ts = pd.Timestamp(leg["start_date"])
        cur_regime_days = int(
            ((taiex.index >= leg_start_ts) & (taiex.index <= today_ts)).sum()
        )

    h_252 = float(taiex.iloc[-252:].max()) if len(taiex) >= 252 else float(taiex.max())
    l_60  = float(taiex.iloc[-60:].min())  if len(taiex) >=  60 else float(taiex.min())
    dist_1y_hi = (current - h_252) / h_252 * 100.0 if h_252 > 0 else 0.0
    dist_60_lo = (current - l_60)  / l_60  * 100.0 if l_60  > 0 else 0.0

    # Per-metric classification — regime takes the distance-from-high as
    # context so a 多頭 near the 1y high reads yellow/orange, not green
    c_day,    s_day,    r_day    = _classify_day_change(day_pct)
    c_reg,    s_reg,    r_reg    = _classify_regime(cur_regime_label, cur_regime_days, dist_1y_hi)
    c_hi,     s_hi,     r_hi     = _classify_distance_from_high(dist_1y_hi)
    c_lo,     s_lo,     r_lo     = _classify_distance_from_low(dist_60_lo)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _render_health_metric(
            "今日漲跌（加權指數）",
            f"{current:,.0f}　<span style='font-size:1.0rem'>{day_pct:+.2f}%</span>",
            c_day, s_day, "今日漲跌單獨看：" + r_day,
        )
    with c2:
        days_str = f"{cur_regime_label}已 {cur_regime_days} 交易日" if cur_regime_days is not None else "—"
        _render_health_metric(
            "目前規制（ZigZag 4%）",
            f"{cur_regime_label}　<span style='font-size:1.0rem'>{days_str}</span>",
            c_reg, s_reg, r_reg,
        )
    with c3:
        _render_health_metric(
            "距 1 年高點",
            f"{dist_1y_hi:+.1f}%",
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
        insight_parts.append("加權指數正在創 1 年新高")
    elif dist_1y_hi >= -5:
        insight_parts.append(f"加權指數距 1 年高點僅 {abs(dist_1y_hi):.1f}%")
    elif dist_1y_hi <= -20:
        insight_parts.append(f"加權指數較高點回落 {abs(dist_1y_hi):.0f}%（已進入熊市區間）")
    if cur_regime_label == "多頭" and cur_regime_days and cur_regime_days >= 30:
        insight_parts.append(f"處於多頭第 {cur_regime_days} 交易日，趨勢延續中")
    elif cur_regime_label in ("中熊", "大熊"):
        insight_parts.append(f"處於{cur_regime_label}規制，趨勢偏空")
    if dist_60_lo >= 25:
        insight_parts.append(f"近 60 日已反彈 {dist_60_lo:.0f}%（漲幅偏大）")

    if insight_parts:
        _section_insight(f"今日 {day_pct:+.2f}%（{s_day}），" + "，".join(insight_parts) + "。")

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

    # Stretch cases — graded by share of indices stretched (smoother than the
    # old "<=total//2 → 局部, > total//2 → 接近全面" jump at exactly 3/5).
    if n_stretched == 0:
        return f"{total} 個指數全部位於中性區間 — 全球大盤未過熱。"
    if n_stretched == total:
        return f"{total} 個指數同步拉伸 — 全面過熱，無一倖免。"
    if n_stretched == 1:
        return (f"**僅 {_join(stretched_names)}** 處於自身歷史高位區間；"
                f"其餘 {total - 1} 個指數仍中性 — **局部現象，非全球同步**。")
    share = n_stretched / total
    if share <= 0.40:        # 1-2 of 5
        return (f"**熱點集中於 {_join(stretched_names)}**；"
                f"**{_join(neutral_names)}** 仍在中性區間 — "
                f"**全球大盤尚未全面過熱**。")
    if share <= 0.70:        # 3-4 of 5
        return (f"**{_join(stretched_names)}** 已拉伸（多市場同步）；"
                f"{_join(neutral_names)} 仍中性 — 偏熱範圍正在擴大。")
    return (f"**{_join(stretched_names)}** 已拉伸；"
            f"僅 {_join(neutral_names) or '少數'} 仍中性 — **接近全面偏熱**。")


def _render_stretch_normalized() -> dict:
    """Cross-asset stretch table normalised by each asset's own 2y history.

    Returns a dict summary {n_stretched, n_compressed, n_total} so the
    summary card downstream can build a breadth statement without
    re-running the math.
    """
    st.markdown("### 🌍 趨勢拉伸（跨資產，自身分布標準化）")
    st.caption(
        "**距 MA200** = 與 200 日均線的距離。"
        "**z-score** = 該距離在自身過去拉伸值分布中的標準差位置。"
        "因為 SOX 天生比道瓊波動大，比較原始百分比不公平 — "
        "z-score 把它們放在同一個量尺：**>+2 = 該資產自己歷史上的高位**。"
        f"（樣本：MA200 起算後 ~14 個月的擺動分布；資料庫保留 2 年，前 200 天用於 MA 暖機。）"
    )

    rows: list[dict] = []
    stretched_names:  list[str] = []   # z >= Z_ELEVATED → cell labeled "偏高" / "高位"
    neutral_names:    list[str] = []   # in between
    compressed_names: list[str] = []   # z <= Z_DEPRESSED → cell labeled "偏低" / "低位"
    tw_z_score: float | None = None
    for ticker, name in CROSS_ASSET_INDICES:
        df = db.get_prices(ticker)
        if df.empty:
            rows.append({"指數": f"{name} ({ticker})",
                         "距 MA200": None, "z-score": None, "判斷": "—"})
            continue
        prices = df.set_index("date")["close"].dropna()
        stretch, z = _stretch_zscore(prices, ma_window=200, lookback_max=504)
        rows.append({
            "指數":     f"{name} ({ticker})",
            "距 MA200": stretch,
            "z-score":  z,
            "判斷":     _zscore_label(z),
        })
        if ticker == "^TWII":
            tw_z_score = z
        if z is None:
            continue
        # Counts use the SAME threshold the cell-label uses, so the breadth
        # narrative can never disagree with the visible table. (Bug 2 fix:
        # was z>=2.0 here but cell labelled "偏高" at z>=1.5, causing
        # 'all normal' narrative beneath an orange-tinted row.)
        if z >= Z_ELEVATED:
            stretched_names.append(name)
        elif z <= Z_DEPRESSED:
            compressed_names.append(name)
        else:
            neutral_names.append(name)

    df_show = pd.DataFrame(rows)
    # Sort by z-score desc so most-stretched float to the top; nones at bottom
    df_show["_sort"] = df_show["z-score"].fillna(-9999)
    df_show = df_show.sort_values("_sort", ascending=False).drop(columns="_sort")

    # Color BOTH the 距 MA200 and z-score columns using the row's z-score band,
    # not the cell's raw value. Within one asset stretch% and z-score are
    # colinear, so applying the z-band to the MA200 column gives consistent
    # visual signal (Bug 19 fix: previously MA200 column had no color cue,
    # which made the most visceral number on the row the least flagged).
    def _row_style(row: pd.Series) -> list[str]:
        z = row["z-score"]
        style = _zscore_style(z)
        return [style if col in ("距 MA200", "z-score") else "" for col in row.index]

    styled = (
        df_show.style
        .format({
            "距 MA200": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "z-score":  lambda v: f"{v:+.2f}"  if pd.notna(v) else "—",
        })
        .apply(_row_style, axis=1)
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
        "tw_z":             tw_z_score,   # explicit, no string-match fragility
    }


def _render_speed_panel(taiex: pd.Series) -> dict:
    """Single combined card: 30d return, 60d return, acceleration, 20d vol.

    Returns a dict summary so the summary card can use it.
    """
    st.markdown("### 🚀 動能與波動（加權指數）")
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
    vol_hist_all = vol_series.dropna()
    if len(vol_hist_all) >= 30:
        cur_vol = float(vol_hist_all.iloc[-1])
        # Exclude the current value from its own percentile reference.
        vol_ref = vol_hist_all.iloc[:-1]
        if len(vol_ref) >= 30:
            vol_window = vol_ref.iloc[-252:] if len(vol_ref) > 252 else vol_ref
            vol_pct = float((vol_window <= cur_vol).sum()) / len(vol_window) * 100

    tag, _composite_color = _momentum_composite(ret_30, ret_60, accel)

    c_30,   s_30,   r_30   = _classify_return(ret_30, "30d")
    c_60,   s_60,   r_60   = _classify_return(ret_60, "60d")
    c_acc,  s_acc,  r_acc  = _classify_acceleration(accel)
    if cur_vol is not None:
        c_vol, s_vol, r_vol = _classify_volatility(cur_vol, vol_pct)
    else:
        c_vol, s_vol, r_vol = HEALTH_COLORS["gray"], "—", "加權指數歷史中位 ~18%"

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

    # End-of-section insight — uses the SAME constants as the tile classifiers
    # so insight text and tile color/tag never contradict each other.
    speed_parts = []
    if ret_30 >= RET_30_EXTREME_UP:
        speed_parts.append(f"近 30 天 +{ret_30:.1f}% 屬急漲區間")
    elif ret_30 >= RET_30_BIG_UP:
        speed_parts.append(f"近 30 天 +{ret_30:.1f}% 漲幅偏大")
    elif ret_30 <= RET_30_BIG_DOWN:
        speed_parts.append(f"近 30 天 {ret_30:+.1f}% 跌幅偏大")
    if accel >= ACCEL_NOTABLE:
        speed_parts.append(f"加速度 +{accel:.1f}pp 顯示拋物線型上漲（近期比前期更快）")
    elif accel <= ACCEL_NOTABLE_DOWN:
        speed_parts.append(f"加速度 {accel:+.1f}pp 顯示動能急速轉弱")
    if cur_vol is not None and vol_pct is not None and cur_vol < VOL_LOW_ABS and vol_pct < 25:
        speed_parts.append("波動率位於 1 年低位，市場集體鬆懈（複雜頂風險）")
    elif cur_vol is not None and cur_vol >= VOL_HIGH_ABS:
        speed_parts.append(f"波動率 {cur_vol:.0f}% 偏高，市場意見分歧")

    if speed_parts:
        _section_insight("；".join(speed_parts) + "。")
    else:
        _section_insight("動能與波動皆處於中性區間，無特殊型態。")
    return {"ret_30": ret_30, "ret_60": ret_60, "accel": accel,
            "vol": cur_vol, "vol_pct": vol_pct, "tag": tag}


def _roc_to_date(s: str) -> "pd.Timestamp":
    y, m, d = s.split("/")
    return pd.Timestamp(int(y) + 1911, int(m), int(d))


@st.cache_data(ttl=3600, show_spinner=False)
def _twse_daily_market(months: int = 4) -> pd.DataFrame:
    """Official TWSE daily market stats (FMTQIK): TAIEX close + 成交金額(turnover)
    + 成交股數(volume). Authoritative and same-day-after-close — and crucially it
    never carries a 0 placeholder for an unsettled day (the latest published row
    is always fully settled), unlike Yahoo's ^TWII volume. The monthly endpoint
    returns one calendar month per call, so we stitch the last few months to get
    enough history for the 20-day windows."""
    import requests
    requests.packages.urllib3.disable_warnings()
    today = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None).normalize()
    seen: dict = {}
    for k in range(months):
        anchor = today.replace(day=1) - pd.DateOffset(months=k)
        ymd = anchor.strftime("%Y%m01")
        try:
            r = requests.get(
                f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ymd}&response=json",
                headers={"User-Agent": "Mozilla/5.0"}, verify=False, timeout=15,
            )
            payload = r.json()
        except Exception:
            continue
        if payload.get("stat") != "OK":
            continue
        for row in payload.get("data") or []:
            try:
                dt = _roc_to_date(row[0])
                volume   = float(str(row[1]).replace(",", ""))
                turnover = float(str(row[2]).replace(",", ""))
                close    = float(str(row[4]).replace(",", ""))
            except Exception:
                continue
            seen[dt] = (close, turnover, volume)
    if not seen:
        return pd.DataFrame(columns=["close", "turnover", "volume"])
    return (
        pd.DataFrame([(d, *v) for d, v in sorted(seen.items())],
                     columns=["date", "close", "turnover", "volume"])
        .set_index("date")
    )


def _fmt_turnover(x: float) -> str:
    if x is None:
        return "—"
    if x >= 1e12:
        return f"{x / 1e12:.2f} 兆"
    return f"{x / 1e8:,.0f} 億"


def _render_price_volume() -> dict:
    """價量健康 — built ONLY from close + volume (the least-lagging, most honest
    data). Source is TWSE official daily stats (成交金額 + TAIEX), which is
    same-day-after-close and never an estimate/0 — "correct or nothing". Decision
    *context*, not a buy/sell signal."""
    st.markdown("### 🔊 價量健康（成交量與量價關係）")
    st.caption(
        "只看**收盤價 + 成交量**——市場最誠實的兩個數字。量＝真金白銀的參與度："
        "**上漲要有量才有說服力，下跌帶量代表賣壓重**。"
        "資料來源：**TWSE 官方每日成交統計（收盤後更新，無估計值）**；以成交金額衡量量能。"
    )

    mkt = _twse_daily_market()
    if mkt.empty or len(mkt) < 25:
        st.info("無法取得 TWSE 官方成交量資料（或有效交易日不足 25 天）。")
        return {}

    close, turn = mkt["close"], mkt["turnover"]
    as_of   = mkt.index[-1]
    last_t  = float(turn.iloc[-1])
    avg20   = float(turn.iloc[-21:-1].mean())
    vol_ratio = (last_t / avg20) if avg20 > 0 else None

    ret_5   = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1.0) * 100.0 if len(close) >= 6 else 0.0
    t5, t20 = float(turn.iloc[-5:].mean()), float(turn.iloc[-20:].mean())
    vol_trend = (t5 / t20) if t20 > 0 else 1.0

    diff = close.diff()
    signed = turn.astype(float).copy()
    signed[diff < 0] *= -1.0
    signed[diff == 0] = 0.0
    obv = signed.cumsum()
    if len(close) >= 21 and avg20 > 0:
        price_20    = (float(close.iloc[-1]) / float(close.iloc[-21]) - 1.0) * 100.0
        obv_20_days = (float(obv.iloc[-1]) - float(obv.iloc[-21])) / avg20
    else:
        price_20, obv_20_days = 0.0, 0.0

    # 1) 今日量能 vs 20 日均量（成交金額）
    if vol_ratio is None:
        c_v, s_v, vol_value = HEALTH_COLORS["gray"], "—", "—"
    else:
        vol_value = f"{vol_ratio:.1f} 倍"
        if   vol_ratio >= 2.0: c_v, s_v = HEALTH_COLORS["red"], "爆量"
        elif vol_ratio >= 1.3: c_v, s_v = HEALTH_COLORS["orange"], "放量"
        elif vol_ratio >= 0.7: c_v, s_v = HEALTH_COLORS["green"], "正常"
        elif vol_ratio >= 0.5: c_v, s_v = HEALTH_COLORS["blue"], "量縮"
        else:                  c_v, s_v = HEALTH_COLORS["deep_blue"], "急縮"
    r_v = f"今日 {_fmt_turnover(last_t)} ÷ 前20日均量 {_fmt_turnover(avg20)}。>2倍=爆量，1.3~2=放量，<0.5=急縮"

    # 2) 量價關係 — this week's price direction × volume trend
    price_up, price_down = ret_5 > 1.0, ret_5 < -1.0
    vol_expand, vol_shrink = vol_trend > 1.1, vol_trend < 0.9
    if   price_up and vol_expand:   c_pv, s_pv, pv_msg = HEALTH_COLORS["green"],  "價漲量增", "上漲有買盤撐腰，走勢較可信"
    elif price_up and vol_shrink:   c_pv, s_pv, pv_msg = HEALTH_COLORS["yellow"], "價漲量縮", "漲勢量能不足，追高需謹慎"
    elif price_down and vol_expand: c_pv, s_pv, pv_msg = HEALTH_COLORS["red"],    "價跌量增", "賣壓沉重（恐慌或出貨）"
    elif price_down and vol_shrink: c_pv, s_pv, pv_msg = HEALTH_COLORS["blue"],   "價跌量縮", "賣壓減弱，跌勢趨緩"
    elif price_up:                  c_pv, s_pv, pv_msg = HEALTH_COLORS["green"],  "價漲量平", "上漲但量能普通"
    elif price_down:                c_pv, s_pv, pv_msg = HEALTH_COLORS["yellow"], "價跌量平", "下跌但量能普通"
    else:                           c_pv, s_pv, pv_msg = HEALTH_COLORS["gray"],   "量價平淡", "近一週價格波動不大"
    r_pv = "近5日加權指數方向 × 量能趨勢。價漲量增最健康，價漲量縮要小心，價跌量增最危險"

    # 3) OBV 量價背離 (20d, on turnover)
    if   price_20 > 1.5 and obv_20_days < 0:  c_o, s_o, o_msg = HEALTH_COLORS["orange"], "頂背離",   "指數創高但量能沒跟上 — 買盤在縮手"
    elif price_20 < -1.5 and obv_20_days > 0: c_o, s_o, o_msg = HEALTH_COLORS["blue"],   "底背離",   "指數走弱但量能流入 — 可能有人默默承接"
    elif abs(price_20) <= 1.5:                c_o, s_o, o_msg = HEALTH_COLORS["gray"],   "量價持平", "近20日價量皆無明顯方向"
    else:                                     c_o, s_o, o_msg = HEALTH_COLORS["green"],  "量價同步", "量能與指數方向一致，趨勢有量能背書"
    r_o = "OBV＝上漲日加量、下跌日減量的累積線。與指數背離＝量能不認同價格"

    # 4) 量能趨勢 (5d vs 20d)
    vt_value = f"{vol_trend:.1f} 倍"
    if   vol_trend >= 1.2: c_t, s_t = HEALTH_COLORS["orange"], "量能擴張"
    elif vol_trend >= 0.9: c_t, s_t = HEALTH_COLORS["green"],  "持平"
    else:                  c_t, s_t = HEALTH_COLORS["blue"],   "量能萎縮"
    r_t = "近5日均量 ÷ 近20日均量。>1.2=參與度升溫，<0.9=退潮"

    c1, c2, c3, c4 = st.columns(4)
    with c1: _render_health_metric("今日量能（成交金額）", vol_value, c_v, s_v, r_v)
    with c2: _render_health_metric("量價關係（近5日）", s_pv, c_pv, pv_msg, r_pv)
    with c3: _render_health_metric("OBV 量價背離（20日）", s_o, c_o, o_msg, r_o)
    with c4: _render_health_metric("量能趨勢", vt_value, c_t, s_t, r_t)

    st.caption(f"資料截至 {as_of.date()}（TWSE 官方收盤統計）；今日 {_fmt_turnover(last_t)}。")

    decision: list[str] = []
    if s_pv == "價漲量縮" or s_o == "頂背離":
        decision.append("上漲缺量能背書，**追高的勝算下降**——可等放量確認或拉回再評估")
    if s_pv == "價漲量增" and s_o == "量價同步":
        decision.append("上漲有量能支撐，**走勢健康**；風險主要來自價格拉伸而非缺量")
    if s_pv == "價跌量增":
        decision.append("帶量下跌代表賣壓沉重，**不宜搶接**，待量能萎縮（賣壓宣洩）再觀察")
    if s_pv == "價跌量縮" or s_o == "底背離":
        decision.append("下跌但量在縮、OBV 默默流入，**賣壓可能在減弱**，可留意止穩")
    if s_v == "爆量":
        decision.append(f"爆量（{vol_ratio:.1f} 倍均量）— 爆量常見於轉折，方向仍須價格確認")
    if not decision:
        decision.append("量價大致同步、無明顯背離 — 照原訂計畫即可，不需因量能特別調整")

    _section_insight(f"量價綜合：**{s_pv}**，OBV **{s_o}**。" + "；".join(decision) + "。")
    return {"vol_ratio": vol_ratio, "pv": s_pv, "obv": s_o, "vol_trend": vol_trend, "as_of": as_of}

def _render_chart_with_regimes(taiex: pd.Series, regimes_df: pd.DataFrame) -> None:
    """`regimes_df` passed in from upstream (Bug 13 fix: was being
    recomputed here). Insight variables now computed inline at the
    end where they're used (Bug 20)."""
    st.markdown("### 📊 加權指數 2 年走勢（含規制色塊）")

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

    # End-of-chart insight — vars computed inline at point of use (Bug 20)
    if regimes_df is not None and not regimes_df.empty:
        n_bull  = int((regimes_df["regime"] == "bull").sum())
        n_corr  = int((regimes_df["regime"] == "correction").sum())
        n_minib = int((regimes_df["regime"] == "mini_bear").sum())
        n_bigb  = int((regimes_df["regime"] == "bear").sum())
        last      = regimes_df.iloc[-1]
        cur_label = REGIME_LABELS_ZH.get(last["regime"], last["regime"])
        cur_mag   = float(last["severity"])
        # Bug 18: trading days, not calendar days
        leg_start = pd.Timestamp(last["start_date"])
        leg_end   = pd.Timestamp(last["end_date"])
        n_trading_days = int(
            ((taiex.index >= leg_start) & (taiex.index <= leg_end)).sum()
        )
        _section_insight(
            f"過去 2 年加權指數共經歷 **{n_bull} 段多頭 / {n_corr} 段小熊 / "
            f"{n_minib} 段中熊 / {n_bigb} 段大熊**。"
            f"目前處於最新的 **{cur_label}** 段（已 {n_trading_days} 交易日，振幅 {cur_mag:+.1f}%）。"
        )


def _render_summary_card(taiex: pd.Series,
                         stretch_info: dict,
                         speed_info: dict,
                         headline_info: dict | None = None) -> None:
    """Single plain-text interpretation combining all section signals.

    Receives precomputed values from upstream sections so it never
    re-derives metrics that might drift from the headline tile display.

    No alarms, no 🚨 — descriptive only. The page deliberately stops
    short of saying "buy" or "sell"; it leaves that to the reader.
    """
    st.markdown("### 📋 綜合解讀")

    # Pull TAIEX z-score directly (no fragile string match on row labels)
    tw_z: float | None = stretch_info.get("tw_z")

    # Compose state line + interpretation
    parts: list[str] = []

    # Level descriptor — use headline's precomputed dist_1y_hi if available,
    # otherwise recompute. (Bug 11: previously always recomputed and risked
    # divergence if the formula in _render_headline ever changed.)
    dist_1y = headline_info.get("dist_1y_hi") if headline_info else None
    if dist_1y is None:
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

    # Speed descriptor — drop dead .lower() (no-op on Chinese)
    tag = speed_info.get("tag")
    if tag:
        parts.append(tag)

    # TAIEX stretch descriptor — uses SAME Z_* constants as the table
    if tw_z is None:
        parts.append("加權指數 z-score 暫不可用")
    elif tw_z >= Z_EXTREME:
        parts.append(f"加權指數拉伸 z={tw_z:+.1f}（自身高位）")
    elif tw_z >= Z_ELEVATED:
        parts.append(f"加權指數拉伸 z={tw_z:+.1f}（自身偏高）")
    elif tw_z >  Z_DEPRESSED:
        parts.append(f"加權指數拉伸 z={tw_z:+.1f}（中性）")
    elif tw_z >  Z_VERY_DEPRESSED:
        parts.append(f"加權指數拉伸 z={tw_z:+.1f}（偏低）")
    else:
        parts.append(f"加權指數拉伸 z={tw_z:+.1f}（低位）")

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

    # Interpretation — uses unified Z_* and ACCEL_* constants so it cannot
    # contradict the per-metric tiles. (Bug 3 fix: previously had its own
    # 1.5 / 2.0 / 1.0 thresholds, drifting from the table.)
    accel    = speed_info.get("accel", 0) or 0
    high_stretch    = tw_z is not None and tw_z >= Z_ELEVATED
    extreme_stretch = tw_z is not None and tw_z >= Z_EXTREME
    low_stretch     = tw_z is not None and tw_z <= Z_DEPRESSED
    fast            = accel >= ACCEL_NOTABLE
    declining_fast  = accel <= ACCEL_NOTABLE_DOWN
    breadth_ok      = ns >= 2

    if tw_z is None:
        # Bug 6 fix: don't silently fall to "neutral" when data is missing
        interp = "**解讀**：加權指數 z-score 暫不可用（資料不足），無法綜合判讀。"
    elif extreme_stretch and fast and breadth_ok:
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
            "**解讀**：加權指數偏高且多市場同步拉伸，"
            "但動能未顯著加速。屬於成熟趨勢階段，**非賣出訊號**。"
        )
    elif high_stretch:
        interp = (
            "**解讀**：加權指數處於自身歷史偏高位，但**廣度未確認全球同步**，"
            "可能為單一市場現象。**非賣出訊號**。"
        )
    elif low_stretch and declining_fast:
        interp = (
            "**解讀**：拉伸低位且仍在加速下行，趨勢尚未止穩。"
            "歷史上逢低布局時機通常出現在減速之後，**非立即買入訊號**。"
        )
    elif low_stretch:
        interp = (
            "**解讀**：加權指數處於自身歷史低位，逢低布局的歷史回報通常較高，"
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
        st.error("資料庫無加權指數 (^TWII) 價格。請先執行 step3_backfill。")
        return
    taiex = taiex_df.set_index("date")["close"].dropna()

    # "As of" date banner — TZ-aware so a US-deployed server still uses TW
    # market day (Bug 15). Chinese weekday so the banner matches the page (Bug 16).
    WEEKDAY_ZH = {0: "週一", 1: "週二", 2: "週三", 3: "週四",
                  4: "週五", 5: "週六", 6: "週日"}
    as_of_date = taiex.index[-1].date()
    today      = pd.Timestamp.now(tz="Asia/Taipei").date()
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
    weekday_zh = WEEKDAY_ZH[as_of_date.weekday()]
    st.markdown(
        f"""<div style="padding:0.55rem 1rem; margin:0.4rem 0 1rem 0;
                      background:{bg_color}; border-radius:4px;
                      font-size:0.92rem; color:{text_color};">
              📅 <b>資料截至</b>　{as_of_date.strftime('%Y-%m-%d')}　({weekday_zh})　·　{freshness}
            </div>""",
        unsafe_allow_html=True,
    )

    # Compute regimes ONCE per render and pass to both sections that need it
    # (Bug 13: was being recomputed 2-3 times, ~100ms wasted each call)
    regimes_df = _compute_regimes_live(threshold_pct=4.0)

    # 1. Market Level — capture return dict so summary can reuse it (Bug 11)
    headline_info = _render_headline(taiex, regimes_df)
    st.markdown("---")

    # 2. Stretch (normalised) + breadth tally
    stretch_info = _render_stretch_normalized()
    st.markdown("---")

    # 3. Speed (momentum + volatility, combined card)
    speed_info = _render_speed_panel(taiex)
    st.markdown("---")

    # 3.5 Price-Volume health — TWSE official daily turnover + TAIEX (same-day,
    # authoritative; no Yahoo ^TWII volume lag/0 placeholder)
    _render_price_volume()
    st.markdown("---")

    # 4. Historical analogs (data mining, not a forecast signal)
    _render_historical_analogs(taiex)
    st.markdown("---")

    # 5. Chart with regime overlay
    _render_chart_with_regimes(taiex, regimes_df)
    st.markdown("---")

    # 6. Summary card — receives precomputed headline/stretch/speed values
    _render_summary_card(taiex, stretch_info, speed_info, headline_info)
