#!/usr/bin/env python3
"""Archive issuer evidence without changing deployed code, HEAD, or real index."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


TICKERS = ("00403A", "00981A", "00988A", "00991A", "0050", "0056", "00830", "00878", "00891", "00918", "009805", "009820")
PATHS = tuple(f"data/{'etf' if ticker.endswith('A') else 'passive'}_{ticker}_{kind}.json"
              for ticker in TICKERS for kind in ("history", "log"))


def git(root, *args, data=None, env=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def blob_at(root, revision, path):
    return git(root, "rev-parse", f"{revision}:{path}").decode()


def archive(root: Path, *, attempts=3):
    root = root.resolve()
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").decode())
    receipt = git_dir / "issuer-archive.json"
    base = (json.loads(receipt.read_text())["published_commit"] if receipt.exists()
            else git(root, "rev-parse", "HEAD").decode())
    deployed = git(root, "rev-parse", "HEAD").decode()
    if receipt.exists():
        try:
            git(root, "merge-base", "--is-ancestor", base, deployed)
            base = deployed  # A later reviewed deployment may include newer evidence.
        except RuntimeError:
            git(root, "merge-base", "--is-ancestor", deployed, base)
    # Capture one immutable input batch; never stage arbitrary data/*.json or JPGs.
    blobs = {}
    for path in PATHS:
        content = (root / path).read_bytes()
        json.loads(content)
        blobs[path] = git(root, "hash-object", "-w", "--stdin", data=content).decode()

    with tempfile.TemporaryDirectory(prefix="stock-issuer-index-") as temp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index"),
               "GIT_AUTHOR_NAME": "OCI Server Bot", "GIT_AUTHOR_EMAIL": "oci-bot@localhost",
               "GIT_COMMITTER_NAME": "OCI Server Bot", "GIT_COMMITTER_EMAIL": "oci-bot@localhost"}
        for attempt in range(1, attempts + 1):
            git(root, "fetch", "origin", "main")
            parent = git(root, "rev-parse", "FETCH_HEAD").decode()
            git(root, "merge-base", "--is-ancestor", base, parent)
            for path, oid in blobs.items():
                upstream = blob_at(root, parent, path)
                if upstream not in {blob_at(root, base, path), oid}:
                    raise RuntimeError(f"issuer evidence changed independently upstream: {path}; reconciliation required")
            git(root, "read-tree", parent, env=env)
            for path, oid in blobs.items():
                git(root, "update-index", "--add", "--cacheinfo", f"100644,{oid},{path}", env=env)
            tree = git(root, "write-tree", env=env).decode()
            if tree == git(root, "rev-parse", f"{parent}^{{tree}}").decode():
                published = parent
            else:
                published = git(root, "commit-tree", tree, "-p", parent,
                                "-m", "Archive sealed issuer acquisition histories and receipts", env=env).decode()
                try:
                    git(root, "push", "origin", f"{published}:refs/heads/main")
                except RuntimeError:
                    # Retry only if the branch actually advanced. Auth/network errors stay failures.
                    git(root, "fetch", "origin", "main")
                    if attempt == attempts or git(root, "rev-parse", "FETCH_HEAD").decode() == parent:
                        raise
                    continue
            payload = {"published_commit": published, "attempts": attempt, "paths": list(PATHS)}
            pending = receipt.with_suffix(".tmp")
            pending.write_text(json.dumps(payload, indent=2) + "\n")
            os.replace(pending, receipt)
            print(f"Issuer Git archive verified: {published}; attempts={attempt}; deployed HEAD/index unchanged")
            return payload
    raise RuntimeError("issuer archive retries exhausted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    archive(parser.parse_args().root)
