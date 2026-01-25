# app.py
# Realized P/L Dashboard (Cathay CSV) - Master Raw Trades
#
# Master design:
# - Store ONLY raw trades in data/master_trades.csv
# - Admin uploads monthly Cathay export CSV (filename can vary)
# - Merge into master by COMPOSITE KEY (not 委託書號)
# - All calculations (FIFO + same-day match + board/odd pools) derived from master
#
# Required Streamlit Secrets:
#   GITHUB_TOKEN
#   ADMIN_PASSWORD
#   VIEW_PASSWORD
#   GITHUB_REPO        e.g. "benson930417-prog/STOCK"
#   GITHUB_BRANCH      e.g. "main"
#   GITHUB_FILE_PATH   e.g. "data/master_trades.csv"
#
# requirements.txt:
#   streamlit
#   pandas
#   numpy
#   plotly
#   requests

INVESTMENT_TWD = 3_080_000

import os
import time
import base64
import json
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st


# -------------------- page --------------------
st.set_page_config(page_title="Realized P/L Dashboard", layout="wide")

DATA_DIR = Path("data")
MASTER_PATH = DATA_DIR / "master_trades.csv"
META_PATH = DATA_DIR / "master_meta.json"

# -------------------- translations --------------------
def T(lang: str, en: str, zh: str) -> str:
    return zh if lang == "中文" else en


# Check for upload success toast (Moved here to ensure T is defined)
if st.session_state.get("upload_toast"):
     st.toast(T(st.session_state.get("lang", "中文"), "Master updated & pushed.", "Master 已更新並推送。"), icon="✅")
     del st.session_state["upload_toast"]


# -------------------- secrets --------------------
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


# -------------------- auth --------------------
def require_view_password_centered(lang: str):
    if not VIEW_PASSWORD:
        return

    if "authed_view" not in st.session_state:
        st.session_state.authed_view = False

    if st.session_state.authed_view:
        return

    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.markdown(f"## 🔒 {T(lang,'Enter password to view','輸入密碼以查看')}")
        typed = st.text_input(T(lang, "Password", "密碼"), type="password", key="view_pw_input_main")
        if st.button(T(lang, "Enter", "確認"), use_container_width=True):
            if typed == VIEW_PASSWORD:
                st.session_state.authed_view = True
                st.rerun()
            else:
                st.error(T(lang, "Wrong password", "密碼錯誤"))

    st.stop()


def is_admin_authed() -> bool:
    if not ADMIN_PASSWORD:
        return False
    if "authed_admin" not in st.session_state:
        st.session_state.authed_admin = False
    return bool(st.session_state.authed_admin)


def admin_login_ui(lang: str):
    if not ADMIN_PASSWORD:
        st.info(T(lang, "Admin upload disabled (ADMIN_PASSWORD not set).", "未設定 ADMIN_PASSWORD，管理者上傳功能停用。"))
        return

    if is_admin_authed():
        st.success(T(lang, "Admin mode enabled.", "已啟用管理者模式。"))
        if st.button(T(lang, "Logout admin", "登出管理者")):
            st.session_state.authed_admin = False
            st.rerun()
        return

    typed = st.text_input(T(lang, "Admin password", "管理者密碼"), type="password", key="admin_pw_input")
    if typed:
        if typed == ADMIN_PASSWORD:
            st.session_state.authed_admin = True
            st.rerun()
        else:
            st.error(T(lang, "Wrong admin password", "管理者密碼錯誤"))


# -------------------- helpers --------------------
def hr():
    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.12); margin: 10px 0 14px 0;'/>",
        unsafe_allow_html=True,
    )


def to_float(x):
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        x = x.replace(",", "").strip()
    if x == "":
        return 0.0
    return float(x)


def to_int(x):
    return int(round(to_float(x)))



def fmt_signed_money(x, rate: float = 1.0, currency: str = "") -> str:
    try:
        v = float(x) * rate
        sign = "+" if v > 0 else ""
        return f"{sign}{currency}{v:,.0f}"
    except Exception:
        return str(x)


def fmt_money(x, rate: float = 1.0, currency: str = "", decimals: int = 0) -> str:
    try:
        v = float(x) * rate
        return f"{currency}{v:,.{decimals}f}"
    except Exception:
        return str(x)


def fmt_signed_pct(x) -> str:
    try:
        v = float(x)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except Exception:
        return str(x)


def scale_unit(values: pd.Series, lang: str, rate: float = 1.0):
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
        # Currency conversion
        vals = values * rate
        
        # User requested FULL NUMBER for Euro, no K/M scaling.
        if rate != 1.0:
             return vals, "€", 1.0
        
        # Fallback to TWD or other logic if rate == 1.0 but lang=EN? 
        # For now assume rate==1.0 is TWD-like
        max_abs_conv = float(np.nanmax(np.abs(vals.to_numpy()))) if len(vals) else 0.0
        
        if max_abs_conv >= 1e6:
            return vals / 1e6, "M TWD", 1e6
        if max_abs_conv >= 1e3:
            return vals / 1e3, "K TWD", 1e3
        return vals, "TWD", 1.0


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
  <div style="font-size: 13px; opacity: 0.9; margin-top: 4px;">{subtitle}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def humanize_ago_from_utc_epoch(epoch_utc: float, lang: str) -> str:
    """
    Avoid timezone/DST issues by comparing in UTC only.
    """
    if not epoch_utc:
        return T(lang, "Unknown", "未知")
    now = datetime.now(timezone.utc)
    then = datetime.fromtimestamp(epoch_utc, tz=timezone.utc)
    diff = now - then
    sec = int(diff.total_seconds())
    if sec < 0:
        sec = 0

    if sec < 60:
        return T(lang, f"{sec} sec ago", f"{sec} 秒前")
    if sec < 3600:
        m = sec // 60
        return T(lang, f"{m} min ago", f"{m} 分鐘前")
    if sec < 86400:
        h = sec // 3600
    d = sec // 86400
    return T(lang, f"{d} days ago", f"{d} 天前")


    d = sec // 86400
    return T(lang, f"{d} days ago", f"{d} 天前")


def hex_to_rgba(hex_color: str, alpha: float = 0.2) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join([c * 2 for c in hex_color])
    return f"rgba({int(hex_color[:2], 16)}, {int(hex_color[2:4], 16)}, {int(hex_color[4:], 16)}, {alpha})"


def get_twd_to_eur_rate():
    # Cache in session to avoid spamming API on rerun
    if "eur_rate" in st.session_state:
        return st.session_state["eur_rate"]
    
    try:
        url = "https://api.exchangerate-api.com/v4/latest/TWD"
        r = requests.get(url, timeout=3.0)
        r.raise_for_status()
        data = r.json()
        rate = float(data["rates"]["EUR"])
        st.session_state["eur_rate"] = rate
        return rate
    except Exception:
        return None


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
    try:
        df = pd.read_csv(file_like_or_path, header=1, encoding="utf-8-sig")
        df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
        if all(c in df.columns for c in RAW_REQUIRED):
            return df
    except Exception:
        pass

    df = pd.read_csv(file_like_or_path, header=0, encoding="utf-8-sig")
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
    return df


def normalize_raw_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize types and build a COMPOSITE KEY to dedupe safely.
    """
    df = df.copy()
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]

    missing = [c for c in RAW_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}\nFound: {list(df.columns)}")

    df = df[RAW_REQUIRED].copy()

    df["股名"] = df["股名"].astype(str).str.strip()
    df["買賣別"] = df["買賣別"].astype(str).str.strip()

    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期"])

    df["成交股數"] = df["成交股數"].apply(to_int)

    # stabilize key by storing 淨收付金額 as int
    df["淨收付金額"] = df["淨收付金額"].apply(to_float).round(0).astype(int)

    def _num_clean_int(x):
        if pd.isna(x):
            return 0
        if isinstance(x, str):
            x = x.replace(",", "").strip()
        if x == "":
            return 0
        try:
            return int(round(float(x)))
        except Exception:
            return 0

    def _num_clean_float(x):
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

    df["成交價"] = df["成交價"].apply(_num_clean_float).round(4)

    for c in ["成本", "手續費", "交易稅", "利息", "稅款", "券手續費/標借費", "融資金額/券擔保品", "資自備款/券保證金"]:
        df[c] = df[c].apply(_num_clean_int)

    df["委託書號"] = df["委託書號"].astype(str).str.strip()

    df["_key"] = (
        df["股名"].astype(str)
        + "|"
        + df["日期"].dt.strftime("%Y-%m-%d")
        + "|"
        + df["成交股數"].astype(str)
        + "|"
        + df["淨收付金額"].astype(str)
        + "|"
        + df["買賣別"].astype(str)
        + "|"
        + df["成交價"].map(lambda v: f"{v:.4f}")
        + "|"
        + df["成本"].astype(str)
        + "|"
        + df["手續費"].astype(str)
        + "|"
        + df["交易稅"].astype(str)
        + "|"
        + df["委託書號"].astype(str)
    )

    return df


def load_master_trades() -> pd.DataFrame:
    if not MASTER_PATH.exists():
        return pd.DataFrame(columns=RAW_REQUIRED + ["_key"])

    df = read_cathay_csv_any(str(MASTER_PATH))
    df = normalize_raw_trades(df)
    return df


def save_master_trades(df_master: pd.DataFrame):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_master.to_csv(MASTER_PATH, index=False, encoding="utf-8-sig")


def load_meta() -> dict:
    if not META_PATH.exists():
        return {}
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_meta(meta: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_into_master(new_month_df: pd.DataFrame, upload_filename: str):
    master = load_master_trades()
    n_old = len(master)
    n_uploaded = len(new_month_df)

    combined = pd.concat([master, new_month_df], ignore_index=True)

    before = len(combined)
    combined = combined.drop_duplicates(subset=["_key"], keep="last")
    n_after = len(combined)

    dup_skipped = before - n_after
    added_rows = max(0, n_after - n_old)

    combined = combined.sort_values(["日期", "股名"]).reset_index(drop=True)

    save_master_trades(combined)

    min_date = combined["日期"].min() if len(combined) else None
    max_date = combined["日期"].max() if len(combined) else None

    meta = load_meta()
    meta["last_update_utc_epoch"] = datetime.now(timezone.utc).timestamp()
    meta["rows"] = int(len(combined))
    meta["min_date"] = str(pd.to_datetime(min_date).date()) if min_date is not None else None
    meta["max_date"] = str(pd.to_datetime(max_date).date()) if max_date is not None else None

    meta["last_upload"] = {
        "filename": upload_filename,
        "uploaded_rows": int(n_uploaded),
        "old_rows": int(n_old),
        "added_rows": int(added_rows),
        "dup_skipped": int(dup_skipped),
        "after_rows": int(n_after),
        "range_after": {
            "min_date": meta["min_date"],
            "max_date": meta["max_date"],
        },
    }
    save_meta(meta)

    return {
        "old_rows": n_old,
        "uploaded_rows": n_uploaded,
        "after_rows": n_after,
        "added_rows": added_rows,
        "dup_skipped": dup_skipped,
        "min_date": min_date,
        "max_date": max_date,
        "meta": meta,
    }


def push_master_and_meta_to_github(message_suffix: str = ""):
    master_bytes = MASTER_PATH.read_bytes()
    msg = f"Update master_trades.csv {message_suffix}".strip()
    github_put_file(
        repo=GITHUB_REPO,
        path=GITHUB_FILE_PATH,
        ref=GITHUB_BRANCH,
        content_bytes=master_bytes,
        message=msg,
    )

    meta_bytes = META_PATH.read_bytes()
    github_put_file(
        repo=GITHUB_REPO,
        path=str(META_PATH).replace("\\", "/"),
        ref=GITHUB_BRANCH,
        content_bytes=meta_bytes,
        message=f"Update master_meta.json {message_suffix}".strip(),
    )


# -------------------- accounting --------------------
def pool_of(qty: int) -> str:
    return "board" if qty % 1000 == 0 else "odd"


def realized_match_first_then_fifo_separate_pools_from_raw_trades(raw_trades: pd.DataFrame):
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

    def sell_against_inventory(stock, pool, date, qty, cash_in, sell_fee_tot, sell_tax_tot):
        remaining = int(qty)
        allocated_cost = 0.0
        allocated_buy_fee = 0.0

        while remaining > 0:
            if not inventory[(stock, pool)]:
                raise ValueError(
                    f"Sell without inventory: {stock} ({pool}) on {pd.to_datetime(date).date()} sell_qty={qty}. "
                    "Master may be missing older BUYs."
                )
            lot = inventory[(stock, pool)][0]
            take = min(remaining, lot["qty"])
            allocated_cost += take * lot["cps"]
            allocated_buy_fee += take * lot["fee_per_share"]

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
                total_fee=float(allocated_buy_fee + sell_fee_tot),
                total_tax=float(sell_tax_tot),
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
                    fee = float(r["手續費"])
                    cps = cash_out / qty
                    fps = fee / qty
                    day_buy_lots.append({"qty": qty, "cps": cps, "fee_per_share": fps})

                day_sell_lots = deque()
                for _, r in sells.iterrows():
                    qty = int(r["成交股數"])
                    cash_in = float(r["淨收付金額"])
                    fee = float(r["手續費"])
                    tax = float(r["交易稅"])
                    pps = cash_in / qty
                    fps = fee / qty
                    tps = tax / qty
                    day_sell_lots.append({"qty": qty, "pps": pps, "fee_per_share": fps, "tax_per_share": tps})

                # 1) Same-day match
                intraday_qty = 0
                intraday_cost = 0.0
                intraday_cash = 0.0
                intraday_buy_fee = 0.0
                intraday_sell_fee = 0.0
                intraday_sell_tax = 0.0

                while day_buy_lots and day_sell_lots:
                    b = day_buy_lots[0]
                    s = day_sell_lots[0]
                    take = min(b["qty"], s["qty"])

                    intraday_qty += take
                    intraday_cost += take * b["cps"]
                    intraday_cash += take * s["pps"]
                    intraday_buy_fee += take * b["fee_per_share"]
                    intraday_sell_fee += take * s["fee_per_share"]
                    intraday_sell_tax += take * s["tax_per_share"]

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
                            total_fee=float(intraday_buy_fee + intraday_sell_fee),
                            total_tax=float(intraday_sell_tax),
                            method_key="day_trade",
                            type_key="day_trade",
                            pool_key=pool,
                        )
                    )

                # 2) Remaining buys -> inventory
                for lot in list(day_buy_lots):
                    if lot["qty"] > 0:
                        inventory[(stock, pool)].append({
                            "qty": int(lot["qty"]), 
                            "cps": float(lot["cps"]),
                            "fee_per_share": float(lot["fee_per_share"])
                        })

                # 3) Remaining sells -> inventory
                for lot in list(day_sell_lots):
                    if lot["qty"] > 0:
                        qty = int(lot["qty"])
                        cash_in = float(lot["pps"] * qty)
                        s_fee = float(lot["fee_per_share"] * qty)
                        s_tax = float(lot["tax_per_share"] * qty)
                        sell_against_inventory(stock, pool, date, qty, cash_in, s_fee, s_tax)

    realized = pd.DataFrame(realized_rows).sort_values(["date", "stock"]).reset_index(drop=True)
    return df, realized


# -------------------- table styling --------------------
def make_trade_styler(df_show: pd.DataFrame, profit_color: str, loss_color: str):
    def color_pl(v):
        try:
            x = float(v)
        except Exception:
            return ""
        if x == 0: return "color: #FFFFFF;"
        return f"color: {profit_color};" if x > 0 else f"color: {loss_color};"

    def color_pct(v):
        try:
            x = float(str(v).replace("%", ""))
        except Exception:
            return ""
        if x == 0: return "color: #FFFFFF;"
        return f"color: {profit_color};" if x > 0 else f"color: {loss_color};"

    def color_winrate(v):
        try:
            x = float(str(v).replace("%", ""))
        except Exception:
            return ""
        if abs(x - 50.0) < 0.01: return "color: #FFFFFF;"
        return f"color: {profit_color};" if x > 50.0 else f"color: {loss_color};"

    styler = df_show.style
    for col in df_show.columns:
        low = str(col).lower()
        if low in ["realized p/l", "total p/l", "month p/l"] or col in ["已實現損益", "總損益", "損益", "月損益"]:
            styler = styler.applymap(color_pl, subset=[col])
        if low in ["realized %", "total p/l %", "month %"] or col in ["已實現%", "總損益%", "報酬%", "月報酬%"]:
            styler = styler.applymap(color_pct, subset=[col])
        if low in ["win rate %", "win rate"] or col in ["勝率%", "勝率"]:
            styler = styler.applymap(color_winrate, subset=[col])
    return styler


# -------------------- sidebar: recent update (post-auth) --------------------
def sidebar_recent_update(lang: str):
    st.markdown(f"## {T(lang,'Recent update','最近更新')}")

    meta = load_meta()
    epoch = float(meta.get("last_update_utc_epoch", 0) or 0)
    ago_text = humanize_ago_from_utc_epoch(epoch, lang)

    # ✅ Always compute "master range" from master_trades.csv (source of truth)
    master_rows = "?"
    rng_min = None
    rng_max = None
    if MASTER_PATH.exists():
        try:
            _m = load_master_trades()
            master_rows = int(len(_m))
            if len(_m):
                rng_min = pd.to_datetime(_m["日期"].min()).date().isoformat()
                rng_max = pd.to_datetime(_m["日期"].max()).date().isoformat()
        except Exception:
            pass

    if epoch:
        # st.success(f"✅ {T(lang,'Master updated & pushed','Master 已更新並推送')}") # Removed by request (temp only)
        st.caption(f"{T(lang,'Last updated','最後更新')}: **{ago_text}**")
        if rng_min and rng_max:
            st.caption(f"{T(lang,'Range','範圍')}: {rng_min} ~ {rng_max}")
    else:
        st.info(T(lang, "No master yet. Admin needs to upload.", "尚未建立 master，請管理者上傳。"))

    with st.expander(T(lang, "Details", "詳細資訊"), expanded=False):
        if not epoch:
            st.write(T(lang, "No data yet.", "目前沒有資料。"))
            return

        st.write(f"**{T(lang,'Master rows','總筆數')}** : {master_rows}")

        last_up = meta.get("last_upload", {})
        if last_up:
            st.markdown("---")
            st.write(f"### {T(lang,'Last upload result','最近一次上傳結果')}")
            st.write(f"**{T(lang,'Filename','檔名')}** : {last_up.get('filename','?')}")
            st.write(f"**{T(lang,'Uploaded rows','本次上傳')}** : {last_up.get('uploaded_rows','?')}")
            st.write(f"**{T(lang,'Added','新增')}** : {last_up.get('added_rows','?')}")
            st.write(f"**{T(lang,'Ignored (duplicates)','忽略(重複)')}** : {last_up.get('dup_skipped','?')}")
            ra = last_up.get("range_after", {})
            if ra.get("min_date") and ra.get("max_date"):
                st.write(f"**{T(lang,'Merged range','合併後範圍')}** : {ra['min_date']} ~ {ra['max_date']}")


# -------------------- chart helpers --------------------
def add_month_major_lines(fig: go.Figure, dates: pd.Series):
    if dates.empty:
        return fig
    dmin = pd.to_datetime(dates.min()).normalize()
    dmax = pd.to_datetime(dates.max()).normalize()
    months = pd.date_range(dmin.replace(day=1), dmax + pd.Timedelta(days=31), freq="MS")
    for m in months:
        fig.add_vline(
            x=m,
            line_width=2,
            line_dash="solid",
            line_color="rgba(255,255,255,0.22)",
        )
    return fig


def augment_zero_crossings(df: pd.DataFrame, date_col: str, val_col: str) -> pd.DataFrame:
    """
    Insert rows where the value crosses zero to ensure smooth filled area transitions.
    Assumes df is sorted by date.
    """
    if df.empty or len(df) < 2:
        return df

    out_rows = []
    recs = df.to_dict("records")
    out_rows.append(recs[0])
    
    for i in range(len(recs) - 1):
        curr = recs[i]
        next_ = recs[i+1]
        y1 = curr[val_col]
        y2 = next_[val_col]
        
        # Check strictly crossing zero
        if (y1 > 0 and y2 < 0) or (y1 < 0 and y2 > 0):
            # linear interpolation fraction
            f = -y1 / (y2 - y1)
            t1 = curr[date_col].timestamp()
            t2 = next_[date_col].timestamp()
            t_cross = t1 + (t2 - t1) * f
            
            row = curr.copy()
            row[date_col] = pd.to_datetime(t_cross, unit="s")
            row[val_col] = 0.0
            out_rows.append(row)
        
        out_rows.append(next_)
        
    return pd.DataFrame(out_rows)





def plot_pnl_distribution(df: pd.DataFrame, lang: str, profit_color: str, loss_color: str, rate: float = 1.0):
    """
    Histogram of P/L distribution.
    Expects df with column 'realized_pnl'.
    """
    if df.empty:
        return go.Figure()

    # Scale units first
    # unit_val is the series of scaled values, unit_txt is the label (e.g., '萬' or '€')
    scaled_vals, unit_txt, _ = scale_unit(df["realized_pnl"], lang, rate)
    vals = scaled_vals.to_numpy()

    # Pre-calculate histogram bins with 0 alignment
    # We want bins to start/end exactly at 0.
    if len(vals) > 0:
        v_min, v_max = vals.min(), vals.max()
        # Estimate ideal bin width using "auto" logic or FD rule on the whole set
        # Then align to 0
        iqr = np.subtract(*np.percentile(vals, [75, 25]))
        # Use a higher resolution (smaller width) than standard FD rule
        # Standard: 2 * IQR / n^(1/3). We use 1 * IQR ... essentially double the bins
        fd_width = 1.0 * iqr / (len(vals) ** (1/3)) if iqr > 0 else 0
        
        # Enforce max step size of 10,000 (scaled)
        # We need the scale factor used in scale_unit.
        # scale_unit returns (series, label, divisor) -> divisor is what we divided by.
        # So 10,000 real = 10,000 / divisor (scaled)
        _, _, divisor = scale_unit(pd.Series([10000]), lang)
        max_width = 10000.0 / divisor if divisor else 10000.0
        
        if fd_width > max_width:
             fd_width = max_width

        if fd_width == 0:
             # Fallback if IQR is 0 (low variation)
             fd_width = (v_max - v_min) / 20 if v_max != v_min else 10.0

        # FORCE STEP SIZE based on user request / unit
        if unit_txt == "萬":
            # User wants step of 50000 TWD -> 5.0 萬
            fd_width = 5.0
        elif unit_txt == "€":
            # User wants step of 1000 EUR
            fd_width = 1000.0

        # Enforce a minimum width to prevent needle-like bars
        min_w = 0.1 if unit_txt == "萬" else 1.0
        if fd_width < min_w:
            fd_width = min_w

        # Construct edges: 0 to max, and 0 to min
        # use ceil to cover full range
        pos_edges = np.arange(0, v_max + fd_width, fd_width)
        neg_edges = np.arange(0, v_min - fd_width, -fd_width)
        # neg_edges starts 0, -w, -2w... need to reverse and be unique
        bin_edges = np.unique(np.concatenate([neg_edges, pos_edges]))
        bin_edges.sort()
    else:
        bin_edges = "auto"

    counts, bin_edges = np.histogram(vals, bins=bin_edges)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    
    # Color condition: center >= 0 is profit
    colors = [profit_color if c >= 0 else loss_color for c in centers]
    
    # Filter out zero-count labels
    times_unit = "次" if lang == "中文" else "x"
    text_labels = [f"{int(x)}{times_unit}" if x > 0 else "" for x in counts]

    fig = go.Figure(
        data=go.Bar(
            x=centers,
            y=counts,
            marker_color=colors,
            text=text_labels,
            textposition="outside",
            # Make bars look connected like a histogram
            # Width needs to be calculated or let plotly handle it?
            # Setting width to (edge[1] - edge[0]) * 0.9 close gaps
        )
    )
    
    # Calculate approximate bar width
    if len(bin_edges) > 1:
        # Avoid forcing manual width if possible, let plotly handle or use gaps
        # But if we want connected look...
        pass 
        # width = (bin_edges[-1] - bin_edges[0]) / len(counts)
        # fig.update_traces(width=width * 0.95)

    fig.update_layout(
        title=T(lang, "P/L Distribution", "損益分佈"),
        xaxis_title=f"{T(lang, 'Realized P/L', '已實現損益')} ({unit_txt})",
        yaxis_title=T(lang, "Count", "筆數"),
        height=380,
        bargap=0.1,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    add_zero_line(fig, axis="x", color="#A9B1BD", width=2, dash="dash")
    return fig


# -------------------- APP --------------------
try:
    # Sidebar pre-auth: ONLY language switch
    with st.sidebar:
        lang = st.radio(
            "Language / 語言",
            ["EN", "中文"],
            index=1,
            horizontal=True,
        )


    # Gate everything else behind VIEW password
    require_view_password_centered(lang)

    # Sidebar post-auth: recent update + everything else
    with st.sidebar:
        sidebar_recent_update(lang)
        hr()

        # st.markdown(f"## {T(lang,'Theme','主題')}")
        # tw_colors = st.toggle(
        #     T(lang, "Taiwan colors (red=profit, green=loss)", "台股顏色（紅=賺、綠=虧）"),
        #     value=True,
        # )

        hr()
        st.markdown(f"## {T(lang,'Admin','管理者')}")
        admin_login_ui(lang)

        # Admin upload area (FORM to stop infinite reruns)
        if is_admin_authed():
            st.markdown(f"**{T(lang,'Upload monthly CSV → merge into master','上傳當月 CSV → 合併進 master')}**")
            st.caption(T(lang, "Dedupe = composite key (safe)", "去重 = 組合 key（更安全）"))

            with st.form("admin_upload_form", clear_on_submit=True):
                up_admin = st.file_uploader(
                    T(lang, "Upload Cathay CSV", "上傳國泰 CSV"),
                    type=["csv"],
                    key="admin_month_uploader",
                )
                submitted = st.form_submit_button(
                    T(lang, "Upload & Merge & Push", "上傳 + 合併 + 推送"),
                    use_container_width=True,
                )

            if submitted:
                if up_admin is None:
                    st.error(T(lang, "Please choose a CSV file first.", "請先選擇 CSV 檔案。"))
                else:
                    with st.spinner(T(lang, "Merging and pushing to GitHub...", "合併並推送至 GitHub...")):
                        monthly_df = read_cathay_csv_any(up_admin)
                        monthly_df = normalize_raw_trades(monthly_df)

                        stats = merge_into_master(monthly_df, upload_filename=getattr(up_admin, "name", "uploaded.csv"))

                        suffix = f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})"
                        push_master_and_meta_to_github(message_suffix=suffix)

                        st.session_state["last_upload_stats"] = stats
                        
                        st.session_state["last_upload_stats"] = stats
                        
                        # Set secure toast flag to show AFTER rerun
                        st.session_state["upload_toast"] = True
                        # Also save lang in session state to ensure toast has correct lang if needed (though global lang works too)
                        st.session_state["lang"] = lang 
                        
                        # Rerun to refresh data immediately
                        st.rerun()

    # Colors
    # Colors (Auto based on Language)
    if lang == "中文":
        # Taiwan: Red = Profit, Green = Loss
        PROFIT_COLOR = "#E74C3C" 
        LOSS_COLOR = "#2ECC71"
    else:
        # Western: Green = Profit, Red = Loss
        PROFIT_COLOR = "#2ECC71"
        LOSS_COLOR = "#E74C3C"
        
    NEUTRAL_BLUE = "#4C78A8"
    NEUTRAL_PURPLE = "#6F42C1"

    # Currency Rate Logic
    CURRENCY_RATE = 1.0
    CURRENCY_SYMBOL = ""
    
    if lang != "中文":
        # Try to get EUR rate
        rate_found = get_twd_to_eur_rate()
        if rate_found:
            CURRENCY_RATE = rate_found
            CURRENCY_SYMBOL = "€"
        else:
            # Fallback
            if "currency_fail_toast" not in st.session_state:
                st.toast("Currency API failed. Displaying TWD.", icon="⚠️")
                st.session_state["currency_fail_toast"] = True
            CURRENCY_RATE = 1.0
            CURRENCY_SYMBOL = ""
    else:
        # Reset toast flag if switching back to ZH
        if "currency_fail_toast" in st.session_state:
            del st.session_state["currency_fail_toast"]

    # Master availability
    if not MASTER_PATH.exists():
        st.warning(T(lang, "No master file yet. Admin please upload in sidebar.", "尚未有 master 檔，請管理者在左側上傳當月 CSV。"))
        st.stop()

    master_trades = load_master_trades()
    raw_df, realized = realized_match_first_then_fifo_separate_pools_from_raw_trades(master_trades)

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

    # Filters (sidebar post-auth)
    with st.sidebar:
        hr()
        st.markdown(f"## {T(lang,'Selection','篩選')}")
        min_d, max_d = realized["date"].min(), realized["date"].max()
        dr = st.date_input(
            T(lang, "Date range", "日期範圍"),
            value=(min_d.date(), max_d.date()),
        )
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
        f_view.groupby(["day", "stock", "type_display", "type_key"], as_index=False)
        .agg(
            sell_qty=("sell_qty", "sum"),
            allocated_cost=("allocated_cost", "sum"),
            sell_cash_in=("sell_cash_in", "sum"),
            realized_pnl=("realized_pnl", "sum"),
            total_fee=("total_fee", "sum"),
            total_tax=("total_tax", "sum"),
            method_display=("method_display", lambda s: " / ".join(sorted(set(map(str, s))))),
        )
    )

    f_view["realized_return_pct"] = np.where(
        f_view["allocated_cost"] != 0,
        f_view["realized_pnl"] / f_view["allocated_cost"] * 100.0,
        0.0,
    )
    f_view["date"] = f_view["day"]
    
    # Calculate averages for display
    f_view["avg_buy_price"] = np.where(f_view["sell_qty"] > 0, f_view["allocated_cost"] / f_view["sell_qty"], 0.0)
    f_view["avg_sell_price"] = np.where(f_view["sell_qty"] > 0, f_view["sell_cash_in"] / f_view["sell_qty"], 0.0)

    profit_label = T(lang, "Profit", "獲利")
    loss_label = T(lang, "Loss", "虧損")
    f_view["sign"] = np.where(f_view["realized_pnl"] >= 0, profit_label, loss_label)

    f_sorted = f_view.sort_values(["date", "stock", "type_display"]).copy()
    f_sorted["cum_pnl"] = f_sorted["realized_pnl"].cumsum()

    # KPIs
    total_pnl = float(f_sorted["realized_pnl"].sum())
    trades = int(len(f_sorted))
    win_rate = float((f_sorted["realized_pnl"].to_numpy() > 0).mean()) if trades else 0.0
    total_pl_pct = (total_pnl / float(INVESTMENT_TWD) * 100.0) if INVESTMENT_TWD else 0.0
    total_pl_pct = (total_pnl / float(INVESTMENT_TWD) * 100.0) if INVESTMENT_TWD else 0.0
    trade_volume = float(f_sorted["allocated_cost"].sum()) * CURRENCY_RATE

    total_color = PROFIT_COLOR if total_pnl > 0 else (LOSS_COLOR if total_pnl < 0 else "#FFFFFF")
    plpct_color = PROFIT_COLOR if total_pl_pct > 0 else (LOSS_COLOR if total_pl_pct < 0 else "#FFFFFF")
    
    # Win rate: 50% is neutral (White)
    wr_val = win_rate * 100.0
    if abs(wr_val - 50.0) < 0.01:
        win_color = "#FFFFFF"
    elif wr_val > 50.0:
        win_color = PROFIT_COLOR
    else:
        win_color = LOSS_COLOR

    st.markdown(f"### {T(lang, 'Key Metrics', '關鍵指標')}")
    k1, k2, k3, k4, k5 = st.columns([1, 1, 1, 1, 1], gap="medium")

    # Sub-win rates
    def calc_wr(df_in):
        if df_in.empty: return 0.0
        return (df_in["realized_pnl"] > 0).mean() * 100.0

    wr_day = calc_wr(f_sorted[f_sorted["type_key"] == "day_trade"])
    wr_cash = calc_wr(f_sorted[f_sorted["type_key"] == "cash"])

    # Sub-trades
    n_day = len(f_sorted[f_sorted["type_key"] == "day_trade"])
    n_cash = len(f_sorted[f_sorted["type_key"] == "cash"])

    with k1:
        KPI_CARD(T(lang, "Total P/L", "總損益"), fmt_signed_money(total_pnl, CURRENCY_RATE, CURRENCY_SYMBOL), total_color, "&nbsp;")
    with k2:
        # Percentage is invariant to currency
        base_cap_converted = float(INVESTMENT_TWD) * CURRENCY_RATE
        KPI_CARD(T(lang, "Total P/L %", "總損益%"), fmt_signed_pct(total_pl_pct), plpct_color, "&nbsp;")
    with k3:
        sub_wr = f"{T(lang, 'Day Trade', '當沖')}: {wr_day:.0f}%  {T(lang, 'Cash', '現股')}: {wr_cash:.0f}%"
        KPI_CARD(T(lang, "Win rate", "勝率"), f"{win_rate*100:.1f}%", win_color, sub_wr)
    with k4:
        sub_tr = f"{T(lang, 'Day Trade', '當沖')}: {n_day}  {T(lang, 'Cash', '現股')}: {n_cash}"
        KPI_CARD(T(lang, "Trades", "筆數"), f"{trades}", NEUTRAL_PURPLE, sub_tr)
    with k5:
        # Split fee/tax (Converted)
        total_fee = float(f_sorted["total_fee"].sum())
        total_tax = float(f_sorted["total_tax"].sum())
        
        # Sub label with full text
        fee_str = fmt_money(total_fee, CURRENCY_RATE, CURRENCY_SYMBOL)
        tax_str = fmt_money(total_tax, CURRENCY_RATE, CURRENCY_SYMBOL)
        
        sub_lbl = f"{T(lang, 'Fee', '手續費')}: {fee_str}  {T(lang, 'Tax', '稅')}: {tax_str}"
        KPI_CARD(T(lang, "Trade volume", "交易量"), fmt_money(trade_volume, 1.0, CURRENCY_SYMBOL), NEUTRAL_BLUE, sub_lbl)

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

        # Aggregate to Daily Close for a smooth "Pro" curve
        daily_agg = f_sorted.groupby("date", as_index=False)["realized_pnl"].sum()
        daily_agg["cum_pnl"] = daily_agg["realized_pnl"].cumsum()

        scaled_cum, unit_lbl, _ = scale_unit(daily_agg["cum_pnl"], lang, CURRENCY_RATE)

        # Dynamic color based on final result? or just a pro theme color.
        # User wants split coloring based on 0 line.
        
        # Prepare Split Data (Augment with zero crossings)
        # We need to act on 'scaled_cum' which matches x-axis 'daily_agg["date"]'
        # Let's create a temp DF
        df_chart = pd.DataFrame({
            "date": daily_agg["date"], 
            "val": scaled_cum.values
        })
        df_chart = augment_zero_crossings(df_chart, "date", "val")
        
        vals = df_chart["val"].to_numpy()
        dates_aug = df_chart["date"]
        
        # Use np.nan to hide the line when it's "inactive" (on the other side of zero)
        # This prevents the "red line on zero" artifact
        y_pos = np.where(vals >= 0, vals, np.nan)
        y_neg = np.where(vals <= 0, vals, np.nan)
        
        profit_fill = hex_to_rgba(PROFIT_COLOR, 0.15)
        loss_fill = hex_to_rgba(LOSS_COLOR, 0.15)

        fig_eq = go.Figure()
        
        # Negative Trace (Loss)
        fig_eq.add_trace(
            go.Scatter(
                x=dates_aug,
                y=y_neg,
                mode="lines",
                name=T(lang, "Cumulative Loss", "累計虧損"),
                line=dict(width=2, color=LOSS_COLOR),
                fill="tozeroy",
                fillcolor=loss_fill,
                hoverinfo="x+y",
                showlegend=False,
            )
        )

        # Positive Trace (Profit)
        fig_eq.add_trace(
            go.Scatter(
                x=dates_aug,
                y=y_pos,
                mode="lines",
                name=T(lang, "Cumulative Profit", "累計獲利"),
                line=dict(width=2, color=PROFIT_COLOR),
                fill="tozeroy",
                fillcolor=profit_fill,
                hoverinfo="x+y",
                showlegend=False,
            )
        )
        
        # Add marker/label for the latest point
        if not daily_agg.empty:
            last_idx = daily_agg.index[-1]
            last_date = daily_agg["date"].iloc[-1]
            last_val = scaled_cum.iloc[-1]
            last_txt = f"{last_val:,.2f} {unit_lbl}"
            
            # Determine color for the marker based on final value
            final_color = PROFIT_COLOR if last_val >= 0 else LOSS_COLOR

            fig_eq.add_trace(
                go.Scatter(
                    x=[last_date],
                    y=[last_val],
                    mode="markers+text",
                    text=[last_txt],
                    textposition="top left",
                    textfont=dict(size=11, color="#EAEAEA"),
                    marker=dict(size=6, color=final_color, line=dict(width=1, color="white")),
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
        
        # Calculate range padding
        if not daily_agg.empty:
            min_date = daily_agg["date"].min()
            max_date = daily_agg["date"].max()
            # Add small padding (e.g. 5%) or tight? User asked for tight left.
            # We can set range explicitly.
            range_x = [min_date, max_date]
        else:
            range_x = None

        fig_eq.update_layout(
            title=dict(
                 text=T(lang, "Cumulative Realized P/L (Daily Close)", "累計已實現損益（日結）"),
                 font=dict(size=18)
            ),
            xaxis=dict(
                title="",
                dtick=7 * 24 * 60 * 60 * 1000, # Weekly ticks
                tickformat="%b %d" if lang != "中文" else "%m/%d",
                showgrid=True,
                gridcolor="rgba(255,255,255,0.08)",
                gridwidth=1,
                range=range_x, # Tight range
            ),
            yaxis=dict(
                title=f"{T(lang, 'P/L', '損益')} ({unit_lbl})",
                showgrid=True,
                gridcolor="rgba(255,255,255,0.08)",
            ),
            height=460,
            margin=dict(l=10, r=20, t=60, b=10),
            legend_title_text="",
            hovermode="x unified",
        )

        # fig_eq = add_month_major_lines(fig_eq, daily_agg["date"]) # Optional, cleaning up to look simpler
        add_zero_line(fig_eq, axis="y", color="#A9B1BD", width=2, dash="dash")
        st.plotly_chart(fig_eq, width="stretch")

        hr()
        
        # New Visualizations
        st.subheader(T(lang, "P/L Distribution", "損益分佈"))
        fig_hist = plot_pnl_distribution(f, lang, PROFIT_COLOR, LOSS_COLOR, CURRENCY_RATE)
        st.plotly_chart(fig_hist, width="stretch")

        hr()
        st.subheader(T(lang, "Per-stock Contribution", "各股貢獻"))

        by_stock = f_view.groupby("stock", as_index=False)["realized_pnl"].sum()
        profit_label = T(lang, "Profit", "獲利")
        loss_label = T(lang, "Loss", "虧損")
        by_stock["sign"] = np.where(by_stock["realized_pnl"] >= 0, profit_label, loss_label)
        by_stock["abs"] = by_stock["realized_pnl"].abs()
        by_stock = by_stock.sort_values("abs", ascending=False)

        scaled_vals, unit_lbl2, _ = scale_unit(by_stock["realized_pnl"], lang, CURRENCY_RATE)
        by_stock["_scaled_pnl"] = scaled_vals

        sorted_df = by_stock.sort_values("_scaled_pnl")
        fig_bar = px.bar(
            sorted_df,
            x="_scaled_pnl",
            y="stock",
            orientation="h",
            color="sign",
            color_discrete_map={profit_label: PROFIT_COLOR, loss_label: LOSS_COLOR},
            text=sorted_df["_scaled_pnl"].map(lambda v: f"{v:+.2f} {unit_lbl2}"),
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

    # -------------------- Leaderboard --------------------
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
                    "total_pnl_pct": T(lang, "P/L %", "損益%"),
                    "win_rate_pct": T(lang, "Win rate %", "勝率%"),
                }
            )
            out[T(lang, "Total P/L", "總損益")] = out[T(lang, "Total P/L", "總損益")].round(0).astype(int)
            out[T(lang, "P/L %", "損益%")] = out[T(lang, "P/L %", "損益%")].round(2)
            out[T(lang, "Win rate %", "勝率%")] = out[T(lang, "Win rate %", "勝率%")].round(1)
            out[T(lang, "Trades", "筆數")] = out[T(lang, "Trades", "筆數")].astype(int)

            return out[
                [
                    T(lang, "Stock", "股票"),
                    T(lang, "Total P/L", "總損益"),
                    T(lang, "P/L %", "損益%"),
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
                        T(lang, "Total P/L", "總損益"): lambda x: fmt_signed_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                        T(lang, "P/L %", "損益%"): "{:+.2f}",
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
                        T(lang, "Total P/L", "總損益"): lambda x: fmt_signed_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                        T(lang, "P/L %", "損益%"): "{:+.2f}",
                        T(lang, "Trades", "筆數"): "{:.0f}",
                        T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    }
                ),
                width="stretch",
                height=420,
            )

    # -------------------- Monthly report --------------------
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

        m_cum["prev_cum_pnl"] = m_cum["cum_pnl"].shift(1).fillna(0.0)
        m_cum["month_pnl"] = m_cum["cum_pnl"] - m_cum["prev_cum_pnl"]
        
        # Start equity for the month = Initial Investment + Previous Cumulative P/L
        m_cum["start_equity"] = float(INVESTMENT_TWD) + m_cum["prev_cum_pnl"]
        
        m_cum["month_pct"] = np.where(
            m_cum["start_equity"] != 0, 
            m_cum["month_pnl"] / m_cum["start_equity"] * 100.0, 
            0.0
        )
        
        m_cum["cum_pl_pct"] = np.where(INVESTMENT_TWD != 0, m_cum["cum_pnl"] / float(INVESTMENT_TWD) * 100.0, 0.0)
        m_cum["cum_win_rate_pct"] = np.where(m_cum["cum_trades"] > 0, m_cum["cum_wins"] / m_cum["cum_trades"] * 100.0, 0.0)

        table = pd.DataFrame(
            {
                T(lang, "Month", "月份"): pd.to_datetime(m_cum["month"]).dt.strftime("%Y-%m"),
                # T(lang, "Total P/L", "總損益"): m_cum["cum_pnl"].round(0).astype(int),
                # T(lang, "Total P/L %", "總損益%"): m_cum["cum_pl_pct"].round(2),
                T(lang, "Month P/L", "月損益"): m_cum["month_pnl"].round(0).astype(int),
                T(lang, "Month %", "月報酬%"): m_cum["month_pct"].round(2),
                T(lang, "Trades", "筆數"): m_cum["cum_trades"].astype(int),
                T(lang, "Win rate %", "勝率%"): m_cum["cum_win_rate_pct"].round(1),
                T(lang, "Trade volume", "交易量"): m_cum["cum_volume"].round(0).astype(int),
            }
        )

        st.dataframe(
            make_trade_styler(table, PROFIT_COLOR, LOSS_COLOR).format(
                {
                    # T(lang, "Total P/L", "總損益"): "{:,.0f}",
                    # T(lang, "Total P/L %", "總損益%"): "{:.2f}",
                    T(lang, "Month P/L", "月損益"): lambda x: fmt_signed_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                    T(lang, "Month %", "月報酬%"): "{:+.2f}",
                    T(lang, "Trades", "筆數"): "{:.0f}",
                    T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    T(lang, "Trade volume", "交易量"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                }
            ),
            width="stretch",
        )

        hr()
        st.subheader(T(lang, "Cumulative P/L by Month", "月度累計損益"))

        scaled_vals, unit_lbl_m, _ = scale_unit(m_cum["cum_pnl"], lang, CURRENCY_RATE)
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
            xaxis=dict(
                type="date",
                tickformat="%b %Y" if lang != "中文" else "%Y-%m",
                dtick="M1", # Force 1 month ticks to prevent repetition
            )
        )
        add_zero_line(fig_m, axis="y", color="#A9B1BD", width=3, dash="dash")
        st.plotly_chart(fig_m, width="stretch")

    # -------------------- Trades --------------------
    with tab_trades:
        st.subheader(T(lang, "Trade History", "交易紀錄"))

        view = f_view.sort_values(["date", "stock", "type_display"], ascending=[False, True, True]).copy()
        
        # Consistent date formatting for table
        if lang == "中文":
            view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m-%d")
        else:
            view["date"] = pd.to_datetime(view["date"]).dt.strftime("%b %d, %Y")

        view_show = view.rename(
            columns={
                "date": T(lang, "Date", "日期"),
                "stock": T(lang, "Stock", "股名"),
                "type_display": T(lang, "Type", "類型"),
                "allocated_cost": T(lang, "Total Buy Cost", "買入總額"),
                "avg_buy_price": T(lang, "Avg Buy Price", "買入均價"),
                "sell_cash_in": T(lang, "Total Sell Proceeds", "賣出總額"),
                "avg_sell_price": T(lang, "Avg Sell Price", "賣出均價"),
                "realized_pnl": T(lang, "Realized P/L", "已實現損益"),
                "realized_return_pct": T(lang, "Realized %", "已實現%"),
            }
        )

        df_show = view_show[
            [
                T(lang, "Date", "日期"),
                T(lang, "Stock", "股名"),
                T(lang, "Type", "類型"),
                T(lang, "Avg Buy Price", "買入均價"),
                T(lang, "Total Buy Cost", "買入總額"),
                T(lang, "Avg Sell Price", "賣出均價"),
                T(lang, "Total Sell Proceeds", "賣出總額"),
                T(lang, "Realized P/L", "已實現損益"),
                T(lang, "Realized %", "已實現%"),
            ]
        ].copy()

        st.dataframe(
            make_trade_styler(df_show, PROFIT_COLOR, LOSS_COLOR).format(
                {
                    T(lang, "Avg Buy Price", "買入均價"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL, 2),
                    T(lang, "Total Buy Cost", "買入總額"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                    T(lang, "Avg Sell Price", "賣出均價"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL, 2),
                    T(lang, "Total Sell Proceeds", "賣出總額"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                    T(lang, "Realized P/L", "已實現損益"): lambda x: fmt_signed_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                    T(lang, "Realized %", "已實現%"): "{:+.2f}",
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
