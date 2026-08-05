"""Interactive ETF consensus V4 portfolio backtest tab."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.etf_consensus_backtest import (
    BacktestConfig,
    audit_full_range,
    audit_latest_three_days,
    run_backtest,
)
from src.etf_981_follow_strategy import build_981_follow_signal

MIN_MEANINGFUL_TRADES = 30

STRATEGY_V4 = "V4 三檔 ETF 買方共識"
STRATEGY_981_STRICT = "00981A 續買特殊版（立即退出）"
STRATEGY_981_SWING = "00981A 波段版（容許暫停續買）"


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pct(value):
    return "—" if value is None else f"{value:.2%}"


def _build_signal(name, consensus, history_981, tags, missed_limit):
    if name == STRATEGY_V4:
        return consensus
    return build_981_follow_signal(history_981, tags, exit_after_missed_disclosures=missed_limit)


def render_etf_consensus_backtest_tab(*, DATA_DIR=None, **kwargs):
    st.subheader("ETF 策略回測")
    st.caption("共用無偷看回測工具｜訊號揭露後，最早於下一個可交易日開盤成交。")
    consensus = _load(DATA_DIR / "etf_consensus_v4.json")
    history_981 = _load(DATA_DIR / "etf_00981A_history.json")
    tag_payload = _load(DATA_DIR / "stock_tags.json") or {}
    prices = _load(DATA_DIR / "yuanta_v4_daily_k.json")
    corporate_actions = _load(DATA_DIR / "twse_corporate_actions.json")
    disclosure_times = _load(DATA_DIR / "etf_00981A_disclosure_times.json")
    if not consensus or not history_981 or not prices:
        st.warning("缺少 V4、00981A 歷史或元大日 K 回補檔，尚無法回測。")
        return
    if not corporate_actions:
        st.error(
            "找不到 data/twse_corporate_actions.json：元大日 K 是未還原原始價，"
            "沒有這份除權息表，持股與 0050 都會少算配息。請先執行 "
            "`python scripts/fetch_twse_corporate_actions.py`。"
        )

    tags = tag_payload.get("tags") or {}
    strategy_name = st.selectbox(
        "策略",
        [STRATEGY_V4, STRATEGY_981_STRICT, STRATEGY_981_SWING],
        help="三個策略共用同一套資金、成交成本、下一交易日成交與績效計算引擎。",
    )
    missed_limit = 1
    if strategy_name == STRATEGY_981_SWING:
        missed_limit = st.slider(
            "連續幾次未續買才退出",
            min_value=2,
            max_value=5,
            value=2,
            help="第 N 次連續未續買的揭露日形成退出訊號，下一個可交易日開盤賣出；中途重新續買會歸零。",
        )
    signal = _build_signal(strategy_name, consensus, history_981, tags, missed_limit)
    is_981 = strategy_name != STRATEGY_V4
    if is_981:
        exit_copy = (
            "下一次揭露沒有續買，就於再下一交易日賣出"
            if missed_limit == 1
            else f"連續 {missed_limit} 次揭露都沒有續買，才於下一交易日賣出；中途續買會重新計數"
        )
        st.info(
            "特殊版不等共識：00981A 實際股數增加，且扣除基金申購贖回後的主動配置也增加，"
            f"就於下一交易日買進；{exit_copy}。任何正向可抄動作都算，不設顯著門檻。"
        )
    else:
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
        + (
            f"｜除權息 {corporate_actions.get('event_count', 0)} 筆（{corporate_actions.get('source', '')}）"
            if corporate_actions
            else "｜未載入除權息表"
        )
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
    a1, a2, a3 = st.columns(3)
    with a1:
        requeue = st.checkbox(
            "滿倉訊號排隊補買",
            value=True,
            help="關掉＝滿倉時錯過的訊號永遠不補買。兩者差距就是這個策略的路徑依賴程度。",
        )
    with a2:
        pay_dividends = st.checkbox(
            "配息入帳",
            value=bool(corporate_actions),
            disabled=not corporate_actions,
            help="元大日 K 為未還原價。關掉＝除息當天只看到股價下跌、拿不到現金。",
        )
    with a3:
        compound = st.checkbox(
            "依淨值調整部位",
            value=True,
            help="每格預算＝當前淨值÷檔數。關掉＝永遠用初始資金計算，賺賠都不改變下單規模。",
        )
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
        requeue_missed_entries=requeue,
        compound_position_size=compound,
    )
    closes_981 = {
        str(day): (payload.get("meta") or {}).get("closing_price")
        for day, payload in (history_981 or {}).items()
        if (payload.get("meta") or {}).get("closing_price")
    }
    buy_and_hold = {"symbol": "00981A", "label": "買進持有 00981A", "closes": closes_981}
    extra = {
        "corporate_actions": corporate_actions if pay_dividends else None,
        "buy_and_hold": buy_and_hold,
        "disclosure_times": disclosure_times if is_981 else None,
    }
    try:
        result = run_backtest(signal, prices, config, start_date=start_date, end_date=end_date, **extra)
    except ValueError as exc:
        st.error(str(exc))
        return
    metrics = result["metrics"]

    if metrics["closed_trades"] < MIN_MEANINGFUL_TRADES:
        st.warning(
            f"樣本過小：本區間只有 {metrics['closed_trades']} 筆已平倉交易、"
            f"{metrics['trading_days']} 個交易日。勝率與總報酬在這個樣本數下和擲硬幣沒有差別，"
            "請把下面的數字當作「有沒有明顯壞掉」的檢查，而不是績效證據。"
        )

    cols = st.columns(4)
    cols[0].metric("總報酬（市值）", _pct(metrics["total_return"]))
    cols[1].metric(
        "全部平倉後",
        _pct(metrics["net_total_return"]),
        help="未平倉部位改用實際賣得回來的金額計算，已扣手續費、證交稅與滑價。",
    )
    cols[2].metric(
        "0050 含息",
        _pct(metrics["benchmark_return"]),
        delta=f"純價格 {_pct(metrics['benchmark_price_return'])}",
        delta_color="off",
    )
    cols[3].metric(
        "買進持有 00981A",
        _pct(metrics["buy_and_hold_return"]),
        help="抄作業的真正機會成本：直接買這檔 ETF、不用盯盤也不用付來回成本。",
    )
    cols2 = st.columns(4)
    cols2[0].metric("最大回撤", _pct(metrics["max_drawdown"]))
    cols2[1].metric(
        "已平倉",
        f"{metrics['closed_trades']} 筆",
        delta=f"勝率 {metrics['win_rate']:.0%}",
        delta_color="off",
    )
    cols2[2].metric(
        "已實現／未實現",
        f"{metrics['realized_pnl']:,.0f} / {metrics['unrealized_pnl']:,.0f}",
        delta=f"已實現占比 {metrics['realized_share']:.0%}",
        delta_color="off",
    )
    cols2[3].metric(
        "平均曝險",
        f"{metrics['average_exposure']:.0%}",
        help="平均用掉幾成持股格。策略常抱現金，和滿倉的 0050 比波動本來就不對等。",
    )

    if metrics["dividends_collected"]:
        st.caption(
            f"期間收到現金股利 {metrics['dividends_collected']:,.0f} 元"
            f"（{len(result['dividends'])} 次除權息）；0050 含息與純價格差 "
            f"{(metrics['benchmark_return'] - metrics['benchmark_price_return']) * 100:.2f} 個百分點。"
        )

    curve = pd.DataFrame(result["equity"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curve["date"], y=curve["strategy_return"] * 100, name=strategy_name, line={"width": 2.5, "color": "#38bdf8"}))
    fig.add_trace(go.Scatter(x=curve["date"], y=curve["benchmark_return"] * 100, name="0050 含息", line={"width": 1.7, "color": "#f59e0b"}))
    if "buy_and_hold_return" in curve:
        fig.add_trace(
            go.Scatter(
                x=curve["date"],
                y=curve["buy_and_hold_return"] * 100,
                name="買進持有 00981A",
                line={"width": 1.7, "color": "#a78bfa", "dash": "dot"},
            )
        )
    fig.update_layout(height=430, margin={"l": 15, "r": 15, "t": 30, "b": 15}, yaxis_title="累積報酬 %", hovermode="x unified", legend={"orientation": "h"})
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("同一引擎跑完所有策略與參數（避免只看最好看的那條）", expanded=False):
        st.caption(
            "只挑一組參數展示，等於讓人挑出最漂亮的曲線。以下是同區間、同成本下的全部組合。"
        )
        rows = []
        variants = [(STRATEGY_V4, 1), (STRATEGY_981_STRICT, 1)] + [
            (STRATEGY_981_SWING, limit) for limit in range(2, 6)
        ]
        for name, limit in variants:
            try:
                variant_signal = _build_signal(name, consensus, history_981, tags, limit)
                variant_dates = [day for day in (variant_signal.get("dates") or []) if day in price_dates]
                if not variant_dates:
                    continue
                sweep = run_backtest(
                    variant_signal,
                    prices,
                    config,
                    start_date=max(start_date, variant_dates[0]),
                    end_date=min(end_date, variant_dates[-1]),
                    **extra,
                )
            except ValueError:
                continue
            sweep_metrics = sweep["metrics"]
            rows.append(
                {
                    "策略": name if name != STRATEGY_981_SWING else f"{name}｜{limit} 次",
                    "總報酬": _pct(sweep_metrics["total_return"]),
                    "全部平倉後": _pct(sweep_metrics["net_total_return"]),
                    "0050 含息": _pct(sweep_metrics["benchmark_return"]),
                    "買進持有 981": _pct(sweep_metrics["buy_and_hold_return"]),
                    "最大回撤": _pct(sweep_metrics["max_drawdown"]),
                    "已平倉": f"{sweep_metrics['closed_trades']} 筆",
                    "勝率": f"{sweep_metrics['win_rate']:.0%}",
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "同一段資料被反覆測試越多次，最好的那組越可能只是運氣。這張表是探索用的樣本內結果，"
                "不是可以拿去下單的績效。"
            )

    left, right = st.columns([1.35, 1])
    with left:
        st.markdown("#### 交易明細")
        trade_df = pd.DataFrame(result["trades"])
        if trade_df.empty:
            st.info("此區間沒有完成可執行的買方共識交易。")
        else:
            trade_df["return_pct"] *= 100
            trade_df["status"] = trade_df["status"].map(
                {"closed": "已平倉", "open": "未平倉", "blocked": "無法出場"}
            ).fillna(trade_df["status"])
            for column in ("exit_signal_date", "exit_date"):
                trade_df[column] = trade_df[column].fillna("—")
            view = trade_df.rename(columns={"symbol": "代號", "name": "名稱", "entry_signal_date": "進場訊號日", "entry_date": "進場日", "exit_signal_date": "出場訊號日", "exit_date": "出場日", "shares": "股數", "entry_price": "進場價", "exit_price": "出場/清算價", "dividends": "股利", "pnl": "損益", "return_pct": "報酬率 %", "status": "狀態"})
            st.dataframe(view, use_container_width=True, hide_index=True, column_config={"報酬率 %": st.column_config.NumberColumn(format="%.2f"), "進場價": st.column_config.NumberColumn(format="%.2f"), "出場/清算價": st.column_config.NumberColumn(format="%.2f"), "股利": st.column_config.NumberColumn(format="%.0f"), "損益": st.column_config.NumberColumn(format="%.0f")})
            if metrics["queued_entries"]:
                st.caption(
                    f"另有 {metrics['queued_entries']} 次訊號在當下無法成交（滿倉或現金不足）。"
                    + ("目前設定會在空位釋出後補買。" if requeue else "目前設定直接放棄，不再補買。")
                )
    with right:
        st.markdown("#### 資料稽核（全區間）")
        report = audit_full_range(
            signal,
            prices,
            corporate_actions=corporate_actions,
            disclosure_times=disclosure_times if is_981 else None,
        )
        summary = pd.DataFrame(
            [
                {"項目": "訊號日", "結果": f"{report['tradable_signal_dates']}/{report['signal_dates']} 有行情"},
                {"項目": "個股行情覆蓋", "結果": f"{report['coverage_pct']:.1f}%（缺 {report['missing_bars']} 格）"},
                {"項目": "OHLC 邏輯異常", "結果": f"{report['invalid_ohlc']} 筆"},
                {"項目": "已套用除權息", "結果": f"{report['corporate_actions_applied']} 筆"},
                {"項目": "無法解釋的跳空", "結果": f"{len(report['unexplained_gaps'])} 筆"},
                {
                    "項目": "揭露時點",
                    "結果": (
                        f"{report['disclosure_checked']} 日已驗證"
                        + (f"，{len(report['disclosure_suspect'])} 日可疑" if report["disclosure_suspect"] else "，全部在收盤後")
                        if report["disclosure_checked"]
                        else "此策略無揭露時戳"
                    ),
                },
            ]
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)
        if report["passed"]:
            st.success("全區間覆蓋、OHLC 邏輯、除權息與揭露時點皆通過。")
        else:
            st.warning("有項目未通過，展開下方明細確認是真實漲跌停還是資料缺漏。")
        if report["unexplained_gaps"]:
            with st.expander(f"無法解釋的跳空 {len(report['unexplained_gaps'])} 筆"):
                st.caption("扣掉大盤同日跳空後仍下跌超過 9%，且查無除權息紀錄。多半是真的跌停，但值得人工確認。")
                st.dataframe(
                    pd.DataFrame(report["unexplained_gaps"]).rename(
                        columns={"symbol": "代號", "date": "日期", "gap_pct": "跳空 %", "excess_pct": "超額 %"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
        with st.expander("最近三個交易日逐檔明細"):
            audit = pd.DataFrame(audit_latest_three_days(signal, prices))
            st.dataframe(
                audit.rename(columns={"signal_date": "訊號日", "next_trading_date": "下一交易日", "covered_symbols": "有行情", "expected_symbols": "應有", "invalid_ohlc": "OHLC異常", "passed": "通過"}),
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("成交與出場規則（重要）"):
        st.markdown(
            f"""
- **進場**：策略訊號第一次進入 `buy`，於下一個有該股票行情的交易日開盤買進；V4 同日候選先排核心層再排共識分數，981 特殊版依主動配置增加幅度排序。
- **出場**：策略訊號不再維持 `buy`，於下一個可交易日開盤全數賣出。第一版不放空。停牌或下市買不掉的部位會持續掛單，並在明細標成「無法出場」，不會憑空消失。
- **資金**：每格預算＝{'當前淨值' if compound else '初始資金'} ÷ 最多持股；支援盤中零股，不使用融資。
- **滿倉**：{'仍在 `buy` 的訊號會排隊，空位釋出後補買。' if requeue else '滿倉時錯過的訊號直接放棄，不再補買。'}把這個開關切換一次，就能看出結果有多依賴「誰先進場」。
- **成本**：買賣手續費可調（每筆最低 20 元）；賣出證交稅 0.3%；零股額外加 20 bps 滑價；單筆成交量不超過當日量的 5%。
- **配息**：{'已套用 TWSE 除權除息計算結果表，現金股利入帳、配股調整股數；0050 基準同樣採含息報酬。' if pay_dividends else '**目前關閉**，除息只會看到股價下跌卻沒有現金入帳，策略與 0050 都會被低估。'}
- **均價限制**：元大歷史 `GetKLine` 只有未還原 OHLCV，沒有歷史成交均價。因此回測採下一交易日開盤價，沒有用訊號日收盤偷看。
- **未平倉**：以最後收盤價列示，另用「全部平倉後」欄位顯示扣掉出場成本後真正拿得回來的金額。
            """
        )
