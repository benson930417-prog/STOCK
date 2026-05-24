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
COMMON_PRICE_ADJUSTMENT_RATIOS = (2, 3, 4, 5, 6, 7, 10)
PRICE_ADJUSTMENT_TOLERANCE = 0.08


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

    def _set_max():
        st.session_state["etfc_baseline"] = min_d

    with st.container(border=True):
        st.markdown("**起始點設定**")
        c_date, c_fast, c_ref = st.columns([2, 4, 2])
        with c_date:
            st.date_input(" ", min_value=min_d, max_value=max_d,
                          key="etfc_baseline", label_visibility="collapsed")
        with c_fast:
            r1 = st.columns(4)
            r1[0].button("1D",  on_click=_set_bday,   args=(1,),                 key="etfc_b1d",  use_container_width=True)
            r1[1].button("5D",  on_click=_set_bday,   args=(5,),                 key="etfc_b5d",  use_container_width=True)
            r1[2].button("1M",  on_click=_set_offset, kwargs={"months": 1},      key="etfc_b1m",  use_container_width=True)
            r1[3].button("6M",  on_click=_set_offset, kwargs={"months": 6},      key="etfc_b6m",  use_container_width=True)
            r2 = st.columns(4)
            r2[0].button("YTD", on_click=_set_ytd,                                key="etfc_bytd", use_container_width=True)
            r2[1].button("1Y",  on_click=_set_offset, kwargs={"years": 1},       key="etfc_b1y",  use_container_width=True)
            r2[2].button("2Y",  on_click=_set_offset, kwargs={"years": 2},       key="etfc_b2y",  use_container_width=True)
            r2[3].button("MAX", on_click=_set_max,                                key="etfc_bmax", use_container_width=True)
        with c_ref:
            show_taiex = st.checkbox("顯示加權指數", value=True, key="etfc_show_taiex")
            show_otc   = st.checkbox("顯示櫃買指數", value=False, key="etfc_show_otc")
            use_adj    = st.checkbox("配息還原 (adj close)", value=True,
                                     key="etfc_use_adj",
                                     help="勾起：用 Yahoo 的 adj_close 算報酬率（公平比較高股息）。"
                                          "取消：用原始收盤價（高股息會被低估）。")

    baseline_date = pd.Timestamp(st.session_state["etfc_baseline"])
    today_ts = pd.Timestamp(summary["date_max"])

    # ─────────── 抓 DB + 畫圖 ───────────
    fig = go.Figure()
    y_max, y_min = 0.0, 0.0
    palette = px.colors.qualitative.Plotly + px.colors.qualitative.Vivid
    line_rows: list[dict] = []
    per_ticker_dates: dict[str, list] = {}
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
