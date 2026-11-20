from app.services import files, plans, settings


def test_seed_has_files_and_settings(db):
    items = files.list_files(db_path=db)
    assert len(items) >= 8
    assert any(x["status"] == "已入计划" for x in items)
    st = settings.list_settings(db_path=db)
    assert len(st) >= 3


def test_create_and_publish_plan(db):
    # dw_payments 的碎文件均在现行阈值(256KB)之上，可被挂入。
    p = plans.create_plan("CP-T1", "测试计划", "dw_payments", db_path=db)
    attached = plans.attach_loose_files(p["id"], db_path=db)
    assert attached["attached"] >= 1
    assert attached["gate"]["aligned"] is True
    pub = plans.publish_plan(p["id"], db_path=db)
    assert pub["status"] == "已发布"
