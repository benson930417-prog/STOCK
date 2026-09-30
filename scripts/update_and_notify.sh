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

# Serialize publications, not the market database. Image generation briefly
# takes write.lock only to capture one coherent set of read-only inputs.
exec 7>/var/lib/stock/market/derived-publication.lock
flock -w 1800 7 || exit 75

# Deployment owns application code and dependencies. Scheduled data jobs never
# mutate their own code or virtual environment. Canonical market data is never
# stored in Git.

SECRETS_FILE="/home/ubuntu/.stock_secrets"
if [ -f "$SECRETS_FILE" ]; then
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
    # Secrets files historically contain plain shell assignments.  Child
    # Python processes cannot see those unless the orchestrator exports them.
    # Keep both supported names because webhook/rebroadcast deployments use
    # either one.
    [ -n "${LINE_TOKEN:-}" ] && export LINE_TOKEN
    [ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ] && export LINE_CHANNEL_ACCESS_TOKEN
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
            generate_etf_summary)
                metrics=$(grep -E "^Saved " "$logfile" | sed 's/^/          /')
                ;;
            "daily LINE publication gate")
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
    # Keep the evidence in journald after the temporary email directory is removed.
    tail -n 30 "$logfile"
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
TRADING_DATE="$(python - <<'PY'
import json
from pathlib import Path

manifest = json.loads(
    Path("/var/lib/stock/market/holdings-fetch.json").read_text(encoding="utf-8")
)
print(manifest["trading_date"])
PY
)" || exit 2
printf "  [OK]   today's CLEAN issuer manifest and matching DB import started at %s\n" \
    "$RUN_STARTED_UTC" >> "$SUMMARY_FILE"

# ──────────────────────────────────────────────────────────────────────────
# 2. Derived analytics refresh from the sole market database.
# ──────────────────────────────────────────────────────────────────────────
# Retired analytics are no longer publication prerequisites.

# The four active-operation images are publication inputs, not an optimization
# conditional on this process seeing NEW DATA.  Repaired/replayed runs must
# regenerate them before the independent daily publication gate evaluates
# completeness.
run_step "generate_etf_summary" python scripts/generate_etf_summary.py

# ──────────────────────────────────────────────────────────────────────────
# 3. Required daily LINE publication gate
# ──────────────────────────────────────────────────────────────────────────
# Publication requires the canonical inputs and generated images to be
# successful. Images are served by the webhook, so Git archival is independent.  The Python gate then independently re-verifies the sealed fetch,
# matching DB import, all four fresh images and payload
# shape.  A durable daily receipt makes retries idempotent: incomplete runs stay
# PENDING; the first repaired CLEAN run sends; later reruns return already SENT.
if [ "$fail_count" -eq 0 ]; then
    run_step "daily LINE publication gate" \
        python scripts/daily_line_publish.py \
        --trading-date "$TRADING_DATE" \
        --confirm-upstream-ready
else
    python scripts/daily_line_publish.py \
        --trading-date "$TRADING_DATE" \
        --defer "upstream derived run has ${fail_count} failed step(s)" \
        >> "$SUMMARY_FILE" 2>&1 || true
    printf "  [PENDING] daily LINE publication: upstream failures=%d\n" \
        "$fail_count" >> "$SUMMARY_FILE"
fi

# ──────────────────────────────────────────────────────────────────────────
# 4. Git archival follows publication; archival failure never triggers a re-send.
# ──────────────────────────────────────────────────────────────────────────
# The archive helper uses a separate Git index and retries concurrent code pushes.
# It does not merge into, reset, or checkout the live application working tree.
run_step "git push issuer archive" python scripts/archive_issuer_data.py

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
