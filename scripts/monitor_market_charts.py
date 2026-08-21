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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "keys", nargs="*",
        default=["oil", "brent", "bond", "gold", "usdtwd", "usdjpy", "usdchf", "nasdaq"],
    )
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        backoff_seconds = refresh_cycle(args.keys, args.timeout)
        if args.once:
            if backoff_seconds:
                raise SystemExit(1)
            return
        time.sleep(backoff_seconds or max(10, args.interval))


if __name__ == "__main__":
    main()
