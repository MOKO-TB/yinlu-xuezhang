"""今日简报(minicode/briefing.py):四段聚合、诚实性、空数据不推、推送出口。

一切网络/时间都不真碰:课表/待办/校历都喂固定日期,天气只写假缓存,
weather.refresh_async 钉成空操作(简报绝不该当场等网络)。
"""
import time
from datetime import date, timedelta

import pytest

from minicode import briefing, paths, reminders, schedule, weather
from minicode import calendar as cal
from minicode import proactive

TODAY = date(2026, 9, 7)                      # 周一,开学第 1 周


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(weather, "refresh_async", lambda: None)
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


# ---------------------------------------------------------------- 其余三段

def test_weather_section_from_cache_only():
    weather._save_cache(_weather_payload())
    b = briefing.build(TODAY)
    assert b and "27℃" in b["text"] and "带伞" not in b["text"]
    assert "【天气】" in b["ask"]


def test_weather_umbrella_flagged():
    weather._save_cache(_weather_payload(precip=0.5))
    assert "带伞" in briefing.build(TODAY)["text"]


def test_reminders_counts_nearest_first():
    reminders.add("交实验报告", due=(TODAY + timedelta(days=1)).isoformat())
    reminders.add("报名竞赛", due=(TODAY + timedelta(days=3)).isoformat())
    b = briefing.build(TODAY)
    assert b and "待办 2 件临期" in b["text"]
    assert "交实验报告 还有 1 天" in b["text"]
    assert "【临期待办】" in b["ask"]


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
    weather._save_cache(_weather_payload(precip=0.5))
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
