"""阈值旧闸纠偏：快照凭证、拒绝旧闸挂件、并发保存不互相覆盖。"""
import threading

import pytest

from app.db import get_db
from app.services import plans, rewrite, settings
from app.services import threshold_gate as gate
from app.services.errors import ServiceError
from app.services.thr_stale_ledger import append_ledger, dashboard
from app.services.thr_stale_surface import reconcile_surface


def test_display_and_runtime_share_one_reading(db):
    st = {s["key"]: s for s in settings.list_settings(db_path=db)}
    thr = st["compact_threshold_kb"]
    assert thr["runtime_gate"] == thr["threshold_kb"] == 256
    assert thr["aligned"] is True
    assert thr["gate_chip"] == gate.OK_CHIP


def test_save_threshold_records_snapshot_and_credential(db):
    before = gate.gate_state(db)
    item = settings.update_setting("compact_threshold_kb", "300", db_path=db)
    after = gate.gate_state(db)
    assert after["threshold_kb"] == 300
    assert after["snapshot_threshold_kb"] == 300
    assert after["aligned"] is True
    assert after["seq"] == before["seq"] + 1
    assert after["credential"] != before["credential"]
    assert item["gate_chip"] == gate.OK_CHIP

    with get_db(db) as conn:
        rows = conn.execute(
            "SELECT seq, threshold_kb, credential FROM threshold_snapshots ORDER BY seq"
        ).fetchall()
    assert rows[-1]["threshold_kb"] == 300
    assert rows[-1]["credential"] == after["credential"]


def test_old_gate_rejects_attach_and_pins_new_chip(db):
    settings.update_setting("compact_threshold_kb", "512", db_path=db)
    # 外部把旧闸键写回旧值：闸口径漂移，必须拒绝挂件入库。
    with get_db(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('runtime_threshold_kb','128')"
        )
    state = gate.gate_state(db)
    assert state["aligned"] is False
    assert state["chip"] == gate.BAD_CHIP

    p = plans.create_plan("CP-OLD", "旧闸拒绝", "dw_payments", db_path=db)
    with pytest.raises(ServiceError) as exc:
        plans.attach_loose_files(p["id"], db_path=db)
    assert "拒绝挂件入库" in exc.value.message

    with get_db(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM plan_items WHERE plan_id=?", (p["id"],)
        ).fetchone()["c"]
    assert n == 0  # 漏拦即会非零，此处必须为零


def test_gate_skew_between_setting_and_snapshot_rejects(db):
    settings.update_setting("compact_threshold_kb", "300", db_path=db)
    # 直接改现行阈值但不动快照：口径漂移。
    with get_db(db) as conn:
        conn.execute(
            "UPDATE lab_settings SET value='400' WHERE key='compact_threshold_kb'"
        )
    state = gate.gate_state(db)
    assert state["aligned"] is False
    assert state["reason"] == "现行阈值与快照口径漂移"


def test_rewrite_refuses_when_gate_skewed(db):
    p = plans.create_plan("CP-RW", "编排闸", "dw_payments", db_path=db)
    plans.attach_loose_files(p["id"], db_path=db)
    plans.publish_plan(p["id"], db_path=db)
    with get_db(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('runtime_threshold_kb','64')"
        )
    with pytest.raises(ServiceError) as exc:
        rewrite.start_rewrite(p["id"], db_path=db)
    assert "拒绝挂件入库" in exc.value.message


def test_attach_obeys_current_threshold(db):
    # 抬高阈值到 512KB：dw_payments 的 290/305KB 都不该被挂入。
    settings.update_setting("compact_threshold_kb", "512", db_path=db)
    p = plans.create_plan("CP-HI", "高阈值", "dw_payments", db_path=db)
    r = plans.attach_loose_files(p["id"], db_path=db)
    assert r["attached"] == 0


def test_concurrent_saves_do_not_clobber_credentials(db):
    settings.update_setting("compact_threshold_kb", "256", db_path=db)
    errors = []

    def worker(val):
        try:
            settings.update_setting("compact_threshold_kb", str(val), db_path=db)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(v,)) for v in (250, 260, 270, 280)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    with get_db(db) as conn:
        seqs = conn.execute(
            "SELECT seq FROM threshold_snapshots ORDER BY seq"
        ).fetchall()
    # 每次保存各占一个 seq，无覆盖、无重号。
    seq_vals = [r["seq"] for r in seqs]
    assert seq_vals == sorted(seq_vals)
    assert len(seq_vals) == len(set(seq_vals))
    assert len(seq_vals) >= 5  # 初始 seed 快照 + 4 次并发保存
    state = gate.gate_state(db)
    assert state["aligned"] is True


def test_old_gate_qualifying_files_blocked_but_chip_lit(db):
    # 新闸 256KB：dw_users 的 180/195/210KB 只够旧闸(128)口径，必须被拦。
    settings.update_setting("compact_threshold_kb", "256", db_path=db)
    state = gate.gate_state(db)
    assert state["aligned"] is True
    assert state["chip"] == gate.OK_CHIP
    p = plans.create_plan("CP-UG", "旧闸口径文件", "dw_users", db_path=db)
    r = plans.attach_loose_files(p["id"], db_path=db)
    assert r["attached"] == 0
    assert r["gate"]["chip"] == gate.OK_CHIP
    with get_db(db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM plan_items WHERE plan_id=?", (p["id"],)
        ).fetchone()["c"]
    assert n == 0


def test_rewrite_does_not_merge_undersized_after_raise(db):
    # 低阈值下挂入，随后抬高阈值：旧挂件不得再按旧口径被合并。
    p = plans.create_plan("CP-RAISE", "抬阈值", "dw_payments", db_path=db)
    plans.attach_loose_files(p["id"], db_path=db)
    plans.publish_plan(p["id"], db_path=db)
    settings.update_setting("compact_threshold_kb", "999", db_path=db)
    with pytest.raises(ServiceError) as exc:
        rewrite.start_rewrite(p["id"], db_path=db)
    assert "无可合并碎文件" in exc.value.message


def test_ledger_row_records_credential_for_audit(db):
    settings.update_setting("compact_threshold_kb", "300", db_path=db)
    state = gate.gate_state(db)
    out = append_ledger(1, "阈值旧闸", db_path=db)
    assert out["credential"] == state["credential"]
    assert out["threshold_kb"] == 300


def test_half_fix_cannot_paint_fake_green(db):
    with get_db(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('runtime_threshold_kb','128')"
        )
    r = reconcile_surface(db)
    assert r["half_fixed"] is False
    assert r["panel_chip"] == gate.BAD_CHIP
    dash = dashboard(db)
    assert dash["fake_green"] is False
    assert dash["chip"] == gate.BAD_CHIP
