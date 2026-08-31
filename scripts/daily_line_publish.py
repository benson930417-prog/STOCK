"""Publish the daily active-ETF LINE report exactly once when it is complete.

This is deliberately independent from the fetcher's ``NEW DATA FOUND`` flag.
A repaired/replayed pipeline may have written today's source files in an
earlier attempt; publication is controlled by a durable per-trading-day
receipt instead of by what changed in the current process.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.line_active_report_payload import (  # noqa: E402
    ACTIVE_TICKERS,
    build_active_report_messages,
)
from src.market_db import load_holding_history  # noqa: E402


EXPECTED_TICKERS = (
    "00403A",
    "00981A",
    "00988A",
    "00991A",
    "0050",
    "0056",
    "00830",
    "00878",
    "00891",
    "00918",
    "009805",
    "009820",
)
DEFAULT_MARKET_DIR = Path("/var/lib/stock/market")
DEFAULT_DATA_DIR = ROOT / "data"
CHANNEL = "LINE_BROADCAST_ACTIVE_ETF"
SCHEMA_VERSION = 1
LINE_ENDPOINT = "https://api.line.me/v2/bot/message/broadcast"
RETRY_NAMESPACE = uuid.UUID("975a8d57-14e8-4969-8d9f-931b5dfdcdb7")


class PublicationError(RuntimeError):
    """A publication precondition or send operation failed."""


class PublicationSendError(PublicationError):
    """A LINE error whose acceptance may or may not be knowable."""

    def __init__(self, message: str, *, uncertain: bool):
        super().__init__(message)
        self.uncertain = uncertain


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublicationError(f"missing required file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"unreadable JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"expected JSON object: {path}")
    return value


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def receipt_path(market_dir: Path, trading_date: str) -> Path:
    return (
        market_dir
        / "notifications"
        / "line-active-report"
        / f"{trading_date}.json"
    )


def upstream_ready_path(market_dir: Path, trading_date: str) -> Path:
    return (
        market_dir
        / "notifications"
        / "line-active-report-ready"
        / f"{trading_date}.json"
    )


def record_upstream_state(
    trading_date: str,
    status: str,
    *,
    market_dir: Path = DEFAULT_MARKET_DIR,
    reason: str | None = None,
) -> dict:
    manifest = read_json(market_dir / "holdings-fetch.json")
    if manifest.get("trading_date") != trading_date:
        raise PublicationError(
            f"cannot mark {trading_date}: fetch is for {manifest.get('trading_date')}"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "trading_date": trading_date,
        "status": status,
        "fetch_run_id": manifest.get("run_id"),
        "reason": reason,
        "updated_at_utc": utc_now(),
    }
    atomic_write_json(upstream_ready_path(market_dir, trading_date), payload)
    return payload


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicationError(f"invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_import(
    market_db: Path,
    trading_date: str,
    manifest: dict,
) -> dict:
    if not market_db.exists():
        raise PublicationError(f"missing canonical market database: {market_db}")
    try:
        with closing(sqlite3.connect(
            f"file:{market_db}?mode=ro", uri=True, timeout=30
        )) as connection:
            row = connection.execute(
                """SELECT status, failure_count, report_json, finished_at_utc
                     FROM ingest_runs
                    WHERE job='ETF_ISSUER_HOLDINGS' AND trading_date=?
                    ORDER BY finished_at_utc DESC LIMIT 1""",
                (trading_date,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise PublicationError(f"cannot verify holdings import: {exc}") from exc
    if not row:
        raise PublicationError("today's issuer holdings import is missing")
    status, failure_count, report_json, finished_at_utc = row
    if status != "CLEAN" or int(failure_count or 0) != 0:
        raise PublicationError(
            f"holdings import is not CLEAN: status={status} failures={failure_count}"
        )
    try:
        report = json.loads(report_json or "{}")
    except json.JSONDecodeError as exc:
        raise PublicationError("holdings import report_json is invalid") from exc
    if report.get("source_run_id") != manifest.get("run_id"):
        raise PublicationError("holdings import did not consume the current fetch run")
    if report.get("manifest_sha256") != _canonical_sha256(manifest):
        raise PublicationError("holdings import manifest checksum mismatch")
    return {
        "status": status,
        "finished_at_utc": finished_at_utc,
        "source_run_id": report.get("source_run_id"),
    }


def validate_inputs(
    trading_date: str,
    *,
    market_dir: Path = DEFAULT_MARKET_DIR,
    data_dir: Path = DEFAULT_DATA_DIR,
    expected_tickers: tuple[str, ...] = EXPECTED_TICKERS,
    active_tickers: list[str] = ACTIVE_TICKERS,
    history_loader=load_holding_history,
) -> tuple[dict, bytes]:
    """Return completeness evidence and the deterministic LINE request body."""
    manifest_path = market_dir / "holdings-fetch.json"
    manifest = read_json(manifest_path)
    if manifest.get("job") != "ETF_ISSUER_FETCH":
        raise PublicationError("holdings fetch manifest has the wrong job")
    if manifest.get("status") != "CLEAN" or int(manifest.get("failure_count") or 0):
        raise PublicationError("holdings fetch manifest is not CLEAN")
    if manifest.get("trading_date") != trading_date:
        raise PublicationError(
            f"fetch manifest is for {manifest.get('trading_date')}, not {trading_date}"
        )
    files = manifest.get("files")
    if not isinstance(files, list):
        raise PublicationError("holdings fetch manifest has no files list")
    by_ticker = {
        str(item.get("ticker")): item for item in files if isinstance(item, dict)
    }
    missing = sorted(set(expected_tickers) - set(by_ticker))
    unexpected = sorted(set(by_ticker) - set(expected_tickers))
    if missing or unexpected or len(files) != len(expected_tickers):
        raise PublicationError(
            f"issuer set mismatch: missing={missing} unexpected={unexpected} "
            f"files={len(files)}/{len(expected_tickers)}"
        )
    bad_files = [
        ticker
        for ticker in expected_tickers
        if by_ticker[ticker].get("status") != "CLEAN"
        or int(by_ticker[ticker].get("row_count") or 0) <= 0
        or not by_ticker[ticker].get("latest_date")
    ]
    if bad_files:
        raise PublicationError(f"issuer files are incomplete: {bad_files}")

    fetch_finished = _parse_utc(manifest.get("finished_at_utc"), "finished_at_utc")
    upstream = read_json(upstream_ready_path(market_dir, trading_date))
    if (
        upstream.get("schema_version") != SCHEMA_VERSION
        or upstream.get("trading_date") != trading_date
        or upstream.get("status") != "READY"
        or upstream.get("fetch_run_id") != manifest.get("run_id")
    ):
        raise PublicationError(
            "derived/cache/Git upstream has not certified READY for this fetch run"
        )
    import_evidence = _validate_import(
        market_dir / "market.db", trading_date, manifest
    )

    action_path = data_dir / "etf_action_insight.json"
    action = read_json(action_path)
    if action.get("as_of") != trading_date or not str(action.get("line_text") or "").strip():
        raise PublicationError(
            f"ETF action insight is not complete for {trading_date}"
        )
    action_generated = _parse_utc(action.get("generated"), "action insight generated")
    if action_generated < fetch_finished:
        raise PublicationError("ETF action insight predates the sealed fetch")

    summaries: dict[str, dict] = {}
    for ticker in active_tickers:
        history = history_loader(ticker)
        if not history:
            raise PublicationError(f"market.db has no holding history for {ticker}")
        latest_history = max(history)
        expected_latest = str(by_ticker[ticker].get("latest_date"))
        if latest_history != expected_latest:
            raise PublicationError(
                f"{ticker} market.db latest={latest_history}, manifest={expected_latest}"
            )
        image = data_dir / "summaries" / f"etf_{ticker}_summary_latest.jpg"
        try:
            stat = image.stat()
        except OSError as exc:
            raise PublicationError(f"missing summary image for {ticker}: {image}") from exc
        image_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if stat.st_size <= 0 or image_mtime < fetch_finished:
            raise PublicationError(
                f"summary image for {ticker} is empty or predates the sealed fetch"
            )
        summaries[ticker] = {
            "latest_holding_date": latest_history,
            "bytes": stat.st_size,
            "mtime_utc": image_mtime.isoformat().replace("+00:00", "Z"),
        }

    cache_buster = int(trading_date.replace("-", ""))
    messages = build_active_report_messages(
        active_tickers,
        data_dir=data_dir,
        action_cache=action_path,
        cache_buster=cache_buster,
        history_loader=history_loader,
    )
    if len(messages) != 1 + len(active_tickers) or len(messages) > 5:
        raise PublicationError(f"invalid LINE object count: {len(messages)}")
    body = json.dumps(
        {"messages": messages}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    evidence = {
        "fetch": {
            "run_id": manifest.get("run_id"),
            "finished_at_utc": manifest.get("finished_at_utc"),
            "ticker_count": len(files),
        },
        "upstream": upstream,
        "import": import_evidence,
        "action_insight": {
            "as_of": action.get("as_of"),
            "generated": action.get("generated"),
        },
        "summaries": summaries,
        "message_object_count": len(messages),
    }
    return evidence, body


def send_line(body: bytes, token: str, retry_key: str) -> tuple[int, str]:
    req = request.Request(
        LINE_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Line-Retry-Key": retry_key,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 409:
            # LINE returns 409 when this retry key was already accepted.  That
            # is a successful idempotent recovery, not a failed publication.
            return exc.code, response_body
        raise PublicationSendError(
            f"LINE rejected broadcast: HTTP {exc.code} {response_body}",
            uncertain=500 <= exc.code <= 599,
        ) from exc
    except OSError as exc:
        raise PublicationSendError(
            f"LINE broadcast transport failed: {exc}", uncertain=True
        ) from exc


def defer_publication(
    trading_date: str,
    reason: str,
    *,
    market_dir: Path = DEFAULT_MARKET_DIR,
) -> dict:
    path = receipt_path(market_dir, trading_date)
    existing = read_json(path) if path.exists() else {}
    if existing.get("status") == "SENT":
        return existing
    payload = {
        **existing,
        "schema_version": SCHEMA_VERSION,
        "channel": CHANNEL,
        "trading_date": trading_date,
        "status": "PENDING",
        "pending_reason": reason,
        "updated_at_utc": utc_now(),
    }
    atomic_write_json(path, payload)
    return payload


def publish_daily(
    trading_date: str,
    token: str,
    *,
    market_dir: Path = DEFAULT_MARKET_DIR,
    data_dir: Path = DEFAULT_DATA_DIR,
    sender: Callable[[bytes, str, str], tuple[int, str]] = send_line,
    expected_tickers: tuple[str, ...] = EXPECTED_TICKERS,
    active_tickers: list[str] = ACTIVE_TICKERS,
    history_loader=load_holding_history,
) -> dict:
    path = receipt_path(market_dir, trading_date)
    existing = read_json(path) if path.exists() else {}
    if existing.get("status") == "SENT":
        print(
            f"LINE publication already SENT for {trading_date}: "
            f"{existing.get('accepted_at_utc')}"
        )
        return existing

    try:
        evidence, body = validate_inputs(
            trading_date,
            market_dir=market_dir,
            data_dir=data_dir,
            expected_tickers=expected_tickers,
            active_tickers=active_tickers,
            history_loader=history_loader,
        )
    except PublicationError as exc:
        defer_publication(trading_date, f"INCOMPLETE: {exc}", market_dir=market_dir)
        raise

    payload_sha256 = hashlib.sha256(body).hexdigest()
    attempted = int(existing.get("attempt_count") or 0) > 0
    old_sha256 = existing.get("payload_sha256")
    if attempted and old_sha256 and old_sha256 != payload_sha256:
        reason = "payload changed after a LINE attempt; manual reconciliation required"
        defer_publication(trading_date, reason, market_dir=market_dir)
        raise PublicationError(reason)
    if attempted and existing.get("status") == "RETRYABLE_UNCERTAIN":
        first_attempt = _parse_utc(
            existing.get("first_attempt_at_utc"), "first LINE attempt"
        )
        if datetime.now(timezone.utc) - first_attempt >= timedelta(hours=24):
            expired = {
                **existing,
                "status": "UNCERTAIN_EXPIRED",
                "pending_reason": (
                    "LINE acceptance was uncertain and its 24-hour retry-key "
                    "window expired; reconcile manually before another send"
                ),
                "updated_at_utc": utc_now(),
            }
            atomic_write_json(path, expired)
            raise PublicationError(expired["pending_reason"])

    retry_key = str(
        existing.get("retry_key")
        or uuid.uuid5(RETRY_NAMESPACE, f"{trading_date}:{payload_sha256}")
    )
    ready = {
        **existing,
        "schema_version": SCHEMA_VERSION,
        "channel": CHANNEL,
        "trading_date": trading_date,
        "status": "READY",
        "payload_sha256": payload_sha256,
        "retry_key": retry_key,
        "evidence": evidence,
        "ready_at_utc": existing.get("ready_at_utc") or utc_now(),
        "updated_at_utc": utc_now(),
    }
    atomic_write_json(path, ready)
    if not token:
        defer_publication(trading_date, "LINE token is unavailable", market_dir=market_dir)
        raise PublicationError("LINE token is unavailable")

    sending = {
        **ready,
        "status": "SENDING",
        "attempt_count": int(existing.get("attempt_count") or 0) + 1,
        "last_attempt_at_utc": utc_now(),
        "updated_at_utc": utc_now(),
    }
    sending["first_attempt_at_utc"] = (
        existing.get("first_attempt_at_utc") or sending["last_attempt_at_utc"]
    )
    atomic_write_json(path, sending)
    try:
        status, response_body = sender(body, token, retry_key)
    except Exception as exc:
        uncertain = not isinstance(exc, PublicationSendError) or exc.uncertain
        failed = {
            **sending,
            "status": "RETRYABLE_UNCERTAIN" if uncertain else "RETRYABLE_REJECTED",
            "last_error": str(exc),
            "updated_at_utc": utc_now(),
        }
        atomic_write_json(path, failed)
        raise PublicationError(str(exc)) from exc
    if status not in {200, 409}:
        failed = {
            **sending,
            "status": "RETRYABLE_UNCERTAIN",
            "last_error": f"unexpected HTTP {status}: {response_body}",
            "updated_at_utc": utc_now(),
        }
        atomic_write_json(path, failed)
        raise PublicationError(failed["last_error"])

    sent = {
        **sending,
        "status": "SENT",
        "accepted_at_utc": utc_now(),
        "acceptance": "HTTP_200" if status == 200 else "HTTP_409_ALREADY_ACCEPTED",
        "http_status": status,
        "response_body": response_body,
        "updated_at_utc": utc_now(),
    }
    sent.pop("last_error", None)
    sent.pop("pending_reason", None)
    atomic_write_json(path, sent)
    print(
        f"LINE publication SENT for {trading_date}: HTTP {status}; "
        f"retry_key={retry_key}; objects={evidence['message_object_count']}"
    )
    return sent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trading-date", required=True)
    parser.add_argument("--market-dir", type=Path, default=DEFAULT_MARKET_DIR)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--defer",
        metavar="REASON",
        help="record a pending publication without contacting LINE",
    )
    parser.add_argument(
        "--confirm-upstream-ready",
        action="store_true",
        help="record that this orchestrator completed every required upstream step",
    )
    args = parser.parse_args()
    try:
        if args.defer is not None:
            record_upstream_state(
                args.trading_date,
                "BLOCKED",
                market_dir=args.market_dir,
                reason=args.defer,
            )
            state = defer_publication(
                args.trading_date, args.defer, market_dir=args.market_dir
            )
            print(
                f"LINE publication {state.get('status')} for {args.trading_date}: "
                f"{state.get('pending_reason', 'already sent')}"
            )
            return 0
        if args.confirm_upstream_ready:
            record_upstream_state(
                args.trading_date, "READY", market_dir=args.market_dir
            )
        token = os.environ.get("LINE_TOKEN") or os.environ.get(
            "LINE_CHANNEL_ACCESS_TOKEN"
        )
        publish_daily(
            args.trading_date,
            token or "",
            market_dir=args.market_dir,
            data_dir=args.data_dir,
        )
        return 0
    except PublicationError as exc:
        print(f"LINE publication blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
