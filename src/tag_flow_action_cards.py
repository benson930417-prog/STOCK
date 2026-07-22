"""Shared HTML cards for the website and cached LINE ETF-action lane images."""
from __future__ import annotations

from html import escape


ACTION_ETFS = ["00403A", "00981A", "00991A"]

ACTION_LANES = {
    "buying": (
        "🔴 買進觀察",
        "剛建倉、重新建倉、賣後轉買",
        "buy",
    ),
    "holding": (
        "🟠 續抱參考",
        "不是新買點；代表加碼認同仍持續",
        "hold",
    ),
    "selling": (
        "🟢 賣出警示",
        "剛出清、買後轉賣、沉寂後重新賣",
        "sell",
    ),
}

ACTION_BOARD_CSS = r"""
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
.tfv2-stock-row {display:flex; justify-content:space-between; gap:.65rem;
  align-items:baseline}
.tfv2-age {display:inline-flex; align-items:center; border:1px solid;
  border-radius:999px; padding:.13rem .42rem; font-size:.68rem; font-weight:720;
  line-height:1.35; white-space:nowrap; flex:none}
.tfv2-age-current {color:rgba(248,250,252,.94); background:rgba(226,232,240,.13);
  border-color:rgba(226,232,240,.38); box-shadow:inset 0 0 0 1px rgba(255,255,255,.035)}
.tfv2-age-prior {color:rgba(203,213,225,.72); background:transparent;
  border-color:rgba(148,163,184,.2)}
.tfv2-age-latest {color:rgba(203,213,225,.7); background:rgba(148,163,184,.06);
  border-color:rgba(148,163,184,.16)}
.tfv2-stock {font-size:1.08rem; font-weight:850; min-width:0}
.tfv2-code {font-size:.72rem; font-weight:700; opacity:.58; margin-left:.42rem}
.tfv2-fields {display:grid; gap:.18rem; margin-top:.42rem}
.tfv2-field {display:grid; grid-template-columns:2.9rem minmax(0,1fr);
  gap:.35rem; font-size:.76rem; line-height:1.42}
.tfv2-field span {opacity:.55}.tfv2-field b {font-weight:650}
.tfv2-empty {font-size:.82rem; opacity:.66; padding:1.1rem .4rem}
@media (max-width:760px){.tfv2-grid{grid-template-columns:1fr}}
"""


def event_card(event: dict, group: str) -> str:
    if group == "hold":
        age = "最新資料仍確認"
        age_class = "latest"
    else:
        is_current = event["age_sessions"] == 0
        age = "本交易日確認" if is_current else "前一交易日確認"
        age_class = "current" if is_current else "prior"
    evidence = "・".join(event.get("evidence_parts") or [event.get("reason", "")])
    stock_id = str(event.get("stock_id") or "")
    identity = escape(str(event["name"]))
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
        '<div class="tfv2-stock-row">'
        f'<div class="tfv2-stock">{identity}</div>'
        f'<span class="tfv2-age tfv2-age-{age_class}">{age}</span>'
        "</div>"
        f'<div class="tfv2-fields">{field_html}</div>'
        "</div>"
    )


def lane(title: str, note: str, events: list[dict], group: str) -> str:
    cards = "".join(event_card(event, group) for event in events)
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
        "</div>"
        f'<div class="tfv2-lane-note">{note}</div>'
        f"{cards}</section>"
    )


def render_action_grid(snapshot: dict) -> str:
    """Render every already-qualified event in the shared engine snapshot."""
    lanes = "".join(render_action_lane(snapshot, key) for key in ACTION_LANES)
    return f'<div class="tfv2-grid">{lanes}</div>'


def render_action_lane(snapshot: dict, key: str) -> str:
    """Render one complete lane with the exact website card component."""
    try:
        title, note, group = ACTION_LANES[key]
    except KeyError as exc:
        raise ValueError(f"Unknown ETF action lane: {key}") from exc
    return lane(title, note, list(snapshot.get(key) or []), group)
