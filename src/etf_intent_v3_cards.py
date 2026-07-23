"""Shared V3 intent cards for Streamlit and cached LINE images."""
from __future__ import annotations

from html import escape


INTENT_LANES = {
    "buying": (
        "🔴 買方共識",
        "至少 2/3 ETF 同日顯著買進；單一 ETF 不上榜",
        "buy",
    ),
    "selling": (
        "🟢 賣方共識",
        "至少 2/3 ETF 同日顯著賣出；單一 ETF 不上榜",
        "sell",
    ),
}

INTENT_CSS = """
.tfv3-grid {display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  gap:1rem; align-items:start; margin:.65rem 0 1rem}
.tfv3-lane {border:1px solid rgba(148,163,184,.24); border-radius:1rem;
  padding:1rem; background:rgba(15,23,42,.34)}
.tfv3-lane-head {display:flex; justify-content:space-between; align-items:center;
  gap:.6rem}
.tfv3-lane-title {font-weight:850; font-size:1.02rem}
.tfv3-count {display:inline-flex; justify-content:center; align-items:center;
  min-width:2rem; height:2rem; padding:0 .55rem; border-radius:999px;
  background:rgba(148,163,184,.13); font-size:.72rem; font-weight:800;
  white-space:nowrap}
.tfv3-lane-note {font-size:.72rem; opacity:.64; margin:.12rem 0 .72rem}
.tfv3-section-head {display:flex; align-items:center; gap:.45rem;
  margin:.82rem 0 .25rem; padding-top:.62rem;
  border-top:1px solid rgba(148,163,184,.16);
  font-size:.72rem; font-weight:820; color:rgba(241,245,249,.82)}
.tfv3-section-head:first-of-type {margin-top:.32rem; padding-top:.12rem;
  border-top:0}
.tfv3-section-count {display:inline-flex; align-items:center; justify-content:center;
  min-width:1.4rem; height:1.4rem; padding:0 .34rem; border-radius:999px;
  background:rgba(148,163,184,.12); font-size:.64rem}
.tfv3-card {position:relative; border-radius:.82rem; padding:.9rem .92rem;
  margin:.62rem 0; background:#111827; border-left:4px solid transparent;
  box-shadow:0 5px 18px rgba(0,0,0,.12)}
.tfv3-buy {border-left-color:#E75B4E}
.tfv3-sell {border-left-color:#45C879}
.tfv3-top {display:flex; justify-content:space-between; align-items:flex-start;
  gap:.6rem}
.tfv3-stock {font-size:1rem; font-weight:900; line-height:1.25}
.tfv3-code {font-size:.64rem; opacity:.58; margin-left:.38rem}
.tfv3-time {font-size:.61rem; line-height:1.2; white-space:nowrap; padding:.25rem .48rem;
  border:1px solid rgba(226,232,240,.3); color:rgba(241,245,249,.86);
  border-radius:999px}
.tfv3-time-current {background:rgba(226,232,240,.10);
  border-color:rgba(248,250,252,.58)}
.tfv3-time-prior {opacity:.64}
.tfv3-signal {display:flex; justify-content:space-between; align-items:center;
  gap:.55rem; font-size:.82rem; font-weight:850; margin:.55rem 0 .18rem}
.tfv3-consensus {font-size:.61rem; color:rgba(226,232,240,.74); white-space:nowrap;
  font-weight:700}
.tfv3-reason {font-size:.72rem; line-height:1.5; color:rgba(226,232,240,.78)}
.tfv3-meta {display:grid; grid-template-columns:4.15rem minmax(0,1fr);
  gap:.2rem .42rem; margin-top:.55rem; font-size:.70rem; line-height:1.42}
.tfv3-meta span {color:rgba(203,213,225,.56)}
.tfv3-meta b {font-weight:720; min-width:0}
.tfv3-evidence {margin-top:.58rem; padding:.48rem .55rem;
  border-radius:.58rem; background:rgba(148,163,184,.055);
  font-size:.65rem; line-height:1.55; color:rgba(226,232,240,.76)}
.tfv3-evidence-row {display:flex; justify-content:space-between; gap:.55rem}
.tfv3-evidence-row b {font-weight:760; color:rgba(248,250,252,.9)}
.tfv3-chart-wrap {border-top:1px solid rgba(148,163,184,.13);
  margin-top:.6rem; padding-top:.42rem}
.tfv3-chart-head {display:flex; justify-content:space-between; align-items:center;
  font-size:.58rem; color:rgba(203,213,225,.5); margin-bottom:.12rem}
.tfv3-chart {display:block; width:100%; height:2.25rem; overflow:visible}
.tfv3-axis {stroke:rgba(148,163,184,.22); stroke-width:1}
.tfv3-buybar {fill:#E76A5C; opacity:.76}
.tfv3-sellbar {fill:#45C879; opacity:.78}
.tfv3-marker {fill:none; stroke:rgba(248,250,252,.82); stroke-width:1.1}
.tfv3-marker-prior {stroke-dasharray:2 2; opacity:.58}
.tfv3-empty {font-size:.76rem; opacity:.62; padding:1rem .2rem}
@media (max-width:760px){.tfv3-grid{grid-template-columns:1fr}}
"""


def _sparkline(event: dict) -> str:
    trend = list(event.get("flow_trend_20d") or [])[-20:]
    if not trend:
        return ""
    width = 320.0
    height = 39.0
    center = height / 2
    slot = width / max(1, len(trend))
    bar_width = max(2.0, slot * .56)
    values = [float(row.get("flow") or 0.0) for row in trend]
    scale = max((abs(value) for value in values), default=0.0) or 1.0
    elements = [
        f'<line class="tfv3-axis" x1="0" y1="{center:g}" '
        f'x2="{width:g}" y2="{center:g}"/>'
    ]
    signal_date = str(event.get("signal_date") or "")
    signal_index = next(
        (
            index
            for index, row in enumerate(trend)
            if str(row.get("date") or "") == signal_date
        ),
        max(0, len(trend) - 1 - int(event.get("age_sessions") or 0)),
    )
    marker_class = (
        "tfv3-marker tfv3-marker-prior"
        if int(event.get("age_sessions") or 0)
        else "tfv3-marker"
    )
    marker_x = signal_index * slot + .7
    elements.append(
        f'<rect class="{marker_class}" x="{marker_x:.2f}" y="1" '
        f'width="{max(1.0, slot - 1.4):.2f}" height="{height - 2:g}" rx="2">'
        f'<title>{escape(signal_date)} 意圖轉折</title></rect>'
    )
    for index, (row, value) in enumerate(zip(trend, values)):
        if abs(value) <= 1e-10:
            continue
        bar_height = max(1.4, abs(value) / scale * 15.5)
        x = index * slot + (slot - bar_width) / 2
        y = center - bar_height if value > 0 else center
        css = "tfv3-buybar" if value > 0 else "tfv3-sellbar"
        date = escape(str(row.get("date") or "")[-5:].replace("-", "/"))
        elements.append(
            f'<rect class="{css}" x="{x:.2f}" y="{y:.2f}" '
            f'width="{bar_width:.2f}" height="{bar_height:.2f}" rx="1">'
            f'<title>{date} 主動配置殘差 {value:+.4f}%</title></rect>'
        )
    return (
        '<div class="tfv3-chart-wrap">'
        '<div class="tfv3-chart-head"><span>20日主動配置殘差</span>'
        '<span>白框＝本次轉折依據</span></div>'
        f'<svg class="tfv3-chart" viewBox="0 0 {width:g} {height:g}" '
        f'role="img" aria-label="{escape(str(event.get("name") or ""))} '
        '最近20日主動配置殘差">'
        f'{"".join(elements)}</svg></div>'
    )


def _evidence_rows(event: dict) -> str:
    rows = []
    for item in event.get("evidence") or []:
        ratio = float(item.get("significance_ratio") or 0.0)
        raw_delta = int(item.get("raw_delta_shares") or 0)
        action = str(item.get("action") or "")
        shares = f"{abs(raw_delta):,} 股"
        rows.append(
            '<div class="tfv3-evidence-row">'
            f'<b>{escape(str(item.get("etf_label") or ""))} {escape(action)}</b>'
            f'<span>{shares}・顯著門檻 {ratio:.1f}×</span>'
            "</div>"
        )
    return (
        '<div class="tfv3-evidence">'
        + "".join(rows)
        + "</div>"
        if rows
        else ""
    )


def render_intent_card(event: dict, group: str) -> str:
    stock_id = str(event.get("stock_id") or "")
    identity = escape(str(event.get("name") or stock_id))
    if stock_id:
        identity += f'<span class="tfv3-code">{escape(stock_id)}</span>'
    confirmed = str(event.get("signal_phase") or "new") == "confirmed"
    time_class = "tfv3-time-prior" if confirmed else "tfv3-time-current"
    breadth = int(event.get("consensus_etfs") or event.get("breadth") or 0)
    money = float(event.get("estimated_money_yi") or 0.0)
    money_label = f"約 {money:+.2f} 億（僅供金額感）"
    meta = [
        ("類股", str(event.get("category") or "未分類")),
        ("ETF", str(event.get("etf_label") or "未提供")),
        ("估計主動額", money_label),
    ]
    meta_html = "".join(
        f"<span>{escape(label)}</span><b>{escape(value)}</b>"
        for label, value in meta
    )
    return (
        f'<article class="tfv3-card tfv3-{group}">'
        '<div class="tfv3-top">'
        f'<div class="tfv3-stock">{identity}</div>'
        f'<span class="tfv3-time {time_class}">'
        f'{escape(str(event.get("timing_label") or ""))}</span>'
        "</div>"
        '<div class="tfv3-signal">'
        f'<span>{escape(str(event.get("event_label") or ""))}</span>'
        f'<span class="tfv3-consensus">共識 {breadth}/3</span></div>'
        f'<div class="tfv3-reason">{escape(str(event.get("reason") or ""))}</div>'
        f'<div class="tfv3-meta">{meta_html}</div>'
        f'{_evidence_rows(event)}'
        f'{_sparkline(event)}'
        "</article>"
    )


def render_intent_lane(payload: dict, lane_key: str) -> str:
    if lane_key not in INTENT_LANES:
        raise ValueError(f"Unknown V3 lane: {lane_key}")
    title, note, group = INTENT_LANES[lane_key]
    events = list((payload.get("signals") or {}).get(lane_key) or [])
    new_events = [
        event
        for event in events
        if str(event.get("signal_phase") or "new") == "new"
    ]
    confirmed_events = [
        event
        for event in events
        if str(event.get("signal_phase") or "") == "confirmed"
    ]
    sections = []
    if new_events:
        sections.append(
            '<div class="tfv3-section-head"><span>本交易日新形成</span>'
            f'<span class="tfv3-section-count">{len(new_events)}</span></div>'
            + "".join(render_intent_card(event, group) for event in new_events)
        )
    if confirmed_events:
        sections.append(
            '<div class="tfv3-section-head"><span>前一交易日形成・今日仍確認</span>'
            f'<span class="tfv3-section-count">{len(confirmed_events)}</span></div>'
            + "".join(
                render_intent_card(event, group)
                for event in confirmed_events
            )
        )
    cards = "".join(sections)
    if not cards:
        cards = '<div class="tfv3-empty">本交易日沒有至少 2/3 ETF 的同向共識。</div>'
    return (
        '<section class="tfv3-lane">'
        '<div class="tfv3-lane-head">'
        f'<div class="tfv3-lane-title">{title}</div>'
        f'<div class="tfv3-count">新 {len(new_events)}・續 {len(confirmed_events)}</div>'
        "</div>"
        f'<div class="tfv3-lane-note">{note}</div>'
        f"{cards}</section>"
    )


def render_intent_grid(payload: dict) -> str:
    lanes = "".join(render_intent_lane(payload, key) for key in INTENT_LANES)
    return f'<div class="tfv3-grid">{lanes}</div>'
