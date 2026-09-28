import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.market_chart_cache import file_sha256  # noqa: E402


QUOTE_CACHE_DIR = ROOT_DIR / "data" / "quote_cache"
CHART_SERVICE_URL = "http://127.0.0.1:5005"
UPSTREAM_BLOCKED_BACKOFF_SECONDS = 300
UPSTREAM_BLOCKED_MAX_BACKOFF_SECONDS = 1800
UPSTREAM_BLOCKED_STATE_MAX_AGE_SECONDS = 6 * 60 * 60
OUTAGE_STATE_PATH = QUOTE_CACHE_DIR / "market_monitor_outage.json"
SCHEDULE_STATE_PATH = QUOTE_CACHE_DIR / "market_monitor_schedule.json"


class ChartServiceUnavailable(RuntimeError):
    def __init__(self, message, retry_after_seconds=60):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.chmod(0o644)
    tmp_path.replace(path)


def upstream_blocked_backoff_seconds(consecutive_outages):
    exponent = max(0, int(consecutive_outages) - 1)
    return min(
        UPSTREAM_BLOCKED_BACKOFF_SECONDS * (2 ** exponent),
        UPSTREAM_BLOCKED_MAX_BACKOFF_SECONDS,
    )


def load_outage_state(now_epoch=None):
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    try:
        payload = json.loads(OUTAGE_STATE_PATH.read_text(encoding="utf-8"))
        updated_epoch = float(payload["updated_epoch"])
        if now_epoch - updated_epoch > UPSTREAM_BLOCKED_STATE_MAX_AGE_SECONDS:
            return {"consecutive_outages": 0, "next_retry_epoch": 0.0}
        return {
            "consecutive_outages": max(0, int(payload["consecutive_outages"])),
            "next_retry_epoch": max(0.0, float(payload["next_retry_epoch"])),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"consecutive_outages": 0, "next_retry_epoch": 0.0}


def record_outage(consecutive_outages, now_epoch=None):
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    delay = upstream_blocked_backoff_seconds(consecutive_outages)
    atomic_write_json(
        OUTAGE_STATE_PATH,
        {
            "consecutive_outages": int(consecutive_outages),
            "updated_epoch": now_epoch,
            "next_retry_epoch": now_epoch + delay,
        },
    )
    return delay


def clear_outage_state():
    try:
        OUTAGE_STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def post_chart_service(endpoint, key, timeout):
    response = requests.post(
        f"{CHART_SERVICE_URL}/{endpoint}",
        json={"key": key},
        timeout=timeout,
    )
    if response.status_code == 503:
        try:
            detail = str(response.json().get("detail") or "")
        except (TypeError, ValueError):
            detail = response.text[:500]
        blocked = "upstream blocked" in detail.lower()
        raise ChartServiceUnavailable(
            detail or f"chart service unavailable for {key}",
            retry_after_seconds=(
                UPSTREAM_BLOCKED_BACKOFF_SECONDS if blocked else 60
            ),
        )
    response.raise_for_status()
    return response.json()


def refresh_key(key, timeout):
    # Single /snapshot call captures the chart image AND the price/% from the
    # same page render, so the cached text matches the chart at the same moment.
    snapshot_payload = post_chart_service("snapshot", key, timeout)
    if not snapshot_payload.get("url"):
        raise RuntimeError(f"{key} snapshot returned no url: {snapshot_payload}")
    text = snapshot_payload.get("text")
    if not text:
        raise RuntimeError(
            f"{key} snapshot returned no text (same-moment quote failed): {snapshot_payload}"
        )
    snapshot_path = Path(
        snapshot_payload.get("path")
        or ROOT_DIR / "data" / "images" / snapshot_payload["url"]
    )
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT_DIR / snapshot_path
    snapshot_sha256 = file_sha256(snapshot_path)
    service_sha256 = str(snapshot_payload.get("sha256") or "")
    if not service_sha256:
        raise RuntimeError(f"{key} snapshot returned no image checksum")
    if snapshot_sha256 != service_sha256:
        raise RuntimeError(
            f"{key} snapshot changed before cache commit: "
            f"service={service_sha256[:12]} current={snapshot_sha256[:12]}"
        )

    payload = {
        "key": key,
        "updated_at": utc_now_iso(),
        "text": text,
        "quote": snapshot_payload.get("quote"),
        "snapshot_url": snapshot_payload["url"],
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": snapshot_sha256,
        "snapshot_size": int(
            snapshot_payload.get("size") or os.path.getsize(snapshot_path)
        ),
        "clip": snapshot_payload.get("clip"),
        "viewport": snapshot_payload.get("viewport"),
        "source": "chart_service",
    }
    atomic_write_json(QUOTE_CACHE_DIR / f"market_{key}.json", payload)
    print(f"{payload['updated_at']} refreshed market chart cache: {key}", flush=True)
    return payload


def refresh_cycle(keys, timeout):
    for key in keys:
        try:
            refresh_key(key, timeout)
        except ChartServiceUnavailable as exc:
            print(
                f"{utc_now_iso()} chart service unavailable at {key}; "
                f"stopping this cycle and backing off {exc.retry_after_seconds}s: {exc}",
                flush=True,
            )
            return exc.retry_after_seconds
        except Exception as exc:
            print(f"{utc_now_iso()} error refreshing market chart cache {key}: {type(exc).__name__}: {exc}", flush=True)
    return 0


def load_schedule(keys, now_epoch=None):
    now = time.time() if now_epoch is None else float(now_epoch)
    try:
        state = json.loads(SCHEDULE_STATE_PATH.read_text(encoding="utf-8"))
        if state.get("version") != 1 or now - float(state["updated_epoch"]) > 21600:
            raise ValueError("expired schedule")
        entries = {}
        for key in keys:
            item = state.get("keys", {}).get(key, {})
            entries[key] = {
                "next_due": max(0, min(now + 1800, float(item.get("next_due", 0)))),
                "failures": max(0, min(20, int(item.get("failures", 0)))),
                "status": str(item.get("status", "pending")),
            }
        return {"version": 1, "updated_epoch": now, "keys": entries,
                "provider_until": max(0, min(now + 1800, float(state.get("provider_until", 0))))}
    except (OSError, ValueError, TypeError, KeyError):
        # Preserve an existing upstream cooldown across the first deployment.
        previous = load_outage_state(now_epoch=now)
        return {"version": 1, "updated_epoch": now, "provider_until": previous["next_retry_epoch"],
                "keys": {key: {"next_due": 0, "failures": 0, "status": "pending"} for key in keys}}


def run_due_refreshes(keys, state, *, interval=60, timeout=200, clock=time.time):
    """One serial worker, independent due times, no catch-up bursts.

    All current symbol URLs, including IG:NASDAQ, are TradingView pages.
    A single failed page cools down alone; two blocked pages in this pass
    also open a provider circuit. No alternate source is ever substituted.
    """
    now = clock()
    if now < state.get("provider_until", 0):
        return
    blocked = 0
    due = sorted((key for key in keys if state['keys'][key]['next_due'] <= now),
                 key=lambda key: state['keys'][key]['next_due'])
    for key in due:
        started = clock()
        item = state['keys'][key]
        try:
            refresh_key(key, timeout)
        except Exception as exc:
            item['failures'] = min(20, item['failures'] + 1)
            is_blocked = (isinstance(exc, ChartServiceUnavailable)
                          and exc.retry_after_seconds == UPSTREAM_BLOCKED_BACKOFF_SECONDS)
            if is_blocked:
                blocked += 1
                delay = upstream_blocked_backoff_seconds(item['failures'])
                item['status'] = 'upstream_blocked'
            else:
                delay = max(60, interval)
                item['status'] = 'unavailable'
            item['next_due'] = clock() + delay
            print(f"{utc_now_iso()} {key} refresh failed: {type(exc).__name__}: {exc}; retry in {delay}s", flush=True)
        else:
            item.update(failures=0, status='ok', next_due=max(started + interval, clock() + 1))
        if blocked >= 2:
            state['provider_until'] = clock() + UPSTREAM_BLOCKED_BACKOFF_SECONDS
        state['updated_epoch'] = clock()
        atomic_write_json(SCHEDULE_STATE_PATH, state)
        if blocked >= 2:
            break


def next_schedule_delay(keys, state, *, now_epoch=None):
    now = time.time() if now_epoch is None else float(now_epoch)
    earliest = min(state['keys'][key]['next_due'] for key in keys)
    earliest = max(earliest, state.get('provider_until', 0))
    return max(1, min(60, earliest - now))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "keys", nargs="*",
        default=["oil", "brent", "bond", "gold", "usdtwd", "usdjpy", "usdchf", "nasdaq"],
    )
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=200)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.interval < 10 or args.timeout < 1:
        parser.error("interval must be >=10s and timeout positive")
    state = load_schedule(args.keys)
    while True:
        run_due_refreshes(args.keys, state, interval=args.interval, timeout=args.timeout)
        if args.once:
            raise SystemExit(0 if all(state['keys'][key]['status'] == 'ok' for key in args.keys) else 1)
        time.sleep(next_schedule_delay(args.keys, state))


if __name__ == "__main__":
    main()
