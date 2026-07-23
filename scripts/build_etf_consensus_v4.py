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


DATA = ROOT / "data"
OUT = DATA / "etf_consensus_v4.json"
TAGS = DATA / "stock_tags.json"
HISTORIES = {
    "00403A": DATA / "etf_00403A_history.json",
    "00981A": DATA / "etf_00981A_history.json",
    "00991A": DATA / "etf_00991A_history.json",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def generate(out: Path = OUT) -> dict:
    histories = {
        etf: _load(path)
        for etf, path in HISTORIES.items()
        if path.exists()
    }
    if set(histories) != set(HISTORIES):
        missing = sorted(set(HISTORIES) - set(histories))
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
