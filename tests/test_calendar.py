"""校历模块测试:加载 / 倒计时 / 待定条目的诚实口径 / 工具处理器。"""
import json
from datetime import date

import pytest

from minicode import calendar as cal


@pytest.fixture(autouse=True)
def _calendar_file(tmp_path, monkeypatch):
    """把校历文件钉到临时目录;需要真实种子文件的用例用 real_seed 标记跳过。"""
    path = tmp_path / "school-calendar.json"
    monkeypatch.setattr(cal, "_candidate_paths", lambda: [path])
    cal._seed_file = path
    return path


def _write(events, notes="n"):
    cal._seed_file.write_text(
        json.dumps({"notes": notes, "events": events}, ensure_ascii=False),
        encoding="utf-8")


TODAY = date(2026, 9, 19)


def test_seed_file_in_repo_is_wellformed():
    """仓库里的真实种子文件:条目齐全、日期可解析、都有来源(不编日期的底线)。"""
    from minicode.paths import resource_path
    data = json.loads(
        resource_path("kb/school-calendar.json").read_text(encoding="utf-8"))
    events = data["events"]
    assert len(events) >= 4
    for e in events:
        assert e.get("name") and e.get("source") and e.get("credibility")
        assert e.get("kind") in cal.KIND_LABELS
        assert cal._parse_date(e.get("start")) is not None


def test_upcoming_sorted_with_days_left():
    _write([
        {"name": "四六级笔试", "start": "2026-12-12", "kind": "exam", "credibility": "A"},
        {"name": "中秋节", "start": "2026-09-25", "end": "2026-09-27",
         "kind": "holiday", "credibility": "A"},
        {"name": "国庆节", "start": "2026-10-01", "end": "2026-10-07",
         "kind": "holiday", "credibility": "A"},
    ])
    rows = cal.upcoming(cal.load_events()[0], TODAY)
    assert [e["name"] for e in rows] == ["中秋节", "国庆节", "四六级笔试"]
    assert rows[0]["days_left"] == 6 and rows[1]["days_left"] == 12


def test_upcoming_excludes_past_and_far():
    _write([
        {"name": "报到", "start": "2026-09-05", "kind": "event"},
        {"name": "中秋节", "start": "2026-09-25", "kind": "holiday"},
        {"name": "很久以后", "start": "2027-06-01", "kind": "event"},
    ])
    rows = cal.upcoming(cal.load_events()[0], TODAY)
    assert [e["name"] for e in rows] == ["中秋节"]


def test_current_and_pending():
    _write([
        {"name": "国庆假期", "start": "2026-09-15", "end": "2026-09-21", "kind": "holiday"},
        {"name": "期末考试周", "kind": "exam"},          # 没日期:待定
    ])
    events, _ = cal.load_events()
    assert [e["name"] for e in cal.current(events, TODAY)] == ["国庆假期"]
    assert [e["name"] for e in cal.pending(events)] == ["期末考试周"]
    assert cal.upcoming(events, TODAY) == []             # 无日期不进倒计时


def test_format_honesty_notes():
    _write([{"name": "期末考试周", "kind": "exam"}])
    text = cal.format_for_model(cal.load_events()[0], TODAY)
    assert "期末考试周" in text and "不要推测" in text
    assert "不要编日期" in cal.format_for_model([], TODAY)


def test_tool_query_hit_and_miss(tmp_path):
    _write([{"name": "国庆节", "start": "2026-10-01", "end": "2026-10-07",
             "kind": "holiday", "credibility": "A", "source": "国务院办公厅"}])
    out = cal.campus_calendar(None, action="query", query="国庆", today=TODAY)
    assert "国庆节" in out and "还有 12 天" in out and "国务院办公厅" in out
    miss = cal.campus_calendar(None, action="query", query="校运会", today=TODAY)
    assert "以教务处通知为准" in miss and "不要编" in miss
    empty = cal.campus_calendar(None, action="query", query="")
    assert empty.startswith("错误:")


def test_tool_upcoming_lists_and_honesty():
    _write([{"name": "中秋节", "start": "2026-09-25", "end": "2026-09-27",
             "kind": "holiday", "credibility": "A", "source": "国务院办公厅"}])
    out = cal.campus_calendar(None, today=TODAY)
    assert "中秋节" in out and "正在进行中" not in out
    # 今天落在区间内 → 标"进行中"
    out2 = cal.campus_calendar(None, action="query", query="中秋",
                               today=date(2026, 9, 26))
    assert "中秋节" in out2 and "进行中" in out2


def test_payload_for_home_chips():
    _write([
        {"name": "中秋节", "start": "2026-09-25", "kind": "holiday"},
        {"name": "国庆节", "start": "2026-10-01", "kind": "holiday"},
        {"name": "口试", "start": "2026-11-21", "kind": "exam"},
        {"name": "笔试", "start": "2026-12-12", "kind": "exam"},
    ])
    payload = cal.calendar_payload(limit=3, today=TODAY)
    assert [c["name"] for c in payload] == ["中秋节", "国庆节", "口试"]
    assert payload[0]["days_left"] == 6
