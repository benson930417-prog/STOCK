"""ETF 比較 tab — rebased % return comparison across ETFs (Chinese-only UI).

Reads ENTIRELY from the local SQLite (scripts/etf_benchmark/db.py).
No Yahoo calls at request time → tab loads instantly.

Refresh path:
    python -m scripts.etf_benchmark.step3_backfill --incremental
(or full rebuild with `--reset` on step2 + plain step3 run)
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from scripts.etf_benchmark import db
from scripts.etf_benchmark.step6_regimes import zigzag_pivots, classify_leg


FUND_TYPE_LABELS = {
    "passive_equity": "被動股票型",
    "active_equity":  "主動股票型",
    "bond":           "債券型",
    "commodity":      "商品期貨型",
    "leveraged":      "槓桿/反向",
    "other":          "其他",
}

CORPORATE_ACTION_WARNINGS = {
    "0052": "曾有分割 / 資本事件，圖表保留，但該段期間的報酬線請視為需要人工解讀",
}

REGIME_COLORS = {
    "bull":       "rgba(46,  204, 113, 0.28)",   # emerald — stands out on dark theme
    "correction": "rgba(241, 196,  15, 0.50)",   # sunflower yellow
    "mini_bear":  "rgba(230, 126,  34, 0.60)",   # carrot orange
    "bear":       "rgba(231,  76,  60, 0.65)",   # alizarin red
}
REGIME_LABELS_ZH = {
    "bull":       "多頭",
    "correction": "小熊",
    "mini_bear":  "中熊",
    "bear":       "大熊",
}
COMMON_PRICE_ADJUSTMENT_RATIOS = (2, 3, 4, 5, 6, 7, 10)
PRICE_ADJUSTMENT_TOLERANCE = 0.08

# ── 綜合評分 (fair, regime-neutral composite) constants ──────────────────────
TRADING_DAYS_PER_YEAR = 252.0
SCORE_RISK_FREE_ANNUAL = 0.0        # MAR for Sortino; raise once a TW rf series is ingested
SCORE_MIN_DAYS         = 20         # < this → 資料不足, excluded from ranking
SCORE_FULL_CONF_DAYS   = 252        # ≥ this → full confidence (no shrinkage toward median)
SCORE_R2_MIN           = 0.20       # benchmark must explain ≥20% of variance to score asymmetry
SCORE_RET_CLIP         = 0.50       # winsorise daily returns (guards split / bad-print artefacts)
TW_EQUITY_FUND_TYPES   = {"passive_equity", "active_equity", "leveraged"}

SCORE_PILLAR_KEYS   = ("efficiency", "asymmetry", "consistency")
SCORE_PILLAR_LABELS = {"efficiency": "效率", "asymmetry": "不對稱", "consistency": "一致性"}
SCORE_PILLAR_MEMBERS = {
    "efficiency":  ["sortino", "calmar"],
    "asymmetry":   ["capture_spread"],
    "consistency": ["batting", "tracking_err", "ann_vol"],
}
# direction per metric: True = higher is better
SCORE_METRIC_DIRECTION = {
    "sortino": True, "calmar": True, "capture_spread": True,
    "batting": True, "tracking_err": False, "ann_vol": False,
}


def _group_consecutive_missing(missing_dates: list, ref_dates: list) -> list[tuple]:
    if not missing_dates:
        return []
    ref_idx = {d: i for i, d in enumerate(ref_dates)}
    miss = sorted(missing_dates)
    ranges: list[tuple] = []
    start = prev = miss[0]
    for d in miss[1:]:
        if ref_idx.get(prev) is not None and ref_idx.get(d) == ref_idx[prev] + 1:
            prev = d
        else:
            ranges.append((start, prev))
            start = prev = d
    ranges.append((start, prev))
    return ranges


def _fmt_range(s, e) -> str:
    s_ = pd.Timestamp(s).strftime("%Y-%m-%d")
    e_ = pd.Timestamp(e).strftime("%Y-%m-%d")
    return s_ if s_ == e_ else f"{s_} ~ {e_}"


def _fmt_ntd(amount: float) -> str:
    if amount <= 0:
        return "不篩選"
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:g} 億"
    if amount >= 10_000_000:
        return f"{amount / 10_000_000:g} 千萬"
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:g} 百萬"
    return f"{amount:,.0f}"


def _as_date_or_none(value):
    if value is None or pd.isna(value) or value == "":
        return None
    return pd.Timestamp(value).date()


def _humanize_db_mtime(summary: dict) -> str:
    epoch = summary.get("db_mtime_epoch")
    if not epoch:
        return summary.get("db_mtime", "未知")

    sec = max(0, int(datetime.now().timestamp() - float(epoch)))
    if sec < 60:
        return f"{sec} 秒前"
    if sec < 3600:
        return f"{sec // 60} 分鐘前"
    if sec < 86400:
        return f"{sec // 3600} 小時前"
    return f"{sec // 86400} 天前"


def _nearest_price_adjustment_ratio(close_ratio: float) -> float | None:
    if close_ratio <= 0:
        return None
    candidates = list(COMMON_PRICE_ADJUSTMENT_RATIOS) + [
        1.0 / ratio for ratio in COMMON_PRICE_ADJUSTMENT_RATIOS
    ]
    nearest = min(candidates, key=lambda ratio: abs(close_ratio / ratio - 1.0))
    if abs(close_ratio / nearest - 1.0) <= PRICE_ADJUSTMENT_TOLERANCE:
        return nearest
    return None


def _format_adjustment_ratio(ratio: float) -> str:
    if ratio >= 1:
        return f"{ratio:.0f}:1"
    return f"1:{1.0 / ratio:.0f}"


def _period_return_pct(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float | None, int]:
    """Return (return_pct, n_trading_days) for a price series between start and end inclusive."""
    if series is None or series.empty:
        return None, 0
    mask = (series.index >= start) & (series.index <= end)
    sub = series[mask]
    if len(sub) < 2:
        return None, len(sub)
    p0, p1 = float(sub.iloc[0]), float(sub.iloc[-1])
    if p0 <= 0:
        return None, len(sub)
    return (p1 - p0) / p0 * 100.0, len(sub)


def _weighted_avg(values: list[float], weights: list[int]) -> float | None:
    """Trading-day-weighted average: Σ(v_i × w_i) / Σ(w_i). None if no weight."""
    if not values or not weights:
        return None
    total_w = sum(weights)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _build_capture_table(
    per_ticker_prices: dict[str, pd.Series],
    bench_series: pd.Series,
    regimes_df: pd.DataFrame,
    baseline_date: pd.Timestamp,
    today_ts: pd.Timestamp,
    etf_universe: pd.DataFrame,
    bench_label: str = "加權指數",
    exclude_tickers: tuple[str, ...] = ("^TWII", "^TWOII"),
) -> tuple[pd.DataFrame, dict]:
    """Per-ETF benchmark table — one row per ETF, columns are regime cumulative
    returns + up/down capture vs the benchmark. Returns (df, benchmark_summary).
    """
    # Benchmark return per regime period (compute once, reused for every ETF)
    bench_per_period: dict[tuple, dict] = {}
    bench_by_regime: dict[str, dict[str, list]] = {
        r: {"rets": [], "days": []} for r in ("bull", "correction", "mini_bear", "bear")
    }
    for _, rrow in regimes_df.iterrows():
        s = pd.Timestamp(rrow["start_date"])
        e = pd.Timestamp(rrow["end_date"])
        if e < baseline_date or s > today_ts:
            continue
        bret, n_days = _period_return_pct(bench_series, s, e)
        if bret is None:
            continue
        regime = rrow["regime"]
        bench_per_period[(s, e)] = {"ret": bret, "regime": regime, "n_days": n_days}
        if regime in bench_by_regime:
            bench_by_regime[regime]["rets"].append(bret)
            bench_by_regime[regime]["days"].append(n_days)

    bench_avg_by_regime = {
        r: _weighted_avg(v["rets"], v["days"]) for r, v in bench_by_regime.items()
    }

    rows: list[dict] = []
    for ticker, price_series in per_ticker_prices.items():
        if ticker in exclude_tickers or price_series is None or price_series.empty:
            continue
        urow = etf_universe[etf_universe["ticker"] == ticker]
        name = urow.iloc[0]["name"] if not urow.empty else ticker

        # Per-regime: collect (return, n_days) for weighted average
        per_regime: dict[str, dict[str, list]] = {
            r: {"rets": [], "days": []} for r in ("bull", "correction", "mini_bear", "bear")
        }
        # Aligned lists for capture math — only periods where this ETF has data
        bull_fund_r:  list[float] = []
        bull_bench_r: list[float] = []
        bull_days:    list[int]   = []
        down_fund_r:  list[float] = []
        down_bench_r: list[float] = []
        down_days:    list[int]   = []

        for (s, e), bench_info in bench_per_period.items():
            fund_ret, n_days = _period_return_pct(price_series, s, e)
            if fund_ret is None:
                continue
            regime = bench_info["regime"]
            if regime in per_regime:
                per_regime[regime]["rets"].append(fund_ret)
                per_regime[regime]["days"].append(n_days)
            if regime == "bull":
                bull_fund_r.append(fund_ret)
                bull_bench_r.append(bench_info["ret"])
                bull_days.append(n_days)
            else:
                down_fund_r.append(fund_ret)
                down_bench_r.append(bench_info["ret"])
                down_days.append(n_days)

        bull_fund_avg  = _weighted_avg(bull_fund_r,  bull_days)
        bull_bench_avg = _weighted_avg(bull_bench_r, bull_days)
        down_fund_avg  = _weighted_avg(down_fund_r,  down_days)
        down_bench_avg = _weighted_avg(down_bench_r, down_days)

        up_capture = (
            bull_fund_avg / bull_bench_avg * 100.0
            if bull_fund_avg is not None and bull_bench_avg is not None
               and abs(bull_bench_avg) > 0.01
            else None
        )
        down_capture = (
            down_fund_avg / down_bench_avg * 100.0
            if down_fund_avg is not None and down_bench_avg is not None
               and abs(down_bench_avg) > 0.01
            else None
        )
        capture_ratio = (
            up_capture / down_capture
            if up_capture is not None and down_capture is not None
               and abs(down_capture) > 0.01
            else None
        )

        rows.append({
            "代號": ticker,
            "名稱": name,
            "多頭平均 %": _weighted_avg(per_regime["bull"]["rets"],       per_regime["bull"]["days"]),
            "小熊平均 %": _weighted_avg(per_regime["correction"]["rets"], per_regime["correction"]["days"]),
            "中熊平均 %": _weighted_avg(per_regime["mini_bear"]["rets"],  per_regime["mini_bear"]["days"]),
            "大熊平均 %": _weighted_avg(per_regime["bear"]["rets"],       per_regime["bear"]["days"]),
            "上漲捕獲 %":   up_capture,
            "下跌捕獲 %":   down_capture,
            "捕獲比":       capture_ratio,
        })

    df = pd.DataFrame(rows)
    bench_summary = {
        "label":      bench_label,
        "by_regime":  bench_avg_by_regime,
        "n_periods":  {r: len(v["rets"]) for r, v in bench_by_regime.items()},
        "total_days": {r: sum(v["days"]) for r, v in bench_by_regime.items()},
    }
    return df, bench_summary


def _compute_regimes_live(threshold_pct: float) -> pd.DataFrame:
    """Run ZigZag on the latest TAIEX prices in the DB. Returns the same
    schema as db.get_regimes() so the rest of the tab is agnostic."""
    taiex = db.get_prices("^TWII")
    if taiex.empty or len(taiex) < 2:
        return pd.DataFrame()
    prices = taiex["close"].to_numpy(dtype=float)
    dates  = pd.to_datetime(taiex["date"]).tolist()
    pivot_idxs = zigzag_pivots(prices, threshold_pct)
    rows: list[dict] = []
    for a, b in zip(pivot_idxs[:-1], pivot_idxs[1:]):
        p0, p1 = float(prices[a]), float(prices[b])
        if p0 <= 0:
            continue
        mag = (p1 - p0) / p0 * 100.0
        rows.append({
            "start_date": pd.Timestamp(dates[a]),
            "end_date":   pd.Timestamp(dates[b]),
            "regime":     classify_leg(mag, threshold_pct),
            "severity":   round(mag, 2),
            "notes":      f"{b - a + 1} trading days",
        })
    return pd.DataFrame(rows)


def _corporate_action_warnings(ticker: str, name: str, prices: pd.DataFrame) -> list[str]:
    details: list[str] = []
    warning = CORPORATE_ACTION_WARNINGS.get(ticker)

    if len(prices) >= 2:
        prev_close = prices["close"].shift(1)
        for row in prices.assign(prev_close=prev_close).itertuples(index=False):
            if pd.isna(row.prev_close) or row.prev_close <= 0 or row.close <= 0:
                continue
            matched_ratio = _nearest_price_adjustment_ratio(float(row.close) / float(row.prev_close))
            if matched_ratio is None:
                continue
            event_date = pd.Timestamp(row.date).date().isoformat()
            details.append(
                f"{event_date} 偵測到約 {_format_adjustment_ratio(matched_ratio)} 的價格調整"
                f"（收盤 {float(row.prev_close):.2f} → {float(row.close):.2f}）"
            )

    if not warning and not details:
        return []

    parts = [warning] if warning else ["偵測到可能的價格調整，該段期間請用人工判斷。"]
    parts.extend(list(dict.fromkeys(details)))
    return [f"**{ticker} {name}**：{'；'.join(parts)}。"]


# ── 綜合評分 helpers — all metrics are direction-neutral by construction ─────
def _score_benchmark_for(urow: pd.Series) -> str | None:
    """Category-appropriate reference index for capture / tracking math.

    Conservative: only re-route to a US index on an explicit name match; TW-equity
    → ^TWII; non-equity (bond / commodity / other) → None. A None benchmark, or a
    later R² < SCORE_R2_MIN, drops the benchmark-relative metrics and reweights the
    remaining pillars — so a bond ETF is never judged on TAIEX behaviour.
    """
    blob = " ".join(
        str(urow.get(c, "") or "")
        for c in ("name", "full_name", "en_name", "tracked_index")
    ).upper()
    if "那斯達克" in blob or "NASDAQ" in blob:
        return "^IXIC"
    if "標普" in blob or "S&P" in blob or "SP500" in blob:
        return "^GSPC"
    if "道瓊" in blob or "DOW JONES" in blob:
        return "^DJI"
    if urow.get("fund_type") in TW_EQUITY_FUND_TYPES:
        return "^TWII"
    return None


def _daily_returns(price: pd.Series) -> pd.Series:
    r = price.astype(float).pct_change().dropna()
    return r.clip(lower=-SCORE_RET_CLIP, upper=SCORE_RET_CLIP)


def _ann_return(price: pd.Series, n_periods: int) -> float | None:
    if len(price) < 2 or n_periods <= 0:
        return None
    total = float(price.iloc[-1]) / float(price.iloc[0])
    if total <= 0:
        return None
    years = n_periods / TRADING_DAYS_PER_YEAR
    return total ** (1.0 / years) - 1.0 if years > 0 else None


def _max_drawdown(price: pd.Series) -> float:
    peak = price.cummax()
    return float((price / peak - 1.0).min())


def _downside_dev(returns: pd.Series, mar_daily: float = 0.0) -> float | None:
    """Annualised downside deviation vs a daily MAR (target-semivariance, N in denom)."""
    if returns.empty:
        return None
    below = (returns - mar_daily).clip(upper=0.0)
    val = float((below ** 2).mean()) ** 0.5 * (TRADING_DAYS_PER_YEAR ** 0.5)
    return val if val > 0 else None


def _capture_stats(fund_r: pd.Series, bench_r: pd.Series) -> dict | None:
    """Daily up/down capture, R², batting average and tracking error vs benchmark.

    Up- and down-capture are each computed only over their own side, so the score's
    up/down combination is naturally 50/50 weighted — the sample's bull/bear mix
    does not tilt it.
    """
    joined = pd.concat(
        [fund_r.rename("f"), bench_r.rename("b")], axis=1, join="inner"
    ).dropna()
    if len(joined) < 10:
        return None
    f, b = joined["f"], joined["b"]
    up, dn = b > 0, b < 0

    def _cum(x: pd.Series) -> float:
        return float((1.0 + x).prod() - 1.0)

    up_b, dn_b = _cum(b[up]), _cum(b[dn])
    up_cap = (_cum(f[up]) / up_b) if abs(up_b) > 1e-6 else None
    dn_cap = (_cum(f[dn]) / dn_b) if abs(dn_b) > 1e-6 else None
    r2 = float(f.corr(b) ** 2) if (f.std(ddof=1) > 0 and b.std(ddof=1) > 0) else None
    return {
        "up_cap": up_cap, "dn_cap": dn_cap, "r2": r2,
        "batting": float((f > b).mean()),
        "tracking_err": float((f - b).std(ddof=1) * (TRADING_DAYS_PER_YEAR ** 0.5)),
        "n": len(joined),
    }


def _rank_0_100(series: pd.Series, higher_better: bool) -> pd.Series:
    """Rank non-null values into 0–100 within the current selection (best = 100).
    A lone value scores 50 (neutral — no peer to compare against)."""
    out = pd.Series(index=series.index, dtype=float)
    vals = series.dropna()
    if vals.empty:
        return out
    if len(vals) == 1:
        out.loc[vals.index[0]] = 50.0
        return out
    rr = vals.rank(method="average", ascending=higher_better)
    out.loc[vals.index] = (rr - 1.0) / (len(vals) - 1.0) * 100.0
    return out


def _conf_label(n_days: int) -> str:
    if n_days >= SCORE_FULL_CONF_DAYS:
        return "高"
    if n_days >= 120:
        return "中"
    if n_days >= SCORE_MIN_DAYS:
        return "低"
    return "資料不足"


def _stars(v: float | None) -> str:
    if v is None or pd.isna(v):
        return ""
    n = 1 + int(min(4, max(0, v // 20)))
    return "★" * n + "☆" * (5 - n)


def _build_score_table(
    selected_tickers: list[str],
    etf_universe: pd.DataFrame,
    baseline_date: pd.Timestamp,
    weights: dict[str, float],
    as_of: pd.Timestamp | None = None,
    shrink: bool = True,
) -> pd.DataFrame:
    """One row per selected ETF with pillar sub-scores + the fair composite.

    Computed on adj_close (total return) regardless of the chart's display toggle,
    so high-dividend funds are compared fairly and split artefacts are avoided.

    `as_of` caps the data at a past date so the very same scorer can reproduce the
    score "as it would have looked then" — used by the history backfill and step7.
    """
    end = pd.Timestamp(as_of) if as_of is not None else None
    bench_cache: dict[str, pd.Series | None] = {}

    def _bench_returns(idx: str | None) -> pd.Series | None:
        if idx is None:
            return None
        if idx not in bench_cache:
            bdf = db.get_prices(idx, start=baseline_date, end=end)
            if bdf.empty:
                bench_cache[idx] = None
            else:
                s = bdf["close"].astype(float)
                s.index = pd.to_datetime(bdf["date"])
                bench_cache[idx] = _daily_returns(s)
        return bench_cache[idx]

    recs: list[dict] = []
    for t in selected_tickers:
        urow_df = etf_universe[etf_universe["ticker"] == t]
        urow = urow_df.iloc[0] if not urow_df.empty else pd.Series({"ticker": t})
        ftype = urow.get("fund_type", "other")
        rec = {
            "代號": t, "名稱": urow.get("name", t),
            "類別": FUND_TYPE_LABELS.get(ftype, ftype),
            "n_days": 0, "_insufficient": True,
            "benchmark": None, "r2": None,
        }

        df = db.get_prices(t, start=baseline_date, end=end)
        if df.empty:
            recs.append(rec)
            continue
        price = df["adj_close"].fillna(df["close"]).astype(float)
        price.index = pd.to_datetime(df["date"])
        rets = _daily_returns(price)
        rec["n_days"] = len(rets)
        if len(rets) < SCORE_MIN_DAYS:
            recs.append(rec)
            continue
        rec["_insufficient"] = False

        # Efficiency (risk-adjusted, benchmark-free)
        ann_ret = _ann_return(price, len(rets))
        max_dd  = _max_drawdown(price)
        dd_dev  = _downside_dev(rets)
        if ann_ret is not None and dd_dev:
            rec["sortino"] = (ann_ret - SCORE_RISK_FREE_ANNUAL) / dd_dev
        if ann_ret is not None and max_dd < 0:
            rec["calmar"] = ann_ret / abs(max_dd)
        rec["ann_vol"] = float(rets.std(ddof=1) * (TRADING_DAYS_PER_YEAR ** 0.5))

        # Asymmetry + consistency (benchmark-relative; gated on R²)
        bench_idx = _score_benchmark_for(urow)
        rec["benchmark"] = bench_idx
        cap = _capture_stats(rets, _bench_returns(bench_idx)) if bench_idx else None
        if cap:
            rec["r2"] = cap["r2"]
            if cap["r2"] is not None and cap["r2"] >= SCORE_R2_MIN:
                rec["batting"] = cap["batting"]
                rec["tracking_err"] = cap["tracking_err"]
                if cap["up_cap"] is not None and cap["dn_cap"] is not None:
                    rec["capture_spread"] = cap["up_cap"] - cap["dn_cap"]
        recs.append(rec)

    score_df = pd.DataFrame(recs).set_index("代號", drop=False)
    rankable = score_df[~score_df["_insufficient"]]

    # Metric → 0–100 within the rankable selection
    metric_scores: dict[str, pd.Series] = {}
    for m, higher_better in SCORE_METRIC_DIRECTION.items():
        if m in rankable.columns:
            metric_scores[m] = _rank_0_100(rankable[m], higher_better)

    # Pillar = mean of its available member scores
    for pk, members in SCORE_PILLAR_MEMBERS.items():
        cols = [metric_scores[m] for m in members if m in metric_scores]
        if not cols:
            continue
        pillar = pd.concat(cols, axis=1).mean(axis=1, skipna=True)
        score_df.loc[pillar.index, SCORE_PILLAR_LABELS[pk]] = pillar

    # Composite = weighted mean over available pillars, then confidence shrinkage
    pillar_cols = [SCORE_PILLAR_LABELS[k] for k in SCORE_PILLAR_KEYS]
    wmap = {SCORE_PILLAR_LABELS[k]: float(weights.get(k, 1.0)) for k in SCORE_PILLAR_KEYS}
    comp, conf, completeness = {}, {}, {}
    for tkr, row in score_df.iterrows():
        if row["_insufficient"]:
            continue
        num = den = 0.0
        have = 0
        for pc in pillar_cols:
            v = row.get(pc)
            if pd.notna(v):
                num += v * wmap[pc]
                den += wmap[pc]
                have += 1
        if den <= 0:
            continue
        raw = num / den
        c = min(1.0, max(0.0, row["n_days"] / SCORE_FULL_CONF_DAYS))
        # Snapshot (shrink=True): pull new funds toward the median so a thin sample
        # can't top the table. Trend (shrink=False): keep the raw standing so a time
        # series shows real ranking change, not the mechanical confidence ramp.
        comp[tkr] = (50.0 + (raw - 50.0) * c) if shrink else raw
        conf[tkr] = c
        completeness[tkr] = have / len(pillar_cols)
    score_df["綜合評分"]      = pd.Series(comp)
    score_df["_conf"]         = pd.Series(conf)
    score_df["_completeness"] = pd.Series(completeness)

    # Within-category rank among the selection (only where a peer exists)
    score_df["同類排名"] = ""
    scored = score_df[score_df["綜合評分"].notna()]
    for _, grp in scored.groupby("類別"):
        order = grp["綜合評分"].rank(ascending=False, method="min")
        for tkr in grp.index:
            score_df.loc[tkr, "同類排名"] = f"{int(order[tkr])}/{len(grp)}"
    return score_df


def _history_composite(df: pd.DataFrame, w_eff: float, w_asy: float, w_con: float) -> pd.Series:
    """Weighted mean of the stored pillar sub-scores per row, over whichever
    pillars are present (so a NaN 不對稱 reweights to 效率+一致性)."""
    wt = {"eff": w_eff, "asy": w_asy, "con": w_con}
    vals = df[["eff", "asy", "con"]]
    mask = vals.notna()
    wdf = pd.DataFrame({c: wt[c] for c in ("eff", "asy", "con")}, index=df.index)
    num = (vals.fillna(0.0) * wdf * mask).sum(axis=1)
    den = (wdf * mask).sum(axis=1)
    return num / den.where(den > 0)


def render_etf_compare_tab(*, lang=None, T=None, DATA_DIR=None,
                           get_market_data=None, add_zero_line=None,
                           hex_to_rgba=None, PROFIT_COLOR=None, LOSS_COLOR=None):
    st.subheader("ETF 比較")

    summary = db.db_summary()
    if not summary.get("db_exists"):
        st.error(
            "ETF 資料庫尚未建立。請執行：\n\n"
            "```\npython -m scripts.etf_benchmark.step2_schema --reset\n"
            "python -m scripts.etf_benchmark.step3_backfill\n```"
        )
        return

    st.caption(
        f"📦 資料庫：{summary['n_with_px']} 檔有價、共 {summary['n_prices']:,} 筆日資料、"
        f"{summary['n_dividends']} 筆配息 ｜ 資料區間 {summary['date_min']} → {summary['date_max']} ｜ "
        f"最後更新 {_humanize_db_mtime(summary)}"
    )

    universe = db.get_universe()
    # ETFs only (exclude reference indices) for the picker
    etf_universe = universe[universe["market"].isin(["TWSE", "TPEx"])].copy()
    etf_universe = etf_universe[etf_universe["has_prices"]]   # drop the 30 empties
    etf_universe["display"] = etf_universe["ticker"] + "  " + etf_universe["name"]

    # ─────────── 全域流動性篩選 ───────────
    with st.container(border=True):
        st.markdown("**全域流動性篩選**")
        TURNOVER_OPTIONS = [
            0, 1_000_000, 5_000_000, 10_000_000, 50_000_000,
            100_000_000, 500_000_000, 1_000_000_000,
        ]
        min_turnover = st.select_slider(
            "近三個月日均成交金額下限（新台幣 / 日）",
            options=TURNOVER_OPTIONS,
            value=0,
            format_func=_fmt_ntd,
            key="etfc_min_turnover",
        )

    if min_turnover > 0:
        to_map = db.get_avg_turnover_map()
        etf_universe["avg_turnover_3mo"] = etf_universe["ticker"].map(to_map).fillna(0.0)
        before_n = len(etf_universe)
        liquid   = etf_universe[etf_universe["avg_turnover_3mo"] >= min_turnover]
        no_data  = etf_universe[etf_universe["avg_turnover_3mo"] == 0.0]
        excluded = etf_universe[
            (etf_universe["avg_turnover_3mo"] > 0)
            & (etf_universe["avg_turnover_3mo"] < min_turnover)
        ]
        etf_universe = pd.concat([liquid, no_data]).drop_duplicates(subset=["ticker"])
        st.info(
            f"**流動性篩選摘要**　下限：{_fmt_ntd(min_turnover)} 元 / 日　｜　"
            f"保留 {len(etf_universe)} / {before_n} 檔　"
            f"（達標 {len(liquid)}；無資料 {len(no_data)} 保留；排除 {len(excluded)}）"
        )
    else:
        st.caption("（流動性篩選未啟用，顯示全部 ETF）")

    # ─────────── 各類別獨立多選 ───────────
    TYPE_DEFAULTS = {"passive_equity": ["0050"], "active_equity": ["00981A"]}
    selected_tickers: list[str] = []
    st.markdown("**選擇要比較的 ETF**　(總計上限 10 檔)")

    for ftype in FUND_TYPE_LABELS.keys():
        type_rows = etf_universe[etf_universe["fund_type"] == ftype]
        if type_rows.empty:
            continue
        label = FUND_TYPE_LABELS.get(ftype, ftype)
        d2t = dict(zip(type_rows["display"], type_rows["ticker"]))
        options = type_rows["display"].tolist()
        default_tickers = TYPE_DEFAULTS.get(ftype, [])
        default_picks = [d for d in options
                         if any(d.startswith(t + "  ") for t in default_tickers)]
        picked = st.multiselect(
            f"{label}　({len(options)} 檔可選)",
            options=options,
            default=default_picks,
            key=f"etfc_pick_{ftype}",
        )
        selected_tickers.extend(d2t[d] for d in picked)

    if len(selected_tickers) > 10:
        st.warning(f"已選 {len(selected_tickers)} 檔，超過上限，僅取前 10 檔繪圖。")
        selected_tickers = selected_tickers[:10]

    # ─────────── 起始點設定 ───────────
    if "etfc_baseline" not in st.session_state:
        st.session_state["etfc_baseline"] = (
            pd.Timestamp.now().normalize() - pd.DateOffset(years=1)
        ).date()

    max_d = pd.Timestamp(summary["date_max"]).date()
    min_d = pd.Timestamp(summary["date_min"]).date()

    def _clamp_baseline(value):
        value = pd.Timestamp(value).date()
        return min(max(value, min_d), max_d)

    st.session_state["etfc_baseline"] = _clamp_baseline(st.session_state["etfc_baseline"])

    def _set_bday(n):
        st.session_state["etfc_baseline"] = _clamp_baseline(pd.Timestamp(max_d) - pd.offsets.BDay(n))

    def _set_offset(months=0, years=0):
        st.session_state["etfc_baseline"] = _clamp_baseline(
            pd.Timestamp(max_d) - pd.DateOffset(months=months, years=years)
        )

    def _set_ytd():
        st.session_state["etfc_baseline"] = _clamp_baseline(pd.Timestamp(year=max_d.year, month=1, day=1))

    with st.container(border=True):
        st.markdown("**起始點設定**")
        c_date, c_fast, c_ref = st.columns([2, 4, 2])
        with c_date:
            st.date_input(" ", min_value=min_d, max_value=max_d,
                          key="etfc_baseline", label_visibility="collapsed")
        with c_fast:
            r1 = st.columns(4)
            r1[0].button("1D", on_click=_set_bday,   args=(1,),             key="etfc_b1d",  use_container_width=True)
            r1[1].button("5D", on_click=_set_bday,   args=(5,),             key="etfc_b5d",  use_container_width=True)
            r1[2].button("1M", on_click=_set_offset, kwargs={"months": 1},  key="etfc_b1m",  use_container_width=True)
            r1[3].button("3M", on_click=_set_offset, kwargs={"months": 3},  key="etfc_b3m",  use_container_width=True)
            r2 = st.columns(4)
            r2[0].button("6M",  on_click=_set_offset, kwargs={"months": 6}, key="etfc_b6m",  use_container_width=True)
            r2[1].button("YTD", on_click=_set_ytd,                          key="etfc_bytd", use_container_width=True)
            r2[2].button("1Y",  on_click=_set_offset, kwargs={"years": 1},  key="etfc_b1y",  use_container_width=True)
            r2[3].button("2Y",  on_click=_set_offset, kwargs={"years": 2},  key="etfc_b2y",  use_container_width=True)
        with c_ref:
            show_taiex   = st.checkbox("顯示加權指數", value=True,  key="etfc_show_taiex")
            show_otc     = st.checkbox("顯示櫃買指數", value=False, key="etfc_show_otc")
            show_regimes = st.checkbox("顯示市場區間", value=True,  key="etfc_show_regimes",
                                       help="在圖上疊加多頭 / 小熊 / 中熊 / 大熊色塊（以加權指數擺動偵測計算）。")
            use_adj      = st.checkbox("配息還原 (adj close)", value=True,
                                       key="etfc_use_adj",
                                       help="勾起：用 Yahoo 的 adj_close 算報酬率（公平比較高股息）。"
                                            "取消：用原始收盤價（高股息會被低估）。")

    if show_regimes:
        with st.container(border=True):
            zigzag_threshold = st.slider(
                "市場區間敏感度（擺動反轉門檻 %）",
                min_value=3.0, max_value=10.0, value=4.0, step=0.5,
                key="etfc_zigzag_threshold",
                help="擺動偵測演算法用這個百分比認定一次「擺動轉折」。"
                     "門檻越小越敏感（小波動也算一段），越大越乾淨（只看大方向）。"
                     "預設 4%；學術慣例為 5%。",
            )
    else:
        zigzag_threshold = 4.0

    baseline_date = pd.Timestamp(st.session_state["etfc_baseline"])
    today_ts = pd.Timestamp(summary["date_max"])

    # Warn if the selected window is shorter than 3 months — too few regime
    # legs for capture ratios to be statistically meaningful.
    _three_months_ago = today_ts - pd.DateOffset(months=3)
    if baseline_date > _three_months_ago:
        st.warning(
            "⚠️ **樣本太短**：選擇的區間不足 3 個月，市場區間擺動段數過少，"
            "**捕獲比僅供參考、勿過度解讀**。建議至少選 3M 以上。"
        )

    # ─────────── 抓 DB + 畫圖 ───────────
    fig = go.Figure()
    y_max, y_min = 0.0, 0.0
    palette = px.colors.qualitative.Plotly + px.colors.qualitative.Vivid
    line_rows: list[dict] = []
    per_ticker_dates: dict[str, list] = {}
    per_ticker_prices: dict[str, pd.Series] = {}   # price series keyed by ticker, for regime stats
    corporate_action_warnings: list[str] = []

    def _add_line(ticker, name, color, dash="solid", record=True, force_raw=False, status_override=None):
        nonlocal y_max, y_min
        df = db.get_prices(ticker, start=baseline_date)
        if df.empty:
            if record:
                line_rows.append({
                    "ticker": ticker, "name": name, "status": "❌ 資料庫無此 ticker",
                    "start_date": None, "start_close": None,
                    "end_date": None, "end_close": None,
                    "return_pct": None, "max_dd_pct": None, "n_points": 0,
                })
                per_ticker_dates[ticker] = []
            return

        # Price series: adj_close (還原) or raw close, per toggle
        if use_adj and not force_raw:
            price = df["adj_close"].fillna(df["close"])
            if db.get_dividends(ticker).empty:
                status_label = "無配息資料"
            else:
                status_label = "✅ 配息還原"
        else:
            price = df["close"]
            status_label = "📊 原始收盤"
        if status_override:
            status_label = status_override
        base = float(price.iloc[0])
        if base <= 0:
            return
        pct = (price - base) / base * 100.0
        peak = price.cummax()
        dd = (price - peak) / peak * 100.0
        max_dd = float(dd.min())

        fig.add_trace(go.Scatter(
            x=df["date"], y=pct,
            mode="lines+markers",
            marker=dict(size=4),
            name=f"{ticker} {name}",
            line=dict(color=color, width=2, dash=dash),
            hovertemplate=f"{ticker} {name}: %{{y:.2f}}%<extra></extra>",
        ))

        y_max = max(y_max, float(pct.max()))
        y_min = min(y_min, float(pct.min()))
        per_ticker_dates[ticker] = sorted(df["date"].tolist())
        if record:
            # Store the price series with date as index for per-regime stats
            price_indexed = price.copy()
            price_indexed.index = pd.to_datetime(df["date"])
            per_ticker_prices[ticker] = price_indexed

        if record:
            line_rows.append({
                "ticker":      ticker,
                "name":        name,
                "status":      status_label,
                "start_date":  df["date"].iloc[0].date().isoformat(),
                "start_close": round(float(price.iloc[0]), 4),
                "end_date":    df["date"].iloc[-1].date().isoformat(),
                "end_close":   round(float(price.iloc[-1]), 4),
                "return_pct":  round(float(pct.iloc[-1]), 2),
                "max_dd_pct":  round(max_dd, 2),
                "n_points":    int(len(df)),
            })

    # User-selected ETFs
    for i, t in enumerate(selected_tickers):
        urow = etf_universe[etf_universe["ticker"] == t].iloc[0]
        corporate_action_warnings.extend(
            _corporate_action_warnings(t, urow["name"], db.get_prices(t))
        )
        _add_line(t, urow["name"], palette[i % len(palette)], dash="solid")

    # TAIEX — always fetched in background as gap-reference; chart-drawn only if opted-in
    if show_taiex:
        _add_line(
            "^TWII", "加權指數", "rgba(200,200,200,0.85)",
            dash="dash", force_raw=True, status_override="📈 參考指數"
        )
    else:
        taiex_df = db.get_prices("^TWII", start=baseline_date)
        if not taiex_df.empty:
            per_ticker_dates["^TWII"] = sorted(taiex_df["date"].tolist())

    # OTC index — only when opted-in; not used as reference
    if show_otc:
        _add_line(
            "^TWOII", "櫃買指數", "rgba(255,200,100,0.85)",
            dash="dash", force_raw=True, status_override="📈 參考指數"
        )

    # ─────────── Regime background overlays ───────────
    regimes_df = pd.DataFrame()
    if show_regimes:
        regimes_df = _compute_regimes_live(zigzag_threshold)
        for _, row in regimes_df.iterrows():
            s = pd.Timestamp(row["start_date"])
            e = pd.Timestamp(row["end_date"])
            if e < baseline_date or s > today_ts:
                continue
            color = REGIME_COLORS.get(row["regime"], "rgba(128,128,128,0.08)")
            x0 = max(s, baseline_date)
            x1 = min(e, today_ts)
            label = REGIME_LABELS_ZH.get(row["regime"], row["regime"])
            show_label = (x1 - x0).days >= 15
            fig.add_vrect(
                x0=x0, x1=x1,
                fillcolor=color, layer="below", line_width=0,
                annotation_text=label if show_label else "",
                annotation_position="top left",
                annotation_font=dict(size=11, color="rgba(240,240,240,0.95)"),
            )

    if not fig.data:
        st.info("請至少選擇一檔 ETF。")
        return

    rng = y_max - y_min if (y_max - y_min) > 0 else 10.0
    pad = rng * 0.1
    fig.update_layout(
        xaxis=dict(
            title="", tickformat="%m/%d",
            showgrid=True, gridcolor="rgba(255,255,255,0.08)",
            range=[baseline_date, max(today_ts, baseline_date)],
        ),
        yaxis=dict(
            title=("報酬率 % (含配息還原)" if use_adj else "報酬率 % (原始收盤)"),
            showgrid=True, gridcolor="rgba(255,255,255,0.08)",
            tickformat=".1f", ticksuffix="%",
            range=[y_min - pad, y_max + pad],
        ),
        height=460,
        margin=dict(l=10, r=20, t=30, b=10),
        legend=dict(x=0.01, y=0.99, xanchor="left", yanchor="top",
                    bgcolor="rgba(0,0,0,0.5)"),
        hovermode="x unified",
    )
    if add_zero_line:
        add_zero_line(fig, axis="y", color="#A9B1BD", width=2, dash="dash")
    st.plotly_chart(fig, width="stretch")

    # ─────────── 綜合評分排名（公平、與市場多空方向無關）───────────
    if selected_tickers:
        tab_rank, tab_hist = st.tabs(["🏆 綜合評分排名", "📈 評分歷史"])
        with tab_rank:
            st.markdown("### 🏆 綜合評分排名")
            st.caption(
                "以三大支柱在你選的 ETF 之間排名，**所有指標皆與市場多空方向無關**："
                "不獎勵單純漲多、也不獎勵單純抗跌，只獎勵「同樣風險下賺更多、"
                "相對基準留住更多漲幅卻少跌、表現穩定」。"
                "評分採總報酬（adj_close）計算，與上方圖表的顯示選項無關。"
            )

            # Regime coverage of the scoring window — so the score self-documents
            # whether it has actually seen a bear yet.
            _cov = regimes_df if not regimes_df.empty else _compute_regimes_live(zigzag_threshold)
            _rc = {"bull": 0, "correction": 0, "mini_bear": 0, "bear": 0}
            for _, _rr in _cov.iterrows():
                if (pd.Timestamp(_rr["end_date"]) < baseline_date
                        or pd.Timestamp(_rr["start_date"]) > today_ts):
                    continue
                if _rr["regime"] in _rc:
                    _rc[_rr["regime"]] += 1
            _no_bear = (_rc["mini_bear"] + _rc["bear"]) == 0
            st.caption(
                f"📐 評分期間市場樣本："
                f"多頭 {_rc['bull']} 段 · 小熊 {_rc['correction']} 段 · "
                f"中熊 {_rc['mini_bear']} 段 · 大熊 {_rc['bear']} 段"
                + ("　⚠️ 尚未經歷真正空頭，**抗跌/防禦能力未受考驗**，排名僅反映多頭效率。"
                   if _no_bear else "")
            )

            with st.expander("⚙️ 調整支柱權重（預設等權＝最公平）", expanded=False):
                cw = st.columns(3)
                w_eff = cw[0].slider("效率",   0.0, 3.0, 1.0, 0.5, key="etfc_w_eff")
                w_asy = cw[1].slider("不對稱", 0.0, 3.0, 1.0, 0.5, key="etfc_w_asy")
                w_con = cw[2].slider("一致性", 0.0, 3.0, 1.0, 0.5, key="etfc_w_con")
            weights = {"efficiency": w_eff, "asymmetry": w_asy, "consistency": w_con}

            score_df = _build_score_table(selected_tickers, etf_universe,
                                          baseline_date, weights)

            ranked = score_df[score_df["綜合評分"].notna()].sort_values(
                "綜合評分", ascending=False)
            insufficient = score_df[score_df["綜合評分"].isna()]

            if ranked.empty:
                st.info("沒有足夠資料可評分（所選 ETF 皆資料不足或缺基準）。")
            else:
                disp_rows: list[dict] = []
                for i, (_, r) in enumerate(ranked.iterrows(), 1):
                    disp_rows.append({
                        "排名":   i,
                        "代號":   r["代號"], "名稱": r["名稱"], "類別": r["類別"],
                        "綜合評分": round(float(r["綜合評分"]), 1),
                        "評等":   _stars(r["綜合評分"]),
                        "效率":   r.get("效率"),
                        "不對稱": r.get("不對稱"),
                        "一致性": r.get("一致性"),
                        "同類排名": r.get("同類排名", ""),
                        "交易日數": int(r["n_days"]),
                        "信賴":   _conf_label(int(r["n_days"])),
                        "完整度": f"{r['_completeness'] * 100:.0f}%",
                    })
                for _, r in insufficient.iterrows():
                    disp_rows.append({
                        "排名": "—", "代號": r["代號"], "名稱": r["名稱"], "類別": r["類別"],
                        "綜合評分": None, "評等": "", "效率": None, "不對稱": None,
                        "一致性": None, "同類排名": "",
                        "交易日數": int(r["n_days"]),
                        "信賴": _conf_label(int(r["n_days"])), "完整度": "—",
                    })
                disp = pd.DataFrame(disp_rows)

                def _score_color(v):
                    if pd.isna(v):
                        return ""
                    if v >= 70:
                        return "background-color: rgba(74,222,128,0.18); font-weight: 700"
                    if v >= 50:
                        return "background-color: rgba(74,222,128,0.07)"
                    if v >= 30:
                        return "background-color: rgba(251,191,36,0.10)"
                    return "background-color: rgba(248,113,113,0.14)"

                def _pillar_color(v):
                    if pd.isna(v):
                        return "color: #6b7280"
                    if v >= 66:
                        return "color: #4ade80"
                    if v <= 33:
                        return "color: #f87171"
                    return ""

                styled = (
                    disp.style
                    .format({
                        "綜合評分": lambda v: f"{v:.1f}" if pd.notna(v) else "—",
                        "效率":   lambda v: f"{v:.0f}" if pd.notna(v) else "—",
                        "不對稱": lambda v: f"{v:.0f}" if pd.notna(v) else "—",
                        "一致性": lambda v: f"{v:.0f}" if pd.notna(v) else "—",
                    })
                    .map(_score_color, subset=["綜合評分"])
                    .map(_pillar_color, subset=["效率", "不對稱", "一致性"])
                )
                st.dataframe(styled, hide_index=True, width="stretch")

                st.markdown(
                    "**📖 讀法**（每欄 0–100，僅在你目前選的 ETF 之間相對排名）\n\n"
                    "- ⚙️ **效率**：風險調整後報酬（Sortino＋Calmar）→ 同樣下跌風險下，賺得越多越高\n"
                    "- ⚖️ **不對稱**：相對基準的「上漲捕獲 − 下跌捕獲」→ 留住越多漲幅、少跌越多越高\n"
                    "- 🎯 **一致性**：勝率＋低追蹤誤差＋低波動 → 表現越穩定越高\n"
                    "- 🏆 **綜合評分**：三支柱加權平均（預設等權），已依資料長度調整信賴度\n"
                    "- 🛈 **不對稱「—」**：該 ETF 與基準關聯太低（R² < 0.2，常見於債券/商品）"
                    "或無對應基準，不以捕獲評分，改由其餘支柱計分\n"
                    "- 🆕 **信賴 / 完整度**：新上市或資料不足者，分數會自動往中位收斂並標示"
                )

                # Transparency: which benchmark each fund was scored against
                bench_rows = [
                    {
                        "代號": r["代號"], "名稱": r["名稱"],
                        "評分基準": r.get("benchmark") or "（無，未用捕獲）",
                        "R²": (round(float(r["r2"]), 2)
                               if pd.notna(r.get("r2")) else "—"),
                        "交易日數": int(r["n_days"]),
                    }
                    for _, r in score_df.iterrows()
                ]
                with st.expander("🔍 評分基準與相關性（R²）", expanded=False):
                    st.caption(
                        "R² = 該 ETF 日報酬被基準解釋的比例。低於 0.2 時不採計捕獲類指標，"
                        "避免拿大盤行情去評斷債券 / 商品 / 低相關 ETF。"
                    )
                    st.dataframe(pd.DataFrame(bench_rows), hide_index=True, width="stretch")

        with tab_hist:
            st.markdown("### 📈 綜合評分歷史")
            st.caption(
                "上方所選 ETF 的每日公平評分走勢——在「同資產類別」（股票／債券／商品）內的"
                "百分位，越高＝同類中越好。三大支柱權重沿用「綜合評分排名」分頁的設定。"
            )
            score_hist = db.get_score_history()
            name_map = dict(zip(universe["ticker"], universe["name"]))

            def _hist_disp(t: str) -> str:
                return f"{t}  {name_map.get(t, '')}"

            avail = set(score_hist["ticker"].unique()) if not score_hist.empty else set()
            hist_tickers = [t for t in selected_tickers if t in avail]
            compress_hist = st.checkbox(
                "依信賴度壓縮（新基金分數往中位 50 收斂）", value=False, key="etfc_hist_compress",
                help="勾選後，資料越短的基金分數越往 50 靠攏，避免新基金的高分被過度解讀。",
            )

            w_eff = st.session_state.get("etfc_w_eff", 1.0)
            w_asy = st.session_state.get("etfc_w_asy", 1.0)
            w_con = st.session_state.get("etfc_w_con", 1.0)

            if score_hist.empty:
                st.info("尚無評分歷史資料。請先在伺服器執行 "
                        "`python -m scripts.etf_benchmark.step7_score --backfill`。")
            elif not hist_tickers:
                st.info("上方所選的 ETF 尚無評分歷史（可能為新上市未滿 30 個交易日，"
                        "或非股票／債券／商品型）。")
            else:
                sub = score_hist[score_hist["ticker"].isin(hist_tickers)].copy()
                sub["score"] = _history_composite(sub, w_eff, w_asy, w_con)
                if compress_hist:
                    conf = (sub["n_days"] / 252.0).clip(0, 1)
                    sub["score"] = 50.0 + (sub["score"] - 50.0) * conf

                hist_palette = px.colors.qualitative.Plotly + px.colors.qualitative.Vivid
                figh = go.Figure()
                for i, t in enumerate(hist_tickers):
                    s = sub[sub["ticker"] == t].sort_values("date")
                    if s.empty:
                        continue
                    latest_n = int(s["n_days"].iloc[-1])
                    figh.add_trace(go.Scatter(
                        x=s["date"], y=s["score"], mode="lines",
                        name=_hist_disp(t),
                        opacity=0.45 + 0.55 * min(1.0, latest_n / 252.0),   # young funds fainter
                        line=dict(color=hist_palette[i % len(hist_palette)], width=2),
                        customdata=s[["eff", "asy", "con", "n_days"]].to_numpy(),
                        hovertemplate=(
                            "%{x|%Y-%m-%d}　評分 <b>%{y:.1f}</b><br>"
                            "效率 %{customdata[0]:.0f}｜不對稱 %{customdata[1]:.0f}｜"
                            "一致性 %{customdata[2]:.0f}　(交易日 %{customdata[3]})"
                            "<extra>" + t + "</extra>"
                        ),
                    ))
                figh.add_hline(y=50, line=dict(color="#A9B1BD", width=1.5, dash="dash"))
                figh.update_layout(
                    height=440, margin=dict(l=10, r=20, t=30, b=10),
                    yaxis=dict(title="綜合評分（同類百分位）", range=[0, 100],
                               ticksuffix="", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                    xaxis=dict(title="", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                    legend=dict(x=0.01, y=0.99, xanchor="left", yanchor="top",
                                bgcolor="rgba(0,0,0,0.5)"),
                    hovermode="x unified",
                )
                st.plotly_chart(figh, width="stretch")
                st.caption(
                    "線越淡＝該基金資料越短、評分越不穩定（新上市基金自上市 30 個交易日後才開始計分）。"
                    "拖曳可縮放、點圖例可隱藏單條線。"
                )

    # ─────────── 市場區間績效摘要 ───────────
    if show_regimes and not regimes_df.empty and per_ticker_prices:
        # Always need TAIEX prices for the benchmark math, whether it's on the chart or not
        bench_series = per_ticker_prices.get("^TWII")
        if bench_series is None or bench_series.empty:
            _btmp = db.get_prices("^TWII", start=baseline_date)
            if not _btmp.empty:
                bench_series = _btmp["close"].copy()
                bench_series.index = pd.to_datetime(_btmp["date"])

        capture_df, bench_summary = _build_capture_table(
            per_ticker_prices=per_ticker_prices,
            bench_series=bench_series,
            regimes_df=regimes_df,
            baseline_date=baseline_date,
            today_ts=today_ts,
            etf_universe=etf_universe,
        )

        with st.expander(
            f"📊 市場區間績效（擺動門檻 {zigzag_threshold:g}%）",
            expanded=True,
        ):
            # Benchmark caption — what we're comparing against
            br = bench_summary["by_regime"]
            np_ = bench_summary["n_periods"]
            def _fmt_pct(v):
                return f"{v:+.1f}%" if v is not None else "—"
            st.caption(
                f"🏛️ **基準：加權指數（每段交易日加權平均）**　"
                f"多頭 {_fmt_pct(br.get('bull'))} ({np_.get('bull', 0)} 段)　·　"
                f"小熊 {_fmt_pct(br.get('correction'))} ({np_.get('correction', 0)})　·　"
                f"中熊 {_fmt_pct(br.get('mini_bear'))} ({np_.get('mini_bear', 0)})　·　"
                f"大熊 {_fmt_pct(br.get('bear'))} ({np_.get('bear', 0)})"
            )

            if capture_df.empty:
                st.info("請選擇至少一檔 ETF（不含參考指數）以計算捕獲指標。")
            else:
                # Sort by capture ratio desc (best defensive ETF first), put N/A last
                sorted_df = capture_df.assign(
                    _sort=capture_df["捕獲比"].fillna(-9999)
                ).sort_values("_sort", ascending=False).drop(columns="_sort").reset_index(drop=True)
                display_df = sorted_df.rename(columns={
                    "上漲捕獲 %": "上漲捕獲",
                    "下跌捕獲 %": "下跌捕獲",
                })

                # Render with Styler — color cells, format numbers
                def _color_pct(v):
                    if v is None or pd.isna(v):
                        return ""
                    return "color: #4ade80" if v > 0 else "color: #f87171"

                def _color_capture_ratio(v):
                    if v is None or pd.isna(v):
                        return ""
                    if v >= 1.10:
                        return "background-color: rgba(74, 222, 128, 0.20); font-weight: 700"
                    if v >= 1.00:
                        return "background-color: rgba(74, 222, 128, 0.08); font-weight: 600"
                    if v <= 0.90:
                        return "background-color: rgba(248, 113, 113, 0.15)"
                    return ""

                def _color_up_capture(v):
                    if v is None or pd.isna(v):
                        return ""
                    if v >= 100:
                        return "color: #4ade80"
                    if v < 80:
                        return "color: #f87171"
                    return ""

                def _color_down_capture(v):
                    if v is None or pd.isna(v):
                        return ""
                    # For down capture, LOWER is better (loses less)
                    if v <= 80:
                        return "color: #4ade80"
                    if v > 110:
                        return "color: #f87171"
                    return ""

                styled = (
                    display_df.style
                    .format({
                        "多頭平均 %":  lambda v: f"{v:+.2f}" if pd.notna(v) else "—",
                        "小熊平均 %":  lambda v: f"{v:+.2f}" if pd.notna(v) else "—",
                        "中熊平均 %":  lambda v: f"{v:+.2f}" if pd.notna(v) else "—",
                        "大熊平均 %":  lambda v: f"{v:+.2f}" if pd.notna(v) else "—",
                        "上漲捕獲":    lambda v: f"{v / 100:.2f} 倍" if pd.notna(v) else "—",
                        "下跌捕獲":    lambda v: f"{v / 100:.2f} 倍" if pd.notna(v) else "—",
                        "捕獲比":      lambda v: f"{v:.2f}"  if pd.notna(v) else "—",
                    })
                    .map(_color_pct,           subset=["多頭平均 %", "小熊平均 %", "中熊平均 %", "大熊平均 %"])
                    .map(_color_up_capture,    subset=["上漲捕獲"])
                    .map(_color_down_capture,  subset=["下跌捕獲"])
                    .map(_color_capture_ratio, subset=["捕獲比"])
                )
                st.dataframe(styled, hide_index=True, width="stretch")

                st.markdown(
                    "**📖 讀法**\n\n"
                    "- 📊 **多頭 / 小熊 / 中熊 / 大熊平均 %**：該 ETF 在同類擺動期間，每段交易日加權平均報酬率\n"
                    "- 🚀 **上漲捕獲**：ETF 多頭平均 ÷ 大盤 →  **越大越會漲**（1.00 倍 = 跟大盤一樣，>1.00 倍 = 跑贏大盤）\n"
                    "- 🛡️ **下跌捕獲**：ETF 下跌平均 ÷ 大盤 →  **越小越抗跌**（0.90 倍 = 只跌大盤的九成）\n"
                    "- 🏆 **捕獲比 = 上漲捕獲 ÷ 下跌捕獲** →  **>1.0 = 防禦型優勢**，>1.10 = 優秀防禦"
                )

            # ── Detail expander: per-leg breakdown ─────────────────────────
            with st.expander("展開逐期明細（每段擺動）"):
                detail_rows = []
                for ticker, price_series in per_ticker_prices.items():
                    if price_series is None or price_series.empty:
                        continue
                    urow = etf_universe[etf_universe["ticker"] == ticker]
                    t_name = urow.iloc[0]["name"] if not urow.empty else ticker
                    for _, rrow in regimes_df.iterrows():
                        s = pd.Timestamp(rrow["start_date"])
                        e = pd.Timestamp(rrow["end_date"])
                        if e < baseline_date or s > today_ts:
                            continue
                        fret, ndays = _period_return_pct(price_series, s, e)
                        if fret is None:
                            continue
                        detail_rows.append({
                            "代號":        ticker,
                            "名稱":        t_name,
                            "區間類型":    REGIME_LABELS_ZH.get(rrow["regime"], rrow["regime"]),
                            "起":          rrow["start_date"].date().isoformat(),
                            "訖":          rrow["end_date"].date().isoformat(),
                            "指數變動 %":  round(float(rrow["severity"]), 1) if not pd.isna(rrow["severity"]) else None,
                            "ETF 報酬 %":  round(fret, 2),
                            "交易日數":    ndays,
                        })
                if detail_rows:
                    ddf = pd.DataFrame(detail_rows)
                    regime_order = ["多頭", "小熊", "中熊", "大熊"]
                    ddf["_sort"] = ddf["區間類型"].map(
                        {r: i for i, r in enumerate(regime_order)}
                    ).fillna(99)
                    ddf = ddf.sort_values(["代號", "_sort", "起"]).drop(columns="_sort")
                    st.dataframe(ddf, hide_index=True, width="stretch")

    if corporate_action_warnings:
        with st.container(border=True):
            st.markdown("⚠️ **資本事件提醒**")
            st.caption("以下 ETF 曾有資本事件；我們保留圖表，但該段期間請用人工判斷。")
            for w in list(dict.fromkeys(corporate_action_warnings)):
                st.markdown(f"- {w}")

    # ─────────── 缺漏交易日警示 ───────────
    ref_ticker = None
    if per_ticker_dates.get("^TWII"):
        ref_ticker = "^TWII"
    else:
        non_empty = {k: v for k, v in per_ticker_dates.items() if v}
        if non_empty:
            ref_ticker = max(non_empty, key=lambda k: len(non_empty[k]))

    if ref_ticker:
        ref_dates = per_ticker_dates[ref_ticker]
        ref_set = set(ref_dates)
        ref_n = len(ref_dates)
        gap_warnings: list[str] = []
        not_listed_warnings: list[str] = []
        for t, dates in per_ticker_dates.items():
            if t == ref_ticker or not dates:
                continue
            missing_all = sorted(ref_set - set(dates))
            if not missing_all:
                continue
            urow = etf_universe[etf_universe["ticker"] == t]
            name = urow.iloc[0]["name"] if not urow.empty else ""
            listed_from = None
            if not urow.empty:
                listed_from = _as_date_or_none(urow.iloc[0].get("listing_date"))
                listed_from = listed_from or _as_date_or_none(urow.iloc[0].get("inception_date"))
                listed_from = listed_from or _as_date_or_none(urow.iloc[0].get("first_date"))

            not_listed_missing = []
            true_missing = missing_all
            if listed_from:
                not_listed_missing = [d for d in missing_all if pd.Timestamp(d).date() < listed_from]
                true_missing = [d for d in missing_all if pd.Timestamp(d).date() >= listed_from]

            if not_listed_missing:
                ranges = _group_consecutive_missing(not_listed_missing, ref_dates)
                parts = "、".join(_fmt_range(s, e) for s, e in ranges)
                not_listed_warnings.append(
                    f"**{t} {name}** 於 {listed_from.isoformat()} 上市，"
                    f"因此起始日前少 {len(not_listed_missing)} 個參考交易日：{parts}"
                )

            if true_missing:
                ranges = _group_consecutive_missing(true_missing, ref_dates)
                parts = "、".join(_fmt_range(s, e) for s, e in ranges)
                gap_warnings.append(
                    f"**{t} {name}** 缺 {len(true_missing)} 個交易日"
                    f"（{t} {len(dates)} 天 vs 參考 {ref_n} 天）：{parts}"
                )
        if not_listed_warnings:
            with st.container(border=True):
                st.markdown("ℹ️ **新上市資料不足**")
                st.caption("這不是資料缺漏，而是 ETF 在比較起始日之後才上市；圖表會從該 ETF 第一筆可用價格開始。")
                for w in not_listed_warnings:
                    st.markdown(f"- {w}")
        if gap_warnings:
            with st.container(border=True):
                st.markdown("⚠️ **缺漏交易日警示**")
                st.caption("可能原因：除權息暫停交易、分割暫停交易、Yahoo 資料缺失。"
                           "Bull/Bear 壓力測試時這些缺漏期間會自動排除。")
                for w in gap_warnings:
                    st.markdown(f"- {w}")

    # ─────────── 驗證表 ───────────
    st.markdown("**驗證表**")
    st.caption(
        "對照表：圖上每條線的最終值應等於下表「報酬率 %」。"
        + ("報酬率使用 Yahoo adj_close（已自動配息還原）。"
           if use_adj else "報酬率使用原始收盤價（**未**配息還原，高股息 ETF 會被低估）。")
    )
    if line_rows:
        vdf = pd.DataFrame(line_rows)
        vdf = vdf[["ticker", "name", "status",
                   "start_date", "start_close", "end_date", "end_close",
                   "return_pct", "max_dd_pct", "n_points"]]
        vdf.columns = ["代號", "名稱", "狀態", "起始日", "起始收盤",
                       "結束日", "最新收盤", "報酬率 %", "最大跌幅 %", "交易日數"]
        st.dataframe(vdf, hide_index=True, width="stretch")
