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
  padding:1rem;background:rgba(15,23,42,.34);min-width:0}
.tfv4-head {display:flex;justify-content:space-between;align-items:center;gap:.6rem}
.tfv4-title {font-weight:900;font-size:1.02rem}
.tfv4-count {display:inline-flex;align-items:center;justify-content:center;
  min-width:2rem;height:2rem;padding:0 .55rem;border-radius:999px;
  background:rgba(148,163,184,.14);font-size:.74rem;font-weight:850}
.tfv4-note {font-size:.69rem;opacity:.64;line-height:1.45;margin:.15rem 0 .72rem}
.tfv4-card {position:relative;border-radius:.84rem;padding:.9rem .92rem;
  margin:.62rem 0;background:#111827;border-left:4px solid transparent;
  box-shadow:0 5px 18px rgba(0,0,0,.12)}
.tfv4-watch {border-left-color:#F2AE32}
.tfv4-buy {border-left-color:#E75B4E}
.tfv4-sell {border-left-color:#45C879}
.tfv4-top {display:flex;justify-content:space-between;align-items:flex-start;gap:.6rem}
.tfv4-stock {font-size:1rem;font-weight:900;line-height:1.25}
.tfv4-code {font-size:.62rem;opacity:.58;margin-left:.35rem}
.tfv4-score {font-size:.61rem;white-space:nowrap;padding:.26rem .48rem;
  border:1px solid rgba(226,232,240,.24);border-radius:999px;
  color:rgba(241,245,249,.86)}
.tfv4-action {font-size:.83rem;font-weight:850;margin:.52rem 0 .22rem}
.tfv4-summary {font-size:.70rem;line-height:1.48;color:rgba(226,232,240,.76)}
.tfv4-meta {display:grid;grid-template-columns:4.25rem minmax(0,1fr);
  gap:.19rem .42rem;margin-top:.55rem;font-size:.68rem;line-height:1.42}
.tfv4-meta span {color:rgba(203,213,225,.56)}
.tfv4-meta b {font-weight:730;min-width:0}
.tfv4-points {display:flex;gap:.28rem;flex-wrap:wrap;margin-top:.58rem}
.tfv4-point {font-size:.58rem;color:rgba(226,232,240,.67);
  padding:.19rem .36rem;border-radius:.35rem;background:rgba(148,163,184,.07)}
.tfv4-evidence {margin-top:.55rem;padding:.45rem .52rem;border-radius:.58rem;
  background:rgba(148,163,184,.055);font-size:.63rem;line-height:1.5}
.tfv4-evidence-row {display:grid;grid-template-columns:auto minmax(0,1fr);
  gap:.05rem .5rem;padding:.08rem 0}
.tfv4-evidence-row b {font-weight:780;color:rgba(248,250,252,.91)}
.tfv4-evidence-row span {text-align:right}
.tfv4-evidence-row small {grid-column:1/-1;text-align:right;
  color:rgba(203,213,225,.55);font-size:.55rem}
.tfv4-charts {border-top:1px solid rgba(148,163,184,.13);
  margin-top:.58rem;padding-top:.42rem}
.tfv4-chart-note {display:flex;justify-content:space-between;gap:.5rem;
  font-size:.55rem;color:rgba(203,213,225,.48);margin-bottom:.2rem}
.tfv4-chart-row {display:grid;grid-template-columns:1.8rem minmax(0,1fr);
  gap:.35rem;align-items:center;margin:.12rem 0}
.tfv4-chart-label {font-size:.55rem;font-weight:800;color:rgba(226,232,240,.65)}
.tfv4-chart {display:block;width:100%;height:1.85rem;overflow:visible}
.tfv4-axis {stroke:rgba(148,163,184,.22);stroke-width:1}
.tfv4-buybar {fill:#E76A5C;opacity:.80}
.tfv4-sellbar {fill:#45C879;opacity:.82}
.tfv4-window {fill:rgba(226,232,240,.045)}
.tfv4-trigger {fill:none;stroke:rgba(248,250,252,.84);stroke-width:1.05}
.tfv4-muted {opacity:.32}
.tfv4-empty {font-size:.75rem;opacity:.60;padding:1rem .15rem}
@media (max-width:1000px){.tfv4-grid{grid-template-columns:1fr}}
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


def _sparkline(rows: list[dict], etf_label: str, participant: bool) -> str:
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
            f'<rect class="tfv4-window" x="{x:.2f}" y="1" '
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
            f'<rect class="tfv4-trigger" x="{x:.2f}" y="1" '
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


def _charts(card: dict) -> str:
    trends = card.get("etf_trends") or {}
    participants = set(card.get("participants") or [])
    rows = "".join(
        _sparkline(
            list(series or []),
            ETF_LABEL.get(str(etf), str(etf)),
            str(etf) in participants,
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
    if stock_id:
        identity += f'<span class="tfv4-code">{escape(stock_id)}</span>'
    score = int(card.get("score") or 0)
    score_label = str(card.get("score_label") or "分數")
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
    return (
        f'<article class="tfv4-card tfv4-{group}">'
        '<div class="tfv4-top">'
        f'<div class="tfv4-stock">{identity}</div>'
        f'<span class="tfv4-score">{escape(score_label)} {score}/100</span>'
        "</div>"
        f'<div class="tfv4-action">{escape(transition)}</div>'
        f'<div class="tfv4-summary">{escape(summary)}</div>'
        f'<div class="tfv4-meta">{meta_html}</div>'
        f'<div class="tfv4-points">{points}</div>'
        f"{_evidence(card)}"
        f"{_charts(card)}"
        "</article>"
    )


def render_v4_lane(payload: dict, lane_key: str) -> str:
    if lane_key not in V4_LANES:
        raise ValueError(f"Unknown V4 lane: {lane_key}")
    title, note, group = V4_LANES[lane_key]
    cards = list((payload.get("signals") or {}).get(lane_key) or [])
    body = "".join(render_v4_card(card, group) for card in cards)
    if not body:
        body = '<div class="tfv4-empty">目前沒有符合此狀態的個股。</div>'
    return (
        '<section class="tfv4-lane">'
        '<div class="tfv4-head">'
        f'<div class="tfv4-title">{title}</div>'
        f'<div class="tfv4-count">{len(cards)}</div>'
        "</div>"
        f'<div class="tfv4-note">{note}</div>'
        f"{body}</section>"
    )


def render_v4_grid(payload: dict) -> str:
    return (
        '<div class="tfv4-grid">'
        + "".join(render_v4_lane(payload, key) for key in V4_LANES)
        + "</div>"
    )
