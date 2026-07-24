"""Integrity helpers for cached market-chart images served to LINE."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


class MarketImageChecksumMismatch(RuntimeError):
    """The mutable chart file no longer matches its quote-cache metadata."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_market_reply_image(
    cache: dict,
    images_dir: Path,
    *,
    require_checksum: bool = True,
) -> str:
    """Copy the exact cached chart bytes to an immutable LINE filename."""
    key = str(cache.get("key") or "")
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise ValueError(f"Unsafe market cache key: {key!r}")

    source_name = str(cache.get("snapshot_url") or "")
    if not source_name or Path(source_name).name != source_name:
        raise ValueError(f"Unsafe market snapshot filename: {source_name!r}")

    expected = str(cache.get("snapshot_sha256") or "").lower()
    if require_checksum and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise MarketImageChecksumMismatch(
            f"Market cache has no valid snapshot checksum: {key}"
        )

    source_path = images_dir / source_name
    data = source_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise MarketImageChecksumMismatch(
            f"Market image changed during cache read: {key} "
            f"expected={expected[:12]} actual={actual[:12]}"
        )

    stamp = re.sub(r"[^0-9]", "", str(cache.get("updated_at") or ""))[:20]
    if not stamp:
        stamp = "undated"
    suffix = source_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError(f"Unsupported market snapshot type: {suffix!r}")
    target_name = f"line_market_{key}_{stamp}_{actual[:16]}{suffix}"
    target_path = images_dir / target_name

    if target_path.exists() and file_sha256(target_path) == actual:
        return target_name

    images_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "wb",
        dir=images_dir,
        prefix=f".{target_name}.",
        delete=False,
    ) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.chmod(0o644)
        os.replace(temporary_path, target_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return target_name
