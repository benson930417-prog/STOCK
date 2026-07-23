"""Insight-first active-ETF buy/hold/sell action radar."""
from __future__ import annotations

from html import escape
import json

import streamlit as st

from src.tag_flow_action_cards import (
    ACTION_BOARD_CSS,
    ACTION_ETFS,
    event_card as _event_card,
    lane as _lane,
    render_action_grid,
)
from src.tag_flow_events import build_event_snapshot


def _load(data_dir):
    path = data_dir / "tag_flow.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def render_tag_flow_v2_tab(*, DATA_DIR=None, **kwargs):
    st.markdown(
        "<style>"
        + ACTION_BOARD_CSS
        + """
        .tfv2-hero {border:1px solid rgba(148,163,184,.22); border-radius:.9rem;
          padding:1rem 1.1rem; margin:.6rem 0 1rem; background:rgba(30,41,59,.32)}
        .tfv2-hero b {font-size:1.02rem}.tfv2-hero span {opacity:.72; font-size:.82rem}
        .tfv2-rules {display:grid; grid-template-columns:repeat(3,minmax(180px,1fr));
          gap:.65rem; margin:.6rem 0 1rem}
        .tfv2-rule {border:1px solid rgba(148,163,184,.22); border-radius:.75rem;
          padding:.72rem .82rem; background:rgba(30,41,59,.2)}
        .tfv2-rule b {display:block; font-size:.82rem; margin-bottom:.14rem}
        .tfv2-rule span {display:block; font-size:.74rem; opacity:.7; line-height:1.4}
        @media (max-width:760px){.tfv2-rules{grid-template-columns:1fr}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 買／抱／賣雷達")
    st.caption(
        "買＝新觸發或觸發後今日續買；抱＝接近門檻、有效續抱、臨界或剛降級；"
        "賣＝新觸發、今日續賣或由續抱轉賣。狀態不再無提示地消失。"
    )
    st.markdown(
        """
<div class="tfv2-rules">
<div class="tfv2-rule"><b>先判斷每筆動作是否顯著</b><span>逐一比較同 ETF、同股票、同方向近 10 日慣常量；規模比已排除基金大小差異。</span></div>
<div class="tfv2-rule"><b>1/3 只保留兩種例外</b><span>持股名單改變（建倉／出清），或單一 ETF 反轉且連續 2 日確認。</span></div>
<div class="tfv2-rule"><b>續抱是滾動 10 日狀態</b><span>顯示尚差幾次、最早證據剩幾日、無新買幾日後降級；安靜一天不會直接消失。</span></div>
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
    st.markdown(render_action_grid(snapshot), unsafe_allow_html=True)
    st.caption(
        "買進／賣出保留觸發日與下一個共同交易日；若第二天仍有同方向顯著動作，"
        "會明寫『昨日觸發・今日續買／續賣』。續抱每天用滾動 10 日重算，"
        "並保留剛降級的一日轉換提示。每張卡使用同一組狀態欄位，普通小額調整仍隱藏。"
        "底部 20 日迷你圖加總三檔 ETF 的規模比動作，只看該股自身節奏；不可用柱高跨股票比較。"
        "類股只作背景標籤，不參與事件判斷。"
    )
    with st.expander("ETF 動作何時才算『剛確認』？"):
        st.markdown(
            """
- **新建倉**：股票新出現在 ETF 持股，當日直接成立；極小部位標成「試單建倉」。
- **重新建倉**：同一檔 ETF 先前曾完整出清，現在又把股票納回持股。
- **完全出清**：股票從持股名單消失，而且原部位不是可忽略的小尾巴。
- **單筆顯著性**：先以基金規模標準化，再把每一筆動作和「同 ETF、同股票、同方向」前 10 個共同交易日的慣常量比較；至少達慣常中位數的 60% 且不低於 0.02%。歷史不足才退回 ETF 的整體基準，計算不使用未來資料。
- **一般門檻**：通過單筆顯著性後，普通加減碼與沉寂後重啟仍必須至少 2/3 ETF 同向。
- **顯示期限與續做**：觸發保留 2 個共同交易日；第二天若仍有同方向顯著動作，明確標成「昨日觸發・今日續買／續賣」，而不是只顯示昨天。
- **1/3 例外**：只保留持股名單改變（建倉／重新建倉／完整出清），或單一 ETF 的反手連續 2 個共同交易日。
- **反手背景**：過去 20 個共同交易日明顯偏向一邊，現在方向相反；2/3 可當日成立，1/3 必須連續 2 日。
- **20 日迷你圖**：紅柱為規模比淨買、綠柱為規模比淨賣；三檔 ETF 的規模比相加，不使用絕對億元。每檔股票獨立縮放，只用來看持續、沉寂與反手形狀。
- **證據標記**：實線框是單日確認；連接框是兩日反轉；框上圓點是建倉／出清等持股名單改變；虛線框是沉寂後重啟；續抱則框最新一日並在最近 10 日下方加淡色底線。框只落在真正成立訊號的交易日，不會固定圈最後兩根。
- **續抱參考**：每天用最近 10 個共同交易日重算；至少 2 檔 ETF 曾參與、至少 4 日顯著加碼、買日比賣日至少多 2 日、規模比淨買至少 +0.20%。不再強迫最後一日一定買；卡片會顯示安靜幾日後降級。
- **升級／降級**：只差一次且其他續抱條件已達標時顯示升級觀察；續抱條件剛失效時保留「今日降級」提示，若同時形成賣出訊號則標為「續抱→賣出警示」。
- **不顯示**：小額雜訊、普通 1/3 動作、單日未確認反手、沒有持續證據的普通加減碼。

動作雷達只核對 ETF 的持股動作；「股價高／低」不會由持股變化自行推論。
            """
        )
