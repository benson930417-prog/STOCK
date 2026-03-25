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
        if typed:
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
        if v < 0:
            return f"-{currency}{abs(v):,.0f}"
        sign = "+" if v > 0 else ""
        return f"{sign}{currency}{v:,.0f}"
    except Exception:
        return str(x)


def fmt_money(x, rate: float = 1.0, currency: str = "", decimals: int = 0) -> str:
    try:
        v = float(x) * rate
        if v < 0:
            return f"-{currency}{abs(v):,.{decimals}f}"
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
    # Currency conversion first
    vals = values * rate
    
    # If using Euro (implied by rate != 1.0), prioritize Euro formatting
    # Or strict check? For now rate != 1.0 is the flag.
    if rate != 1.0:
         # Euro mode: Use k/M or just plain EUR
         return vals, "€", 1.0

    max_abs = float(np.nanmax(np.abs(values.to_numpy()))) if len(values) else 0.0
    if lang == "中文":
        if max_abs >= 1e4:
            return values / 1e4, "萬", 1e4
        if max_abs >= 1e3:
            return values / 1e3, "千", 1e3
        return values, "元", 1.0
    else:
        # TWD English mode
        # User requested FULL NUMBER for Euro, no K/M scaling.
        # But this is now TWD English mode.
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
        return T(lang, f"{h} hours ago", f"{h} 小時前")
    d = sec // 86400
    return T(lang, f"{d} days ago", f"{d} 天前")


def hex_to_rgba(color_str: str, alpha: float = 0.2) -> str:
    if color_str.startswith("rgb"):
        import re
        nums = re.findall(r'\d+', color_str)
        if len(nums) >= 3:
            return f"rgba({nums[0]}, {nums[1]}, {nums[2]}, {alpha})"
    hex_color = color_str.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join([c * 2 for c in hex_color])
    return f"rgba({int(hex_color[:2], 16)}, {int(hex_color[2:4], 16)}, {int(hex_color[4:], 16)}, {alpha})"


def get_twd_to_eur_rate():
    # Cache in session to avoid spamming API on rerun
    # Structure: {"rate": float, "date": str, "timestamp": float}
    if "eur_rate_data" in st.session_state:
        return st.session_state["eur_rate_data"]
    
    try:
        url = "https://api.exchangerate-api.com/v4/latest/TWD"
        r = requests.get(url, timeout=3.0)
        r.raise_for_status()
        data = r.json()
        rate = float(data["rates"]["EUR"])
        
        # also capture date/timestamp from API if available or use current
        updated_date = data.get("date", "")
        updated_ts = data.get("time_last_updated", 0)
        
        res = {
            "rate": rate,
            "date": updated_date,
            "timestamp": updated_ts
        }
        st.session_state["eur_rate_data"] = res
        return res
    except Exception:
        return None


def get_market_data(symbol, days=365):
    # Fetch Market data (e.g. ^TWII, ^DJI) from Yahoo Finance Chart API
    # Cache in session
    cache_key = f"market_data_{symbol}_{days}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    try:
        # Range: roughly days -> 1y, 2y, 5y etc.
        # Yahoo ranges: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
        range_str = "1y"
        if days > 365 * 2: range_str = "5y"
        elif days > 365: range_str = "2y"
        
        # URL encode symbol if needed, but simple ones are safe
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_str}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5.0)
        r.raise_for_status()
        data = r.json()
        
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        
        # Meta for last update time
        meta = result.get("meta", {})
        last_trade_ts = meta.get("regularMarketTime", 0)
        
        # Create DataFrame
        df = pd.DataFrame({"ts": timestamps, "close": closes})
        df["date"] = pd.to_datetime(df["ts"], unit="s").dt.normalize()
        df = df.dropna().sort_values("date")
        
        # Store metadata
        df.attrs["last_update"] = last_trade_ts

        
        st.session_state[cache_key] = df
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400)
def get_tw_stock_options():
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    options = {}
    
    # 1. TWSE
    try:
        r1 = requests.get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL', verify=False, timeout=5)
        if r1.status_code == 200:
            for item in r1.json():
                code = str(item.get('Code', '')).strip()
                name = str(item.get('Name', '')).strip()
                if (len(code) == 4 and code.isdigit()) or code.startswith('00'):
                    lbl = f"{code} {name}"
                    options[lbl] = f"{code}.TW"
    except Exception:
        pass

    # 2. TPEX
    try:
        r2 = requests.get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes', verify=False, timeout=5)
        if r2.status_code == 200:
            for item in r2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                name = str(item.get('CompanyName', '')).strip()
                if len(code) == 4 and code.isdigit() and not code.startswith('0'):
                    lbl = f"{code} {name}"
                    options[lbl] = f"{code}.TWO"
    except Exception:
        pass

    return options

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

    inventory = defaultdict(deque)  # stock -> deque lots {qty, cps}
    realized_rows = []

    def sell_against_inventory(stock, pool, date, qty, cash_in, sell_fee_tot, sell_tax_tot):
        remaining = int(qty)
        allocated_cost = 0.0
        allocated_buy_fee = 0.0

        while remaining > 0:
            if not inventory[stock]:
                import streamlit as st
                st.warning(
                    f"Sell without inventory: {stock} on {pd.to_datetime(date).date()} sell_qty={remaining}. "
                    "Master may be missing older BUYs. Assuming zero cost basis for remaining shares."
                )
                break
                
            lot = inventory[stock][0]
            take = min(remaining, lot["qty"])
            allocated_cost += take * lot["cps"]
            allocated_buy_fee += take * lot["fee_per_share"]

            lot["qty"] -= take
            remaining -= take
            if lot["qty"] == 0:
                inventory[stock].popleft()

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
                gross_cost=float(allocated_cost - allocated_buy_fee),
                gross_sell_cash_in=float(cash_in + sell_fee_tot + sell_tax_tot),
                method_key="cash",
                type_key="cash",
                pool_key=pool,
            )
        )

    for stock, sdf in df.groupby("股名", sort=False):
        sdf = sdf.sort_values("日期")

        for date, ddf in sdf.groupby(sdf["日期"].dt.date, sort=False):
            buys = ddf[ddf["淨收付金額"] < 0].copy()
            sells = ddf[ddf["淨收付金額"] > 0].copy()

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
                        gross_cost=float(intraday_cost - intraday_buy_fee),
                        gross_sell_cash_in=float(intraday_cash + intraday_sell_fee + intraday_sell_tax),
                        method_key="day_trade",
                        type_key="day_trade",
                        pool_key=pool_of(intraday_qty),
                    )
                )

            # 2) Remaining buys -> inventory
            for lot in list(day_buy_lots):
                if lot["qty"] > 0:
                    inventory[stock].append({
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
                    sell_against_inventory(stock, pool_of(qty), date, qty, cash_in, s_fee, s_tax)

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
        if low in ["realized %", "total p/l %", "month %", "p/l %"] or col in ["已實現%", "總損益%", "報酬%", "月報酬%", "損益%"]:
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
    scaled_vals, unit_txt, divisor = scale_unit(df["realized_pnl"], lang, rate)
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
        
        # FORCE STEP SIZE based on user request / unit
        # Use divisor to determine correct step in scaled units.
        # Target: 50000 TWD or 1000 EUR
        
        target_step = 50000.0
        if "€" in unit_txt or unit_txt == "EUR":
            target_step = 1000.0
            
        d_val = divisor if divisor else 1.0
        fd_width = target_step / d_val

        # Enforce a minimum width to prevent needle-like bars
        min_w = 1.0
        if unit_txt in ["百萬", "M TWD"]:
            min_w = 0.05
        elif unit_txt in ["萬", "K TWD"]:
            min_w = 0.1
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
    # Create categorical labels for bins
    # e.g. "0~5", "5~10", "-5~0"
    bin_labels = []
    
    def fmt_edge(val):
        # Format edge value smoothly
        # For Eur (1000 step) -> 1k, 2k...
        # For Wan (5 step) -> 5, 10...
        if unit_txt == "€":
             if abs(val) >= 1000:
                 return f"{int(val/1000)}k"
             return f"{int(val)}"
        else:
             if val == int(val):
                 return f"{int(val)}"
             else:
                 return f"{val:.3f}".rstrip("0").rstrip(".")

    for i in range(len(bin_edges) - 1):
        left = bin_edges[i]
        right = bin_edges[i+1]
        
        l_str = fmt_edge(left)
        r_str = fmt_edge(right)
        
        # User request: "negative should be less (-) to more (-)"
        # This implies swapping the order for negative bins to read "closer to 0 ~ further from 0"
        # Standard: -10 ~ -5. Desired: -5 ~ -10.
        if right <= 0:
             start_s, end_s = r_str, l_str
        else:
             start_s, end_s = l_str, r_str
        
        # Add unit to the label
        # Use ' to ' as separator to distinguish from negative sign
        sep = " to "
        # Revert to prefix for Euro as requested (+€1000)
        # Actually user wants "sign+euro+value".
        # fmt_edge returns "7k" or "7".
        # We need to construct labels like "-€7k to -€8k" or "+€0 to +€1k"
        
        # Helper to strict format edge
        def fmt_lbl(val_edge):
             s_edge = "+" if val_edge > 0 else "-" if val_edge < 0 else ""
             # remove sign from fmt_edge result if any (it usually doesn't have sign if we just pass abs, but let's be safe)
             # fmt_edge uses raw value.
             # let's map val_edge to abs val for fmt_edge
             abs_v = abs(val_edge)
             
             # Re-use logic:
             txt_v = fmt_edge(abs_v) # e.g. "7k"
             
             if unit_txt == "€":
                 return f"{s_edge}€{txt_v}"
             else:
                 return f"{s_edge}{txt_v}{unit_txt}"

        l_str_fmt = fmt_lbl(left)
        r_str_fmt = fmt_lbl(right)
        
        if right <= 0:
             # swap for negative range readability: -5k to -10k
             label = f"{r_str_fmt}{sep}{l_str_fmt}"
        else:
             label = f"{l_str_fmt}{sep}{r_str_fmt}"
            
        bin_labels.append(label)

    # Color condition: center >= 0 is profit
    # IMPORTANT: center > 0 or center < 0. What about strictly 0?
    # Our bins are aligned to 0. So 0~5 is positive. -5~0 is negative.
    colors = []
    for i in range(len(bin_edges) - 1):
        mid = (bin_edges[i] + bin_edges[i+1]) / 2
        if mid > 0:
            colors.append(profit_color)
        else:
             # This handles -5~0 as negative
            colors.append(loss_color)

    # Filter out zero-count labels
    times_unit = "次" if lang == "中文" else "x"
    text_labels = [f"{int(x)}{times_unit}" if x > 0 else "" for x in counts]

    fig = go.Figure(
        data=go.Bar(
            x=bin_labels,
            y=counts,
            marker_color=colors,
            text=text_labels,
            textposition="outside",
            # Make bars look connected like a histogram
            # Width needs to be calculated or let plotly handle it?
            # Setting width to (edge[1] - edge[0]) * 0.9 close gaps
        )
    )
    
    # Allow text to overflow
    fig.update_traces(cliponaxis=False)

    # Calculate approximate bar width
    if len(bin_edges) > 1:
        # Avoid forcing manual width if possible, let plotly handle or use gaps
        # But if we want connected look...
        pass 
        # width = (bin_edges[-1] - bin_edges[0]) / len(counts)
        # fig.update_traces(width=width * 0.95)

    fig.update_layout(
        title=T(lang, "P/L Distribution", "損益分佈"),
        xaxis_title=T(lang, 'Realized P/L', '已實現損益'),
        yaxis_title=T(lang, "Count", "筆數"),
        height=380,
        bargap=0.1,
        margin=dict(l=10, r=10, t=60, b=10), # Increased top margin to prevent title/label clash
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.08)",
            # Add padding to top of y-axis to fit labels
            # range=[0, max(counts)*1.15] if len(counts) else None
        )
    )
    
    # Fix Zero Line: Find index where edges cross 0
    # bin_edges has size len(bin_labels) + 1.
    # The split is at edge value 0.
    zero_idx = -1
    for i, e in enumerate(bin_edges):
        if e == 0:
            zero_idx = i
            break
            
    if zero_idx != -1:
         # Plotly categorical axis indices are 0, 1, 2... for bins.
         # Edge i=0 is left of bin 0. Edge i=1 is right of bin 0 / left of bin 1.
         # So edge index 'zero_idx' corresponds to position 'zero_idx - 0.5'
         # Wait, let's verify:
         # Bins: [A, B, C] -> indices 0, 1, 2.
         # Edges: [e0, e1, e2, e3]
         # Bin 0 is e0-e1. Bin 1 is e1-e2.
         # If e1 is 0, then the split is between Bin 0 and Bin 1.
         # The visual coordinate for center of Bin 0 is 0. Center of Bin 1 is 1.
         # The boundary is 0.5.
         # So position = zero_idx - 0.5.
         fig.add_vline(x=zero_idx - 0.5, line_width=2, line_dash="dash", line_color="#A9B1BD")
    
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
        st.markdown(f"## {T(lang, 'Preferences', '設定')}")
        currency_opt = st.radio(
            "Currency / 幣別",
            ["TWD (NTD)", "EUR (€)"],
            index=0,
            horizontal=True,
        )
        
        # Display Rate Info if available (always try to fetch to show user)
        # But maybe only if user cares? User request: "display... on the section"
        # It implies showing it always or at least when EUR is relevant.
        # Let's fetch it if possible.
        rate_info = get_twd_to_eur_rate()
        if rate_info:
            r_val = rate_info["rate"]
            # 1 EUR = ? TWD => 1 / r_val
            if r_val > 0:
                eur_to_twd = 1.0 / r_val
                # formatting date
                # api returns date string usually YYYY-MM-DD
                date_str = rate_info.get("date", "")
                
                st.caption(
                    f"1 EUR ≈ {eur_to_twd:.2f} TWD"
                )
        
        # Color theme strictly derived from language selection
        tw_colors = (lang == "中文")
        hr()

        sidebar_recent_update(lang)
        hr()

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
    # Colors
    # Colors (Controlled by toggle)
    if tw_colors:
        # Taiwan: Red = Profit, Green = Loss
        PROFIT_COLOR = "#E74C3C" 
        LOSS_COLOR = "#2ECC71"
        NEW_COLOR = "#ff80ab"
        REMOVED_COLOR = "#ccff00"
    else:
        # Western: Green = Profit, Red = Loss
        PROFIT_COLOR = "#2ECC71"
        LOSS_COLOR = "#E74C3C"
        NEW_COLOR = "#ccff00"
        REMOVED_COLOR = "#ff80ab"
        
    NEUTRAL_BLUE = "#4C78A8"
    NEUTRAL_PURPLE = "#6F42C1"

    # Currency Rate Logic
    CURRENCY_RATE = 1.0
    CURRENCY_SYMBOL = ""
    
    curr_code = "TWD"
    if "EUR" in currency_opt:
        curr_code = "EUR"

    if curr_code == "EUR":
        # Try to get EUR rate
        rate_info = get_twd_to_eur_rate()
        if rate_info:
            CURRENCY_RATE = rate_info["rate"]
            CURRENCY_SYMBOL = "€"
        else:
            # Fallback
            if "currency_fail_toast" not in st.session_state:
                st.toast("Currency API failed. Displaying TWD.", icon="⚠️")
                st.session_state["currency_fail_toast"] = True
            CURRENCY_RATE = 1.0
            CURRENCY_SYMBOL = ""
    else:
        CURRENCY_RATE = 1.0
        CURRENCY_SYMBOL = ""
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

    chrono_df = raw_df.copy()
    chrono_df["date"] = pd.to_datetime(chrono_df["日期"]).dt.floor("D")
    
    # 🚨 CRITICAL FIX: Aggregate cash flows by day FIRST, then cumsum.
    # Because Taiwan bank settles T+2 as a single daily net transfer, 
    # intraday sequences (buy then sell) don't trigger multiple bank transfers.
    daily_net_flows = chrono_df.groupby("date")["淨收付金額"].sum().reset_index()
    daily_net_flows = daily_net_flows.sort_values("date").reset_index(drop=True)
    daily_net_flows["invested_capital"] = -daily_net_flows["淨收付金額"].cumsum()
    
    daily_base = daily_net_flows.copy()
    
    # For chart scaling, we use the expanding max to prevent artificial spikes when withdrawing funds
    daily_base["dynamic_base"] = daily_base["invested_capital"].expanding().max().clip(lower=1.0)
    
    # Mathematically derived Base Capital (Final Active Principal before liquidation)
    # This precisely matches the user's derivation: Proceeds - Total PNL
    active_bases = daily_base[daily_base["invested_capital"] >= 100]["invested_capital"]
    peak_base = float(active_bases.iloc[-1]) if not active_bases.empty else 1.0

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
            gross_cost=("gross_cost", "sum"),
            gross_sell_cash_in=("gross_sell_cash_in", "sum"),
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
    f_view["avg_buy_price"] = np.where(f_view["sell_qty"] > 0, f_view["gross_cost"] / f_view["sell_qty"], 0.0)
    f_view["avg_sell_price"] = np.where(f_view["sell_qty"] > 0, f_view["gross_sell_cash_in"] / f_view["sell_qty"], 0.0)

    profit_label = T(lang, "Profit", "獲利")
    loss_label = T(lang, "Loss", "虧損")
    f_view["sign"] = np.where(f_view["realized_pnl"] >= 0, profit_label, loss_label)

    f_sorted = f_view.sort_values(["date", "stock", "type_display"]).copy()
    f_sorted["cum_pnl"] = f_sorted["realized_pnl"].cumsum()

    # KPIs
    total_pnl = float(f_sorted["realized_pnl"].sum())
    trades = int(len(f_sorted))
    
    # Calculate Time-Weighted Return (TWR) for the KPI
    # 1. Get daily pnl
    d_pnl = f_sorted.groupby("date")["realized_pnl"].sum().reset_index()
    # 2. Merge with daily_base to get dynamic_base
    d_merge = pd.merge_asof(d_pnl.sort_values("date"), daily_base.sort_values("date"), on="date")
    d_merge["dynamic_base"] = d_merge["dynamic_base"].ffill().bfill().replace(0, 1.0)
    # 3. Daily returns r_t
    d_merge["r_t"] = d_merge["realized_pnl"] / d_merge["dynamic_base"]
    # 4. Cumulative TWR: product(1 + r_t) - 1
    final_twr = ((1.0 + d_merge["r_t"]).prod() - 1.0) * 100.0
    
    win_rate = float((f_sorted["realized_pnl"].to_numpy() > 0).mean()) if trades else 0.0
    total_pl_pct = final_twr # Use TWR for the headline percentage
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
        # User request: include Base Capital (Dynamic Max Drawdown) on total PL kpi
        base_cap_converted = float(peak_base) * CURRENCY_RATE
        base_cap_str = fmt_money(base_cap_converted, 1.0, CURRENCY_SYMBOL)
        base_lbl = T(lang, "Base", "累計投入本金")
        KPI_CARD(T(lang, "Total P/L", "總損益"), fmt_signed_money(total_pnl, CURRENCY_RATE, CURRENCY_SYMBOL), total_color, f"{base_lbl}: {base_cap_str}")
    with k2:
        # Percentage is invariant to currency
        # Calculate vs TAIEX
        alpha_text = "&nbsp;"
        try:
             # Get TAIEX data for the same range
             d_min = f_sorted["date"].min()
             d_max = f_sorted["date"].max()
             days_range = (d_max - d_min).days + 10
             
             tw_df = get_market_data("^TWII", days=max(30, days_range))
             if not tw_df.empty:
                 # Find closest close to start and end
                 # We need open of start_date (or close of prev day) vs close of end_date
                 # Simplified: Close of first available day in range vs Close of last available day
                 
                 mask = (tw_df["date"] >= d_min) & (tw_df["date"] <= d_max)
                 tw_rel = tw_df[mask]
                 
                 if not tw_rel.empty:
                     start_price = tw_rel["close"].iloc[0]
                     end_price = tw_rel["close"].iloc[-1]
                     if start_price > 0:
                         tw_pct = (end_price - start_price) / start_price * 100.0
                         diff = total_pl_pct - tw_pct
                         
                         # Format text
                         lbl = T(lang, "vs TAIEX", "加權指數")
                         sign_str = "+" if diff > 0 else "-" if diff < 0 else "" 
                         # Use logic: 'Ahead' or 'Behind' or just signed diff
                         # User asked: "how much behind or ahead"
                         # "Ahead by 5%" or "Behind by 5%"
                         if diff > 0:
                             status = T(lang, "Ahead", "領先")
                         else:
                             status = T(lang, "Behind", "落後")
                             
                         alpha_text = f"{status} {T(lang,'TAIEX','加權')} {abs(diff):.2f}%"
        except Exception:
             pass

        # TWR is standard for strategy performance.
        KPI_CARD(T(lang, "Personal Return %", "個人報酬率 %"), fmt_signed_pct(total_pl_pct), plpct_color, alpha_text)
    with k3:
        sub_wr = f"{T(lang, 'Day Trade', '當沖')}: {wr_day:.1f}%  {T(lang, 'Cash', '現股')}: {wr_cash:.1f}%"
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

    tabs_dict = {
        "overview": T(lang, "Overview", "總覽"),
        "leader": T(lang, "Leaderboard", "排行"),
        "monthly": T(lang, "Monthly report", "月報"),
        "trades": T(lang, "Trades", "交易"),
        "etf": T(lang, "Active ETFs", "主動型 ETF")
    }
    
    if hasattr(st, "segmented_control"):
        active_tab = st.segmented_control(
            "MainTabs",
            options=list(tabs_dict.keys()),
            format_func=lambda x: tabs_dict[x],
            default="overview",
            key="main_tab",
            label_visibility="collapsed"
        )
    else:
        active_tab = st.radio(
            "MainTabs",
            options=list(tabs_dict.keys()),
            format_func=lambda x: tabs_dict[x],
            index=0,
            horizontal=True,
            key="main_tab",
            label_visibility="collapsed"
        )
    if not active_tab: active_tab = "overview"

    # -------------------- Overview --------------------
    if active_tab == "overview":
        
        # --- Definitions ---
        market_keys = ["TAIEX", "Dow Jones", "S&P 500", "PHLX Semi", "NASDAQ"]
        market_symbols = {
             "TAIEX": "^TWII",
             "Dow Jones": "^DJI",
             "S&P 500": "^GSPC",
             "PHLX Semi": "^SOX",
             "NASDAQ": "^IXIC"
        }
        MARKET_ZH = {
             "TAIEX": "加權指數",
             "Dow Jones": "道瓊工業",
             "S&P 500": "標普500",
             "PHLX Semi": "費城半導體",
             "NASDAQ": "那斯達克"
        }
        def fmt_mkt(k):
             return f"{k} {MARKET_ZH.get(k, '')}" if lang == "中文" else k

        tw_stock_keys = []
        int_stock_keys = ["TSMC ADR", "1306 TPX", "1321 NK225", "VOO"]
        
        stock_symbols = {
             "TSMC ADR": "TSM",
             "1306 TPX": "1306.T",
             "1321 NK225": "1321.T",
             "VOO": "VOO"
        }
        STOCK_ZH = {
             "TSMC ADR": "台積電 ADR",
             "1306 TPX": "1306 東証 TOPIX ETF",
             "1321 NK225": "1321 日經 225 ETF",
             "VOO": "VOO 標普500 ETF"
        }
        
        # Mix in dynamic TW stocks (both standard and ETFs)
        tw_stocks = get_tw_stock_options()
        for lbl, sym in tw_stocks.items():
             tw_stock_keys.append(lbl)
             stock_symbols[lbl] = sym
             STOCK_ZH[lbl] = lbl

        def fmt_stk(k):
             return STOCK_ZH.get(k, k) if lang == "中文" else k

        # --- Header & Controls Layout ---
        st.subheader(T(lang, "Equity Curve", "資金曲線"))
        
        with st.container(border=True):
             c_row1_mkt, c_row1_tw, c_row1_int = st.columns([1, 1, 1])
             with c_row1_mkt:
                  sel_indices = st.multiselect(
                      T(lang, "Index Comparison", "大盤指數對照"),
                      options=market_keys,
                      default=["TAIEX"],
                      format_func=fmt_mkt
                  )
             with c_row1_tw:
                  sel_tw_stocks = st.multiselect(
                      T(lang, "TW Stock & ETF", "台灣個股與ETF"),
                      options=tw_stock_keys,
                      default=[],
                      format_func=fmt_stk
                  )
             with c_row1_int:
                  sel_int_stocks = st.multiselect(
                      T(lang, "Intl Stock & ETF", "國際個股與ETF"),
                      options=int_stock_keys,
                      default=[],
                      format_func=fmt_stk
                  )
                  # Placeholder for status UI (filled after data fetch)
                  status_placeholder = st.empty()


             
             # Aggregate all selected stock-like entities for chart processing
             all_sel_stocks = sel_tw_stocks + sel_int_stocks




        # Aggregate to Daily Close for a smooth "Pro" curve
        daily_agg = f_sorted.groupby("date", as_index=False)["realized_pnl"].sum()
        
        # Merge with daily_base early for TWR calculation
        daily_agg = pd.merge_asof(
            daily_agg.sort_values("date"), 
            daily_base.sort_values("date"), 
            on="date"
        )
        daily_agg["dynamic_base"] = daily_agg["dynamic_base"].ffill().bfill().replace(0, 1.0)
        
        # Calculate TWR curve
        daily_agg["r_t"] = daily_agg["realized_pnl"] / daily_agg["dynamic_base"]
        daily_agg["cum_twr"] = (1.0 + daily_agg["r_t"]).cumprod() - 1.0
        daily_agg["twr_pct"] = daily_agg["cum_twr"] * 100.0
        
        daily_agg["cum_pnl"] = daily_agg["realized_pnl"].cumsum()
        scaled_cum, unit_lbl, unit_div = scale_unit(daily_agg["cum_pnl"], lang, CURRENCY_RATE)

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

        # We remove the line trace/fill areas since we are moving to Bar charts for P/L now.
        
        # Hidden Hover Trace -> Visual Data Points (Original Daily Points Only)
        # Fixes the "0" artifact at zero-crossings by ignoring interpolated points
        # Also ensures strict +€1000 formatting in hover
        # Daily Realized P/L is scaled identically
        scaled_daily = (daily_agg["realized_pnl"] * CURRENCY_RATE) / unit_div

        hover_texts_daily = []
        marker_colors_daily = []
        for val in scaled_daily:
             if "€" in unit_lbl:
                  s_val = "+" if val > 0 else "-" if val < 0 else ""
                  txt = f"{s_val}€{abs(val):,.2f}"
             else:
                  txt = f"{val:,.2f} {unit_lbl}"
             hover_texts_daily.append(txt)
             c = PROFIT_COLOR if val >= 0 else LOSS_COLOR
             marker_colors_daily.append(c)

        hover_texts_cum = []
        for val in scaled_cum:
             if "€" in unit_lbl:
                  s_val = "+" if val > 0 else "-" if val < 0 else ""
                  txt = f"{s_val}€{abs(val):,.2f}"
             else:
                  txt = f"{val:,.2f} {unit_lbl}"
             hover_texts_cum.append(txt)

        fig_eq = go.Figure()
        
        # 1. Daily Realized P/L as Bars
        fig_eq.add_trace(
             go.Bar(
                 x=daily_agg["date"],
                 y=scaled_daily,
                 name=T(lang, "Daily P/L", "單日損益"),
                 marker_color=marker_colors_daily,
                 hovertemplate="%{text}<extra></extra>",
                 text=hover_texts_daily,
                 textposition="none",
                 showlegend=True,
             )
        )

        # 2. Cumulative P/L as a Line Overlay
        fig_eq.add_trace(
             go.Scatter(
                 x=daily_agg["date"],
                 y=scaled_cum,
                 mode="lines+markers",
                 name=T(lang, "Cumulative P/L", "累計損益"),
                 line=dict(width=2, color="rgba(255, 255, 255, 0.8)"),
                 marker=dict(size=6, color="rgba(255, 255, 255, 0.9)"),
                 hovertemplate="%{text}<extra></extra>",
                 text=hover_texts_cum,
                 showlegend=True,
             )
        )

        # Add marker/label for the latest cumulative point
        if not daily_agg.empty:
             last_date = daily_agg["date"].iloc[-1]
             last_val = scaled_cum.iloc[-1]
             last_txt = hover_texts_cum[-1]
             
             fig_eq.add_trace(
                 go.Scatter(
                     x=[last_date],
                     y=[last_val],
                     mode="text",
                     text=[last_txt],
                     textposition="top center",
                     textfont=dict(size=11, color="#EAEAEA"),
                     showlegend=False,
                     hoverinfo="skip",
                 )
             )
        
        # Allow labels to overflow
        fig_eq.update_traces(cliponaxis=False)
        
        # Ensure we have fig_base initialized
        fig_base = go.Figure()
        
        # Dedicated trace for Money Invested (dynamic_base)
        if not daily_agg.empty:
             # Already merged earlier
             pass
             
             # Scale invested capital using the same scale function
             scaled_invested, unit_lbl_inv, inv_div = scale_unit(daily_agg["dynamic_base"], lang, CURRENCY_RATE)
             
             # Align the Total Equity (Capital + Cumulative P/L) to the same y-axis scale
             scaled_equity_for_inv = ((daily_agg["dynamic_base"] + daily_agg["cum_pnl"]) * CURRENCY_RATE) / inv_div
             
             # Extend traces to today for step-plot visuals
             plot_dates = daily_agg["date"].tolist()
             plot_inv = scaled_invested.tolist()
             plot_eq = scaled_equity_for_inv.tolist()
             
             today_norm = pd.Timestamp.now().normalize()
             if plot_dates and plot_dates[-1] < today_norm:
                 plot_dates.append(today_norm)
                 plot_inv.append(plot_inv[-1])
                 plot_eq.append(plot_eq[-1])
                 
             fig_base.add_trace(
                 go.Scatter(
                     x=plot_dates,
                     y=plot_inv,
                     mode="lines",
                     name=T(lang, "Money Invested", "投入本金"),
                     line=dict(width=2, color="#4FC3F7", shape="hv"), # Light Blue line
                     fill="tozeroy",
                     fillcolor="rgba(79, 195, 247, 0.15)", # Subtle blue fill
                     hovertemplate=f"%{{y:,.2f}} {unit_lbl_inv}<extra></extra>",
                     showlegend=True,
                 )
             )
             
             hover_texts_equity_inv = []
             for val in plot_eq:
                  txt = f"{val:,.2f} {unit_lbl_inv}"
                  hover_texts_equity_inv.append(txt)

             fig_base.add_trace(
                 go.Scatter(
                     x=plot_dates,
                     y=plot_eq,
                     mode="lines",
                     name=T(lang, "Total Equity", "總權益 (本金+損益)"),
                     line=dict(width=2, color="#F5A623", dash="dash", shape="hv"), # Orange/Gold dashed line
                     fill="tonexty", # Fill the gap between invested and equity
                     fillcolor="rgba(245, 166, 35, 0.1)",
                     hovertemplate="%{text}<extra></extra>",
                     text=hover_texts_equity_inv,
                     showlegend=True,
                 )
             )
        
        # ========== CHART 1: Percentage Return Comparison ==========
        st.subheader(T(lang, "Percentage Return Comparison", "報酬率對照"))
        
        baseline_date = None
        if not daily_agg.empty:
             min_d = daily_agg["date"].min().date()
             max_d = pd.Timestamp.now().normalize().date()
             
             if "baseline_date_picker" not in st.session_state:
                 st.session_state["baseline_date_picker"] = min_d
                 
             def reset_baseline():
                 st.session_state["baseline_date_picker"] = min_d
                 
             col1, col2, col3 = st.columns([2, 1, 4])
             with col1:
                 st.date_input(
                     T(lang, "Baseline Date (0%)", "基準日期 (0%)"),
                     min_value=min_d, max_value=max_d,
                     key="baseline_date_picker"
                 )
             with col2:
                 st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                 st.button(T(lang, "Reset", "重設"), on_click=reset_baseline, use_container_width=True)
                 
             baseline_date = pd.to_datetime(st.session_state["baseline_date_picker"])

        fig_pct = go.Figure()
        
        if not daily_agg.empty:
             # Calculate rebasing offset for TWR
             if baseline_date is not None:
                 past_twr = daily_agg[daily_agg["date"] <= baseline_date]
                 twr_base = past_twr["twr_pct"].iloc[-1] if not past_twr.empty else 0.0
             else:
                 twr_base = 0.0
                 
             # Rebase TWR (Time-Weighted Return)
             rebased_twr = ((1 + daily_agg["twr_pct"] / 100.0) / (1 + twr_base / 100.0) - 1) * 100.0
             
             # Filter daily_agg for plotting
             plot_mask = daily_agg["date"] >= baseline_date if baseline_date is not None else daily_agg["date"] == daily_agg["date"]
             plot_daily_agg = daily_agg[plot_mask]
             pct_vals = rebased_twr[plot_mask]
             
             # Calculate Simple Return for comparison (optional secondary line could be added)
             simple_vals = (daily_agg["cum_pnl"] / daily_agg["dynamic_base"]) * 100.0
             
             # Plot personal Time-Weighted Return (TWR)
             fig_pct.add_trace(
                 go.Scatter(
                     x=plot_daily_agg["date"], 
                     y=pct_vals,
                     mode="lines+markers",
                     marker=dict(size=8, color=PROFIT_COLOR, symbol="circle"),
                     name=T(lang, "Personal Return %", "個人報酬率 %"),
                     line=dict(width=3, color=PROFIT_COLOR),
                     hovertemplate="%{y:.2f}%<extra></extra>",
                 )
             )
             
             # No need for invisible trace on secondary axis since we have a dedicated chart
             
             # Bounds Initialization
             y_max_pct = pct_vals.max() if not pct_vals.empty else 0.0
             y_min_pct = pct_vals.min() if not pct_vals.empty else 0.0
             
             # Fetch and Plot Selected Indices
             color_map = {
                 "TAIEX": "rgba(150,150,150,0.5)",
                 "Dow Jones": "rgba(50, 100, 200, 0.5)",
                 "S&P 500": "rgba(200, 150, 50, 0.5)",
                 "PHLX Semi": "rgba(100, 200, 150, 0.5)",
                 "NASDAQ": "rgba(180, 50, 200, 0.5)",
                 "1306 TPX": "rgba(255, 100, 100, 0.5)",
                 "1321 NK225": "rgba(100, 100, 255, 0.5)"
             }


                  
             # Extend colors for stocks
             stock_colors = {
                 "2330 TSMC": "rgba(0, 255, 255, 0.6)",
                 "0050 Yuanta 50": "rgba(255, 0, 255, 0.6)",
                 "TSMC ADR": "rgba(255, 100, 50, 0.6)",
                 "1306 TPX": "rgba(255, 100, 100, 0.6)",
                 "1321 NK225": "rgba(100, 100, 255, 0.6)",
                 "VOO": "rgba(255, 215, 0, 0.6)"
             }

             max_tw_ts = 0
             max_us_ts = 0



             # Pre-calculate bounds
             max_tw_ts = 0
             max_us_ts = 0
             max_jp_ts = 0

             # Process Market Indices
             for m_name in sel_indices:
                 symbol = market_symbols[m_name]
                 # Fetch up to today to ensure we get latest data even if personal data is old
                 # Add buffer to days calculation
                 days_needed = (pd.Timestamp.now().normalize() - daily_agg["date"].min()).days + 10
                 m_df = get_market_data(symbol, days=max(days_needed, 365))

                 
                 if not m_df.empty:
                     start_date_m = daily_agg["date"].min() if baseline_date is None else baseline_date
                     # Decouple: End date is open (up to today/future)
                     mask = (m_df["date"] >= start_date_m)
                     m_rel = m_df[mask].copy()
                     
                     # Track latest update time
                     ts = m_df.attrs.get("last_update", 0)
                     if "^TWII" in symbol or ".TW" in symbol:
                         if ts > max_tw_ts: max_tw_ts = ts
                     elif ".T" in symbol:
                         if ts > max_jp_ts: max_jp_ts = ts
                     else:
                         if ts > max_us_ts: max_us_ts = ts


                     
                     if not m_rel.empty:
                         base_price = m_rel["close"].iloc[0]
                         if base_price > 0:
                             m_rel["pct"] = (m_rel["close"] - base_price) / base_price * 100.0
                             
                             c_line = color_map.get(m_name)
                             if not c_line:
                                 palette = px.colors.qualitative.Pastel + px.colors.qualitative.Set2
                                 idx = sum(ord(c) for c in m_name) % len(palette)
                                 c_line = hex_to_rgba(palette[idx], 0.7)
                             
                             disp_name = fmt_mkt(m_name)
                             
                             fig_pct.add_trace(
                                 go.Scatter(
                                     x=m_rel["date"],
                                     y=m_rel["pct"],
                                     mode="lines+markers",
                                     marker=dict(size=4),
                                     name=disp_name,
                                     line=dict(color=c_line, width=1.5, dash='dash'),
                                     hovertemplate=f"{disp_name}: %{{y:.2f}}%<extra></extra>"
                                 )
                             )
                             
                             y_max_pct = max(y_max_pct, m_rel["pct"].max())
                             y_min_pct = min(y_min_pct, m_rel["pct"].min())

             # Process Stocks
             for s_name in all_sel_stocks:
                 symbol = stock_symbols[s_name]
                 # Reuse get_market_data logic for duration
                 days_needed = (pd.Timestamp.now().normalize() - daily_agg["date"].min()).days + 10
                 s_df = get_market_data(symbol, days=max(days_needed, 365))

                 
                 if not s_df.empty:
                     start_date_s = daily_agg["date"].min() if baseline_date is None else baseline_date
                     # Decouple: End date is open
                     mask = (s_df["date"] >= start_date_s)
                     s_rel = s_df[mask].copy()
                     
                     # Track latest update time
                     ts = s_df.attrs.get("last_update", 0)
                     if "^TWII" in symbol or ".TW" in symbol:
                         if ts > max_tw_ts: max_tw_ts = ts
                     elif ".T" in symbol:
                         if ts > max_jp_ts: max_jp_ts = ts
                     else:
                         if ts > max_us_ts: max_us_ts = ts


                     
                     if not s_rel.empty:
                         base_price = s_rel["close"].iloc[0]
                         if base_price > 0:
                             s_rel["pct"] = (s_rel["close"] - base_price) / base_price * 100.0
                             
                             c_line = stock_colors.get(s_name)
                             if not c_line:
                                 palette = px.colors.qualitative.Plotly + px.colors.qualitative.Vivid
                                 idx = sum(ord(c) for c in s_name) % len(palette)
                                 c_line = hex_to_rgba(palette[idx], 0.85) # High opacity for stocks
                             
                             disp_name = fmt_stk(s_name)
                             
                             fig_pct.add_trace(
                                 go.Scatter(
                                     x=s_rel["date"],
                                     y=s_rel["pct"],
                                     mode="lines+markers",
                                     marker=dict(size=4),
                                     name=disp_name,
                                     line=dict(color=c_line, width=1.5, dash='dashdot'), 
                                     hovertemplate=f"{disp_name}: %{{y:.2f}}%<extra></extra>"
                                 )
                             )
                             
                             y_max_pct = max(y_max_pct, s_rel["pct"].max())
                             y_min_pct = min(y_min_pct, s_rel["pct"].min())







             
        # Add padding (e.g. 10%)
        rng = y_max_pct - y_min_pct
        if rng == 0: rng = 10.0 # default buffer if flat
        
        # Ensure 0 is visible? Plotly usually does.
        # But for padding:
        pad = rng * 0.1
        final_max_pct = y_max_pct + pad
        final_min_pct = y_min_pct - pad
        
        # Calculate bounds for secondary axis (Invested)
        if not scaled_invested.empty:
            scaled_equity_for_inv = ((daily_agg["dynamic_base"] + daily_agg["cum_pnl"]) * CURRENCY_RATE) / inv_div
            inv_max = max(scaled_invested.max(), scaled_equity_for_inv.max())
            inv_min = min(0, scaled_equity_for_inv.min())
            inv_pad = (inv_max - inv_min) * 0.1 if (inv_max - inv_min) > 0 else 10
            final_max_inv = inv_max + inv_pad
            final_min_inv = inv_min - inv_pad
        else:
            final_max_inv = 100
            final_min_inv = 0

        # Also recalculate final_max_val and final_min_val for the primary chart (scaled_cum + scaled_daily)
        all_vals = list(scaled_cum) + list(scaled_daily) if 'scaled_daily' in locals() else list(scaled_cum)
        final_max_val = max([val for val in all_vals if not pd.isna(val)] + [10.0]) * 1.2
        final_min_val = min([val for val in all_vals if not pd.isna(val)] + [0.0]) * 1.2
        
        # Calculate range boundary
        if not daily_agg.empty:
            min_date = baseline_date if baseline_date is not None else daily_agg["date"].min()
            max_date = daily_agg["date"].max()
            end_x = max(max_date, pd.Timestamp.now().normalize())
            range_x = [min_date, end_x]
        else:
            range_x = None
            final_max_inv = 100
        
        fig_pct.update_layout(
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
                 title=T(lang, "Return %", "報酬率 %"),
                 showgrid=True,
                 gridcolor="rgba(255,255,255,0.08)",
                 tickformat=".1f",
                 ticksuffix="%",
                 range=[final_min_pct, final_max_pct]
             ),
             height=400,
             margin=dict(l=10, r=20, t=40, b=10),
             legend=dict(
                 x=0.01,
                 y=0.99,
                 xanchor="left",
                 yanchor="top",
                 bgcolor="rgba(0,0,0,0.5)"
             ),
             hovermode="x unified",
        )
        add_zero_line(fig_pct, axis="y", color="#A9B1BD", width=2, dash="dash")
        st.plotly_chart(fig_pct, width="stretch")
        hr()
  
        # ========== CHART 2: Absolute P/L ==========
        fig_eq.update_layout(
             title=dict(
                  text=T(lang, "Absolute P/L", "絕對損益"),
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
                 range=[final_min_val, final_max_val]
             ),
             height=350,
             margin=dict(l=10, r=20, t=60, b=10),
             legend=dict(
                 x=0.01,
                 y=0.99,
                 xanchor="left",
                 yanchor="top",
                 bgcolor="rgba(0,0,0,0)"
             ),
             hovermode="x unified",
        )

        add_zero_line(fig_eq, axis="y", color="#A9B1BD", width=2, dash="dash")
        st.plotly_chart(fig_eq, width="stretch")
        
        # Configure and rendering Money Invested Figure
        fig_base.update_layout(
             title=dict(
                  text=T(lang, "Money Invested", "投入本金"),
                  font=dict(size=18)
             ),
             xaxis=dict(
                 title="",
                 dtick=7 * 24 * 60 * 60 * 1000,
                 tickformat="%b %d" if lang != "中文" else "%m/%d",
                 showgrid=True,
                 gridcolor="rgba(255,255,255,0.08)",
                 gridwidth=1,
                 range=range_x, 
             ),
             yaxis=dict(
                 title=f"{T(lang, 'Capital', '本金')} ({unit_lbl_inv if not daily_agg.empty else unit_lbl})",
                 showgrid=True,
                 gridcolor="rgba(255,255,255,0.08)",
                 range=[final_min_inv, final_max_inv] # Capital bounds including cum P/L
             ),
             height=300,
             margin=dict(l=10, r=20, t=60, b=10),
             hovermode="x unified",
        )
        st.plotly_chart(fig_base, width="stretch")
        
        # Inject Status to Top Right Placeholder
        status_parts = []
        if max_tw_ts > 0:
             ago_tw = humanize_ago_from_utc_epoch(max_tw_ts, lang)
             status_parts.append(f"TW: {ago_tw}")
        if max_jp_ts > 0:
             ago_jp = humanize_ago_from_utc_epoch(max_jp_ts, lang)
             status_parts.append(f"JP: {ago_jp}")
        if max_us_ts > 0:
             ago_us = humanize_ago_from_utc_epoch(max_us_ts, lang)
             status_parts.append(f"US: {ago_us}")
        
        with status_placeholder.container():
             # Status text
             if status_parts:
                 full_status = f"**{T(lang, 'Data Status', '資料狀態')}**: " + " | ".join(status_parts)
                 st.caption(full_status)
             else:
                 st.caption(f"**{T(lang, 'Data Status', '資料狀態')}**")

             # Refresh Button
             if st.button(T(lang, "Refresh Data", "更新資料"), key="btn_refresh_market", use_container_width=True):
                  keys_to_del = [k for k in st.session_state.keys() if k.startswith("market_data_")]
                  for k in keys_to_del:
                      del st.session_state[k]
                  st.rerun()
        

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
            # Strict format +€1000
            text=sorted_df["_scaled_pnl"].map(lambda v: (f"{'+' if v>0 else '-' if v<0 else ''}€{abs(v):.2f}" if "€" in unit_lbl2 else f"{v:+.2f} {unit_lbl2}")),
        )
        fig_bar.update_traces(textposition="outside", cliponaxis=False)
        fig_bar.update_layout(
            title=T(lang, "Realized P/L by stock", "各股已實現損益"),
            xaxis_title=f"{T(lang, 'P/L', '損益')} ({unit_lbl2})",
            yaxis_title="",
            height=520,
            # Increase right margin for labels
            margin=dict(l=10, r=60, t=60, b=10),
            legend_title_text="",
        )
        add_zero_line(fig_bar, axis="x", color="#A9B1BD", width=3, dash="dash")
        st.plotly_chart(fig_bar, width="stretch")

    # -------------------- Leaderboard --------------------
    if active_tab == "leader":
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

        winners = lb[lb["total_pnl"] > 0].sort_values("total_pnl", ascending=False)
        losers = lb[lb["total_pnl"] < 0].sort_values("total_pnl", ascending=True)

        def prep(df_):
            out = df_.copy()
            out = out.rename(
                columns={
                    "stock": T(lang, "Stock", "股票"),
                    "trades": T(lang, "Trades", "筆數"),
                    "total_pnl": T(lang, "Total P/L", "總損益"),
                    "win_rate_pct": T(lang, "Win rate %", "勝率%"),
                }
            )
            out[T(lang, "Total P/L", "總損益")] = out[T(lang, "Total P/L", "總損益")].round(0).astype(int)
            out[T(lang, "Win rate %", "勝率%")] = out[T(lang, "Win rate %", "勝率%")].round(1)
            out[T(lang, "Trades", "筆數")] = out[T(lang, "Trades", "筆數")].astype(int)

            return out[
                [
                    T(lang, "Stock", "股票"),
                    T(lang, "Total P/L", "總損益"),
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
                        T(lang, "Trades", "筆數"): "{:.0f}",
                        T(lang, "Win rate %", "勝率%"): "{:.1f}",
                    }
                 ),
                width="stretch",
                height=420,
            )

    # -------------------- Monthly report --------------------
    if active_tab == "monthly":
        st.subheader(T(lang, "Monthly Performance (month-end)", "月度績效（月末快照）"))

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
        m_cum["start_equity"] = peak_base + m_cum["prev_cum_pnl"]
        
        m_cum["month_pct"] = np.where(
            m_cum["start_equity"] != 0, 
            m_cum["month_pnl"] / m_cum["start_equity"] * 100.0, 
            0.0
        )
        
        m_cum["cum_pl_pct"] = np.where(peak_base != 0, m_cum["cum_pnl"] / peak_base * 100.0, 0.0)
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
        if "€" in unit_lbl_m:
             labels = [f"{'+' if v>0 else '-' if v<0 else ''}€{abs(v):.2f}" for v in scaled_vals.to_numpy()]
        else:
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
        fig_m.update_traces(cliponaxis=False)
        fig_m.update_layout(
            height=360,
            margin=dict(l=10, r=10, t=60, b=10), # Increased top margin to 60
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
    if active_tab == "trades":
        st.subheader(T(lang, "Realized Trades", "已實現交易紀錄"))

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
                "total_fee": T(lang, "Total Fee", "總手續費"),
                "total_tax": T(lang, "Total Tax", "總交易稅"),
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
                T(lang, "Total Fee", "總手續費"),
                T(lang, "Total Tax", "總交易稅"),
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
                    T(lang, "Total Fee", "總手續費"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
                    T(lang, "Total Tax", "總交易稅"): lambda x: fmt_money(x, CURRENCY_RATE, CURRENCY_SYMBOL),
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

    # -------------------- Active ETFs --------------------
    if active_tab == "etf":
        st.subheader(T(lang, "Active ETF Holdings", "主動型 ETF 投資組合"))
        
        etf_ticker = st.selectbox(
            T(lang, "Select ETF", "選擇 ETF"),
            ["00981A", "00991A"]
        )
        
        # --- NEW TRACKER UI ---
        log_file = DATA_DIR / f"etf_{etf_ticker}_log.json"
        if log_file.exists():
             try:
                  with open(log_file, "r", encoding="utf-8") as fl:
                       log_data = json.loads(fl.read())
                       
                  def _time_ago(dt_str, lang):
                       if not dt_str:
                            return T(lang, "Unknown", "未知")
                       dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                       import zoneinfo
                       try:
                           belgium_tz = zoneinfo.ZoneInfo("Europe/Brussels")
                       except Exception:
                           # Fallback to hardcoded UTC+1 (Note: misses daylight saving changes)
                           import datetime as dt_mod
                           belgium_tz = dt_mod.timezone(dt_mod.timedelta(hours=1))
                       dt_local_str = dt.astimezone(belgium_tz).strftime('%m-%d %H:%M')
                       now = datetime.now(timezone.utc)
                       diff = (now - dt).total_seconds()
                       mins = int(diff / 60)
                       if mins < 60:
                            rel = f"{mins} mins ago" if lang != "中文" else f"{mins} 分鐘前"
                       elif mins < 1440:
                            rel = f"{mins//60} hrs ago" if lang != "中文" else f"{mins//60} 小時前"
                       else:
                            rel = f"{mins//1440} days ago" if lang != "中文" else f"{mins//1440} 天前"
                       return f"{rel} ({dt_local_str})"
                       
                  lcu = log_data.get("last_checked_utc")
                  luu = log_data.get("last_updated_utc")
                  status_msg = log_data.get("status", "Unknown")
                  
                  checked_str = _time_ago(lcu, lang)
                  update_str = _time_ago(luu, lang) if luu else T(lang, "Never (or before tracking)", "從未 (或追蹤前)")
                  
                  st.info(
                      f"**{T(lang, 'Backend Tracker', '雲端更新狀態')}**: {status_msg}  \n"
                      f"**{T(lang, 'Last checked', '最後檢查時間')}**: {checked_str}  \n"
                      f"**{T(lang, 'Last updated', '最後資料變動')}**: {update_str}",
                      icon="🤖"
                  )
             except Exception as e:
                  pass
        # ----------------------

        etf_file = DATA_DIR / f"etf_{etf_ticker}_history.json"

        history_data = {}
        if etf_file.exists():
             try:
                  import json
                  with open(etf_file, "r", encoding="utf-8") as fl:
                       history_data = json.loads(fl.read())
             except Exception:
                  pass
                  
        dates = sorted(list(history_data.keys()), reverse=True)
        if not dates:
             st.warning(T(lang, "No ETF history data available currently.", "目前無 ETF 歷史資料。"))
             # return # Removed return to allow the subtabs to show empty state
             
        col_d1, col_d2 = st.columns([1, 3])
        with col_d1:
             selected_date = st.selectbox(T(lang, "Select Data Date", "選擇資料日期"), dates) if dates else None
             
        # Add Sub-tabs for Holdings and Daily Report
        etf_tabs_dict = {
            "etf_overview": T(lang, "Holdings Overview", "總覽 / 持股"),
            "etf_daily": T(lang, "Operation Daily Report", "操作日報")
        }
        if hasattr(st, "segmented_control"):
            active_etf_tab = st.segmented_control(
                "ETF Navigation", 
                options=list(etf_tabs_dict.keys()), 
                format_func=lambda x: etf_tabs_dict[x],
                default="etf_overview",
                key="etf_tab",
                label_visibility="collapsed"
            )
        else:
            active_etf_tab = st.radio(
                "ETF Navigation", 
                options=list(etf_tabs_dict.keys()), 
                format_func=lambda x: etf_tabs_dict[x],
                horizontal=True,
                key="etf_tab",
                label_visibility="collapsed"
            )
        if not active_etf_tab: active_etf_tab = "etf_overview"

        if active_etf_tab == "etf_overview":
             if selected_date and selected_date in history_data:
                 curr_day_data = history_data[selected_date]
                 holdings = curr_day_data.get("holdings", [])
                 
                 st.markdown(f"**{T(lang, 'Total Stocks', '總檔數')}**: {len(holdings)}")
                 
                 if holdings:
                      df_h = pd.DataFrame(holdings)
                      # Sort by weight
                      df_h = df_h.sort_values(by="weight_pct", ascending=False).reset_index(drop=True)
                      df_h["stock_label"] = df_h["id"].astype(str) + " " + df_h["name"]
                      
                      top_n = st.slider(T(lang, "Show Top N Holdings", "顯示前 N 大持股"), min_value=5, max_value=len(df_h), value=min(20, len(df_h)), step=5)
                      
                      df_top = df_h.head(top_n)
                      
                      fig = px.bar(
                           df_top,
                           x="stock_label",
                           y="weight_pct",
                           text="weight_pct",
                           color_discrete_sequence=[NEUTRAL_PURPLE],
                           title=f"{T(lang, f'Top {top_n} Holdings', f'前 {top_n} 大持股')} ({selected_date})",
                           labels={"stock_label": T(lang, "Stock", "股票"), "weight_pct": T(lang, "Weight (%)", "權重 (%)")}
                      )
                      fig.update_traces(texttemplate='%{text:.2f}%', textposition='outside')
                      fig.update_layout(xaxis_title="", yaxis_title=T(lang, "Weight (%)", "權重 (%)"), height=500, margin=dict(b=100))
                      st.plotly_chart(fig, use_container_width=True)
                      
                      # Rename columns for display
                      df_show = df_h[["id", "name", "weight_pct", "shares"]].copy()
                      df_show["shares"] = df_show["shares"] / 1000
                      df_show.columns = [
                           T(lang, "Stock ID", "代號"),
                           T(lang, "Stock Name", "名稱"),
                           T(lang, "Weight (%)", "權重 (%)"),
                           T(lang, "Holdings (Lots)", "張數")
                      ]
                      st.dataframe(df_show, width="stretch")
                 else:
                      st.info(T(lang, "No holdings data for this date.", "此日期無持股資料。"))
             else:
                 st.info(T(lang, "Please select a date with available ETF data.", "請選擇有 ETF 資料的日期。"))

        if active_etf_tab == "etf_daily":
            if selected_date and selected_date in history_data:
                st.markdown(f"### {selected_date} {T(lang, 'Operation Daily Report', '操作日報')}")
                curr_idx = dates.index(selected_date)
                if curr_idx == len(dates) - 1:
                    st.warning(T(lang, "No previous day data available to compare.", "無前一日資料可供比較。"))
                else:
                    prev_date = dates[curr_idx + 1]
                    st.caption(f"{T(lang, 'Compared to', '較前日')} {prev_date}")
                    
                    curr_data = history_data[selected_date]
                    prev_data = history_data[prev_date]
                    

                    curr_meta = curr_data.get("meta", {})
                    
                    fund_size = curr_meta.get("fund_size", 0)
                    nav = curr_meta.get("nav", 0)
                    
                    # Use EXACT daily closing price for accurate comparison
                    market_price = curr_meta.get("closing_price")
                    
                    if not market_price:
                        # Fallback for live operations
                        market_price = nav
                        try:
                            import requests
                            res = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{etf_ticker}.TW", headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
                            if res.status_code == 200:
                                market_price = res.json()['chart']['result'][0]['meta']['regularMarketPrice']
                        except Exception:
                            pass
                    
                    premium_pct = 0.0
                    if nav and nav > 0:
                        premium_pct = ((market_price - nav) / nav) * 100
                    
                    # Top Metadata Cards
                    m1, m2 = st.columns(2)
                    with m1:
                        if fund_size:
                            c_val = fund_size * CURRENCY_RATE
                            
                            prev_meta = prev_data.get("meta", {}) if prev_data else {}
                            prev_fund_size = prev_meta.get("fund_size", 0)
                            delta_str = None
                            if prev_fund_size and prev_fund_size > 0:
                                diff_pct = ((fund_size - prev_fund_size) / prev_fund_size) * 100.0
                                delta_str = f"{diff_pct:+.2f}%"
                                
                            if CURRENCY_RATE != 1.0:
                                f_size_disp = f"€ {c_val / 1_000_000:,.1f}M"
                                st.metric(T(lang, "Fund Size (EUR)", "基金規模 (歐元)"), f_size_disp, delta=delta_str)
                            else:
                                if lang == "中文":
                                    f_size_disp = f"{int(c_val / 100000000)} 億"
                                    st.metric("基金規模 (TWD)", f_size_disp, delta=delta_str)
                                else:
                                    f_size_disp = f"{c_val / 1_000_000:,.1f}M"
                                    st.metric("Fund Size (TWD)", f_size_disp, delta=delta_str)
                        else:
                            st.metric(T(lang, "Fund Size", "基金規模"), "N/A")
                    with m2:
                        st.metric(T(lang, "Premium/Discount", "折溢價"), f"{premium_pct:+.2f}%", 
                                  help=f"{T(lang, 'Market Price:', '股價:')} {market_price:.2f} | {T(lang, 'NAV:', '淨值:')} {nav:.2f}" if nav else "")
                                  
                    # Calculate Differences
                    prev_map = { h['id']: h for h in prev_data.get('holdings', []) }
                    curr_map = { h['id']: h for h in curr_data.get('holdings', []) }
                    
                    new_s, del_s, inc_s, dec_s = [], [], [], []
                    for sid, ch in curr_map.items():
                        if sid not in prev_map:
                            new_s.append(ch)
                        else:
                            ph = prev_map[sid]
                            diff_sh = ch['shares'] - ph['shares']
                            if diff_sh > 0: inc_s.append((ch, ph))
                            elif diff_sh < 0: dec_s.append((ch, ph))
                            
                    for sid, ph in prev_map.items():
                        if sid not in curr_map:
                            del_s.append(ph)
                            
                    # 4 Status Boxes - 2x2 Layout
                    st.write("")
                    row1_col1, row1_col2 = st.columns(2)
                    row2_col1, row2_col2 = st.columns(2)
                    
                    def box_ui(title, count, color):
                        return f'''
                        <div style="background-color: {color}15; border-left: 6px solid {color}; padding: 16px 20px; border-radius: 8px; margin-bottom: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                            <div style="color: {color}; font-weight: 700; font-size: 16px; margin-bottom: 8px; letter-spacing: 0.5px;">{title}</div>
                            <div style="color: white; font-size: 32px; font-weight: 800; line-height: 1;">{count}</div>
                        </div>
                        '''
                        
                    with row1_col1: st.markdown(box_ui(T(lang, "New", "新增"), f"{len(new_s)} {T(lang,'Count','檔')}", NEW_COLOR), unsafe_allow_html=True)
                    with row1_col2: st.markdown(box_ui(T(lang, "Removed", "刪除"), f"{len(del_s)} {T(lang,'Count','檔')}", REMOVED_COLOR), unsafe_allow_html=True)
                    with row2_col1: st.markdown(box_ui(T(lang, "Increased", "加碼"), f"{len(inc_s)} {T(lang,'Count','檔')}", PROFIT_COLOR), unsafe_allow_html=True)
                    with row2_col2: st.markdown(box_ui(T(lang, "Decreased", "減碼"), f"{len(dec_s)} {T(lang,'Count','檔')}", LOSS_COLOR), unsafe_allow_html=True)
                    
                    st.write(f"{T(lang, 'Total ', '共')} {len(new_s)+len(del_s)+len(inc_s)+len(dec_s)} {T(lang, 'changes detected.', '檔異動')}")
                    
                    # Build unified operations DataFrame
                    rows = []
                    for h in new_s:
                        rows.append({"ID": h['id'], "Name": h['name'], "Status": T(lang, "New", "新增"), "ShareDiff": h['shares'], "MagPct": 100.0, "CurrWeight": h['weight_pct'], "WeightDiff": h['weight_pct'], "ActiveWeight": h['weight_pct']})
                    for h in del_s:
                        rows.append({"ID": h['id'], "Name": h['name'], "Status": T(lang, "Removed", "刪除"), "ShareDiff": -h['shares'], "MagPct": -100.0, "CurrWeight": 0.0, "WeightDiff": -h['weight_pct'], "ActiveWeight": -h['weight_pct']})
                    for ch, ph in inc_s:
                        diff_sh = ch['shares'] - ph['shares']
                        mag = (diff_sh / ph['shares'] * 100.0) if ph['shares'] else 0.0
                        active_w = diff_sh * (ch['weight_pct'] / ch['shares']) if ch['shares'] else 0.0
                        rows.append({"ID": ch['id'], "Name": ch['name'], "Status": T(lang, "Increased", "加碼"), "ShareDiff": diff_sh, "MagPct": mag, "CurrWeight": ch['weight_pct'], "WeightDiff": ch['weight_pct'] - ph['weight_pct'], "ActiveWeight": active_w})
                    for ch, ph in dec_s:
                        diff_sh = ch['shares'] - ph['shares']
                        mag = (diff_sh / ph['shares'] * 100.0) if ph['shares'] else 0.0
                        active_w = diff_sh * (ph['weight_pct'] / ph['shares']) if ph['shares'] else 0.0
                        rows.append({"ID": ch['id'], "Name": ch['name'], "Status": T(lang, "Decreased", "減碼"), "ShareDiff": diff_sh, "MagPct": mag, "CurrWeight": ch['weight_pct'], "WeightDiff": ch['weight_pct'] - ph['weight_pct'], "ActiveWeight": active_w})
                        
                    if rows:
                        df_ops = pd.DataFrame(rows)
                        # Sort by MagPct abs value or Status
                        df_ops = df_ops.sort_values(by="MagPct", ascending=False).reset_index(drop=True)
                        
                        df_ops["Target"] = df_ops["Name"] + " (" + df_ops["ID"].astype(str) + ")"
                        df_ops["ShareDiffStr"] = (df_ops["ShareDiff"] / 1000).apply(lambda x: f"+{x:,.0f}" if x>0 else f"{x:,.0f}")
                        df_ops["CurrWeightStr"] = df_ops["CurrWeight"].apply(lambda x: f"{x:.2f}%")
                        df_ops["WeightDiffStr"] = df_ops["WeightDiff"].apply(lambda x: f" {x:+.2f}%")
                        df_ops["ActiveWeightStr"] = df_ops["ActiveWeight"].apply(lambda x: f" {x:+.2f}%")
                        
                        f_sz = (fund_size * CURRENCY_RATE) if fund_size else 0.0
                        df_ops["ActiveMoney"] = (df_ops["ActiveWeight"] / 100.0) * f_sz
                        
                        def fmt_mny_only(m):
                            if abs(m) < 1.0: return ""
                            sign = "+" if m > 0 else "-"
                            am = abs(m)
                            if lang == "中文":
                                if am >= 100000000: return f"{sign}{am/100000000:.2f} 億"
                                elif am >= 10000: return f"{sign}{am/10000:.0f} 萬"
                                else: return f"{sign}{am:,.0f}"
                            else:
                                if am >= 1000000000: return f"{sign}{am/1000000000:.2f}B"
                                elif am >= 1000000: return f"{sign}{am/1000000:.1f}M"
                                elif am >= 1000: return f"{sign}{am/1000:.0f}K"
                                else: return f"{sign}{am:,.0f}"
                                
                        df_ops["ActiveMoneyStr"] = df_ops["ActiveMoney"].apply(lambda x: f"({fmt_mny_only(x)})" if fmt_mny_only(x) else "")
                        
                        import plotly.graph_objects as go
                        import plotly.express as px
                        
                        chart_df = df_ops.sort_values(by="ActiveMoney", ascending=True).copy()
                        
                        max_abs_val = chart_df["ActiveMoney"].abs().max() if not chart_df.empty else 0
                        currency_str = "歐元" if CURRENCY_RATE != 1.0 else "TWD"
                        currency_eng_str = "EUR" if CURRENCY_RATE != 1.0 else "TWD"
                        
                        if lang == "中文":
                            if max_abs_val >= 100000000:
                                scale_div = 100000000.0
                                axis_unit = f"億 {currency_str}"
                            elif max_abs_val >= 10000:
                                scale_div = 10000.0
                                axis_unit = f"萬 {currency_str}"
                            else:
                                scale_div = 1.0
                                axis_unit = currency_str
                            axis_title = f"資金分配變動額 (估值, {axis_unit})"
                        else:
                            if max_abs_val >= 1000000000:
                                scale_div = 1000000000.0
                                axis_unit = f"B {currency_eng_str}"
                            elif max_abs_val >= 1000000:
                                scale_div = 1000000.0
                                axis_unit = f"M {currency_eng_str}"
                            elif max_abs_val >= 1000:
                                scale_div = 1000.0
                                axis_unit = f"K {currency_eng_str}"
                            else:
                                scale_div = 1.0
                                axis_unit = currency_eng_str
                            axis_title = f"Capital Allocation Amount (Est, {axis_unit})"
                            
                        chart_df["PlotValue"] = chart_df["ActiveMoney"] / scale_div
                        
                        chart_df["PrevWeight"] = chart_df["CurrWeight"] - chart_df["WeightDiff"]
                        
                        def format_label(row):
                            money_val = fmt_mny_only(row["ActiveMoney"])
                            if not money_val: money_val = "0"
                            share_str = f" <span style='font-size:12px; color:#cccccc'>({row['ShareDiffStr']} {T(lang, 'Lots', '張')})</span>"
                            prev = f"{row['PrevWeight']:.2f}%"
                            curr = row["CurrWeightStr"]
                            return f"<b>{money_val}</b>{share_str} <span style='font-size:12px; color:#aaaaaa'>({prev} ➜ {curr})</span>"
                            
                        def get_bar_color(status):
                            if status == T(lang, "New", "新增"): return NEW_COLOR
                            if status == T(lang, "Removed", "刪除"): return REMOVED_COLOR
                            if status == T(lang, "Increased", "加碼"): return PROFIT_COLOR
                            if status == T(lang, "Decreased", "減碼"): return LOSS_COLOR
                            return "gray"
                            
                        colors = chart_df["Status"].apply(get_bar_color)
                        texts = chart_df.apply(format_label, axis=1)
                        
                        hover_texts = chart_df.apply(
                            lambda row: f"<b>{row['Name']}</b><br>Before: {row['PrevWeight']:.2f}%<br>Allocated: {fmt_mny_only(row['ActiveMoney'])} ({row['ActiveWeightStr'].strip()})<br>Result: {row['CurrWeightStr']}", 
                            axis=1
                        )
                        
                        fig = go.Figure(go.Bar(
                            x=chart_df["PlotValue"],
                            y=chart_df["Name"],
                            orientation='h',
                            marker_color=colors,
                            cliponaxis=False,
                            text=texts,
                            textposition="outside",
                            textfont=dict(color="white"),
                            hovertemplate="%{customdata}<extra></extra>",
                            customdata=hover_texts
                        ))
                        
                        fig.update_layout(
                            margin=dict(l=0, r=100, t=30, b=0),
                            height=max(300, min(650, len(chart_df) * 30)),
                            xaxis_title=axis_title,
                            yaxis_title="",
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(color="white"),
                            showlegend=False
                        )
                        
                        x_min = min(0.0, chart_df["PlotValue"].min())
                        x_max = max(0.0, chart_df["PlotValue"].max())
                        x_range = x_max - x_min if (x_max - x_min) > 0 else 1.0
                        
                        x_pad_l = x_range * 0.40
                        x_pad_r = x_range * 0.40
                        fig.update_xaxes(
                            range=[x_min - x_pad_l, x_max + x_pad_r],
                            showgrid=True, 
                            gridcolor='rgba(255,255,255,0.1)', 
                            zeroline=True, 
                            zerolinecolor='rgba(255,255,255,0.3)'
                        )
                        
                        st.markdown(f"#### {T(lang, 'Portfolio Capital Adjustments', '投資組合資金分配變動圖')}")
                        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                        
                        df_ops_show = df_ops[["Target", "Status", "ShareDiffStr", "CurrWeightStr", "ActiveWeightStr"]].copy()
                        df_ops_show.columns = [
                            T(lang, "Target", "標的"),
                            T(lang, "Status", "狀態"),
                            T(lang, "Share Chg (Lots)", "持股變動 (張)"),
                            T(lang, "Weight (%)", "目前權重"),
                            T(lang, "Alloc Chg (%)", "資金分配變動%")
                        ]
                        
                        def style_status(val):
                             if val == T(lang, "New", "新增"):
                                  return f"color: {NEW_COLOR}; font-weight: bold;"
                             elif val == T(lang, "Removed", "刪除"):
                                  return f"color: {REMOVED_COLOR}; font-weight: bold;"
                             elif val == T(lang, "Increased", "加碼"):
                                  return f"color: {PROFIT_COLOR}; font-weight: bold;"
                             elif val == T(lang, "Decreased", "減碼"):
                                  return f"color: {LOSS_COLOR}; font-weight: bold;"
                             return ""
                             
                        styler = df_ops_show.style.applymap(style_status, subset=[T(lang, "Status", "狀態")])
                        st.dataframe(styler, width="stretch", height=600)
                    else:
                        st.info(T(lang, "No portfolio changes detected from previous day.", "相較前日無任何持股變動。"))

except Exception:
    st.error("App crashed during rendering. Here is the full traceback:")
    st.code(traceback.format_exc())
