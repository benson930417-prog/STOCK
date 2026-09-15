"""Temporary public ETF entry during backend retirement.

This keeps the existing public ETF panels accessible until the separate,
complete frontend replacement. Private accounting is archived offline.
"""
from pathlib import Path
import streamlit as st
from src.ui.etf_tab import render_etf_tab, render_passive_etf_tab
from src.view_access import require_view_access

st.set_page_config(page_title="ETF 公開資料", layout="wide")
require_view_access()

def T(lang, en, zh):
    return zh if lang == "中文" else en

st.title("ETF 公開資料")
active, passive = st.tabs(["主動型 ETF", "被動型 ETF"])
shared = dict(lang="中文", T=T, DATA_DIR=Path(__file__).resolve().parent / "data",
              NEUTRAL_PURPLE="#9B59B6", delta_color_param="inverse",
              PROFIT_COLOR="#E74C3C", LOSS_COLOR="#2ECC71")
with active:
    render_etf_tab(**shared, CURRENCY_RATE=1.0, NEW_COLOR="#3498DB", REMOVED_COLOR="#95A5A6")
with passive:
    render_passive_etf_tab(**shared)
