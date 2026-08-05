"""Interactive ETF consensus V4 portfolio backtest tab."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.etf_consensus_backtest import BacktestConfig, audit_latest_three_days, run_backtest
from src.etf_981_follow_strategy import build_981_follow_signal


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_etf_consensus_backtest_tab(*, DATA_DIR=None, **kwargs):
    st.subheader("ETF 策略回測")
    st.caption("共用無偷看回測工具｜訊號揭露後，最早於下一個可交易日開盤成交。")
    consensus = _load(DATA_DIR / "etf_consensus_v4.json")
    history_981 = _load(DATA_DIR / "etf_00981A_history.json")
    tag_payload = _load(DATA_DIR / "stock_tags.json") or {}
    prices = _load(DATA_DIR / "yuanta_v4_daily_k.json")
    if not consensus or not history_981 or not prices:
        st.warning("缺少 V4、00981A 歷史或元大日 K 回補檔，尚無法回測。")
        return

    strategy_name = st.selectbox(
        "策略",
        ["V4 三檔 ETF 買方共識", "00981A 續買特殊版"],
        help="兩個策略共用同一套資金、成交成本、下一交易日成交與績效計算引擎。",
    )
    if strategy_name == "00981A 續買特殊版":
        signal = build_981_follow_signal(history_981, tag_payload.get("tags") or {})
        st.info(
            "特殊版不等共識：00981A 實際股數增加，且扣除基金申購贖回後的主動配置也增加，"
            "就於下一交易日買進；下一次揭露沒有續買，就於再下一交易日賣出。任何正向可抄動作都算，不設顯著門檻。"
        )
    else:
        signal = consensus
        st.info("通用版：股票首次進入 V4 三檔 ETF 買方共識時買進，不再維持買方共識時賣出。")

    benchmark = (prices.get("symbols") or {}).get(str(prices.get("benchmark") or "0050")) or []
    price_dates = {str(bar.get("date")) for bar in benchmark}
    dates = [day for day in (signal.get("dates") or []) if day in price_dates]
    if not dates:
        st.warning("元大行情快照與 V4 共識沒有共同交易日。")
        return
    price_end = max(price_dates)
    st.caption(
        f"行情來源：{prices.get('source', 'Yuanta SPARK')}｜"
        f"快照截止 {price_end}｜{prices.get('successful_symbols', len(prices.get('symbols') or {}))}/"
        f"{prices.get('symbol_count', len(prices.get('symbols') or {}))} 檔成功"
    )
    if str(signal.get("as_of") or "") > price_end:
        st.info(f"策略訊號已更新至 {signal.get('as_of')}，本次單次行情快照只到 {price_end}；回測暫停在快照截止日。")
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
        result = run_backtest(signal, prices, config, start_date=start_date, end_date=end_date)
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
        audit = pd.DataFrame(audit_latest_three_days(signal, prices))
        audit = audit.rename(columns={"signal_date": "訊號日", "next_trading_date": "下一交易日", "covered_symbols": "有行情", "expected_symbols": "應有", "invalid_ohlc": "OHLC異常", "passed": "通過"})
        st.dataframe(audit, use_container_width=True, hide_index=True)
        if bool(audit["通過"].all()):
            expected = int(audit["應有"].max()) if not audit.empty else 0
            st.success(f"快照內最近三個策略交易日：日期、{expected} 檔覆蓋及 OHLC 邏輯全部通過。")
        else:
            st.warning("三日稽核有缺值或 OHLC 異常，請先檢查行情回補檔。")

    with st.expander("成交與出場規則（重要）"):
        st.markdown(
            """
- **進場**：策略訊號第一次進入 `buy`，於下一個有該股票行情的交易日開盤買進；V4 同日候選先排核心層再排共識分數，981 特殊版依主動配置增加幅度排序。
- **出場**：策略訊號不再維持 `buy`，於下一個可交易日開盤全數賣出。第一版不放空。
- **資金**：每格預算＝初始資金 ÷ 最多持股；支援整股零股，不使用融資。已滿倉的訊號不會延後追買。
- **成本**：買賣手續費可調；賣出證交稅固定 0.3%；買賣皆套用可調滑價。
- **均價限制**：元大歷史 `GetKLine` 只有 OHLCV，沒有歷史成交均價；當日逐筆也只能查當日。因此回測採下一交易日開盤價，沒有用訊號日收盤偷看。
- **未平倉**：回測結束仍為買方共識者，以最後收盤價計算未實現損益，不假裝賣出。
            """
        )
