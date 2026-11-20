"""阈值闸读口：展示读 lab_settings，执行读 meta 旧闸。"""
from __future__ import annotations

from app.db import get_db


def display_threshold_kb(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM lab_settings WHERE key='compact_threshold_kb'"
        ).fetchone()
        return int(row["value"]) if row else 256


def runtime_threshold_kb(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='runtime_threshold_kb'"
        ).fetchone()
        if row:
            return int(row["value"])
        # seed stale gate once
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('runtime_threshold_kb','128')"
        )
        return 128


def sync_display_only(db_path: str | None = None) -> int:
    """Half-fix: copy display into another meta key, not runtime gate."""
    v = display_threshold_kb(db_path)
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('display_threshold_mirror', ?)",
            (str(v),),
        )
    return v
