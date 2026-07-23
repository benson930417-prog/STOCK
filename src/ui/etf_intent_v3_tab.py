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
    st.subheader("主動 ETF 意圖轉折")
    st.caption("只顯示新買方／新賣方意圖；所有既有持股與普通延續動作留在後端，不拿來填版面。")
    payload = _load(DATA_DIR)
    if not payload:
        st.warning("尚無 V3 意圖資料，請先執行 build_etf_intent_v3.py。")
        return

    buying = list((payload.get("signals") or {}).get("buying") or [])
    selling = list((payload.get("signals") or {}).get("selling") or [])
    st.markdown(
        f"""
<div class="tfv3-hero">
<b>截至 {escape(str(payload.get("as_of") or ""))}：買方轉折 {len(buying)} 檔・賣方轉折 {len(selling)} 檔</b>
<p>先扣除 ETF 申購贖回造成的整體持股縮放，再比較各 ETF／個股過去 20 個共同交易日的正常動作。只有實際股數方向與主動配置方向一致，或持股名單真的新增／移除，才可能進入本頁。</p>
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
        "證據強度衡量動作相對自身近期門檻，不是上漲機率。紅柱＝主動買方殘差，"
        "綠柱＝主動賣方殘差；每檔股票獨立縮放。前交易日訊號只保留一日，並明寫"
        "本交易日是否延續。沒有新轉折的持續持股、持續加碼與單純降溫均不顯示。"
    )
    with st.expander("V3 如何判斷，和 ETF 動作有什麼不同？"):
        st.markdown(
            """
- **ETF 動作（舊版）**：保留買進、續抱與賣出完整生命週期，適合查核。
- **意圖轉折（V3）**：只呈現可能可以跟隨的新變化；沒有「續抱」籃。
- **先排除規模變化**：用精確流通單位數計算理論持股，再以實際持股扣除。舊資料沒有單位數時，才使用基金規模除以 NAV 並明確標示為估算。
- **可複製門檻**：基金規模調整後的方向，必須與真實股數買賣方向一致；否則只是相對配置變化，不冒充實際交易。
- **不是固定 2/3**：兩檔普通顯著動作可以成立；單檔極端異常亦可成立；建倉、重新建倉、出清及反手另有結構性價值。不同方向同時出現而沒有明顯優勢時保持沉默。
- **隱藏狀態**：後端仍記錄買方／中性／賣方，以辨認反手與重啟。停止買進不等於開始賣出。
- **不重複播報**：觸發後下一日若同方向仍顯著，最多顯示一次延續；之後除非重新加速、ETF 共識擴散或方向反轉，否則不再出現。
- **類股只作背景**；概念標籤完全不參與 V3 資料解讀。
            """
        )
