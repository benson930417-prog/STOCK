"""Insight-first V2 active-ETF action radar."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.tag_flow_events import build_event_snapshot


V2_ETFS = ["00403A", "00981A", "00991A"]


def _load(data_dir):
    path = data_dir / "tag_flow.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _event_card(event: dict, group: str) -> str:
    age = "今日確認" if event["age_sessions"] == 0 else "昨日確認"
    return f"""
    <div class="tfv2-card tfv2-{group}">
      <div class="tfv2-card-top">
        <span class="tfv2-event-label">{escape(event['event_label'])}</span>
        <span class="tfv2-age">{age}</span>
      </div>
      <div class="tfv2-stock">
        {escape(event['name'])}
        <span class="tfv2-category">{escape(event['category'])}</span>
      </div>
      <div class="tfv2-reason">{escape(event['reason'])}</div>
      <div class="tfv2-confirm">✓ {escape(event['confirmation_label'])}</div>
    </div>
    """


def _lane(title: str, note: str, events: list[dict], group: str) -> str:
    cards = "".join(_event_card(event, group) for event in events)
    if not cards:
        cards = (
            '<div class="tfv2-empty">今天沒有新的已確認訊號。'
            "延續性動作不會拿來填版面。</div>"
        )
    return f"""
    <section class="tfv2-lane">
      <div class="tfv2-lane-head">
        <div class="tfv2-lane-title">{title}</div>
        <div class="tfv2-count">{len(events)}</div>
      </div>
      <div class="tfv2-lane-note">{note}</div>
      {cards}
    </section>
    """


def render_tag_flow_v2_tab(*, DATA_DIR=None, **kwargs):
    st.markdown(
        """
        <style>
        .tfv2-hero {border:1px solid rgba(148,163,184,.22); border-radius:.9rem;
          padding:1rem 1.1rem; margin:.6rem 0 1rem; background:rgba(30,41,59,.32)}
        .tfv2-hero b {font-size:1.02rem}.tfv2-hero span {opacity:.72; font-size:.82rem}
        .tfv2-grid {display:grid; grid-template-columns:repeat(2,minmax(280px,1fr));
          gap:1rem; margin:.6rem 0 1rem}
        .tfv2-lane {border:1px solid rgba(148,163,184,.22); border-radius:.9rem;
          padding:.9rem; background:rgba(15,23,42,.24)}
        .tfv2-lane-head {display:flex; justify-content:space-between; align-items:center}
        .tfv2-lane-title {font-size:1rem; font-weight:800}
        .tfv2-count {min-width:1.75rem; height:1.75rem; border-radius:999px;
          display:flex; align-items:center; justify-content:center;
          background:rgba(148,163,184,.14); font-weight:800}
        .tfv2-lane-note {font-size:.76rem; opacity:.68; margin:.12rem 0 .7rem}
        .tfv2-card {border-left:4px solid; border-radius:.7rem; padding:.78rem .82rem;
          margin:.55rem 0; background:rgba(15,23,42,.48)}
        .tfv2-buy {border-color:#E74C3C}.tfv2-sell {border-color:#2ECC71}
        .tfv2-card-top {display:flex; justify-content:space-between; gap:.5rem;
          align-items:center; margin-bottom:.3rem}
        .tfv2-event-label {font-size:.76rem; font-weight:800; letter-spacing:.02em}
        .tfv2-buy .tfv2-event-label {color:#FF6B61}
        .tfv2-sell .tfv2-event-label {color:#55DC85}
        .tfv2-age {font-size:.68rem; opacity:.58}
        .tfv2-stock {font-size:1.08rem; font-weight:850}
        .tfv2-category {font-size:.68rem; font-weight:650; opacity:.68;
          margin-left:.42rem; border:1px solid rgba(148,163,184,.25);
          border-radius:999px; padding:.12rem .42rem; vertical-align:middle}
        .tfv2-reason {font-size:.82rem; margin-top:.38rem; line-height:1.42}
        .tfv2-confirm {font-size:.72rem; opacity:.68; margin-top:.25rem}
        .tfv2-empty {font-size:.82rem; opacity:.66; padding:1.1rem .4rem}
        @media (max-width:760px){.tfv2-grid{grid-template-columns:1fr}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 動作雷達 V2")
    st.caption(
        "只顯示剛確認的建倉、出清、反手與沉寂後重啟；已經持續多日的加減碼不列。"
        "目標是抓到可跟進的起點，不是重複整理持股現況。"
    )

    data = _load(DATA_DIR)
    if not data:
        st.warning("尚無題材流向資料。")
        return
    selected_etfs = [etf for etf in V2_ETFS if etf in data.get("etfs", [])]
    if not selected_etfs:
        st.warning("找不到 V2 所需的主動 ETF 資料。")
        return
    has_position_events = any(
        move.get("position_event")
        for observation in data.get("observations", [])[-12:]
        for move in observation.get("stocks", [])
    )
    if not has_position_events:
        st.info("V2 動作資料尚未建立，請先重新執行 build_tag_flow.py。")
        return

    snapshot = build_event_snapshot(data, selected_etfs)
    buying = snapshot["buying"]
    selling = snapshot["selling"]
    buy_names = "、".join(event["name"] for event in buying[:3]) or "無"
    sell_names = "、".join(event["name"] for event in selling[:3]) or "無"
    st.markdown(
        f"""
        <div class="tfv2-hero">
          <b>截至 {escape(snapshot['as_of'])} 的新動作</b><br>
          <span>🔴 開始買：{escape(buy_names)}　｜　🟢 開始賣：{escape(sell_names)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tfv2-grid">'
        + _lane("🔴 剛開始買", "新建倉、賣後轉買、沉寂後重新買", buying, "buy")
        + _lane("🟢 剛開始賣", "完整出清、買後轉賣、沉寂後重新賣", selling, "sell")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("普通小額調整與延續性買賣已自動隱藏；類股只作背景標籤，不參與事件判斷。")
    with st.expander("V2 何時才算『剛確認』？"):
        st.markdown(
            """
- **新建倉**：股票首次出現在 ETF 持股，當日直接成立；極小部位標成「試單建倉」。
- **完全出清**：股票從持股名單消失，而且原部位不是可忽略的小尾巴。
- **反手**：過去 20 個共同交易日長期偏向一邊，現在由至少 2 檔 ETF 同步反向，或同方向連續 2 日。
- **沉寂後重啟**：至少 5 個共同交易日沒有明顯動作，之後重新出現已確認買賣。
- **不顯示**：小額雜訊、單日未確認換股、已經連續多日的同方向加減碼。
            """
        )
