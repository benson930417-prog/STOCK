import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.generate_master_holding_card import generate_master_holding_card


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def refresh_once(limit=50):
    text, paths = generate_master_holding_card(limit=limit)
    print(
        f"{utc_now()} updated master holding cache: "
        f"images={len(paths)}, text_chars={len(text)}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        try:
            refresh_once(limit=args.limit)
        except Exception as exc:
            print(f"{utc_now()} master holding cache update failed: {exc}", flush=True)
        if args.once:
            break
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
