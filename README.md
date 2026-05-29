# STOCK

STOCK is a self-hosted ETF and market-monitoring system. It has three user-facing surfaces:

- A Streamlit dashboard for ETF holdings, quote status, ETF comparison, master holdings, and market pulse.
- A LINE bot webhook for quote cards, daily reports, market charts, and admin commands.
- A Playwright/FastAPI chart service that captures TradingView text quotes and chart snapshots for the LINE bot.

The production server path used by all service files is `/home/ubuntu/STOCK`.

## Runtime Map

| Runtime | Entry point | systemd unit | Purpose |
|---|---|---|---|
| Streamlit dashboard | `app.py` | `stock-dashboard.service` | Browser UI for ETF data, master holdings, ETF comparison, and market pulse. |
| LINE webhook | `api/webhook.py` | `stock-webhook.service` | Handles LINE messages, returns text/cards/images, and routes admin commands. |
| TradingView chart API | `scripts/chart_service.py` | `stock-chart.service` | Keeps a browser alive and exposes `/market-text`, `/snapshot`, and `/market-debug` on `127.0.0.1:5005`. |
| Daily fetch | `scripts/update_and_notify.sh` | `stock-fetch-1730-tw.timer` + `.service` | Pulls latest code, refreshes data, updates benchmark DB, commits/pushes, broadcasts reports, and emails admin summary. |
| ETF quote monitors | `scripts/monitor_etf_quotes.py` | `stock-quote-monitor-*.service` | Refreshes per-ETF quote caches used by the dashboard and LINE cards. |
| Gold monitor | `scripts/monitor_gold_quote.py` | `stock-gold-monitor.service` | Refreshes TradingView GOLD quote cache. |
| Master holdings monitor | `scripts/monitor_master_holding.py` | `stock-master-holding-monitor.service` | Refreshes the expanded portfolio/master-holding cache. |
| OCI boot firewall reset | system iptables | `oci-firewall.service` | Host boot helper that clears restrictive iptables rules so dashboard/webhook services are reachable after reboot. |

## Repository Structure

```text
.
├── app.py                         # Streamlit dashboard entry point
├── requirements.txt               # Single Python dependency list for all runtimes
├── api/
│   └── webhook.py                 # LINE bot Flask webhook
├── data/                          # Tracked source/history state plus ignored generated caches
├── scripts/                       # Fetchers, monitors, renderers, chart API, daily orchestrator
│   └── etf_benchmark/             # Local SQLite ETF benchmark pipeline
├── services/                      # systemd unit templates for the OCI server
└── src/ui/                        # Streamlit tab modules
```

## Root Files

| File | Purpose |
|---|---|
| `.gitignore` | Keeps generated images, quote caches, logs, SQLite DBs, virtualenvs, and local research sandboxes out of Git. |
| `app.py` | Main Streamlit app. Loads tracked JSON data, quote caches, secrets, authentication, translations, and renders all tabs. |
| `requirements.txt` | Dependency source of truth for dashboard, webhook, chart service, fetchers, monitors, and benchmark jobs. |
| `README.md` | This handoff and operations guide. |

## Dashboard

`app.py` renders the main dashboard and delegates major sections to `src/ui/`.

| File | Purpose |
|---|---|
| `src/__init__.py` | Marks `src` as an import package. |
| `src/ui/__init__.py` | Marks UI helpers as an import package. |
| `src/ui/etf_tab.py` | Active/passive ETF dashboard views and daily operation report UI. |
| `src/ui/etf_compare_tab.py` | ETF comparison tab backed by the local `data/etf_bench/etf_bench.sqlite` database. |
| `src/ui/market_pulse_tab.py` | Market pulse tab using ETF benchmark/index history and regime calculations. |

Dashboard authentication uses `VIEW_PASSWORD` and `ADMIN_PASSWORD` from Streamlit secrets, environment variables, or `/home/ubuntu/.stock_secrets` depending on the runtime.

## LINE Webhook

`api/webhook.py` is a Flask app listening on `0.0.0.0:8080` when executed directly. It requires LINE credentials and calls the chart service through `CHART_SERVICE_URL`, defaulting to `http://127.0.0.1:5005`.

Common LINE commands:

| Command | Response |
|---|---|
| `981`, `988`, `0050`, `830`, `878`, `891`, `9805`, `9820` | ETF quote card/report for the mapped ETF. |
| `吳大師` | Master holding portfolio card. |
| `市場脈動` | Latest generated market pulse image. |
| `油價` | WTI and Brent TradingView text quotes plus charts. |
| `匯率` | USD/TWD, USD/CHF, and USD/JPY TradingView text quotes plus charts. |
| `債券` or `債卷` | US 10-year yield text quote and chart. |
| `黃金` / `gold` / `xau` / `xauusd` | GOLD text quote and chart. |
| `操作日報 981`, `操作日報 988` | Re-render and broadcast an active ETF operation report. |
| `id` | Return LINE user/group/room identifiers. |
| `admin` | Show admin command help. |

Errors from TradingView are intentionally returned as explicit error messages. There is no silent non-TradingView fallback for market text/chart commands.

## TradingView Chart Service

`scripts/chart_service.py` is a FastAPI service on `127.0.0.1:5005`. It launches Playwright Chromium once and reuses the browser for requests.

Endpoints:

| Endpoint | Method | Body | Purpose |
|---|---|---|---|
| `/market-text` | POST | `{"key":"oil"}` | Return formatted quote text for one market key. |
| `/snapshot` | POST | `{"key":"oil"}` | Capture a TradingView chart image into `data/images/`. |
| `/market-debug` | POST | `{"key":"oil"}` | Return raw debug information for parser troubleshooting. |

Market keys:

| Key | Market |
|---|---|
| `oil` | WTI crude oil |
| `brent` | Brent crude oil |
| `bond` | US 10-year yield, using TradingView `CBOT_MINI-10Y1!` for text performance and charting |
| `gold` | GOLD spot |
| `usdtwd` | USD/TWD |
| `usdchf` | USD/CHF |
| `usdjpy` | USD/JPY |

Manual checks on the server:

```bash
curl -s -X POST http://127.0.0.1:5005/market-text \
  -H "Content-Type: application/json" \
  -d '{"key":"bond"}'

curl -s -X POST http://127.0.0.1:5005/snapshot \
  -H "Content-Type: application/json" \
  -d '{"key":"bond"}'
```

## Scripts

| File | Purpose |
|---|---|
| `scripts/admin_email.py` | Sends daily run summaries through Gmail SMTP. Exits successfully when email secrets are missing so the daily job is not blocked. |
| `scripts/chart_service.py` | TradingView quote/chart FastAPI service used by the LINE webhook. |
| `scripts/fetch_etf_00403A.py` | Fetches official 00403A holdings/NAV data from Unified's `fundCode=63YTW` Excel endpoint. |
| `scripts/fetch_etf_00981A.py` | Fetches official 00981A holdings/NAV data into tracked history/log JSON. |
| `scripts/fetch_etf_00988A.py` | Fetches official 00988A holdings/NAV data from Unified's `fundCode=61YTW` Excel endpoint. |
| `scripts/fetch_passive_0050.py` | Fetches 0050 passive ETF holdings/history. |
| `scripts/fetch_passive_00830.py` | Fetches 00830 passive ETF holdings/history from the official Cathay source. |
| `scripts/fetch_passive_00878.py` | Fetches 00878 passive ETF holdings/history from the official Cathay source. |
| `scripts/fetch_passive_00891.py` | Fetches 00891 passive ETF holdings/history from CTBC's official ETF API. |
| `scripts/fetch_passive_009805.py` | Fetches 009805 passive ETF holdings/history. |
| `scripts/fetch_passive_009820.py` | Fetches 009820 passive ETF holdings/history. |
| `scripts/generate_etf_summary.py` | Builds daily ETF summary images for LINE broadcast. |
| `scripts/generate_market_pulse_summary.py` | Renders the market pulse summary image served by the LINE `市場脈動` command. |
| `scripts/generate_quote_card.py` | Shared quote-card image renderer for ETF/master-holding views. |
| `scripts/master_holding_quote_card.py` | Expands ETF holdings into the configured master portfolio and renders/caches its quote card. |
| `scripts/master_manual_positions.py` | Manual position data/helpers for the master portfolio. |
| `scripts/monitor_etf_quotes.py` | Long-running quote cache daemon for one ETF ticker. |
| `scripts/monitor_gold_quote.py` | Long-running GOLD quote monitor. |
| `scripts/monitor_master_holding.py` | Long-running master-holding cache monitor. |
| `scripts/rebroadcast_line.py` | Manual helper for rebroadcasting generated LINE report images. |
| `scripts/setup_rich_menu.py` | Creates/updates the LINE rich menu. |
| `scripts/update_and_notify.sh` | Daily orchestrator for fetchers, benchmark refresh, market pulse image, Git update, LINE broadcast, and admin email. |

00988A global-holding quote handling: the holdings sheet uses global market suffixes such as `NVDA US`, `7203 JP`, or Hong Kong/Taiwan codes. `scripts/monitor_etf_quotes.py` normalizes those into Yahoo Finance symbols (`NVDA`, `7203.T`, `0005.HK`, `2330.TW`, etc.) and applies the existing exchange-session watcher logic.

00891 CTBC handling: `scripts/fetch_passive_00891.py` first requests CTBC's public `home/AuthToken`, confirms `CNO=88182265` maps to internal `FID=E0017`, then reads `etf/ETFHoldingWeight`. Only the stock holding block is stored for quote monitoring; futures/margin/cash blocks are left out of the quote card because they do not map to Yahoo equity quotes.

TSMC night-session handling: when a holding maps to `2330` / `2330.TW` during the QFF1! night futures window, the monitor uses TradingView's printed `TAIFEX:QFF1!` change percent. It does not calculate the percent against the 2330.TW day close, because that mixes different markets and baselines.

## ETF Benchmark Pipeline

The ETF comparison tab reads a local SQLite database generated under `data/etf_bench/`. The database is intentionally ignored by Git and rebuilt on each host.

| File | Purpose |
|---|---|
| `scripts/etf_benchmark/__init__.py` | Package marker. |
| `scripts/etf_benchmark/db.py` | Streamlit-cached SQLite read helpers. |
| `scripts/etf_benchmark/seed_tpex_etfs.csv` | Seed list for TPEx ETFs not covered by the TWSE source. |
| `scripts/etf_benchmark/step1_universe.py` | Builds `data/etf_bench/universe.csv`. |
| `scripts/etf_benchmark/step2_schema.py` | Creates/resets SQLite schema. |
| `scripts/etf_benchmark/step3_backfill.py` | Downloads prices/dividends/splits through yfinance. Use `--incremental` for daily refresh. |
| `scripts/etf_benchmark/step4_verify.py` | Audits adjusted-close returns against a transparent cash-dividend total-return model. |
| `scripts/etf_benchmark/step5_verify_nav.py` | Optional NAV diagnostic for issuer NAV snapshots. |
| `scripts/etf_benchmark/step6_regimes.py` | Builds market regime tags used by market pulse/benchmark views. |

First-time benchmark setup:

```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
python -m scripts.etf_benchmark.step1_universe
python -m scripts.etf_benchmark.step2_schema --reset
python -m scripts.etf_benchmark.step3_backfill
python -m scripts.etf_benchmark.step4_verify
python -m scripts.etf_benchmark.step6_regimes
```

Daily refresh uses:

```bash
python -m scripts.etf_benchmark.step3_backfill --incremental
python -m scripts.etf_benchmark.step4_verify
python -m scripts.etf_benchmark.step5_verify_nav
python -m scripts.etf_benchmark.step6_regimes
```

## Data Directory

Tracked files in `data/` are source/history state that should move with the repo:

| File pattern | Purpose |
|---|---|
| `data/etf_00403A_history.json`, `data/etf_00981A_history.json`, `data/etf_00988A_history.json` | Active ETF official history snapshots. |
| `data/etf_00403A_log.json`, `data/etf_00981A_log.json`, `data/etf_00988A_log.json` | Active ETF fetch logs/status. |
| `data/passive_*_history.json` | Passive ETF official history snapshots for 0050, 00830, 00878, 00891, 009805, and 009820. |
| `data/passive_*_log.json` | Passive ETF fetch logs/status. |
| `data/master_manual_positions.json` | Manual master portfolio positions. |
| `data/master_meta.json` | Master portfolio metadata/state. |
| `data/master_trades.csv` | Manual trade ledger for the master portfolio. |

Ignored generated data:

| Path | Producer |
|---|---|
| `data/images/` | `chart_service.py`, quote-card renderers, webhook responses. |
| `data/summaries/` | `generate_market_pulse_summary.py` and report generators. |
| `data/quote_cache/` | Quote monitor services. |
| `data/fonts/` | Local font assets if installed on the server. |
| `data/etf_bench/*.sqlite` | ETF benchmark pipeline. |

## Services

All service templates live in `services/` and assume:

- Linux user: `ubuntu`
- Repository: `/home/ubuntu/STOCK`
- Virtualenv: `/home/ubuntu/STOCK/venv`
- Secrets: `/home/ubuntu/.stock_secrets`

| File | Installed unit | Purpose |
|---|---|---|
| `services/stock-dashboard.service` | `stock-dashboard.service` | Streamlit dashboard on port 8501. |
| `services/stock-webhook.service` | `stock-webhook.service` | LINE webhook on port 8080. |
| `services/stock-chart.service` | `stock-chart.service` | TradingView Playwright API on `127.0.0.1:5005`. |
| `services/oci-firewall.service` | `oci-firewall.service` | Boot-time iptables reset used on OCI so public services remain reachable after reboot. |
| `services/stock-fetch-1730-tw.service` | `stock-fetch-1730-tw.service` | One-shot daily fetch/orchestration job. |
| `services/stock-fetch-1730-tw.timer` | `stock-fetch-1730-tw.timer` | Runs the daily job at 09:30 UTC / 17:30 Taiwan time. |
| `services/stock-gold-monitor.service` | `stock-gold-monitor.service` | GOLD quote monitor. |
| `services/stock-master-holding-monitor.service` | `stock-master-holding-monitor.service` | Master holdings monitor. |
| `services/stock-quote-monitor-00403a.service` | `stock-quote-monitor-00403a.service` | 00403A quote monitor. |
| `services/stock-quote-monitor-0050.service` | `stock-quote-monitor-0050.service` | 0050 quote monitor. |
| `services/stock-quote-monitor-00830.service` | `stock-quote-monitor-00830.service` | 00830 quote monitor. |
| `services/stock-quote-monitor-00878.service` | `stock-quote-monitor-00878.service` | 00878 quote monitor. |
| `services/stock-quote-monitor-00891.service` | `stock-quote-monitor-00891.service` | 00891 quote monitor. |
| `services/stock-quote-monitor-009805.service` | `stock-quote-monitor-009805.service` | 009805 quote monitor. |
| `services/stock-quote-monitor-00981a.service` | `stock-quote-monitor-00981a.service` | 00981A quote monitor. |
| `services/stock-quote-monitor-00988a.service` | `stock-quote-monitor-00988a.service` | 00988A quote monitor. |
| `services/stock-quote-monitor-009820.service` | `stock-quote-monitor-009820.service` | 009820 quote monitor. |

Install/update service templates:

```bash
cd /home/ubuntu/STOCK
sudo cp services/*.service services/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable stock-dashboard.service stock-webhook.service stock-chart.service
sudo systemctl enable oci-firewall.service
sudo systemctl enable stock-fetch-1730-tw.timer
sudo systemctl enable stock-gold-monitor.service stock-master-holding-monitor.service
sudo systemctl enable stock-quote-monitor-0050.service stock-quote-monitor-00830.service
sudo systemctl enable stock-quote-monitor-00878.service stock-quote-monitor-00891.service
sudo systemctl enable stock-quote-monitor-009805.service
sudo systemctl enable stock-quote-monitor-00403a.service stock-quote-monitor-00981a.service stock-quote-monitor-00988a.service stock-quote-monitor-009820.service
```

Restart common production services after code changes:

```bash
sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

Restart all monitors:

```bash
sudo systemctl restart stock-gold-monitor.service stock-master-holding-monitor.service
sudo systemctl restart stock-quote-monitor-0050.service stock-quote-monitor-00830.service
sudo systemctl restart stock-quote-monitor-00878.service stock-quote-monitor-00891.service
sudo systemctl restart stock-quote-monitor-009805.service
sudo systemctl restart stock-quote-monitor-00403a.service stock-quote-monitor-00981a.service stock-quote-monitor-00988a.service stock-quote-monitor-009820.service
```

## Server Setup

First-time host setup:

```bash
cd /home/ubuntu
git clone https://github.com/benson930417-prog/STOCK.git
cd /home/ubuntu/STOCK
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -q
python -m playwright install chromium
```

Create `/home/ubuntu/.stock_secrets`.

Minimum server pattern used by the webhook, daily LINE broadcast, and admin email:

```bash
LINE_TOKEN="..."
LINE_UID="..."
export LINE_CHANNEL_SECRET="..."
export ADMIN_EMAIL="..."
export GMAIL_FROM="..."
export GMAIL_APP_PASSWORD="..."
```

Optional values used by the Streamlit dashboard's admin/GitHub features:

```bash
export VIEW_PASSWORD="..."
export ADMIN_PASSWORD="..."
export GITHUB_TOKEN="..."
export GITHUB_REPO="benson930417-prog/STOCK"
export GITHUB_BRANCH="main"
```

`scripts/update_and_notify.sh` does not read `GITHUB_TOKEN`; it runs `git pull origin main --rebase --autostash` and `git push origin main`, so server Git authentication must already be configured through the Git remote/credential helper. `LINE_CHANNEL_ACCESS_TOKEN` is also optional because the webhook maps `LINE_TOKEN` to that name internally.

Secure it:

```bash
chmod 600 /home/ubuntu/.stock_secrets
```

Deploy services:

```bash
sudo cp services/*.service services/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oci-firewall.service
sudo systemctl enable --now stock-chart.service stock-webhook.service stock-dashboard.service
sudo systemctl enable --now stock-fetch-1730-tw.timer
```

## Standard Deployment

Use this after pulling new code on the server:

```bash
cd /home/ubuntu/STOCK
git pull origin main --rebase --autostash
source venv/bin/activate
pip install -r requirements.txt -q
sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

If service files changed:

```bash
sudo cp services/*.service services/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

## Daily Job Flow

`stock-fetch-1730-tw.timer` runs at 09:30 UTC, which is 17:30 Taiwan time.

`scripts/update_and_notify.sh` performs:

1. Load `/home/ubuntu/.stock_secrets`.
2. Pull latest Git changes with rebase/autostash.
3. Install dependencies from `requirements.txt`.
4. Run all active/passive ETF fetchers requested by the service arguments.
5. Refresh ETF benchmark SQLite data and regime tags.
6. Generate the market pulse image.
7. Commit and push changed tracked data.
8. Broadcast active ETF reports through LINE when new active ETF data exists.
9. Send admin email summary with success/failure details.

Manual run:

```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
bash scripts/update_and_notify.sh 00403A 00981A 00988A 0050 00830 00878 00891 009805 009820
```

## Rich Menu

Run this when LINE rich-menu buttons change:

```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
source /home/ubuntu/.stock_secrets
python scripts/setup_rich_menu.py
```

The rich menu uses three LINE rich-menu aliases. Page 1 is fast market/macro, Page 2 is the primary ETF watchlist, and Page 3 is ETF overflow. Navigation convention is fixed: previous page is bottom-left, next page is bottom-right, and the last page uses bottom-right `首頁` until another page is needed.

## ETF Maintenance Playbook For Agents

This section is written for future AI/code agents. Treat an ETF add/delete as a cross-system change, not as a single fetcher edit. The same ticker appears in the dashboard, LINE webhook, rich menu, quote monitors, daily fetch orchestration, benchmark seed logic, master holding expansion, service templates, generated/tracked data, and this README.

Use consistent casing:

- Ticker in user-facing text/data: uppercase, e.g. `00403A`, `00981A`, `009820`.
- Quote-monitor service filename: lowercase suffix, e.g. `services/stock-quote-monitor-00403a.service`.
- LINE short command: compact numeric alias, e.g. `403`, `981`, `9820`.
- Active ETF data files: `data/etf_<TICKER>_history.json` and `data/etf_<TICKER>_log.json`.
- Passive ETF data files: `data/passive_<TICKER>_history.json` and `data/passive_<TICKER>_log.json`.

### Add One ETF

1. Classify the ETF.
   - Active ETFs with issuer operation reports use `scripts/fetch_etf_<TICKER>.py`, `data/etf_<TICKER>_*`, summary images, and LINE operation broadcasts.
   - Passive ETFs use `scripts/fetch_passive_<TICKER>.py`, `data/passive_<TICKER>_*`, and usually do not join active operation-report broadcasts.
   - Global-holding ETFs need quote normalization support in `scripts/monitor_etf_quotes.py` if holdings include non-Taiwan suffixes such as `US`, `JP`, `KS`, `HK`, or issuer-specific symbols.

2. Add the official fetcher.
   - Create `scripts/fetch_etf_<TICKER>.py` or `scripts/fetch_passive_<TICKER>.py`.
   - Fetch from the official issuer/API endpoint only. Do not invent holdings from Yahoo.
   - Write the same normalized history shape used by existing ETF files: date-keyed JSON with holdings containing at least `id`, `name`, and `weight_pct`.
   - Write a log JSON with fetch status so the daily email can say `NEW DATA FOUND`, `NO CHANGE`, or an explicit error.
   - Run the fetcher locally/server-side with the project venv: `./venv/bin/python scripts/fetch_etf_<TICKER>.py`.

3. Add tracked data files.
   - Commit the initial `data/etf_<TICKER>_history.json` and `data/etf_<TICKER>_log.json` for active ETFs.
   - Commit the initial `data/passive_<TICKER>_history.json` and `data/passive_<TICKER>_log.json` for passive ETFs.
   - Do not commit `data/images/`, `data/summaries/`, `data/quote_cache/`, or `data/etf_bench/*.sqlite`.

4. Wire daily fetch orchestration in `scripts/update_and_notify.sh`.
   - Add the ticker to the default `ETFS=(...)` list.
   - Add the ticker to the fetcher dispatch `case`.
   - For an active ETF, include it in the active ETF branch used for summary image generation and LINE broadcast.
   - Add the display name to the embedded Python `names = {...}` map used for daily LINE broadcasts.
   - Ensure failure details print the ETF fetch log clearly in the admin email.

5. Wire dashboard ETF views.
   - Add the ticker to `src/ui/etf_tab.py` ETF selectors/lists.
   - If the ETF is active, include it beside other active ETFs in active report sections.
   - If any display name appears in `app.py`, add the new ticker/name there too.
   - Run the dashboard after edits and verify the ETF page loads from tracked history, not quote cache alone.

6. Wire LINE webhook commands in `api/webhook.py`.
   - Add the ticker to `ETF_QUOTE_NAMES`.
   - Add the command parser alias, such as `403 -> 00403A`.
   - Add the command to admin/help text and any user-facing command list.
   - If it has operation reports, add support for `操作日報 <alias>`.
   - Keep errors explicit. Do not silently fall back to stale data when a quote/fetch fails.

7. Wire LINE rich menu in `scripts/setup_rich_menu.py`.
   - Place the ETF in the page layout according to current menu organization.
   - Preserve navigation convention: previous page bottom-left, next page bottom-right, and final page bottom-right `首頁` only when there is no future item/page.
   - Add/update the tap command to match webhook parsing.
   - Re-run `scripts/setup_rich_menu.py` on the server after deployment if the menu changed.

8. Add a quote monitor service.
   - Create `services/stock-quote-monitor-<ticker-lower>.service`.
   - The service should run `/home/ubuntu/STOCK/venv/bin/python /home/ubuntu/STOCK/scripts/monitor_etf_quotes.py <TICKER> --interval 60`.
   - Add it to README service tables, enable commands, restart commands, and Current File Inventory.
   - After deployment, copy services and enable it with systemd.

9. Verify quote monitor compatibility.
   - Run `./venv/bin/python scripts/monitor_etf_quotes.py <TICKER> --interval 60` once and stop after it writes `data/quote_cache/etf_<TICKER>_quotes.json`.
   - Check global holdings have correct `country`, `market_session`, `day_change_pct`, and no misleading stale fallback.
   - For markets without pre/post trading, closed status should be `已收盤`; reserve `盤後收` only for markets/instruments with post-market sessions.

10. Wire quote card rendering.
    - Add the display name to `scripts/generate_quote_card.py`.
    - Confirm LINE card generation works even when a constituent has no trade today; cards should still show a valid percent if the monitor has an exact current quote/change.

11. Wire active ETF summary reports when applicable.
    - Add the ticker/title to `scripts/generate_etf_summary.py`.
    - Add the ticker/name to `scripts/rebroadcast_line.py`.
    - For a brand-new ETF with only one history date, summary generation must not crash; it should render a meaningful first-day/no-prior-data state or skip cleanly with an explicit status.

12. Wire Master Wu / master holding expansion.
    - Add the ETF product name and ticker to `ETF_NAME_TO_TICKER` in both `app.py` and `scripts/master_holding_quote_card.py`.
    - Add aliases only if the trade ledger may contain old names. Put the intended display name last when building reverse maps so `ETF_TICKER_TO_NAME` resolves to the current name.
    - Add the ticker to the expansion allow-list in `app.py` and `scripts/master_holding_quote_card.py`.
    - Test that `全部成分股絕對權重` expands into underlying holdings and does not leave the ETF itself as a `直接持股` row.

13. Wire ETF benchmark.
    - Add the ticker to `scripts/etf_benchmark/step1_universe.py` required/seed logic when the ETF should appear in comparison/market-pulse data.
    - If needed, add it to `scripts/etf_benchmark/seed_tpex_etfs.csv`.
    - Rebuild or incrementally refresh benchmark data and run verification.

14. Update README.
    - Update Scripts table, Data Directory, Services table, enable/restart commands, Daily Job manual command, Manual Fetch commands, Current File Inventory, and any command/help examples.
    - If the ETF has special source behavior, document it in the Scripts notes.

15. Run verification before committing.
    - Syntax:
      ```bash
      python -m py_compile app.py api/webhook.py scripts/fetch_etf_<TICKER>.py scripts/monitor_etf_quotes.py scripts/generate_quote_card.py scripts/master_holding_quote_card.py scripts/setup_rich_menu.py
      bash -n scripts/update_and_notify.sh
      ```
    - Fetch:
      ```bash
      ./venv/bin/python scripts/fetch_etf_<TICKER>.py
      ```
    - Quote cache:
      ```bash
      ./venv/bin/python scripts/monitor_etf_quotes.py <TICKER> --interval 60
      ```
    - Master expansion:
      ```bash
      ./venv/bin/python scripts/master_holding_quote_card.py
      ```
    - Active report if applicable:
      ```bash
      ./venv/bin/python scripts/generate_etf_summary.py
      ```
    - Benchmark if applicable:
      ```bash
      ./venv/bin/python -m scripts.etf_benchmark.step1_universe
      ./venv/bin/python -m scripts.etf_benchmark.step3_backfill --incremental
      ./venv/bin/python -m scripts.etf_benchmark.step4_verify
      ./venv/bin/python -m scripts.etf_benchmark.step5_verify_nav
      ./venv/bin/python -m scripts.etf_benchmark.step6_regimes
      ```

16. Deployment commands after merging/pulling on the server.
    ```bash
    cd /home/ubuntu/STOCK
    git pull origin main --rebase --autostash
    source venv/bin/activate
    pip install -r requirements.txt -q
    sudo cp services/*.service services/*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now stock-quote-monitor-<ticker-lower>.service
    sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service stock-quote-monitor-<ticker-lower>.service
    ```
    If the rich menu changed:
    ```bash
    source /home/ubuntu/.stock_secrets
    ./venv/bin/python scripts/setup_rich_menu.py
    ```

### Delete One ETF

1. Remove it from daily orchestration.
   - Delete the ticker from `ETFS=(...)` in `scripts/update_and_notify.sh`.
   - Delete its fetcher dispatch branch.
   - Delete it from active summary/LINE broadcast lists and embedded name maps.

2. Remove LINE access.
   - Delete its display name and command parser alias from `api/webhook.py`.
   - Delete it from admin/help text.
   - Delete operation-report command support if it existed.
   - Delete or replace its rich-menu cell in `scripts/setup_rich_menu.py`.
   - Preserve page navigation rules after reshuffling.

3. Remove dashboard references.
   - Delete it from `src/ui/etf_tab.py` selectors/lists.
   - Delete display-name mappings and allow-lists in `app.py`.
   - Confirm the dashboard no longer expects its history/log/quote-cache files.

4. Remove Master Wu references.
   - Delete product-name aliases from `ETF_NAME_TO_TICKER` in both `app.py` and `scripts/master_holding_quote_card.py`.
   - Delete the ticker from ETF expansion allow-lists.
   - If the master trade ledger still contains the ETF, decide explicitly whether to keep it as a direct holding or remove/rename the trade data. Do not silently leave a deleted ETF in expansion logic.

5. Remove quote monitor service.
   - Delete `services/stock-quote-monitor-<ticker-lower>.service`.
   - Remove it from README service table, enable commands, restart commands, and Current File Inventory.
   - On the server:
     ```bash
     sudo systemctl disable --now stock-quote-monitor-<ticker-lower>.service
     sudo rm -f /etc/systemd/system/stock-quote-monitor-<ticker-lower>.service
     sudo systemctl daemon-reload
     ```

6. Remove fetcher and tracked data.
   - Delete `scripts/fetch_etf_<TICKER>.py` or `scripts/fetch_passive_<TICKER>.py`.
   - Delete tracked `data/etf_<TICKER>_history.json` and `data/etf_<TICKER>_log.json`, or `data/passive_<TICKER>_history.json` and `data/passive_<TICKER>_log.json`.
   - Do not chase ignored generated files unless the user explicitly wants local cleanup. `data/quote_cache/`, `data/images/`, and `data/summaries/` are generated.

7. Remove active report references.
   - Delete from `scripts/generate_etf_summary.py`.
   - Delete from `scripts/rebroadcast_line.py`.
   - Delete any generated summary references in webhook routes if present.

8. Remove ETF benchmark references.
   - Delete from `scripts/etf_benchmark/step1_universe.py`.
   - Delete from `scripts/etf_benchmark/seed_tpex_etfs.csv` if it was manually seeded.
   - Rebuild or refresh benchmark outputs on the server. The SQLite DB is ignored and can be regenerated.

9. Update README.
   - Remove the ticker from Scripts, Data Directory, Services, Daily Job manual command, Manual Fetch commands, Current File Inventory, and any special notes.
   - If deleting an ETF creates a rich-menu empty slot, document or fill the slot according to current menu policy.

10. Run deletion verification.
    ```bash
    rg "<TICKER>|<ticker-lower>|<LINE_ALIAS>|<ETF_DISPLAY_NAME>"
    python -m py_compile app.py api/webhook.py scripts/monitor_etf_quotes.py scripts/generate_quote_card.py scripts/master_holding_quote_card.py scripts/setup_rich_menu.py
    bash -n scripts/update_and_notify.sh
    ```
    Expected `rg` leftovers should only be in Git history or intentional documentation. No runtime file should still require the deleted ETF.

11. Deployment commands after deleting an ETF.
    ```bash
    cd /home/ubuntu/STOCK
    git pull origin main --rebase --autostash
    sudo systemctl disable --now stock-quote-monitor-<ticker-lower>.service
    sudo rm -f /etc/systemd/system/stock-quote-monitor-<ticker-lower>.service
    sudo cp services/*.service services/*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service
    ```
    If the rich menu changed:
    ```bash
    source /home/ubuntu/.stock_secrets
    ./venv/bin/python scripts/setup_rich_menu.py
    ```

## Manual Fetch and Cache Checks

Fetch official holdings:

```bash
python scripts/fetch_etf_00981A.py
python scripts/fetch_etf_00988A.py
python scripts/fetch_etf_00403A.py
python scripts/fetch_passive_0050.py
python scripts/fetch_passive_00830.py
python scripts/fetch_passive_00878.py
python scripts/fetch_passive_00891.py
python scripts/fetch_passive_009805.py
python scripts/fetch_passive_009820.py
```

Seed one ETF quote cache:

```bash
python scripts/monitor_etf_quotes.py 00403A --interval 60
```

Render master holding card once:

```bash
python scripts/master_holding_quote_card.py
```

Test admin email:

```bash
source /home/ubuntu/.stock_secrets
python scripts/admin_email.py \
  --subject "[STOCK] manual SMTP test" \
  --body "If you see this, email config works."
```

## Troubleshooting

Service status:

```bash
sudo systemctl status stock-chart.service --no-pager
sudo systemctl status stock-webhook.service --no-pager
sudo systemctl status stock-dashboard.service --no-pager
```

Recent logs:

```bash
journalctl -u stock-chart.service -n 100 --no-pager
journalctl -u stock-webhook.service -n 100 --no-pager
journalctl -u stock-fetch-1730-tw.service -n 100 --no-pager
```

If LINE says it cannot connect to `127.0.0.1:5005`, restart and test the chart service:

```bash
sudo systemctl restart stock-chart.service
curl -s http://127.0.0.1:5005/docs >/dev/null && echo "chart service reachable"
```

If TradingView text or chart parsing breaks, use the exact failing key with `/market-debug` and inspect `journalctl -u stock-chart.service`.

If GitHub push returns a remote 500, retry after a few minutes. That error is server-side when local `git status` is clean and credentials are unchanged.

## Current File Inventory

This is the complete intended production tree after cleanup:

```text
.gitignore
README.md
requirements.txt
app.py
api/webhook.py
data/etf_00981A_history.json
data/etf_00981A_log.json
data/etf_00988A_history.json
data/etf_00988A_log.json
data/etf_00403A_history.json
data/etf_00403A_log.json
data/master_manual_positions.json
data/master_meta.json
data/master_trades.csv
data/passive_0050_history.json
data/passive_0050_log.json
data/passive_00830_history.json
data/passive_00830_log.json
data/passive_00878_history.json
data/passive_00878_log.json
data/passive_00891_history.json
data/passive_00891_log.json
data/passive_009805_history.json
data/passive_009805_log.json
data/passive_009820_history.json
data/passive_009820_log.json
scripts/admin_email.py
scripts/chart_service.py
scripts/fetch_etf_00981A.py
scripts/fetch_etf_00988A.py
scripts/fetch_etf_00403A.py
scripts/fetch_passive_0050.py
scripts/fetch_passive_00830.py
scripts/fetch_passive_00878.py
scripts/fetch_passive_00891.py
scripts/fetch_passive_009805.py
scripts/fetch_passive_009820.py
scripts/generate_etf_summary.py
scripts/generate_market_pulse_summary.py
scripts/generate_quote_card.py
scripts/master_holding_quote_card.py
scripts/master_manual_positions.py
scripts/monitor_etf_quotes.py
scripts/monitor_gold_quote.py
scripts/monitor_master_holding.py
scripts/rebroadcast_line.py
scripts/setup_rich_menu.py
scripts/update_and_notify.sh
scripts/etf_benchmark/__init__.py
scripts/etf_benchmark/db.py
scripts/etf_benchmark/seed_tpex_etfs.csv
scripts/etf_benchmark/step1_universe.py
scripts/etf_benchmark/step2_schema.py
scripts/etf_benchmark/step3_backfill.py
scripts/etf_benchmark/step4_verify.py
scripts/etf_benchmark/step5_verify_nav.py
scripts/etf_benchmark/step6_regimes.py
services/stock-chart.service
services/stock-dashboard.service
services/oci-firewall.service
services/stock-fetch-1730-tw.service
services/stock-fetch-1730-tw.timer
services/stock-gold-monitor.service
services/stock-master-holding-monitor.service
services/stock-quote-monitor-00403a.service
services/stock-quote-monitor-0050.service
services/stock-quote-monitor-00830.service
services/stock-quote-monitor-00878.service
services/stock-quote-monitor-00891.service
services/stock-quote-monitor-009805.service
services/stock-quote-monitor-00981a.service
services/stock-quote-monitor-00988a.service
services/stock-quote-monitor-009820.service
services/stock-webhook.service
src/__init__.py
src/ui/__init__.py
src/ui/etf_compare_tab.py
src/ui/etf_tab.py
src/ui/market_pulse_tab.py
```

## Cleanup Policy

Only production code, service templates, and tracked source/history data belong in this repo. Generated images, quote caches, benchmark SQLite files, local TradingView/TSIT captures, and one-off research experiments are ignored or removed. Local experiments should live outside the repo or in `strategy_experiment*/`, which is ignored.
