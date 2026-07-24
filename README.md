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
cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main
```

For code changes, use the **Standard Deployment** command in this README and restart the relevant services. If service files changed, also copy service templates and run `sudo systemctl daemon-reload`.

### Complete Agent Debugging Loop (SSH deploy + browser verify)

An AI agent on the dev machine can run the whole loop end-to-end with no human in the middle: **edit → check → commit → push → SSH deploy → browser-verify**. This has been tested and works.

**This repository is PUBLIC on GitHub. Never commit the SSH private key, or the dashboard session token, to any tracked file, commit message, or log output.** The machine-specific secrets live in the untracked, git-ignored `CLAUDE.local.md` at the repo root of the dev machine (Claude Code loads it automatically). It contains:

- The exact SSH command (`ssh -i "<local key path>" ubuntu@80.225.204.45`) for the production server.
- A permanent dashboard session URL (`http://80.225.204.45:8501/?session=<token>`) that skips the `VIEW_PASSWORD` gate, so the agent can open the dashboard in a browser tool and visually verify a deployed change.

### AI sandbox and browser rules

AI coding agents may run inside a restricted sandbox even though the repository, Chrome extension, SSH key, and production server exist on the host machine. Treat `EPERM`, `permission denied`, blocked network/DNS, unreadable host paths, or an SSH key rejected only because of sandbox ACLs as execution-boundary failures — not automatically as application bugs.

1. **Use the connected Chrome extension first for live dashboard QA.** Open the permanent session URL from `CLAUDE.local.md` in the user's connected Chrome, navigate to the changed tab, interact with the real controls, read the rendered text, and take a screenshot. Do not begin by launching another local browser runtime or writing a remote Playwright workaround when the Chrome extension is available.
2. **Move required commands outside the sandbox when the sandbox blocks them.** Use the agent host's explicit approval/escalation mechanism to rerun necessary network, SSH, Git, service, or host-file commands outside the sandbox. Ask for approval when required; do not repeatedly retry the same command inside the sandbox.
3. **Do not weaken host security to escape the sandbox.** Never broadly relax ACLs on the user's home directory, `AppData`, SSH key, or secret files. Prefer approved outside-sandbox execution. If a temporary restricted copy is genuinely unavoidable, keep it under a verified temp directory and remove it immediately.
4. **Browser fallback order:** connected Chrome extension → in-app browser (if Chrome is unavailable) → server-side headless browser only when neither connected browser works. State the fallback reason briefly.
5. **A missing local command alias is not an application failure.** In the Codex desktop sandbox, `python`, Git, or another host-installed binary may not be on `PATH`. Load and use the desktop workspace dependency runtime when available; do not install a second interpreter or change application code merely because the sandbox alias is absent. On production, always use `/home/ubuntu/STOCK/venv/bin/python` (or activate that virtualenv first).

The loop:

1. Edit locally and run checks (`python -m py_compile ...`, `bash -n ...`).
2. Commit and push to `origin main`.
3. Deploy with a one-shot SSH command (key path from `CLAUDE.local.md`):

   ```bash
   ssh -i "<KEY_PATH>" ubuntu@80.225.204.45 "cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main && source venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service"
   ```

   For README/doc-only changes, the `git pull` alone is enough — skip pip and restarts.
4. Verify in the browser: open the permanent session URL from `CLAUDE.local.md` with the connected Chrome extension first, navigate to the affected tab, interact with the changed controls, and confirm the change actually renders (screenshot and rendered text). If `CLAUDE.local.md` is missing (fresh machine), ask the user for the session URL or `VIEW_PASSWORD` — do not guess.
5. Report to the user what changed, what was deployed, and what was visually verified.

Notes:

- The server repo may be ahead of the local checkout because the 18:30 daily job auto-commits data. Run `git pull origin main --rebase --autostash` locally before committing.
- The dashboard is Streamlit: after a `stock-dashboard.service` restart it needs a few seconds before the page responds; reload once if the first browser load looks broken.

**Production working-tree safety.** The daily job legitimately leaves some tracked latest-data files modified between commits, commonly `data/stock_tags.json`, `data/tag_flow.json`, `data/etf_action_insight.json`, `data/etf_action_history.json`, and the tracked latest summary JPGs. Before every deploy, run `git status --short` on the server. Never use `git reset --hard`, `git clean`, or a whole-tree restore/stash just to make a pull succeed. If the incoming commit does not touch the dirty paths, use the explicit fast-forward command shown above; `-c pull.rebase=false` matters because the server's configured rebase mode otherwise rejects an unstaged working tree even with `--ff-only`. If an incoming commit does touch a dirty generated path, back up only that exact path under `/tmp`, restore only that path, fast-forward, then regenerate it with its documented producer. Preserve all unrelated live data.

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
├── src/tag_flow_rotation.py       # Shared window-independent category rotation engine
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
| `src/margin_risk.py` | Shared read-only financing-risk model used by both the dashboard and LINE card: cache loading, 1/5/20-session changes, risk wording, legal 130%/166% account-level references, and Taiwan direction colors. It never fetches data. |
| `src/tag_flow_rotation.py` | Window-independent 類股 rotation engine used only by the dashboard's `類股輪動` tab. It combines 3/10/20-session EWMA pressure, each category's own historical magnitude, ETF breadth, and two-session state confirmation. The 3-session EWMA is the early current-pressure warning; there is no separate literal 3-day sum. It never reads 概念 labels and never fetches data. |
| `src/tag_flow_events.py` | Stateful stock-level buy/hold/sell lifecycle engine shared by the dashboard, cached LINE lane images, scheduled text, and admin email. Before any event logic, each move is judged with no look-ahead against that same ETF + stock + direction's preceding 10 common sessions; the significance gate is `max(0.02% fund-size-normalized flow, 60% of the recent median)`, with the legacy ETF-wide baseline used only when fewer than two stock-specific observations exist. Fresh entry/re-entry/exit/reversal/restart events retain the original trigger date but also identify same-direction continuation on the next session (`昨日觸發・今日續買／續賣`). The rolling hold state shows exact upgrade gaps, evidence expiry, quiet-session cooling countdowns, one-session `續抱降溫` transitions, and `續抱→賣出` transitions rather than silently removing a card. Falling below the strong-hold threshold without a qualified sell is explicitly **not** an exit: it remains in the hold lane as `續抱降溫・尚無顯著賣出`. Category is context only and concepts are never interpreted. |
| `src/etf_intent_v3.py` | Profit-research-oriented V3 stock consensus engine. It removes mechanical portfolio scaling from ETF creations/redemptions using exact disclosed outstanding units (`fund_size / NAV` only for historical rows that predate the field), keeps only actual share trades whose direction agrees with the flow-adjusted allocation residual, and applies a no-look-ahead 20-common-session gate separately to every ETF/stock. A visible event requires at least **2/3 ETFs** to pass their own gates in the same direction on the same common session; one ETF never qualifies, including extreme actions, entries, reversals, and full exits. It maintains hidden buy/neutral/sell regimes and emits new consensus transitions plus at most one separately labelled next-session consensus confirmation. It never emits a visible hold basket and never reads concepts. |
| `src/etf_consensus_v4.py` | Final state/history engine. It reuses V3's flow-adjusted stock moves, then assigns V4's own no-look-ahead ETF-wide usual-action scale from the preceding 10 common sessions. Yellow is one high-information manager precursor, red is independently confirmed buying, and green is independently confirmed selling. Red/green require at least 2/3 individually significant signals whose three-common-session lifetimes overlap; a yellow score can never bypass that gate. Maintenance requires the participating ETF/stock 10-session net derivative to remain in the state direction, while freshness and the displayed expiry fall on every quiet session. A separate decision tier marks only fresh, meaningful, repeated or strongly newly formed red/green consensus as `core`; lower-priority red/green stays valid as `tracking` instead of being deleted. Fixed 3/10/20 EWMA half-lives describe current pressure/direction/background. The engine keeps 260-session date-keyed boards, compact state transitions, and per-ETF normalized history for replay. |
| `src/etf_consensus_v4_cards.py` | Shared final V4 card renderer. It puts `核心決策` before `有效追蹤` without changing the hard yellow/red/green state. Red/green cards show three aligned ETF microcharts; yellow shows only its triggering ETF. Bar height is the move divided by that ETF's preceding-10-session **usual non-zero single action**, never the significance-gate multiple or absolute dollars. Evidence also shows estimated 億元 for intuition, the ETF's usual single-action 億元, its 10-session net derivative, and a no-new-action expiry countdown. The decision window, latest significant frame, lane border, labels, evidence panel, and card hierarchy use the lane's red/amber/green colour rather than low-contrast grayscale; there are no hard-coded empty frames. |
| `src/ui/__init__.py` | Marks UI helpers as an import package. |
| `src/ui/etf_tab.py` | Active/passive ETF dashboard views and daily operation report UI. |
| `src/ui/etf_compare_tab.py` | ETF comparison tab backed by the local `data/etf_bench/etf_bench.sqlite` database. |
| `src/ui/market_pulse_tab.py` | Market pulse tab using ETF benchmark/index history and regime calculations. |
| `src/ui/margin_risk_tab.py` | 融資風險 tab: flexible 1m/3m/6m/1y/all/custom history, TAIEX overlay, financing-balance context, and a plain-language risk conclusion. Pure render of `data/margin_maintenance.csv` (no network). |
| `src/ui/tag_flow_tab.py` | `類股輪動` tab: category context only—a concise three-lane category board plus a category ranking and trend drill-down. It must not display stock names, stock-source tables, stock filters, or individual actions; those belong exclusively to `ETF 動作`. The heading says `最新…截至 YYYY-MM-DD`, never `今日`, because lane assignment is a 3/10/20-half-life combined state. The selector sits directly above the category chart and changes only ranking/chart audit evidence, never the rotation phase. Visible presets omit 3 and 5 sessions; only supported 1/10/20/60/120/240 ranges appear. Ranking uses normalized 相對力道, with estimated 億元 as approximate context only. Taiwan colors: red = 加碼/買進, green = 減碼/賣出, amber = pending. Pure render of `data/tag_flow.json` (no network). |
| `src/ui/tag_flow_v2_tab.py` | `ETF 動作` tab (the filename is historical): an insight-only three-lane stock lifecycle radar (`買進觀察／續抱參考／賣出警示`) with no interval selector or analytical panel beyond each stock's 20-session evidence sparkline. It shows every qualified card, including trigger continuation, one-buy-away hold watches, active holds, near-cooling countdowns, just-cooled holds, and hold-to-sell transitions. Its lane/card renderer is shared exactly with the three cached on-demand LINE images. |
| `src/ui/etf_intent_v3_tab.py` | `ETF 意圖 V3` tab: two visible consensus lanes only (`買方共識／賣方共識`). Each lane separates `本交易日新形成` from `前一交易日形成・今日仍確認`; a previous signal without current 2/3 confirmation disappears instead of filling the board. Existing positions, single-ETF actions, ordinary continuing buys, and cooling without an actual sell stay hidden. Each card states the transition, the 2/3 or 3/3 ETF drivers, estimated TWD context, unit-data quality, and a 20-session flow-adjusted residual sparkline. |
| `src/ui/etf_consensus_v4_tab.py` | `ETF 共識 V4` final tab. The only selector is a historical trading-date replay; it cannot change the fixed 3/10/20 model or rewrite the story. A compact core-decision strip gives the short list first, while the complete valid board remains below for audit. The frontend explicitly explains the hard colour gate, non-probability score, core-vs-tracking priority, 10-session net-derivative maintenance/downgrade rules, daily freshness decay, and usual-single-action chart scale. |

`類股輪動` periods mean the latest **common trading sessions** across the selected ETFs, not calendar days; 20 trading sessions naturally span about one calendar month. If 60/120/240 exceeds the current shared history, those buttons stay hidden rather than silently showing the same one-month data. They appear automatically after enough common sessions accumulate. The selector is immediately above the category chart and is a chart/audit viewport only. The rotation story always uses all available common history through `src/tag_flow_rotation.py`, so switching display ranges cannot rewrite the headline.

The rotation model deliberately has no decision interval. It gives every old session a gradually fading weight instead of dropping an entire buy wave at a boxcar boundary: **3-session EWMA = current pressure, 10-session EWMA = underlying direction, and 20-session EWMA = background**. These are decay speeds, not literal truncated day windows. "Strong/weak" magnitude is relative to that category's own prior smoothed-pressure history; a cross-category rank is secondary context. A coordinated buy/sell claim needs at least two selected ETFs pointing the same way, and a new phase must persist for two common sessions before it replaces the confirmed state. An abrupt reduction is surfaced when the 3-session EWMA current pressure turns materially negative—even while the 10/20-session EWMAs remain positive—without falsely calling that a confirmed long-term downtrend. This model is dashboard-only category context. The selector's interval totals are display/audit evidence only and never alter the model conclusion.

### ETF flow surface boundaries

- **類股輪動 (V1)** answers which `category` has sustained pressure. It never interprets concepts or individual stocks.
- **ETF 動作 (V2)** is the complete buy/hold/sell lifecycle and audit surface. Its visible hold lane means qualified persistent accumulation—not every currently held stock.
- **ETF 意圖 V3** remains the strict same-day transition prototype/audit surface.
- **ETF 共識 V4** is the final copy-the-manager surface. It keeps a historical state rather than a daily lottery: yellow is a high-information one-manager watch, while red/green require at least two independent managers. A stricter core tier reduces choice overload but never hides the remaining valid red/green consensus. Ordinary holdings and ordinary 1/3 activity remain hidden.

V3's primitive is not raw share change and not a raw second derivative. For each ETF disclosure pair it first computes:

```text
expected shares from ETF scale
  = prior shares × current outstanding ETF units / prior outstanding ETF units

flow-adjusted active shares
  = disclosed current shares − expected shares from ETF scale
```

All three active-ETF source workbooks disclose exact outstanding units. The fetchers now persist `meta.outstanding_units`; historical records created before this field use the auditable `fund_size / rounded NAV` estimate and carry `unit_quality=estimated`. V3 stores both raw and adjusted directions. A residual that opposes the actual share trade remains audit context but is not shown as a copyable buy/sell.

Each ETF/stock move is judged with no look-ahead against its preceding 20 common sessions. Only after those individual tests does V3 apply the copyability gate: at least two independent ETFs must be significant in the same direction on the same common session. There are no 1/3 exceptions for extreme size, entry, re-entry, reversal, or exit. The second ETF's arrival is the consensus trigger. Conflicting or single-manager actions remain silent. The backend keeps decaying buy/neutral/sell state to distinguish onset, reversal, restart, acceleration, and breadth expansion. The current surface contains only events formed today and previous-session events that again receive 2/3 confirmation today; an unconfirmed prior event is not carried forward.

Dashboard authentication uses `VIEW_PASSWORD` and `ADMIN_PASSWORD` from Streamlit secrets, environment variables, or `/home/ubuntu/.stock_secrets` depending on the runtime.

Market Pulse (`src/ui/market_pulse_tab.py`) is a dashboard view, not a live data fetcher. It reads the local ETF benchmark DB plus the server-local TWSE volume cache. Do not add slow network calls to the Streamlit render path; add a script/cache refresh step instead.

Margin Risk (`src/ui/margin_risk_tab.py`) follows the same cache-only rule. It must never be presented as an exchange-published daily "average account maintenance rate": the public TWSE/TPEx inputs omit client-level supplementary collateral. The feature is explicitly the transparent `全市場融資擔保估算率` documented below.

## LINE Webhook

`api/webhook.py` is a Flask app listening on `0.0.0.0:8080` when executed directly. It requires LINE credentials and calls the chart service through `CHART_SERVICE_URL`, defaulting to `http://127.0.0.1:5005`.

Common LINE commands:

| Command | Response |
|---|---|
| `981`, `988`, `0050`, `830`, `878`, `891`, `918`, `9805`, `9820` | ETF quote card/report for the mapped ETF. |
| `吳大師` | Master holding portfolio card. |
| `ETF動作` / `ETF 動作` | Currently exactly three reply objects: mobile-width vertical images for `買進觀察`, `續抱參考`, and `賣出警示`, in that order. The cached text remains available but is temporarily disabled by `ETF_ACTION_INCLUDE_TEXT = False` in `api/webhook.py`; switch it to `True` to restore text-first delivery. Each image is only its website lane—no repeated methodology/header block—and includes every card in that lane. 類股 is context only and 概念 is never interpreted. The three JPGs are regenerated daily and served from cache; the webhook never screenshots or recalculates on demand. |
| `ETF共識` / `ETF意圖` | Exactly three cached 1800px-wide image reply objects and no text: red buy, green sell, then yellow one-manager watch. Each former page 1/page 2 pair is placed side by side in one image, split evenly by its existing score order and labelled with non-overlapping rank ranges. Every valid card appears exactly once. The webhook only validates and serves the manifest cache; it never calculates or screenshots on demand. The rich-menu action uses `ETF共識`; legacy `ETF意圖` aliases intentionally route to the same final result. |
| `市場脈動` | Latest generated market pulse image. |
| `融資維持率` / `融資風險` | Latest generated financing-risk image. This is also a visible quick-reply button under `吳大師`. |
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

**Snapshot crop rules.** Generic 1-month charts (`oil`, `brent`, `bond`, `gold`, `usdtwd`, `usdjpy`, `usdchf`) may detect the chart's `y` and `height`, but they must use the full TradingView viewport width: `clip.x = 0` and `clip.width = window.innerWidth`. Their LINE quote text remains the same four performance rows (`1日`, `1週`, `1月`, `6月`); only the plotted chart window is one month. Do not crop or shift the x-axis for generic charts; the right price axis and last-price marker live at the far right edge. NASDAQ is the exception: it uses a separate IG-NASDAQ 24h branch with its own `1200x900` viewport, fixed `y`/`height`, 1-day button click, and trading-session overlay. Do not "simplify" NASDAQ into the generic crop path unless it is manually reverified.

**Chart-service restart timing.** `stock-chart.service` preloads all eight TradingView pages before port `5005` becomes ready; a production restart can therefore take roughly 60–90 seconds. Early `curl: (7) Failed to connect` lines during that warm-up are expected. Wait for `curl -fsS http://127.0.0.1:5005/docs`, then regenerate the requested caches with `scripts/monitor_market_charts.py ... --once`, and finally confirm both `stock-chart.service` and `stock-market-chart-monitor.service` are `active`. Running the monitor or opening the cached public JPGs is safe and sends no LINE message.

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
   cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now stock-market-chart-monitor.service && sudo systemctl restart stock-chart.service stock-market-chart-monitor.service stock-webhook.service
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
| `scripts/build_stock_tags.py` | Scrapes cmoney forum per stock → `data/stock_tags.json` (one 類股 category plus 概念股 labels retained only as stock-level notes). Incremental; covers the union of active-ETF holdings; monthly refresh. Supplies category context to `類股輪動` and `ETF 動作`. |
| `scripts/build_tag_flow.py` | Theme-flow engine → `data/tag_flow.json`. Stores price-drift-free daily ActiveWeight observations plus estimated TWD cash flow (`ActiveWeight × disclosed fund size`) and the structural action (`new_position`, `increase`, `decrease`, `full_exit`) for every available ETF session, with no-look-ahead empirical trade-size percentiles from each ETF's prior 20 sessions. The UI aggregates any selected range. Reads histories only, no network. |
| `scripts/generate_etf_action_insight.py` | Converts `data/tag_flow.json` through the lifecycle engine into cached `data/etf_action_insight.json` (schema v2) and upserts the latest two common-session snapshots into `data/etf_action_history.json`. The two-day backfill preserves yesterday/today comparisons even when the feature first runs after today's fetch. `line_text` remains the concise copy embedded in the scheduled daily report's first text object and admin email. Every stock follows the standardized `類股／動作／ETF／判定／狀態／進度（when relevant）／依據` grammar. It never sends LINE itself. |
| `scripts/generate_etf_action_summary.py` | Renders `data/etf_action_insight.json` through the website's shared lane/card renderer and writes three mobile-width images: `etf_action_buy_latest.jpg`, `etf_action_hold_latest.jpg`, and `etf_action_sell_latest.jpg`. Every image contains one lane only, captures its computed body height, verifies its rendered-card count, and must never crop the last card. |
| `scripts/build_etf_intent_v3.py` | Reads the three active-ETF holding histories plus category metadata and writes the self-contained schema-v3 `data/etf_intent_v3.json`. It is pure local computation and sends nothing. |
| `scripts/generate_etf_intent_v3_summary.py` | Renders the shared V3 website cards into two uncropped, mobile-width LINE images: `etf_intent_v3_buy_latest.jpg` and `etf_intent_v3_sell_latest.jpg`. It verifies that every cached event became a card before saving. |
| `scripts/build_etf_consensus_v4.py` | Builds the final date-keyed V4 state/history cache from the three active ETF histories and 類股 context. Pure local computation; no network and no LINE send. |
| `scripts/generate_etf_consensus_v4_summary.py` | Renders the shared V4 website cards into exactly three uncropped 1800px-wide LINE images. Each lane is split into two balanced, side-by-side rank ranges so the reply is wide rather than skinny. Core/tracking remains visible on each card badge. The generator verifies that every source card is rendered exactly once and writes `etf_consensus_v4_manifest.json` as the webhook reply contract. |
| `scripts/line_active_report_payload.py` | Single source of truth for the scheduled and manual active-ETF LINE payload: exactly one combined header/action text object followed by at most four active-ETF images. It hard-fails above the five-object LINE limit instead of chunking or starting a second broadcast. |
| `scripts/generate_etf_summary.py` | Builds daily ETF summary images for LINE broadcast. |
| `scripts/generate_market_pulse_summary.py` | Renders the market pulse summary image served by the LINE `市場脈動` command. |
| `scripts/update_margin_maintenance.py` | Fetches official TWSE + TPEx daily margin balances/prices and writes the server-local `data/margin_maintenance.csv` public-data estimate cache. |
| `scripts/generate_margin_maintenance_summary.py` | Renders the daily `data/summaries/margin_maintenance_latest.jpg` card served by the LINE `融資維持率` / `融資風險` command. |
| `scripts/generate_quote_card.py` | Shared quote-card image renderer for ETF/master-holding views. |
| `scripts/master_holding_quote_card.py` | Expands ETF holdings into the configured master portfolio and renders/caches its quote card. |
| `scripts/master_manual_positions.py` | Manual position data/helpers for the master portfolio. |
| `scripts/monitor_etf_quotes.py` | Long-running quote cache daemon for one ETF ticker. |
| `scripts/monitor_gold_quote.py` | Long-running GOLD quote monitor. |
| `scripts/monitor_market_charts.py` | Long-running TradingView market text/chart cache monitor for LINE chart commands such as NASDAQ. |
| `scripts/monitor_master_holding.py` | Long-running master-holding cache monitor. |
| `scripts/overlay_market_sessions.py` | Draws Taiwan/US session markers on the special NASDAQ 24h chart image. |
| `scripts/rebroadcast_line.py` | Admin-only manual rebroadcast helper using the same one-text-plus-at-most-four-images payload builder as the scheduled daily send. |
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
cd /home/ubuntu/STOCK && source venv/bin/activate && python -m scripts.etf_benchmark.step1_universe && python -m scripts.etf_benchmark.step2_schema --reset && python -m scripts.etf_benchmark.step3_backfill && python -m scripts.etf_benchmark.step4_regimes && python -m scripts.etf_benchmark.step5_score --backfill && python scripts/update_market_pulse_volume.py --backfill-years 5 && python scripts/update_margin_maintenance.py --backfill-years 1 && python scripts/generate_margin_maintenance_summary.py
```

Daily refresh uses:

```bash
python -m scripts.etf_benchmark.step3_backfill --incremental && python -m scripts.etf_benchmark.step4_regimes && python -m scripts.etf_benchmark.step5_score && python scripts/update_market_pulse_volume.py --months 4 && python scripts/update_margin_maintenance.py --days 10 && python scripts/generate_margin_maintenance_summary.py
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

### Margin Risk Data Flow

The feature is a transparent **public-data estimate**, not a copied vendor series and not the actual daily average of brokerage customer accounts:

```text
全市場融資擔保估算率
= Σ(TWSE + TPEx 非 ETF 今日融資餘額張數 × 當日收盤價 × 1,000)
  ÷ Σ(TWSE + TPEx 今日融資金額餘額) × 100
```

Official inputs are TWSE `MI_MARGN` + `MI_INDEX` and TPEx `融資融券餘額` + `每日收盤行情`. ETF codes (the exchanges' `00...` family) are excluded from the numerator, following MacroMicro's published methodology note; the denominator deliberately remains the exchanges' unified aggregate financing balance. The cache records the excluded ETF collateral value so this adjustment is auditable. The denominator reports are in 仟元; the cache converts displayed money to 億元. The legal 130% call and 166% cure levels are shown only as **account-level references**. They must never be described as exact trigger lines for the aggregate estimate because the public reports do not include supplementary collateral held inside each client's whole account.

The system has three read/write boundaries:

- `scripts/update_margin_maintenance.py` is the only network writer. Daily: `--days 10`; one-time history: `--backfill-years 1` (skips cached dates, so rerunning repairs only gaps; add `--force` only for an intentional full refresh). Official requests retry transient failures, preserve existing rows, and a long backfill writes atomic 25-date checkpoints. If SSH or an exchange fails midway, rerun the same command to resume the missing dates; do not delete the CSV.
- `src/ui/margin_risk_tab.py` only reads `data/margin_maintenance.csv` and offers 1m/3m/6m/1y/all/custom ranges.
- `scripts/generate_margin_maintenance_summary.py` reads that same cache and writes the tracked latest LINE card. `api/webhook.py` only serves the cached JPG; it must never recalculate or fetch data on demand.

The daily orchestrator logs `[margin-risk] rows=... latest=... financing=... coverage=...`. The admin email therefore proves that both exchanges were incorporated and reports price-match coverage. The card is generated every day but is not automatically broadcast; it is returned free as an on-demand LINE reply from the `吳大師` quick button.

## Data Directory

Tracked files in `data/` are source/history state that should move with the repo:

| File pattern | Purpose |
|---|---|
| `data/etf_00403A_history.json`, `data/etf_00981A_history.json`, `data/etf_00988A_history.json`, `data/etf_00991A_history.json` | Active ETF official history snapshots. |
| `data/etf_00403A_log.json`, `data/etf_00981A_log.json`, `data/etf_00988A_log.json`, `data/etf_00991A_log.json` | Active ETF fetch logs/status. |
| `data/passive_*_history.json` | Passive ETF official history snapshots for 0050, 0056, 00830, 00878, 00891, 00918, 009805, and 009820. |
| `data/passive_*_log.json` | Passive ETF fetch logs/status. |
| `data/stock_tags.json` | cmoney stock-tag map for active-ETF holdings; 類股 is the sole aggregation field, while 概念股 is display-only stock metadata. Built by `scripts/build_stock_tags.py`, refreshed monthly in the daily job. |
| `data/tag_flow.json` | `類股輪動` and `ETF 動作` daily observation store (schema v2: per ETF/session/stock normalized flow, estimated TWD cash flow, disclosed fund size, and trailing percentile context); rebuilt daily by `scripts/build_tag_flow.py`. |
| `data/etf_action_insight.json` | Cached complete stock-level buy/hold/sell result plus the scheduled-report/admin-email text, rebuilt daily by `scripts/generate_etf_action_insight.py`. Category rotation has no LINE cache or image. |
| `data/etf_action_history.json` | Date-keyed ETF-action lifecycle snapshots (up to 260 common sessions), upserted by `scripts/generate_etf_action_insight.py`. It records both yesterday and today when available so transitions can be audited without relying on screenshots. |
| `data/etf_intent_v3.json` | V3 flow-adjusted consensus cache (schema 4): exact/estimated ETF-unit quality, strict 2/3 same-session signals split into new/confirmed phases, the complete historical consensus-event log, and hidden-regime audit rows. Rebuilt daily by `scripts/build_etf_intent_v3.py`. |
| `data/etf_consensus_v4.json` | Final V4 state/history cache: date-keyed yellow/red/green boards, the independent `core/tracking/watch` decision tier and reason, hard-gate methodology, transparent score components (including freshness), compact state-transition audit, and up to 260 aligned per-ETF usual-action sessions. Rebuilt daily by `scripts/build_etf_consensus_v4.py`. |
| `data/master_manual_positions.json` | Manual master portfolio positions. |
| `data/master_meta.json` | Master portfolio metadata/state. |
| `data/master_trades.csv` | Manual trade ledger for the master portfolio. |
| `data/summaries/market_pulse_latest.jpg` | Latest generated Market Pulse LINE image. |
| `data/summaries/margin_maintenance_latest.jpg` | Latest generated 融資風險 LINE card. It is regenerated and committed by the daily job, then served on demand. |
| `data/summaries/etf_action_{buy,hold,sell}_latest.jpg` | Three latest mobile-width `ETF 動作` lane images. Together they contain every event in the qualified board; they are regenerated and committed daily, then served directly in the free on-demand LINE reply. The optional cached text is currently disabled. |
| `data/summaries/etf_intent_v3_{buy,sell}_latest.jpg` | Legacy V3 audit images; no longer used by the main rich-menu action. |
| `data/summaries/etf_consensus_v4_{buy,sell,watch}_wide_latest.jpg` + `etf_consensus_v4_manifest.json` | Three two-column V4 LINE images in buy/sell/watch order. The manifest is the sole ordered reply list; no text object is included. Images and manifest are generated/ignored artifacts, not tracked source. |

Ignored generated data:

| Path | Producer |
|---|---|
| `data/images/` | `chart_service.py`, quote-card renderers, webhook responses. |
| `data/summaries/` | Generated report images. Everything is ignored except the explicitly tracked `market_pulse_latest.jpg` and `margin_maintenance_latest.jpg` cards. |
| `data/quote_cache/` | Quote monitor services. |
| `data/market_pulse_volume.csv` | Server-local TWSE market turnover/volume cache from `scripts/update_market_pulse_volume.py`. |
| `data/margin_maintenance.csv` | Server-local TWSE+TPEx financing-collateral estimate from `scripts/update_margin_maintenance.py`. |
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
cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main && source venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart stock-chart.service stock-webhook.service stock-dashboard.service
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
4. Run all active/passive ETF fetchers requested by the service arguments. Each fetcher gets **one retry after 90s** (`run_step_retry`) because issuer sources occasionally hiccup for a single run (e.g. Yuanta's page rendering without its weight table, issuer API connect timeouts). A fetch that recovers on retry is reported `[OK] ... (recovered on attempt 2/2)` and does not flip the run to PARTIAL_FAIL; a hard outage still fails after the retry.
5. Refresh ETF benchmark SQLite data, regime tags, and score history.
6. Refresh the server-local Market Pulse TWSE volume cache.
7. Generate the market pulse image.
8. Run a live Cmoney category canary, validate category coverage, build the shared flow observations, generate the complete stock-level `ETF 動作` cache and three audit images, then build V3 and render its two mobile intent images.
9. Commit and push changed tracked data.
10. When new active ETF data exists, send one LINE broadcast containing exactly one combined report/action text object plus the four active-ETF images.
11. Send the admin email summary with success/failure details plus the same cached `ETF 動作` text. The category-only `類股輪動` dashboard never creates a LINE image or message.

Manual run — admin-only (⚠️ **sends the paid LINE broadcast to ALL followers when active ETFs have new data** — see Invariant #11; agents must not run this to test code changes, only the admin triggers it deliberately):

```bash
cd /home/ubuntu/STOCK && source venv/bin/activate && bash scripts/update_and_notify.sh 00403A 00981A 00988A 0050 0056 00830 00878 00891 00918 009805 009820
```

## Rich Menu

Run this when LINE rich-menu buttons change:

```bash
cd /home/ubuntu/STOCK && source venv/bin/activate && source /home/ubuntu/.stock_secrets && python scripts/setup_rich_menu.py
```

The rich menu uses three LINE rich-menu aliases. Page 1 is fast market/macro plus direct `市場脈動`, `吳大師`, and `ETF 共識` actions; Page 2 is the primary ETF watchlist, and Page 3 is ETF overflow. Navigation convention is fixed: previous page is bottom-left, next page is bottom-right, and the last page uses bottom-right `首頁` until another page is needed. Page 1's narrow five-column row uses the width-fitted `ETF 共識` / `觀察・買・賣` tile and `那斯達克` / `24 小時` tile. The former three-lane `ETF動作` remains available as a typed command for audit.

## ⚠️ Invariants & Gotchas (read before editing)

Each of these is a trap that has already caused a wrong result or a missed step. Check them before and after any change.

1. **The daily fetch list is the timer's `ExecStart` args, NOT the script default.** `services/stock-fetch-1830-tw.service` passes an explicit ticker list to `update_and_notify.sh`, which makes `ETFS=("$@")` override the default `ETFS=(...)` array. Editing only the default array does nothing for the scheduled run — you must edit the `ExecStart` line. (Only the manual webhook "每日更新" path, which passes no args, uses the default.) `0056` is intentionally/historically absent from the `ExecStart` args even though it is in the default array and has a quote monitor.

2. **One ETF ticker is hardcoded in ~16 places.** Adding/removing an ETF is a cross-file change. Before editing, list every occurrence:
   ```bash
   grep -rnE '00981A|00988A|<TICKER>' --include=*.py --include=*.sh --include=*.service . | grep -v __pycache__
   ```
   Missing one silently breaks a single surface (no error). The authoritative list is the touch-point table under **Add One ETF**.

3. **Quote-card output has TWO independent render paths — keep them in sync.** The **text** bubble is built by `build_etf_quote_text` in `api/webhook.py`; the **image** is drawn by `scripts/generate_quote_card.py` (`_fmt_pct` etc.) and regenerated by each quote monitor. A formatting rule (e.g. "no `+` sign on percentages") must be applied in BOTH. The master 吳大師 text is a third path in `scripts/master_holding_quote_card.py`.

4. **`master_trades.csv` merge is append + dedupe, never window-replace.** `merge_into_master` in `app.py` must not delete rows by date window — broker weekly exports overlap (handled by `_key`/stable-key dedupe) and routine exports legitimately omit special transactions (in-kind subscriptions). Window-replace once silently wiped a 101,000-share buy. Recover lost rows from the CSV's git history if needed. **定期定額 corollary: `委託書號` must stay part of BOTH dedupe keys.** Recurring-savings (定期定額) orders — lowercase `p`-prefixed 委託書號, odd-lot 現買 — can produce rows byte-identical in every other column (e.g. 4 × 169-share 元大台灣50 buys on 2026-07-13, same price/fee/net). Dropping 委託書號 from `_key`/stable-key would silently collapse them into one buy. The 2026-07 Cathay export format (banner row + 15 columns) was verified end-to-end against this pipeline. **Reclassification corollary: an upload's rows supersede master rows for the same order (股名+日期+委託書號).** An export pulled before the broker finalizes same-day matching shows a buy as 現買; the next export shows the same order as 沖買. Both would survive dedupe (買賣別 is in `_key`) and double the position — three 2026-07-08 0050 buys became 6,000 phantom shares this way. `merge_into_master` therefore replaces same-order rows with the upload's version (order-scoped — NOT a window-replace; orders absent from the upload are untouched). Upload exports in generation order: re-uploading a stale export regresses classifications until the newest export is re-uploaded.

5. **吳大師 financial definitions** (LINE 總覽 + website KPIs), keep consistent:
   - `成本` (LINE) / `此次投入成本` (dashboard) = **current open-position cost basis** (NOT net cash deployed — that figure, 成本−已實, legitimately goes negative on house money and was removed from both surfaces).
   - `已實` = realized P/L. Compute it via `scripts/realized_pnl.py` (`compute_realized_total`); it is verified equal to the dashboard's realized KPI — do not invent a second formula.
   - `未實` / `此次未實現損益` = current-position unrealized P/L, **after** estimated exit fee+tax (so it can read lower/negative vs a broker's gross). Its % (vs open cost) is the **only** percentage shown — do NOT add a total return-%; any % against net cash deployed is meaningless once it is ≤0.
   - `累計總損益` = 已實 + 未實 since inception (shown on both the dashboard KPI row and the LINE 總覽).
   - `槓桿值` = sum of the look-through expansion weights (total exposure ÷ capital); **>100% is expected** with leveraged holdings.

6. **Leveraged ETFs decompose to a proxy basket with doubled weights — totals over 100% on purpose.** `LEVERAGED_ETF_PROXY` (duplicated in `app.py` and `scripts/master_holding_quote_card.py`) maps `00631L → {expand_as: 0050, leverage: 2.0}`. The expansion weight denominator is the real portfolio value, so leveraged constituents read as a true share of capital and the column legitimately totals >100%. Do not renormalise it back to 100%.

7. **LINE broadcast/push caps at 5 message objects, and this repo intentionally uses all 5 in one request.** The daily active-ETF broadcast is exactly **1 combined text object** (existing report header plus cached `ETF 動作` insight underneath) + **4 active-ETF images**. `scripts/line_active_report_payload.py` is the single payload builder for both scheduled and manual sends. It must reject a fifth active image; never chunk, create a second broadcast, or append a separate category image/message.

8. **Per-ETF data prefix:** active = `data/etf_<TICKER>_*`, passive = `data/passive_<TICKER>_*`. `monitor_etf_quotes.py` and `_latest_history_payload` pick the prefix from the `PASSIVE_*` sets — an active ETF must NOT be in those sets.

9. **Asian markets with a midday break must stay in-session during lunch.** Tokyo (11:30–12:30), Hong Kong (12:00–13:00) and Shanghai/Shenzhen (11:30–13:00) pause for lunch but are NOT closed for the day. `_regular_session_bounds` (in `monitor_etf_quotes.py`) therefore spans open→close as ONE window for JP/HK/CN; do not split it back into morning/afternoon windows or the lunch gap is mis-detected as `CLOSE`, which freezes those holdings at the morning close and dumps them into the 已收盤 composite instead of 交易中. A holding showing 已收盤 with a stale ~morning timestamp while its own exchange is mid-day is the symptom.

10. **Market Pulse volume is daily cached, never fetched on demand.** `src/ui/market_pulse_tab.py` must read `data/market_pulse_volume.csv` only. `scripts/update_market_pulse_volume.py` is the only place that should call TWSE `FMTQIK`; it runs at 18:30 through `scripts/update_and_notify.sh`. If the dashboard says the cache is missing, run `python scripts/update_market_pulse_volume.py --backfill-years 5` once on the server, then let the daily `--months 4` refresh maintain it. Do not add `requests` back into the Streamlit render path.

11. **LINE push/broadcast is PAID — only the daily job or an explicit admin action may send.** Push/broadcast messages consume the paid LINE message quota (only replies to user-initiated webhook messages are free). Sanctioned senders: the 18:30 daily broadcast step inside `scripts/update_and_notify.sh`, an admin deliberately triggering that same run (manual server run or the webhook `每日更新` admin command), and `scripts/rebroadcast_line.py` when the admin explicitly asks. Agents must NEVER send on their own initiative: do NOT run `update_and_notify.sh` end-to-end to test code changes (its broadcast step pushes to ALL followers), do NOT call `api.line.me` push/broadcast endpoints manually, and do NOT "verify" LINE features by sending. Fetchers, quote/chart monitors, `chart_service.py`, cache checks, and the dashboard send nothing and are always safe to run.

12. **Cached TradingView charts use a 1-month window and full x-width except NASDAQ.** Generic market charts (`oil`, `brent`, `bond`, `gold`, `usdtwd`, `usdjpy`, `usdchf`) use `timeframe=1M`, while their LINE quote text retains the existing `1日` / `1週` / `1月` / `6月` rows. They should crop only vertically and must keep `clip.x=0`, `clip.width=window.innerWidth`; otherwise the right price axis/last-price marker gets cut off. NASDAQ is intentionally special: it uses IG-NASDAQ 24h, a fixed `1200x900` viewport, custom y/height, a 1-day click, and `overlay_market_sessions.py`. Do not share generic crop edits into NASDAQ unless reverified with a real screenshot.

13. **類股輪動 interpretation is category-only.** Every ranking, chart, persistence calculation, ETF-consensus calculation, filter, and drill-down in `src/ui/tag_flow_tab.py` must aggregate by the stock's single `category` field. Never aggregate, rank, score, filter, or narrate using 概念股 labels. This tab must not show stock names, stock-source tables, stock filters, or individual actions; all stock-level decisions belong to `ETF 動作`.

14. **類股輪動 ranks by 相對力道; 億元 is context only.** A larger fund such as 00981A naturally trades more cash, so actual TWD must never drive category ranking or the "most added/trimmed" narrative. Rank with normalized ActiveWeight (each ETF's estimated trade cash ÷ its own disclosed fund size, averaged across selected ETFs). Show estimated 億元 beside it for intuition and label it as approximate because disclosed weights/fund sizes are rounded. The drill-down timeline uses actual 億元 bars plus a normalized cumulative 相對力道 line.
15. **Both ETF-flow surfaces use Taiwan direction colors.** Red means 加碼/買進, green means 減碼/賣出, and gray means near-flat. Apply that convention to category cards, charts, and stock action lanes even if another dashboard surface uses a Western color toggle. Color describes flow direction, not guaranteed future performance.

16. **The daily Cmoney `[OK]` must prove the live parser, not just reuse cache.** `update_and_notify.sh` runs `build_stock_tags.py --probe 2308`, which re-fetches one stable category page every run while also filling missing holdings. Failed/missing categories remain retryable and must make the step `PARTIAL_FAIL`; the admin email includes `[cmoney-tags]` coverage and canary output. Never interpret 概念股 in the generated insight.

17. **`類股輪動` is dashboard-only category context.** `src/tag_flow_rotation.py` is the only source of category phase, ranking, breadth, confidence, and 3-session-EWMA current-pressure warnings. It uses all common history with EWMA half-lives 3/10/20 for current pressure/underlying direction/background, judges magnitude against each category's own prior pressure history, requires majority-ETF direction breadth, and confirms a changed phase only after two consecutive common sessions. The graph selector belongs directly above the category trend chart; unsupported 60/120/240 ranges remain hidden until enough common history exists. Changing the graph range must not change phase or rank. There is deliberately no category-rotation LINE command, cache, image, scheduled message, email narrative, or stock list.

18. **`ETF 動作` is the stateful three-lane audit lifecycle; do not silently turn it into V3.** `src/tag_flow_events.py` is its single evidence source; the website and `scripts/generate_etf_action_summary.py` share `src/tag_flow_action_cards.py`. The on-demand `ETF動作` reply remains exactly **3 mobile-width vertical images**, ordered buy, hold, sell. The cached text remains available but `ETF_ACTION_INCLUDE_TEXT = False` temporarily keeps it out of the on-demand reply. Each image renders one complete website lane with every qualified card; computed-height capture and rendered-card-count verification are mandatory, and fixed crops/`max-height`/`overflow:hidden` are forbidden. The webhook serves cached images only and must never recalculate or screenshot during a reply. The scheduled daily broadcast remains one combined report/action text plus exactly four active-ETF images.

    **Significance comes first.** A routine action may enter any strategy only after it clears a no-look-ahead threshold for that same ETF, stock, and direction over the preceding 10 common sessions: `max(0.02% fund-size-normalized flow, 60% of the median absolute action)`. Fewer than two matching stock observations uses the existing ETF-wide fallback (`35%` of its trailing trade median). This is fund-size normalized; absolute 億元 must never rank or qualify signals. `new_position` remains the sole tiny-size buy exception because the holding list itself changed; tiny residual full exits remain hidden.

    **Fresh-event qualification remains explicit.** Ordinary changes need at least 2/3 ETFs aligned. A 1/3 action is hidden unless the disclosed holding list changed (`新建倉／重新建倉／完整出清`) or a genuine reversal persists for two common sessions. `new_position` after any earlier full exit by that ETF is `重新建倉`. Trigger date and current confirmation date are separate: a yesterday trigger followed by another significant same-direction move today must say `昨日觸發・今日續買` or `昨日觸發・今日續賣` and mark both sessions in the sparkline; it must not keep a stale yesterday-only label. A trigger with no second-day continuation remains `昨日觸發` and leaves after the two-session fresh window.

    **Hold is a rolling lifecycle.** Entry/continuation requires at least 2 participating ETFs, at least 4 significant buy days in the latest 10 common sessions, buy days at least 2 greater than sell days, and normalized net flow of at least `+0.20%`. The latest session does not have to be a buy: a hold survives a quiet day while its rolling evidence still qualifies. Every relevant positive card shows the missing buy days/ETF breadth/net strength; a one-buy-away card appears only when one additional significant buy day would satisfy the count/balance and all other criteria already pass. Active holds show how many quiet sessions remain before strength cools. A just-failed strong-hold remains visible for one session as `續抱降溫`; without a qualified sell it explicitly says `尚無顯著賣出` and remains a hold, while a qualified sell moves it to `續抱→賣出警示`. `data/etf_action_history.json` upserts the latest two daily snapshots and retains 260 sessions for audit.

    **Sparkline evidence grammar:** every card keeps the full 20-session normalized-flow background. Fresh-event frames still mark their actual trigger strategy. Hold cards never frame the latest session merely because the state was recalculated: small amber/green evidence dots identify only the significant buy/sell days currently counted by the rolling hold state, and a subtle amber frame marks the latest such evidence day. The old hard 10-session bracket was removed because it falsely suggested that the surrounding history and per-stock significance baseline were ignored.

    Every card follows `類股／動作／ETF／判定／狀態／門檻／進度（when relevant）／依據`. Its 20-session micro chart sums the three ETFs' normalized flows (red buy, green sell) and uses a local per-stock vertical scale. Fresh events frame the original trigger and add a second neutral frame only for an actual current continuation; hold cards use evidence dots plus the latest-evidence frame described above. Category is context only; concepts are never interpreted. Never infer high/low purchase price from ETF holdings alone.

19. **`ETF 意圖 V3` is a preserved strict same-session prototype, not the final rich-menu surface.** Exact outstanding ETF units must be stored by all three active fetchers and used before taking any stock-flow derivative. Historical `fund_size / NAV` estimates retain their `estimated` label. V3 still requires strict same-day 2/3 agreement and remains visible as a dashboard audit tab, but its two images are no longer the main LINE reply.

20. **融資風險 is an estimate, not an official account-average series.** The exchanges publish margin-share balances, prices, and aggregate financing amounts, but not every brokerage customer's supplementary collateral. Its numerator must exclude the exchange `00...` ETF code family while its denominator retains the official aggregate financing balance, matching MacroMicro's published ETF-exclusion principle; do not silently put ETFs back into the numerator. Always label the result `全市場融資擔保估算率` / `公開資料估算`; never call it the official `台股平均融資維持率`, never claim it reproduces MacroMicro, and never describe 130% as this aggregate line's exact forced-liquidation trigger. Only `scripts/update_margin_maintenance.py` may call TWSE/TPEx. The dashboard and webhook are cache-only. Red means improving buffer and green means worsening buffer, per the site's Taiwan color convention. The `吳大師` card must keep all three quick replies (`ETF 共識`, `ETF 動作`, and `融資風險`).

21. **`ETF 共識 V4` is the final copy-the-manager contract.** Colour and score have different jobs. Yellow/watch is allowed only for one-manager structural/re-entry/full-exit/reversal/restart evidence or a former consensus that lost its second manager. To avoid tiny holding-list cleanup washing the lane, new/re-entry needs at least `0.6×` that ETF's usual single action and full exit needs `1.0×`; reversal/restart already requires the complete significance gate. Red/green require at least **2/3 ETFs**, each already significant, with same-direction signal lifetimes overlapping within three common sessions. A score—however large—must never upgrade 1/3 to red/green.

    **V4 significance and display scale are deliberately different.** For every ETF/date, use only the preceding 10 common sessions and collect all copyable, non-zero actions across that ETF's stocks. Prefer the same-direction sample when it has at least 8 observations; otherwise use the combined buy/sell sample, with a `0.02% / 0.60` fallback only for early history. `normal_action_flow` is that sample's median. The significance gate is `max(0.02% ETF size, 60% × normal_action_flow)`, but the displayed multiple and chart height are `abs(active_flow) / normal_action_flow`. Never display `active_flow / significance_gate` as “normal”: that sparse-floor bug made 環球晶's 2026-07-22 991 purchase look like `23.9×` when it was actually about `3.72億`, `2.51×` 991's same-direction usual single action of about `1.48億`. Estimated 億元 is explanation only and must never rank managers of different size.

    Fixed EWMA half-lives remain 3 (current pressure), 10 (underlying direction), and 20 (background); never add an interval selector that changes the conclusion. The only selector is date-keyed historical replay. Entry and maintenance additionally require each participating ETF/stock's **signed net active flow over the latest 10 common sessions** to remain in the state direction—this is the first derivative of the holding allocation, so a static holding is never “hold evidence.” A red/green state needs at least two such managers and score at least 40; one remaining manager downgrades to yellow, zero removes the card. Freshness is scored from the second-freshest confirming manager and falls every quiet trading session; yellow freshness falls from its one trigger. Cards show the exact maximum remaining trading sessions if no new significant same-direction action arrives, and evidence expires when it leaves the 10-session window. Red/green cards always show three aligned ETF rows; yellow shows only its triggering manager.

    **Core is a priority layer, never a replacement for the hard state.** A red/green card enters `core` only when score ≥60, freshness ≥8, relative strength ≥8, 3/10/20 alignment ≥3, and it is either repeatedly confirmed (joint-persistence component ≥10) or a strong formation no older than three state sessions. Everything else remains visible as valid `tracking`. This protects the user from choice overload without pretending that deleting cards improves the underlying evidence.

    The LINE command/rich-menu reply contains no text, fetch, on-demand screenshot, or paid push. It reads the generated manifest and sends exactly **three 1800px-wide, two-column cached images** in buy/sell/watch order. Each lane is split evenly by its existing score order; the two headers state non-overlapping rank ranges, while core/tracking remains a card-level badge. Every card must occur exactly once. Never return five same-colour continuation previews or one ultra-long low-resolution image.

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
   - In `scripts/update_and_notify.sh`: add the ticker to the default `ETFS=(...)` list (used by the manual "每日更新" path), the fetcher dispatch `case`, and the active-new `case` (active). Add the display name to `ACTIVE_NAMES` in `scripts/line_active_report_payload.py`.
   - Ensure failure details print the ETF fetch log clearly in the admin email.
   - Active ETFs broadcast 1 image each, but the single daily payload has room for only four because its first object is the combined report/action text. Do not add a fifth active image without redesigning the single-send product requirement (Invariant #7).

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
    cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main && source venv/bin/activate && pip install -r requirements.txt -q && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now stock-quote-monitor-<ticker-lower>.service && sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service stock-quote-monitor-<ticker-lower>.service
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
    cd /home/ubuntu/STOCK && git -c pull.rebase=false pull --ff-only origin main && sudo systemctl disable --now stock-quote-monitor-<ticker-lower>.service && sudo rm -f /etc/systemd/system/stock-quote-monitor-<ticker-lower>.service && sudo cp services/*.service services/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart stock-dashboard.service stock-webhook.service stock-master-holding-monitor.service
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
