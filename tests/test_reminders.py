"""截止日期提醒(reminder 工具 + 看门狗临期推送)测试。"""
import json
from datetime import date

import pytest

from minicode import reminders


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    yield


def test_add_list_finish_delete():
    out = reminders.reminder(None, action="add", title="交奖学金材料",
                             due="2026-10-08", note="交到学工办")
    assert "已记下" in out and "r1" in out and "2026-10-08" in out
    items = reminders.list_items()
    assert len(items) == 1 and items[0]["due"] == "2026-10-08"
    out = reminders.reminder(None, action="finish", item_id="r1")
    assert "已完成" in out
    assert reminders.list_items() == []
    assert len(reminders.list_items(include_done=True)) == 1
    out = reminders.reminder(None, action="delete", item_id="r1")
    assert "已删除" in out
    assert reminders.reminder(None, action="delete", item_id="r9").startswith("错误:")


def test_add_requires_title_and_clamps():
    assert reminders.reminder(None, action="add", title="  ").startswith("错误:")
    reminders.reminder(None, action="add", title="无日期事项")
    items = reminders.list_items()
    assert items[0]["due"] == "" and items[0]["due_raw"] == ""


def test_parse_due_formats():
    assert reminders.parse_due("2026-10-08")[0] == "2026-10-08"
    assert reminders.parse_due("2026/10/8")[0] == "2026-10-08"
    assert reminders.parse_due("2026年10月8日")[0] == "2026-10-08"
    assert reminders.parse_due("10-08")[0].endswith("-10-08")   # 补当前年
    assert reminders.parse_due("周五")[0] == ""                  # 解析不了不猜
    assert reminders.parse_due("")[0] == ""
    assert reminders.parse_due("2026-13-40")[0] == ""            # 非法日期不猜


def test_due_soon_orders_and_filters():
    today = date(2026, 9, 20)
    items = [
        {"id": "r1", "title": "早已逾期", "due": "2026-09-10", "done": False},
        {"id": "r2", "title": "明天截止", "due": "2026-09-21", "done": False},
        {"id": "r3", "title": "还早", "due": "2026-12-01", "done": False},
        {"id": "r4", "title": "无日期", "due": "", "done": False},
        {"id": "r5", "title": "已完成", "due": "2026-09-18", "done": True},
    ]
    near = reminders.due_soon(items, today=today)
    assert [i["id"] for i in near] == ["r1", "r2"]      # 逾期排最前,远期/无日期不进
    assert near[0]["days_left"] == -10


def test_list_shows_urgent_first():
    reminders.reminder(None, action="add", title="逾期事项", due="2026-09-01")
    reminders.reminder(None, action="add", title="远期事项", due="2026-12-01")
    out = reminders.reminder(None, action="list")
    assert "临期/逾期" in out
    assert out.index("逾期事项") < out.index("远期事项")


def test_rejects_unknown_action():
    assert reminders.reminder(None, action="fabulous").startswith("错误:")


def test_state_survives_reload(tmp_path):
    reminders.add("跨重启的提醒", due="2026-10-01")
    items = reminders.load_items()
    assert items[0]["title"] == "跨重启的提醒"
    data = json.loads(reminders.state_path().read_text(encoding="utf-8"))
    assert data["next_id"] == 2
