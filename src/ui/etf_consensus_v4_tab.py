"""Historical active-ETF consensus dashboard."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.etf_consensus_v4 import hydrate_board
from src.etf_consensus_v4_cards import (
    V4_CSS,
    render_v4_grid,
    render_v4_priority_summary,
)


def _load(data_dir):
    path = data_dir / "etf_consensus_v4.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_etf_consensus_v4_tab(*, DATA_DIR=None, **kwargs):
    st.markdown(
        "<style>"
        + V4_CSS
        + """
        .tfv4-hero {border:1px solid rgba(148,163,184,.23);border-radius:.95rem;
          padding:.95rem 1rem;margin:.55rem 0 .8rem;
          background:linear-gradient(135deg,rgba(30,41,59,.40),rgba(15,23,42,.22))}
        .tfv4-hero b {font-size:1rem}
        .tfv4-hero p {font-size:.76rem;opacity:.76;line-height:1.58;
          margin:.32rem 0 0}
        .tfv4-date {font-size:.69rem;opacity:.68;margin:.15rem 0 .35rem}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 共識追蹤")
    st.caption("V4 最終版｜看經理人主動配置的形成、確認、延續與降溫，不把普通持股當訊號。")
    raw = _load(DATA_DIR)
    if not raw:
        st.warning("尚無 V4 共識資料，請先執行 build_etf_consensus_v4.py。")
        return

    dates = list(raw.get("dates") or [])
    selected = st.select_slider(
        "歷史交易日",
        options=dates,
        value=str(raw.get("as_of") or dates[-1]),
        format_func=lambda value: str(value).replace("-", "/"),
        help="這是歷史回放，不是改變判斷區間。3／10／20 日模型固定不變。",
    )
    payload = hydrate_board(raw, selected)
    signals = payload.get("signals") or {}
    watch_count = len(signals.get("watching") or [])
    buy_count = len(signals.get("buying") or [])
    sell_count = len(signals.get("selling") or [])
    core_count = sum(
        1
        for lane_key in ("buying", "selling")
        for card in signals.get(lane_key) or []
        if str(card.get("decision_tier") or "") == "core"
    )
    st.markdown(
        f"""
<div class="tfv4-hero">
<b>截至 {escape(selected)}：核心決策 {core_count}・觀察 {watch_count}・買方共識 {buy_count}・賣方共識 {sell_count}</b>
<p><b>顏色是硬判定：</b>黃燈只有一位經理人的高資訊前兆，不能靠高分升級；
紅／綠燈一定要至少兩檔 ETF 各自先通過顯著門檻，且動作在 3 個共同交易日內重疊。
進入共識後，每檔 ETF 的近 10 日淨動作（第一導數）都必須維持同方向；失去第二位經理人就降回黃燈。</p>
<p><b>核心是決策排序，不是第四種訊號：</b>紅／綠共識仍全部保留，但只有第二位經理人的
動作仍夠新、夠大，並已重複確認或剛形成強訊號者置頂。這能減少選擇負擔，又不會把有效證據藏掉。</p>
<p><b>分數不是勝率：</b>只用來比較同色訊號的成熟度。紅綠評分＝獨立 ETF、
共同持續、各自相對強度、訊號新鮮度、3／10／20 日一致性。沒有新的顯著同向動作，
新鮮度會逐交易日扣分；卡片也會直接顯示最晚還能維持多久。</p>
</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(render_v4_priority_summary(payload), unsafe_allow_html=True)
    st.markdown(render_v4_grid(payload), unsafe_allow_html=True)
    st.caption(
        "紅柱＝主動買進、綠柱＝主動賣出；柱高是相對該 ETF 近 10 日平常單筆動作的倍數，"
        "不是門檻倍數也不是跨 ETF 比億元，因此 981 規模較大不會壓過其他 ETF。帶色區是 3 日訊號重疊窗，"
        "同色亮框是該 ETF 最近一次顯著動作；沒有動作就不畫空框。"
    )
    quality = payload.get("data_quality") or {}
    if int(quality.get("estimated_move_rows") or 0) and not int(
        quality.get("exact_move_rows") or 0
    ):
        st.info(
            "舊歷史尚無精確流通單位數，使用基金規模 ÷ NAV 估算規模變化；"
            "新抓取資料保存精確單位數後會自動改用精確值。"
        )
    with st.expander("V4 判斷規則與使用方式"):
        st.markdown(
            """
- **先判斷單一 ETF 是否真的有動作**：扣除申購贖回造成的機械式持股縮放，真實股數方向還必須和主動配置方向一致。顯著門檻是該 ETF 前 10 個共同交易日、同方向非零單筆中位數的 60%（最低 0.02% ETF 規模）；樣本不足才合併買賣方向。當日不會偷看自己。
- **「幾倍」不是門檻倍數**：卡片的 `2.4×平常單筆` 是這筆動作 ÷ 該 ETF 近期平常單筆；另列實際估計億元只幫助直覺理解，不參與跨 ETF 排名。
- **黃燈不是建議買賣**：只保留新建倉、重新建倉、完整出清、方向反轉、沉寂後重啟或共識降溫；普通 1/3 買賣全部隱藏。
- **紅／綠一定是獨立確認**：至少 2/3 ETF 在最多 3 個共同交易日內同方向各自通過門檻，避免要求經理人恰好同一天完成分批交易。
- **固定三尺度，不提供會改故事的區間旋鈕**：3 日 EWMA 看當前壓力、10 日 EWMA 看主方向、20 日 EWMA 看背景。歷史滑桿只回放當時畫面。
- **共識不是永久續抱**：維持時至少兩位經理人的近 10 日淨主動配置必須仍為同方向，且共識強度至少 40。每個沒有新顯著動作的交易日都會扣新鮮度；掉到一位就降為黃燈，沒有支持就離場，最晚在舊證據離開 10 日窗時消失。
- **核心決策層只負責縮小選擇**：必須先是紅／綠共識，再同時滿足共識分數至少 60、新鮮度至少 8、相對力道至少 8、3／10／20 一致，並且是重複確認或三日內剛形成的強訊號。沒進核心不等於訊號無效，只代表暫時不該和最有把握者搶注意力。
- **類股只作背景**，概念標籤完全不參與 V4 判斷。

這是 ETF 經理人行為偵測，不是報酬保證；實際交易仍需配合價格、估值與風險控管。
            """
        )
