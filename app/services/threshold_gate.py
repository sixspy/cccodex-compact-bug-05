"""阈值闸唯一读口。

现行阈值只有一个落点：``lab_settings.compact_threshold_kb``。
每次“保存阈值”都会在 ``threshold_snapshots`` 里原子地追加一行快照，
并把当时的闸凭证（credential）指向最新一行。

挂件 / 任务编排都先问本模块要闸状态：

* 现行阈值与最新快照阈值一致，且没有遗留的旧闸值 —— 闸已对齐；
* 任一不一致 —— 口径漂移，拒绝挂件入库。

旧的 ``meta.runtime_threshold_kb`` 不再被读取为执行闸，只作为
“旧闸是否已收回”的旁证：保存阈值时会删除该键；若被外部写回旧值，
则视为旧闸仍在，挂件会被拦下。展示面与执行面因此共用同一读数。
"""
from __future__ import annotations

import secrets
import sqlite3

from app.db import connect, get_db
from app.services.errors import ServiceError

OK_CHIP = "新闸已生效"
BAD_CHIP = "运行闸"
DEFAULT_THRESHOLD_KB = 256
SETTING_KEY = "compact_threshold_kb"

# 旧代码遗留、仅用于判断旧闸是否已收回的执行闸键。
LEGACY_RUNTIME_KEY = "runtime_threshold_kb"
GATE_CREDENTIAL_KEY = "thr_gate_credential"
GATE_SEQ_KEY = "thr_gate_seq"
GATE_THRESHOLD_KEY = "thr_gate_threshold_kb"


def _parse_threshold(raw: object) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD_KB


def current_threshold_kb(db_path: str | None = None) -> int:
    """现行阈值：展示与执行共用这一个读数。"""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM lab_settings WHERE key=?", (SETTING_KEY,)
        ).fetchone()
        return _parse_threshold(row["value"]) if row else DEFAULT_THRESHOLD_KB


# 兼容旧调用名：展示读口与执行读口必须是同一个。
def display_threshold_kb(db_path: str | None = None) -> int:
    return current_threshold_kb(db_path)


def runtime_threshold_kb(db_path: str | None = None) -> int:
    return current_threshold_kb(db_path)


def new_credential(seq: int, threshold_kb: int) -> str:
    return f"thr-{seq}-{threshold_kb}-{secrets.token_hex(6)}"


def _meta_value(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _legacy_skewed(conn, threshold_kb: int) -> bool:
    """旧闸值仍在且与现行阈值不同 —— 说明还有波次会吃旧值。"""
    legacy = _meta_value(conn, LEGACY_RUNTIME_KEY)
    if legacy is None:
        return False
    try:
        return int(str(legacy).strip()) != int(threshold_kb)
    except (TypeError, ValueError):
        return True


def gate_state_from_conn(conn: sqlite3.Connection) -> dict:
    """在调用方已有事务内计算闸状态，避免读写之间的 TOCTOU。"""
    row = conn.execute(
        "SELECT value FROM lab_settings WHERE key=?", (SETTING_KEY,)
    ).fetchone()
    threshold = _parse_threshold(row["value"]) if row else DEFAULT_THRESHOLD_KB
    snap = conn.execute(
        "SELECT * FROM threshold_snapshots ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    legacy_skew = _legacy_skewed(conn, threshold)

    snap_threshold = int(snap["threshold_kb"]) if snap else None
    credential = snap["credential"] if snap else None
    seq = int(snap["seq"]) if snap else 0

    if snap is None:
        reason = "阈值快照缺失"
    elif snap_threshold != threshold:
        reason = "现行阈值与快照口径漂移"
    elif legacy_skew:
        reason = "旧闸值尚未收回"
    else:
        reason = ""

    aligned = not reason
    return {
        "threshold_kb": threshold,
        "snapshot_threshold_kb": snap_threshold,
        "credential": credential,
        "seq": seq,
        "aligned": aligned,
        "chip": OK_CHIP if aligned else BAD_CHIP,
        "reason": reason,
    }


def gate_state(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return gate_state_from_conn(conn)


def require_aligned_from_conn(conn: sqlite3.Connection) -> dict:
    state = gate_state_from_conn(conn)
    if not state["aligned"]:
        raise ServiceError("阈值口径漂移，拒绝挂件入库", 409)
    return state


def require_aligned(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return require_aligned_from_conn(conn)


def ensure_initial_snapshot(db_path: str | None = None) -> None:
    """升级/首次初始化：没有快照时按现行阈值补一行对齐快照。

    只在没有历史快照时补，且顺手收回旧闸键；已有快照时绝不覆盖，
    以免把已被外部改过的阈值“洗白”。
    """
    with get_db(db_path) as conn:
        has = conn.execute(
            "SELECT 1 FROM threshold_snapshots LIMIT 1"
        ).fetchone()
        if has:
            return
        row = conn.execute(
            "SELECT value FROM lab_settings WHERE key=?", (SETTING_KEY,)
        ).fetchone()
        if not row:
            return
        threshold = _parse_threshold(row["value"])
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM threshold_snapshots"
        ).fetchone()
        seq = int(seq_row["m"]) + 1
        conn.execute(
            "INSERT INTO threshold_snapshots(seq, threshold_kb, credential, source) "
            "VALUES (?,?,?, 'seed')",
            (seq, threshold, new_credential(seq, threshold)),
        )
        conn.execute("DELETE FROM meta WHERE key=?", (LEGACY_RUNTIME_KEY,))


def save_threshold(value: str, db_path: str | None = None) -> dict:
    """单次落盘：改现行阈值 + 追加快照 + 收回旧闸，全在同一事务内。

    使用 ``BEGIN IMMEDIATE`` 抢占写锁，两名仓管同时保存时依次串行落盘，
    不会互相覆盖对方的闸凭证（每次都会得到新的 seq / credential）。
    """
    text = (value or "").strip()
    try:
        threshold = int(text)
    except (TypeError, ValueError) as exc:
        raise ServiceError("阈值必须是整数 KB") from exc
    if threshold <= 0:
        raise ServiceError("阈值必须大于 0")

    conn = connect(db_path)
    conn.isolation_level = None  # 手动控制事务
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM lab_settings WHERE key=?", (SETTING_KEY,)
        ).fetchone()
        if not row:
            raise ServiceError("参数不存在", 404)
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM threshold_snapshots"
        ).fetchone()
        seq = int(seq_row["m"]) + 1
        credential = new_credential(seq, threshold)
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?",
            (str(threshold), SETTING_KEY),
        )
        conn.execute(
            "INSERT INTO threshold_snapshots(seq, threshold_kb, credential, source) "
            "VALUES (?,?,?, 'save')",
            (seq, threshold, credential),
        )
        # 收回旧闸：展示面/执行面只认 lab_settings + 最新快照。
        conn.execute("DELETE FROM meta WHERE key=?", (LEGACY_RUNTIME_KEY,))
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (GATE_CREDENTIAL_KEY, credential),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (GATE_SEQ_KEY, str(seq)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (GATE_THRESHOLD_KEY, str(threshold)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return gate_state(db_path)
