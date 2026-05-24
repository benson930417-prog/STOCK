# STOCK: ETF Monitoring Dashboard & LINE Webhook

This repository contains the backend services, data pipelines, and frontend applications for an automated ETF tracking and monitoring system. It provides a real-time dashboard and a LINE messaging bot to query live metrics, view daily operation reports, and evaluate composite index performances (like the "吳大師" master holdings).

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Server Environment](#server-environment)
- [Systemd Services](#systemd-services)
- [Data Pipeline & Business Logic](#data-pipeline--business-logic)
- [ETF Benchmark Database](#etf-benchmark-database)
- [Repository Structure](#repository-structure)
- [Deployment & Operations](#deployment--operations)
- [Daily Run & Admin Email](#daily-run--admin-email)

---

## Architecture Overview

1. **Frontend (Streamlit):** Located in `app.py` and `src/ui/`. Renders dashboards for both Active (00981A, 00997A) and Passive (0050, 00830, 00878, 009805) ETFs.
2. **LINE Webhook (Flask):** Located in `api/webhook.py`. Listens for user commands, builds ETF quote cards, returns charts (Forex, Oil, Bonds, 市場脈動), and broadcasts daily operation reports.
3. **Data Fetchers:** Scheduled scripts that pull official NAVs and constituent holdings from respective providers (e.g., Cathay JSON APIs, Capital Fund, etc.).
4. **Quote Cache Monitors:** Persistent daemon services that fetch real-time stock prices (via Yahoo Finance/TradingView) during market hours and build localized JSON caches to serve the frontend and webhook rapidly.

## Server Environment

The application is deployed on an OCI (Oracle Cloud Infrastructure) instance.
- **Host / User:** `ubuntu@minecraft-vnic`
- **Root Directory:** `/home/ubuntu/STOCK`
- **Virtual Environment:** `/home/ubuntu/STOCK/venv`
- **Secrets File:** `/home/ubuntu/.stock_secrets` (Required for LINE tokens and GitHub integration)

## Systemd Services

The infrastructure relies heavily on `systemd` to keep background monitors and webhooks running. 

### Core Services
- `stock-dashboard.service`: The main Streamlit web application.
- `stock-webhook.service`: The LINE Bot webhook server.
- `stock-chart.service`: A persistent browser service using Playwright for generating ad-hoc snapshot charts.
- `stock-master-holding-monitor.service`: Monitors and generates the cached composite view for the "吳大師" master holdings.

### Per-ETF Quote Monitors
These services run continuously to update real-time pricing caches.
- `stock-quote-monitor-0050.service`
- `stock-quote-monitor-00830.service`
- `stock-quote-monitor-00981a.service`
- `stock-quote-monitor-00878.service`
- `stock-quote-monitor-009805.service`

### Scheduled Fetchers
- `stock-fetch-1730-tw.timer` / `stock-fetch-1730-tw.service`: Daily job that executes `/usr/bin/bash /home/ubuntu/STOCK/scripts/update_and_notify.sh 00981A 00997A 0050 00830 00878 009805`.

**Useful debugging commands:**
```bash
# Check service status
systemctl list-units --type=service | grep -i -E "stock|webhook|line|bot"
systemctl status stock-webhook.service

# View recent logs
journalctl -u stock-webhook.service -n 100 --no-pager
```

## Data Pipeline & Business Logic

### Business Logic Constraints
- **Live-Market Composites:** The "Composite %" solely represents the live-market weighted movement. If a market is closed, the composite is hidden.
- **Market Hours Compliance:** A stock's "open" or "closed" status is determined strictly by its official exchange hours, not by quote staleness.
  - TW: 09:00 - 13:30
  - JP: 09:00 - 11:30 and 12:30 - 15:30
  - HK: 09:30 - 12:00 and 13:00 - 16:00
  - US: Tracks Pre-market, Regular, Post-market, and After-close.
- **TSMC Futures (QFF1!):** Acts solely as a proxy for 2330.TW during Taiwan's night session. It is explicitly labeled as "期" (Futures) and does not double-count in weighted calculations.
- **Official Data Enforcement (Cathay ETFs):** The 00830 and 00878 fetchers strictly use the official Cathay JSON API (`cwapi.cathaysite.com.tw`). Unofficial fallbacks (like Yahoo) are deliberately excluded.

### Data Storage (`data/`)
- **Holdings/Logs:** `etf_{TICKER}_history.json`, `passive_{TICKER}_log.json`.
- **Quote Caches:** `quote_cache/etf_{TICKER}_quotes.json`, `quote_cache/master_holding.json`.
- **Generated Media:** Summaries and snapshot images are saved to `data/images/` and `data/summaries/`.
  `market_pulse_latest.png` is generated on demand from the Streamlit 市場脈動 tab through `stock-chart.service`.

## ETF Benchmark Database

A local-first, dividend-aware SQLite database covering every TWSE-listed ETF
plus key reference indices. Built from `yfinance` and verified with a transparent
cash-dividend total-return model. Powers the **ETF 比較** tab (replaces per-request Yahoo calls
with sub-second SQLite reads).

**Location:** `data/etf_bench/etf_bench.sqlite` (gitignored — built per host)

**Coverage:** fixed window from 2024-01-01 onward.
- 313 entries: 307 ETFs (from TWSE OpenAPI + TPEx seed list) + 6 reference
  indices (^TWII, ^TWOII, ^SOX, ^GSPC, ^IXIC, ^DJI).
- ~280 ETFs have actual prices (the rest are delisted shells).

**Tables:**
| Table | Purpose |
|---|---|
| `etfs` | Master list (ticker, name, market, fund_type, inception_date, ...) |
| `prices` | Daily OHLCV + Yahoo's `adj_close` (dividend-adjusted) |
| `dividends` | Ex-date / amount, with `is_income_equalization` flag |
| `splits` | Stock split events (ratio = new/old shares) |
| `benchmark` | TAIEX price + total-return index |
| `regimes` | Bull / correction / bear regime tags (TODO step 6) |
| `ingest_log` | One row per ticker per backfill run — success / empty / fail |
| `verification_log` | Per-ticker per-check pass/warn/fail — persistent audit trail |

**Pipeline scripts** (`scripts/etf_benchmark/`):
| Script | What it does |
|---|---|
| `step1_universe.py` | Builds `data/etf_bench/universe.csv` from TWSE OpenAPI + TPEx seed |
| `step2_schema.py` | Creates / resets SQLite schema (`--reset` drops tables first) |
| `step3_backfill.py` | yfinance batch download → prices/dividends/splits. `--incremental` = last 5 trading days only |
| `step4_verify.py` | Compares Yahoo `adj_close` baseline-to-latest returns against a transparent raw-close + cash-dividend reinvested total-return model |
| `step5_verify_nav.py` | Optional/manual diagnostic only: cross-checks Yahoo close vs issuer NAV snapshots. This is intentionally noisy for US/TW-listed ETFs because close, NAV, FX, and market timing differ. |
| `db.py` | Streamlit-cached read helpers (`get_universe`, `get_prices`, `get_dividends`, `get_avg_turnover_map`) |

**Verification model** (last full run, 2024-05-24 → today):
1. **Required freshness:** `step3_backfill --incremental` keeps prices, dividends, and splits current for the comparison tab.
2. **Fairness audit:** `step4_verify` ignores duplicate-event baseline dates, builds an independent total-return series from raw close + reinvested cash dividends, then compares every possible baseline-to-latest return against Yahoo `adj_close`. Known or inferred split/corporate-action caveats, such as `0052`, are surfaced as warnings even when Yahoo's adjusted price series appears usable.
3. **NAV diagnostic:** `step5_verify_nav` checks issuer NAV snapshots. US-tracking ETFs are reported as `INFO` because market close, NAV, and FX timing naturally differ; domestic ETF mismatches remain actionable `WARN`/`FAIL`.

**First-time setup on a new host:**
```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
python -m scripts.etf_benchmark.step1_universe       # build universe.csv
python -m scripts.etf_benchmark.step2_schema --reset # create schema
python -m scripts.etf_benchmark.step3_backfill       # full backfill from 2024-01-01
python -m scripts.etf_benchmark.step4_verify         # total-return fairness audit
# Optional only when debugging NAV snapshots:
# python -m scripts.etf_benchmark.step5_verify_nav
```

After that the daily 17:30 timer keeps it fresh via `--incremental` mode.

---

## Repository Structure

### `scripts/` (Pipeline & Processors)
- **`fetch_etf_*.py`**: Fetchers for actively managed ETFs.
- **`fetch_passive_*.py`**: Fetchers for passive ETFs (e.g., 0050, 00830, 00878).
- **`monitor_etf_quotes.py`**: The main daemon script for building the quote cache. Handles Yahoo pricing and TSMC futures.
- **`generate_quote_card.py`**: The shared image-rendering engine for ETF and Master Quote cards.
- **`master_holding_quote_card.py`**: Data adapter that expands ETF holdings into granular underlying exposure for the "吳大師" composite.
- **`update_and_notify.sh`**: The orchestrator. Runs fetches, refreshes the etf_benchmark DB, generates summaries, commits updates to GitHub, triggers LINE push messages, and emails an admin summary.
- **`admin_email.py`**: Gmail SMTP helper used by `update_and_notify.sh` to send the daily run summary.
- **`etf_benchmark/`**: The ETF benchmark database pipeline (see [section above](#etf-benchmark-database)).

### `api/` (Webhook)
- **`webhook.py`**: Flask server handling LineBot events. Routes text commands (e.g., "981", "0050", "油價", "吳大師") to their respective rendering and API integration functions.

### `src/ui/` (Frontend Layouts)
- **`etf_tab.py`**: Contains routing and visualization components for active and passive ETFs within the Streamlit dashboard.

## Deployment & Operations

### Standard Update Command
When pulling new code changes into the server, run the following:

```bash
cd /home/ubuntu/STOCK
git pull origin main --rebase --autostash
source venv/bin/activate
pip install -r requirements.txt -q
```

### Updating the LINE Rich Menu
If you added or modified buttons in the LINE Bot Rich Menu, run the setup script within the virtual environment to deploy the changes to LINE:

```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
python scripts/setup_rich_menu.py
```

### Restarting Services after Updates
If you add new services, update the webhook logic, or modify the image generation code, restart the relevant systemd services. Note that new `.service` files must be copied to `/etc/systemd/system/` first:

```bash
# Example: Copying a new service (only needed once for new services)
sudo cp services/stock-quote-monitor-009805.service /etc/systemd/system/

sudo systemctl daemon-reload

# Example: Enabling a new service to start on boot
sudo systemctl enable stock-quote-monitor-009805.service

sudo systemctl restart stock-webhook.service \
                       stock-master-holding-monitor.service \
                       stock-quote-monitor-0050.service \
                       stock-quote-monitor-00981a.service \
                       stock-quote-monitor-00997a.service \
                       stock-quote-monitor-00830.service \
                       stock-quote-monitor-00878.service \
                       stock-quote-monitor-009805.service
```

### Manual Triggering
To manually initialize data or test fetchers without waiting for the cron schedule:

```bash
source venv/bin/activate

# 1. Fetch official holdings
python scripts/fetch_etf_00981A.py
python scripts/fetch_passive_00830.py

# 2. Seed the quote cache (Ctrl+C after first successful update)
python scripts/monitor_etf_quotes.py 00981A --interval 60

# 3. Generate Master card cache
python scripts/master_holding_quote_card.py
```

---

## Daily Run & Admin Email

The `stock-fetch-1730-tw.timer` fires `scripts/update_and_notify.sh` every day
at 17:30 TPE (09:30 UTC). The script now performs the following steps in order,
captures each step's status, and emails a summary to the admin:

1. `git pull` (rebase + autostash) — pulls latest code
2. `pip install -r requirements.txt -q` — keep deps in sync
3. **Per-ETF fetchers** (the 7 active/passive scripts) — write `data/*_history.json`
4. **etf_benchmark refresh** —
   - `step3_backfill --incremental` (yfinance → SQLite, last 5 trading days)
   - `step4_verify` (audit Yahoo `adj_close` vs transparent total-return model)
   - `step5_verify_nav` (issuer NAV diagnostic; foreign-market timing differences are reported as INFO)
5. **Git commit + push** if any tracked data changed
6. **LINE broadcast** of active ETF reports if new data found
7. **Admin email summary** — sent every run, success or partial-fail

### Email format

Subject: `[STOCK] daily run — SUCCESS (2026-05-24 17:30) TPE`
Body: per-step OK/FAIL lines with key metrics (`step3` row counts, `step4` pass/fail totals,
and `step5` per-ETF status). If any step failed, a `FAILURE DETAILS` section
appends the last 30 lines of that step's full log.

### Gmail App Password setup (one-time)

The script uses Gmail SMTP via `scripts/admin_email.py`. You need a **Gmail App
Password** (NOT your regular Google password) because Google blocks normal
password auth from scripts.

**Steps to generate:**
1. Sign in to [https://myaccount.google.com](https://myaccount.google.com)
2. **Security** → ensure **2-Step Verification** is **ON** (App Passwords are
   only available when 2FA is enabled)
3. Go to [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. App name: `STOCK Server` → **Create**
5. Copy the 16-character password Google shows you (looks like
   `abcd efgh ijkl mnop`). Save it now — Google won't show it again.

### Adding the secrets on the OCI server

Edit `/home/ubuntu/.stock_secrets` and add three lines (alongside the existing
`LINE_TOKEN` etc.):

```bash
# Admin email (Gmail SMTP)
export GMAIL_APP_PASSWORD="abcdefghijklmnop"   # 16 chars, spaces OK or stripped
export ADMIN_EMAIL="benson930417@gmail.com"    # recipient
export GMAIL_FROM="benson930417@gmail.com"     # the Gmail account that owns the App Password (often same as ADMIN_EMAIL)
```

Permissions check:
```bash
chmod 600 /home/ubuntu/.stock_secrets
ls -l /home/ubuntu/.stock_secrets             # should show -rw------- ubuntu ubuntu
```

### Verifying email works (manual test)

```bash
cd /home/ubuntu/STOCK
source venv/bin/activate
source /home/ubuntu/.stock_secrets
python scripts/admin_email.py \
    --subject "[STOCK] manual SMTP test" \
    --body "If you see this, email config works."
```

You should receive the test email within ~30 seconds. If not, check:
- 2-Step Verification is actually ON
- App Password was copied correctly (no extra characters)
- `journalctl -u stock-fetch-1730-tw.service -n 50` shows the SMTP error

### Behaviour when secrets are missing

If `GMAIL_APP_PASSWORD` is not set, `admin_email.py` prints a warning and
exits 0 — the daily job will **never abort** because of a missing email
secret. The same applies to `LINE_TOKEN` (existing behaviour).

---

## Server Deployment (new host or after this commit)

```bash
# 1. Pull the new code
cd /home/ubuntu/STOCK
git pull origin main --rebase --autostash
source venv/bin/activate
pip install -r requirements.txt -q     # picks up yfinance >= 0.2.30

# 2. Build the etf_benchmark database for the first time (~15 sec)
python -m scripts.etf_benchmark.step1_universe
python -m scripts.etf_benchmark.step2_schema --reset
python -m scripts.etf_benchmark.step3_backfill
python -m scripts.etf_benchmark.step4_verify
python -m scripts.etf_benchmark.step5_verify_nav

# 3. Add Gmail App Password to /home/ubuntu/.stock_secrets
#    (see "Daily Run & Admin Email" section above)
nano /home/ubuntu/.stock_secrets

# 4. Verify email works
source /home/ubuntu/.stock_secrets
python scripts/admin_email.py --subject "[STOCK] manual test" --body "OK"

# 5. Restart the Streamlit dashboard so it picks up the new tab + DB reads
sudo systemctl restart stock-dashboard.service

# 6. (Optional) trigger a manual run of the daily orchestrator to verify
#    everything works end-to-end without waiting for 17:30
bash scripts/update_and_notify.sh
```

The 17:30 timer needs no changes — it already calls `update_and_notify.sh`,
which now includes the etf_benchmark refresh + admin email automatically.
