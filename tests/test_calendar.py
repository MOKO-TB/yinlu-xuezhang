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
    """仓库里的真实种子文件:条目齐全、日期可解析、都有来源(不编日期的底线)。

    日期只允许一种例外:**每年例行**且明确还待核实的事(如新生心理测评 —— 学校
    还没发今年的通知)。这种条目走 pending 口径:工具只报名字并说"以通知为准"。
    除此之外每一条都必须有可解析的日期。
    """
    from minicode.paths import resource_path
    data = json.loads(
        resource_path("kb/school-calendar.json").read_text(encoding="utf-8"))
    events = data["events"]
    assert len(events) >= 4
    for e in events:
        assert e.get("name") and e.get("source") and e.get("credibility")
        assert e.get("kind") in cal.KIND_LABELS
        if cal._parse_date(e.get("start")) is not None:
            continue
        assert str(e.get("repeat") or "").lower() == "yearly", e["name"]
        assert str(e.get("credibility") or "").startswith("B"), e["name"]
        assert "还没录入" in str(e.get("note") or ""), e["name"]


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


def test_shift_year_keeps_feb29_legal():
    assert cal._shift_year(date(2024, 2, 29), 2026) == date(2026, 2, 28)
    assert cal._shift_year(date(2026, 5, 25), 2027) == date(2027, 5, 25)


def test_expand_repeats_only_touches_yearly_events():
    """一次性事件绝不能被"投影"到今年 —— 那等于替学校编了一个日期。"""
    ev = [{"name": "5·25 活动月", "start": "2024-05-25", "kind": "event",
           "repeat": "yearly"},
          {"name": "校庆", "start": "2024-11-08", "kind": "event"}]
    out = cal.expand_repeats(ev, TODAY)
    names = [e["name"] for e in out]
    assert names.count("校庆") == 1
    assert "2026-11-08" not in [e["start"] for e in out if e["name"] == "校庆"]
    yearly = [e for e in out if e["name"] == "5·25 活动月"]
    assert [e["start"] for e in yearly] == ["2025-05-25", "2026-05-25", "2027-05-25"]
    assert all(e["projected"] and e["anchor"] == "2024-05-25" for e in yearly)


def test_expand_repeats_keeps_span():
    ev = [{"name": "校运会", "start": "2025-11-20", "end": "2025-11-22",
           "kind": "event", "repeat": "yearly"}]
    row = next(e for e in cal.expand_repeats(ev, date(2026, 9, 19))
               if e["start"] == "2026-11-20")
    assert row["end"] == "2026-11-22"


def test_yearly_output_labels_the_projection():
    """给模型看的那段必须自带口径:这是推出来的,不是今年通知过的。"""
    _write([{"name": "5·25 活动月", "start": "2026-05-25", "kind": "event",
             "repeat": "yearly", "credibility": "A", "source": "学校公众号"}])
    out = cal.campus_calendar(None, today=date(2027, 3, 1))
    assert "2027-05-25" in out and "[每年重复]" in out
    assert "口径" in out and "2026-05-25" in out        # 锚点那次的日期也写出来


def test_yearly_window_widens_only_for_the_listing():
    """列表可以放宽(每年例行的事该常驻);倒计时那种窄窗口绝不放宽。"""
    ev = [{"name": "5·25 活动月", "start": "2026-05-25",
           "kind": "event", "repeat": "yearly"},
          {"name": "补考报名", "start": "2027-06-01", "kind": "deadline"}]
    today = date(2026, 9, 19)
    assert cal.UPCOMING_DAYS < 248 <= cal.YEARLY_LOOKAHEAD
    listed = cal.upcoming(ev, today, limit=8, yearly_within=cal.YEARLY_LOOKAHEAD)
    got = {e["name"] for e in listed}
    assert "5·25 活动月" in got          # 248 天后:每年例行的事不该因此消失
    assert "补考报名" not in got         # 255 天后的一次性事件仍按 120 天窗口
    # 7 天倒计时:推出来的日子**不配**进去(那不是学校通知过的日子)
    assert cal.upcoming(ev, today, limit=3, within_days=7) == []


def test_yearly_without_a_date_goes_to_pending():
    """心理普查这类"每年都有、今年日期还没发"的条目:只报名字,绝不投影出日期。"""
    ev = [{"name": "新生心理测评", "start": "", "kind": "deadline",
           "repeat": "yearly"}]
    assert cal.upcoming(ev, TODAY) == []
    assert [e["name"] for e in cal.pending(ev)] == ["新生心理测评"]


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
