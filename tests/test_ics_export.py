""".ics 兜底导出测试(v0.9.58 A5):格式合法、钟点诚实、投影不外发、UID 稳定。

钉四件事:①RFC 层(CRLF/75 字节折行/TEXT 转义/多字节不劈开)—— 导入端对坏行
最不宽容;②钟点只来自课表+作息表(`schedule.row_clock`),没填作息表降级全天
并如实报数,绝不编一个 45 分钟;③校历 `repeat:"yearly"` 的投影日子不导 ——
写进系统日历就成了每年照着过的事实;④UID 确定性:同一份日程重复导出不变,
日历软件按 UID 去重,反复导入不堆重。
"""
import json
from datetime import date, datetime

import pytest

from minicode import ics_export
from minicode import paths


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    # 校历的回退链是 app_path → resource_path(仓库里那份真 kb 文件):
    # 不按住的话,测试会吃进真实校历,条数与"空数据"断言全看仓库内容漂。
    from minicode import calendar as cal
    monkeypatch.setattr(cal, "load_events", lambda: ([], "test"))


TIMES = {"periods": {n: {"start": f"{7 + n:02d}:00", "end": f"{7 + n:02d}:45"}
                     for n in range(1, 13)},
         "total": 12, "source": "测试作息表"}


def _write_schedule(rows, term_start="2026-09-07"):
    """按 schedule.load() 认的形状写一份课表(第 1 周周一 = 2026-09-07)。"""
    path = paths.APP_DIR / "schedule.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"term_start": term_start, "classes": rows},
                               ensure_ascii=False), encoding="utf-8")


def _course(name="高等数学", day=1, periods="1-2", weeks="1-16", **kw):
    row = {"name": name, "day": day, "periods": periods, "weeks": weeks,
           "start_time": "", "location": "A203", "teacher": "张老师"}
    row.update(kw)
    return row


# ---------------------------------------------------------------- 文本层

def test_render_uses_crlf_and_folds_long_lines_by_bytes():
    long_title = "很长的课程名称" * 30          # 中文 3 字节/字,一准超 75 字节
    events = [{"kind": "calendar", "title": long_title,
               "start": date(2026, 10, 1), "end": None, "all_day": True,
               "rrule": None, "location": "", "description": "", "alarm": None}]
    text = ics_export.render(events)
    assert "\r\n" in text and text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")           # 没有裸 LF
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line[:40]
    folded = [l for l in text.split("\r\n") if l.startswith(" ")]
    assert folded
    unfolded = text.replace("\r\n ", "")                  # RFC 展开后续行后再找全文
    assert long_title in unfolded                         # 折行且内容完整


def test_render_escapes_text_and_marks_all_day_dtend_exclusive():
    events = [{"kind": "calendar", "title": "运动会, 田径;场",
               "start": date(2026, 10, 10), "end": date(2026, 10, 12),
               "all_day": True, "rrule": None, "location": "东区",
               "description": "来源:校历(A 级)", "alarm": None}]
    text = ics_export.render(events)
    assert "SUMMARY:运动会\\, 田径\\;场" in text
    assert "DTSTART;VALUE=DATE:20261010" in text
    assert "DTEND;VALUE=DATE:20261013" in text            # 排他:末日 +1
    assert "DTSTAMP:" in text and text.count("DTSTAMP") == 1


def test_uid_is_deterministic_and_changes_with_the_schedule():
    base = {"kind": "course", "title": "高数", "start": datetime(2026, 9, 7, 8, 0),
            "end": datetime(2026, 9, 7, 9, 40), "all_day": False,
            "rrule": "FREQ=WEEKLY;COUNT=16", "location": "", "description": "",
            "alarm": None}
    assert ics_export._uid(base) == ics_export._uid(dict(base))
    assert ics_export._uid(base) != ics_export._uid({**base, "rrule": None})


def test_week_runs_split_on_gaps():
    assert ics_export._week_runs({1, 2, 3, 5, 6}) == [(1, 3), (5, 6)]
    assert ics_export._week_runs({4}) == [(4, 4)]
    assert ics_export._week_runs(set()) == []


# ---------------------------------------------------------------- 课表层

def test_course_with_period_times_exports_real_clock(monkeypatch):
    from minicode import schedule
    monkeypatch.setattr(schedule, "load_period_times", lambda: TIMES)
    _write_schedule([_course(weeks="1-2")])               # 两周 → RRULE COUNT=2
    events, notes = ics_export._course_events(date(2026, 9, 10))
    assert len(events) == 1 and not notes
    e = events[0]
    assert e["start"] == datetime(2026, 9, 7, 8, 0)       # 第 1 周周一 第 1 节
    assert e["end"] == datetime(2026, 9, 7, 9, 45)        # 末节下课,不是编的 45 分钟
    assert e["rrule"] == "FREQ=WEEKLY;COUNT=2"
    assert e["alarm"] == "-PT30M" and e["location"] == "A203"


def test_course_without_period_times_degrades_to_all_day_and_says_so(monkeypatch):
    from minicode import schedule
    monkeypatch.setattr(schedule, "load_period_times",
                        lambda: {"periods": {}, "total": 0, "source": ""})
    _write_schedule([_course(name="大学英语")])
    events, notes = ics_export._course_events(date(2026, 9, 10))
    assert len(events) == 1 and events[0]["all_day"] is True
    assert events[0]["start"] == date(2026, 9, 7)
    assert any("大学英语" in n and "作息表" in n for n in notes)   # 点名 + 怎么补


def test_course_week_gaps_split_into_two_rules(monkeypatch):
    from minicode import schedule
    monkeypatch.setattr(schedule, "load_period_times", lambda: TIMES)
    _write_schedule([_course(weeks="1-2,4-5")])
    events, _notes = ics_export._course_events(date(2026, 9, 10))
    assert [e["rrule"] for e in events] == ["FREQ=WEEKLY;COUNT=2", "FREQ=WEEKLY;COUNT=2"]
    assert events[1]["start"] == datetime(2026, 9, 28, 8, 0)      # 第 4 周周一,不假装第 3 周连上


def test_course_without_a_schedule_says_so(monkeypatch):
    from minicode import schedule
    monkeypatch.setattr(schedule, "load_period_times", lambda: TIMES)
    events, notes = ics_export._course_events(date(2026, 9, 10))
    assert events == [] and notes and "课表还没录入" in notes[0]


# ---------------------------------------------------------------- 待办层

def test_reminders_export_with_clock_repeat_and_honest_skips():
    from minicode import reminders
    reminders.add("交周记", due="2026-10-14", at="17:00", repeat="每周")
    reminders.add("只写了日期的事", due="2026-10-20")
    reminders.add("连日期都没有的事")
    events, notes = ics_export._reminder_events(date(2026, 10, 8))
    by_title = {e["title"]: e for e in events}
    weekly = by_title["截止:交周记"]
    assert weekly["start"] == datetime(2026, 10, 14, 17, 0)
    assert weekly["rrule"] == "FREQ=WEEKLY" and weekly["alarm"] == "PT0S"
    day_only = by_title["截止:只写了日期的事"]
    assert day_only["all_day"] is True and day_only["start"] == date(2026, 10, 20)
    assert "连日期都没有的事" not in by_title
    assert any("1 条待办" in n for n in notes)


def test_reminder_every_n_days_becomes_daily_interval_rule():
    from minicode import reminders
    reminders.add("背单词", due="2026-10-14", at="08:00", repeat="每3天")
    events, _notes = ics_export._reminder_events(date(2026, 10, 8))
    assert events[0]["rrule"] == "FREQ=DAILY;INTERVAL=3"


# ---------------------------------------------------------------- 校历层

def test_calendar_events_skip_projected_and_keep_sources(monkeypatch):
    from minicode import calendar as cal
    rows = [{"name": "校运会", "start": "2026-11-06", "end": "2026-11-07",
             "kind": "event", "note": "东区田径场", "credibility": "A",
             "source": "教务处通知"},
            {"name": "12·25 心理普查", "start": "2025-12-25", "kind": "event",
             "repeat": "yearly", "credibility": "A", "source": "校历"}]
    monkeypatch.setattr(cal, "load_events", lambda: (rows, "test"))
    events, notes = ics_export._calendar_events(date(2026, 10, 8))
    assert [e["title"] for e in events] == ["校运会"]
    assert events[0]["end"] == date(2026, 11, 7)
    assert "教务处通知" in events[0]["description"] and "A" in events[0]["description"]
    assert any("1 条" in n and "投影" in n for n in notes)        # 投影没导,说明原因
    assert not any("心理普查" in e["title"] for e in events)       # 投影的 2026-12-25 在窗内也不导


# ---------------------------------------------------------------- 出口

def test_export_writes_the_file_and_reports_counts(tmp_path, monkeypatch):
    from minicode import schedule
    monkeypatch.setattr(schedule, "load_period_times", lambda: TIMES)
    _write_schedule([_course(weeks="1-2")])
    result = ics_export.export(tmp_path, today=date(2026, 9, 10))
    assert result["ok"] is True and result["events"] >= 1
    assert result["counts"]["course"] == 1
    text = (tmp_path / ics_export.ICS_FILE_NAME).read_text(encoding="utf-8")
    assert text.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in text


def test_tool_handler_speaks_human(tmp_path):
    from minicode.ics_export import export_ics_tool

    class Ctx:
        workspace = tmp_path

    out = export_ics_tool(Ctx())
    assert "已导出" in out and "导入方法" in out
    assert "没有任何日程" in out                            # 空数据不冒充有内容
