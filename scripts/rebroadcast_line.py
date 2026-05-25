"""Manually re-fire the LINE broadcast for active ETF reports.

Use this when the daily 17:30 job got interrupted AFTER fetching data
but BEFORE pushing to LINE — re-running update_and_notify.sh would see
"no new data" (logs already say checked) and skip the broadcast.

Usage:
    # broadcast both active ETFs
    source /home/ubuntu/.stock_secrets
    python scripts/rebroadcast_line.py 00981A 00997A

    # broadcast just one
    python scripts/rebroadcast_line.py 00981A

    # regenerate the summary JPG first, then broadcast
    python scripts/rebroadcast_line.py --regen 00981A 00997A

Requires:
    LINE_TOKEN     — from /home/ubuntu/.stock_secrets
    GITHUB_REPO    — defaults to benson930417-prog/STOCK
    Internet access to api.line.me

Reads `data/etf_{ticker}_history.json` for the latest data date (for the
text caption) and assumes `data/summaries/etf_{ticker}_summary_latest.jpg`
exists on GitHub (the script does NOT re-push to GitHub — if you need a
fresh image, run with --regen and then commit/push manually before this).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

ACTIVE_NAMES = {
    "00981A": "主動統一台股增長",
    "00997A": "主動群益美國增長",
}

SECRETS_FILE = "/home/ubuntu/.stock_secrets"


def _get_line_token() -> str | None:
    """Match webhook.py's lookup: env var first, then read secrets file
    directly. Accepts either LINE_TOKEN or LINE_CHANNEL_ACCESS_TOKEN since
    the secrets file uses one and the webhook treats them as aliases."""
    for env_key in ("LINE_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN"):
        val = os.environ.get(env_key)
        if val:
            return val
    # Fall back to reading the secrets file directly — handles the case
    # where the file uses `LINE_TOKEN=xxx` without `export` (source puts
    # it in the shell but not in the env Python sees).
    try:
        with open(SECRETS_FILE, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Tolerate "export KEY=VAL" or just "KEY=VAL"
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


def _latest_history_date(ticker: str) -> str:
    path = DATA_DIR / f"etf_{ticker}_history.json"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    with path.open(encoding="utf-8") as fh:
        return max(json.load(fh).keys())


def _broadcast(tickers: list[str], repo: str, token: str) -> None:
    messages: list[dict] = []
    cache_buster = int(time.time())
    for ticker in tickers:
        date_str = _latest_history_date(ticker)
        img_url = (
            f"https://raw.githubusercontent.com/{repo}/main/"
            f"data/summaries/etf_{ticker}_summary_latest.jpg?t={cache_buster}"
        )
        name = ACTIVE_NAMES.get(ticker, ticker)
        messages.append({
            "type": "text",
            "text": f"{date_str} {name} ({ticker}) 操作日報",
        })
        messages.append({
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl":    img_url,
        })

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
                    help="Active ETF tickers to broadcast (e.g. 00981A 00997A)")
    ap.add_argument("--regen", action="store_true",
                    help="Run scripts/generate_etf_summary.py first to refresh JPGs")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO", "benson930417-prog/STOCK"),
                    help="GitHub repo for image URL (default: benson930417-prog/STOCK)")
    args = ap.parse_args()

    token = _get_line_token()
    if not token:
        print("ERROR: LINE_TOKEN / LINE_CHANNEL_ACCESS_TOKEN not found.", file=sys.stderr)
        print(f"  Checked env vars + {SECRETS_FILE} (matching webhook.py logic).", file=sys.stderr)
        print(f"  Verify your secrets file has a line like:", file=sys.stderr)
        print(f"    LINE_TOKEN=\"abc123...\"     # or LINE_CHANNEL_ACCESS_TOKEN=\"...\"", file=sys.stderr)
        return 1

    # Validate tickers exist and have history
    unknown = [t for t in args.tickers if t not in ACTIVE_NAMES]
    if unknown:
        print(f"WARN: unknown active ETF tickers: {unknown} — will broadcast anyway",
              file=sys.stderr)
    for t in args.tickers:
        if not (DATA_DIR / f"etf_{t}_history.json").exists():
            print(f"ERROR: missing data/etf_{t}_history.json", file=sys.stderr)
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
        print("[regen] done. NOTE: if you regenerated, you should `git add + commit + push` "
              "the new JPGs BEFORE broadcasting so the GitHub URL serves the latest image.")
        print("       Press Enter to continue with broadcast, or Ctrl+C to abort.")
        try:
            input()
        except KeyboardInterrupt:
            return 130

    print(f"[broadcast] sending LINE messages for: {args.tickers}")
    _broadcast(args.tickers, args.repo, token)
    print("[broadcast] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
