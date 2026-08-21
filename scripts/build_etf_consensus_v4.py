#!/usr/bin/env python3
"""Build the cached final active-ETF consensus/history dataset."""
from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etf_consensus_v4 import build_consensus_payload  # noqa: E402
from src.market_db import load_holding_history  # noqa: E402


DATA = ROOT / "data"
OUT = DATA / "etf_consensus_v4.json"
TAGS = DATA / "stock_tags.json"
HISTORY_TICKERS = ("00403A", "00981A", "00991A")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def generate(out: Path = OUT) -> dict:
    histories = {etf: load_holding_history(etf) for etf in HISTORY_TICKERS}
    missing = sorted(etf for etf, history in histories.items() if not history)
    if missing:
        raise RuntimeError(f"Missing active ETF histories: {', '.join(missing)}")
    tags = (_load(TAGS).get("tags") or {}) if TAGS.exists() else {}
    payload = build_consensus_payload(histories, tags)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    signals = payload["signals"]
    print(
        "[etf-consensus-v4] "
        f"as_of={payload['as_of']} "
        f"watch={len(signals['watching'])} "
        f"buy={len(signals['buying'])} "
        f"sell={len(signals['selling'])}"
    )
    print(f"Saved {out.relative_to(ROOT)}")
    return payload


if __name__ == "__main__":
    generate()
