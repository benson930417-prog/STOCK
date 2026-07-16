# STOCK

STOCK is a self-hosted ETF and market-monitoring system. It has three user-facing surfaces:

- A Streamlit dashboard for ETF holdings, quote status, ETF comparison, master holdings, and market pulse.
- A LINE bot webhook for quote cards, daily reports, market charts, and admin commands.
- A Playwright/FastAPI chart service that captures TradingView text quotes and chart snapshots for the LINE bot.

The production server path used by all service files is `/home/ubuntu/STOCK`.

## Agent Delivery Contract

For future AI/code agents: when the user asks for a repo change, finish the delivery loop unless the user explicitly says not to.

1. Edit the repo and run appropriate checks.
2. Commit the completed change locally.
3. Push to `origin main`.
4. Deploy by pulling the pushed commit on the OCI server.
5. Tell the user exactly what changed, what was verified, and what ran on the server.

Normal server target:

```bash
ubuntu@80.225.204.45:/home/ubuntu/STOCK
```

Prefer one-shot SSH commands for routine deploys because they are auditable and close automatically. Use an interactive SSH session only for live debugging, log-following, or manual server inspection.

For README-only/doc changes, deployment usually means only:

```bash
cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash
```

For code changes, use the **Standard Deployment** command in this README and restart the relevant services. If service files changed, also copy service templates and run `sudo systemctl daemon-reload`.

### Complete Agent Debugging Loop (SSH deploy + browser verify)

An AI agent on the dev machine can run the whole loop end-to-end with no human in the middle: **edit → check → commit → push → SSH deploy → browser-verify**. This has been tested and works.

**This repository is PUBLIC on GitHub. Never commit the SSH private key, or the dashboard session token, to any tracked file, commit message, or log output.** The machine-specific secrets live in the untracked, git-ignored `CLAUDE.local.md` at the repo root of the dev machine (Claude Code loads it automatically). It contains:

- The exact SSH command (`ssh -i "<local key path>" ubuntu@80.225.204.45`) for the production server.
- A permanent dashboard session URL (`http://80.225.204.45:8501/?session=<token>`) that skips the `VIEW_PASSWORD` gate, so the agent can open the dashboard in a browser tool and visually verify a deployed change.

The loop:

1. Edit locally and run checks (`python -m py_compile ...`, `bash -n ...`).
2. Commit and push to `origin main`.
3. Deploy with a one-shot SSH command (key path from `CLAUDE.local.md`):

   ```bash
   ssh -i "<KEY_PATH>" ubuntu@80.225.204.45 "cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash && source venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service"
   ```

   For README/doc-only changes, the `git pull` alone is enough — skip pip and restarts.
4. Verify in the browser: open the permanent session URL from `CLAUDE.local.md` with the browser tool, navigate to the affected tab, and confirm the change actually renders (screenshot / read the page text). If `CLAUDE.local.md` is missing (fresh machine), ask the user for the session URL or `VIEW_PASSWORD` — do not guess.
5. Report to the user what changed, what was deployed, and what was visually verified.

Notes:

- The server repo may be ahead of the local checkout because the 18:30 daily job auto-commits data. Run `git pull origin main --rebase --autostash` locally before committing.
- The dashboard is Streamlit: after a `stock-dashboard.service` restart it needs a few seconds before the page responds; reload once if the first browser load looks broken.

## Runtime Map

| Runtime | Entry point | systemd unit | Purpose |
|---|---|---|---|
| Streamlit dashboard | `app.py` | `stock-dashboard.service` | Browser UI for ETF data, master holdings, ETF comparison, and market pulse. |
| LINE webhook | `api/webhook.py` | `stock-webhook.service` | Handles LINE messages, returns text/cards/images, and routes admin commands. |
| TradingView chart API | `scripts/chart_service.py` | `stock-chart.service` | Keeps a browser alive and exposes `/market-text`, `/snapshot`, and `/market-debug` on `127.0.0.1:5005`. |
| Daily fetch | `scripts/update_and_notify.sh` | `stock-fetch-1830-tw.timer` + `.service` | Pulls latest code, refreshes data, updates benchmark DB, commits/pushes, broadcasts reports, and emails admin summary. |
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

`app.py` renders the main dashboard and delegates major sections to `src/ui/`. It is served by `stock-dashboard.service` on port **8501** — publicly reachable at **http://80.225.204.45:8501/** (the OCI host's public IP). The page is password-gated (`VIEW_PASSWORD`); Streamlit auth is per browser session, so each new tab must re-enter the password.

| File | Purpose |
|---|---|
| `src/__init__.py` | Marks `src` as an import package. |
| `src/ui/__init__.py` | Marks UI helpers as an import package. |
| `src/ui/etf_tab.py` | Active/passive ETF dashboard views and daily operation report UI. |
| `src/ui/etf_compare_tab.py` | ETF comparison tab backed by the local `data/etf_bench/etf_bench.sqlite` database. |
| `src/ui/market_pulse_tab.py` | Market pulse tab using ETF benchmark/index history and regime calculations. |

Dashboard authentication uses `VIEW_PASSWORD` and `ADMIN_PASSWORD` from Streamlit secrets, environment variables, or `/home/ubuntu/.stock_secrets` depending on the runtime.

Market Pulse (`src/ui/market_pulse_tab.py`) is a dashboard view, not a live data fetcher. It reads the local ETF benchmark DB plus the server-local TWSE volume cache. Do not add slow network calls to the Streamlit render path; add a script/cache refresh step instead.

## LINE Webhook

`api/webhook.py` is a Flask app listening on `0.0.0.0:8080` when executed directly. It requires LINE credentials and calls the chart service through `CHART_SERVICE_URL`, defaulting to `http://127.0.0.1:5005`.

Common LINE commands:

| Command | Response |
|---|---|
| `981`, `988`, `0050`, `830`, `878`, `891`, `918`, `9805`, `9820` | ETF quote card/report for the mapped ETF. |
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
| `/snapshot` | POST | `{"key":"oil"}` | Capture a TradingView chart image into `data/images/` **and** return `text`/`quote` read from the same page render, so the price matches the chart at the same moment. |
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
| `nasdaq` | IG US Tech 100 Cash / NASDAQ 24-hour proxy |

Manual checks on the server:

```bash
curl -s -X POST http://127.0.0.1:5005/market-text -H "Content-Type: application/json" -d '{"key":"bond"}' && curl -s -X POST http://127.0.0.1:5005/snapshot -H "Content-Type: application/json" -d '{"key":"bond"}'
```

### Caching and Resource Limits

All LINE market commands (`油價`, `匯率`, `債券`, `黃金`, `那斯達克`) are served **only from cache**. The webhook never renders TradingView live during a reply — it reads `data/quote_cache/market_<key>.json`.

| Concern | Where | Value |
|---|---|---|
| Market cache refresh | `stock-market-chart-monitor.service` → `monitor_market_charts.py oil brent bond gold usdtwd usdjpy usdchf nasdaq --interval 60` | every **60s**, all 8 keys |
| Market monitor caps | `stock-market-chart-monitor.service` | `MemoryMax=512M`, `CPUQuota=35%`, `RuntimeMaxSec=12h` |
| Heavy lifting (Playwright) | `stock-chart.service` | `MemoryMax=2500M`, `RuntimeMaxSec=2h` (this is the real CPU/RAM governor; the monitor only makes HTTP calls) |
| Webhook cache freshness | `get_cached_market_chart(..., max_age_seconds=240)` | serve cache up to **240s** old, else explicit error |
| ETF quote-card cache | `stock-quote-monitor-*.service` → `monitor_etf_quotes.py <TICKER> --interval 180 --max-workers 4 --jitter 150` | every **180s** (3 min), `MemoryMax=512M`, `CPUQuota=35%`, `RuntimeMaxSec=12h` |
| Gold quote cache | `stock-gold-monitor.service` → `monitor_gold_quote.py --interval 60 --scanner-only` | every **60s**, `MemoryMax=512M` |

**Same-moment price + chart.** `monitor_market_charts.refresh_key` makes a single `/snapshot` call per key. `chart_service.py` reads the price/% from the *same page render* that produced the screenshot and returns both, so the cached text never drifts from the cached chart. There is no separate `/market-text` pass and no per-key price buffer.

**Snapshot crop rules.** Generic 5-day charts (`oil`, `brent`, `bond`, `gold`, `usdtwd`, `usdjpy`, `usdchf`) may detect the chart's `y` and `height`, but they must use the full TradingView viewport width: `clip.x = 0` and `clip.width = window.innerWidth`. Do not crop or shift the x-axis for generic charts; the right price axis and last-price marker live at the far right edge. NASDAQ is the exception: it uses a separate IG-NASDAQ 24h branch with its own `1200x900` viewport, fixed `y`/`height`, 1-day button click, and trading-session overlay. Do not "simplify" NASDAQ into the generic crop path unless it is manually reverified.

The `/snapshot` response includes `clip` and `viewport`; `monitor_market_charts.py` stores those fields in `data/quote_cache/market_<key>.json`. If a chart image looks cropped, check those values first. For generic charts, `clip.x` should be `0`.

### Adding a Cached Market Chart Monitor

Use this procedure when adding a LINE market chart command that must reply fast and should not render TradingView during the LINE reply.

1. Add the TradingView key in `scripts/chart_service.py`.
   - Add the URL to `CHART_TABS`.
   - Add display metadata to `CHART_META`.
   - Add quote extraction logic if `/market-text` cannot use the generic parser.
   - Add any symbol-specific `/snapshot` crop logic if the generic chart crop is not reliable.
   - For generic TradingView charts, keep full x-width (`x=0`, full viewport width) and only tune vertical crop. Do not add x movement/cropping unless the key is deliberately special-cased.
   - Keep failures explicit. Do not silently fall back to Yahoo or another provider for these TradingView commands.

2. Test the chart service directly on the server.

   ```bash
   curl -s -X POST http://127.0.0.1:5005/market-text -H "Content-Type: application/json" -d '{"key":"nasdaq"}' && curl -s -X POST http://127.0.0.1:5005/snapshot -H "Content-Type: application/json" -d '{"key":"nasdaq"}'
   ```

3. Add the key to `scripts/monitor_market_charts.py`.
   - If the existing service should monitor multiple charts, add the key to `services/stock-market-chart-monitor.service` after `monitor_market_charts.py`.
   - The monitor writes `data/quote_cache/market_<key>.json` and refreshes the chart image in `data/images/`.

4. Update `api/webhook.py`.
   - Add the LINE command aliases.
   - For fast replies, call `get_cached_market_chart("<key>")`.
   - Send `cache["text"]` and `cache["snapshot_url"]`.
   - Do not call `get_market_text()` or `get_chart_snapshot()` inside the LINE reply path for cached chart commands.
   - If the cache is missing or stale, return the explicit cache error. Do not generate live inside the webhook.

5. Add or update the systemd service.
   - Reuse `services/stock-market-chart-monitor.service` for multiple chart keys when possible.
   - Create a separate service only if the chart has a different interval, timeout, or isolation need.

6. Update this README.
   - Add the key to the Market keys table.
   - Add any new script/service to the Scripts and Services tables.
   - Add install/enable/restart commands.
   - Add troubleshooting notes if the chart uses special crop or page parsing rules.

7. Verify before pushing/deploying.

   ```bash
   python -m py_compile api/webhook.py scripts/chart_service.py scripts/monitor_market_charts.py
   ```

8. Deploy on the server.

   ```bash
   cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now stock-market-chart-monitor.service && sudo systemctl restart stock-chart.service stock-market-chart-monitor.service stock-webhook.service
   ```

9. Verify the cache, then test LINE.

   ```bash
   python scripts/monitor_market_charts.py oil brent bond gold usdtwd usdjpy usdchf nasdaq --once && cat data/quote_cache/market_oil.json && ls -lh data/images/*_chart.png && journalctl -u stock-market-chart-monitor.service -n 80 --no-pager
   ```

## Scripts

| File | Purpose |
|---|---|
| `scripts/admin_email.py` | Sends daily run summaries through Gmail SMTP. Exits successfully when email secrets are missing so the daily job is not blocked. |
| `scripts/chart_service.py` | TradingView quote/chart FastAPI service used by the LINE webhook. |
| `scripts/fetch_etf_00403A.py` | Fetches official 00403A holdings/NAV data from Unified's `fundCode=63YTW` Excel endpoint. |
| `scripts/fetch_etf_00981A.py` | Fetches official 00981A holdings/NAV data into tracked history/log JSON. |
| `scripts/fetch_etf_00988A.py` | Fetches official 00988A holdings/NAV data from Unified's `fundCode=61YTW` Excel endpoint. |
| `scripts/fetch_etf_00991A.py` | Fetches official 00991A (主動復華未來50) holdings/NAV from Fuh Hwa's `assetsExcel/ETF23` Excel endpoint. |
| `scripts/fetch_passive_0050.py` | Fetches 0050 passive ETF holdings/history. |
| `scripts/fetch_passive_0056.py` | Fetches 0056 (元大高股息) passive ETF holdings/history from the official Yuanta source. |
| `scripts/fetch_passive_00830.py` | Fetches 00830 passive ETF holdings/history from the official Cathay source. |
| `scripts/fetch_passive_00878.py` | Fetches 00878 passive ETF holdings/history from the official Cathay source. |
| `scripts/fetch_passive_00891.py` | Fetches 00891 passive ETF holdings/history from CTBC's official ETF API. |
| `scripts/fetch_passive_00918.py` | Fetches 00918 passive ETF holdings/history from UOBAM's official PCF API. |
| `scripts/fetch_passive_009805.py` | Fetches 009805 passive ETF holdings/history. |
| `scripts/fetch_passive_009820.py` | Fetches 009820 passive ETF holdings/history. |
| `scripts/generate_etf_summary.py` | Builds daily ETF summary images for LINE broadcast. |
| `scripts/generate_market_pulse_summary.py` | Renders the market pulse summary image served by the LINE `市場脈動` command. |
| `scripts/generate_quote_card.py` | Shared quote-card image renderer for ETF/master-holding views. |
| `scripts/master_holding_quote_card.py` | Expands ETF holdings into the configured master portfolio and renders/caches its quote card. |
| `scripts/master_manual_positions.py` | Manual position data/helpers for the master portfolio. |
| `scripts/monitor_etf_quotes.py` | Long-running quote cache daemon for one ETF ticker. |
| `scripts/monitor_gold_quote.py` | Long-running GOLD quote monitor. |
| `scripts/monitor_market_charts.py` | Long-running TradingView market text/chart cache monitor for LINE chart commands such as NASDAQ. |
| `scripts/monitor_master_holding.py` | Long-running master-holding cache monitor. |
| `scripts/overlay_market_sessions.py` | Draws Taiwan/US session markers on the special NASDAQ 24h chart image. |
| `scripts/rebroadcast_line.py` | Manual helper for rebroadcasting generated LINE report images. |
| `scripts/setup_rich_menu.py` | Creates/updates the LINE rich menu. |
| `scripts/update_and_notify.sh` | Daily orchestrator for fetchers, benchmark refresh, market pulse image, Git update, LINE broadcast, and admin email. |
| `scripts/update_market_pulse_volume.py` | Refreshes the server-local TWSE 成交量/成交金額 cache used by the 市場脈動 price-volume panel. |

00988A global-holding quote handling: the holdings sheet uses global market suffixes such as `NVDA US`, `7203 JP`, or Hong Kong/Taiwan codes. `scripts/monitor_etf_quotes.py` normalizes those into Yahoo Finance symbols (`NVDA`, `7203.T`, `0005.HK`, `2330.TW`, etc.) and applies the existing exchange-session watcher logic.

00891 CTBC handling: `scripts/fetch_passive_00891.py` first requests CTBC's public `home/AuthToken`, confirms `CNO=88182265` maps to internal `FID=E0017`, then reads `etf/ETFHoldingWeight`. Only the stock holding block is stored for quote monitoring; futures/margin/cash blocks are left out of the quote card because they do not map to Yahoo equity quotes.

00918 UOBAM handling: `scripts/fetch_passive_00918.py` reads UOBAM's official `WebSitePcfRequest` JSON for fund ID `88329556`, stores only `kind=stock` rows for quote monitoring, and leaves cash/margin rows out of the quote card.

TSMC night-session handling: when a holding maps to `2330` / `2330.TW` during the QFF1! night futures window, the monitor uses TradingView's printed `TAIFEX:QFF1!` change percent. It does not calculate the percent against the 2330.TW day close, because that mixes different markets and baselines.

## ETF Benchmark Pipeline

The ETF comparison tab reads a local SQLite database generated under `data/etf_bench/`. The database is intentionally ignored by Git and rebuilt on each host.

The fair ETF score (綜合評分: ranking table + history) is specified in
`scripts/etf_benchmark/SCORING.md` — read that for the ranking logic and the
final four-basket (股票/債券/商品/其他) design and rationale.

| File | Purpose |
|---|---|
| `scripts/etf_benchmark/__init__.py` | Package marker. |
| `scripts/etf_benchmark/db.py` | Streamlit-cached SQLite read helpers. |
| `scripts/etf_benchmark/seed_tpex_etfs.csv` | Seed list for TPEx ETFs not covered by the TWSE source. |
| `scripts/etf_benchmark/step1_universe.py` | Builds `data/etf_bench/universe.csv`. |
| `scripts/etf_benchmark/step2_schema.py` | Creates/resets SQLite schema. |
| `scripts/etf_benchmark/step3_backfill.py` | Downloads prices/dividends/splits through yfinance. Use `--incremental` for daily refresh. |
| `scripts/etf_benchmark/step4_regimes.py` | Builds market regime tags used by market pulse/benchmark views. |
| `scripts/etf_benchmark/step5_score.py` | Records each ETF's fair-score pillars (效率/不對稱/一致性) per day into `data/etf_bench/score_history.csv`, ranked within asset class. `--backfill` rebuilds history (default 1y); no args appends today. Powers the ETF compare tab's 綜合評分 ranking + history. |
| `scripts/etf_benchmark/SCORING.md` | Authoritative spec for the 綜合評分 score: ranking logic, the four-basket method, the three pillars, methodology, and verification. |

First-time benchmark setup:

```bash
cd /home/ubuntu/STOCK && source venv/bin/activate && python -m scripts.etf_benchmark.step1_universe && python -m scripts.etf_benchmark.step2_schema --reset && python -m scripts.etf_benchmark.step3_backfill && python -m scripts.etf_benchmark.step4_regimes && python -m scripts.etf_benchmark.step5_score --backfill && python scripts/update_market_pulse_volume.py --backfill-years 5
```

Daily refresh uses:

```bash
python -m scripts.etf_benchmark.step3_backfill --incremental && python -m scripts.etf_benchmark.step4_regimes && python -m scripts.etf_benchmark.step5_score && python scripts/update_market_pulse_volume.py --months 4
```

### Market Pulse Data Flow

Market Pulse has two separate outputs:

- Dashboard tab: `src/ui/market_pulse_tab.py`, rendered live in Streamlit from local data.
- LINE image: `scripts/generate_market_pulse_summary.py`, saved as `data/summaries/market_pulse_latest.jpg`.

The dashboard's 價量健康 panel uses TWSE official `FMTQIK` daily stats for TAIEX close, turnover, and volume. That data is cached in ignored server-local `data/market_pulse_volume.csv`. The dashboard only reads the CSV via `_twse_daily_market()`; it must not call TWSE during page render. The cache is refreshed by `scripts/update_market_pulse_volume.py`, and the 18:30 daily job runs:

```bash
python scripts/update_market_pulse_volume.py --months 4
```

On a fresh server, initialize history once:

```bash
python scripts/update_market_pulse_volume.py --backfill-years 5
```

`scripts/update_and_notify.sh` treats this as its own logged step (`market pulse volume cache`) and the admin email includes the `[market-volume] rows=... range=...` line. `step4_regimes` and `step5_score` are production benchmark steps, not temporary debug steps: step 4 builds regime tags, step 5 appends/backfills fair-score history.

Regime threshold must stay aligned: the dashboard imports `DEFAULT_THRESHOLD_PCT` from `scripts/etf_benchmark/step4_regimes.py` for its live ZigZag overlay and headline label. Do not hardcode a separate 4%/5% threshold in `src/ui/market_pulse_tab.py`.

## Data Directory

Tracked files in `data/` are source/history state that should move with the repo:

| File pattern | Purpose |
|---|---|
| `data/etf_00403A_history.json`, `data/etf_00981A_history.json`, `data/etf_00988A_history.json`, `data/etf_00991A_history.json` | Active ETF official history snapshots. |
| `data/etf_00403A_log.json`, `data/etf_00981A_log.json`, `data/etf_00988A_log.json`, `data/etf_00991A_log.json` | Active ETF fetch logs/status. |
| `data/passive_*_history.json` | Passive ETF official history snapshots for 0050, 0056, 00830, 00878, 00891, 00918, 009805, and 009820. |
| `data/passive_*_log.json` | Passive ETF fetch logs/status. |
| `data/master_manual_positions.json` | Manual master portfolio positions. |
| `data/master_meta.json` | Master portfolio metadata/state. |
| `data/master_trades.csv` | Manual trade ledger for the master portfolio. |
| `data/summaries/market_pulse_latest.jpg` | Latest generated Market Pulse LINE image. |

Ignored generated data:

| Path | Producer |
|---|---|
| `data/images/` | `chart_service.py`, quote-card renderers, webhook responses. |
| `data/summaries/` | `generate_market_pulse_summary.py` and report generators. |
| `data/quote_cache/` | Quote monitor services. |
| `data/market_pulse_volume.csv` | Server-local TWSE market turnover/volume cache from `scripts/update_market_pulse_volume.py`. |
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
| `services/stock-fetch-1830-tw.service` | `stock-fetch-1830-tw.service` | One-shot daily fetch/orchestration job. |
| `services/stock-fetch-1830-tw.timer` | `stock-fetch-1830-tw.timer` | Runs the daily job at 10:30 UTC / 18:30 Taiwan time. |
| `services/stock-gold-monitor.service` | `stock-gold-monitor.service` | GOLD quote monitor. |
| `services/stock-market-chart-monitor.service` | `stock-market-chart-monitor.service` | TradingView market text/chart cache monitor for fast LINE chart replies. |
| `services/stock-master-holding-monitor.service` | `stock-master-holding-monitor.service` | Master holdings monitor. |
| `services/stock-quote-monitor-00403a.service` | `stock-quote-monitor-00403a.service` | 00403A quote monitor. |
| `services/stock-quote-monitor-0050.service` | `stock-quote-monitor-0050.service` | 0050 quote monitor. |
| `services/stock-quote-monitor-0056.service` | `stock-quote-monitor-0056.service` | 0056 quote monitor. |
| `services/stock-quote-monitor-00830.service` | `stock-quote-monitor-00830.service` | 00830 quote monitor. |
| `services/stock-quote-monitor-00878.service` | `stock-quote-monitor-00878.service` | 00878 quote monitor. |
| `services/stock-quote-monitor-00891.service` | `stock-quote-monitor-00891.service` | 00891 quote monitor. |
| `services/stock-quote-monitor-00918.service` | `stock-quote-monitor-00918.service` | 00918 quote monitor. |
| `services/stock-quote-monitor-009805.service` | `stock-quote-monitor-009805.service` | 009805 quote monitor. |
| `services/stock-quote-monitor-00981a.service` | `stock-quote-monitor-00981a.service` | 00981A quote monitor. |
| `services/stock-quote-monitor-00988a.service` | `stock-quote-monitor-00988a.service` | 00988A quote monitor. |
| `services/stock-quote-monitor-00991a.service` | `stock-quote-monitor-00991a.service` | 00991A quote monitor. |
| `services/stock-quote-monitor-009820.service` | `stock-quote-monitor-009820.service` | 009820 quote monitor. |

Install/update service templates:

```bash
cd /home/ubuntu/STOCK && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable stock-dashboard.service stock-webhook.service stock-chart.service && sudo systemctl enable oci-firewall.service && sudo systemctl enable stock-fetch-1830-tw.timer && sudo systemctl enable stock-gold-monitor.service stock-market-chart-monitor.service stock-master-holding-monitor.service && sudo systemctl enable stock-quote-monitor-0050.service stock-quote-monitor-0056.service stock-quote-monitor-00830.service && sudo systemctl enable stock-quote-monitor-00878.service stock-quote-monitor-00891.service stock-quote-monitor-00918.service && sudo systemctl enable stock-quote-monitor-009805.service && sudo systemctl enable stock-quote-monitor-00403a.service stock-quote-monitor-00981a.service stock-quote-monitor-00988a.service stock-quote-monitor-00991a.service stock-quote-monitor-009820.service
```

Restart common production services after code changes:

```bash
sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

Restart all monitors:

```bash
sudo systemctl restart stock-gold-monitor.service stock-market-chart-monitor.service stock-master-holding-monitor.service && sudo systemctl restart stock-quote-monitor-0050.service stock-quote-monitor-0056.service stock-quote-monitor-00830.service && sudo systemctl restart stock-quote-monitor-00878.service stock-quote-monitor-00891.service stock-quote-monitor-00918.service && sudo systemctl restart stock-quote-monitor-009805.service && sudo systemctl restart stock-quote-monitor-00403a.service stock-quote-monitor-00981a.service stock-quote-monitor-00988a.service stock-quote-monitor-00991a.service stock-quote-monitor-009820.service
```

## Server Setup

First-time host setup:

```bash
cd /home/ubuntu && git clone https://github.com/benson930417-prog/STOCK.git && cd /home/ubuntu/STOCK && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt -q && python -m playwright install chromium
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
sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now oci-firewall.service && sudo systemctl enable --now stock-chart.service stock-webhook.service stock-dashboard.service && sudo systemctl enable --now stock-fetch-1830-tw.timer
```

## Standard Deployment

Use this after pulling new code on the server:

```bash
cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash && source venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

If service files changed:

```bash
sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
```

## Daily Job Flow

`stock-fetch-1830-tw.timer` runs at 10:30 UTC, which is 18:30 Taiwan time.

`scripts/update_and_notify.sh` performs:

1. Load `/home/ubuntu/.stock_secrets`.
2. Pull latest Git changes with rebase/autostash.
3. Install dependencies from `requirements.txt`.
4. Run all active/passive ETF fetchers requested by the service arguments.
5. Refresh ETF benchmark SQLite data, regime tags, and score history.
6. Refresh the server-local Market Pulse TWSE volume cache.
7. Generate the market pulse image.
8. Commit and push changed tracked data.
9. Broadcast active ETF reports through LINE when new active ETF data exists.
10. Send admin email summary with success/failure details, including small step metrics such as DB rows, regime counts, score writes, market-volume cache range, generated summary files, LINE send output, and git push status.

Manual run:

```bash
cd /home/ubuntu/STOCK && source venv/bin/activate && bash scripts/update_and_notify.sh 00403A 00981A 00988A 0050 0056 00830 00878 00891 00918 009805 009820
```

## Rich Menu

Run this when LINE rich-menu buttons change:

```bash
cd /home/ubuntu/STOCK && source venv/bin/activate && source /home/ubuntu/.stock_secrets && python scripts/setup_rich_menu.py
```

The rich menu uses three LINE rich-menu aliases. Page 1 is fast market/macro, Page 2 is the primary ETF watchlist, and Page 3 is ETF overflow. Navigation convention is fixed: previous page is bottom-left, next page is bottom-right, and the last page uses bottom-right `首頁` until another page is needed.

## ⚠️ Invariants & Gotchas (read before editing)

Each of these is a trap that has already caused a wrong result or a missed step. Check them before and after any change.

1. **The daily fetch list is the timer's `ExecStart` args, NOT the script default.** `services/stock-fetch-1830-tw.service` passes an explicit ticker list to `update_and_notify.sh`, which makes `ETFS=("$@")` override the default `ETFS=(...)` array. Editing only the default array does nothing for the scheduled run — you must edit the `ExecStart` line. (Only the manual webhook "每日更新" path, which passes no args, uses the default.) `0056` is intentionally/historically absent from the `ExecStart` args even though it is in the default array and has a quote monitor.

2. **One ETF ticker is hardcoded in ~16 places.** Adding/removing an ETF is a cross-file change. Before editing, list every occurrence:
   ```bash
   grep -rnE '00981A|00988A|<TICKER>' --include=*.py --include=*.sh --include=*.service . | grep -v __pycache__
   ```
   Missing one silently breaks a single surface (no error). The authoritative list is the touch-point table under **Add One ETF**.

3. **Quote-card output has TWO independent render paths — keep them in sync.** The **text** bubble is built by `build_etf_quote_text` in `api/webhook.py`; the **image** is drawn by `scripts/generate_quote_card.py` (`_fmt_pct` etc.) and regenerated by each quote monitor. A formatting rule (e.g. "no `+` sign on percentages") must be applied in BOTH. The master 吳大師 text is a third path in `scripts/master_holding_quote_card.py`.

4. **`master_trades.csv` merge is append + dedupe, never window-replace.** `merge_into_master` in `app.py` must not delete rows by date window — broker weekly exports overlap (handled by `_key`/stable-key dedupe) and routine exports legitimately omit special transactions (in-kind subscriptions). Window-replace once silently wiped a 101,000-share buy. Recover lost rows from the CSV's git history if needed.

5. **吳大師 financial definitions** (LINE 總覽 + website KPIs), keep consistent:
   - `總成本` = open-position cost basis. `成本` (LINE) = **deployed principal = 總成本 − 已實**.
   - `已實` = realized P/L. Compute it via `scripts/realized_pnl.py` (`compute_realized_total`); it is verified equal to the dashboard's realized KPI — do not invent a second formula.
   - `未實` = unrealized P/L, **after** estimated exit fee+tax (so it can read lower/negative vs a broker's gross).
   - `槓桿值` = sum of the look-through expansion weights (total exposure ÷ capital); **>100% is expected** with leveraged holdings.

6. **Leveraged ETFs decompose to a proxy basket with doubled weights — totals over 100% on purpose.** `LEVERAGED_ETF_PROXY` (duplicated in `app.py` and `scripts/master_holding_quote_card.py`) maps `00631L → {expand_as: 0050, leverage: 2.0}`. The expansion weight denominator is the real portfolio value, so leveraged constituents read as a true share of capital and the column legitimately totals >100%. Do not renormalise it back to 100%.

7. **LINE broadcast/push caps at 5 message objects.** The daily active-ETF broadcast is 1 text header + 1 image per active ETF. With 4 active ETFs that is exactly 5 (the cap). The broadcast code chunks into batches of 5; a 5th active ETF would mean >1 push.

8. **Per-ETF data prefix:** active = `data/etf_<TICKER>_*`, passive = `data/passive_<TICKER>_*`. `monitor_etf_quotes.py` and `_latest_history_payload` pick the prefix from the `PASSIVE_*` sets — an active ETF must NOT be in those sets.

9. **Asian markets with a midday break must stay in-session during lunch.** Tokyo (11:30–12:30), Hong Kong (12:00–13:00) and Shanghai/Shenzhen (11:30–13:00) pause for lunch but are NOT closed for the day. `_regular_session_bounds` (in `monitor_etf_quotes.py`) therefore spans open→close as ONE window for JP/HK/CN; do not split it back into morning/afternoon windows or the lunch gap is mis-detected as `CLOSE`, which freezes those holdings at the morning close and dumps them into the 已收盤 composite instead of 交易中. A holding showing 已收盤 with a stale ~morning timestamp while its own exchange is mid-day is the symptom.

10. **Market Pulse volume is daily cached, never fetched on demand.** `src/ui/market_pulse_tab.py` must read `data/market_pulse_volume.csv` only. `scripts/update_market_pulse_volume.py` is the only place that should call TWSE `FMTQIK`; it runs at 18:30 through `scripts/update_and_notify.sh`. If the dashboard says the cache is missing, run `python scripts/update_market_pulse_volume.py --backfill-years 5` once on the server, then let the daily `--months 4` refresh maintain it. Do not add `requests` back into the Streamlit render path.

11. **Cached TradingView charts use full x-width except NASDAQ.** Generic market charts (`oil`, `brent`, `bond`, `gold`, `usdtwd`, `usdjpy`, `usdchf`) should crop only vertically and must keep `clip.x=0`, `clip.width=window.innerWidth`; otherwise the right price axis/last-price marker gets cut off. NASDAQ is intentionally special: it uses IG-NASDAQ 24h, a fixed `1200x900` viewport, custom y/height, a 1-day click, and `overlay_market_sessions.py`. Do not share generic crop edits into NASDAQ unless reverified with a real screenshot.

## ETF Maintenance Playbook For Agents

This section is written for future AI/code agents. Treat an ETF add/delete as a cross-system change, not as a single fetcher edit. The same ticker appears in the dashboard, LINE webhook, rich menu, quote monitors, daily fetch orchestration, benchmark seed logic, master holding expansion, service templates, generated/tracked data, and this README.

Use consistent casing:

- Ticker in user-facing text/data: uppercase, e.g. `00403A`, `00981A`, `009820`.
- Quote-monitor service filename: lowercase suffix, e.g. `services/stock-quote-monitor-00403a.service`.
- LINE short command: compact numeric alias, e.g. `403`, `981`, `9820`.
- Active ETF data files: `data/etf_<TICKER>_history.json` and `data/etf_<TICKER>_log.json`.
- Passive ETF data files: `data/passive_<TICKER>_history.json` and `data/passive_<TICKER>_log.json`.

### Add One ETF

**Complete touch-point checklist.** Every row must be updated (or consciously skipped). This is the authoritative list — the prose steps below expand on it. `A` = active only, `P` = passive only, `★` = was missed before, check it.

| # | File | Symbol / location | Active/Passive |
|---|---|---|---|
| 1 | `scripts/fetch_etf_<T>.py` or `scripts/fetch_passive_<T>.py` | new fetcher | both |
| 2 | `data/etf_<T>_*.json` / `data/passive_<T>_*.json` | initial history + log (commit) | both |
| 3 ★ | `services/stock-fetch-1830-tw.service` | **`ExecStart` ticker args** (the real daily list) + Description | both |
| 4 | `scripts/update_and_notify.sh` | default `ETFS=(...)`, fetch `case`, active-new `case` (A), broadcast `names` (A) | both |
| 5 | `api/webhook.py` | `ETF_QUOTE_NAMES`, `ETF_QUOTE_ALIASES`, `ACTIVE_ETF_TICKERS` (A), help text | both |
| 6 ★ | `app.py` | `ETF_NAME_TO_TICKER`, `expandable` set, `missing_etfs` check set | both |
| 7 ★ | `scripts/master_holding_quote_card.py` | `ETF_NAME_TO_TICKER`, `expandable` set | both |
| 8 | `scripts/generate_quote_card.py` | `ETF_NAMES`, `PASSIVE_TICKERS` (P) | both |
| 9 | `scripts/generate_etf_summary.py` | `ETFS` list | A |
| 10 | `scripts/rebroadcast_line.py` | `ACTIVE_NAMES` | A |
| 11 | `scripts/monitor_etf_quotes.py` | `PASSIVE_ETF_TICKERS` (P only — never add an active ETF here) | P |
| 12 | `src/ui/etf_tab.py` | active selector list / passive selector | both |
| 13 | `services/stock-quote-monitor-<t-lower>.service` | new quote-monitor unit | both |
| 14 ★ | `scripts/setup_rich_menu.py` | a menu tile (fill the `預留` placeholder or add a slot) | both |
| 15 | `scripts/etf_benchmark/step1_universe.py` | `required` list (only if it should appear in ETF compare) | both |
| 16 | `README.md` | Scripts table, Data Directory, Services table, enable/restart one-liners, Manual Fetch, Current File Inventory | both |

After editing, re-run the grep from Invariant #2 and confirm the only remaining non-matches are intentional. Verify: `python -m py_compile` the touched `.py`, `bash -n scripts/update_and_notify.sh`.

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

4. Wire daily fetch orchestration.
   - **`services/stock-fetch-1830-tw.service` — add the ticker to the `ExecStart` argument list AND the `Description`. This is the list the scheduled run actually uses (see Invariant #1); the default array alone is not enough.** Requires `sudo cp services/*.service /etc/systemd/system/ && sudo systemctl daemon-reload` to take effect.
   - In `scripts/update_and_notify.sh`: add the ticker to the default `ETFS=(...)` list (used by the manual "每日更新" path), the fetcher dispatch `case`, the active-new `case` (active), and the broadcast `names = {...}` map (active).
   - Ensure failure details print the ETF fetch log clearly in the admin email.
   - Active ETFs broadcast 1 image each; mind the 5-object LINE cap (Invariant #7).

5. Wire dashboard ETF views.
   - Add the ticker to `src/ui/etf_tab.py` ETF selectors/lists.
   - If the ETF is active, include it beside other active ETFs in active report sections.
   - If any display name appears in `app.py`, add the new ticker/name there too.
   - Run the dashboard after edits and verify the ETF page loads from tracked history, not quote cache alone.

6. Wire LINE webhook commands in `api/webhook.py`.
   - Add the ticker to `ETF_QUOTE_NAMES` and a numeric alias to `ETF_QUOTE_ALIASES` (e.g. `991 -> 00991A`).
   - For an active ETF, add it to `ACTIVE_ETF_TICKERS` (this is what selects the `etf_*` vs `passive_*` data prefix in the webhook).
   - Add a `• <alias> — <TICKER> 持股即時表` line to the help text. (There is no on-demand `操作日報` command anymore — active reports go out via the daily broadcast only.)
   - Keep errors explicit. Do not silently fall back to stale data when a quote/fetch fails.
   - The quote-card **text** lives here (`build_etf_quote_text`); the **image** is separate (`generate_quote_card.py`). Formatting rules apply to both — see Invariant #3.

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
   - **Passive ETFs — update every passive-ticker set, or the monitor reads the
     wrong path.** A passive ETF's holdings live in `data/passive_<TICKER>_history.json`
     (not `etf_…`). The passive-ticker set is duplicated in several files and ALL of them
     must include the new ticker: `PASSIVE_ETF_TICKERS` in `scripts/monitor_etf_quotes.py`,
     `PASSIVE_TICKERS` in `scripts/generate_quote_card.py`, the `{"0050", …}` sets in
     `app.py` and `scripts/master_holding_quote_card.py`, the passive `case`/sets in
     `scripts/update_and_notify.sh`, and the selector list in `src/ui/etf_tab.py`.
   - **Populate holdings before running the monitor**: run `fetch_passive_<TICKER>.py`
     first so `passive_<TICKER>_history.json` has real data; the monitor reads it.

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
      python -m py_compile app.py api/webhook.py scripts/fetch_etf_<TICKER>.py scripts/monitor_etf_quotes.py scripts/generate_quote_card.py scripts/master_holding_quote_card.py scripts/setup_rich_menu.py && bash -n scripts/update_and_notify.sh
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
      ./venv/bin/python -m scripts.etf_benchmark.step1_universe && ./venv/bin/python -m scripts.etf_benchmark.step3_backfill --incremental && ./venv/bin/python -m scripts.etf_benchmark.step4_regimes && ./venv/bin/python -m scripts.etf_benchmark.step5_score
      ```

16. Deployment commands after merging/pulling on the server.
    ```bash
    cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash && source venv/bin/activate && pip install -r requirements.txt -q && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now stock-quote-monitor-<ticker-lower>.service && sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service stock-quote-monitor-<ticker-lower>.service
    ```
    If the rich menu changed:
    ```bash
    source /home/ubuntu/.stock_secrets && ./venv/bin/python scripts/setup_rich_menu.py
    ```

### Delete One ETF

Work the **Add One ETF touch-point table in reverse** — remove the ticker from every one of those 16 rows (most easily found via the Invariant #2 grep). The rows most often missed on deletion are the same ones missed on add: the `ExecStart` args (row 3) and the rich-menu tile (row 14, leave a `預留` placeholder). The steps below are the detail.

1. Remove it from daily orchestration (incl. the `ExecStart` args in `services/stock-fetch-1830-tw.service`).
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
     sudo systemctl disable --now stock-quote-monitor-<ticker-lower>.service && sudo rm -f /etc/systemd/system/stock-quote-monitor-<ticker-lower>.service && sudo systemctl daemon-reload
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
    cd /home/ubuntu/STOCK && git pull origin main --rebase --autostash && sudo systemctl disable --now stock-quote-monitor-<ticker-lower>.service && sudo rm -f /etc/systemd/system/stock-quote-monitor-<ticker-lower>.service && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service
    ```
    If the rich menu changed:
    ```bash
    source /home/ubuntu/.stock_secrets && ./venv/bin/python scripts/setup_rich_menu.py
    ```

## Manual Fetch and Cache Checks

Fetch official holdings:

```bash
python scripts/fetch_etf_00981A.py && python scripts/fetch_etf_00988A.py && python scripts/fetch_etf_00991A.py && python scripts/fetch_etf_00403A.py && python scripts/fetch_passive_0050.py && python scripts/fetch_passive_0056.py && python scripts/fetch_passive_00830.py && python scripts/fetch_passive_00878.py && python scripts/fetch_passive_00891.py && python scripts/fetch_passive_00918.py && python scripts/fetch_passive_009805.py && python scripts/fetch_passive_009820.py
```

Seed one ETF quote cache:

```bash
python scripts/monitor_etf_quotes.py 00403A --interval 60
```

Render master holding card once:

```bash
python scripts/master_holding_quote_card.py
```

Refresh Market Pulse TWSE volume cache once:

```bash
python scripts/update_market_pulse_volume.py --months 4
```

Initialize Market Pulse TWSE volume history on a fresh server:

```bash
python scripts/update_market_pulse_volume.py --backfill-years 5
```

Refresh cached LINE market charts once:

```bash
python scripts/monitor_market_charts.py oil brent bond gold usdtwd usdjpy usdchf nasdaq --once
```

Test admin email:

```bash
source /home/ubuntu/.stock_secrets && python scripts/admin_email.py --subject "[STOCK] manual SMTP test" --body "If you see this, email config works."
```

## Troubleshooting

Service status:

```bash
sudo systemctl status stock-chart.service --no-pager && sudo systemctl status stock-webhook.service --no-pager && sudo systemctl status stock-dashboard.service --no-pager
```

Recent logs:

```bash
journalctl -u stock-chart.service -n 100 --no-pager && journalctl -u stock-webhook.service -n 100 --no-pager && journalctl -u stock-fetch-1830-tw.service -n 100 --no-pager
```

If LINE says it cannot connect to `127.0.0.1:5005`, restart and test the chart service:

```bash
sudo systemctl restart stock-chart.service && curl -s http://127.0.0.1:5005/docs >/dev/null && echo "chart service reachable"
```

If TradingView text or chart parsing breaks, use the exact failing key with `/market-debug` and inspect `journalctl -u stock-chart.service`.

If a cached TradingView image is horizontally cropped, first inspect the saved cache JSON:

```bash
cat data/quote_cache/market_oil.json | python -m json.tool | grep -A8 '"clip"'
```

For generic market charts, `clip.x` should be `0` and `clip.width` should match the snapshot viewport width. Restart the chart service after crop-code changes, then regenerate cache images:

```bash
sudo systemctl restart stock-chart.service && source venv/bin/activate && python scripts/monitor_market_charts.py oil brent bond gold usdtwd usdjpy usdchf --once
```

NASDAQ is intentionally excluded from that generic crop check; verify it separately because it has custom 24h parameters:

```bash
python scripts/monitor_market_charts.py nasdaq --once && cat data/quote_cache/market_nasdaq.json | python -m json.tool | grep -A12 '"clip"'
```

If the Market Pulse dashboard says TWSE volume cache is missing or too short, initialize or refresh the cache instead of editing the dashboard:

```bash
python scripts/update_market_pulse_volume.py --backfill-years 5
```

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
data/etf_00991A_history.json
data/etf_00991A_log.json
data/etf_00403A_history.json
data/etf_00403A_log.json
data/master_manual_positions.json
data/master_meta.json
data/master_trades.csv
data/summaries/market_pulse_latest.jpg
data/passive_0050_history.json
data/passive_0050_log.json
data/passive_0056_history.json
data/passive_0056_log.json
data/passive_00830_history.json
data/passive_00830_log.json
data/passive_00878_history.json
data/passive_00878_log.json
data/passive_00891_history.json
data/passive_00891_log.json
data/passive_00918_history.json
data/passive_00918_log.json
data/passive_009805_history.json
data/passive_009805_log.json
data/passive_009820_history.json
data/passive_009820_log.json
scripts/admin_email.py
scripts/chart_service.py
scripts/fetch_etf_00981A.py
scripts/fetch_etf_00988A.py
scripts/fetch_etf_00991A.py
scripts/fetch_etf_00403A.py
scripts/fetch_passive_0050.py
scripts/fetch_passive_0056.py
scripts/fetch_passive_00830.py
scripts/fetch_passive_00878.py
scripts/fetch_passive_00891.py
scripts/fetch_passive_00918.py
scripts/fetch_passive_009805.py
scripts/fetch_passive_009820.py
scripts/generate_etf_summary.py
scripts/generate_market_pulse_summary.py
scripts/generate_quote_card.py
scripts/master_holding_quote_card.py
scripts/master_manual_positions.py
scripts/monitor_etf_quotes.py
scripts/monitor_gold_quote.py
scripts/monitor_market_charts.py
scripts/monitor_master_holding.py
scripts/overlay_market_sessions.py
scripts/rebroadcast_line.py
scripts/setup_rich_menu.py
scripts/update_and_notify.sh
scripts/update_market_pulse_volume.py
scripts/etf_benchmark/__init__.py
scripts/etf_benchmark/db.py
scripts/etf_benchmark/seed_tpex_etfs.csv
scripts/etf_benchmark/step1_universe.py
scripts/etf_benchmark/step2_schema.py
scripts/etf_benchmark/step3_backfill.py
scripts/etf_benchmark/step4_regimes.py
scripts/etf_benchmark/step5_score.py
services/stock-chart.service
services/stock-dashboard.service
services/oci-firewall.service
services/stock-fetch-1830-tw.service
services/stock-fetch-1830-tw.timer
services/stock-gold-monitor.service
services/stock-master-holding-monitor.service
services/stock-quote-monitor-00403a.service
services/stock-quote-monitor-0050.service
services/stock-quote-monitor-0056.service
services/stock-quote-monitor-00830.service
services/stock-quote-monitor-00878.service
services/stock-quote-monitor-00891.service
services/stock-quote-monitor-00918.service
services/stock-quote-monitor-009805.service
services/stock-quote-monitor-00981a.service
services/stock-quote-monitor-00988a.service
services/stock-quote-monitor-00991a.service
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
