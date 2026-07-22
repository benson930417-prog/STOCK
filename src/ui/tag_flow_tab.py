"""Interactive active-ETF category rotation explorer.

This is a pure renderer over ``data/tag_flow.json``.  The headline rotation
story is window-independent and deliberately keeps four questions separate:

* pressure: the fast EWMA of normalized flow;
* background: slower EWMA direction rather than a boxcar window;
* relative magnitude: the category versus its own prior pressure history;
* consensus: how many selected ETFs confirm the current direction.

The 1/5/10/20/... selector changes charts and audit totals only.  It never
changes the phase label, ranking, or headline conclusion.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape
import json
from textwrap import dedent

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.tag_flow_rotation import build_rotation_snapshot, phase_explanation

ETF_LABEL = {"00403A": "403", "00981A": "981", "00991A": "991"}
EPSILON = 1e-6
BUY_COLOR = "#E74C3C"
SELL_COLOR = "#2ECC71"
NEUTRAL_COLOR = "#94A3B8"


def _fmt_ratio(value: float) -> str:
    return f"{value:+.2f}% 規模" if abs(value) > EPSILON else "0.00% 規模"


def _fmt_money(value_twd: float, *, signed: bool = True) -> str:
    value_yi = value_twd / 100_000_000.0
    sign = "+" if signed and value_yi > 0 else ""
    decimals = 2 if abs(value_yi) < 1 else 1
    return f"{sign}{value_yi:.{decimals}f}億"


def _direction_label(value: float) -> str:
    if value > EPSILON:
        return "🔴 加碼"
    if value < -EPSILON:
        return "🟢 減碼"
    return "⚪ 持平"


def _direction_style(
    frame: pd.DataFrame, value_column: str
) -> pd.io.formats.style.Styler:
    """Color every decision column from one numeric direction source."""
    raw_values = frame[value_column].to_dict()
    visible = frame.drop(columns=[value_column])
    emphasized = {
        "方向",
        "區間約買賣",
        "相對力道",
        "最新一日",
    }

    def style_row(row: pd.Series) -> list[str]:
        value = raw_values[row.name]
        color = (
            BUY_COLOR
            if value > EPSILON
            else SELL_COLOR
            if value < -EPSILON
            else NEUTRAL_COLOR
        )
        return [
            f"color: {color}; font-weight: 700;" if column in emphasized else ""
            for column in row.index
        ]

    return visible.style.apply(style_row, axis=1)


def _render_legend() -> None:
    st.markdown(
        f"""
        <div class="tf-legend">
          <span><i style="background:{BUY_COLOR}"></i><b>紅色＝加碼／買進</b></span>
          <span><i style="background:{SELL_COLOR}"></i><b>綠色＝減碼／賣出</b></span>
          <span><i style="background:{NEUTRAL_COLOR}"></i>灰色＝接近持平</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _rotation_board(rows: list[dict]) -> None:
    lanes = [
        ("buy", "🔴 資金進場／延續", "買盤已由多數 ETF 確認"),
        ("transition", "🟠 輪動轉折", "背景與近期壓力正在交接"),
        ("sell", "🟢 近期減碼", "賣方壓力已由多數 ETF 確認"),
        ("neutral", "⚪ 證據不足", "單一 ETF 或方向尚未形成共識"),
    ]
    cards: list[str] = []
    for group, title, note in lanes:
        lane_rows = [row for row in rows if row["phase_group"] == group]
        items: list[str] = []
        for row in lane_rows[:6]:
            percentile = row.get("strength_percentile")
            magnitude = f"自身 P{percentile:.0f}" if percentile is not None else "樣本累積中"
            if row["fast"] > 0:
                breadth = f"{row['buyers']}/{row['etf_count']} ETF 買"
            elif row["fast"] < 0:
                breadth = f"{row['sellers']}/{row['etf_count']} ETF 賣"
            else:
                breadth = "近期中性"
            pending = (
                f"<div class='tf-pending'>轉向待確認：{escape(row['pending_label'])}</div>"
                if row.get("pending_label") else ""
            )
            items.append(
                dedent(
                    f"""
                    <div class="tf-rotation-item tf-rotation-{group}">
                      <div class="tf-rotation-name">{escape(row['category'])}</div>
                      <div class="tf-rotation-phase">{escape(row['phase_label'])}</div>
                      <div class="tf-rotation-meta">{magnitude} · {breadth} · 方向信心 {row['confidence']}</div>
                      {pending}
                    </div>
                    """
                ).strip()
            )
        if not items:
            items.append('<div class="tf-rotation-empty">目前沒有類股落在此階段</div>')
        more = (
            f"<div class='tf-rotation-more'>另有 {len(lane_rows) - 6} 類股，見完整表</div>"
            if len(lane_rows) > 6 else ""
        )
        cards.append(
            dedent(
                f"""
                <div class="tf-rotation-lane">
                  <div class="tf-rotation-title">{title}</div>
                  <div class="tf-rotation-note">{note}</div>
                  {''.join(items)}{more}
                </div>
                """
            ).strip()
        )
    st.markdown(
        '<div class="tf-rotation-grid">' + "".join(cards) + "</div>",
        unsafe_allow_html=True,
    )


def _rotation_table(rows: list[dict]) -> pd.DataFrame:
    display = []
    for row in rows:
        percentile = row.get("strength_percentile")
        pending = f" → 待確認：{row['pending_label']}" if row.get("pending_label") else ""
        if row["fast"] > 0:
            breadth = f"{row['buyers']}/{row['etf_count']} 偏買"
        elif row["fast"] < 0:
            breadth = f"{row['sellers']}/{row['etf_count']} 偏賣"
        else:
            breadth = "中性"
        totals = row["window_totals"]
        display.append(
            {
                "類股": row["category"],
                "一致輪動判斷": row["phase_label"] + pending,
                "近期壓力": f"{row['fast']:+.3f}%",
                "主方向": f"{row['trend']:+.3f}%",
                "背景": f"{row['background']:+.3f}%",
                "相對自身": f"P{percentile:.0f}" if percentile is not None else "樣本累積中",
                "全類股排名": f"{row['cross_section_rank']}/{row['cross_section_total']}",
                "ETF 確認": breadth,
                "方向信心": row["confidence"],
                "3 / 5 / 10 / 20日證據": (
                    f"{totals['3']:+.2f} / {totals['5']:+.2f} / "
                    f"{totals['10']:+.2f} / {totals['20']:+.2f}%"
                ),
            }
        )
    return pd.DataFrame(display)


def _load(data_dir):
    path = data_dir / "tag_flow.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken cache should not crash the app
        return None


def _shared_dates(data: dict, selected_etfs: list[str]) -> list[str]:
    by_etf = data.get("dates", {}).get("by_etf", {})
    available = [set(by_etf.get(etf, [])) for etf in selected_etfs]
    if not available:
        return []
    return sorted(set.intersection(*available))


def _aggregate(
    data: dict,
    selected_etfs: list[str],
    selected_dates: list[str],
) -> tuple[list[dict], list[dict]]:
    """Aggregate observations by the stock's single 類股 classification."""
    etf_set = set(selected_etfs)
    date_set = set(selected_dates)
    n_etfs = max(1, len(selected_etfs))
    latest = selected_dates[-1]

    themes: dict[str, dict] = {}
    stocks: dict[str, dict] = {}
    latest_fund_sizes: dict[str, float] = {}

    for observation in data.get("observations", []):
        etf = observation.get("etf")
        date = observation.get("date")
        if etf not in etf_set or date not in date_set:
            continue
        observation_fund_size = float(observation.get("fund_size") or 0.0)
        if observation_fund_size:
            latest_fund_sizes[etf] = observation_fund_size

        for move in observation.get("stocks", []):
            stock_id = str(move.get("id", ""))
            flow = float(move.get("flow", 0.0))
            money_twd = float(move.get("money_twd") or 0.0)
            stock = stocks.setdefault(
                stock_id,
                {
                    "id": stock_id,
                    "name": move.get("name", stock_id),
                    "category": move.get("category") or "未分類",
                    "group": move.get("group") or "",
                    "concepts": move.get("concepts") or [],
                    "flow_sum": 0.0,
                    "money_sum": 0.0,
                    "flow_by_etf": defaultdict(float),
                    "money_by_etf": defaultdict(float),
                    "flow_by_date": defaultdict(float),
                    "money_by_date": defaultdict(float),
                    "latest_by_etf": defaultdict(float),
                    "latest_money_by_etf": defaultdict(float),
                    "max_percentile": None,
                    "notable_days": 0,
                    "outlier_days": 0,
                },
            )
            stock["flow_sum"] += flow
            stock["money_sum"] += money_twd
            stock["flow_by_etf"][etf] += flow
            stock["money_by_etf"][etf] += money_twd
            stock["flow_by_date"][date] += flow
            stock["money_by_date"][date] += money_twd
            if date == latest:
                stock["latest_by_etf"][etf] += flow
                stock["latest_money_by_etf"][etf] += money_twd
            percentile = move.get("percentile")
            if percentile is not None:
                stock["max_percentile"] = max(
                    stock["max_percentile"] or 0.0, float(percentile)
                )
                if percentile >= 80:
                    stock["notable_days"] += 1
                if percentile >= 95:
                    stock["outlier_days"] += 1

            theme_name = move.get("category") or "未分類"
            theme = themes.setdefault(
                theme_name,
                {
                    "theme": theme_name,
                    "flow_sum": 0.0,
                    "money_sum": 0.0,
                    "flow_by_etf": defaultdict(float),
                    "money_by_etf": defaultdict(float),
                    "flow_by_date": defaultdict(float),
                    "money_by_date": defaultdict(float),
                    "daily_by_etf": defaultdict(lambda: defaultdict(float)),
                    "daily_money_by_etf": defaultdict(lambda: defaultdict(float)),
                    "stock_flows": defaultdict(float),
                    "stock_money": defaultdict(float),
                },
            )
            theme["flow_sum"] += flow
            theme["money_sum"] += money_twd
            theme["flow_by_etf"][etf] += flow
            theme["money_by_etf"][etf] += money_twd
            theme["flow_by_date"][date] += flow
            theme["money_by_date"][date] += money_twd
            theme["daily_by_etf"][etf][date] += flow
            theme["daily_money_by_etf"][etf][date] += money_twd
            theme["stock_flows"][stock_id] += flow
            theme["stock_money"][stock_id] += money_twd

    stock_rows: list[dict] = []
    for stock in stocks.values():
        flow_by_etf = dict(stock["flow_by_etf"])
        flow_by_date = dict(stock["flow_by_date"])
        net = stock["flow_sum"] / n_etfs
        latest_flow = sum(stock["latest_by_etf"].values()) / n_etfs
        stock.update(
            {
                "flow": net,
                "money": stock["money_sum"],
                "latest": latest_flow,
                "latest_money": sum(stock["latest_money_by_etf"].values()),
                "flow_by_etf": flow_by_etf,
                "flow_by_date": flow_by_date,
                "buyers": sum(value > EPSILON for value in flow_by_etf.values()),
                "sellers": sum(value < -EPSILON for value in flow_by_etf.values()),
                "buy_days": sum(value > EPSILON for value in flow_by_date.values()),
                "sell_days": sum(value < -EPSILON for value in flow_by_date.values()),
            }
        )
        stock_rows.append(stock)
    stock_rows.sort(key=lambda row: -abs(row["flow"]))

    theme_rows: list[dict] = []
    for theme in themes.values():
        flow_by_etf = dict(theme["flow_by_etf"])
        daily_raw = dict(theme["flow_by_date"])
        net = theme["flow_sum"] / n_etfs
        daily = {date: daily_raw.get(date, 0.0) / n_etfs for date in selected_dates}
        direction_days = sum(
            value > EPSILON if net >= 0 else value < -EPSILON
            for value in daily.values()
        )
        top_stock_ids = sorted(
            theme["stock_flows"],
            key=lambda sid: -abs(theme["stock_flows"][sid]),
        )[:5]
        stock_names = {row["id"]: row["name"] for row in stock_rows}
        theme_rows.append(
            {
                "theme": theme["theme"],
                "flow": net,
                "money": theme["money_sum"],
                "latest": daily.get(latest, 0.0),
                "latest_money": theme["money_by_date"].get(latest, 0.0),
                "flow_by_etf": flow_by_etf,
                "money_by_etf": dict(theme["money_by_etf"]),
                "daily": daily,
                "daily_by_etf": {
                    etf: dict(values) for etf, values in theme["daily_by_etf"].items()
                },
                "daily_money_by_etf": {
                    etf: dict(values)
                    for etf, values in theme["daily_money_by_etf"].items()
                },
                "fund_size_by_etf": dict(latest_fund_sizes),
                "buyers": sum(value > EPSILON for value in flow_by_etf.values()),
                "sellers": sum(value < -EPSILON for value in flow_by_etf.values()),
                "buy_days": sum(value > EPSILON for value in daily.values()),
                "sell_days": sum(value < -EPSILON for value in daily.values()),
                "direction_days": direction_days,
                "top_stocks": [
                    {
                        "id": stock_id,
                        "name": stock_names.get(stock_id, stock_id),
                        "flow": theme["stock_flows"][stock_id] / n_etfs,
                        "money": theme["stock_money"][stock_id],
                    }
                    for stock_id in top_stock_ids
                ],
            }
        )
    theme_rows.sort(key=lambda row: -abs(row["flow"]))
    return theme_rows, stock_rows


def _top_sided(rows: list[dict], per_side: int = 7) -> list[dict]:
    positive = sorted(
        (row for row in rows if row["flow"] > EPSILON),
        key=lambda row: -row["flow"],
    )[:per_side]
    negative = sorted(
        (row for row in rows if row["flow"] < -EPSILON),
        key=lambda row: row["flow"],
    )[:per_side]
    selected = positive + negative
    if not selected:
        selected = rows[: per_side * 2]
    return sorted(selected, key=lambda row: row["flow"])


def _theme_chart(
    rows: list[dict],
    n_dates: int,
    n_etfs: int,
    profit_color: str,
    loss_color: str,
):
    shown = _top_sided(rows)
    if not shown:
        st.info("此範圍沒有可顯示的題材交易。")
        return

    labels = [row["theme"] for row in shown]
    values = [row["flow"] for row in shown]
    colors = [profit_color if value > 0 else loss_color for value in values]
    text = []
    custom = []
    for row in shown:
        direction = "加碼" if row["flow"] >= 0 else "減碼"
        text.append(
            f"{direction} 約 {_fmt_money(row['money'])} · 力道 {row['flow']:+.2f}%"
        )
        drivers = "、".join(
            f"{stock['name']} 約{_fmt_money(stock['money'])}"
            for stock in row["top_stocks"][:4]
        )
        custom.append(
            [
                row["latest"],
                row["money"] / 100_000_000.0,
                row["latest_money"] / 100_000_000.0,
                row["buy_days"],
                row["sell_days"],
                row["buyers"],
                row["sellers"],
                drivers,
            ]
        )

    cap = max(max(abs(value) for value in values), 0.1)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=colors,
            showlegend=False,
            text=text,
            textposition="outside",
            cliponaxis=False,
            customdata=custom,
            hovertemplate=(
                "<b>%{y}</b><br>區間約 %{customdata[1]:+.2f} 億"
                "<br>相對力道 %{x:+.2f}%（已按 ETF 大小調整）"
                "<br>最新日約 %{customdata[2]:+.2f} 億 / 力道 %{customdata[0]:+.2f}%"
                "<br>偏買 / 偏賣：%{customdata[3]} / %{customdata[4]} 日"
                "<br>ETF 淨買 / 淨賣：%{customdata[5]} / %{customdata[6]}"
                "<br>主要個股：%{customdata[7]}<extra></extra>"
            ),
        )
    )
    if n_dates > 1:
        fig.add_trace(
            go.Scatter(
                x=[row["latest"] for row in shown],
                y=labels,
                mode="markers",
                name="最新一日",
                marker={
                    "symbol": "diamond-open",
                    "size": 9,
                    "color": [profit_color if row["latest"] >= 0 else loss_color for row in shown],
                    "line": {"width": 2},
                },
                customdata=[[row["latest_money"] / 100_000_000.0] for row in shown],
                hovertemplate=(
                    "<b>%{y}</b><br>最新日約 %{customdata[0]:+.2f} 億"
                    "<br>相對力道 %{x:+.2f}%<extra></extra>"
                ),
            )
        )
    fig.add_vline(x=0, line_width=1, line_color="rgba(128,128,128,0.55)")
    fig.update_layout(
        template="streamlit",
        height=max(390, 31 * len(shown) + 120),
        margin={"l": 8, "r": 205, "t": 28, "b": 58},
        xaxis={
            "title": "← 減碼｜相對力道（已按 ETF 大小調整）｜加碼 →",
            "range": [-cap * 1.42, cap * 1.42],
            "zeroline": False,
        },
        yaxis={"categoryorder": "array", "categoryarray": labels},
        legend={"orientation": "h", "y": 1.05, "x": 1, "xanchor": "right"},
        showlegend=n_dates > 1,
        hoverlabel={"align": "left"},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _timeline_chart(
    theme: dict,
    dates: list[str],
    selected_etfs: list[str],
    profit_color: str,
    loss_color: str,
):
    n_etfs = len(selected_etfs)
    daily_ratio = [
        sum(theme["daily_by_etf"].get(etf, {}).get(date, 0.0) for etf in selected_etfs)
        / n_etfs
        for date in dates
    ]
    daily_money_yi = [
        sum(
            theme["daily_money_by_etf"].get(etf, {}).get(date, 0.0)
            for etf in selected_etfs
        )
        / 100_000_000.0
        for date in dates
    ]
    cumulative = []
    running = 0.0
    for value in daily_ratio:
        running += value
        cumulative.append(running)

    colors = [profit_color if value >= 0 else loss_color for value in daily_money_yi]
    line_color = profit_color if cumulative[-1] >= 0 else loss_color
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=dates,
            y=daily_money_yi,
            name="每日估計買賣（億元）",
            marker_color=colors,
            opacity=0.58,
            hovertemplate="%{x}<br>當日約 %{y:+.2f} 億<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=cumulative,
            name="累積相對力道",
            mode="lines+markers",
            line={"color": line_color, "width": 3},
            marker={"size": 6},
            yaxis="y2",
            hovertemplate="%{x}<br>累積相對力道 %{y:+.2f}%<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_width=1, line_color="rgba(128,128,128,0.45)")
    fig.update_layout(
        template="streamlit",
        height=350,
        margin={"l": 8, "r": 18, "t": 28, "b": 36},
        hovermode="x unified",
        barmode="relative",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        xaxis={"type": "category", "tickangle": -35 if len(dates) > 12 else 0},
        yaxis={"title": "每日估計買賣（億元）", "zeroline": False},
        yaxis2={
            "title": "累積相對力道（%）",
            "overlaying": "y",
            "side": "right",
            "zeroline": False,
        },
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _etf_breakdown(theme: dict, dates: list[str], selected_etfs: list[str]) -> pd.DataFrame:
    rows = []
    for etf in selected_etfs:
        daily = theme["daily_by_etf"].get(etf, {})
        daily_money = theme["daily_money_by_etf"].get(etf, {})
        values = [daily.get(date, 0.0) for date in dates]
        money_values = [daily_money.get(date, 0.0) for date in dates]
        total_flow = sum(values)
        rows.append(
            {
                "ETF": ETF_LABEL.get(etf, etf),
                "方向": _direction_label(total_flow),
                "區間約買賣": _fmt_money(sum(money_values)),
                "相對力道": _fmt_ratio(total_flow),
                "最新一日": _fmt_money(money_values[-1]),
                "最新基金規模": _fmt_money(
                    theme["fund_size_by_etf"].get(etf, 0.0), signed=False
                ),
                "偏買日": sum(value > EPSILON for value in values),
                "偏賣日": sum(value < -EPSILON for value in values),
                "_direction": total_flow,
            }
        )
    return pd.DataFrame(rows)


def _stock_table(
    rows: list[dict],
    n_dates: int,
    n_etfs: int,
    stock_ids: set[str] | None = None,
    limit: int = 30,
):
    if stock_ids is not None:
        rows = [row for row in rows if row["id"] in stock_ids]
    if not rows:
        st.info("此條件下沒有個股交易。")
        return

    display_rows = []
    for row in rows[:limit]:
        aligned_days = row["buy_days"] if row["flow"] >= 0 else row["sell_days"]
        consensus = f"{row['buyers']}買 / {row['sellers']}賣"
        percentile = (
            f"P{row['max_percentile']:.0f}" if row["max_percentile"] is not None else "樣本不足"
        )
        display_rows.append(
            {
                "個股": f"{row['name']} · {row['id']}",
                "概念（附註）": "、".join(row["concepts"][:3]) or "—",
                "產業": row["category"],
                "方向": _direction_label(row["flow"]),
                "區間約買賣": _fmt_money(row["money"]),
                "相對力道": _fmt_ratio(row["flow"]),
                "最新一日": _fmt_money(row["latest_money"]),
                "同向日": f"{aligned_days}/{n_dates}",
                "ETF 共識": consensus if n_etfs > 1 else "—",
                "最大單日": percentile,
                "_direction": row["flow"],
            }
        )
    display_frame = pd.DataFrame(display_rows)
    st.dataframe(
        _direction_style(display_frame, "_direction"),
        use_container_width=True,
        hide_index=True,
        column_config={
            "個股": st.column_config.TextColumn(width="medium"),
            "概念（附註）": st.column_config.TextColumn(width="large"),
        },
    )


def render_tag_flow_tab(
    *,
    lang=None,
    T=None,
    DATA_DIR=None,
    PROFIT_COLOR="#E74C3C",
    LOSS_COLOR="#2ECC71",
    **kwargs,
):
    # This view intentionally follows the Taiwan-market convention regardless of
    # the dashboard's optional Western color toggle: red is buying, green is selling.
    PROFIT_COLOR = BUY_COLOR
    LOSS_COLOR = SELL_COLOR
    st.markdown(
        """
        <style>
        .tf-legend {display:flex; flex-wrap:wrap; gap:.65rem 1.25rem; align-items:center;
          padding:.7rem .9rem; margin:.3rem 0 1rem; border:1px solid rgba(148,163,184,.24);
          border-radius:.7rem; background:rgba(148,163,184,.06); font-size:.92rem}
        .tf-legend span {display:inline-flex; align-items:center; gap:.42rem}
        .tf-legend i {width:.7rem; height:.7rem; border-radius:50%; display:inline-block}
        .tf-summary-grid {display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
          gap:.8rem; margin:.35rem 0 1.25rem}
        .tf-summary {padding:1rem 1.05rem; border-radius:.8rem; border-left:5px solid;
          background:rgba(148,163,184,.06)}
        .tf-buy {border-color:#E74C3C; background:rgba(231,76,60,.10)}
        .tf-sell {border-color:#2ECC71; background:rgba(46,204,113,.10)}
        .tf-kicker {font-size:.82rem; font-weight:700; opacity:.82; margin-bottom:.2rem}
        .tf-theme {font-size:1.12rem; font-weight:750}
        .tf-money {font-size:1.5rem; font-weight:800; line-height:1.35}
        .tf-buy .tf-money {color:#E74C3C}.tf-sell .tf-money {color:#2ECC71}
        .tf-detail {font-size:.84rem; opacity:.82; margin-top:.18rem}
        .tf-rotation-grid {display:grid; grid-template-columns:repeat(4,minmax(220px,1fr));
          gap:.8rem; margin:.5rem 0 1.1rem}
        .tf-rotation-lane {padding:.9rem; border:1px solid rgba(148,163,184,.22);
          border-radius:.85rem; background:rgba(148,163,184,.045)}
        .tf-rotation-title {font-size:1rem; font-weight:800}
        .tf-rotation-note {font-size:.76rem; opacity:.68; margin:.12rem 0 .7rem}
        .tf-rotation-item {padding:.68rem .72rem; margin:.48rem 0; border-radius:.65rem;
          border-left:4px solid #94A3B8; background:rgba(15,23,42,.35)}
        .tf-rotation-buy {border-color:#E74C3C}.tf-rotation-sell {border-color:#2ECC71}
        .tf-rotation-transition {border-color:#F59E0B}.tf-rotation-neutral {border-color:#94A3B8}
        .tf-rotation-name {font-weight:800; font-size:.98rem}
        .tf-rotation-phase {font-size:.83rem; margin-top:.12rem}
        .tf-rotation-meta,.tf-pending,.tf-rotation-more,.tf-rotation-empty {
          font-size:.73rem; opacity:.72; margin-top:.2rem}
        .tf-pending {color:#F59E0B; opacity:1}
        @media (max-width:1100px){.tf-rotation-grid{grid-template-columns:repeat(2,minmax(220px,1fr))}}
        @media (max-width:650px){.tf-rotation-grid{grid-template-columns:1fr}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.subheader("主動 ETF 題材流向")
    st.caption(
        "先看紅色加碼、綠色減碼；億元看實際金額感，相對力道用來公平比較不同大小的 ETF。"
        "所有題材統計只使用單一類股分類。"
    )
    _render_legend()

    data = _load(DATA_DIR)
    if not data:
        st.warning(
            "尚無題材流向資料。請執行 `python scripts/build_stock_tags.py` 與 "
            "`python scripts/build_tag_flow.py`。"
        )
        return
    if data.get("schema_version") != 2:
        st.warning("題材資料仍是舊格式，請先執行 `python scripts/build_tag_flow.py` 更新。")
        return

    control_a, control_b = st.columns([1.1, 1.4])
    with control_a:
        selected_etfs = st.multiselect(
            "選擇 ETF",
            data.get("etfs", []),
            default=data.get("etfs", []),
            format_func=lambda etf: f"{ETF_LABEL.get(etf, etf)}（{etf}）",
            key="tag_flow_etfs",
        )
    with control_b:
        window = st.radio(
            "圖表顯示期間（不影響輪動故事）",
            ["1日", "5日", "10日", "20日", "60日", "120日", "240日", "全部", "自訂"],
            index=2,
            horizontal=True,
            key="tag_flow_window",
        )

    if not selected_etfs:
        st.info("請至少選擇一檔 ETF。")
        return
    available_dates = _shared_dates(data, selected_etfs)
    if not available_dates:
        st.info("所選 ETF 沒有共同可比較日期。")
        return
    if len(available_dates) < 240:
        st.caption(
            f"目前所選 ETF 只有 {len(available_dates)} 個共同交易日；"
            "較長圖表會使用全部可用資料。輪動故事始終使用全部共同歷史，不受這個按鈕影響。"
        )

    try:
        rotation = build_rotation_snapshot(data, selected_etfs, chart_days=10)
    except ValueError as exc:
        st.info(f"輪動資料仍在累積：{exc}")
        return
    rotation_rows = rotation["rows"]
    rotation_by_category = {row["category"]: row for row in rotation_rows}

    if window == "自訂":
        default_start = available_dates[max(0, len(available_dates) - 20)]
        start, end = st.select_slider(
            "自訂交易日範圍",
            options=available_dates,
            value=(default_start, available_dates[-1]),
            key="tag_flow_custom_range",
        )
        start_index, end_index = available_dates.index(start), available_dates.index(end)
        selected_dates = available_dates[start_index : end_index + 1]
    else:
        counts = {
            "1日": 1,
            "5日": 5,
            "10日": 10,
            "20日": 20,
            "60日": 60,
            "120日": 120,
            "240日": 240,
            "全部": len(available_dates),
        }
        selected_dates = available_dates[-min(counts[window], len(available_dates)) :]

    interval_theme_rows, stock_rows = _aggregate(data, selected_etfs, selected_dates)
    full_theme_rows, _ = _aggregate(data, selected_etfs, available_dates)
    full_theme_by_name = {row["theme"]: row for row in full_theme_rows}
    n_dates = len(selected_dates)
    n_etfs = len(selected_etfs)
    date_label = (
        selected_dates[-1]
        if n_dates == 1
        else f"{selected_dates[0]} → {selected_dates[-1]}"
    )
    st.caption(
        f"圖表：{date_label} · {n_dates} 個共同交易日｜"
        f"輪動判斷：截至 {rotation['as_of']}，使用全部 {rotation['history_sessions']} 個共同交易日｜"
        f"資料產生 {data.get('generated', '—')}"
    )

    st.markdown("#### ① 今日類股輪動階段")
    st.caption(
        "這套判斷不採固定區間：3 日 EWMA 看近期壓力、10 日 EWMA 看主方向、20 日 EWMA 看背景；"
        "強度和本類股自己的歷史比較，方向需 ETF 廣度，轉換需連續 2 個交易日確認。"
        "切換上方圖表期間不會改變這裡。"
    )
    _rotation_board(rotation_rows)
    with st.expander("查看完整輪動判斷與 3／5／10／20 日證據"):
        st.dataframe(_rotation_table(rotation_rows), use_container_width=True, hide_index=True)
        st.caption(
            "3／5／10／20 日只是查證數字，不參與階段命名；全類股排名按今日平滑後的近期壓力比較。"
        )

    if rotation_rows:
        st.divider()
        st.markdown("#### ② 點一個類股看趨勢")
        theme_names = [
            row["category"] for row in rotation_rows
            if row["category"] in full_theme_by_name
        ]
        selected_theme_name = st.selectbox(
            "選擇類股",
            theme_names,
            key="tag_flow_theme_detail",
        )
        selected_theme = full_theme_by_name[selected_theme_name]
        selected_rotation = rotation_by_category[selected_theme_name]
        st.markdown(
            f"**{selected_rotation['phase_label']}**　｜　"
            f"{phase_explanation(selected_rotation)}"
        )
        _timeline_chart(
            selected_theme,
            selected_dates,
            selected_etfs,
            PROFIT_COLOR,
            LOSS_COLOR,
        )
        st.caption(
            "長條：所選圖表期間每天估計買賣億元（紅買、綠賣）｜"
            "折線：所選期間累積相對力道。這張圖會縮放，但上方輪動故事不變。"
        )
        breakdown = _etf_breakdown(selected_theme, selected_dates, selected_etfs)
        st.dataframe(
            _direction_style(breakdown, "_direction"),
            use_container_width=True,
            hide_index=True,
        )

        with st.expander("③ 查看所選期間類股排行（只供查證）"):
            st.caption(
                "這裡刻意保留區間加總，讓你查證窗口差異；它不再產生『主線／轉弱』結論。"
            )
            _theme_chart(
                interval_theme_rows, n_dates, n_etfs, PROFIT_COLOR, LOSS_COLOR
            )

        st.markdown("#### ④ 所選類股的個股來源")
        contributing_ids = {stock["id"] for stock in selected_theme["top_stocks"]}
        _stock_table(stock_rows, n_dates, n_etfs, contributing_ids, limit=12)

    st.divider()
    st.markdown("#### ⑤ 所選圖表期間的全部個股明細")
    st.caption("這一區是區間查證資料，會隨圖表期間改變，不代表上方輪動判斷。")
    stock_filter = st.radio(
        "個股篩選",
        ["全部", "淨加碼", "淨減碼", "ETF 共識", "異常單日"],
        horizontal=True,
        key="tag_flow_stock_filter",
    )
    filtered_stocks = stock_rows
    if stock_filter == "淨加碼":
        filtered_stocks = [row for row in stock_rows if row["flow"] > EPSILON]
    elif stock_filter == "淨減碼":
        filtered_stocks = [row for row in stock_rows if row["flow"] < -EPSILON]
    elif stock_filter == "ETF 共識":
        filtered_stocks = [
            row
            for row in stock_rows
            if max(row["buyers"], row["sellers"]) >= min(2, n_etfs)
        ]
    elif stock_filter == "異常單日":
        filtered_stocks = [row for row in stock_rows if row["outlier_days"] > 0]
    _stock_table(filtered_stocks, n_dates, n_etfs)

    with st.expander("怎麼讀這些數字"):
        st.markdown(
            """
- **輪動故事**：不使用 5／10／20 日區間加總下結論。近期壓力、主方向、背景分別使用半衰期 3／10／20 個交易日的 EWMA；舊交易會逐漸淡出，不會在窗口邊界整筆消失。
- **相對自身 P 值**：今日平滑後壓力相對同一類股過去壓力的經驗百分位。P80 表示目前壓力幅度大於自身過去約 80% 的觀察，不是報酬率，也不是拿大型類股和小型類股硬比。
- **輪動階段確認**：方向至少需要兩檔 ETF 同向；階段轉換需要連續兩個共同交易日。單一 ETF 的一次換股只會顯示證據不足或轉向待確認。
- **圖表顯示期間**：只改變趨勢圖、區間億元與個股查證表。切換 5／10／20 日不會改變輪動階段、排名或 LINE 結論。
- **約買賣（億元）**：依張數變化、持股權重與當日基金規模反推的台幣金額，三檔 ETF 直接加總。因揭露權重與基金規模有四捨五入，所以是估計值，適合建立金額感、不適合單獨比較誰更積極。
- **相對力道（% 規模）**：先把每檔 ETF 的買賣金額除以自己的基金規模，再對所選 ETF 取平均。這是圖表排序依據，因此 981 規模較大不會自動取得較高排名。它不是報酬率，也不會把持股上漲造成的權重增加算成買進。
- **同向日**：題材每日淨流向與區間總方向相同的交易日數；大數字但只有一天同向，通常是單次換股，不是持續布局。
- **ETF 共識**：每檔 ETF 在整個區間的淨方向。`3買 / 0賣` 比單一 ETF 買進更有廣度，但仍不是投資建議。
- **最大單日 P 值**：該筆交易相對同一 ETF 之前 20 個交易日出手大小的經驗百分位；P95 代表比先前約 95% 的交易更大。每一天只使用當時已知的歷史，不偷看未來。
- **類股是唯一解讀層**：所有排名、圖表、趨勢與共識只按每檔股票的單一類股加總。
- 日期只使用所選 ETF **共同有資料**的交易日，避免把缺資料誤當成零交易。
            """
        )
