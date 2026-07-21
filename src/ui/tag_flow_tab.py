"""主動 ETF 題材流向 tab — what themes the 3 TW active ETFs are buying today.

Pure render: reads data/tag_flow.json (built by scripts/build_tag_flow.py in the
18:30 job) and never touches the network — same discipline as market_pulse_tab.

The signal is ActiveWeight money-flow = (money an ETF traded on a stock) / fund
size × 100 — price-drift free and self-normalising across fund sizes. "加碼 / 大幅
加碼" is judged against each ETF's own trailing-7-session trade-size distribution.

Sections:
  1. 今日 / 5日 題材流向   — which themes got net money (bar), today or 5-day.
  2. 5 日題材熱力圖         — theme × session heatmap: a building trend vs a blip.
  3. 共識個股             — stocks multiple ETFs bought, or 大幅 moves vs baseline.
  4. 個別 ETF 展開         — one ETF's biggest adds / trims, tagged.
"""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ETF_LABEL = {"00403A": "403", "00981A": "981", "00991A": "991"}


def _fmt(v: float) -> str:
    return f"{v:+.2f}%" if v else "0.00%"


def _load(DATA_DIR):
    p = DATA_DIR / "tag_flow.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _tag_bar(tags, pos_color, neg_color, title):
    rows = [t for t in tags if abs(t["flow_total"]) > 1e-6 and t["tag"] != "未分類"][:14]
    if not rows:
        st.info("此區間無明顯題材資金流動。")
        return
    rows = rows[::-1]  # plotly plots bottom-up
    labels = [f'{t["tag"]}' for t in rows]
    vals = [t["flow_total"] for t in rows]
    colors = [pos_color if v > 0 else neg_color for v in vals]
    text = [f'{_fmt(v)} · {t["n_etf"]}檔ETF' for v, t in zip(vals, rows)]
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h",
        marker_color=colors, text=text, textposition="outside",
        hovertext=[
            "  ".join(f'{s["name"]}{_fmt(s["flow"])}' for s in t["stocks"][:5])
            for t in rows
        ],
        hoverinfo="text",
    ))
    fig.update_layout(
        title=title, height=max(340, 26 * len(rows) + 90),
        margin=dict(l=10, r=40, t=48, b=10),
        xaxis_title="淨買賣 佔基金規模 %（三檔 ETF 加總）",
        yaxis=dict(tickfont=dict(size=13)),
        showlegend=False,
    )
    fig.add_vline(x=0, line_width=1, line_color="#888")
    st.plotly_chart(fig, use_container_width=True)


def _heatmap(heat, pos_color, neg_color):
    tags, dates, matrix = heat["tags"], heat["dates"], heat["matrix"]
    if not tags or not dates:
        st.info("熱力圖資料不足。")
        return
    # reverse so the strongest 5-day theme is on top
    tags_r, matrix_r = tags[::-1], matrix[::-1]
    cap = max((abs(v) for row in matrix_r for v in row), default=1.0) or 1.0
    fig = go.Figure(go.Heatmap(
        z=matrix_r, x=[d[5:] for d in dates], y=tags_r,
        zmid=0, zmin=-cap, zmax=cap,
        colorscale=[[0, neg_color], [0.5, "#f5f5f5"], [1, pos_color]],
        text=[[_fmt(v) if abs(v) > 1e-6 else "" for v in row] for row in matrix_r],
        texttemplate="%{text}", textfont=dict(size=11),
        colorbar=dict(title="% 基金"),
        hovertemplate="%{y}<br>%{x}: %{z:+.2f}%<extra></extra>",
    ))
    fig.update_layout(
        height=max(320, 30 * len(tags_r) + 80),
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(tickfont=dict(size=12)),
        xaxis=dict(side="top"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _consensus_table(stocks, etfs):
    rows = []
    for s in stocks:
        big = any("大幅" in m for m in s.get("mag_by_etf", {}).values())
        multi = (s["n_buyers"] >= 2) or (s["n_sellers"] >= 2)
        if not (big or multi):
            continue
        per = "  ".join(
            f'{ETF_LABEL.get(e, e)} {_fmt(f)}' for e, f in s["flow_by_etf"].items()
        )
        rows.append({
            "個股": f'{s["name"]}',
            "題材": s["category"] + (f' · {s["concepts"][0]}' if s.get("concepts") else ""),
            "淨流向": _fmt(s["flow_total"]),
            "強度": s.get("mag", ""),
            "共識": ("🟢×" + str(s["n_buyers"])) if s["n_buyers"] >= s["n_sellers"]
            else ("🔴×" + str(s["n_sellers"])),
            "各ETF": per,
        })
    if not rows:
        st.info("今日無多檔 ETF 共識或大幅加減碼的個股。")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _etf_drill(stocks, etf, pos_color, neg_color):
    rows = []
    for s in stocks:
        f = s["flow_by_etf"].get(etf)
        if not f:
            continue
        rows.append({
            "個股": s["name"],
            "題材": s["category"],
            "動作": s["mag_by_etf"].get(etf, ""),
            "流向": f,
        })
    rows.sort(key=lambda r: -abs(r["流向"]))
    if not rows:
        st.info("此 ETF 於此區間無異動。")
        return
    df = pd.DataFrame(rows[:20])
    df["流向"] = df["流向"].map(_fmt)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_tag_flow_tab(*, lang=None, T=None, DATA_DIR=None,
                        PROFIT_COLOR="#2ECC71", LOSS_COLOR="#E74C3C", **kwargs):
    st.subheader("🏷️ 主動 ETF 題材流向")
    st.caption(
        "追蹤 00403A / 00981A / 00991A 三檔主動 ETF 每日**實際買賣的張數**換算成資金流"
        "（佔基金規模 %，已排除股價漲跌雜訊），並依 cmoney 類股／概念股歸類成題材。"
        "「加碼／大幅加碼」是相對各 ETF 自己近 7 日的平均出手大小判定。"
    )

    data = _load(DATA_DIR)
    if not data:
        st.warning("尚無題材流向資料。請於伺服器執行 "
                   "`python scripts/build_stock_tags.py` 與 "
                   "`python scripts/build_tag_flow.py`（已納入每日 18:30 排程）。")
        return

    d = data["dates"]
    st.caption(f"資料日期：{d['prev']} → **{d['cur']}**（今日）；"
               f"5 日視窗自 {d['base5']} 起。產生時間 {data.get('generated','')}")

    # ---- 1. theme flow bar, today / 5d toggle ----
    win = st.radio("時間區間", ["今日", "近 5 日"], horizontal=True,
                   label_visibility="collapsed")
    block = data["today"] if win == "今日" else data["d5"]
    _tag_bar(block["tags"], PROFIT_COLOR, LOSS_COLOR,
             f"{win}題材資金流向（類股）")

    with st.expander("概念股題材（多標籤，含 cmoney 日均漲跌動能）"):
        _tag_bar(block["concepts"], PROFIT_COLOR, LOSS_COLOR,
                 f"{win}題材資金流向（概念股）")

    st.divider()

    # ---- 2. 5-day heatmap ----
    st.markdown("#### 📊 近 5 日題材熱力圖")
    st.caption("每格為當日該題材的淨資金流（佔基金規模 %，三檔加總）。"
               "連續多日同色＝資金持續進出，單日一格＝可能只是雜訊。")
    _heatmap(data["heatmap"], PROFIT_COLOR, LOSS_COLOR)

    st.divider()

    # ---- 3. consensus stocks ----
    st.markdown("#### 🎯 共識個股（多檔 ETF 同買／同賣 或 大幅加減碼）")
    st.caption("今日訊號。「強度」以各 ETF 近 7 日平均出手大小為基準；"
               "🟢×n 代表 n 檔 ETF 同向買進。")
    _consensus_table(data["today"]["stocks"], data["etfs"])

    st.divider()

    # ---- 4. per-ETF drilldown ----
    st.markdown("#### 🔍 個別 ETF 展開")
    c1, c2 = st.columns([1, 3])
    with c1:
        etf = st.selectbox("ETF", data["etfs"],
                           format_func=lambda e: f'{ETF_LABEL.get(e, e)}（{e}）')
        win2 = st.radio("區間", ["今日", "近 5 日"], key="drill_win")
    block2 = data["today"] if win2 == "今日" else data["d5"]
    with c2:
        b = data["baseline"].get(etf, {})
        if b:
            st.caption(
                f"{ETF_LABEL.get(etf, etf)} 近 7 日平均單筆出手 "
                f"{b.get('mean', 0):.2f}%，加碼門檻(1σ) {b.get('one_sigma', 0):.2f}%，"
                f"大幅門檻(2σ) {b.get('two_sigma', 0):.2f}%（佔基金規模）。"
            )
        _etf_drill(block2["stocks"], etf, PROFIT_COLOR, LOSS_COLOR)
