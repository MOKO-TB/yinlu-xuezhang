"""今日简报(minicode/briefing.py):四段聚合、诚实性、空数据不推、推送出口。

一切网络/时间都不真碰:课表/待办/校历都喂固定日期,天气只写假缓存,
weather.refresh_async 钉成空操作(简报绝不该当场等网络)。
"""
import re
from datetime import date, timedelta

import pytest

from minicode import briefing, paths, reminders, schedule, weather
from minicode import calendar as cal
from minicode import proactive

TODAY = date(2026, 9, 7)                      # 周一,开学第 1 周


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(weather, "refresh_async", lambda *a, **k: None)
    monkeypatch.setattr(weather, "_refreshing", False)
    # 校历默认不给数据,单个用例自己喂
    monkeypatch.setattr(cal, "load_events", lambda: ([], "test"))


def _save_schedule(classes):
    ok, msg = schedule.save("2026-09-07", classes)
    assert ok, msg


def _weather_payload(precip=0.0, temp=27.0, rain_pct=10):
    days = [(TODAY + timedelta(days=i)).isoformat() for i in range(4)]
    return {"current": {"temperature_2m": temp, "apparent_temperature": temp,
                        "precipitation": precip},
            "daily": {"time": days, "weather_code": [2, 0, 0, 0],
                      "temperature_2m_max": [31, 30, 30, 30],
                      "temperature_2m_min": [24, 23, 23, 23],
                      "precipitation_probability_max": [rain_pct, 20, 20, 20]}}


# ---------------------------------------------------------------- 空与最小

def test_nothing_registered_returns_none():
    assert briefing.build(TODAY) is None         # 不推空卡


def test_schedule_only_text_and_ask():
    _save_schedule([
        {"name": "高等数学", "day": 1, "start_time": "08:00",
         "periods": "1-2", "location": "思齐楼B203"},
        {"name": "大学英语", "day": 1, "start_time": "10:00", "periods": "3-4"},
    ])
    b = briefing.build(TODAY)
    assert b and b["date"] == "2026-09-07"
    assert b["text"].startswith("今日简报:")
    assert "周一" in b["text"] and "第 1 周" in b["text"]
    assert "今天 2 节课" in b["text"] and "08:00 高等数学" in b["text"]
    assert "思齐楼B203" in b["text"]
    assert "【今天日程】" in b["ask"] and "大学英语" in b["ask"]


def test_no_classes_today_says_so():
    _save_schedule([{"name": "高等数学", "day": 5, "start_time": "08:00"}])
    b = briefing.build(TODAY)                    # 周一没课,但课表已登记
    assert b and "今天没课" in b["text"]
    assert "08:00" not in b["text"]


def test_no_start_time_never_invented():
    _save_schedule([{"name": "体育课", "day": 1, "periods": "7-8",
                     "location": "田径场"}])
    b = briefing.build(TODAY)
    assert "第 7-8 节" in b["text"]              # 只报节次
    assert "08:00" not in b["text"] and ":00" not in b["text"]   # 绝不编钟点


# ------------------------------------------------ 走堂换楼这一段(v0.9.35)

def test_briefing_counts_only_real_building_changes():
    _save_schedule([
        {"name": "高等数学", "day": 1, "periods": "1-2", "location": "J1至善楼"},
        {"name": "大学英语", "day": 1, "periods": "3-4", "location": "S5致用楼"},
    ])
    b = briefing.build(TODAY)
    assert "1 处要换教学楼" in b["text"]
    assert "换楼:" in b["ask"] and "J1至善楼" in b["ask"] and "S5致用楼" in b["ask"]
    walk_lines = "\n".join(l for l in b["ask"].splitlines() if "换楼" in l)
    assert not re.search(r"\d+\s*分钟", walk_lines)   # 本机没填作息表:换楼行里不该有分钟数字


def test_briefing_phrase_names_the_earliest_change_so_you_leave_in_time():
    """出门前那一刻要的是一句能照着做的事,不是一个数字。"""
    _save_schedule([
        {"name": "高等数学", "day": 1, "periods": "1-2", "location": "J1至善楼"},
        {"name": "大学英语", "day": 1, "periods": "3-4", "location": "S5致用楼"},
        {"name": "数据结构", "day": 1, "periods": "5-6", "location": "J2明德楼"},
    ])
    b = briefing.build(TODAY)
    assert "2 处要换教学楼" in b["text"]
    assert "最早第 3 节上课前:J1至善楼 → S5致用楼" in b["text"]
    # 没填作息表:简报那句里一个分钟数字都不许出现
    assert not re.search(r"\d+\s*分钟", b["text"])


def test_briefing_names_unplaceable_classes_instead_of_rounding_them_away():
    _save_schedule([
        {"name": "高等数学", "day": 1, "periods": "1-2", "location": "J1至善楼"},
        {"name": "体育课", "day": 1, "periods": "3-4", "location": "田径场"},
    ])
    b = briefing.build(TODAY)
    assert "要换教学楼" not in b["text"]            # 判不出楼就没得比,不硬凑一个数
    assert "体育课 的地点判不出是哪栋楼" in b["ask"]


# ---------------------------------------------------------------- 其余三段

def test_weather_section_from_cache_only():
    weather._save_entry("肇庆·鼎湖", _weather_payload())
    b = briefing.build(TODAY)
    assert b and "27℃" in b["text"] and "带伞" not in b["text"]
    assert "【天气】" in b["ask"]


def test_weather_umbrella_flagged():
    weather._save_entry("肇庆·鼎湖", _weather_payload(precip=0.5))
    assert "带伞" in briefing.build(TODAY)["text"]


def test_reminders_counts_nearest_first():
    reminders.add("交实验报告", due=(TODAY + timedelta(days=1)).isoformat())
    reminders.add("报名竞赛", due=(TODAY + timedelta(days=3)).isoformat())
    b = briefing.build(TODAY)
    assert b and "待办 2 件临期" in b["text"]
    assert "交实验报告 还有 1 天" in b["text"]
    assert "【临期待办】" in b["ask"]


def test_briefing_shows_the_clock_only_for_todays_deadline():
    """「今天 17:00 截止」比"今天截止"能提前把人推出门;但没记钟点就不许编。"""
    reminders.add("交奖学金材料", due=TODAY.isoformat(), at="17:00")
    reminders.add("明天的事", due=(TODAY + timedelta(days=1)).isoformat(), at="09:00")
    b = briefing.build(TODAY)
    assert "交奖学金材料 今天截止 17:00" in b["text"]
    assert "09:00" not in b["text"]                    # 短句只说最紧那条
    assert "明天的事(截止 还有 1 天 09:00)" in b["ask"]   # 明细带上用户记的钟点
    reminders.delete("r1")
    reminders.add("只说了今天", due=TODAY.isoformat())
    b = briefing.build(TODAY)
    assert "只说了今天(截止 今天截止)" in b["ask"]      # 没记钟点就不许跟一个时间


def test_calendar_nearest_event(monkeypatch):
    events = [{"name": "中秋节", "start": "2026-09-25", "kind": "holiday"},
              {"name": "四六级口试", "start": "2026-09-12", "kind": "exam"}]
    monkeypatch.setattr(cal, "load_events", lambda: (events, "test"))
    b = briefing.build(TODAY)
    assert b and "四六级口试还有 5 天" in b["text"]
    assert "中秋节" not in b["text"]             # 7 天外的不进简报


# ---------------------------------------------------------------- 聚合出口

def test_all_four_sections_present(monkeypatch):
    _save_schedule([{"name": "高等数学", "day": 1, "start_time": "08:00"}])
    weather._save_entry("肇庆·鼎湖", _weather_payload(precip=0.5))
    reminders.add("交材料", due=TODAY.isoformat())
    monkey_events = [{"name": "新生见面会", "start": "2026-09-07", "kind": "other"}]
    monkeypatch.setattr(cal, "load_events", lambda: (monkey_events, "test"))
    b = briefing.build(TODAY)
    for needle in ("周一", "1 节课", "27℃", "带伞", "待办 1 件临期",
                   "新生见面会就是今天"):
        assert needle in b["text"], needle


class FakeUI:
    def __init__(self):
        self.events = []

    def push(self, event, **payload):        # 与 GuiUI.push 同签名
        self.events.append((event, payload))


class FakeBridge:
    def __init__(self):
        self.ui = FakeUI()


def test_push_briefing_success_and_payload():
    # push_briefing 内部走真实今天,课程要登记在"今天"这个星期几
    _save_schedule([{"name": "高等数学", "day": date.today().weekday() + 1,
                     "start_time": "08:00"}])
    bridge = FakeBridge()
    assert proactive.push_briefing(bridge) is True
    event, payload = bridge.ui.events[0]
    assert event == "proactive" and payload["kind"] == "briefing"
    assert payload["text"].startswith("今日简报:")
    assert "高等数学" in payload["ask"]


def test_push_briefing_empty_and_broken_return_false():
    bridge = FakeBridge()
    assert proactive.push_briefing(bridge) is False     # 没数据:不推
    assert bridge.ui.events == []

    def boom(today=None):
        raise RuntimeError("聚合炸了")

    orig = briefing.build
    briefing.build = boom
    try:
        assert proactive.push_briefing(bridge) is False  # 炸了也不许冒出去
    finally:
        briefing.build = orig
