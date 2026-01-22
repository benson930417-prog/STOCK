# app.py
# Realized P/L Dashboard (Cathay / 國泰 CSV)
#
# Goal (GitHub + Streamlit Cloud):
# - Friends open the website and see YOUR latest data (no upload needed).
# - You update daily by committing/pushing a single file: latest.csv (repo root).
# - Simple password gate (password stored directly in this Python file).
# - Sidebar = all controls (language/theme/filters). Main page = KPIs first.
#
# Repo structure:
#   app.py
#   requirements.txt
#   latest.csv   <-- you overwrite this daily and push
#
# Notes:
# - If latest.csv is missing (local/dev), it falls back to manual upload.
# - Tab2 Win rate is back
# - Tables have clean decimals
# - Tab3 graph has point labels
# - Per-stock Contribution has value labels
# - Equity curve x-axis shows months clearly + light daily vertical grid lines

INVESTMENT_TWD = 3_080_000  # <-- change this anytime
APP_PASSWORD = "abc123"     # <-- change this (hardcoded password as you requested)

import io
import os
import traceback
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Realized P/L Dashboard", layout="wide")


# -------------------- simple password gate (hardcoded) --------------------
def require_password():
    pw = APP_PASSWORD.strip()

    # if you set APP_PASSWORD="" then no lock (dev mode)
    if not pw:
        return

    if "authed" not in st.session_state:
        st.session_state.authed = False

    if st.session_state.authed:
        return

    with st.sidebar:
        st.markdown("### 🔒 Access")
        typed = st.text_input("Password", type="password")
        if typed:
            if typed == pw:
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("Wrong password")

    st.stop()


require_password()


# -------------------- helpers --------------------
def to_float(x):
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        x = x.replace(",", "").strip()
    return float(x)


def to_int(x):
    return int(round(to_float(x)))


def fmt_signed_money(x) -> str:
    try:
        v = float(x)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:,.0f}"
    except Exception:
        return str(x)


def fmt_signed_pct(x) -> str:
    try:
        v = float(x)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except Exception:
        return str(x)


def scale_unit(values: pd.Series, lang: str):
    """Scale for chart axes.

    EN: TWD / K / M
    中文: 元 / 千 / 萬 / 百萬
    """
    max_abs = float(np.nanmax(np.abs(values.to_numpy()))) if len(values) else 0.0

    if lang == "中文":
        if max_abs >= 1e6:
            return values / 1e6, "百萬", 1e6
        if max_abs >= 1e4:
            return values / 1e4, "萬", 1e4
        if max_abs >= 1e3:
            return values / 1e3, "千", 1e3
        return values, "元", 1.0
    else:
        if max_abs >= 1e6:
            return values / 1e6, "M", 1e6
        if max_abs >= 1e3:
            return values / 1e3, "K", 1e3
        return values, "TWD", 1.0


def add_zero_line(fig, axis="y", color="#A9B1BD", width=3, dash="dash"):
    if axis == "y":
        fig.add_hline(y=0, line_width=width, line_dash=dash, line_color=color)
    else:
        fig.add_vline(x=0, line_width=width, line_dash=dash, line_color=color)
    return fig


def KPI_CARD(title: str, value: str, color_hex: str, subtitle: str = ""):
    st.markdown(
        f"""
<div style="
  background: {color_hex};
  border-radius: 14px;
  padding: 14px 14px;
  color: white;
  box-shadow: 0 6px 18px rgba(0,0,0,0.25);
  min-height: 86px;
">
  <div style="font-size: 12px; opacity: 0.95; letter-spacing: 0.2px;">{title}</div>
  <div style="font-size: 26px; font-weight: 700; margin-top: 6px; line-height: 1.05;">{value}</div>
  <div style="font-size: 12px; opacity: 0.85; margin-top: 4px;">{subtitle}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def hr():
    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.12); margin: 10px 0 14px 0;'/>",
        unsafe_allow_html=True,
    )


def T(lang, en, zh):
    return zh if lang == "中文" else en


# -------------------- accounting (board/odd pools, displayed as 2 types) --------------------
def pool_of(qty: int) -> str:
    return "board" if qty % 1000 == 0 else "odd"  # 整股 vs 零股


def realized_match_first_then_fifo_separate_pools(uploaded_file_like_or_path):
    """Cathay CSV: row1 banner, row2 header -> header=1"""

    df = pd.read_csv(uploaded_file_like_or_path, header=1, encoding="utf-8-sig")
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]

    required = ["股名", "日期", "成交股數", "淨收付金額", "買賣別"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}\nFound: {list(df.columns)}")

    df["日期"] = pd.to_datetime(df["日期"])
    df["成交股數"] = df["成交股數"].apply(to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(to_float)
    df["買賣別"] = df["買賣別"].astype(str).str.strip()

    df = df.sort_values(["股名", "日期"]).reset_index(drop=True)

    inventory = defaultdict(deque)  # (stock, pool) -> deque lots {qty, cps}
    realized_rows = []

    def sell_against_inventory(stock, pool, date, qty, cash_in):
        remaining = int(qty)
        allocated_cost = 0.0

        while remaining > 0:
            if not inventory[(stock, pool)]:
                raise ValueError(
                    f"Sell without inventory: {stock} ({pool}) on {pd.to_datetime(date).date()} sell_qty={qty}. "
                    "Export likely missing earlier BUYs. Export a wider date range."
                )
            lot = inventory[(stock, pool)][0]
            take = min(remaining, lot["qty"])
            allocated_cost += take * lot["cps"]
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] == 0:
                inventory[(stock, pool)].popleft()

        pnl = float(cash_in) - allocated_cost
        ret_pct = (pnl / allocated_cost * 100.0) if allocated_cost else 0.0

        realized_rows.append(
            dict(
                date=pd.to_datetime(date),
                stock=stock,
                sell_qty=int(qty),
                sell_cash_in=float(cash_in),
                allocated_cost=float(allocated_cost),
                realized_pnl=float(pnl),
                realized_return_pct=float(ret_pct),
                method_key="cash",
                type_key="cash",
                pool_key=pool,
            )
        )

    for stock, sdf in df.groupby("股名", sort=False):
        sdf = sdf.sort_values("日期")

        for date, ddf in sdf.groupby(sdf["日期"].dt.date, sort=False):
            for pool in ["board", "odd"]:
                dpool = ddf[ddf["成交股數"].apply(lambda q: pool_of(q) == pool)].copy()
                if dpool.empty:
                    continue

                buys = dpool[dpool["淨收付金額"] < 0].copy()
                sells = dpool[dpool["淨收付金額"] > 0].copy()

                day_buy_lots = deque()
                for _, r in buys.iterrows():
                    qty = int(r["成交股數"])
                    cash_out = -float(r["淨收付金額"])
                    cps = cash_out / qty
                    day_buy_lots.append({"qty": qty, "cps": cps})

                day_sell_lots = deque()
                for _, r in sells.iterrows():
                    qty = int(r["成交股數"])
                    cash_in = float(r["淨收付金額"])
                    pps = cash_in / qty
                    day_sell_lots.append({"qty": qty, "pps": pps})

                # 1) Same-day match (當沖)
                intraday_qty = 0
                intraday_cost = 0.0
                intraday_cash = 0.0

                while day_buy_lots and day_sell_lots:
                    b = day_buy_lots[0]
                    s = day_sell_lots[0]
                    take = min(b["qty"], s["qty"])

                    intraday_qty += take
                    intraday_cost += take * b["cps"]
                    intraday_cash += take * s["pps"]

                    b["qty"] -= take
                    s["qty"] -= take
                    if b["qty"] == 0:
                        day_buy_lots.popleft()
                    if s["qty"] == 0:
                        day_sell_lots.popleft()

                if intraday_qty > 0:
                    pnl = intraday_cash - intraday_cost
                    ret_pct = (pnl / intraday_cost * 100.0) if intraday_cost else 0.0
                    realized_rows.append(
                        dict(
                            date=pd.to_datetime(date),
                            stock=stock,
                            sell_qty=int(intraday_qty),
                            sell_cash_in=float(intraday_cash),
                            allocated_cost=float(intraday_cost),
                            realized_pnl=float(pnl),
                            realized_return_pct=float(ret_pct),
                            method_key="day_trade",
                            type_key="day_trade",
                            pool_key=pool,
                        )
                    )

                # 2) Remaining buys -> inventory
                for lot in list(day_buy_lots):
                    if lot["qty"] > 0:
                        inventory[(stock, pool)].append({"qty": int(lot["qty"]), "cps": float(lot["cps"])})

                # 3) Remaining sells -> inventory
                for lot in list(day_sell_lots):
                    if lot["qty"] > 0:
                        qty = int(lot["qty"])
                        cash_in = float(lot["pps"] * qty)
                        sell_against_inventory(stock, pool, date, qty, cash_in)

    realized = pd.DataFrame(realized_rows).sort_values(["date", "stock"]).reset_index(drop=True)
    return df, realized


# -------------------- styling (tables) --------------------
def make_trade_styler(df_show: pd.DataFrame, profit_color: str, loss_color: str):
    def color_pl(v):
        try:
            x = float(v)
        except Exception:
            return ""
        return f"color: {profit_color};" if x > 0 else (f"color: {loss_color};" if x < 0 else "")

    def color_pct(v):
        try:
            x = float(str(v).replace("%", ""))
        except Exception:
            return ""
        return f"color: {profit_color};" if x > 0 else (f"color: {loss_color};" if x < 0 else "")

    def color_winrate(v):
        try:
            x = float(str(v).replace("%", ""))
        except Exception:
            return ""
        return f"color: {profit_color};" if x >= 50.0 else f"color: {loss_color};"

    styler = df_show.style
    for col in df_show.columns:
        if col.lower() in ["realized p/l", "total p/l"] or col in ["已實現損益", "總損益", "損益"]:
            styler = styler.applymap(color_pl, subset=[col])
        if col.lower() in ["realized %", "total p/l %"] or col in ["已實現%", "總損益%", "報酬%"]:
            styler = styler.applymap(color_pct, subset=[col])
        if col.lower() in ["win rate %", "win rate"] or col in ["勝率%", "勝率"]:
            styler = styler.applymap(color_winrate, subset=[col])
    return styler


# -------------------- data source: latest.csv in repo --------------------
def repo_latest_csv_path():
    p = "latest.csv"  # repo root
    return p if os.path.exists(p) else None


# -------------------- app --------------------
try:
    # session cache (used only if latest.csv not present)
    if "csv_bytes" not in st.session_state:
        st.session_state.csv_bytes = None
        st.session_state.csv_name = None

    # Sidebar: language/theme + data source hint + (optional) filters
    with st.sidebar:
        lang = st.radio("Language / 語言", ["EN", "中文"], index=1, horizontal=True)

        st.markdown(f"## {T(lang, 'Realized P/L Dashboard (FIFO)', '已實現損益儀表板（FIFO）')}")
        st.caption(T(lang, f"Base capital: {INVESTMENT_TWD:,.0f} TWD", f"基準投入資金：{INVESTMENT_TWD:,.0f} 元"))
        hr()

        st.subheader(T(lang, "Color Theme", "顏色主題"))
        tw_colors = st.toggle(
            T(lang, "Taiwan colors (red=profit, green=loss)", "台股顏色（紅=賺、綠=虧）"),
            value=True,
        )

    # Colors
    if tw_colors:
        PROFIT_COLOR = "#E74C3C"  # red
        LOSS_COLOR = "#2ECC71"    # green
    else:
        PROFIT_COLOR = "#2ECC71"  # green
        LOSS_COLOR = "#E74C3C"    # red

    NEUTRAL_BLUE = "#4C78A8"
    NEUTRAL_PURPLE = "#6F42C1"

    # Decide data source
    latest_path = repo_latest_csv_path()

    with st.sidebar:
        hr()
        st.subheader(T(lang, "Data Source", "資料來源"))
        if latest_path is not None:
            st.success(T(lang, "Using repo file: latest.csv", "使用 repo 檔案：latest.csv"))
        else:
            st.warning(T(lang, "latest.csv not found. Using upload fallback.", "找不到 latest.csv，改用上傳模式（備援）。"))

            st.subheader(T(lang, "Upload CSV", "上傳 CSV"))
            up = st.file_uploader(
                T(lang, "Cathay CSV", "國泰 CSV"),
                type=["csv"],
                key="uploader_csv",
                help=T(
                    lang,
                    "Upload only for local/dev. For sharing, add latest.csv to the repo root.",
                    "僅供本機/開發備援。要分享給朋友請把 latest.csv 放在 repo 根目錄。",
                ),
            )
            if up is not None:
                st.session_state.csv_bytes = up.getvalue()
                st.session_state.csv_name = up.name

            if st.session_state.csv_bytes is None:
                st.info(T(lang, "Upload your CSV to start.", "請上傳 CSV 開始。"))
                st.stop()

            st.success(T(lang, "Currently loaded:", "目前使用檔案：") + f" {st.session_state.csv_name}")
            st.caption(T(lang, "To update, upload a new CSV above (overwrites).", "要更新只要再上傳一次新的 CSV（會覆蓋）。"))

    # Load data
    if latest_path is not None:
        raw_df, realized = realized_match_first_then_fifo_separate_pools(latest_path)
    else:
        file_like = io.BytesIO(st.session_state.csv_bytes)
        raw_df, realized = realized_match_first_then_fifo_separate_pools(file_like)

    if realized.empty:
        st.warning(T(lang, "No realized sells found.", "找不到已實現賣出紀錄。"))
        st.dataframe(raw_df.head(80), width="stretch")
        st.stop()

    TYPE_ZH = {"day_trade": "當沖交易", "cash": "現股交易"}
    TYPE_EN = {"day_trade": "Day trade", "cash": "Cash trade"}

    METHOD_ZH = {"day_trade": "當沖", "cash": "現股"}
    METHOD_EN = {"day_trade": "Day trade", "cash": "Cash"}

    realized["type_display"] = realized["type_key"].map(TYPE_ZH if lang == "中文" else TYPE_EN).fillna(realized["type_key"])
    realized["method_display"] = realized["method_key"].map(METHOD_ZH if lang == "中文" else METHOD_EN).fillna(realized["method_key"])
    realized["sign"] = np.where(realized["realized_pnl"] >= 0, "Profit", "Loss")

    # Filters in sidebar
    with st.sidebar:
        hr()
        st.subheader(T(lang, "Selection", "篩選"))

        min_d, max_d = realized["date"].min(), realized["date"].max()
        dr = st.date_input(T(lang, "Date range", "日期範圍"), value=(min_d.date(), max_d.date()))
        if isinstance(dr, tuple):
            start_date, end_date = dr
        else:
            start_date, end_date = dr, dr

        stocks = sorted(realized["stock"].unique())
        stock_sel = st.multiselect(T(lang, "Stocks", "股票"), options=stocks, default=stocks)

        type_opts = sorted(realized["type_display"].unique())
        type_sel = st.multiselect(T(lang, "Type", "類型"), options=type_opts, default=type_opts)

    # Apply filters
    f = realized.copy()
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    f = f[(f["date"] >= start) & (f["date"] < end)]
    f = f[f["stock"].isin(stock_sel)]
    f = f[f["type_display"].isin(type_sel)]

    if f.empty:
        st.warning(T(lang, "No trades match the current filters.", "目前篩選條件沒有任何交易。"))
        st.stop()

    # Aggregate: day + stock + type
    f_view = f.copy()
    f_view["day"] = pd.to_datetime(f_view["date"]).dt.floor("D")

    f_view = (
        f_view.groupby(["day", "stock", "type_display"], as_index=False)
        .agg(
            sell_qty=("sell_qty", "sum"),
            allocated_cost=("allocated_cost", "sum"),
            sell_cash_in=("sell_cash_in", "sum"),
            realized_pnl=("realized_pnl", "sum"),
            method_display=("method_display", lambda s: " / ".join(sorted(set(map(str, s))))),
        )
    )

    f_view["realized_return_pct"] = np.where(
        f_view["allocated_cost"] != 0,
        f_view["realized_pnl"] / f_view["allocated_cost"] * 100.0,
        0.0,
    )
    f_view["date"] = f_view["day"]
    f_view["sign"] = np.where(f_view["realized_pnl"] >= 0, "Profit", "Loss")

    f_sorted = f_view.sort_values(["date", "stock", "type_display"]).copy()
    f_sorted["cum_pnl"] = f_sorted["realized_pnl"].cumsum()

    # KPIs (main first)
    total_pnl = float(f_sorted["realized_pnl"].sum())
    trades = int(len(f_sorted))
    win_rate = float((f_sorted["realized_pnl"].to_numpy() > 0).mean()) if trades else 0.0
    total_pl_pct = (total_pnl / float(INVESTMENT_TWD) * 100.0) if INVESTMENT_TWD else 0.0
    trade_volume = float(f_sorted["allocated_cost"].sum())

    total_color = PROFIT_COLOR if total_pnl >= 0 else LOSS_COLOR
    plpct_color = PROFIT_COLOR if total_pl_pct >= 0 else LOSS_COLOR
    win_color = PROFIT_COLOR if (win_rate * 100.0) >= 50.0 else LOSS_COLOR

    st.markdown(f"### {T(lang, 'Key Metrics', '關鍵指標')}")
    k1, k2, k3, k4, k5 = st.columns([1, 1, 1, 1, 1], gap="medium")

    with k1:
        KPI_CARD(
            T(lang, "Total P/L", "總損益"),
            fmt_signed_money(total_pnl),
            total_color,
            T(lang, "Realized (filtered)", "已實現（依篩選範圍）"),
        )
    with k2:
        KPI_CARD(
            T(lang, "Total P/L %", "總損益%"),
            fmt_signed_pct(total_pl_pct),
            plpct_color,
            T(lang, f"Base capital {INVESTMENT_TWD:,.0f}", f"基準資金 {INVESTMENT_TWD:,.0f}"),
        )
    with k3:
        KPI_CARD(
            T(lang, "Win rate", "勝率"),
            f"{win_rate*100:.1f}%",
            win_color,
            T(lang, "Aggregated rows", "以彙總列計算"),
        )
    with k4:
        KPI_CARD(
            T(lang, "Trades", "筆數"),
            f"{trades}",
            NEUTRAL_PURPLE,
            T(lang, "Aggregated (day+stock+type)", "已彙總（日+股票+類型）"),
        )
    with k5:
        KPI_CARD(
            T(lang, "Trade volume", "交易量"),
            f"{trade_volume:,.0f}",
            NEUTRAL_BLUE,
            T(lang, "Total allocated cost", "分攤成本合計"),
        )

    hr()

    tab_overview, tab_leader, tab_monthly, tab_trades = st.tabs(
        [
            T(lang, "Overview", "總覽"),
            T(lang, "Leaderboard", "排行"),
            T(lang, "Monthly report", "月報"),
            T(lang, "Trades", "交易"),
        ]
    )

    # -------------------- Overview --------------------
    with tab_overview:
        st.subheader(T(lang, "Equity Curve", "資金曲線"))
        scaled_cum, unit_lbl, _ = scale_unit(f_sorted["cum_pnl"], lang)

        fig_eq = go.Figure()
        fig_eq.add_trace(
            go.Scatter(
                x=f_sorted["date"],
                y=scaled_cum,
                mode="lines",
                name=T(lang, "Cumulative P/L", "累計損益"),
                line=dict(width=3),
                hovertemplate=(
                    "date=%{x|%Y-%m-%d}<br>"
                    + T(lang, "cum_pnl", "累計損益")
                    + f"=%{{y:.2f}} {unit_lbl}<extra></extra>"
                ),
            )
        )

        for sign_name, color in [("Profit", PROFIT_COLOR), ("Loss", LOSS_COLOR)]:
            sub = f_sorted[f_sorted["sign"] == sign_name]
            if sub.empty:
                continue
            sub_scaled = scale_unit(sub["cum_pnl"], lang)[0]
            custom = np.column_stack(
                [
                    sub["stock"].astype(str).to_numpy(),
                    sub["type_display"].astype(str).to_numpy(),
                    sub["realized_pnl"].to_numpy(),
                    sub["realized_return_pct"].to_numpy(),
                ]
            )
            fig_eq.add_trace(
                go.Scatter(
                    x=sub["date"],
                    y=sub_scaled,
                    mode="markers",
                    name=T(lang, "Profit" if sign_name == "Profit" else "Loss", "獲利" if sign_name == "Profit" else "虧損"),
                    marker=dict(size=8, color=color),
                    customdata=custom,
                    hovertemplate=(
                        "date=%{x|%Y-%m-%d}<br>"
                        + T(lang, "stock", "股票")
                        + "=%{customdata[0]}<br>"
                        + T(lang, "type", "類型")
                        + "=%{customdata[1]}<br>"
                        + T(lang, "pnl", "損益")
                        + "=%{customdata[2]:,.0f}<br>"
                        + T(lang, "return", "報酬")
                        + "=%{customdata[3]:.2f}%<extra></extra>"
                    ),
                )
            )

        # Month major ticks + light daily vertical grid lines
        fig_eq.update_layout(
            title=T(lang, "Cumulative Realized P/L", "累計已實現損益"),
            xaxis=dict(
                title="",
                dtick="M1",
                tickformat="%Y-%m",
                ticklabelmode="period",
                showgrid=True,
                gridcolor="rgba(255,255,255,0.18)",
                gridwidth=1,
                minor=dict(
                    dtick=24 * 60 * 60 * 1000,  # 1 day (ms)
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.06)",
                    gridwidth=1,
                ),
            ),
            yaxis_title=f"{T(lang, 'P/L', '損益')} ({unit_lbl})",
            height=460,
            margin=dict(l=10, r=10, t=60, b=10),
            legend_title_text="",
        )
        add_zero_line(fig_eq, axis="y", color="#A9B1BD", width=3, dash="dash")
        st.plotly_chart(fig_eq, width="stretch")

        hr()
        st.subheader(T(lang, "Per-stock Contribution", "各股貢獻"))

        by_stock = f_view.groupby("stock", as_index=False)["realized_pnl"].sum()
        by_stock["sign"] = np.where(by_stock["realized_pnl"] >= 0, "Profit", "Loss")
        by_stock["abs"] = by_stock["realized_pnl"].abs()
        by_stock = by_stock.sort_values("abs", ascending=False)

        scaled_vals, unit_lbl2, _ = scale_unit(by_stock["realized_pnl"], lang)
        by_stock["_scaled_pnl"] = scaled_vals

        sorted_df = by_stock.sort_values("_scaled_pnl")
        fig_bar = px.bar(
            sorted_df,
            x="_scaled_pnl",
            y="stock",
            orientation="h",
            color="sign",
            color_discrete_map={"Profit": PROFIT_COLOR, "Loss": LOSS_COLOR},
            text=sorted_df["_scaled_pnl"].map(lambda v: f"{v:.2f} {unit_lbl2}"),
        )
        fig_bar.update_traces(textposition="outside", cliponaxis=False)
        fig_bar.update_layout(
            title=T(lang, "Realized P/L by stock", "各股已實現損益"),
            xaxis_title=f"{T(lang, 'P/L', '損益')} ({unit_lbl2})",
            yaxis_title="",
            height=520,
            margin=dict(l=10, r=10, t=60, b=10),
            legend_title_text="",
        )
        add_zero_line(fig_bar, axis="x", color="#A9B1BD", width=3, dash="dash")
        st.plotly_chart(fig_bar, width="stretch")

    # -------------------- Leaderboard (Tab 2) --------------------
    with tab_leader:
        st.subheader(T(lang, "Win / Loss Leaderboard", "勝負排行"))

        lb = f_view.copy()
        lb["pnl"] = lb["realized_pnl"]
        lb["win"] = (lb["pnl"] > 0).astype(int)
        lb["trades"] = 1

        lb = (
            lb.groupby("stock", as_index=False)
            .agg(
                trades=("trades", "sum"),
                win=("win", "sum"),
                total_pnl=("pnl", "sum"),
                total_cost=("allocated_cost", "sum"),
            )
        )
        lb["win_rate_pct"] = np.where(lb["trades"] > 0, lb["win"] / lb["trades"] * 100.0, 0.0)
        lb["total_pnl_pct"] = np.where(lb["total_cost"] > 0, lb["total_pnl"] / lb["total_cost"] * 100.0, 0.0)

        winners = lb[lb["total_pnl"] > 0].sort_values("total_pnl", ascending=False)
        losers = lb[lb["total_pnl"] < 0].sort_values("total_pnl", ascending=True)

        def prep(df_):
            out = df_.copy()
            out = out.rename(
                columns={
                    "stock": T(lang, "Stock", "股票"),
                    "trades": T(lang, "Trades", "筆數"),
                    "total_pnl": T(lang, "Total P/L", "總損益"),
                    "total_pnl_pct": T(lang, "P/L % (vs cost)", "損益%（對成本）"),
                    "win_rate_pct": T(lang, "Win rate %", "勝率%"),
                }
            )

            out[T(lang, "Total P/L", "總損益")] = out[T(lang, "Total P/L", "總損益")].round(0).astype(int)
            out[T(lang, "P/L % (vs cost)", "損益%（對成本）")] = out[T(lang, "P/L % (vs cost)", "損益%（對成本）")].round(2)
            out[T(lang, "Win rate %", "勝率%")] = out[T(lang, "Win rate %", "勝率%")].round(1)
            out[T(lang, "Trades", "筆數")] = out[T(lang, "Trades", "筆數")].astype(int)

            return out[
                [
                    T(lang, "Stock", "股票"),
                    T(lang, "Total P/L", "總損益"),
                    T(lang, "P/L % (vs cost)", "損益%（對成本）"),
                    T(lang, "Trades", "筆數"),
                    T(lang, "Win rate %", "勝率%"),
                ]
            ]

        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.caption(T(lang, "Winners", "獲利榜"))
            wtbl = prep(winners)
            st.dataframe(
                make_trade_styler(wtbl, PROFIT_COLOR, LOSS_COLOR).format(
                    {
                        T(lang, "Total P/L", "總損益"): "{:,.0f}",
                        T(lang, "P/L % (vs cost)", "損益%（對成本）"): "{:.2f}",
                        T(lang, "Trades", "筆數"): "{:.0f}",
                        T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    }
                ),
                width="stretch",
                height=420,
            )
        with c2:
            st.caption(T(lang, "Losers", "虧損榜"))
            ltbl = prep(losers)
            st.dataframe(
                make_trade_styler(ltbl, PROFIT_COLOR, LOSS_COLOR).format(
                    {
                        T(lang, "Total P/L", "總損益"): "{:,.0f}",
                        T(lang, "P/L % (vs cost)", "損益%（對成本）"): "{:.2f}",
                        T(lang, "Trades", "筆數"): "{:.0f}",
                        T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    }
                ),
                width="stretch",
                height=420,
            )

    # -------------------- Monthly report (Tab 3) --------------------
    with tab_monthly:
        st.subheader(T(lang, "Monthly Snapshot (month-end)", "月報（月末快照）"))

        m = f_view.copy()
        m["month"] = pd.to_datetime(m["date"]).dt.to_period("M").dt.to_timestamp()
        m = m.sort_values("date")

        m["cum_pnl"] = m["realized_pnl"].cumsum()
        m["cum_trades"] = 1
        m["cum_wins"] = (m["realized_pnl"] > 0).astype(int)
        m["cum_volume"] = m["allocated_cost"]

        m_cum = (
            m.groupby("month", as_index=False)
            .agg(
                cum_pnl=("cum_pnl", "last"),
                cum_trades=("cum_trades", "sum"),
                cum_wins=("cum_wins", "sum"),
                cum_volume=("cum_volume", "sum"),
            )
        )

        m_cum["cum_pl_pct"] = np.where(INVESTMENT_TWD != 0, m_cum["cum_pnl"] / float(INVESTMENT_TWD) * 100.0, 0.0)
        m_cum["cum_win_rate_pct"] = np.where(m_cum["cum_trades"] > 0, m_cum["cum_wins"] / m_cum["cum_trades"] * 100.0, 0.0)

        table = pd.DataFrame(
            {
                T(lang, "Month", "月份"): pd.to_datetime(m_cum["month"]).dt.strftime("%Y-%m"),
                T(lang, "Total P/L", "總損益"): m_cum["cum_pnl"].round(0).astype(int),
                T(lang, "Total P/L %", "總損益%"): m_cum["cum_pl_pct"].round(2),
                T(lang, "Trades", "筆數"): m_cum["cum_trades"].astype(int),
                T(lang, "Win rate %", "勝率%"): m_cum["cum_win_rate_pct"].round(1),
                T(lang, "Trade volume", "交易量"): m_cum["cum_volume"].round(0).astype(int),
            }
        )

        st.dataframe(
            make_trade_styler(table, PROFIT_COLOR, LOSS_COLOR).format(
                {
                    T(lang, "Total P/L", "總損益"): "{:,.0f}",
                    T(lang, "Total P/L %", "總損益%"): "{:.2f}",
                    T(lang, "Trades", "筆數"): "{:.0f}",
                    T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    T(lang, "Trade volume", "交易量"): "{:,.0f}",
                }
            ),
            width="stretch",
        )

        hr()
        st.subheader(T(lang, "Cumulative P/L by Month", "月度累計損益"))

        scaled_vals, unit_lbl_m, _ = scale_unit(m_cum["cum_pnl"], lang)
        labels = [f"{v:.2f} {unit_lbl_m}" for v in scaled_vals.to_numpy()]

        fig_m = go.Figure()
        fig_m.add_trace(
            go.Scatter(
                x=m_cum["month"],
                y=scaled_vals,
                mode="lines+markers+text",
                text=labels,
                textposition="top center",
                textfont=dict(size=12),
                hovertemplate=(
                    "month=%{x|%Y-%m}<br>"
                    + T(lang, "cum_pnl", "累計損益")
                    + f"=%{{y:.2f}} {unit_lbl_m}<extra></extra>"
                ),
            )
        )
        fig_m.update_layout(
            height=360,
            margin=dict(l=10, r=10, t=40, b=10),
            xaxis_title="",
            yaxis_title=f"{T(lang, 'Cumulative', '累計')} ({unit_lbl_m})",
        )
        add_zero_line(fig_m, axis="y", color="#A9B1BD", width=3, dash="dash")
        st.plotly_chart(fig_m, width="stretch")

    # -------------------- Trades (Tab 4) --------------------
    with tab_trades:
        st.subheader(T(lang, "Realized Trades (Aggregated)", "已實現交易（已彙總）"))

        view = f_view.sort_values(["date", "stock", "type_display"], ascending=[False, True, True]).copy()
        view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m-%d")

        view_show = view.rename(
            columns={
                "type_display": T(lang, "Type", "類型"),
                "method_display": T(lang, "Method", "方式"),
                "realized_pnl": T(lang, "Realized P/L", "已實現損益"),
                "realized_return_pct": T(lang, "Realized %", "已實現%"),
            }
        )

        df_show = view_show[
            [
                "date",
                "stock",
                T(lang, "Type", "類型"),
                T(lang, "Method", "方式"),
                T(lang, "Realized P/L", "已實現損益"),
                T(lang, "Realized %", "已實現%"),
            ]
        ].copy()

        st.dataframe(
            make_trade_styler(df_show, PROFIT_COLOR, LOSS_COLOR).format(
                {
                    T(lang, "Realized P/L", "已實現損益"): "{:,.0f}",
                    T(lang, "Realized %", "已實現%"): "{:.2f}",
                }
            ),
            width="stretch",
            height=560,
        )

        hr()
        csv_out = view.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            T(lang, "Download CSV", "下載 CSV"),
            data=csv_out,
            file_name="realized_fifo_aggregated.csv",
            mime="text/csv",
        )

except Exception:
    st.error("App crashed during rendering. Here is the full traceback:")
    st.code(traceback.format_exc())
