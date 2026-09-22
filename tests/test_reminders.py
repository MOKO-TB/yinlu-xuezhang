"""截止日期提醒(reminder 工具 + 看门狗临期推送)测试。"""
import json
from datetime import date, datetime, timedelta

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


# ---------------------------------------------------------------- 钟点(v0.9.29)

def test_parse_clock_reads_confident_writings():
    assert reminders.parse_clock("14:00") == "14:00"
    assert reminders.parse_clock("14：00") == "14:00"        # 全角冒号(中文输入法)
    assert reminders.parse_clock("14点") == "14:00"
    assert reminders.parse_clock("下午3点") == "15:00"
    assert reminders.parse_clock("晚上8点40") == "20:40"
    assert reminders.parse_clock("中午12点半") == "12:30"
    assert reminders.parse_clock("上午9点") == "09:00"


def test_parse_clock_refers_ambiguous_to_day_level():
    """光「3点」没有上/下午就没有唯一答案 —— 猜错等于差 12 小时,比不催更糟。"""
    assert reminders.parse_clock("3点") == ""
    assert reminders.parse_clock("3点半") == ""
    assert reminders.parse_clock("下午13点") == ""           # 录错了,不猜成 13:00
    assert reminders.parse_clock("25:00") == ""
    assert reminders.parse_clock("周五") == ""


def test_parse_when_splits_date_and_clock():
    assert reminders.parse_when("2026-10-08 17:00") == ("2026-10-08", "17:00", "")
    assert reminders.parse_when("2026-10-08T17:00")[:2] == ("2026-10-08", "17:00")
    assert reminders.parse_when("10月8日下午5点")[:2] == (
        f"{date.today().year}-10-08", "17:00")
    assert reminders.parse_when("10月8日前交材料")[:1] == (
        (f"{date.today().year}-10-08",))       # 日期夹在句子里也要认出来
    # 只有钟点没有日期:钟点必须丢掉(哪天都不知道,定时无从谈起)
    assert reminders.parse_when("17:00") == ("", "", "17:00")
    # at 走第二条通道,且必须配日期才生效
    assert reminders.parse_when("2026-10-08", at="下午5点")[:2] == ("2026-10-08", "17:00")
    assert reminders.parse_when("", at="17:00")[:2] == ("", "")
    # 钟点认不准不能顺手把认得出的日期一起丢掉
    assert reminders.parse_when("2026-10-08 3点")[:2] == ("2026-10-08", "")


def test_add_stores_due_at_only_when_both_parts_are_confident():
    r = reminders.reminder(None, action="add", title="交奖学金材料",
                           due="2026-10-08", at="17:00")
    assert "2026-10-08 17:00" in r and "到点前" in r
    assert reminders.list_items()[0]["due_at"] == "2026-10-08T17:00"
    reminders.reminder(None, action="add", title="只说了下午交", due="2026-10-09")
    assert reminders.list_items()[1]["due_at"] == ""


def test_upcoming_due_fires_only_for_items_with_a_clock():
    now = datetime(2026, 10, 8, 16, 45)
    reminders.add("要交的材料", due="2026-10-08", at="17:00")
    reminders.add("只写了日期的事", due="2026-10-08")        # 绝不猜成 00:00/17:00
    note = reminders.upcoming_due(now, lead_minutes=20)
    assert note and "17:00" in note["text"] and "还有约 15 分钟" in note["text"]
    assert "要交的材料" in note["text"]
    assert reminders.upcoming_due(datetime(2026, 10, 8, 15, 0),
                                  lead_minutes=20) is None   # 还早,不打扰


def test_upcoming_due_gives_one_item_not_the_whole_list():
    reminders.add("先到期", due="2026-10-08", at="17:00")
    reminders.add("后到期", due="2026-10-08", at="17:05")
    note = reminders.upcoming_due(datetime(2026, 10, 8, 16, 50), lead_minutes=20)
    assert "先到期" in note["text"]                          # 只报最紧的那条
    assert "后到期" not in note["text"]                       # 同一轮不刷屏


def test_upcoming_and_late_share_the_key_so_no_double_pop():
    """到点催过的,开机补发不能再催一遍 —— 靠的是两边共用同一个去重键。"""
    reminders.add("补交材料", due="2026-10-08", at="17:00")
    due_note = reminders.upcoming_due(datetime(2026, 10, 8, 16, 50), lead_minutes=20)
    late_note = reminders.late_due(datetime(2026, 10, 8, 17, 10))
    assert due_note["key"] == late_note["key"] == "r1|2026-10-08T17:00"
    # 改了截止时间 = 一件事的新时刻,必须重新催(否则用户永远等不到那一条)
    items = reminders.load_items()
    items[0]["due_at"] = "2026-10-08T18:00"
    reminders.save_items(items)
    assert reminders.upcoming_due(datetime(2026, 10, 8, 17, 50))["key"] \
        == "r1|2026-10-08T18:00"


def test_late_due_window_and_finished_items():
    reminders.add("已过两小时的事", due="2026-10-08", at="15:00")
    assert reminders.late_due(datetime(2026, 10, 8, 17, 30)) is None  # 太久了不打扰
    reminders.add("刚过期", due="2026-10-08", at="17:00")
    assert reminders.late_due(datetime(2026, 10, 8, 17, 30))["name"] == "刚过期"
    reminders.finish("r2")                                   # 已完成的不该再冒出来
    assert reminders.late_due(datetime(2026, 10, 8, 17, 30)) is None


def test_list_marks_the_items_that_will_be_timed():
    """⏰ 标的是"会按钟点催"的那几条;相对日期写,免得换个日子检出就红。"""
    soon = (date.today() + timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=30)).isoformat()
    reminders.add("按钟点催", due=soon, at="17:00")
    reminders.add("按天催", due=later)
    out = reminders.reminder(None, action="list")
    assert "17:00" in out and "⏰" in out
    assert out.index("按钟点催") < out.index("按天催")      # 临期排前面
    assert "⏰" not in out.split("按天催")[1]                # 只有日期的不带钟点


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
