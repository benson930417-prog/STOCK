import json
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


def render_etf_tab(
    *,
    lang,
    T,
    DATA_DIR,
    CURRENCY_RATE,
    NEUTRAL_PURPLE,
    NEW_COLOR,
    REMOVED_COLOR,
    PROFIT_COLOR,
    LOSS_COLOR,
    delta_color_param="normal",
):
    st.subheader(T(lang, "Active ETF Holdings", "主動型 ETF 投資組合"))
    
    etf_ticker = st.selectbox(
        T(lang, "Select ETF", "選擇 ETF"),
        ["00981A", "00997A"]
    )

    share_unit = T(lang, "Shares", "股") if etf_ticker == "00997A" else T(lang, "Lots", "張")

    def _display_share_quantity(value):
        if etf_ticker == "00997A":
            return value
        return value / 1000

    def _format_share_change(value):
        display_value = _display_share_quantity(value)
        return f"+{display_value:,.0f}" if display_value > 0 else f"{display_value:,.0f}"
    
    # --- NEW TRACKER UI ---
    log_file = DATA_DIR / f"etf_{etf_ticker}_log.json"
    if log_file.exists():
         try:
              with open(log_file, "r", encoding="utf-8") as fl:
                   log_data = json.loads(fl.read())
                   
              def _time_ago(dt_str, lang):
                   if not dt_str:
                        return T(lang, "Unknown", "未知")
                   dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                   now = datetime.now(timezone.utc)
                   diff = max(0, (now - dt).total_seconds())
                   mins = int(diff / 60)
                   if mins < 60:
                        rel = T(lang, f"{mins} mins ago", f"{mins} 分鐘前")
                   elif mins < 1440:
                        rel = T(lang, f"{mins // 60} hrs ago", f"{mins // 60} 小時前")
                   else:
                        rel = T(lang, f"{mins // 1440} days ago", f"{mins // 1440} 天前")

                   try:
                        import zoneinfo
                        local_tz = zoneinfo.ZoneInfo("Asia/Taipei")
                        local_time = dt.astimezone(local_tz).strftime("%m-%d %H:%M")
                        return f"{rel} ({local_time} TW)"
                   except Exception:
                        return rel
                   
              lcu = log_data.get("last_checked_utc")
              luu = log_data.get("last_updated_utc")
              status_msg = log_data.get("status", "Unknown")
              
              checked_str = _time_ago(lcu, lang)
              update_str = _time_ago(luu, lang) if luu else T(lang, "Never", "從未")
              
              st.info(
                  f"**{T(lang, 'Backend Tracker', '雲端更新狀態')}**: {status_msg}  \n"
                  f"**{T(lang, 'Last checked', '最後檢查時間')}**: {checked_str}  \n"
                  f"**{T(lang, 'Last updated', '最後資料更新')}**: {update_str}",
                  icon="🔎"
              )
         except Exception as e:
              pass
    # ----------------------

    etf_file = DATA_DIR / f"etf_{etf_ticker}_history.json"

    history_data = {}
    if etf_file.exists():
         try:
              with open(etf_file, "r", encoding="utf-8") as fl:
                   history_data = json.loads(fl.read())
         except Exception:
              pass
              
    dates = sorted(list(history_data.keys()), reverse=True)
    if not dates:
         st.warning(T(lang, "No ETF history data available currently.", "目前無 ETF 歷史資料。"))
         # return # Removed return to allow the subtabs to show empty state
         
    col_d1, col_d2 = st.columns([1, 3])
    with col_d1:
         selected_date = st.selectbox(T(lang, "Select Data Date", "選擇資料日期"), dates) if dates else None
         
    # Add Sub-tabs for Holdings and Daily Report
    etf_tab_overview, etf_tab_daily = st.tabs([
         T(lang, "Holdings Overview", "總覽 / 持股"),
         T(lang, "Operation Daily Report", "操作日報")
    ])
    
    with etf_tab_overview:
         if selected_date and selected_date in history_data:
             curr_day_data = history_data[selected_date]
             holdings = curr_day_data.get("holdings", [])
             
             st.markdown(f"**{T(lang, 'Total Stocks', '總檔數')}**: {len(holdings)}")
             
             if holdings:
                  df_h = pd.DataFrame(holdings)
                  # Sort by weight
                  df_h = df_h.sort_values(by="weight_pct", ascending=False).reset_index(drop=True)
                  df_h["stock_label"] = df_h["id"].astype(str) + " " + df_h["name"]
                  
                  top_n = st.slider(T(lang, "Show Top N Holdings", "顯示前 N 大持股"), min_value=5, max_value=len(df_h), value=min(20, len(df_h)), step=5)
                  
                  df_top = df_h.head(top_n)
                  
                  fig = px.bar(
                       df_top,
                       x="stock_label",
                       y="weight_pct",
                       text="weight_pct",
                       color_discrete_sequence=[NEUTRAL_PURPLE],
                       title=f"{T(lang, f'Top {top_n} Holdings', f'前 {top_n} 大持股')} ({selected_date})",
                       labels={"stock_label": T(lang, "Stock", "股票"), "weight_pct": T(lang, "Weight (%)", "權重 (%)")}
                  )
                  fig.update_traces(texttemplate='%{text:.2f}%', textposition='outside')
                  fig.update_layout(xaxis_title="", yaxis_title=T(lang, "Weight (%)", "權重 (%)"), height=500, margin=dict(b=100))
                  st.plotly_chart(fig, use_container_width=True)
                  
                  # Rename columns for display
                  df_show = df_h[["id", "name", "weight_pct", "shares"]].copy()
                  df_show["shares"] = df_show["shares"].apply(_display_share_quantity)
                  df_show.columns = [
                       T(lang, "Stock ID", "代號"),
                       T(lang, "Stock Name", "名稱"),
                       T(lang, "Weight (%)", "權重 (%)"),
                       T(lang, "Holdings", "持股") + f" ({share_unit})"
                  ]
                  st.dataframe(df_show, width="stretch")
             else:
                  st.info(T(lang, "No holdings data for this date.", "此日期無持股資料。"))
         else:
             st.info(T(lang, "Please select a date with available ETF data.", "請選擇有 ETF 資料的日期。"))

    with etf_tab_daily:
        if selected_date and selected_date in history_data:
            st.markdown(f"### {selected_date} {T(lang, 'Operation Daily Report', '操作日報')}")
            curr_idx = dates.index(selected_date)
            if curr_idx == len(dates) - 1:
                st.warning(T(lang, "No previous day data available to compare.", "無前一日資料可供比較。"))
            else:
                prev_date = dates[curr_idx + 1]
                # st.caption(f"{T(lang, 'Compared to', '較前日')} {prev_date}")
                
                curr_data = history_data[selected_date]
                prev_data = history_data[prev_date]
                

                curr_meta = curr_data.get("meta", {})
                
                fund_size = curr_meta.get("fund_size", 0)
                nav = curr_meta.get("nav", 0)
                
                # Use EXACT daily closing price for accurate comparison
                market_price = curr_meta.get("closing_price")
                
                if not market_price:
                    # Fallback for live operations
                    market_price = nav
                    try:
                        import requests
                        res = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{etf_ticker}.TW", headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
                        if res.status_code == 200:
                            market_price = res.json()['chart']['result'][0]['meta']['regularMarketPrice']
                    except Exception:
                        pass
                
                premium_pct = 0.0
                if nav and nav > 0:
                    premium_pct = ((market_price - nav) / nav) * 100
                
                # Top Metadata Cards
                m1, m2 = st.columns(2)
                with m1:
                    if fund_size:
                        c_val = fund_size * CURRENCY_RATE
                        
                        prev_meta = prev_data.get("meta", {}) if prev_data else {}
                        prev_fund_size = prev_meta.get("fund_size", 0)
                        delta_str = None
                        if prev_fund_size and prev_fund_size > 0:
                            diff_pct = ((fund_size - prev_fund_size) / prev_fund_size) * 100.0
                            delta_str = f"{diff_pct:+.2f}%"
                            
                        if CURRENCY_RATE != 1.0:
                            f_size_disp = f"€ {c_val / 1_000_000:,.1f}M"
                            st.metric(T(lang, "Fund Size (EUR)", "基金規模 (歐元)"), f_size_disp, delta=delta_str, delta_color=delta_color_param)
                        else:
                            if lang == "中文":
                                f_size_disp = f"{int(c_val / 100000000)} 億"
                                st.metric("基金規模 (TWD)", f_size_disp, delta=delta_str, delta_color=delta_color_param)
                            else:
                                f_size_disp = f"{c_val / 1_000_000:,.1f}M"
                                st.metric("Fund Size (TWD)", f_size_disp, delta=delta_str, delta_color=delta_color_param)
                    else:
                        st.metric(T(lang, "Fund Size", "基金規模"), "N/A")
                with m2:
                    st.metric(T(lang, "Premium/Discount", "折溢價"), f"{premium_pct:+.2f}%", 
                              delta_color=delta_color_param, help=f"{T(lang, 'Market Price:', '股價:')} {market_price:.2f} | {T(lang, 'NAV:', '淨值:')} {nav:.2f}" if nav else "")
                              
                # Calculate Differences
                prev_map = { h['id']: h for h in prev_data.get('holdings', []) }
                curr_map = { h['id']: h for h in curr_data.get('holdings', []) }
                
                new_s, del_s, inc_s, dec_s = [], [], [], []
                for sid, ch in curr_map.items():
                    if sid not in prev_map:
                        new_s.append(ch)
                    else:
                        ph = prev_map[sid]
                        diff_sh = ch['shares'] - ph['shares']
                        if diff_sh > 0: inc_s.append((ch, ph))
                        elif diff_sh < 0: dec_s.append((ch, ph))
                        
                for sid, ph in prev_map.items():
                    if sid not in curr_map:
                        del_s.append(ph)
                        
                # 4 Status Boxes - 2x2 Layout
                st.write("")
                row1_col1, row1_col2 = st.columns(2)
                row2_col1, row2_col2 = st.columns(2)
                
                def box_ui(title, count, color):
                    return f'''
                    <div style="background-color: {color}15; border-left: 6px solid {color}; padding: 16px 20px; border-radius: 8px; margin-bottom: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                        <div style="color: {color}; font-weight: 700; font-size: 16px; margin-bottom: 8px; letter-spacing: 0.5px;">{title}</div>
                        <div style="color: white; font-size: 32px; font-weight: 800; line-height: 1;">{count}</div>
                    </div>
                    '''
                    
                with row1_col1: st.markdown(box_ui(T(lang, "New", "新增"), f"{len(new_s)} {T(lang,'Count','檔')}", NEW_COLOR), unsafe_allow_html=True)
                with row1_col2: st.markdown(box_ui(T(lang, "Removed", "刪除"), f"{len(del_s)} {T(lang,'Count','檔')}", REMOVED_COLOR), unsafe_allow_html=True)
                with row2_col1: st.markdown(box_ui(T(lang, "Increased", "加碼"), f"{len(inc_s)} {T(lang,'Count','檔')}", PROFIT_COLOR), unsafe_allow_html=True)
                with row2_col2: st.markdown(box_ui(T(lang, "Decreased", "減碼"), f"{len(dec_s)} {T(lang,'Count','檔')}", LOSS_COLOR), unsafe_allow_html=True)
                
                st.write(f"{T(lang, 'Total ', '共')} {len(new_s)+len(del_s)+len(inc_s)+len(dec_s)} {T(lang, 'changes detected.', '檔異動')}")
                
                # Build unified operations DataFrame
                rows = []
                for h in new_s:
                    rows.append({"ID": h['id'], "Name": h['name'], "Status": T(lang, "New", "新增"), "ShareDiff": h['shares'], "MagPct": 100.0, "CurrWeight": h['weight_pct'], "WeightDiff": h['weight_pct'], "ActiveWeight": h['weight_pct']})
                for h in del_s:
                    rows.append({"ID": h['id'], "Name": h['name'], "Status": T(lang, "Removed", "刪除"), "ShareDiff": -h['shares'], "MagPct": -100.0, "CurrWeight": 0.0, "WeightDiff": -h['weight_pct'], "ActiveWeight": -h['weight_pct']})
                for ch, ph in inc_s:
                    diff_sh = ch['shares'] - ph['shares']
                    mag = (diff_sh / ph['shares'] * 100.0) if ph['shares'] else 0.0
                    active_w = diff_sh * (ch['weight_pct'] / ch['shares']) if ch['shares'] else 0.0
                    rows.append({"ID": ch['id'], "Name": ch['name'], "Status": T(lang, "Increased", "加碼"), "ShareDiff": diff_sh, "MagPct": mag, "CurrWeight": ch['weight_pct'], "WeightDiff": ch['weight_pct'] - ph['weight_pct'], "ActiveWeight": active_w})
                for ch, ph in dec_s:
                    diff_sh = ch['shares'] - ph['shares']
                    mag = (diff_sh / ph['shares'] * 100.0) if ph['shares'] else 0.0
                    active_w = diff_sh * (ph['weight_pct'] / ph['shares']) if ph['shares'] else 0.0
                    rows.append({"ID": ch['id'], "Name": ch['name'], "Status": T(lang, "Decreased", "減碼"), "ShareDiff": diff_sh, "MagPct": mag, "CurrWeight": ch['weight_pct'], "WeightDiff": ch['weight_pct'] - ph['weight_pct'], "ActiveWeight": active_w})
                    
                if rows:
                    df_ops = pd.DataFrame(rows)
                    # Sort by MagPct abs value or Status
                    df_ops = df_ops.sort_values(by="MagPct", ascending=False).reset_index(drop=True)
                    
                    df_ops["Target"] = df_ops["Name"] + " (" + df_ops["ID"].astype(str) + ")"
                    df_ops["ShareDiffStr"] = df_ops["ShareDiff"].apply(_format_share_change)
                    df_ops["CurrWeightStr"] = df_ops["CurrWeight"].apply(lambda x: f"{x:.2f}%")
                    df_ops["WeightDiffStr"] = df_ops["WeightDiff"].apply(lambda x: f" {x:+.2f}%")
                    df_ops["ActiveWeightStr"] = df_ops["ActiveWeight"].apply(lambda x: f" {x:+.2f}%")
                    
                    f_sz = (fund_size * CURRENCY_RATE) if fund_size else 0.0
                    df_ops["ActiveMoney"] = (df_ops["ActiveWeight"] / 100.0) * f_sz
                    
                    def fmt_mny_only(m):
                        if abs(m) < 1000000.0: return "<100 萬" if lang == "中文" else "<1M"
                        sign = "+" if m > 0 else "-"
                        am = abs(m)
                        if lang == "中文":
                            if am >= 100000000: return f"{sign}{am/100000000:.2f} 億"
                            elif am >= 10000: return f"{sign}{am/10000:.0f} 萬"
                            else: return f"{sign}{am:,.0f}"
                        else:
                            if am >= 1000000000: return f"{sign}{am/1000000000:.2f}B"
                            elif am >= 1000000: return f"{sign}{am/1000000:.1f}M"
                            elif am >= 1000: return f"{sign}{am/1000:.0f}K"
                            else: return f"{sign}{am:,.0f}"
                            
                    df_ops["ActiveMoneyStr"] = df_ops["ActiveMoney"].apply(lambda x: f"({fmt_mny_only(x)})" if fmt_mny_only(x) else "")
                    
                    import plotly.graph_objects as go
                    
                    chart_df = df_ops.sort_values(by="ActiveMoney", ascending=True).copy()
                    
                    max_abs_val = chart_df["ActiveMoney"].abs().max() if not chart_df.empty else 0
                    currency_str = "歐元" if CURRENCY_RATE != 1.0 else "TWD"
                    currency_eng_str = "EUR" if CURRENCY_RATE != 1.0 else "TWD"
                    
                    if lang == "中文":
                        if max_abs_val >= 100000000:
                            scale_div = 100000000.0
                            axis_unit = f"億 {currency_str}"
                        elif max_abs_val >= 10000:
                            scale_div = 10000.0
                            axis_unit = f"萬 {currency_str}"
                        else:
                            scale_div = 1.0
                            axis_unit = currency_str
                        axis_title = f"資金分配變動額 (估值, {axis_unit})"
                    else:
                        if max_abs_val >= 1000000000:
                            scale_div = 1000000000.0
                            axis_unit = f"B {currency_eng_str}"
                        elif max_abs_val >= 1000000:
                            scale_div = 1000000.0
                            axis_unit = f"M {currency_eng_str}"
                        elif max_abs_val >= 1000:
                            scale_div = 1000.0
                            axis_unit = f"K {currency_eng_str}"
                        else:
                            scale_div = 1.0
                            axis_unit = currency_eng_str
                        axis_title = f"Capital Allocation Amount (Est, {axis_unit})"
                        
                    chart_df["PlotValue"] = chart_df["ActiveMoney"] / scale_div
                    
                    chart_df["PrevWeight"] = chart_df["CurrWeight"] - chart_df["WeightDiff"]
                    
                    def format_label(row):
                        money_val = fmt_mny_only(row["ActiveMoney"])
                        if not money_val: money_val = "0"
                        share_str = f" <span style='font-size:12px; color:#cccccc'>({row['ShareDiffStr']} {share_unit})</span>"
                        prev = f"{row['PrevWeight']:.2f}%"
                        curr = row["CurrWeightStr"]
                        return f"<b>{money_val}</b>{share_str} <span style='font-size:12px; color:#aaaaaa'>({prev} ➜ {curr})</span>"
                        
                    def get_bar_color(status):
                        if status == T(lang, "New", "新增"): return NEW_COLOR
                        if status == T(lang, "Removed", "刪除"): return REMOVED_COLOR
                        if status == T(lang, "Increased", "加碼"): return PROFIT_COLOR
                        if status == T(lang, "Decreased", "減碼"): return LOSS_COLOR
                        return "gray"
                        
                    colors = chart_df["Status"].apply(get_bar_color)
                    texts = chart_df.apply(format_label, axis=1)
                    
                    hover_texts = chart_df.apply(
                        lambda row: f"<b>{row['Name']}</b><br>Before: {row['PrevWeight']:.2f}%<br>Allocated: {fmt_mny_only(row['ActiveMoney'])} ({row['ActiveWeightStr'].strip()})<br>Result: {row['CurrWeightStr']}", 
                        axis=1
                    )
                    
                    fig = go.Figure(go.Bar(
                        x=chart_df["PlotValue"],
                        y=chart_df["Name"],
                        orientation='h',
                        marker_color=colors,
                        cliponaxis=False,
                        text=texts,
                        textposition="outside",
                        textfont=dict(color="white"),
                        hovertemplate="%{customdata}<extra></extra>",
                        customdata=hover_texts
                    ))
                    
                    fig.update_layout(
                        margin=dict(l=0, r=100, t=30, b=0),
                        height=max(400, len(chart_df) * 30),
                        xaxis_title=axis_title,
                        yaxis_title="",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="white"),
                        showlegend=False
                    )
                    fig.update_yaxes(tickmode='linear')
                    
                    x_min = min(0.0, chart_df["PlotValue"].min())
                    x_max = max(0.0, chart_df["PlotValue"].max())
                    x_range = x_max - x_min if (x_max - x_min) > 0 else 1.0
                    
                    x_pad_l = x_range * 0.40
                    x_pad_r = x_range * 0.40
                    fig.update_xaxes(
                        range=[x_min - x_pad_l, x_max + x_pad_r],
                        showgrid=True, 
                        gridcolor='rgba(255,255,255,0.1)', 
                        zeroline=True, 
                        zerolinecolor='rgba(255,255,255,0.3)'
                    )
                    
                    st.markdown(f"#### {T(lang, 'Portfolio Capital Adjustments', '投資組合資金分配變動圖')}")
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                    
                    df_ops_show = df_ops[["Target", "Status", "ShareDiffStr", "CurrWeightStr", "ActiveWeightStr"]].copy()
                    df_ops_show.columns = [
                        T(lang, "Target", "標的"),
                        T(lang, "Status", "狀態"),
                        T(lang, "Share Chg", "持股變動") + f" ({share_unit})",
                        T(lang, "Weight (%)", "目前權重"),
                        T(lang, "Alloc Chg (%)", "資金分配變動%")
                    ]
                    
                    def style_status(val):
                         if val == T(lang, "New", "新增"):
                              return f"color: {NEW_COLOR}; font-weight: bold;"
                         elif val == T(lang, "Removed", "刪除"):
                              return f"color: {REMOVED_COLOR}; font-weight: bold;"
                         elif val == T(lang, "Increased", "加碼"):
                              return f"color: {PROFIT_COLOR}; font-weight: bold;"
                         elif val == T(lang, "Decreased", "減碼"):
                              return f"color: {LOSS_COLOR}; font-weight: bold;"
                         return ""
                         
                    styler = df_ops_show.style.applymap(style_status, subset=[T(lang, "Status", "狀態")])
                    st.dataframe(styler, width="stretch", height=600)
                else:
                    st.info(T(lang, "No portfolio changes detected from previous day.", "相較前日無任何持股變動。"))


def render_passive_etf_tab(
    *,
    lang,
    T,
    DATA_DIR,
    NEUTRAL_PURPLE,
    delta_color_param="normal",
    PROFIT_COLOR="#2ECC71",
    LOSS_COLOR="#E74C3C",
):
    st.subheader(T(lang, "Passive ETF Holdings", "被動式 ETF 投資組合"))

    etf_ticker = st.selectbox(
        T(lang, "Select ETF", "選擇 ETF"),
        ["0050", "00830", "00878", "009805", "009820"],
        format_func=lambda x: f"{x} (被動)",
    )

    log_file = DATA_DIR / f"passive_{etf_ticker}_log.json"
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as fl:
                log_data = json.loads(fl.read())

            def _time_ago(dt_str):
                if not dt_str:
                    return T(lang, "Unknown", "未知")
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                diff = max(0, (now - dt).total_seconds())
                mins = int(diff / 60)
                if mins < 60:
                    rel = T(lang, f"{mins} mins ago", f"{mins} 分鐘前")
                elif mins < 1440:
                    rel = T(lang, f"{mins // 60} hrs ago", f"{mins // 60} 小時前")
                else:
                    rel = T(lang, f"{mins // 1440} days ago", f"{mins // 1440} 天前")

                try:
                    import zoneinfo

                    local_tz = zoneinfo.ZoneInfo("Asia/Taipei")
                    local_time = dt.astimezone(local_tz).strftime("%m-%d %H:%M")
                    return f"{rel} ({local_time} TW)"
                except Exception:
                    return rel

            checked_str = _time_ago(log_data.get("last_checked_utc"))
            update_str = _time_ago(log_data.get("last_updated_utc")) if log_data.get("last_updated_utc") else T(lang, "Never", "從未")
            status_msg = log_data.get("status", "Unknown")

            st.info(
                f"**{T(lang, 'Backend Tracker', '雲端更新狀態')}**: {status_msg}  \n"
                f"**{T(lang, 'Last checked', '最後檢查時間')}**: {checked_str}  \n"
                f"**{T(lang, 'Last updated', '最後資料更新')}**: {update_str}",
                icon="🔎",
            )
        except Exception:
            pass

    etf_file = DATA_DIR / f"passive_{etf_ticker}_history.json"
    history_data = {}
    if etf_file.exists():
        try:
            with open(etf_file, "r", encoding="utf-8") as fl:
                history_data = json.loads(fl.read())
        except Exception:
            pass

    dates = sorted(list(history_data.keys()), reverse=True)
    if not dates:
        st.warning(T(lang, "No passive ETF history data available currently.", "目前沒有被動式 ETF 歷史資料。"))
        return

    col_d1, col_d2 = st.columns([1, 3])
    with col_d1:
        selected_date = st.selectbox(T(lang, "Select Data Date", "選擇資料日期"), dates, key="passive_etf_date")

    if selected_date and selected_date in history_data:
        curr_day_data = history_data[selected_date]
        holdings = curr_day_data.get("holdings", [])
        meta = curr_day_data.get("meta", {})

        st.markdown(f"**{T(lang, 'Total Stocks', '總檔數')}**: {len(holdings)}")

        nav_history = meta.get("nav_history", {})
        latest_nav = nav_history.get("latest", {})

        # Compute day-over-day deltas from the previous available history entry.
        # The fetcher only stores meta-level scalars, so deltas live in the UI layer.
        curr_idx = dates.index(selected_date)
        prev_meta = {}
        if curr_idx + 1 < len(dates):
            prev_meta = history_data.get(dates[curr_idx + 1], {}).get("meta", {})

        def _pct_change(curr, prev):
            try:
                curr_v = float(curr)
                prev_v = float(prev)
            except (TypeError, ValueError):
                return None
            if not prev_v:
                return None
            return (curr_v - prev_v) / prev_v * 100.0

        deltas = dict(latest_nav.get("deltas") or {})
        deltas.setdefault("fund_net_assets_pct", _pct_change(meta.get("fund_size"), prev_meta.get("fund_size")))
        deltas.setdefault("nav_pct", _pct_change(meta.get("nav"), prev_meta.get("nav")))
        deltas.setdefault("closing_price_pct", _pct_change(meta.get("closing_price"), prev_meta.get("closing_price")))
        deltas.setdefault("outstanding_units_pct", _pct_change(meta.get("outstanding_units"), prev_meta.get("outstanding_units")))

        def _fmt_pct(value):
            return "N/A" if value is None else f"{value:+.2f}%"

        def _fmt_money_yi(value):
            return "N/A" if not value else f"{value / 100000000:,.0f} 億"

        def _fmt_units_yi(value):
            return "N/A" if not value else f"{value / 100000000:,.2f} 億股"

        if latest_nav or meta.get("fund_size") or meta.get("nav") or meta.get("outstanding_units"):
            fund_size = latest_nav.get("fund_net_assets", meta.get("fund_size"))
            nav = latest_nav.get("nav", meta.get("nav"))
            close_price = latest_nav.get("closing_price", meta.get("closing_price"))
            premium = None
            premium_pct = None
            if nav and close_price:
                premium = float(close_price) - float(nav)
                premium_pct = premium / float(nav) * 100.0
            else:
                premium = latest_nav.get("premium_discount", meta.get("premium_discount"))
                premium_pct = latest_nav.get("premium_discount_pct", meta.get("premium_discount_pct"))
            units = latest_nav.get("outstanding_units", meta.get("outstanding_units"))

            metric_cols = st.columns(5)
            with metric_cols[0]:
                st.metric("基金規模 (TWD)", _fmt_money_yi(fund_size), delta=_fmt_pct(deltas.get("fund_net_assets_pct")), delta_color=delta_color_param)
            with metric_cols[1]:
                st.metric("基金淨值", f"{nav:.2f}" if nav else "N/A", delta=_fmt_pct(deltas.get("nav_pct")), delta_color=delta_color_param)
            with metric_cols[2]:
                st.metric("收盤市價", f"{close_price:.2f}" if close_price else "N/A", delta=_fmt_pct(deltas.get("closing_price_pct")), delta_color=delta_color_param)
            with metric_cols[3]:
                st.metric("在外流通單位", _fmt_units_yi(units), delta=_fmt_pct(deltas.get("outstanding_units_pct")), delta_color=delta_color_param)
            with metric_cols[4]:
                premium_value = "N/A" if premium_pct is None else f"{premium_pct:+.2f}%"
                premium_color = PROFIT_COLOR if (premium_pct or 0) >= 0 else LOSS_COLOR
                st.markdown(
                    f"""
                    <div style="padding: 0.25rem 0;">
                        <div style="font-size: 0.875rem; color: rgba(250,250,250,0.85); margin-bottom: 0.35rem;">折溢價</div>
                        <div style="font-size: 2.75rem; line-height: 1.2; color: {premium_color}; font-weight: 400;">{premium_value}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        if holdings:
            df_h = pd.DataFrame(holdings)
            df_h = df_h.sort_values(by="weight_pct", ascending=False).reset_index(drop=True)
            df_h["stock_label"] = df_h["id"].astype(str) + " " + df_h["name"]

            top_n = st.slider(
                T(lang, "Show Top N Holdings", "顯示前 N 大持股"),
                min_value=5,
                max_value=len(df_h),
                value=min(20, len(df_h)),
                step=5,
                key="passive_etf_top_n",
            )
            df_top = df_h.head(top_n)

            fig = px.bar(
                df_top,
                x="stock_label",
                y="weight_pct",
                text="weight_pct",
                color_discrete_sequence=[NEUTRAL_PURPLE],
                title=f"{T(lang, f'Top {top_n} Holdings', f'前 {top_n} 大持股')} ({selected_date})",
                labels={"stock_label": T(lang, "Stock", "股票"), "weight_pct": T(lang, "Weight (%)", "權重 (%)")},
            )
            fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
            fig.update_layout(
                xaxis_title="",
                yaxis_title=T(lang, "Weight (%)", "權重 (%)"),
                height=500,
                margin=dict(b=100),
            )
            st.plotly_chart(fig, use_container_width=True)

            df_show = df_h[["id", "name", "weight_pct", "shares"]].copy()
            df_show.columns = [
                T(lang, "Stock ID", "代號"),
                T(lang, "Stock Name", "名稱"),
                T(lang, "Weight (%)", "權重 (%)"),
                T(lang, "Holdings (shares)", "持股 (股)"),
            ]
            st.dataframe(df_show, width="stretch")
        else:
            st.info(T(lang, "No holdings data for this date.", "此日期無持股資料。"))

