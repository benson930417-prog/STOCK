# app.py
# Realized P/L Dashboard (Cathay / 國泰 CSV) + GitHub-backed master raw trades
#
# FIXES INCLUDED (2026-01-23):
# ✅ trade_id 改成用「整列原始欄位」做 hash，避免 merge 少筆（例如少 4000 股）
# ✅ GitHub meta 檔防呆：避免上傳後一直 push / redeploy 造成重複 push
# ✅ 若 GitHub master 被刪 (404)，本地也會刪掉，避免還顯示舊錯誤
# ✅ push 後清 cache + rerun
# ✅ view 密碼放主畫面（手機不必展開 sidebar），admin 密碼仍在 sidebar
#
# Streamlit Secrets (Streamlit Cloud):
#   VIEW_PASSWORD
#   ADMIN_PASSWORD
#   GITHUB_TOKEN
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

import io
import json
import base64
import hashlib
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

MASTER_PATH_LOCAL = Path("data") / "master_trades.csv"
META_PATH_REPO = "data/_upload_meta.json"  # ✅ persistent guard to prevent infinite push


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
        if st.button("Logout admin", use_container_width=True):
            st.session_state.authed_admin = False
            st.rerun()
        return

    typed = st.text_input("Admin password", type="password", key="admin_pw_input")
    if st.button("Login admin", use_container_width=True):
        if typed == ADMIN_PASSWORD:
            st.session_state.authed_admin = True
            st.rerun()
        else:
            st.error("Wrong admin password")


require_view_password_centered()


# -------------------- GitHub helpers --------------------
def github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_get_content(repo: str, path: str, ref: str):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=github_api_headers(), params={"ref": ref}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def github_download_file_bytes(repo: str, path: str, ref: str) -> tuple[bytes, str] | tuple[None, None]:
    j = github_get_content(repo, path, ref)
    if j is None:
        return None, None
    content_b64 = j["content"]
    sha = j["sha"]
    return base64.b64decode(content_b64), sha


def github_put_file(repo: str, path: str, ref: str, content_bytes: bytes, message: str):
    if not GITHUB_TOKEN or not repo:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_REPO in Streamlit Secrets.")

    existing = github_get_content(repo, path, ref)
    sha = existing.get("sha") if existing else None

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": ref,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=github_api_headers(), data=json.dumps(payload), timeout=60)
    r.raise_for_status()
    return r.json()


def ensure_master_synced_from_github(force: bool = False):
    """
    ✅ SHA-guard sync master from GitHub
    ✅ If GitHub file is deleted (404), delete local master too (IMPORTANT)
    """
    if not (GITHUB_TOKEN and GITHUB_REPO and GITHUB_FILE_PATH):
        return

    if "master_sha_loaded" not in st.session_state:
        st.session_state.master_sha_loaded = ""

    b, sha = github_download_file_bytes(GITHUB_REPO, GITHUB_FILE_PATH, GITHUB_BRANCH)

    # GitHub master missing -> delete local master to prevent stale state
    if b is None:
        if MASTER_PATH_LOCAL.exists():
            MASTER_PATH_LOCAL.unlink(missing_ok=True)
        st.session_state.master_sha_loaded = ""
        return

    if force or (sha != st.session_state.master_sha_loaded) or (not MASTER_PATH_LOCAL.exists()):
        MASTER_PATH_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        MASTER_PATH_LOCAL.write_bytes(b)
        st.session_state.master_sha_loaded = sha


def load_repo_meta() -> dict:
    """
    ✅ Persistent guard to stop infinite push:
       reads data/_upload_meta.json from GitHub
    """
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return {}
    b, _ = github_download_file_bytes(GITHUB_REPO, META_PATH_REPO, GITHUB_BRANCH)
    if b is None:
        return {}
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {}


def save_repo_meta(meta: dict):
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return
    content = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    github_put_file(
        repo=GITHUB_REPO,
        path=META_PATH_REPO,
        ref=GITHUB_BRANCH,
        content_bytes=content,
        message=f"Update upload meta ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
    )


# -------------------- raw trade schema --------------------
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
MASTER_COLS = RAW_REQUIRED + ["trade_id"]


def read_cathay_csv_any(file_like) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_like, header=1, encoding="utf-8-sig")
        df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
        if all(c in df.columns for c in RAW_REQUIRED):
            return df
    except Exception:
        pass
    df = pd.read_csv(file_like, header=0, encoding="utf-8-sig")
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
    return df


def add_trade_id(df: pd.DataFrame) -> pd.DataFrame:
    """
    ✅ NEW: trade_id = SHA1(整列原始欄位 RAW_REQUIRED 的 canonical string)
    這樣不會把 4000 股那筆誤當成同一筆
    """
    df = df.copy()

    # normalize each column deterministically
    def norm_col(series: pd.Series) -> pd.Series:
        s = series.copy()
        # try datetime
        if series.name == "日期":
            s = pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")
            s = s.fillna("")
            return s.astype(str)

        def norm_val(v):
            if pd.isna(v):
                return ""
            if isinstance(v, (float, np.floating)):
                return f"{float(v):.6f}"
            return str(v).strip()

        return s.map(norm_val)

    pieces = []
    for c in RAW_REQUIRED:
        if c not in df.columns:
            pieces.append(pd.Series([""] * len(df)))
        else:
            pieces.append(norm_col(df[c]))

    joined = pieces[0].str.cat(pieces[1:], sep="|")
    df["trade_id"] = joined.map(lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest())
    return df


def normalize_raw_trades(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
    missing = [c for c in RAW_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}\nFound: {list(df.columns)}")

    df = df[RAW_REQUIRED].copy()

    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df["成交股數"] = df["成交股數"].apply(to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(to_float)

    df["委託書號"] = df["委託書號"].astype(str).str.strip()
    df["股名"] = df["股名"].astype(str).str.strip()
    df["買賣別"] = df["買賣別"].astype(str).str.strip()

    df = df.dropna(subset=["日期"])
    df = df[df["股名"].astype(str).str.len() > 0]

    df = add_trade_id(df)
    return df[MASTER_COLS].copy()


def save_master_local(df_master: pd.DataFrame):
    MASTER_PATH_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    df_master[MASTER_COLS].to_csv(MASTER_PATH_LOCAL, index=False, encoding="utf-8-sig")


def load_master_local() -> pd.DataFrame:
    if not MASTER_PATH_LOCAL.exists():
        return pd.DataFrame(columns=MASTER_COLS)
    df = pd.read_csv(MASTER_PATH_LOCAL, encoding="utf-8-sig")
    df.columns = [str(c).strip().replace("\n", "") for c in df.columns]
    # ensure trade_id exists & deterministic
    df = normalize_raw_trades(df)
    return df


def merge_into_master(new_df: pd.DataFrame):
    master = load_master_local()
    n_old = len(master)
    n_new = len(new_df)

    combined = pd.concat([master, new_df], ignore_index=True)

    # ✅ de-dup by trade_id (fingerprint of entire row)
    combined = combined.drop_duplicates(subset=["trade_id"], keep="last")
    combined = combined.sort_values(["日期", "股名", "trade_id"]).reset_index(drop=True)

    n_after = len(combined)
    dup_skipped = (n_old + n_new) - n_after

    save_master_local(combined)

    return {
        "old_rows": n_old,
        "uploaded_rows": n_new,
        "after_rows": n_after,
        "dup_skipped": dup_skipped,
        "min_date": combined["日期"].min() if n_after else None,
        "max_date": combined["日期"].max() if n_after else None,
    }


# -------------------- FIFO logic --------------------
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


# -------------------- cache heavy compute --------------------
@st.cache_data(show_spinner=False)
def compute_realized_from_master_bytes(master_csv_bytes: bytes):
    df_master = pd.read_csv(io.BytesIO(master_csv_bytes), encoding="utf-8-sig")
    df_master.columns = [str(c).strip().replace("\n", "") for c in df_master.columns]
    df_master = normalize_raw_trades(df_master)
    raw_df, realized = realized_match_first_then_fifo_separate_pools_from_raw_trades(df_master)
    return raw_df, realized


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


# -------------------- app --------------------
try:
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

        hr()
        st.subheader("Admin / 管理者")
        admin_login_ui()

        if st.button("🔄 Force reload master from GitHub", use_container_width=True):
            st.cache_data.clear()
            ensure_master_synced_from_github(force=True)
            st.rerun()

        # Admin upload (form + repo meta guard)
        if is_admin_authed():
            hr()
            st.subheader("Upload / 上傳")

            with st.form("admin_upload_form", clear_on_submit=True):
                up_admin = st.file_uploader(
                    "Upload Cathay CSV (any filename)",
                    type=["csv"],
                    key="admin_upload_uploader",
                )
                submitted = st.form_submit_button("✅ Merge into master & push", use_container_width=True)

            if submitted:
                if up_admin is None:
                    st.error("Please select a CSV first.")
                    st.stop()

                file_bytes = up_admin.getvalue()
                upload_hash = hashlib.sha256(file_bytes).hexdigest()

                # ✅ persistent guard (repo meta) prevents infinite pushes across redeploys
                meta = load_repo_meta()
                last_hash = meta.get("last_upload_hash", "")
                if last_hash == upload_hash:
                    st.warning("This upload file content was already processed & pushed. (meta guard)")
                    st.stop()

                with st.spinner("Sync master from GitHub..."):
                    ensure_master_synced_from_github(force=True)

                with st.spinner("Merging into master..."):
                    monthly_df = read_cathay_csv_any(io.BytesIO(file_bytes))
                    monthly_df = normalize_raw_trades(monthly_df)

                    stats = merge_into_master(monthly_df)

                    # (Optional quick sanity check for your case)
                    # You can comment this out later.
                    if stats["dup_skipped"] > 0:
                        st.info(f"Dedup skipped {stats['dup_skipped']} rows (by trade_id).")

                with st.spinner("Pushing to GitHub..."):
                    master_bytes = MASTER_PATH_LOCAL.read_bytes()
                    msg = f"Update master_trades.csv ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
                    github_put_file(
                        repo=GITHUB_REPO,
                        path=GITHUB_FILE_PATH,
                        ref=GITHUB_BRANCH,
                        content_bytes=master_bytes,
                        message=msg,
                    )

                    # ✅ update meta AFTER master push
                    meta = {
                        "last_upload_hash": upload_hash,
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "stats": stats,
                    }
                    save_repo_meta(meta)

                st.success("✅ Master updated + pushed to GitHub.")
                st.caption(
                    f"old={stats['old_rows']} | uploaded={stats['uploaded_rows']} | "
                    f"dup_skipped={stats['dup_skipped']} | after={stats['after_rows']}"
                )

                st.cache_data.clear()
                st.rerun()

    # Colors
    if tw_colors:
        PROFIT_COLOR = "#E74C3C"  # red
        LOSS_COLOR = "#2ECC71"    # green
    else:
        PROFIT_COLOR = "#2ECC71"
        LOSS_COLOR = "#E74C3C"

    NEUTRAL_BLUE = "#4C78A8"
    NEUTRAL_PURPLE = "#6F42C1"

    # Always sync master from GitHub (for everyone)
    ensure_master_synced_from_github(force=False)

    if not MASTER_PATH_LOCAL.exists():
        st.warning(
            T(
                lang,
                "No master file found. Admin please upload in sidebar.",
                "找不到 master 檔案。請管理者在左側上傳 CSV 以建立/更新 master。",
            )
        )
        st.stop()

    master_bytes = MASTER_PATH_LOCAL.read_bytes()

    with st.spinner(T(lang, "Loading & computing...", "載入並計算中...")):
        raw_df, realized = compute_realized_from_master_bytes(master_bytes)

    if realized.empty:
        st.warning(T(lang, "No realized sells found.", "找不到已實現賣出紀錄。"))
        st.dataframe(raw_df.head(80), width="stretch")
        st.stop()

    # Display type vocab (2 types only)
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
            )
        )

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
                    dtick=24 * 60 * 60 * 1000,
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

        scaled_vals2, unit_lbl2, _ = scale_unit(by_stock["realized_pnl"], lang)
        by_stock["_scaled_pnl"] = scaled_vals2

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

        scaled_vals_m, unit_lbl_m, _ = scale_unit(m_cum["cum_pnl"], lang)
        labels = [f"{v:.2f} {unit_lbl_m}" for v in scaled_vals_m.to_numpy()]

        fig_m = go.Figure()
        fig_m.add_trace(
            go.Scatter(
                x=m_cum["month"],
                y=scaled_vals_m,
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

    # -------------------- Trades --------------------
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

except ValueError as e:
    st.error(str(e))
    st.info("如果你剛改了 GitHub master：按左側「Force reload master from GitHub」再試一次。")
    st.stop()

except Exception:
    st.error("App crashed during rendering. Here is the full traceback:")
    st.code(traceback.format_exc())
