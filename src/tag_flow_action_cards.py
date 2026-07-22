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
.tfv2-flow {margin-top:.52rem; padding-top:.38rem;
  border-top:1px solid rgba(148,163,184,.11)}
.tfv2-flow-head {display:flex; justify-content:space-between; align-items:center;
  margin-bottom:.08rem; color:rgba(203,213,225,.58); font-size:.62rem;
  font-weight:650; line-height:1.2}
.tfv2-flow-chart {display:block; width:100%; height:2.35rem; overflow:visible}
.tfv2-flow-time {display:flex; justify-content:space-between; margin-top:-.12rem;
  color:rgba(203,213,225,.52); font-size:.58rem; font-weight:650;
  line-height:1.1}
.tfv2-flow-axis {stroke:rgba(148,163,184,.2); stroke-width:1}
.tfv2-flow-frame {fill:rgba(226,232,240,.045); stroke:rgba(248,250,252,.7);
  stroke-width:1.15}
.tfv2-flow-frame-reversal {fill:rgba(226,232,240,.025); stroke-width:1.3}
.tfv2-flow-frame-restart {fill:rgba(226,232,240,.025); stroke-dasharray:3 2}
.tfv2-flow-structural-dot {fill:rgba(248,250,252,.9)}
.tfv2-flow-hold-window {fill:none; stroke:rgba(226,232,240,.42);
  stroke-width:1}
.tfv2-flow-buy {fill:#E76A5C; opacity:.76}
.tfv2-flow-sell {fill:#45C879; opacity:.76}
.tfv2-empty {font-size:.82rem; opacity:.66; padding:1.1rem .4rem}
@media (max-width:760px){.tfv2-grid{grid-template-columns:1fr}}
"""


def _flow_sparkline(event: dict) -> str:
    trend = list(event.get("flow_trend_20d") or [])[-20:]
    if not trend:
        return ""
    width = 320.0
    height = 42.0
    center = height / 2
    max_bar = 17.0
    slot = width / max(1, len(trend))
    bar_width = max(2.0, slot * 0.56)
    values = [float(row.get("flow") or 0.0) for row in trend]
    scale = max((abs(value) for value in values), default=0.0) or 1.0
    elements = [
        f'<line class="tfv2-flow-axis" x1="0" y1="{center:g}" '
        f'x2="{width:g}" y2="{center:g}"/>'
    ]
    event_date = str(event.get("event_date") or "")
    signal_index = next(
        (
            index
            for index, row in enumerate(trend)
            if str(row.get("date") or "") == event_date
        ),
        None,
    )
    if signal_index is None:
        age = max(0, int(event.get("age_sessions") or 0))
        signal_index = max(0, len(trend) - 1 - age)

    event_type = str(event.get("event_type") or "")
    confirmation = str(event.get("confirmation") or "")
    structural_types = {
        "new_position",
        "trial_position",
        "reentry_position",
        "full_exit",
    }
    reversal_types = {"sell_to_buy", "buy_to_sell"}
    restart_types = {"restart_buy", "restart_sell"}
    marker_start = signal_index
    marker_span = 1
    marker_class = ""
    marker_label = "訊號確認"
    if event_type in reversal_types and confirmation == "persistence":
        marker_start = max(0, signal_index - 1)
        marker_span = signal_index - marker_start + 1
        marker_class = " tfv2-flow-frame-reversal"
        marker_label = "連續兩日反轉確認"
    elif event_type in restart_types:
        marker_class = " tfv2-flow-frame-restart"
        marker_label = "沉寂後重啟確認"
    elif event_type == "conviction_buy":
        marker_label = "最新續抱確認"
        window_start = max(0, len(trend) - 10)
        x1 = window_start * slot + 1
        x2 = len(trend) * slot - 1
        y = height - 2
        elements.append(
            f'<path class="tfv2-flow-hold-window" '
            f'd="M {x1:.2f} {y - 3:g} V {y:g} H {x2:.2f} V {y - 3:g}">'
            '<title>最近10日續抱觀察區</title></path>'
        )
    elif event_type in structural_types:
        marker_label = "持股名單改變"

    marker_x = marker_start * slot + 0.5
    marker_width = max(1.0, marker_span * slot - 1)
    signal_date = escape(event_date[-5:].replace("-", "/"))
    elements.append(
        f'<rect class="tfv2-flow-frame{marker_class}" x="{marker_x:.2f}" y="1" '
        f'width="{marker_width:.2f}" height="{height - 2:g}" rx="2">'
        f'<title>{marker_label} {signal_date}</title></rect>'
    )
    structural_dot = ""
    if event_type in structural_types:
        dot_x = (signal_index + 0.5) * slot
        structural_dot = (
            f'<circle class="tfv2-flow-structural-dot" cx="{dot_x:.2f}" cy="4" r="2.1">'
            '<title>持股名單改變</title></circle>'
        )
    for index, (row, value) in enumerate(zip(trend, values)):
        if abs(value) <= 1e-9:
            continue
        bar_height = max(1.5, abs(value) / scale * max_bar)
        x = index * slot + (slot - bar_width) / 2
        y = center - bar_height if value > 0 else center
        css_class = "tfv2-flow-buy" if value > 0 else "tfv2-flow-sell"
        date = escape(str(row.get("date") or "")[-5:].replace("-", "/"))
        signed = f"{value:+.4f}%"
        elements.append(
            f'<rect class="tfv2-flow-bar {css_class}" x="{x:.2f}" y="{y:.2f}" '
            f'width="{bar_width:.2f}" height="{bar_height:.2f}" rx="1">'
            f'<title>{date} {signed} 規模比合計</title></rect>'
        )
    if structural_dot:
        elements.append(structural_dot)
    chart = "".join(elements)
    return (
        '<div class="tfv2-flow">'
        '<div class="tfv2-flow-head"><span>20日規模比淨動作</span></div>'
        f'<svg class="tfv2-flow-chart" viewBox="0 0 {width:g} {height:g}" '
        f'role="img" aria-label="{escape(str(event.get("name") or ""))}最近20日規模比淨動作">'
        f'{chart}</svg><div class="tfv2-flow-time"><span>20日前</span>'
        '<span>最新</span></div></div>'
    )


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
    flow_html = _flow_sparkline(event)
    # Keep generated HTML flush-left. Four-space indentation is a Markdown code
    # block, which makes Streamlit print the card markup instead of rendering it.
    return (
        f'<div class="tfv2-card tfv2-{group}">'
        '<div class="tfv2-stock-row">'
        f'<div class="tfv2-stock">{identity}</div>'
        f'<span class="tfv2-age tfv2-age-{age_class}">{age}</span>'
        "</div>"
        f'<div class="tfv2-fields">{field_html}</div>'
        f"{flow_html}"
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
