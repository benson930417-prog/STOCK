"""Interactive ETF consensus V4 portfolio backtest tab."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.etf_consensus_backtest import BacktestConfig, audit_latest_three_days, run_backtest


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_etf_consensus_backtest_tab(*, DATA_DIR=None, **kwargs):
    st.subheader("ETF 共識回測")
    st.caption("V4 買方共識的無偷看回測｜訊號揭露後，最早於下一個可交易日開盤成交。")
    consensus = _load(DATA_DIR / "etf_consensus_v4.json")
    prices = _load(DATA_DIR / "yuanta_v4_daily_k.json")
    if not consensus or not prices:
        st.warning("缺少 V4 共識或元大日 K 回補檔，尚無法回測。")
        return

    dates = list(consensus.get("dates") or [])
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        capital = st.number_input("初始資金", min_value=100_000, max_value=100_000_000, value=1_000_000, step=100_000)
    with c2:
        max_positions = st.slider("最多持股", 1, 20, 5)
    with c3:
        commission_pct = st.number_input("單邊手續費 %", min_value=0.0, max_value=1.0, value=0.1425, step=0.01, format="%.4f")
    with c4:
        slippage_bps = st.number_input("滑價 bps", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
    start_date, end_date = st.select_slider(
        "回測訊號區間",
        options=dates,
        value=(dates[0], dates[-1]),
        format_func=lambda value: value.replace("-", "/"),
    )
    config = BacktestConfig(
        initial_capital=float(capital),
        max_positions=max_positions,
        commission_rate=float(commission_pct) / 100.0,
        sell_tax_rate=0.003,
        slippage_bps=float(slippage_bps),
    )
    try:
        result = run_backtest(consensus, prices, config, start_date=start_date, end_date=end_date)
    except ValueError as exc:
        st.error(str(exc))
        return
    metrics = result["metrics"]
    cols = st.columns(6)
    values = [
        ("總報酬", f"{metrics['total_return']:.2%}"),
        ("0050 同期", f"{metrics['benchmark_return']:.2%}"),
        ("最大回撤", f"{metrics['max_drawdown']:.2%}"),
        ("已平倉", f"{metrics['closed_trades']} 筆"),
        ("勝率", f"{metrics['win_rate']:.1%}"),
        ("未平倉", f"{metrics['open_positions']} 檔"),
    ]
    for col, (label, value) in zip(cols, values):
        col.metric(label, value)

    curve = pd.DataFrame(result["equity"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curve["date"], y=curve["strategy_return"] * 100, name="V4 策略", line={"width": 2.5, "color": "#38bdf8"}))
    fig.add_trace(go.Scatter(x=curve["date"], y=curve["benchmark_return"] * 100, name="0050", line={"width": 1.7, "color": "#f59e0b"}))
    fig.update_layout(height=430, margin={"l": 15, "r": 15, "t": 30, "b": 15}, yaxis_title="累積報酬 %", hovermode="x unified", legend={"orientation": "h"})
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns([1.35, 1])
    with left:
        st.markdown("#### 交易明細")
        trade_df = pd.DataFrame(result["trades"])
        if trade_df.empty:
            st.info("此區間沒有完成可執行的買方共識交易。")
        else:
            trade_df["return_pct"] *= 100
            view = trade_df.rename(columns={"symbol": "代號", "name": "名稱", "entry_signal_date": "進場訊號日", "entry_date": "進場日", "exit_signal_date": "出場訊號日", "exit_date": "出場日", "shares": "股數", "entry_price": "進場價", "exit_price": "出場/市價", "pnl": "損益", "return_pct": "報酬率 %", "status": "狀態"})
            st.dataframe(view, use_container_width=True, hide_index=True, column_config={"報酬率 %": st.column_config.NumberColumn(format="%.2f"), "進場價": st.column_config.NumberColumn(format="%.2f"), "出場/市價": st.column_config.NumberColumn(format="%.2f"), "損益": st.column_config.NumberColumn(format="%.0f")})
    with right:
        st.markdown("#### 三交易日資料稽核")
        audit = pd.DataFrame(audit_latest_three_days(consensus, prices))
        audit = audit.rename(columns={"signal_date": "訊號日", "next_trading_date": "下一交易日", "covered_symbols": "有行情", "expected_symbols": "應有", "invalid_ohlc": "OHLC異常", "passed": "通過"})
        st.dataframe(audit, use_container_width=True, hide_index=True)
        if bool(audit["通過"].all()):
            st.success("最近三個 V4 交易日：日期、102 檔覆蓋及 OHLC 邏輯全部通過。")
        else:
            st.warning("三日稽核有缺值或 OHLC 異常，請先檢查行情回補檔。")

    with st.expander("成交與出場規則（重要）"):
        st.markdown(
            """
- **進場**：股票第一次進入 V4 `buy`，於下一個有該股票行情的交易日開盤買進；同日候選先排核心層，再排共識分數。
- **出場**：股票不再維持 `buy`（降為觀察、無訊號或賣方共識），於下一個可交易日開盤全數賣出。第一版不放空。
- **資金**：每格預算＝初始資金 ÷ 最多持股；支援整股零股，不使用融資。已滿倉的訊號不會延後追買。
- **成本**：買賣手續費可調；賣出證交稅固定 0.3%；買賣皆套用可調滑價。
- **均價限制**：元大歷史 `GetKLine` 只有 OHLCV，沒有歷史成交均價；當日逐筆也只能查當日。因此回測採下一交易日開盤價，沒有用訊號日收盤偷看。
- **未平倉**：回測結束仍為買方共識者，以最後收盤價計算未實現損益，不假裝賣出。
            """
        )
