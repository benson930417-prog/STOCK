#!/bin/bash
# Post-import orchestrator: derived analytics + Git data push + LINE/admin notify.
#
# The sealed issuer fetch and the market.db import are separate, authoritative
# stages. This script intentionally cannot fetch holdings by itself.
# Failure of any single step does NOT abort the rest — every step's status is
# captured into a summary and emailed to ADMIN_EMAIL at the end.

set -u

if [ "${1:-}" != "--post-fetch" ]; then
    echo "refusing: run sealed issuer fetch and market.db import before derived work" >&2
    exit 64
fi
shift

cd /home/ubuntu/STOCK || exit 1
source venv/bin/activate

# Deployment owns application code and dependencies. Scheduled data jobs never
# mutate their own code or virtual environment. Canonical market data is never
# stored in Git.

SECRETS_FILE="/home/ubuntu/.stock_secrets"
if [ -f "$SECRETS_FILE" ]; then
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
else
    echo "Warning: Secrets file $SECRETS_FILE not found. LINE/email will fail."
fi

# (GITHUB_REPO no longer needed — daily LINE broadcast serves images via
#  the webhook's duckdns.org URL directly, not GitHub raw.)

# ──────────────────────────────────────────────────────────────────────────
# Logging scaffolding — every step writes to $LOG_DIR/<name>.log and appends
# a one-line status to $SUMMARY_FILE. At end we email both.
# ──────────────────────────────────────────────────────────────────────────
LOG_DIR=$(mktemp -d -t stock_run.XXXX)
SUMMARY_FILE="$LOG_DIR/_summary.txt"
ERRORS_FILE="$LOG_DIR/_errors.txt"
trap 'rm -rf "$LOG_DIR"' EXIT

run_start_utc=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
run_start_epoch=$(date +%s)
overall_status="SUCCESS"
fail_count=0

{
    echo "STOCK daily run summary"
    echo "Run started: $run_start_utc"
    echo "Hostname   : $(hostname)"
    echo
} > "$SUMMARY_FILE"

# Record a successful step: append [OK] + step metrics to the summary.
# Optional third arg is a note appended to the label (e.g. retry recovery).
record_step_ok() {
    local label="$1"
    local logfile="$2"
    local note="${3:-}"
    local summary_label="$label"
    [ -n "$note" ] && summary_label="$label ($note)"
    # Pull useful metrics out of the daily production steps.
    local metrics=""
    case "$label" in
            step4*)
                metrics=$(awk '
                    /^\[step4\]/ {print; next}
                    /^[[:space:]]+Summary by regime:/ {f=1; print; next}
                    f && /^[[:space:]]+(bull|correction|mini_bear|bear)[[:space:]]*:/ {print; next}
                ' "$logfile" | sed 's/^/          /')
                ;;
            step5*)
                metrics=$(grep -E "^\[step5\] (recorded|backfilled)" "$logfile" | sed 's/^/          /')
                ;;
            generate_etf_summary|generate_market_pulse_summary|"generate margin risk summary"|"generate ETF action summary")
                metrics=$(grep -E "^Saved " "$logfile" | sed 's/^/          /')
                ;;
            "market pulse volume cache")
                metrics=$(grep -E "^\[market-volume\]" "$logfile" | sed 's/^/          /')
                ;;
            "margin risk cache")
                metrics=$(grep -E "^\[margin-risk\]" "$logfile" | tail -n 3 | sed 's/^/          /')
                ;;
            "build stock tags (incremental)")
                metrics=$(grep -E "^\[cmoney-tags\]" "$logfile" | sed 's/^/          /')
                ;;
            "generate ETF action insight")
                metrics=$(grep -E "^(\[etf-action\]|Saved data/etf_action_insight\.json)" "$logfile" | sed 's/^/          /')
                ;;
            "LINE broadcast active reports")
                metrics=$(tail -n 5 "$logfile" | sed 's/^/          /')
                ;;
            git\ push*)
                metrics=$(grep -E "^(To |Everything up-to-date|[[:space:]]*[0-9a-f]+\\.\\.[0-9a-f]+[[:space:]]+main -> main)" "$logfile" | sed 's/^/          /')
                ;;
        esac
    if [ -n "$metrics" ]; then
        printf "  [OK]   %s\n%s\n" "$summary_label" "$metrics" >> "$SUMMARY_FILE"
    else
        printf "  [OK]   %s\n" "$summary_label" >> "$SUMMARY_FILE"
    fi
    # Echo tail for journal log
    tail -n 3 "$logfile"
}

# Record a failed step: append [FAIL] to summary, copy the log tail into the
# errors file, and flip the overall run status.
record_step_fail() {
    local label="$1"
    local logfile="$2"
    local rc="$3"
    printf "  [FAIL] %s  (exit=%d)\n" "$label" "$rc" >> "$SUMMARY_FILE"
    {
        echo
        echo "═══════════════════════════════════════════════════"
        echo "FAILURE: $label  (exit=$rc)"
        echo "Last 30 lines of $logfile:"
        echo "───────────────────────────────────────────────────"
        tail -n 30 "$logfile"
        echo
    } >> "$ERRORS_FILE"
    overall_status="PARTIAL_FAIL"
    fail_count=$((fail_count + 1))
    echo "  [FAIL] $label (exit=$rc) — continuing"
}

# Run a labeled command; append OK/FAIL to summary, capture full stderr+stdout
# to a per-step log file, and on failure copy the tail into errors file.
run_step() {
    local label="$1"; shift
    local logfile="$LOG_DIR/${label// /_}.log"
    echo "=== $label ==="
    if "$@" > "$logfile" 2>&1; then
        record_step_ok "$label" "$logfile"
        return 0
    else
        local rc=$?
        record_step_fail "$label" "$logfile" "$rc"
        return $rc
    fi
}

# ──────────────────────────────────────────────────────────────────────────
# 1. Bind derived work to today's CLEAN, sealed issuer fetch.
# ──────────────────────────────────────────────────────────────────────────
if [ "$#" -gt 0 ]; then
    ETFS=("$@")
else
    ETFS=("00403A" "00981A" "00988A" "00991A" "0050" "0056" "00830" "00878" "00891" "00918" "009805" "009820")
fi

echo "ETF list: ${ETFS[*]}"
{ echo "Sealed issuer fetch"; echo "-------------------"; } >> "$SUMMARY_FILE"

RUN_STARTED_UTC="$(python - <<'PY'
import json
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

manifest_path = Path("/var/lib/stock/market/holdings-fetch.json")
manifest_bytes = manifest_path.read_bytes()
manifest = json.loads(manifest_bytes.decode("utf-8"))
today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
if manifest.get("status") != "CLEAN" or manifest.get("trading_date") != today:
    raise SystemExit("today's CLEAN issuer fetch manifest is unavailable")
with sqlite3.connect(
    "file:/var/lib/stock/market/market.db?mode=ro", uri=True, timeout=30
) as connection:
    row = connection.execute(
        """SELECT status,failure_count,report_json FROM ingest_runs
             WHERE job='ETF_ISSUER_HOLDINGS' AND trading_date=?
             ORDER BY finished_at_utc DESC LIMIT 1""",
        (today,),
    ).fetchone()
if not row or row[0] != "CLEAN" or int(row[1]) != 0:
    raise SystemExit("today's CLEAN issuer holdings import is unavailable")
report = json.loads(row[2] or "{}")
if report.get("source_run_id") != manifest.get("run_id"):
    raise SystemExit("latest holdings import did not consume this fetch manifest")
canonical_manifest = json.dumps(
    manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
).encode("utf-8")
manifest_sha256 = hashlib.sha256(canonical_manifest).hexdigest()
if report.get("manifest_sha256") != manifest_sha256:
    raise SystemExit("holdings import manifest checksum does not match current manifest")
print(manifest["started_at_utc"])
PY
)" || exit 2
printf "  [OK]   today's CLEAN issuer manifest and matching DB import started at %s\n" \
    "$RUN_STARTED_UTC" >> "$SUMMARY_FILE"

# ──────────────────────────────────────────────────────────────────────────
# 2. Derived analytics refresh from the sole market database.
# ──────────────────────────────────────────────────────────────────────────
{ echo; echo "etf_benchmark"; echo "-------------"; } >> "$SUMMARY_FILE"
run_step "step4 regime tagger"           python -m scripts.etf_benchmark.step4_regimes
run_step "step5 score (append today)"    python -m scripts.etf_benchmark.step5_score
run_step "market pulse volume cache"     python scripts/update_market_pulse_volume.py --months 4
run_step "generate_market_pulse_summary" python scripts/generate_market_pulse_summary.py
run_step "margin risk cache"              python scripts/update_margin_maintenance.py --days 10
run_step "generate margin risk summary"   python scripts/generate_margin_maintenance_summary.py

# Theme-flow (題材流向) tab: refresh any missing stock tags (incremental, only new
# holdings hit cmoney), then rebuild the ETF theme-flow map. build_tag_flow is
# pure-local; if the tag scrape hiccups it still runs off the cached tags.
run_step "build stock tags (incremental)" python scripts/build_stock_tags.py --probe 2308
run_step "build tag flow"                 python scripts/build_tag_flow.py
if run_step "generate ETF action insight" python scripts/generate_etf_action_insight.py; then
    run_step "generate ETF action summary" python scripts/generate_etf_action_summary.py
    {
        echo
        echo "主動 ETF 買／抱／賣"
        echo "──────────"
        python scripts/generate_etf_action_insight.py --print
    } >> "$SUMMARY_FILE"
else
    {
        echo
        echo "主動 ETF 買／抱／賣"
        echo "──────────"
        echo "本次洞察產生失敗，請查看 failure details。"
    } >> "$SUMMARY_FILE"
fi

# V3 is intentionally independent from the existing category and lifecycle
# pages.  It removes ETF creation/redemption scaling, emits only new buy/sell
# intent transitions, and pre-renders two free on-demand LINE images.
if run_step "build ETF intent V3" python scripts/build_etf_intent_v3.py; then
    run_step "generate ETF intent V3 summary" python scripts/generate_etf_intent_v3_summary.py
else
    {
        echo
        echo "主動 ETF 意圖轉折 V3"
        echo "──────────"
        echo "本次 V3 產生失敗，請查看 failure details。"
    } >> "$SUMMARY_FILE"
fi

# V4 is the final state/history surface.  It keeps V3 available for audit,
# adds high-information one-manager observation, and requires two independent
# managers for red/green consensus.  These are cached on-demand LINE assets;
# this block never sends a push or broadcast.
if run_step "build ETF consensus V4" python scripts/build_etf_consensus_v4.py; then
    run_step "generate ETF consensus V4 summary" python scripts/generate_etf_consensus_v4_summary.py
else
    {
        echo
        echo "主動 ETF 共識追蹤 V4"
        echo "──────────"
        echo "本次 V4 產生失敗，請查看 failure details。"
    } >> "$SUMMARY_FILE"
fi

# ──────────────────────────────────────────────────────────────────────────
# 3. Detect which ETFs got NEW DATA this run
# ──────────────────────────────────────────────────────────────────────────
CHANGED_ETFS=()
ACTIVE_NEW_ETFS=()
while IFS= read -r ETF; do
    [ -n "$ETF" ] && CHANGED_ETFS+=("$ETF")
done < <(RUN_STARTED_UTC="$RUN_STARTED_UTC" ETFS="${ETFS[*]}" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

run_started = datetime.fromisoformat(os.environ["RUN_STARTED_UTC"].replace("Z", "+00:00"))
for etf in os.environ["ETFS"].split():
    prefix = "passive" if etf in {"0050", "0056", "00830", "00878", "00891", "00918", "009805", "009820"} else "etf"
    path = Path(f"data/{prefix}_{etf}_log.json")
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(log.get("last_checked_utc", "")).replace("Z", "+00:00"))
    except Exception:
        continue
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    if checked >= run_started and log.get("status") == "NEW DATA FOUND":
        print(etf)
PY
)

for ETF in "${CHANGED_ETFS[@]}"; do
    case "$ETF" in
        00403A|00981A|00988A|00991A)
            ACTIVE_NEW_ETFS+=("$ETF")
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────────
# 4. Git push + (if active ETF got new data) LINE broadcast
# ──────────────────────────────────────────────────────────────────────────
git config --global user.name "OCI Server Bot"
git config --global user.email "oci-bot@localhost"

{ echo; echo "Git & LINE"; echo "──────────"; } >> "$SUMMARY_FILE"

if [ "${#CHANGED_ETFS[@]}" -gt 0 ]; then
    echo "New data detected for: ${CHANGED_ETFS[*]}"
    printf "  [INFO] NEW DATA for: %s\n" "${CHANGED_ETFS[*]}" >> "$SUMMARY_FILE"
    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ]; then
        run_step "generate_etf_summary" python scripts/generate_etf_summary.py
    fi

    git add data/*.json data/summaries/*.jpg 2>/dev/null
    commit_output=$(git commit -m "Auto-update ETF data and summary images from OCI" 2>&1)
    if [ "$?" -eq 0 ]; then
        printf "  [OK]   git commit\n" >> "$SUMMARY_FILE"
        echo "$commit_output" | sed -n '1,4p' | sed 's/^/          /' >> "$SUMMARY_FILE"
    else
        printf "  [INFO] git: no changes to commit\n" >> "$SUMMARY_FILE"
        echo "$commit_output" | sed -n '1,4p' | sed 's/^/          /' >> "$SUMMARY_FILE"
    fi
    run_step "git push origin main" git push origin main

    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ] && [ -n "${LINE_TOKEN:-}" ]; then
        export LINE_TOKEN
        # Daily broadcast — images served directly by the webhook via duckdns.org,
        # NOT GitHub raw URL. This avoids the git commit+push+CDN-wait dance and
        # the gitignore-mismatch class of bug that broke the daily images.
        run_step "LINE broadcast active reports" python - <<'PY'
import json
import os
from urllib import request

from scripts.line_active_report_payload import ACTIVE_TICKERS, build_active_report_messages

token = os.environ["LINE_TOKEN"]
messages = build_active_report_messages(ACTIVE_TICKERS)
if len(messages) > 5:
    raise RuntimeError(f"refusing LINE batch with {len(messages)} objects")

def _broadcast(objs):
    payload = json.dumps({"messages": objs}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        print(resp.status, resp.read().decode("utf-8", errors="replace"))

_broadcast(messages)
print(f"Daily LINE object count: {len(messages)} (1 text + {len(messages) - 1} images)")
PY
    elif [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ]; then
        printf "  [SKIP] LINE broadcast: LINE_TOKEN not set\n" >> "$SUMMARY_FILE"
        echo "LINE_TOKEN is not set. Skipping LINE notification."
    fi
else
    echo "No new ETF data found. Pushing log timestamps only."
    printf "  [INFO] no new ETF data\n" >> "$SUMMARY_FILE"
    git add data/*.json data/summaries/*.jpg 2>/dev/null
    commit_output=$(git commit -m "Auto-update ETF log timestamps and market pulse summary from OCI" 2>&1)
    if [ "$?" -eq 0 ]; then
        printf "  [OK]   git commit\n" >> "$SUMMARY_FILE"
        echo "$commit_output" | sed -n '1,4p' | sed 's/^/          /' >> "$SUMMARY_FILE"
    else
        printf "  [INFO] git: no changes to commit\n" >> "$SUMMARY_FILE"
        echo "$commit_output" | sed -n '1,4p' | sed 's/^/          /' >> "$SUMMARY_FILE"
    fi
    run_step "git push origin main (log-only)" git push origin main
fi

# ──────────────────────────────────────────────────────────────────────────
# 5. Send admin email summary (always, even on full success)
# ──────────────────────────────────────────────────────────────────────────
run_end_epoch=$(date +%s)
duration=$((run_end_epoch - run_start_epoch))

{
    echo
    echo "──────────"
    echo "Duration   : ${duration}s"
    echo "Overall    : $overall_status"
    echo "Failed steps: $fail_count"
} >> "$SUMMARY_FILE"

# Compose final body: summary always, errors appended if any
FINAL_BODY="$LOG_DIR/_email_body.txt"
cat "$SUMMARY_FILE" > "$FINAL_BODY"
if [ -s "$ERRORS_FILE" ]; then
    {
        echo
        echo "════════════ FAILURE DETAILS ════════════"
        cat "$ERRORS_FILE"
    } >> "$FINAL_BODY"
fi

SUBJECT="[STOCK] daily run — $overall_status ($(date +"%Y-%m-%d %H:%M") TPE)"
if [ -n "${GMAIL_APP_PASSWORD:-}" ]; then
    GMAIL_APP_PASSWORD="$GMAIL_APP_PASSWORD" \
    ADMIN_EMAIL="${ADMIN_EMAIL:-benson930417@gmail.com}" \
    GMAIL_FROM="${GMAIL_FROM:-${ADMIN_EMAIL:-benson930417@gmail.com}}" \
    python scripts/admin_email.py \
        --subject "$SUBJECT" \
        --body-file "$FINAL_BODY" \
        || echo "WARN: admin email failed (non-fatal)"
else
    echo "GMAIL_APP_PASSWORD not set in $SECRETS_FILE — skipping admin email"
fi

echo "Run complete: $overall_status ($fail_count failed steps, ${duration}s)"
if [ "$fail_count" -gt 0 ]; then
    # The email/report has already been attempted and all independent steps
    # have had a chance to run.  Return failure so systemd and the status page
    # cannot mistake PARTIAL_FAIL for a successful daily pipeline.
    exit 1
fi
