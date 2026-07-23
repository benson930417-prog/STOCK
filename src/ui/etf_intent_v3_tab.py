"""Two-lane active-ETF intent transition view."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.etf_intent_v3_cards import INTENT_CSS, render_intent_grid


def _load(data_dir):
    path = data_dir / "etf_intent_v3.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_etf_intent_v3_tab(*, DATA_DIR=None, **kwargs):
    st.markdown(
        "<style>"
        + INTENT_CSS
        + """
        .tfv3-hero {border:1px solid rgba(148,163,184,.23); border-radius:.9rem;
          padding:.9rem 1rem; margin:.55rem 0 .85rem;
          background:linear-gradient(135deg,rgba(30,41,59,.38),rgba(15,23,42,.22))}
        .tfv3-hero b {font-size:1rem}
        .tfv3-hero p {font-size:.77rem; opacity:.75; line-height:1.55;
          margin:.28rem 0 0}
        .tfv3-quality {font-size:.70rem; opacity:.65; margin:.2rem 0 .55rem}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 共識轉折")
    st.caption(
        "只有至少 2/3 ETF 在同一交易日、同一方向且各自通過顯著門檻才上榜；"
        "單一 ETF 動作只留後端，不作跟單結論。"
    )
    payload = _load(DATA_DIR)
    if not payload:
        st.warning("尚無 V3 意圖資料，請先執行 build_etf_intent_v3.py。")
        return

    buying = list((payload.get("signals") or {}).get("buying") or [])
    selling = list((payload.get("signals") or {}).get("selling") or [])
    buy_new = sum(
        str(event.get("signal_phase") or "new") == "new"
        for event in buying
    )
    buy_confirmed = sum(
        str(event.get("signal_phase") or "") == "confirmed"
        for event in buying
    )
    sell_new = sum(
        str(event.get("signal_phase") or "new") == "new"
        for event in selling
    )
    sell_confirmed = sum(
        str(event.get("signal_phase") or "") == "confirmed"
        for event in selling
    )
    st.markdown(
        f"""
<div class="tfv3-hero">
<b>截至 {escape(str(payload.get("as_of") or ""))}：新共識買 {buy_new}・賣 {sell_new}｜今日仍確認買 {buy_confirmed}・賣 {sell_confirmed}</b>
<p>先扣除 ETF 申購贖回造成的整體持股縮放，再讓每一檔 ETF／個股分別和自己的前 20 個共同交易日比較。第二檔 ETF 在同日加入相同方向時，才正式形成可跟隨的共識；1/3 沒有任何建倉、反手、出清或極端金額例外。</p>
</div>
        """,
        unsafe_allow_html=True,
    )
    quality = payload.get("data_quality") or {}
    estimated = int(quality.get("estimated_move_rows") or 0)
    exact = int(quality.get("exact_move_rows") or 0)
    if estimated and not exact:
        st.info(
            "目前既有歷史尚未保存精確流通單位數，因此使用「基金規模 ÷ 已揭露 NAV」估算。"
            "抓取程式已補上精確欄位，從下一份新資料開始會自動升級為精確計算。"
        )
    st.markdown(render_intent_grid(payload), unsafe_allow_html=True)
    st.caption(
        "紅柱＝主動買方殘差，綠柱＝主動賣方殘差；每檔股票獨立縮放。"
        "主畫面只保留本交易日新形成的 2/3 共識，以及前一交易日形成且今日再次"
        "獲得至少 2/3 ETF 確認的共識。昨日未再確認、單一 ETF、普通持股與單純"
        "降溫均不顯示。"
    )
    with st.expander("V3 如何判斷，和 ETF 動作有什麼不同？"):
        st.markdown(
            """
- **ETF 動作（舊版）**：保留買進、續抱與賣出完整生命週期，適合查核。
- **共識轉折（V3）**：只呈現至少兩位經理人同方向確認、可能可以跟隨的新變化；沒有「續抱」籃。
- **先排除規模變化**：用精確流通單位數計算理論持股，再以實際持股扣除。舊資料沒有單位數時，才使用基金規模除以 NAV 並明確標示為估算。
- **個別門檻先成立**：基金規模調整後的方向，必須與真實股數買賣方向一致，而且每檔 ETF 都必須先通過自己的無前視顯著門檻。
- **再要求固定 2/3**：至少兩檔 ETF 必須在同一共同交易日同方向通過門檻。單檔極端異常、建倉、重新建倉、出清及反手全部不破例。
- **隱藏狀態**：後端仍記錄買方／中性／賣方，以辨認反手與重啟。停止買進不等於開始賣出。
- **不混淆時間**：本交易日新形成與前一交易日形成、今日仍確認分區顯示；昨日沒有再次形成 2/3 共識就直接離開主畫面。
- **類股只作背景**；概念標籤完全不參與 V3 資料解讀。
            """
        )
