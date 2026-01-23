# app.py
# Realized P/L Dashboard (Cathay / 國泰 CSV) - Master Raw Trades
#
# Master design:
# - Store ONLY raw trades in data/master_trades.csv
# - Admin uploads monthly Cathay export CSV (filename can vary)
# - Merge into master by COMPOSITE KEY (NOT 委託書號)
# - All calculations (FIFO + same-day match + board/odd pools) derived from master
#
# Required Streamlit Secrets:
#   GITHUB_TOKEN
#   ADMIN_PASSWORD
#   VIEW_PASSWORD
#   GITHUB_REPO        e.g. "benson930417-prog/STOCK"
#   GITHUB_BRANCH      e.g. "main"
#   GITHUB_FILE_PATH   e.g. "data/master_trades.csv"

INVESTMENT_TWD = 3_080_000

import os
import base64
import json
import traceback
from datetime import datetime
from pathlib import Path
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="Realized P/L Dashboard", layout="wide")

MASTER_PATH = Path("data") / "master_trades.csv"

# -------------------- secrets / auth --------------------
def _get_secret(key: str, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


VIEW_PASSWORD = str(_get_secret("VIEW_PASSWORD", "")).strip()
ADMIN_PASSWORD = str(_get_secret("ADMIN_PASSWORD", "")).strip()

GITHUB_TOKEN = _get_secret("GITHUB_TOKEN", "")
GITHUB_REPO = _get_secret("GITHUB_REPO", "")
GITHUB_BRANCH = _get_secret("GITHUB_BRANCH", "main")
GITHUB_FILE_PATH = _get_secret("GITHUB_FILE_PATH", "data/master_trades.csv")


def require_view_password_centered():
    # If not set, do not lock (dev mode)
    if not VIEW_PASSWORD:
        return

    if "authed_view" not in st.session_state:
        st.session_state.authed_view = False

    if st.session_state.authed_view:
        return

    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.markdown("## 🔒 Enter password to view")
        typed = st.text_input("Password", type="password", key="view_pw_input_main")
        if st.button("Enter", use_container_width=True):
            if typed == VIEW_PASSWORD:
                st.session_state.authed_view = True
                st.rerun()
            else:
                st.error("Wrong password")

    st.stop()


def is_admin_authed() -> bool:
    if not ADMIN_PASSWORD:
        return False
    if "authed_admin" not in st.session_state:
        st.session_state.authed_admin = False
    return bool(st.session_state.authed_admin)


def admin_login_ui():
    if not ADMIN_PASSWORD:
        st.info("Admin upload disabled (ADMIN_PASSWORD not set).")
        return

    if is_admin_authed():
        st.success("Admin mode enabled.")
        if st.button("Logout admin"):
            st.session_state.authed_admin = False
            st.rerun()
        return

    typed = st.text_input("Admin password", type="password", key="admin_pw_input")
    if typed:
        if typed == ADMIN_PASSWORD:
            st.session_state.authed_admin = True
            st.rerun()
        else:
            st.error("Wrong admin password")


require_view_password_centered()


# -------------------- helpers --------------------
def to_float(x):
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        x = x.replace(",", "").strip()
        if x == "":
            return 0.0
    try:
        return float(x)
    except Exception:
        return 0.0


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


# -------------------- GitHub push helpers --------------------
def github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_get_file_sha(repo: str, path: str, ref: str):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=github_api_headers(), params={"ref": ref}, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("sha")


def github_put_file(repo: str, path: str, ref: str, content_bytes: bytes, message: str):
    if not GITHUB_TOKEN or not repo:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_REPO in Streamlit Secrets.")

    sha = github_get_file_sha(repo, path, ref)

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": ref,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=github_api_headers(), data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    return r.json()


# -------------------- master file handling --------------------
RAW_REQUIRED = [
    "股名",
    "日期",
    "成交股數",
    "淨收付金額",
    "買賣別",
    "成交價",
    "成本",
    "手續費",
    "交易稅",
    "融資金額/券擔保品",
    "資自備款/券保證金",
    "利息",
    "稅款",
    "券手續費/標借費",
    "委託書號",
]


def read_cathay_csv_any(file_like_or_path) -> pd.DataFrame:
    """
    Supports:
    - Cathay export: row1 banner, row2 header -> header=1
    - Master CSV: normal header=0
    """
    # Try Cathay export style first
    try:
        df = pd.read_csv(file_like_or_path, header=1, encoding="utf-8-sig")
        df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
        if all(c in df.columns for c in RAW_REQUIRED):
            return df
    except Exception:
        pass

    # Fallback: normal CSV
    df = pd.read_csv(file_like_or_path, header=0, encoding="utf-8-sig")
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
    return df


def normalize_raw_trades(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]

    missing = [c for c in RAW_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}\nFound: {list(df.columns)}")

    df = df[RAW_REQUIRED].copy()

    # date
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])

    # normalize strings
    df["股名"] = df["股名"].astype(str).str.strip()
    df["買賣別"] = df["買賣別"].astype(str).str.strip()
    df["委託書號"] = df["委託書號"].astype(str).str.strip()

    # numeric
    df["成交股數"] = df["成交股數"].apply(to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(to_float)

    # parse these numeric-like columns and normalize commas
    num_cols = [
        "成交價",
        "成本",
        "手續費",
        "交易稅",
        "利息",
        "稅款",
        "券手續費/標借費",
        "融資金額/券擔保品",
        "資自備款/券保證金",
    ]
    for c in num_cols:
        df[c] = df[c].apply(to_float)

    # drop weird empty rows
    df = df[df["股名"].str.len() > 0]
    df = df[df["買賣別"].str.len() > 0]
    df = df[df["成交股數"] != 0]

    # composite key (main fix)
    df["_key"] = make_composite_key(df)

    return df


def make_composite_key(df: pd.DataFrame) -> pd.Series:
    """
    A stable composite key to prevent wrong deletions / false duplicates.

    NOTE:
    - do NOT rely on 委託書號
    - include it as a "weak signal" but key is mainly trade content
    - normalize numeric rounding to avoid "0 vs 0.0" duplication
    """

    d = df.copy()

    # stable date string
    date_str = pd.to_datetime(d["日期"]).dt.strftime("%Y-%m-%d")

    # normalize numeric columns into stable string forms
    qty = d["成交股數"].fillna(0).astype(int).astype(str)

    # money values (use integer for NTD-like fields)
    net = d["淨收付金額"].fillna(0).round(2).astype(float)
    net_str = net.map(lambda x: f"{x:.2f}")

    price_str = d["成交價"].fillna(0).round(4).astype(float).map(lambda x: f"{x:.4f}")
    cost_str = d["成本"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")
    fee_str = d["手續費"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")
    tax_str = d["交易稅"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")
    interest_str = d["利息"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")
    levy_str = d["稅款"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")
    borrow_fee_str = d["券手續費/標借費"].fillna(0).round(2).astype(float).map(lambda x: f"{x:.2f}")

    # optional: weak signal id
    oid = d["委託書號"].fillna("").astype(str).str.strip()

    stock = d["股名"].fillna("").astype(str).str.strip()
    side = d["買賣別"].fillna("").astype(str).str.strip()

    key = (
        stock
        + "|"
        + date_str
        + "|"
        + side
        + "|"
        + qty
        + "|"
        + net_str
        + "|"
        + price_str
        + "|"
        + cost_str
        + "|"
        + fee_str
        + "|"
        + tax_str
        + "|"
        + interest_str
        + "|"
        + levy_str
        + "|"
        + borrow_fee_str
        + "|"
        + oid
    )
    return key


def load_master_trades() -> pd.DataFrame:
    if not MASTER_PATH.exists():
        return pd.DataFrame(columns=RAW_REQUIRED + ["_key"])

    df = read_cathay_csv_any(str(MASTER_PATH))
    df = normalize_raw_trades(df)
    return df


def save_master_trades(df_master: pd.DataFrame):
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)

    # master stores raw trades (+ _key)
    cols_out = RAW_REQUIRED + ["_key"]
    df_master = df_master[cols_out].copy()
    df_master.to_csv(MASTER_PATH, index=False, encoding="utf-8-sig")


def merge_into_master(new_month_df: pd.DataFrame):
    master = load_master_trades()
    n_old = len(master)

    combined = pd.concat([master, new_month_df], ignore_index=True)

    # unique by composite key
    combined = combined.drop_duplicates(subset=["_key"], keep="last")
    combined = combined.sort_values(["日期", "股名", "成交股數", "淨收付金額"]).reset_index(drop=True)

    n_new_total = len(new_month_df)
    n_after = len(combined)
    n_added = max(0, n_after - n_old)
    n_dup_skipped = (n_old + n_new_total) - n_after

    save_master_trades(combined)

    return {
        "old_rows": n_old,
        "uploaded_rows": n_new_total,
        "after_rows": n_after,
        "added_rows": n_added,
        "dup_skipped": n_dup_skipped,
        "min_date": combined["日期"].min() if n_after else None,
        "max_date": combined["日期"].max() if n_after else None,
    }


def get_master_status_info(master_df: pd.DataFrame):
    """
    Sidebar status shown to everyone:
    - last updated time (local file mtime)
    - date range
    - total rows
    """
    if not MASTER_PATH.exists() or master_df is None or master_df.empty:
        return None

    ts = datetime.fromtimestamp(MASTER_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    min_date = pd.to_datetime(master_df["日期"]).min()
    max_date = pd.to_datetime(master_df["日期"]).max()

    if pd.isna(min_date) or pd.isna(max_date):
        dr = "N/A"
    else:
        dr = f"{min_date.date()} ~ {max_date.date()}"

    return {"time": ts, "range": dr, "rows": len(master_df)}


# -------------------- accounting (same logic as before) --------------------
def pool_of(qty: int) -> str:
    return "board" if qty % 1000 == 0 else "odd"  # 整股 vs 零股


def realized_match_first_then_fifo_separate_pools_from_raw_trades(raw_trades: pd.DataFrame):
    """
    Input raw_trades is a normalized DataFrame with required columns.
    Uses:
      - board/odd pools based on 成交股數
      - same-day match first (當沖)
      - FIFO inventory for remaining
    """
    df = raw_trades.copy()

    required = ["股名", "日期", "成交股數", "淨收付金額", "買賣別"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}\nFound: {list(df.columns)}")

    df["日期"] = pd.to_datetime(df["日期"])
    df["成交股數"] = df["成交股數"].apply(to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(to_float)
    df["買賣別"] = df["買賣別"].astype(str).str.strip()
    df["股名"] = df["股名"].astype(str).str.strip()

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
                    "Master may be missing older BUYs."
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

                # 2) Remaining buys -> inventory FIFO
                for lot in list(day_buy_lots):
                    if lot["qty"] > 0:
                        inventory[(stock, pool)].append({"qty": int(lot["qty"]), "cps": float(lot["cps"])})

                # 3) Remaining sells -> inventory FIFO
                for lot in list(day_sell_lots):
                    if lot["qty"] > 0:
                        qty = int(lot["qty"])
                        cash_in = float(lot["pps"] * qty)
                        sell_against_inventory(stock, pool, date, qty, cash_in)

    realized = pd.DataFrame(realized_rows).sort_values(["date", "stock"]).reset_index(drop=True)
    return df, realized


# -------------------- styling --------------------
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


# -------------------- Equity curve axis helper --------------------
def add_month_dividers(fig: go.Figure, dates: pd.Series, lang: str):
    """
    Major: month divider lines (thicker) + month label annotation
    Minor: day gridlines (handled by xaxis minor grid)
    Tick labels: weekly only (ticktext)
    """
    if dates.empty:
        return fig

    min_d = pd.to_datetime(dates.min()).normalize()
    max_d = pd.to_datetime(dates.max()).normalize()

    # month starts
    month_starts = pd.date_range(min_d.replace(day=1), max_d + pd.Timedelta(days=31), freq="MS")
    month_starts = month_starts[(month_starts >= min_d) & (month_starts <= max_d)]

    for ms in month_starts:
        fig.add_vline(
            x=ms,
            line_width=2,
            line_color="rgba(255,255,255,0.25)",
            layer="below",
        )
        label = ms.strftime("%Y-%m") if lang != "中文" else ms.strftime("%Y-%m")
        fig.add_annotation(
            x=ms,
            y=1.05,
            xref="x",
            yref="paper",
            text=label,
            showarrow=False,
            font=dict(size=12, color="rgba(255,255,255,0.75)"),
            xanchor="left",
        )

    return fig


def make_week_ticks(min_date: pd.Timestamp, max_date: pd.Timestamp):
    # weekly ticks (Mon)
    tickvals = pd.date_range(min_date.normalize(), max_date.normalize(), freq="W-MON")
    if len(tickvals) == 0:
        tickvals = pd.date_range(min_date.normalize(), max_date.normalize(), freq="7D")
    ticktext = [d.strftime("%m/%d") for d in tickvals]
    return tickvals, ticktext


# -------------------- APP --------------------
try:
    # ---- sidebar ----
    with st.sidebar:
        lang = st.radio("Language / 語言", ["EN", "中文"], index=1, horizontal=True)

        # Always show "recent update" to everyone
        st.markdown("## 最近更新")

        try:
            _tmp_master = load_master_trades() if MASTER_PATH.exists() else None
            info = get_master_status_info(_tmp_master)

            # show also last merge feedback if exists
            last_fb = st.session_state.get("last_merge_feedback")

            if info is None:
                st.warning("尚無 master 資料（請管理者上傳）")
            else:
                st.success("✅ Master 已更新並推送")
                st.caption(f"時間: {info['time']}")
                st.caption(f"範圍: {info['range']}")

                with st.expander("詳細資訊", expanded=False):
                    st.write(f"最後更新：{info['time']}")
                    st.write(f"資料範圍：{info['range']}")
                    st.write(f"總筆數：{info['rows']:,}")
                    if last_fb:
                        st.markdown("---")
                        st.write("**最近一次上傳結果**")
                        st.write(f"檔名：{last_fb.get('filename', 'N/A')}")
                        st.write(f"舊資料：{last_fb.get('old_rows', 0)}")
                        st.write(f"本次上傳：{last_fb.get('uploaded_rows', 0)}")
                        st.write(f"新增：{last_fb.get('added_rows', 0)}")
                        st.write(f"忽略(重複)：{last_fb.get('dup_skipped', 0)}")
                        if last_fb.get("range"):
                            st.write(f"合併後範圍：{last_fb['range']}")
        except Exception:
            st.warning("無法讀取 master 狀態")

        hr()

        st.markdown(f"## {T(lang, 'Realized P/L Dashboard (FIFO)', '已實現損益儀表板（FIFO）')}")
        st.caption(T(lang, f"Base capital: {INVESTMENT_TWD:,.0f} TWD", f"基準投入資金：{INVESTMENT_TWD:,.0f} 元"))
        hr()

        st.subheader(T(lang, "Color Theme", "顏色主題"))
        tw_colors = st.toggle(
            T(lang, "Taiwan colors (red=profit, green=loss)", "台股顏色（紅=賺、綠=虧）"),
            value=True,
        )

        hr()
        st.subheader("Admin / 管理者")
        admin_login_ui()

        # uploader key for clearing the widget after submit
        if "admin_uploader_key" not in st.session_state:
            st.session_state.admin_uploader_key = 0

        if is_admin_authed():
            st.markdown("**Upload monthly CSV → merge into master（組合 key 去重）**")

            with st.form("admin_upload_form", clear_on_submit=False):
                up_admin = st.file_uploader(
                    "Upload Cathay CSV (any filename). It will merge into data/master_trades.csv and push to GitHub.",
                    type=["csv"],
                    key=f"admin_month_uploader_{st.session_state.admin_uploader_key}",
                )
                submitted = st.form_submit_button("✅ Upload & Merge & Push", use_container_width=True)

            if submitted:
                if up_admin is None:
                    st.error("請先選擇 CSV 檔案")
                else:
                    # Prevent double-submit pushing same file endlessly
                    file_bytes = up_admin.getvalue()
                    file_hash = str(hash(file_bytes))

                    if st.session_state.get("last_uploaded_hash") == file_hash:
                        st.warning("這個檔案剛剛已經上傳過了（避免重複 push）")
                    else:
                        st.session_state.last_uploaded_hash = file_hash

                        with st.spinner("Merging + pushing to GitHub..."):
                            monthly_df = read_cathay_csv_any(up_admin)
                            monthly_df = normalize_raw_trades(monthly_df)

                            stats = merge_into_master(monthly_df)

                            # Push master to GitHub
                            master_bytes = MASTER_PATH.read_bytes()
                            msg = f"Update master_trades.csv ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

                            github_put_file(
                                repo=GITHUB_REPO,
                                path=GITHUB_FILE_PATH,
                                ref=GITHUB_BRANCH,
                                content_bytes=master_bytes,
                                message=msg,
                            )

                            # persistent feedback for everyone
                            rng = None
                            if stats["min_date"] is not None:
                                rng = f"{pd.to_datetime(stats['min_date']).date()} ~ {pd.to_datetime(stats['max_date']).date()}"

                            st.session_state.last_merge_feedback = {
                                "filename": getattr(up_admin, "name", "uploaded.csv"),
                                **stats,
                                "range": rng,
                            }

                        st.success("✅ Master updated + pushed to GitHub.")

                        # bump uploader key to clear selection
                        st.session_state.admin_uploader_key += 1
                        st.rerun()

    # Colors
    if tw_colors:
        PROFIT_COLOR = "#E74C3C"
        LOSS_COLOR = "#2ECC71"
    else:
        PROFIT_COLOR = "#2ECC71"
        LOSS_COLOR = "#E74C3C"

    NEUTRAL_BLUE = "#4C78A8"
    NEUTRAL_PURPLE = "#6F42C1"

    # Load master trades
    if not MASTER_PATH.exists():
        st.warning(T(lang, "No master file yet. Admin please upload in sidebar.", "尚未有 master 檔，請管理者在左側上傳當月 CSV。"))
        st.stop()

    master_trades = load_master_trades()

    raw_df, realized = realized_match_first_then_fifo_separate_pools_from_raw_trades(master_trades)

    if realized.empty:
        st.warning(T(lang, "No realized sells found.", "找不到已實現賣出紀錄。"))
        st.dataframe(raw_df.head(80), width="stretch")
        st.stop()

    # Display mapping (2 types only)
    TYPE_ZH = {"day_trade": "當沖交易", "cash": "現股交易"}
    TYPE_EN = {"day_trade": "Day trade", "cash": "Cash trade"}
    METHOD_ZH = {"day_trade": "當沖", "cash": "現股"}
    METHOD_EN = {"day_trade": "Day trade", "cash": "Cash"}

    realized["type_display"] = realized["type_key"].map(TYPE_ZH if lang == "中文" else TYPE_EN).fillna(realized["type_key"])
    realized["method_display"] = realized["method_key"].map(METHOD_ZH if lang == "中文" else METHOD_EN).fillna(realized["method_key"])
    realized["sign"] = np.where(realized["realized_pnl"] >= 0, "Profit", "Loss")

    # Filters (sidebar)
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

    # Aggregate view: day + stock + type
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

    # KPIs
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
        KPI_CARD(T(lang, "Total P/L", "總損益"), fmt_signed_money(total_pnl), total_color, T(lang, "Realized (filtered)", "已實現（依篩選範圍）"))
    with k2:
        KPI_CARD(T(lang, "Total P/L %", "總損益%"), fmt_signed_pct(total_pl_pct), plpct_color, T(lang, f"Base capital {INVESTMENT_TWD:,.0f}", f"基準資金 {INVESTMENT_TWD:,.0f}"))
    with k3:
        KPI_CARD(T(lang, "Win rate", "勝率"), f"{win_rate*100:.1f}%", win_color, T(lang, "Aggregated rows", "以彙總列計算"))
    with k4:
        KPI_CARD(T(lang, "Trades", "筆數"), f"{trades}", NEUTRAL_PURPLE, T(lang, "Aggregated (day+stock+type)", "已彙總（日+股票+類型）"))
    with k5:
        KPI_CARD(T(lang, "Trade volume", "交易量"), f"{trade_volume:,.0f}", NEUTRAL_BLUE, T(lang, "Total allocated cost", "分攤成本合計"))

    hr()

    tab_overview, tab_leader, tab_monthly, tab_trades = st.tabs(
        [T(lang, "Overview", "總覽"), T(lang, "Leaderboard", "排行"), T(lang, "Monthly report", "月報"), T(lang, "Trades", "交易")]
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
            )
        )

        # weekly ticks, daily faint grid, monthly strong dividers
        min_x = pd.to_datetime(f_sorted["date"].min())
        max_x = pd.to_datetime(f_sorted["date"].max())
        tickvals, ticktext = make_week_ticks(min_x, max_x)

        fig_eq.update_layout(
            title=T(lang, "Cumulative Realized P/L", "累計已實現損益"),
            xaxis=dict(
                title="",
                tickmode="array",
                tickvals=tickvals,
                ticktext=ticktext,
                tickangle=-35,
                showgrid=True,
                gridcolor="rgba(255,255,255,0.10)",
                gridwidth=1,
                minor=dict(
                    dtick=24 * 60 * 60 * 1000,  # 1 day
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.04)",
                    gridwidth=1,
                ),
            ),
            yaxis_title=f"{T(lang, 'P/L', '損益')} ({unit_lbl})",
            height=520,
            margin=dict(l=10, r=10, t=80, b=20),
            legend_title_text="",
        )

        fig_eq = add_month_dividers(fig_eq, f_sorted["date"], lang)
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
