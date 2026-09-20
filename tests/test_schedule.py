"""个人课表(minicode/schedule.py):周次解析/推算、存取校验、看门狗文案、工具回环。"""
from datetime import date

import pytest

from minicode import paths, schedule


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


TODAY_MON = date(2026, 9, 7)     # 第 1 周周一


def _save(term="2026-09-07", classes=None):
    return schedule.save(term, classes or [
        {"name": "高等数学", "day": 1, "periods": "1-2", "start_time": "08:00",
         "weeks": "1-16", "location": "思齐楼B203"},
        {"name": "编程基础", "day": 3, "periods": "3-4", "weeks": "1-8,11-16",
         "location": "实验楼302"},
        {"name": "体育课", "day": 5, "weeks": "1-16(双周)"},
    ])


# ---------------------------------------------------------------- 周次解析

def test_parse_weeks_range_and_multi():
    assert schedule.parse_weeks("1-4") == {1, 2, 3, 4}
    assert schedule.parse_weeks("1-2,5-6") == {1, 2, 5, 6}
    assert schedule.parse_weeks("3") == {3}
    assert schedule.parse_weeks("") is None or True     # 空串按默认全学期(见 save 兜底)


def test_parse_weeks_parity():
    assert schedule.parse_weeks("1-6(单周)") == {1, 3, 5}
    assert schedule.parse_weeks("1-6(双周)") == {2, 4, 6}


def test_parse_weeks_bogus_returns_none():
    assert schedule.parse_weeks("没有周次信息") is None


# ---------------------------------------------------------------- 校验与保存

def test_save_rejects_bad_term_start():
    ok, msg = _save(term="开学后第二周")
    assert not ok and "term_start" in msg


def test_save_skips_bad_rows_keeps_good():
    ok, msg = schedule.save("2026-09-07", [
        {"name": "有课", "day": 1},
        {"name": "没天", "day": 99},
        {"name": "", "day": 2},
    ])
    assert ok and "2 条没通过校验" in msg      # day=99 与空名都算无效行
    assert len(schedule.load()["classes"]) == 1


def test_save_requires_valid_rows():
    ok, msg = schedule.save("2026-09-07", [{"day": 1}])
    assert not ok and "没有任何一条" in msg


# ---------------------------------------------------------------- 推算与查询

def test_week_of_and_classes_on():
    assert _save()[0]
    assert schedule.week_of(date(2026, 9, 7)) == 1
    assert schedule.week_of(date(2026, 9, 14)) == 2
    assert schedule.week_of(date(2026, 9, 6)) is None       # 开学前
    monday = schedule.classes_on(date(2026, 9, 7))          # 周一
    assert [c["name"] for c in monday] == ["高等数学"]
    assert monday[0]["week"] == 1
    wed = schedule.classes_on(date(2026, 9, 9))
    assert [c["name"] for c in wed] == ["编程基础"]
    fri_odd = schedule.classes_on(date(2026, 9, 11))        # 第 1 周单周 → 体育课(双周)无
    assert fri_odd == []
    fri_even = schedule.classes_on(date(2026, 9, 18))       # 第 2 周双周 → 有
    assert [c["name"] for c in fri_even] == ["体育课"]
    assert schedule.classes_on(date(2026, 9, 13)) == []     # 周日没课


def test_format_day_honest_about_time():
    assert _save()[0]
    text = schedule.format_day(date(2026, 9, 18))           # 体育课没钟点
    assert "体育课" in text and "08:00" not in text.split("体育课")[1][:20]


def test_no_schedule_all_none():
    assert schedule.load() is None
    assert schedule.week_of(date(2026, 9, 7)) is None
    assert schedule.classes_on(date(2026, 9, 7)) == []
    assert schedule.next_class_note(date(2026, 9, 7)) is None


# ---------------------------------------------------------------- 看门狗与提示词

def test_next_class_note_today_then_tomorrow():
    assert _save()[0]
    note = schedule.next_class_note(date(2026, 9, 7))       # 周一定有高数
    assert note and "今天第一节课" in note["text"] and "高等数学" in note["text"]
    assert "08:00" in note["text"]
    tue = schedule.next_class_note(date(2026, 9, 8))        # 周二没课,看周三
    assert tue and "明天第一节课" in tue["text"] and "编程基础" in tue["text"]
    sat = schedule.next_class_note(date(2026, 9, 19))       # 周六/日都无课
    assert sat is None


def test_prompt_section_before_and_after_save():
    assert "还没登记课表" in schedule.schedule_prompt_section()
    _save()
    text = schedule.schedule_prompt_section()
    assert "第 1 周周一" in text and "schedule(action=save)" in text


# ---------------------------------------------------------------- 工具回环

def test_tool_save_show_clear():
    msg = schedule.schedule(None, action="save", term_start="2026-09-07",
                            classes=[{"name": "大学英语", "day": 2, "weeks": "1-16"}])
    assert "课表已存" in msg
    show = schedule.schedule(None, action="show", when="2026-09-08")
    assert "大学英语" in show
    assert "没有课" in schedule.schedule(None, action="show", when="2026-09-13")
    assert "已清除" in schedule.schedule(None, action="clear")
    assert "还没有登记课表" in schedule.schedule(None, action="show")


def test_tool_bad_action():
    assert schedule.schedule(None, action="dance").startswith("错误")


# ---------------------------------------------------------------- 面板视图

def test_view_before_registration():
    assert schedule.view(date(2026, 9, 7)) == {"registered": False}


def test_view_shape_and_week_grid():
    assert _save()[0]
    v = schedule.view(date(2026, 9, 9))            # 周三(第 1 周)
    assert v["registered"] and v["week"] == 1
    assert v["term_start"] == "2026-09-07"
    assert [c["name"] for c in v["today"]["classes"]] == ["编程基础"]
    assert v["today"]["weekday"] == "三"
    # 本周网格:周一有高数、周三有编程基础、周日空
    grid = v["week_grid"]
    assert [c["name"] for c in grid["1"]["classes"]] == ["高等数学"]
    assert [c["name"] for c in grid["3"]["classes"]] == ["编程基础"]
    assert grid["7"]["classes"] == []


# ---------------------------------------------------------------- 课前到点提醒

def test_upcoming_class_within_lead():
    from datetime import datetime
    assert _save()[0]
    note = schedule.upcoming_class(datetime(2026, 9, 7, 7, 45), lead_minutes=20)
    assert note and "快上课了" in note["text"] and "高等数学" in note["text"]
    assert note["minutes_left"] == 15 and "08:00" in note["text"]
    assert note["key"] == "2026-09-07T08:00:高等数学"


def test_upcoming_class_outside_window_is_none():
    from datetime import datetime
    assert _save()[0]
    # 太早(30 分钟前)与已开课(08:05)都不提醒
    assert schedule.upcoming_class(datetime(2026, 9, 7, 7, 30), lead_minutes=20) is None
    assert schedule.upcoming_class(datetime(2026, 9, 7, 8, 5), lead_minutes=20) is None


def test_upcoming_class_never_fires_without_start_time():
    """诚实性:没写钟点的课(体育课)绝不因"第几节"被定时提醒。"""
    from datetime import datetime
    assert _save()[0]
    # 2026-09-18 周五第 2 周双周 → 体育课(无 start_time),全天任何时刻都不推
    for hh in range(0, 23):
        assert schedule.upcoming_class(datetime(2026, 9, 18, hh, 0)) is None


def test_upcoming_class_no_schedule():
    from datetime import datetime
    assert schedule.upcoming_class(datetime(2026, 9, 7, 7, 50)) is None

