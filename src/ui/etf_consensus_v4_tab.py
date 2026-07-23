"""Historical active-ETF consensus dashboard."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.etf_consensus_v4 import hydrate_board
from src.etf_consensus_v4_cards import V4_CSS, render_v4_grid


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
    st.markdown(
        f"""
<div class="tfv4-hero">
<b>截至 {escape(selected)}：觀察 {watch_count}・買方共識 {buy_count}・賣方共識 {sell_count}</b>
<p><b>顏色是硬判定：</b>黃燈只有一位經理人的高資訊前兆，不能靠高分升級；
紅／綠燈一定要至少兩檔 ETF 各自先通過自身門檻，且顯著動作在 3 個共同交易日內重疊。
進入共識後，用固定 10 日方向與 20 日背景判斷是否仍成立；失去第二位經理人就降回黃燈。</p>
<p><b>分數不是勝率：</b>只用來比較同色訊號的成熟度。紅綠評分＝獨立 ETF、
共同持續、各自相對強度、3／10／20 日一致性；黃燈評分＝事件品質、相對大小、
重複動作、第二檔 ETF 是否接近門檻。</p>
</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(render_v4_grid(payload), unsafe_allow_html=True)
    st.caption(
        "紅柱＝主動買進、綠柱＝主動賣出；柱高用各 ETF／個股自己的歷史門檻"
        "標準化，因此 981 規模較大也不會壓過其他 ETF。淡帶是 3 日訊號重疊窗，"
        "亮框是該 ETF 最近一次顯著動作；沒有動作就不畫空框。"
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
- **先判斷單一 ETF 是否真的有動作**：扣除申購贖回造成的機械式持股縮放，真實股數方向還必須和主動配置方向一致，再和該 ETF／個股自己過去 20 日的無前視門檻比較。
- **黃燈不是建議買賣**：只保留新建倉、重新建倉、完整出清、方向反轉、沉寂後重啟或共識降溫；普通 1/3 買賣全部隱藏。
- **紅／綠一定是獨立確認**：至少 2/3 ETF 在最多 3 個共同交易日內同方向各自通過門檻，避免要求經理人恰好同一天完成分批交易。
- **固定三尺度，不提供會改故事的區間旋鈕**：3 日 EWMA 看當前壓力、10 日 EWMA 看主方向、20 日 EWMA 看背景。歷史滑桿只回放當時畫面。
- **共識不是永久續抱**：維持時仍要有至少兩位經理人的 10 日方向支持，且共識強度至少 40；掉到一位就降為黃燈，沒有支持就離場。
- **類股只作背景**，概念標籤完全不參與 V4 判斷。

這是 ETF 經理人行為偵測，不是報酬保證；實際交易仍需配合價格、估值與風險控管。
            """
        )
