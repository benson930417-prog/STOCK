"""Identity of canonical market inputs used by one set of public ETF images."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def market_revision(database):
    with closing(sqlite3.connect(f"file:{Path(database).resolve().as_posix()}?mode=ro", uri=True, timeout=30)) as db:
        rows = db.execute("""SELECT job,trading_date,status,failure_count,finished_at_utc,report_json
            FROM ingest_runs WHERE status='CLEAN'
            ORDER BY job,trading_date,finished_at_utc,report_json""").fetchall()
    return canonical_sha256(rows)
