#!/usr/bin/env bash
# Rebuild deterministic analytics from the canonical DB without any notification
# or live-quote side effects. This script never touches quote_cache or live images.
set -Eeuo pipefail

readonly root=/home/ubuntu/STOCK
readonly python="$root/venv/bin/python"
readonly db=/var/lib/stock/market/market.db

cd "$root"
export STOCK_GLOBAL_MARKET_DB="$db"
export PYTHONDONTWRITEBYTECODE=1

[[ -r "$db" ]] || { echo "canonical market.db is unavailable" >&2; exit 66; }


run() {
  printf '[derived-rebuild] %s\n' "$*"
  "$@"
}

run "$python" scripts/verify_market_db.py --db "$db" --require-taiwan
run "$python" scripts/generate_etf_summary.py

printf '[derived-rebuild] CLEAN\n'
