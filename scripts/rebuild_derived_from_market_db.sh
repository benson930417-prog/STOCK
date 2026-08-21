#!/usr/bin/env bash
# Rebuild deterministic analytics from the canonical DB without any notification
# or live-quote side effects. This script never touches quote_cache or live images.
set -Eeuo pipefail

readonly root=/home/ubuntu/STOCK
readonly python="$root/venv/bin/python"
readonly db=/var/lib/stock/market/market.db
readonly write_lock=/var/lib/stock/market/write.lock

cd "$root"
export STOCK_GLOBAL_MARKET_DB="$db"
export PYTHONDONTWRITEBYTECODE=1

[[ -r "$db" ]] || { echo "canonical market.db is unavailable" >&2; exit 66; }

exec 9>"$write_lock"
if ! flock -w 1800 9; then
  echo "timed out waiting for the canonical market.db write lock" >&2
  exit 75
fi

run() {
  printf '[derived-rebuild] %s\n' "$*"
  "$@"
}

run "$python" scripts/verify_market_db.py --db "$db" --require-taiwan
run "$python" -m scripts.etf_benchmark.step4_regimes
run "$python" -m scripts.etf_benchmark.step5_score
run "$python" scripts/generate_market_pulse_summary.py
run "$python" scripts/build_tag_flow.py
run "$python" scripts/generate_etf_action_insight.py
run "$python" scripts/generate_etf_action_summary.py
run "$python" scripts/build_etf_intent_v3.py
run "$python" scripts/generate_etf_intent_v3_summary.py
run "$python" scripts/build_etf_consensus_v4.py
run "$python" scripts/generate_etf_consensus_v4_summary.py
run "$python" scripts/generate_etf_summary.py

printf '[derived-rebuild] CLEAN\n'
