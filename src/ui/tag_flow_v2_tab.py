"""Insight-first active-ETF buy/hold/sell action radar."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.tag_flow_events import build_event_snapshot


ACTION_ETFS = ["00403A", "00981A", "00991A"]


def _load(data_dir):
    path = data_dir / "tag_flow.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _event_card(event: dict, group: str) -> str:
    if group == "hold":
        age = "最新仍確認"
    else:
        age = "今日確認" if event["age_sessions"] == 0 else "昨日確認"
    evidence = "・".join(event.get("evidence_parts") or [event.get("reason", "")])
    stock_id = str(event.get("stock_id") or "")
    identity = escape(event["name"])
    if stock_id:
        identity += f'<span class="tfv2-code">{escape(stock_id)}</span>'
    fields = (
        ("類股", str(event.get("category") or "未分類")),
        ("動作", str(event.get("event_label") or "持股異動")),
        ("ETF", str(event.get("etf_label") or "未提供")),
        ("判定", str(event.get("qualification_label") or "訊號成立")),
        ("依據", evidence),
    )
    field_html = "".join(
        '<div class="tfv2-field">'
        f'<span>{label}</span><b>{escape(value)}</b>'
        "</div>"
        for label, value in fields
    )
    # Keep generated HTML flush-left. Four-space indentation is a Markdown code
    # block, which makes Streamlit print the card markup instead of rendering it.
    return (
        f'<div class="tfv2-card tfv2-{group}">'
        '<div class="tfv2-card-top">'
        f'<span class="tfv2-age">{age}</span>'
        '</div>'
        f'<div class="tfv2-stock">{identity}</div>'
        f'<div class="tfv2-fields">{field_html}</div>'
        '</div>'
    )


def _lane(title: str, note: str, events: list[dict], group: str) -> str:
    cards = "".join(_event_card(event, group) for event in events)
    if not cards:
        cards = (
            '<div class="tfv2-empty">今天沒有新的已確認訊號。'
            "延續性動作不會拿來填版面。</div>"
        )
    return (
        '<section class="tfv2-lane">'
        '<div class="tfv2-lane-head">'
        f'<div class="tfv2-lane-title">{title}</div>'
        f'<div class="tfv2-count">{len(events)}</div>'
        '</div>'
        f'<div class="tfv2-lane-note">{note}</div>'
        f'{cards}</section>'
    )


def render_tag_flow_v2_tab(*, DATA_DIR=None, **kwargs):
    st.markdown(
        """
        <style>
        .tfv2-hero {border:1px solid rgba(148,163,184,.22); border-radius:.9rem;
          padding:1rem 1.1rem; margin:.6rem 0 1rem; background:rgba(30,41,59,.32)}
        .tfv2-hero b {font-size:1.02rem}.tfv2-hero span {opacity:.72; font-size:.82rem}
        .tfv2-rules {display:grid; grid-template-columns:repeat(3,minmax(180px,1fr));
          gap:.65rem; margin:.6rem 0 1rem}
        .tfv2-rule {border:1px solid rgba(148,163,184,.22); border-radius:.75rem;
          padding:.72rem .82rem; background:rgba(30,41,59,.2)}
        .tfv2-rule b {display:block; font-size:.82rem; margin-bottom:.14rem}
        .tfv2-rule span {display:block; font-size:.74rem; opacity:.7; line-height:1.4}
        .tfv2-grid {display:grid; grid-template-columns:repeat(3,minmax(250px,1fr));
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
        .tfv2-buy {border-color:#E74C3C}.tfv2-hold {border-color:#F5A623}
        .tfv2-sell {border-color:#2ECC71}
        .tfv2-card-top {display:flex; justify-content:flex-end; gap:.5rem;
          align-items:center; margin-bottom:.3rem}
        .tfv2-age {font-size:.68rem; opacity:.58}
        .tfv2-stock {font-size:1.08rem; font-weight:850}
        .tfv2-code {font-size:.72rem; font-weight:700; opacity:.58; margin-left:.42rem}
        .tfv2-fields {display:grid; gap:.18rem; margin-top:.42rem}
        .tfv2-field {display:grid; grid-template-columns:2.9rem minmax(0,1fr);
          gap:.35rem; font-size:.76rem; line-height:1.42}
        .tfv2-field span {opacity:.55}.tfv2-field b {font-weight:650}
        .tfv2-empty {font-size:.82rem; opacity:.66; padding:1.1rem .4rem}
        @media (max-width:760px){.tfv2-grid,.tfv2-rules{grid-template-columns:1fr}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 買／抱／賣雷達")
    st.caption(
        "買＝剛出現的建倉／反手；抱＝不是新買點，但持續加碼仍獲確認；"
        "賣＝剛出現的轉賣／出清。只留下能改變決策的訊號。"
    )
    st.markdown(
        """
<div class="tfv2-rules">
<div class="tfv2-rule"><b>一般訊號：至少 2/3 同向</b><span>普通加碼、減碼或沉寂後重啟，少於兩檔 ETF 就不顯示。</span></div>
<div class="tfv2-rule"><b>1/3 只保留兩種例外</b><span>持股名單改變（建倉／出清），或單一 ETF 反轉且連續 2 日確認。</span></div>
<div class="tfv2-rule"><b>續抱：至少 2 檔參與</b><span>近 10 日反覆加碼、買多於賣，而且最新仍在買。</span></div>
</div>
        """,
        unsafe_allow_html=True,
    )

    data = _load(DATA_DIR)
    if not data:
        st.warning("尚無 ETF 動作資料。")
        return
    selected_etfs = [etf for etf in ACTION_ETFS if etf in data.get("etfs", [])]
    if not selected_etfs:
        st.warning("找不到動作雷達所需的主動 ETF 資料。")
        return
    has_position_events = any(
        move.get("position_event")
        for observation in data.get("observations", [])[-12:]
        for move in observation.get("stocks", [])
    )
    if not has_position_events:
        st.info("ETF 動作資料尚未建立，請先重新執行 build_tag_flow.py。")
        return

    snapshot = build_event_snapshot(data, selected_etfs)
    buying = snapshot["buying"]
    holding = snapshot["holding"]
    selling = snapshot["selling"]
    buy_names = "、".join(event["name"] for event in buying[:3]) or "無"
    hold_names = "、".join(event["name"] for event in holding[:3]) or "無"
    sell_names = "、".join(event["name"] for event in selling[:3]) or "無"
    st.markdown(
        f"""
        <div class="tfv2-hero">
          <b>截至 {escape(snapshot['as_of'])} 的決策訊號</b><br>
          <span>🔴 買進觀察：{escape(buy_names)}　｜　🟠 續抱參考：{escape(hold_names)}　｜　🟢 賣出警示：{escape(sell_names)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tfv2-grid">'
        + _lane("🔴 買進觀察", "剛建倉、重新建倉、賣後轉買", buying, "buy")
        + _lane("🟠 續抱參考", "不是新買點；代表加碼認同仍持續", holding, "hold")
        + _lane("🟢 賣出警示", "剛出清、買後轉賣、沉寂後重新賣", selling, "sell")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "每張卡固定使用：類股／動作／ETF／判定／依據。普通小額調整仍會隱藏。"
        "類股只作背景標籤，不參與事件判斷。"
    )
    with st.expander("ETF 動作何時才算『剛確認』？"):
        st.markdown(
            """
- **新建倉**：股票新出現在 ETF 持股，當日直接成立；極小部位標成「試單建倉」。
- **重新建倉**：同一檔 ETF 先前曾完整出清，現在又把股票納回持股。
- **完全出清**：股票從持股名單消失，而且原部位不是可忽略的小尾巴。
- **一般門檻**：普通加減碼與沉寂後重啟，都必須至少 2/3 ETF 同向。
- **1/3 例外**：只保留持股名單改變（建倉／重新建倉／完整出清），或單一 ETF 的反手連續 2 個共同交易日。
- **反手背景**：過去 20 個共同交易日明顯偏向一邊，現在方向相反；2/3 可當日成立，1/3 必須連續 2 日。
- **續抱參考**：至少 2 檔 ETF 曾參與；近 10 個共同交易日至少 4 日明顯加碼、買日明顯多於賣日、累積強度足夠，而且最新仍在買。
- **不顯示**：小額雜訊、普通 1/3 動作、單日未確認反手、沒有持續證據的普通加減碼。

動作雷達只核對 ETF 的持股動作；「股價高／低」不會由持股變化自行推論。
            """
        )
