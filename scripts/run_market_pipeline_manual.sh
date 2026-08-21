#!/bin/bash
# Manual/admin entry point that reuses the same sealed production stages.

set -euo pipefail

ROOT=/home/ubuntu/STOCK
MARKET_ROOT=/var/lib/stock/market
PIPELINE=/opt/stock/market-pipeline
PYTHON="$ROOT/venv/bin/python"

cd "$ROOT"

exec 8>"$MARKET_ROOT/manual-pipeline.lock"
if ! flock -n 8; then
    echo "another manual market pipeline is already running" >&2
    exit 75
fi

# Serialize the manifest-producing fetch with the scheduled fetch and keep the
# lock until its manifest has been consumed. Otherwise a timer can replace the
# manifest between this process's fetch and import.
exec 9>"$MARKET_ROOT/holdings-fetch.lock"
if ! flock -w 600 9; then
    echo "issuer holdings fetch/import is already running" >&2
    exit 75
fi

"$PYTHON" scripts/run_issuer_holdings_fetch.py \
    --root "$ROOT" \
    --manifest "$MARKET_ROOT/holdings-fetch.json" \
    --log-dir "$MARKET_ROOT/logs/issuer-holdings"

flock -w 600 "$MARKET_ROOT/write.lock" \
    env PYTHONPATH="$PIPELINE" "$PYTHON" -m market_data import-holdings \
        --db "$MARKET_ROOT/market.db" \
        --data-dir "$ROOT/data" \
        --run-manifest "$MARKET_ROOT/holdings-fetch.json"

exec flock -w 1800 "$MARKET_ROOT/write.lock" \
    scripts/update_and_notify.sh --post-fetch
