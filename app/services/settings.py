"""台参数：压实阈值与对账模式。"""
from __future__ import annotations

from app.db import get_db
from app.services.errors import ServiceError
from app.services.threshold_gate import (
    BAD_CHIP,
    OK_CHIP,
    SETTING_KEY,
    gate_state,
    save_threshold,
)


def _decorate(item: dict, db_path: str | None) -> dict:
    if item["key"] == SETTING_KEY:
        state = gate_state(db_path)
        item["runtime_gate"] = state["threshold_kb"]
        item["threshold_kb"] = state["threshold_kb"]
        item["snapshot_threshold_kb"] = state["snapshot_threshold_kb"]
        item["credential"] = state["credential"]
        item["snapshot_seq"] = state["seq"]
        item["aligned"] = state["aligned"]
        item["gate_reason"] = state["reason"]
        item["gate_chip"] = state["chip"]
        item["ok_chip"] = OK_CHIP
        item["bad_chip"] = BAD_CHIP
    return item


def list_settings(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
        items = [dict(r) for r in rows]
    return [_decorate(it, db_path) for it in items]


def update_setting(key: str, value: str, db_path: str | None = None) -> dict:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        raise ServiceError("参数键与值不能为空")
    if key == SETTING_KEY:
        # 阈值走单次落盘：现行值 + 快照 + 闸凭证同事务提交。
        state = save_threshold(value, db_path)
        return _decorate(
            {
                "key": key,
                "value": str(state["threshold_kb"]),
                "label": _label_of(key, db_path),
            },
            db_path,
        )
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lab_settings WHERE key=?", (key,)
        ).fetchone()
        if not row:
            raise ServiceError("参数不存在", 404)
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?", (value, key)
        )
        return dict(
            conn.execute(
                "SELECT key, value, label FROM lab_settings WHERE key=?", (key,)
            ).fetchone()
        )


def _label_of(key: str, db_path: str | None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT label FROM lab_settings WHERE key=?", (key,)
        ).fetchone()
        return row["label"] if row else ""
