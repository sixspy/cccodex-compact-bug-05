"""阈值旧闸旁路：只做审计留痕，不再拥有第二套闸。

展示芯片与“真相”都直接取 ``threshold_gate`` 的现行闸状态：
现行阈值与快照对齐才亮 ``新闸已生效``，否则亮 ``运行闸``。

“半修”只曾改动展示面，故本模块不再提供任何只刷芯片的写口：
``reconcile_surface`` 在闸未对齐时返回 ``half_fixed=False``，
绝不把假绿写进 meta 冒充已修。
"""
from __future__ import annotations

from app.db import get_db
from app.services.threshold_gate import BAD_CHIP, OK_CHIP, gate_state

_SLUG = "thr_stale"


def panel_chip(db_path: str | None = None) -> str:
    return gate_state(db_path)["chip"]


def truth_flag(db_path: str | None = None) -> str:
    state = gate_state(db_path)
    return OK_CHIP if state["aligned"] else BAD_CHIP


def seed_skew(db_path: str | None = None) -> None:
    """保留旧键位以便审计，但芯片/真相已由闸状态推导，不再参与判定。"""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_counter", "0"),
        )


def _bump_counter_conn(conn) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?",
        (f"{_SLUG}_counter",),
    ).fetchone()
    n = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (f"{_SLUG}_counter", str(n)),
    )
    return n


def bump_counter(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        return _bump_counter_conn(conn)


def snapshot(db_path: str | None = None) -> dict:
    state = gate_state(db_path)
    return {
        "title": "阈值旧闸",
        "panel_chip": state["chip"],
        "truth": OK_CHIP if state["aligned"] else BAD_CHIP,
        "counter": bump_counter(db_path),
        "aligned": state["aligned"],
        "threshold_kb": state["threshold_kb"],
        "snapshot_threshold_kb": state["snapshot_threshold_kb"],
        "credential": state["credential"],
        "seq": state["seq"],
        "reason": state["reason"],
    }


def list_events(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_evt_%",),
        ).fetchall()
        return [{"note": r["value"]} for r in rows]


def record_event(note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        n = _bump_counter_conn(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_evt_{n}", note or BAD_CHIP),
        )
    return snapshot(db_path)


def reconcile_surface(db_path: str | None = None) -> dict:
    """半修入口：只报告，不伪造。闸未对齐时明确标记半修未生效。"""
    state = gate_state(db_path)
    return {
        "panel_chip": state["chip"],
        "truth": OK_CHIP if state["aligned"] else BAD_CHIP,
        "half_fixed": state["aligned"],
        "reason": state["reason"],
        "credential": state["credential"],
        "snapshot_threshold_kb": state["snapshot_threshold_kb"],
        "threshold_kb": state["threshold_kb"],
    }
