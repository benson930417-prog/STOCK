"""Manually re-fire the LINE broadcast for active ETF reports.

Use this when the daily 18:30 job got interrupted AFTER fetching data
but BEFORE pushing to LINE — re-running update_and_notify.sh would see
"no new data" (logs already say checked) and skip the broadcast.

Images are served directly by the webhook via duckdns.org — NO git push
needed (the daily flow was simplified to drop GitHub as middleman).

Usage:
    python scripts/rebroadcast_line.py 00403A 00981A 00988A
    python scripts/rebroadcast_line.py --regen 00403A 00981A 00988A
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.line_active_report_payload import (  # noqa: E402
    ACTIVE_NAMES,
    build_active_report_messages,
)
from src.market_db import load_holding_history  # noqa: E402

SECRETS_FILE = "/home/ubuntu/.stock_secrets"
WEBHOOK_HOST = "https://linechatbot.duckdns.org"


def _get_line_token() -> str | None:
    """Match webhook.py's lookup: env var first, then read secrets file
    directly. Accepts either LINE_TOKEN or LINE_CHANNEL_ACCESS_TOKEN."""
    for env_key in ("LINE_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN"):
        val = os.environ.get(env_key)
        if val:
            return val
    try:
        with open(SECRETS_FILE, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k in ("LINE_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN") and v:
                    return v
    except Exception:
        pass
    return None


def _summary_path(ticker: str) -> Path:
    return DATA_DIR / "summaries" / f"etf_{ticker}_summary_latest.jpg"


def _broadcast(tickers: list[str], token: str) -> None:
    """Broadcast one action text plus at most four ETF images."""
    messages = build_active_report_messages(tickers, webhook_host=WEBHOOK_HOST)
    payload = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"LINE response: HTTP {resp.status} {body}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="+",
                    help="Active ETF tickers to broadcast (e.g. 00403A 00981A 00988A)")
    ap.add_argument("--regen", action="store_true",
                    help="Run scripts/generate_etf_summary.py first to refresh JPGs")
    args = ap.parse_args()

    token = _get_line_token()
    if not token:
        print("ERROR: LINE_TOKEN / LINE_CHANNEL_ACCESS_TOKEN not found.", file=sys.stderr)
        print(f"  Checked env vars + {SECRETS_FILE} (matching webhook.py logic).", file=sys.stderr)
        return 1

    unknown = [t for t in args.tickers if t not in ACTIVE_NAMES]
    if unknown:
        print(f"WARN: unknown active ETF tickers: {unknown} — will broadcast anyway",
              file=sys.stderr)
    for t in args.tickers:
        if not load_holding_history(t):
            print(f"ERROR: market.db has no complete holding history for {t}", file=sys.stderr)
            return 1

    if args.regen:
        print("[regen] running scripts/generate_etf_summary.py …")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_etf_summary.py")],
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            print("ERROR: generate_etf_summary.py failed", file=sys.stderr)
            return result.returncode
        print("[regen] done.")

    # Sanity check: the JPG files actually exist on disk (webhook serves
    # from local disk, so missing files = LINE shows broken image)
    missing = [t for t in args.tickers if not _summary_path(t).exists()]
    if missing:
        print(f"ERROR: summary JPGs missing for: {missing}", file=sys.stderr)
        print("       Run with --regen to create them.", file=sys.stderr)
        return 1

    print(f"[broadcast] sending one text + {len(args.tickers)} images for: {args.tickers}")
    print(f"[broadcast] images served from: {WEBHOOK_HOST}/api/webhook/summaries/")
    _broadcast(args.tickers, token)
    print("[broadcast] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
