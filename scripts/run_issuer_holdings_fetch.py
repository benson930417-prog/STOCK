#!/usr/bin/env python3
"""Fetch issuer ETF holdings only and seal one checksummed run manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ACTIVE = ("00403A", "00981A", "00988A", "00991A")
PASSIVE = ("0050", "0056", "00830", "00878", "00891", "00918", "009805", "009820")
DEFAULT_TICKERS = ACTIVE + PASSIVE
MARKET_DERIVED_META_KEYS = frozenset(
    {
        "closing_price",
        "closing_price_pct",
        "market_price",
        "market_price_pct",
        "premium_discount",
        "premium_discount_pct",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths(root: Path, ticker: str) -> tuple[Path, Path]:
    passive = ticker in PASSIVE
    fetcher = root / "scripts" / f"fetch_{'passive_' if passive else 'etf_'}{ticker}.py"
    history = root / "data" / f"{'passive_' if passive else 'etf_'}{ticker}_history.json"
    return fetcher, history


def _validate_history(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("history is empty or not an object")
    days = sorted(date.fromisoformat(str(key)).isoformat() for key in payload)
    snapshot = payload[days[-1]]
    if not isinstance(snapshot, dict):
        raise ValueError("latest snapshot is not an object")
    if snapshot.get("is_mocked") is True or (snapshot.get("meta") or {}).get("is_mocked") is True:
        raise ValueError("latest snapshot is marked mocked")
    rows = snapshot.get("holdings")
    if not isinstance(rows, list) or len(rows) < 5:
        raise ValueError("latest snapshot has fewer than five holdings")
    return {"latest_date": days[-1], "row_count": len(rows)}


def _sanitize_history(path: Path) -> None:
    """Remove legacy market-price metadata before sealing issuer holdings.

    Historical market prices live only in ARM market.db.daily_bars.  NAV and
    other values supplied by the issuer remain part of the issuer snapshot.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("history is empty or not an object")

    def scrub(value):
        if isinstance(value, dict):
            return {
                str(key): scrub(child)
                for key, child in value.items()
                if str(key) not in MARKET_DERIVED_META_KEYS
            }
        if isinstance(value, list):
            return [scrub(child) for child in value]
        return value

    sanitized = scrub(payload)
    if sanitized != payload:
        _atomic_json(path, sanitized)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/STOCK"))
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("/var/lib/stock/market/holdings-fetch.json"),
    )
    parser.add_argument(
        "--log-dir", type=Path,
        default=Path("/var/lib/stock/market/logs/issuer-holdings"),
    )
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--retry-delay", type=int, default=90)
    parser.add_argument("tickers", nargs="*", default=list(DEFAULT_TICKERS))
    args = parser.parse_args()
    root = args.root.resolve()
    tickers = tuple(dict.fromkeys(str(value).upper() for value in args.tickers))
    unknown = sorted(set(tickers) - set(DEFAULT_TICKERS))
    if unknown or len(tickers) < 1:
        raise SystemExit(f"unsupported ticker set: {unknown or tickers}")
    if not 1 <= args.attempts <= 4 or not 0 <= args.retry_delay <= 600:
        raise SystemExit("unsafe retry settings")

    started = _utc_now()
    trading_date = datetime.now(TAIPEI).date().isoformat()
    run_id = f"issuer-holdings-{trading_date}-{started.replace(':', '').replace('-', '')}"
    run_log_dir = args.log_dir / run_id
    run_log_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict] = []

    for ticker in tickers:
        fetcher, history = _paths(root, ticker)
        result = {
            "ticker": ticker,
            "fetcher": str(fetcher.relative_to(root)),
            "history": str(history.relative_to(root)),
            "attempts": 0,
            "status": "FAILED",
        }
        log_path = run_log_dir / f"{ticker}.log"
        if not fetcher.is_file():
            result["error"] = "fetcher is missing"
            results.append(result)
            continue
        for attempt in range(1, args.attempts + 1):
            result["attempts"] = attempt
            with log_path.open("a", encoding="utf-8", newline="\n") as log:
                log.write(f"=== attempt {attempt}/{args.attempts} at {_utc_now()} ===\n")
                # The child inherits this descriptor. Flush the attempt header
                # first so a traceback cannot appear above the attempt it
                # belongs to in the incident log.
                log.flush()
                try:
                    completed = subprocess.run(
                        [sys.executable, str(fetcher)], cwd=root, stdout=log,
                        stderr=subprocess.STDOUT, check=False, timeout=240,
                    )
                    returncode = completed.returncode
                except subprocess.TimeoutExpired:
                    returncode = 124
                    log.write("fetcher timed out after 240 seconds\n")
            if returncode == 0:
                try:
                    _sanitize_history(history)
                    validation = _validate_history(history)
                    result.update(
                        status="CLEAN",
                        sha256=_sha256(history),
                        size=history.stat().st_size,
                        **validation,
                    )
                    break
                except Exception as exc:
                    result["error"] = f"{type(exc).__name__}: {exc}"
            else:
                result["error"] = f"fetcher exit={returncode}"
                result["log"] = str(log_path)
                try:
                    result["error_tail"] = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )[-2000:]
                except OSError:
                    pass
            if attempt < args.attempts:
                time.sleep(args.retry_delay)
        results.append(result)

    failures = [item["ticker"] for item in results if item["status"] != "CLEAN"]
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "job": "ETF_ISSUER_FETCH",
        "host": os.uname().nodename,
        "trading_date": trading_date,
        "started_at_utc": started,
        "finished_at_utc": _utc_now(),
        "status": "CLEAN" if not failures else "PARTIAL",
        "ticker_count": len(tickers),
        "failure_count": len(failures),
        "failures": failures,
        "files": results,
    }
    _atomic_json(args.manifest.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
