import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
QUOTE_CACHE_DIR = ROOT_DIR / "data" / "quote_cache"
CHART_SERVICE_URL = "http://127.0.0.1:5005"


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
    response.raise_for_status()
    return response.json()


def refresh_key(key, timeout):
    text_payload = post_chart_service("market-text", key, timeout)
    snapshot_payload = post_chart_service("snapshot", key, timeout)
    if not text_payload.get("text"):
        raise RuntimeError(f"{key} market-text returned no text: {text_payload}")
    if not snapshot_payload.get("url"):
        raise RuntimeError(f"{key} snapshot returned no url: {snapshot_payload}")

    payload = {
        "key": key,
        "updated_at": utc_now_iso(),
        "text": text_payload["text"],
        "quote": text_payload.get("quote"),
        "snapshot_url": snapshot_payload["url"],
        "snapshot_path": snapshot_payload.get("path"),
        "clip": snapshot_payload.get("clip"),
        "viewport": snapshot_payload.get("viewport"),
        "source": "chart_service",
    }
    atomic_write_json(QUOTE_CACHE_DIR / f"market_{key}.json", payload)
    print(f"{payload['updated_at']} refreshed market chart cache: {key}", flush=True)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keys", nargs="*", default=["nasdaq"])
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        for key in args.keys:
            try:
                refresh_key(key, args.timeout)
            except Exception as exc:
                print(f"{utc_now_iso()} error refreshing market chart cache {key}: {type(exc).__name__}: {exc}", flush=True)
        if args.once:
            return
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
