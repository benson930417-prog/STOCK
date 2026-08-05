"""Reconstruct when each ETF holding snapshot first became visible to us.

The backtest claims it never peeks: it trades at the open *after* a disclosure.
That claim is only true if the holdings for day D actually reached us after
day D's close. Nothing recorded that, but every snapshot was committed to git by
the scheduled job, so the first commit containing a date key is a hard upper
bound on when we learned it. This rebuilds that evidence into a data file the
backtest can check against instead of assuming.

Going forward ``fetch_etf_00981A.py`` stamps ``first_seen_utc`` directly; this
script only backfills the dates that predate that change.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from datetime import datetime, timedelta, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
TAIPEI = timezone(timedelta(hours=8))
# Taiwan equities close at 13:30; the after-hours session ends at 14:30.
MARKET_CLOSE_HOUR = 13.5


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def reconstruct(tracked_path: str) -> dict[str, str]:
    """Map each snapshot date to the ISO timestamp of the commit that added it."""
    log = [
        line for line in _git("log", "--format=%H|%cI", "--follow", "--", tracked_path).splitlines() if "|" in line
    ]
    first_seen: dict[str, str] = {}
    for line in reversed(log):
        commit, stamp = line.split("|", 1)
        blob = _git("show", f"{commit}:{tracked_path}")
        try:
            snapshot = json.loads(blob)
        except ValueError:
            continue
        for day in snapshot:
            first_seen.setdefault(str(day), stamp)
    return first_seen


def audit(first_seen: dict[str, str]) -> list[dict]:
    """Flag any snapshot we saw before that day's market close."""
    rows = []
    for day, stamp in sorted(first_seen.items()):
        seen = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(TAIPEI)
        close = datetime.fromisoformat(f"{day}T13:30:00+08:00")
        rows.append(
            {
                "date": day,
                "first_seen_utc": stamp,
                "first_seen_taipei": seen.strftime("%Y-%m-%d %H:%M"),
                "after_market_close": seen >= close,
                "hours_after_close": round((seen - close).total_seconds() / 3600.0, 2),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracked", default="data/etf_00981A_history.json")
    parser.add_argument(
        "--output", type=pathlib.Path, default=REPO / "data" / "etf_00981A_disclosure_times.json"
    )
    args = parser.parse_args()

    first_seen = reconstruct(args.tracked)
    rows = audit(first_seen)
    suspect = [row for row in rows if not row["after_market_close"]]
    payload = {
        "schema_version": 1,
        "method": "first git commit containing each snapshot date (upper bound on disclosure time)",
        "tracked_path": args.tracked,
        "rebuilt_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market_close_taipei": "13:30",
        "date_count": len(rows),
        "suspect_count": len(suspect),
        "suspect_dates": [row["date"] for row in suspect],
        "first_seen": {row["date"]: row["first_seen_utc"] for row in rows},
        "audit": rows,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{len(rows)} snapshot dates, {len(suspect)} seen before that day's close")
    for row in suspect:
        print(f"  SUSPECT {row['date']} first seen {row['first_seen_taipei']} Taipei")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
