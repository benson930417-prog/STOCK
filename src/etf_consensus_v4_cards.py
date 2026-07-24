"""Shared V4 consensus cards for Streamlit and cached LINE images."""
from __future__ import annotations

from html import escape

from src.etf_intent_v3 import ETF_LABEL


V4_LANES = {
    "watching": (
        "🟡 單一 ETF 觀察",
        "只收高資訊前兆；分數再高也不等於共識",
        "watch",
    ),
    "buying": (
        "🔴 買方共識",
        "至少兩檔 ETF 各自顯著買進，且訊號在 3 個交易日內重疊",
        "buy",
    ),
    "selling": (
        "🟢 賣方共識",
        "至少兩檔 ETF 各自顯著賣出，且訊號在 3 個交易日內重疊",
        "sell",
    ),
}

V4_CSS = """
.tfv4-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:1rem;align-items:start;margin:.7rem 0 1rem}
.tfv4-lane {border:1px solid rgba(148,163,184,.24);border-radius:1rem;
  padding:1rem;background:rgba(15,23,42,.34);min-width:0;
  --accent:148,163,184}
.tfv4-lane-watch {--accent:242,174,50;border-color:rgba(242,174,50,.34);
  background:linear-gradient(180deg,rgba(242,174,50,.055),rgba(15,23,42,.38) 9rem)}
.tfv4-lane-buy {--accent:231,91,78;border-color:rgba(231,91,78,.34);
  background:linear-gradient(180deg,rgba(231,91,78,.055),rgba(15,23,42,.38) 9rem)}
.tfv4-lane-sell {--accent:69,200,121;border-color:rgba(69,200,121,.34);
  background:linear-gradient(180deg,rgba(69,200,121,.055),rgba(15,23,42,.38) 9rem)}
.tfv4-head {display:flex;justify-content:space-between;align-items:center;gap:.6rem}
.tfv4-title {font-weight:900;font-size:1.02rem;color:rgb(var(--accent))}
.tfv4-count {display:inline-flex;align-items:center;justify-content:center;
  min-width:2rem;height:2rem;padding:0 .55rem;border-radius:999px;
  color:rgb(var(--accent));border:1px solid rgba(var(--accent),.35);
  background:rgba(var(--accent),.11);font-size:.74rem;font-weight:850}
.tfv4-note {font-size:.69rem;color:rgba(226,232,240,.68);
  line-height:1.45;margin:.15rem 0 .72rem}
.tfv4-section-label {display:flex;align-items:center;gap:.4rem;
  margin:.68rem .1rem .26rem;color:rgb(var(--accent));font-size:.67rem;
  font-weight:850;letter-spacing:.02em}
.tfv4-section-label:after {content:"";height:1px;flex:1;
  background:linear-gradient(90deg,rgba(var(--accent),.44),transparent)}
.tfv4-card {position:relative;border-radius:.84rem;padding:.9rem .92rem;
  margin:.62rem 0;background:linear-gradient(135deg,rgba(var(--card-accent),.045),#111827 34%);
  border:1px solid rgba(var(--card-accent),.10);
  border-left:4px solid rgb(var(--card-accent));
  box-shadow:0 5px 18px rgba(0,0,0,.12)}
.tfv4-watch {--card-accent:242,174,50}
.tfv4-buy {--card-accent:231,91,78}
.tfv4-sell {--card-accent:69,200,121}
.tfv4-core {border-color:rgba(var(--card-accent),.34);
  box-shadow:0 0 0 1px rgba(var(--card-accent),.08),0 8px 22px rgba(0,0,0,.18)}
.tfv4-top {display:flex;justify-content:space-between;align-items:flex-start;gap:.6rem}
.tfv4-stock {font-size:1rem;font-weight:900;line-height:1.25}
.tfv4-rank {display:inline-flex;align-items:center;justify-content:center;
  min-width:2rem;height:1.45rem;margin-right:.48rem;border-radius:.38rem;
  color:rgb(var(--card-accent));background:rgba(var(--card-accent),.14);
  border:1px solid rgba(var(--card-accent),.34);font-size:.68rem;
  vertical-align:.08rem}
.tfv4-code {font-size:.62rem;opacity:.58;margin-left:.35rem}
.tfv4-badges {display:flex;align-items:center;justify-content:flex-end;
  gap:.28rem;flex-wrap:wrap}
.tfv4-tier {font-size:.58rem;white-space:nowrap;padding:.27rem .46rem;
  border-radius:999px;font-weight:850;color:rgb(var(--card-accent));
  border:1px solid rgba(var(--card-accent),.48);
  background:rgba(var(--card-accent),.13)}
.tfv4-score {font-size:.61rem;white-space:nowrap;padding:.26rem .48rem;
  border:1px solid rgba(var(--card-accent),.28);border-radius:999px;
  color:rgba(241,245,249,.90);background:rgba(var(--card-accent),.07)}
.tfv4-action {font-size:.83rem;font-weight:850;margin:.52rem 0 .22rem}
.tfv4-summary {font-size:.70rem;line-height:1.48;color:rgba(226,232,240,.76)}
.tfv4-core-reason {font-size:.62rem;margin-top:.28rem;color:rgb(var(--card-accent));
  font-weight:760}
.tfv4-meta {display:grid;grid-template-columns:4.25rem minmax(0,1fr);
  gap:.19rem .42rem;margin-top:.55rem;font-size:.68rem;line-height:1.42}
.tfv4-meta span {color:rgba(var(--card-accent),.68)}
.tfv4-meta b {font-weight:730;min-width:0}
.tfv4-points {display:flex;gap:.28rem;flex-wrap:wrap;margin-top:.58rem}
.tfv4-point {font-size:.58rem;color:rgba(241,245,249,.76);
  padding:.19rem .36rem;border-radius:.35rem;
  border:1px solid rgba(var(--card-accent),.12);
  background:rgba(var(--card-accent),.065)}
.tfv4-evidence {margin-top:.55rem;padding:.45rem .52rem;border-radius:.58rem;
  border:1px solid rgba(var(--card-accent),.10);
  background:rgba(var(--card-accent),.055);font-size:.63rem;line-height:1.5}
.tfv4-evidence-row {display:grid;grid-template-columns:auto minmax(0,1fr);
  gap:.05rem .5rem;padding:.08rem 0}
.tfv4-evidence-row b {font-weight:780;color:rgba(248,250,252,.91)}
.tfv4-evidence-row span {text-align:right}
.tfv4-evidence-row small {grid-column:1/-1;text-align:right;
  color:rgba(203,213,225,.67);font-size:.55rem}
.tfv4-charts {border-top:1px solid rgba(var(--card-accent),.20);
  margin-top:.58rem;padding-top:.42rem}
.tfv4-chart-note {display:flex;justify-content:space-between;gap:.5rem;
  font-size:.55rem;color:rgba(203,213,225,.65);margin-bottom:.2rem}
.tfv4-chart-row {display:grid;grid-template-columns:1.8rem minmax(0,1fr);
  gap:.35rem;align-items:center;margin:.12rem 0}
.tfv4-chart-label {font-size:.55rem;font-weight:800;color:rgba(226,232,240,.78)}
.tfv4-chart {display:block;width:100%;height:1.85rem;overflow:visible}
.tfv4-axis {stroke:rgba(148,163,184,.34);stroke-width:1}
.tfv4-buybar {fill:#F06B5E;opacity:.92}
.tfv4-sellbar {fill:#4FD284;opacity:.92}
.tfv4-window-watch {fill:rgba(242,174,50,.24);stroke:rgba(242,174,50,.52)}
.tfv4-window-buy {fill:rgba(231,91,78,.22);stroke:rgba(231,91,78,.50)}
.tfv4-window-sell {fill:rgba(69,200,121,.22);stroke:rgba(69,200,121,.50)}
.tfv4-trigger-watch {fill:rgba(255,211,106,.07);stroke:#FFD36A;stroke-width:1.55}
.tfv4-trigger-buy {fill:rgba(255,138,126,.07);stroke:#FF8A7E;stroke-width:1.55}
.tfv4-trigger-sell {fill:rgba(114,232,161,.07);stroke:#72E8A1;stroke-width:1.55}
.tfv4-muted {opacity:.48}
.tfv4-empty {font-size:.75rem;opacity:.60;padding:1rem .15rem}
.tfv4-priority {border:1px solid rgba(231,91,78,.28);border-radius:1rem;
  padding:.85rem 1rem;margin:.7rem 0 .9rem;
  background:linear-gradient(135deg,rgba(231,91,78,.075),rgba(69,200,121,.045))}
.tfv4-priority-head {display:flex;justify-content:space-between;align-items:center;
  gap:.7rem;font-weight:900}
.tfv4-priority-head span:last-child {font-size:.67rem;color:rgba(226,232,240,.68)}
.tfv4-priority-note {font-size:.66rem;color:rgba(226,232,240,.68);margin:.18rem 0 .55rem}
.tfv4-priority-list {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.38rem}
.tfv4-priority-item {border-radius:.58rem;padding:.45rem .55rem;
  border-left:3px solid rgb(var(--priority-accent));
  background:rgba(var(--priority-accent),.075)}
.tfv4-priority-buy {--priority-accent:231,91,78}
.tfv4-priority-sell {--priority-accent:69,200,121}
.tfv4-priority-item b {display:block;font-size:.72rem}
.tfv4-priority-item span {display:block;font-size:.57rem;
  color:rgba(226,232,240,.68);margin-top:.08rem}
@media (max-width:1000px){.tfv4-grid{grid-template-columns:1fr}}
@media (max-width:700px){.tfv4-priority-list{grid-template-columns:1fr 1fr}}
"""


COMPONENT_LABELS = {
    "independent_etfs": "獨立ETF",
    "joint_persistence": "共同持續",
    "relative_strength": "相對強度",
    "freshness": "新鮮度",
    "horizon_alignment": "3/10/20一致",
    "event_quality": "事件品質",
    "relative_size": "相對大小",
    "repeat_action": "重複動作",
    "latent_second_etf": "第二ETF接近",
}

WATCH_KIND_LABELS = {
    "new_position": "單一 ETF 新建倉",
    "reentry": "單一 ETF 重新建倉",
    "full_exit": "單一 ETF 完整出清",
    "reversal": "單一 ETF 方向反轉",
    "restart": "單一 ETF 沉寂後重啟",
    "consensus_cooling": "原共識失去第二位經理人",
}


def _date_short(value: str) -> str:
    return str(value or "")[5:].replace("-", "/")


def _sparkline(
    rows: list[dict],
    etf_label: str,
    participant: bool,
    group: str,
) -> str:
    rows = list(rows or [])[-20:]
    if not rows:
        return ""
    width = 300.0
    height = 33.0
    center = height / 2
    slot = width / max(1, len(rows))
    bar_width = max(2.0, slot * .54)
    values = [max(-3.0, min(3.0, float(row.get("ratio") or 0.0))) for row in rows]
    significant_indexes = [
        index for index, row in enumerate(rows) if row.get("significant")
    ]
    latest_significant = significant_indexes[-1] if significant_indexes else None
    elements = [
        f'<line class="tfv4-axis" x1="0" y1="{center:g}" '
        f'x2="{width:g}" y2="{center:g}"/>'
    ]
    if participant and latest_significant is not None:
        start = max(0, latest_significant - 2)
        x = start * slot
        window_width = (latest_significant - start + 1) * slot
        elements.append(
            f'<rect class="tfv4-window-{group}" x="{x:.2f}" y="1" '
            f'width="{window_width:.2f}" height="{height - 2:g}" rx="2"/>'
        )
    for index, (row, value) in enumerate(zip(rows, values)):
        if abs(value) <= 1e-10:
            continue
        bar_height = max(1.3, abs(value) / 3.0 * 13.5)
        x = index * slot + (slot - bar_width) / 2
        y = center - bar_height if value > 0 else center
        css = "tfv4-buybar" if value > 0 else "tfv4-sellbar"
        title = (
            f"{_date_short(str(row.get('date') or ''))} "
            f"{float(row.get('active_flow') or 0):+.4f}% ETF規模・"
            f"{abs(float(row.get('normal_action_multiple') or 0)):.1f}×平常單筆"
        )
        elements.append(
            f'<rect class="{css}" x="{x:.2f}" y="{y:.2f}" '
            f'width="{bar_width:.2f}" height="{bar_height:.2f}" rx="1">'
            f"<title>{escape(title)}</title></rect>"
        )
    if participant and latest_significant is not None:
        x = latest_significant * slot + .6
        elements.append(
            f'<rect class="tfv4-trigger-{group}" x="{x:.2f}" y="1" '
            f'width="{max(1.0, slot - 1.2):.2f}" height="{height - 2:g}" rx="2">'
            f"<title>{escape(etf_label)} 最近顯著動作</title></rect>"
        )
    muted = "" if participant else " tfv4-muted"
    return (
        '<div class="tfv4-chart-row">'
        f'<span class="tfv4-chart-label">{escape(etf_label)}</span>'
        f'<svg class="tfv4-chart{muted}" viewBox="0 0 {width:g} {height:g}" '
        f'role="img" aria-label="{escape(etf_label)} 最近20日相對動作">'
        f'{"".join(elements)}</svg></div>'
    )


def _charts(card: dict, group: str) -> str:
    trends = card.get("etf_trends") or {}
    participants = set(card.get("participants") or [])
    rows = "".join(
        _sparkline(
            list(series or []),
            ETF_LABEL.get(str(etf), str(etf)),
            str(etf) in participants,
            group,
        )
        for etf, series in trends.items()
    )
    if not rows:
        return ""
    return (
        '<div class="tfv4-charts">'
        '<div class="tfv4-chart-note"><span>20日｜柱高＝各 ETF 平常單筆倍數</span>'
        '<span>淡帶／亮框＝3日窗／顯著動作</span></div>'
        f"{rows}</div>"
    )


def _evidence(card: dict) -> str:
    rows = []
    for item in card.get("evidence") or []:
        direction = int(item.get("direction") or 0)
        action = "買" if direction > 0 else "賣"
        ratio = abs(float(item.get("normal_action_multiple") or 0.0))
        active_flow = float(item.get("active_flow") or 0.0)
        signal_date = _date_short(str(item.get("signal_date") or ""))
        money_yi = abs(float(item.get("estimated_money_yi") or 0.0))
        normal_money_yi = abs(
            float(item.get("normal_action_money_yi") or 0.0)
        )
        net_10 = float(item.get("net_active_flow_10") or 0.0)
        money_label = f"約{money_yi:.2f}億・" if money_yi else ""
        rows.append(
            '<div class="tfv4-evidence-row">'
            f'<b>{escape(str(item.get("etf_label") or ""))} {action}'
            f'・{escape(signal_date)}</b>'
            f"<span>{money_label}{ratio:.1f}×平常單筆</span>"
            f"<small>10日淨動作 {net_10:+.3f}% ETF規模"
            + (
                f"・該 ETF 平常單筆約 {normal_money_yi:.2f}億"
                if normal_money_yi
                else ""
            )
            + "</small>"
            "</div>"
        )
    return (
        '<div class="tfv4-evidence">' + "".join(rows) + "</div>"
        if rows
        else ""
    )


def render_v4_card(card: dict, group: str) -> str:
    stock_id = str(card.get("stock_id") or "")
    identity = escape(str(card.get("name") or stock_id))
    line_rank = int(card.get("line_rank") or 0)
    if line_rank:
        identity = f'<span class="tfv4-rank">{line_rank:02d}</span>' + identity
    if stock_id:
        identity += f'<span class="tfv4-code">{escape(stock_id)}</span>'
    score = int(card.get("score") or 0)
    score_label = str(card.get("score_label") or "分數")
    decision_tier = str(card.get("decision_tier") or "")
    decision_reason = str(card.get("decision_reason") or "")
    state = str(card.get("state") or "")
    transition = str(card.get("transition") or "")
    if state == "watch":
        summary = WATCH_KIND_LABELS.get(
            str(card.get("watch_kind") or ""), transition
        )
    else:
        summary = (
            f"{len(card.get('participants') or [])}/3 ETF 獨立確認；"
            f"{transition}"
        )
    meta = [
        ("類股", str(card.get("category") or "未分類")),
        ("參與 ETF", str(card.get("etf_label") or "未提供")),
    ]
    if state == "watch":
        meta.extend(
            [
                ("觀察起點", _date_short(str(card.get("first_seen_date") or ""))),
                (
                    "有效倒數",
                    f"無新顯著動作，最多再 {int(card.get('valid_sessions_remaining') or 0)} 個交易日",
                ),
            ]
        )
    else:
        meta.extend(
            [
                ("首次確認", _date_short(str(card.get("confirmed_date") or ""))),
                ("狀態", f"已維持 {int(card.get('state_days') or 0)} 個交易日"),
                (
                    "有效倒數",
                    f"若都無新顯著動作，最多再 {int(card.get('valid_sessions_remaining') or 0)} 個交易日",
                ),
            ]
        )
    meta_html = "".join(
        f"<span>{escape(label)}</span><b>{escape(value)}</b>"
        for label, value in meta
    )
    points = "".join(
        f'<span class="tfv4-point">'
        f'{escape(COMPONENT_LABELS.get(key, key))} {int(value)}</span>'
        for key, value in (card.get("score_components") or {}).items()
    )
    tier_badge = ""
    if decision_tier == "core":
        tier_badge = '<span class="tfv4-tier">核心決策</span>'
    elif decision_tier == "tracking":
        tier_badge = '<span class="tfv4-tier">有效追蹤</span>'
    core_reason = (
        f'<div class="tfv4-core-reason">先看原因｜{escape(decision_reason)}</div>'
        if decision_tier == "core" and decision_reason
        else ""
    )
    card_classes = f"tfv4-card tfv4-{group}"
    if decision_tier == "core":
        card_classes += " tfv4-core"
    return (
        f'<article class="{card_classes}">'
        '<div class="tfv4-top">'
        f'<div class="tfv4-stock">{identity}</div>'
        '<div class="tfv4-badges">'
        f"{tier_badge}"
        f'<span class="tfv4-score">{escape(score_label)} {score}/100</span>'
        "</div>"
        "</div>"
        f'<div class="tfv4-action">{escape(transition)}</div>'
        f'<div class="tfv4-summary">{escape(summary)}</div>'
        f"{core_reason}"
        f'<div class="tfv4-meta">{meta_html}</div>'
        f'<div class="tfv4-points">{points}</div>'
        f"{_evidence(card)}"
        f"{_charts(card, group)}"
        "</article>"
    )


def _render_lane_cards(cards: list[dict], group: str) -> str:
    if not cards:
        return '<div class="tfv4-empty">目前沒有符合此狀態的個股。</div>'
    if group == "watch":
        return "".join(render_v4_card(card, group) for card in cards)
    core = [
        card for card in cards if str(card.get("decision_tier") or "") == "core"
    ]
    tracking = [
        card for card in cards if str(card.get("decision_tier") or "") != "core"
    ]
    sections = []
    if core:
        sections.append(
            '<div class="tfv4-section-label">核心決策｜先看這些</div>'
            + "".join(render_v4_card(card, group) for card in core)
        )
    if tracking:
        sections.append(
            '<div class="tfv4-section-label">有效共識｜持續追蹤</div>'
            + "".join(render_v4_card(card, group) for card in tracking)
        )
    return "".join(sections)


def render_v4_priority_summary(payload: dict) -> str:
    signals = payload.get("signals") or {}
    core = []
    for lane_key, group in (("buying", "buy"), ("selling", "sell")):
        for card in signals.get(lane_key) or []:
            if str(card.get("decision_tier") or "") == "core":
                core.append((group, card))
    core.sort(key=lambda item: int(item[1].get("score") or 0), reverse=True)
    if not core:
        return (
            '<section class="tfv4-priority">'
            '<div class="tfv4-priority-head"><span>核心決策名單</span>'
            "<span>目前 0 檔</span></div>"
            '<div class="tfv4-priority-note">'
            "今天沒有同時通過共識、力道、新鮮度與持續性的個股；不勉強湊名單。"
            "</div></section>"
        )
    items = []
    for group, card in core:
        stock_id = str(card.get("stock_id") or "")
        name = str(card.get("name") or stock_id)
        direction = "優先看買方" if group == "buy" else "優先看賣方"
        items.append(
            f'<div class="tfv4-priority-item tfv4-priority-{group}">'
            f"<b>{escape(name)} {escape(stock_id)}｜{direction}</b>"
            f'<span>{escape(str(card.get("decision_reason") or ""))}'
            f'・{int(card.get("score") or 0)}分</span></div>'
        )
    return (
        '<section class="tfv4-priority">'
        '<div class="tfv4-priority-head"><span>核心決策名單</span>'
        f"<span>{len(core)} 檔先看</span></div>"
        '<div class="tfv4-priority-note">'
        "只有第二位經理人的動作仍夠新、夠大，並已重複確認或剛形成強訊號才進核心；"
        "其餘共識仍保留在下方追蹤，不混成同一優先級。"
        "</div>"
        f'<div class="tfv4-priority-list">{"".join(items)}</div></section>'
    )


def render_v4_lane(
    payload: dict,
    lane_key: str,
    *,
    cards: list[dict] | None = None,
    total_count: int | None = None,
    page_label: str = "",
    title_override: str = "",
    note_override: str = "",
    show_sections: bool = True,
) -> str:
    if lane_key not in V4_LANES:
        raise ValueError(f"Unknown V4 lane: {lane_key}")
    title, note, group = V4_LANES[lane_key]
    cards = (
        list(cards)
        if cards is not None
        else list((payload.get("signals") or {}).get(lane_key) or [])
    )
    body = (
        _render_lane_cards(cards, group)
        if show_sections
        else "".join(render_v4_card(card, group) for card in cards)
    )
    if not body:
        body = '<div class="tfv4-empty">目前沒有符合此狀態的個股。</div>'
    total = len(cards) if total_count is None else int(total_count)
    count_label = str(len(cards))
    if total != len(cards):
        count_label = f"{len(cards)}／{total}"
    title_label = (title_override or title) + (
        f"｜{escape(page_label)}" if page_label else ""
    )
    note_label = note_override or note
    return (
        f'<section class="tfv4-lane tfv4-lane-{group}">'
        '<div class="tfv4-head">'
        f'<div class="tfv4-title">{title_label}</div>'
        f'<div class="tfv4-count">{count_label}</div>'
        "</div>"
        f'<div class="tfv4-note">{note_label}</div>'
        f"{body}</section>"
    )


def render_v4_grid(payload: dict) -> str:
    return (
        '<div class="tfv4-grid">'
        + "".join(render_v4_lane(payload, key) for key in V4_LANES)
        + "</div>"
    )
