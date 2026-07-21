"""融資風險 tab — a read-only view of the daily official-data estimate."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.margin_risk import (
    LEGAL_CALL_REFERENCE,
    LEGAL_CURE_REFERENCE,
    load_margin_cache,
    make_snapshot,
    tone_color,
)


def _fmt_change(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1f} 點"


def _filter_range(df: pd.DataFrame, choice: str) -> pd.DataFrame:
    if df.empty or choice == "全部":
        return df
    days = {"1個月": 31, "3個月": 93, "6個月": 186, "1年": 366}.get(choice)
    if not days:
        return df
    cutoff = df["date"].max() - pd.Timedelta(days=days)
    return df[df["date"] >= cutoff]


def _headline_html(snapshot) -> str:
    color = tone_color(snapshot.tone)
    d5 = _fmt_change(snapshot.change_5d)
    return f"""
    <div style="background:linear-gradient(135deg,#111827,#0f172a);border:1px solid #334155;
                border-left:7px solid {color};border-radius:14px;padding:20px 24px;margin:6px 0 18px 0;">
      <div style="font-size:14px;color:#94a3b8;font-weight:700;letter-spacing:.08em;">現在最重要的結論</div>
      <div style="font-size:28px;color:{color};font-weight:900;margin-top:5px;">{snapshot.headline}</div>
      <div style="font-size:16px;color:#cbd5e1;margin-top:8px;line-height:1.55;">
        目前 {snapshot.estimate_pct:.1f}%（{snapshot.status}），近 5 個交易日 {d5}（{snapshot.direction}）。
        這是全市場總量估算，不是任何一個帳戶的追繳線。
      </div>
    </div>
    """


def _metric_html(label: str, value: str, note: str, color: str = "#f8fafc") -> str:
    return f"""
    <div style="background:#111827;border:1px solid #334155;border-radius:12px;padding:16px 18px;min-height:126px;">
      <div style="font-size:14px;color:#94a3b8;font-weight:800;">{label}</div>
      <div style="font-size:27px;color:{color};font-weight:900;margin-top:8px;">{value}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:6px;">{note}</div>
    </div>
    """


def _trend_chart(df: pd.DataFrame, overlay_taiex: bool) -> go.Figure:
    use_secondary = bool(overlay_taiex and df["taiex_close"].notna().any())
    fig = make_subplots(specs=[[{"secondary_y": use_secondary}]])
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["estimate_pct"],
            name="融資擔保估算率",
            mode="lines",
            line={"color": "#38bdf8", "width": 3},
            hovertemplate="%{x|%Y-%m-%d}<br>估算率 %{y:.2f}%<extra></extra>",
        ),
        secondary_y=False,
    )
    if use_secondary:
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["taiex_close"],
                name="加權指數",
                mode="lines",
                line={"color": "#ef4444", "width": 2},
                opacity=0.72,
                hovertemplate="%{x|%Y-%m-%d}<br>加權 %{y:,.0f}<extra></extra>",
            ),
            secondary_y=True,
        )

    # These are legal account-level references, not model decision thresholds.
    fig.add_hline(
        y=LEGAL_CALL_REFERENCE,
        line_dash="dot",
        line_color="#22c55e",
        opacity=0.8,
        annotation_text="130% 法規追繳參考",
        annotation_position="bottom right",
        secondary_y=False,
    )
    fig.add_hline(
        y=LEGAL_CURE_REFERENCE,
        line_dash="dot",
        line_color="#f59e0b",
        opacity=0.65,
        annotation_text="166% 法規補足參考",
        annotation_position="top right",
        secondary_y=False,
    )
    fig.update_layout(
        height=510,
        margin={"l": 16, "r": 18, "t": 35, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,.35)",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    fig.update_xaxes(showgrid=False, rangeslider_visible=len(df) >= 50)
    fig.update_yaxes(title_text="估算率 %", gridcolor="rgba(148,163,184,.15)", secondary_y=False)
    if use_secondary:
        fig.update_yaxes(title_text="加權指數", showgrid=False, secondary_y=True)
    return fig


def _financing_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["financing_balance_billion"],
            name="融資餘額",
            marker_color="#6366f1",
            opacity=0.82,
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} 億元<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        margin={"l": 16, "r": 18, "t": 15, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,.35)",
        showlegend=False,
        yaxis_title="億元",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.15)")
    return fig


def render_margin_risk_tab(*, lang: str, T, DATA_DIR) -> None:
    del lang, T, DATA_DIR  # this Taiwan-market page is intentionally Chinese-first
    st.subheader("融資風險雷達")
    st.caption("TWSE + TPEx 官方每日資料｜分子排除 ETF｜每天 18:30 更新快取")

    df = load_margin_cache()
    if df.empty:
        st.warning("融資風險快取尚未建立。請在伺服器執行：python scripts/update_margin_maintenance.py --backfill-years 1")
        return
    snapshot = make_snapshot(df)
    st.markdown(_headline_html(snapshot), unsafe_allow_html=True)

    k1, k2, k3, k4 = st.columns(4)
    d1_color = "#ef4444" if (snapshot.change_1d or 0) > 0 else "#22c55e" if (snapshot.change_1d or 0) < 0 else "#f8fafc"
    d5_color = "#ef4444" if (snapshot.change_5d or 0) > 0 else "#22c55e" if (snapshot.change_5d or 0) < 0 else "#f8fafc"
    with k1:
        st.markdown(_metric_html("非 ETF 估算率", f"{snapshot.estimate_pct:.1f}%", f"近 1 日 {_fmt_change(snapshot.change_1d)}", d1_color), unsafe_allow_html=True)
    with k2:
        st.markdown(_metric_html("近 5 日變化", _fmt_change(snapshot.change_5d), snapshot.direction, d5_color), unsafe_allow_html=True)
    with k3:
        st.markdown(_metric_html("融資餘額", f"{snapshot.financing_billion:,.0f} 億", "上市 + 上櫃"), unsafe_allow_html=True)
    with k4:
        call_color = "#ef4444" if snapshot.distance_to_call >= 0 else "#22c55e"
        st.markdown(_metric_html("距 130% 參考", f"{snapshot.distance_to_call:+.1f} 點", "非個別帳戶追繳距離", call_color), unsafe_allow_html=True)

    control, overlay_col = st.columns([3, 1])
    with control:
        choice = st.radio(
            "趨勢區間",
            ["1個月", "3個月", "6個月", "1年", "全部", "自訂"],
            index=3,
            horizontal=True,
            key="margin_risk_range",
        )
    with overlay_col:
        overlay = st.checkbox("疊加加權指數", value=True, key="margin_risk_overlay")

    view = df
    if choice == "自訂":
        min_date = df["date"].min().date()
        max_date = df["date"].max().date()
        start_default = max(min_date, (df["date"].max() - pd.Timedelta(days=180)).date())
        selected = st.date_input(
            "自訂日期",
            value=(start_default, max_date),
            min_value=min_date,
            max_value=max_date,
            key="margin_risk_custom_dates",
        )
        if isinstance(selected, (tuple, list)) and len(selected) == 2:
            start, end = pd.Timestamp(selected[0]), pd.Timestamp(selected[1])
            view = df[(df["date"] >= start) & (df["date"] <= end)]
    else:
        view = _filter_range(df, choice)

    if view.empty:
        st.info("這個日期區間沒有交易資料。")
        return
    st.plotly_chart(_trend_chart(view, overlay), use_container_width=True, config={"displaylogo": False})
    st.caption("虛線 130% / 166% 是個別信用帳戶的法規參考；本圖是全市場公開資料估算，兩者不能直接畫等號。")

    left, right = st.columns([1.25, 1])
    with left:
        st.markdown("#### 槓桿有沒有繼續堆高？")
        st.plotly_chart(_financing_chart(view), use_container_width=True, config={"displaylogo": False})
    with right:
        latest = df.iloc[-1]
        percentile = "資料不足" if snapshot.percentile_1y is None else f"第 {snapshot.percentile_1y:.0f} 百分位"
        excluded_etf = latest.get("excluded_etf_collateral_billion")
        excluded_etf_text = f"{float(excluded_etf):,.0f} 億元" if pd.notna(excluded_etf) else "資料累積中"
        st.markdown("#### 今天怎麼讀")
        st.markdown(
            f"""
            - **非 ETF 擔保估算市值：** {snapshot.collateral_billion:,.0f} 億元
            - **已從分子排除的 ETF 市值：** {excluded_etf_text}
            - **融資借款餘額：** {snapshot.financing_billion:,.0f} 億元
            - **近一年位置：** {percentile}
            - **上市估算：** {float(latest['twse_estimate_pct']):.1f}%
            - **上櫃估算：** {float(latest['tpex_estimate_pct']):.1f}%
            - **股票價格覆蓋：** {int(latest['twse_matched'] + latest['tpex_matched']):,} / {int(latest['twse_total'] + latest['tpex_total']):,} 檔
            """
        )

    with st.expander("這個數字怎麼算？為什麼和新聞網站不一定一樣？"):
        st.markdown(
            """
            **公式：** Σ（上市 + 上櫃的**非 ETF**融資張數 × 收盤價）÷（上市 + 上櫃官方融資金額餘額）× 100。

            依你提供的 MacroMicro 說明，ETF 從**分子**的融資股票市值排除；分母仍使用交易所公布的統一融資餘額。
            本頁採用相同原則，並涵蓋 TWSE + TPEx，而不是只看單一市場。

            分子、分母都來自 TWSE / TPEx 每日報表。它很適合觀察**全市場槓桿緩衝的方向與速度**，
            但不包含券商客戶帳戶中的補繳擔保品，也不是交易所公布的「全市場實際帳戶平均」。
            因此本頁不冒充 MacroMicro 或其他資料商的專有序列，也不把 130% 當成這條估算線的精準斷頭點。

            **實務上先看兩件事：**估算率是否快速下滑，以及融資餘額是否仍在升高。兩者同時出現時，
            市場槓桿風險比單看某一天的絕對百分比更值得注意。
            """
        )
        st.caption(f"資料截至 {snapshot.date}；頁面不即時連線，僅讀每日快取。紅色＝改善，綠色＝惡化。")
