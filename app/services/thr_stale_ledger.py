"""阈值旧闸旁路账：只记审计，不参与闸判定。"""
from __future__ import annotations

from app.db import get_db
from app.services.threshold_gate import OK_CHIP, gate_state
from app.services.thr_stale_surface import record_event, snapshot

_SLUG = "thr_stale"
_TITLE = "阈值旧闸"


def append_ledger(plan_id: int, note: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_ledger_{plan_id}", note or _TITLE),
        )
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM threshold_snapshots"
        ).fetchone()["m"]
        cred_row = conn.execute(
            "SELECT credential, threshold_kb FROM threshold_snapshots WHERE seq=?",
            (seq,),
        ).fetchone()
    record_event(f"plan={plan_id} {note}", db_path)
    state = gate_state(db_path)
    return {
        "plan_id": plan_id,
        "note": note,
        "panel": state["chip"],
        "truth": OK_CHIP if state["aligned"] else "运行闸",
        "credential": cred_row["credential"] if cred_row else None,
        "threshold_kb": cred_row["threshold_kb"] if cred_row else None,
    }


def ledger_rows(db_path: str | None = None) -> list[dict]:
    state = gate_state(db_path)
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_ledger_%",),
        ).fetchall()
        return [
            {"key": r["key"], "value": r["value"], "chip": state["chip"]}
            for r in rows
        ]


def dashboard(db_path: str | None = None) -> dict:
    snap = snapshot(db_path)
    return {
        "title": _TITLE,
        "chip": snap["panel_chip"],
        "truth": snap["truth"],
        "rows": ledger_rows(db_path),
        "ok_chip": OK_CHIP,
        "bad_chip": "运行闸",
        # 假绿 = 展示亮新闸，但闸实际未对齐。
        "fake_green": snap["panel_chip"] == OK_CHIP and not snap["aligned"],
        "aligned": snap["aligned"],
        "threshold_kb": snap["threshold_kb"],
        "snapshot_threshold_kb": snap["snapshot_threshold_kb"],
        "credential": snap["credential"],
        "reason": snap["reason"],
    }
