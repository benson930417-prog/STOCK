from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "summary_v2.csv"
PDF = ROOT / "strategy_experiment_v2_trader_summary_zh_tw.pdf"
PREVIEW = ROOT / "strategy_experiment_v2_trader_summary_zh_tw_preview.png"


def pct(value: float) -> str:
    return "" if pd.isna(value) else f"{value:.1f}%"


def num(value: float) -> str:
    return "" if pd.isna(value) else f"{value:.2f}"


def build_html() -> str:
    summary = pd.read_csv(SUMMARY).sort_values(["ticker", "years", "selection"])
    main_rows: list[str] = []
    verdict_rows: list[str] = []

    for ticker in ["0050", "SPY", "QQQ"]:
        for years in [5, 10]:
            sub = summary[(summary.ticker.astype(str) == ticker) & (summary.years == years)]
            if sub.empty:
                continue
            all_in = sub[sub.selection == "all_in"].iloc[0]
            dca = sub[sub.selection == "dca_5pct_monthly"].iloc[0]
            robust = sub[sub.selection == "best_robust"].iloc[0]
            main_rows.append(
                f"""
                <tr>
                  <td>{ticker}</td><td>{years}年</td>
                  <td>{pct(all_in.holdout_cagr_pct)}</td><td>{num(all_in.holdout_sharpe)}</td><td>{pct(all_in.holdout_max_dd_pct)}</td>
                  <td>{pct(dca.holdout_cagr_pct)}</td><td>{num(dca.holdout_sharpe)}</td><td>{pct(dca.holdout_max_dd_pct)}</td>
                  <td>{pct(robust.holdout_cagr_pct)}</td><td>{num(robust.holdout_sharpe)}</td><td>{pct(robust.holdout_max_dd_pct)}</td>
                </tr>
                """
            )

            if robust.holdout_cagr_pct >= all_in.holdout_cagr_pct and robust.holdout_sharpe > all_in.holdout_sharpe:
                verdict = "戰術規則在這段測試中值得注意：風險調整後報酬較好，而且沒有犧牲年化報酬。"
            elif robust.holdout_sharpe > all_in.holdout_sharpe and robust.holdout_max_dd_pct > all_in.holdout_max_dd_pct:
                verdict = "戰術規則有降低波動與回撤，但買進持有在純報酬上仍然很難被打敗。"
            else:
                verdict = "買進持有仍然很有競爭力；只有當你更重視降低回撤時，戰術規則才有明確用途。"
            if dca.holdout_max_dd_pct > all_in.holdout_max_dd_pct:
                verdict += " 定期定額在這裡也讓最大回撤變小。"
            verdict_rows.append(f"<tr><td>{ticker}</td><td>{years}年</td><td>{html.escape(verdict)}</td></tr>")

    css = """
      @page { size: Letter; margin: 0.55in; }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Microsoft JhengHei", "PingFang TC", "Noto Sans TC", Arial, sans-serif;
        color: #111827;
        background: white;
        font-size: 13px;
        line-height: 1.58;
      }
      .page { page-break-after: always; }
      .page:last-child { page-break-after: auto; }
      h1 { font-size: 31px; line-height: 1.16; margin: 0 0 10px; letter-spacing: 0; }
      h2 { font-size: 21px; margin: 22px 0 10px; border-top: 1px solid #d1d5db; padding-top: 16px; }
      h3 { font-size: 16px; margin: 16px 0 7px; }
      p { margin: 0 0 10px; }
      .subtitle { color: #4b5563; font-size: 13.5px; margin-bottom: 18px; }
      .callout { background: #f3f4f6; border: 1.5px solid #2563eb; padding: 12px 14px; margin: 14px 0; }
      .callout strong { display: block; font-size: 15px; margin-bottom: 5px; }
      .orange { border-color: #f97316; }
      .green { border-color: #10b981; }
      .grid4 { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid #d1d5db; margin: 14px 0; }
      .grid4 > div { padding: 10px; border-right: 1px solid #d1d5db; }
      .grid4 > div:last-child { border-right: 0; }
      .grid4 b { display: block; background: #111827; color: white; margin: -10px -10px 8px; padding: 8px 10px; text-align: center; }
      table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; table-layout: fixed; }
      th { background: #111827; color: white; font-weight: 700; }
      th, td { border: 1px solid #d1d5db; padding: 7px 8px; vertical-align: top; word-wrap: break-word; }
      tbody tr:nth-child(even) td { background: #f9fafb; }
      .small { color: #4b5563; font-size: 12px; }
      .footer { position: fixed; bottom: 0.2in; left: 0.55in; right: 0.55in; color: #6b7280; font-size: 10px; display: flex; justify-content: space-between; }
      .metric th:nth-child(1), .metric td:nth-child(1) { width: 16%; }
      .metric th:nth-child(2), .metric td:nth-child(2) { width: 38%; }
      .results { font-size: 10.2px; }
      .results th, .results td { padding: 5px 5px; }
      .verdict th:nth-child(1), .verdict td:nth-child(1) { width: 10%; }
      .verdict th:nth-child(2), .verdict td:nth-child(2) { width: 10%; }
    """

    return f"""<!doctype html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>策略實驗 V2：新手交易者摘要</title><style>{css}</style></head>
<body>
  <div class="footer"><span>策略實驗 V2 - 新手交易者摘要</span><span>strategy_experiment_v2</span></div>
  <section class="page">
    <h1>策略實驗 V2：新手交易者摘要</h1>
    <p class="subtitle">根據 strategy_experiment_v2 的 0050、SPY、QQQ 回測輸出整理。產生日期：2026-05-25。本文件是研究摘要，不是投資建議。</p>
    <div class="callout"><strong>一句話結論</strong>買進持有仍然是最重要的基準。定期定額通常讓過程比較好承受。戰術型逢低買進規則有時能改善 Sharpe 與最大回撤，但常常會犧牲部分長期複利。這份實驗真正提醒我們的是：控風險有成本，所以必須誠實衡量這個成本。</div>
    <div class="grid4">
      <div><b>對比</b>用深色標題與藍色重點框，把結論和表格數字分開。</div>
      <div><b>重複</b>每段結果都固定看 CAGR、Sharpe、最大回撤。</div>
      <div><b>對齊</b>三種策略並排呈現，方便直接比較。</div>
      <div><b>親近</b>指標解釋放在結果附近，新手不用來回找定義。</div>
    </div>
    <h2>這次到底測了什麼</h2>
    <p>每一檔 ETF 都獨立測試 5 年與 10 年。實驗比較三大類方法：一次投入買進持有、每月投入 5% 的定期定額，以及大量戰術規則。戰術規則會根據價格下跌、反彈、距離均線、RSI 與執行頻率，在現金與 ETF 之間調整部位。</p>
    <p>這份摘要重點看 holdout 結果，也就是資料後段的表現。這比只看全期間績效更嚴格，因為它在問：某個規則被挑出來之後，後面是否仍然表現穩定。</p>
    <h2>指標怎麼讀</h2>
    <table class="metric"><thead><tr><th>指標</th><th>白話意思</th><th>新手該怎麼看</th></tr></thead><tbody>
      <tr><td>CAGR</td><td>年化成長率。</td><td>越高越好，但前提是你真的能承受中間的下跌。</td></tr>
      <tr><td>Sharpe</td><td>每承擔一單位波動，換到多少報酬。</td><td>大於 1 算不錯；越高通常代表報酬比較平順，但不等於保證安全。</td></tr>
      <tr><td>最大回撤</td><td>從高點跌到低點的最大跌幅。</td><td>這是最考驗人性的數字。你撐不住，它就不是你的策略。</td></tr>
      <tr><td>交易次數</td><td>買賣或調整部位的次數。</td><td>越少通常越單純，也比較少稅費、滑價與執行錯誤。</td></tr>
    </tbody></table>
  </section>
  <section class="page">
    <h1>主要結果</h1>
    <p class="subtitle">以下為 holdout 結果。戰術欄位使用 best_robust，不是單純挑最漂亮、最可能過度擬合的最高分策略。</p>
    <table class="results"><thead><tr><th>ETF</th><th>期間</th><th>All-in CAGR</th><th>All-in Sharpe</th><th>All-in 回撤</th><th>DCA CAGR</th><th>DCA Sharpe</th><th>DCA 回撤</th><th>戰術 CAGR</th><th>戰術 Sharpe</th><th>戰術回撤</th></tr></thead><tbody>{"".join(main_rows)}</tbody></table>
    <div class="callout green"><strong>讀表重點</strong>如果戰術策略 CAGR 稍低，但最大回撤小很多、Sharpe 高很多，代表它可能適合重視穩定度的人。反過來，如果買進持有 CAGR 明顯較高，而且回撤你也承受得住，那簡單反而可能是優勢。</div>
    <h2>各市場解讀</h2>
    <table class="verdict"><thead><tr><th>ETF</th><th>期間</th><th>解讀</th></tr></thead><tbody>{"".join(verdict_rows)}</tbody></table>
  </section>
  <section class="page">
    <h1>新手交易者應該學到什麼</h1>
    <h3>1. 真正的敵人不是策略太笨，而是你在回撤時放棄策略。</h3>
    <p>高 CAGR 加上 -30% 回撤，在數學上可能很漂亮，但在心理上可能完全拿不住。DCA 和戰術規則有價值，很多時候不是因為它們能神準預測，而是因為它們讓過程比較不痛。</p>
    <h3>2. 買進持有不是偷懶，而是所有策略必須打敗的基準。</h3>
    <p>對 SPY、QQQ 這種廣泛 ETF 來說，買進持有的長期複利非常難被打敗。戰術規則有時能提高 Sharpe 或降低回撤，但代價常常是少賺一部分 CAGR。</p>
    <h3>3. 看起來較可靠的戰術規則通常簡單、低頻、耐心。</h3>
    <p>比較可信的規則多半是逢低慢慢買、不要一直交易。那些 Sharpe 很高但 CAGR 很低的規則，可以當成風控想法，但不一定適合作為累積財富的主策略。</p>
    <h3>4. 定期定額是一種行為工具。</h3>
    <p>每月 5% DCA 很少讓報酬最大化，但它常常能降低最大回撤，也能降低一次買在高點的壓力。對新手來說，這種可執行性可能比多幾個百分點的理論 CAGR 更重要。</p>
    <h3>5. 不要在故事不合理時相信最佳化。</h3>
    <p>一個好策略要能說得通、在 holdout 仍然穩定、交易次數不要太誇張，而且不能只靠某一次崩盤或反彈剛好賺到。只會在回測中漂亮的規則，可能只是過度擬合。</p>
    <div class="callout orange"><strong>比較穩健的新手政策</strong>把買進持有或定期定額當作核心。若要加入戰術規則，把它當成小型風控層，而不是整個投資計畫的替代品。進場、出場、什麼時候不動，都要事先寫清楚。壞月份還做不到的規則，就是太複雜。</div>
  </section>
  <section class="page">
    <h1>重要限制</h1>
    <p>這是歷史回測，不是未來預測。它使用本地 v2 實驗中的 ETF 價格與策略定義，只能說明過去在這些條件下發生了什麼。</p>
    <p>成本與稅務會影響結果。0050 有納入台股 ETF 賣出交易稅假設，SPY/QQQ 則沒有賣出稅；但真實交易還有手續費、匯差、買賣價差、股息稅與個人稅務。</p>
    <p>本次刻意沒有換匯。這符合「比較各 ETF 內部策略行為」的目的，但它不能回答台灣投資人實際換成台幣後的完整報酬。</p>
    <p>大量測試會帶來過度擬合風險。這次測了 78,341 組策略規格，速度很快也很危險。最漂亮的規則可能只是最幸運的規則，所以摘要刻意強調 robust 與 holdout，而不是只看最高數字。</p>
    <h2>最後 takeaway</h2>
    <p>對新手來說，證據仍然支持一個無聊但強的核心：廣泛 ETF、簡單執行、低頻交易，以及能在難看月份繼續遵守的計畫。戰術擇時可以是風控工具，但不應該被想像成取代長期投資紀律的魔法。</p>
  </section>
</body></html>"""


def main() -> None:
    html_doc = build_html()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 1400}, device_scale_factor=1)
        page.set_content(html_doc, wait_until="load")
        page.screenshot(path=str(PREVIEW), full_page=False)
        page.pdf(
            path=str(PDF),
            format="Letter",
            print_background=True,
            margin={"top": "0.55in", "right": "0.55in", "bottom": "0.55in", "left": "0.55in"},
        )
        browser.close()
    print(PDF)


if __name__ == "__main__":
    main()
