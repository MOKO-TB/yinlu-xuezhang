"""个人课表(minicode/schedule.py):周次解析/推算、存取校验、看门狗文案、工具回环。"""
import json
import re
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
    assert schedule.parse_weeks(schedule.DEFAULT_WEEKS) == set(range(1, 26))
    # 空串按默认全学期(见 save 兜底)——当年这条被 or True 掩成恒真,
    # 实际行为是"空串 → DEFAULT_WEEKS 全集",不是注释里写的 None
    assert schedule.parse_weeks("") == set(range(1, 26))


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
    assert schedule.view(date(2026, 9, 7)) == {"registered": False, "can_undo": False}


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


# ------------------------------------------------ 补发"错过的课"(v0.9.26)

def test_late_class_note_fires_for_a_class_that_already_started():
    from datetime import datetime
    assert _save()[0]
    note = schedule.late_class_note(datetime(2026, 9, 7, 8, 50))
    assert note and "错过的提醒" in note["text"] and "高等数学" in note["text"]
    assert note["minutes_since"] == 50 and note["title"] == "错过的提醒"
    assert "思齐楼B203" in note["text"]
    # 与到点提醒**共用同一个键**:先弹了"快上课了"就不会再弹这条,反之也一样
    assert note["key"] == "2026-09-07T08:00:高等数学"
    assert note["key"] == schedule.upcoming_class(
        datetime(2026, 9, 7, 7, 50))["key"]


def test_late_class_note_windows_and_edges():
    from datetime import datetime
    assert _save()[0]
    assert schedule.late_class_note(datetime(2026, 9, 7, 7, 59)) is None   # 还没开课
    assert schedule.late_class_note(datetime(2026, 9, 7, 8, 0)) is None    # 正好开课=0 分钟
    assert schedule.late_class_note(datetime(2026, 9, 7, 8, 50))["minutes_since"] == 50
    assert schedule.late_class_note(datetime(2026, 9, 7, 9, 0))["minutes_since"] == 60
    assert schedule.late_class_note(datetime(2026, 9, 7, 9, 1)) is None    # 刚过窗口
    # 窗口收窄 = 只补"刚刚错过"的,不把一小时前的旧事再念一遍
    assert schedule.late_class_note(datetime(2026, 9, 7, 8, 50),
                                    within_minutes=30) is None


def test_late_class_note_only_gives_the_nearest_missed_class():
    """一早上两节都没赶上,也只补最近那一节 —— 补发不是刷屏的许可证。"""
    from datetime import datetime
    assert schedule.save("2026-09-07", [
        {"name": "高等数学", "day": 1, "periods": "1-2", "start_time": "08:00",
         "weeks": "1-16", "location": "思齐楼B203"},
        {"name": "大学英语", "day": 1, "periods": "3-4", "start_time": "08:50",
         "weeks": "1-16", "location": "外院101"},
    ])[0]
    note = schedule.late_class_note(datetime(2026, 9, 7, 9, 10))
    assert note["name"] == "大学英语" and note["minutes_since"] == 20


def test_late_class_note_never_invents_what_the_schedule_lacks():
    from datetime import datetime
    # 没写钟点的课(体育课)不参与补发
    assert _save()[0]
    for hh in range(0, 23):
        assert schedule.late_class_note(datetime(2026, 9, 18, hh, 30)) is None
    # 没写教室 → 正文里不出现"教室 …",但课名/钟点是真的
    assert schedule.save("2026-09-07", [
        {"name": "高等数学", "day": 1, "periods": "1-2", "start_time": "08:00",
         "weeks": "1-16"},
    ])[0]
    note = schedule.late_class_note(datetime(2026, 9, 7, 8, 20))
    assert note and "教室" not in note["text"] and "08:00" in note["text"]
    # 没课表 = 整条不发
    schedule.clear()
    assert schedule.late_class_note(datetime(2026, 9, 7, 8, 20)) is None


def test_late_class_note_never_claims_class_is_still_running():
    """课表里没有结束钟点,所以文案只能说"已开课约 N 分钟",不能说"还在上"。"""
    from datetime import datetime
    assert _save()[0]
    note = schedule.late_class_note(datetime(2026, 9, 7, 8, 20))
    assert "还在上" not in note["text"] and "未结束" not in note["text"]
    assert "过了约 20 分钟" in note["text"]



# ---------------------------------------------------------------- 变动比对(v0.9.24)

def _hi(**kw):
    row = {"name": "高等数学", "day": 1, "periods": "1-2", "start_time": "08:00",
           "weeks": "1-16", "location": "思齐楼B203", "teacher": "王老师的课"}
    row.update(kw)
    return row


def test_compare_first_import_reports_no_changes():
    res = schedule.compare([_hi()])               # 本机什么都没有
    assert res["ok"] and res["has_existing"] is False
    assert res["counts"]["added"] == 1 and res["counts"]["changed"] == 0
    assert "首次导入" in res["msg"]


def test_compare_identical_schedule_is_all_unchanged():
    assert _save(classes=[_hi()])[0]
    res = schedule.compare([_hi()])
    assert res["has_existing"] and res["counts"]["unchanged"] == 1
    assert not res["counts"]["added"] and not res["counts"]["removed"]
    assert "完全一致" in res["msg"]


def test_compare_weeks_written_differently_is_not_a_change():
    """"1-16" 与 "1-8,9-16" 是同一串周次:写法不同不能报成"周次变动"。"""
    assert _save(classes=[_hi(weeks="1-16")])[0]
    res = schedule.compare([_hi(weeks="1-8,9-16")])
    assert res["counts"] == {**res["counts"], "changed": 0, "unchanged": 1}


def test_compare_period_and_time_normalisation():
    """节次 "1-2" vs "01-02"、钟点 "8:00" vs "08:00" 同一回事,不算变动。"""
    assert _save(classes=[_hi(periods="1-2", start_time="8:00")])[0]
    res = schedule.compare([_hi(periods="01-02", start_time="08:00")])
    assert res["counts"]["unchanged"] == 1 and res["counts"]["changed"] == 0


def test_compare_room_change_is_one_change_with_old_and_new():
    assert _save(classes=[_hi()])[0]
    res = schedule.compare([_hi(location="思齐楼A101")])
    assert res["counts"]["changed"] == 1
    assert res["counts"]["added"] == 0 and res["counts"]["removed"] == 0
    fields = res["changed"][0]["fields"]
    assert fields["location"] == {"old": "思齐楼B203", "new": "思齐楼A101"}


def test_compare_moved_class_pairs_instead_of_add_plus_remove():
    """调课(星期+节次都变了,地点没变):报 1 条变动,不是"取消一条又新增一条"。"""
    assert _save(classes=[_hi()])[0]
    res = schedule.compare([_hi(day=3, periods="3-4")])
    assert res["counts"]["changed"] == 1
    assert res["counts"]["added"] == 0 and res["counts"]["removed"] == 0
    assert set(res["changed"][0]["fields"]) == {"day", "periods"}


def test_compare_unrelated_course_is_added_not_a_rename():
    """两门课之间没有相似度可配:该分开报,不能硬凑成"变动"。"""
    assert _save(classes=[_hi(name="高等数学", location="", weeks="1-8")])[0]
    res = schedule.compare([_hi(name="线性代数", day=5, periods="7-8",
                                start_time="", weeks="1-8", location="")])
    assert res["counts"]["added"] == 1 and res["counts"]["removed"] == 1
    assert res["counts"]["changed"] == 0


def test_compare_per_slot_when_a_course_meets_twice():
    """同一门课一周两上:两个时段各自配对,别把第二次上课误判成"新增"。"""
    a, b = _hi(day=1, periods="1-2"), _hi(day=4, periods="1-2")
    assert _save(classes=[a, b])[0]
    res = schedule.compare([a, _hi(day=4, periods="3-4")])
    assert res["counts"]["unchanged"] == 1 and res["counts"]["changed"] == 1
    assert res["counts"]["added"] == 0 and res["counts"]["removed"] == 0


def test_compare_reports_term_start_change():
    assert _save(classes=[_hi()])[0]
    res = schedule.compare([_hi()], term_start="2026-09-14")
    assert res["term_start"] == {"old": "2026-09-07", "new": "2026-09-14",
                                 "changed": True}
    assert "2026-09-14" in res["msg"]


def test_compare_writes_nothing():
    """比对是"看",不是"存":调用前后本机文件必须一模一样。"""
    assert _save(classes=[_hi()])[0]
    before = schedule.state_path().read_text(encoding="utf-8")
    schedule.compare([_hi(location="别处")])
    assert schedule.state_path().read_text(encoding="utf-8") == before
    assert not schedule.prev_path().exists()


def test_describe_changes_lists_every_kind_once():
    assert _save(classes=[_hi(), _hi(name="体育课", day=5, periods="7-8",
                                     start_time="", location="")])[0]
    res = schedule.compare([_hi(location="A101"),
                            _hi(name="大学英语", day=2, periods="1-2",
                                start_time="", location="", teacher="李老师")])
    text = schedule.describe_changes(res)
    assert "变动" in text and "新增" in text and "取消" in text
    assert "体育课" in text and "大学英语" in text
    assert "地点 思齐楼B203→A101" in text


def test_save_reports_changes_and_keeps_backup():
    assert _save(classes=[_hi()])[0]
    ok, msg = schedule.save("2026-09-07", [_hi(location="思齐楼A101")])
    assert ok and "与上一版比对" in msg and "变动 1 条" in msg
    assert "思齐楼B203→思齐楼A101" in msg
    assert schedule.prev_path().exists()


def test_save_without_backup_stays_clean():
    ok, msg = schedule.save("2026-09-07", [_hi()])
    assert ok and "与上一版比对" not in msg
    assert not schedule.prev_path().exists()       # 首次导入没有"上一版"


def test_undo_swaps_back_and_is_repeatable():
    assert _save(classes=[_hi(location="B203")])[0]
    assert schedule.save("2026-09-07", [_hi(location="A101")])[0]
    assert [c["location"] for c in schedule.load()["classes"]] == ["A101"]
    ok, msg = schedule.undo()
    assert ok and [c["location"] for c in schedule.load()["classes"]] == ["B203"]
    assert "上一版" in msg
    # 再点一次换回刚才那版(对调,不销毁任何一版)
    assert schedule.undo()[0]
    assert [c["location"] for c in schedule.load()["classes"]] == ["A101"]


def test_undo_without_backup_is_honest():
    ok, msg = schedule.undo()
    assert not ok and "没有可回滚" in msg


def test_undo_refuses_corrupt_backup():
    assert _save(classes=[_hi()])[0]
    assert schedule.save("2026-09-07", [_hi(location="A101")])[0]
    schedule.prev_path().write_text("{坏文件", encoding="utf-8")
    ok, msg = schedule.undo()
    assert not ok and "读不懂" in msg
    assert [c["location"] for c in schedule.load()["classes"]] == ["A101"]   # 原样不动


def test_clear_keeps_backup_and_undo_restores_it():
    assert _save(classes=[_hi()])[0]
    assert "备份" in schedule.clear()
    assert schedule.load() is None
    assert schedule.view()["registered"] is False and schedule.view()["can_undo"] is True
    assert schedule.undo()[0]
    assert [c["name"] for c in schedule.load()["classes"]] == ["高等数学"]


def test_tool_diff_and_undo_actions():
    assert schedule.schedule(None, action="save", term_start="2026-09-07",
                             classes=[_hi()])
    out = schedule.schedule(None, action="diff", classes=[_hi(location="A101")])
    assert "比对结果(未写文件)" in out and "变动" in out
    assert schedule.load()["classes"][0]["location"] == "思齐楼B203"   # diff 不落盘
    assert "错误" in schedule.schedule(None, action="diff")            # 没带 classes
    assert schedule.schedule(None, action="save", term_start="2026-09-07",
                             classes=[_hi(location="A101")])
    assert "换回上一版" in schedule.schedule(None, action="undo")
    assert schedule.load()["classes"][0]["location"] == "思齐楼B203"


# ------------------------------------------------ 空闲时段 + 作息表(v0.9.27)

def _row(name="高等数学", day=1, periods="1-2", **kw):
    return {"name": name, "day": day, "periods": periods, "weeks": "1-16", **kw}


def _data(*rows, term="2026-09-07"):
    return {"term_start": term, "classes": [dict(r) for r in rows]}


def _times(*pairs):
    """作息表夹具:按 (起, 止) 依次排第 1..N 节(直接喂 free_slots,不落盘)。"""
    return {"periods": {i + 1: {"start": s, "end": e}
                        for i, (s, e) in enumerate(pairs)},
            "total": len(pairs), "source": "夹具"}


# ---------------------------------------------------------------- 节次解析

def test_parse_periods_accepts_schedule_spellings():
    assert schedule._parse_periods("1-2") == {1, 2}
    assert schedule._parse_periods("第 3 节") == {3}
    assert schedule._parse_periods("5-6节") == {5, 6}
    assert schedule._parse_periods("1,3,5") == {1, 3, 5}
    assert schedule._parse_periods([2, 3]) == {2, 3}


def test_parse_periods_rejects_rubbish_and_out_of_range():
    for bad in ("", None, "第一节", "0-2", "1-13", "abc"):
        assert schedule._parse_periods(bad) is None


# ---------------------------------------------------------------- 空档口径

def test_free_slots_finds_gap_between_classes_by_period():
    res = schedule.free_slots(TODAY_MON, _data(_row(periods="1-2"), _row("体育课", 1, "5-6")))
    assert res["in_term"] and not res["clock"]
    assert [(g["label"], g["periods"]) for g in res["gaps"]] == [("第 3-4 节", 2)]
    assert res["gaps"][0]["start"] == ""          # 没作息表就没有钟点,不编


def test_free_slots_without_clock_stays_out_of_the_business_of_times():
    """没填作息表时,回答里出现任何钟点都是编的 —— 连"8:00"这种形态都不许有。"""
    import re as _re
    res = schedule.free_slots(TODAY_MON, _data(_row(periods="1-2"), _row("体育课", 1, "5-6")))
    text = schedule.format_free_slots(res)
    assert not _re.search(r"\d{1,2}:\d{2}", text), text
    assert "第 3-4 节" in text and "只按节次" in text


def test_free_slots_does_not_claim_time_after_the_last_class():
    """没作息表时全天共几节没人知道,最后一节之后的部分不报(报了就是猜)。"""
    res = schedule.free_slots(TODAY_MON, _data(_row(periods="1-2")))
    assert res["gaps"] == [] and "没有空档" in res["note"]


def test_free_slots_with_times_spans_the_whole_day_and_adds_clock():
    times = _times(("08:00", "08:45"), ("08:55", "09:40"), ("10:00", "10:45"),
                   ("10:55", "11:40"), ("14:00", "14:45"))
    res = schedule.free_slots(TODAY_MON, _data(_row(periods="1-2")), times)
    gaps = res["gaps"]
    assert [g["label"] for g in gaps] == ["第 3-5 节"]        # 3/4/5 连着空就是一整段
    assert (gaps[0]["start"], gaps[0]["end"]) == ("10:00", "14:45")   # 跨节取首尾
    assert gaps[0]["clock"] and res["clock"]
    assert "10:00~14:45" in schedule.format_free_slots(res)


def test_free_slots_all_day_free_without_and_with_times():
    empty = _data(_row(day=3))                     # 周三有课,周一整天没有
    plain = schedule.free_slots(TODAY_MON, empty)
    assert plain["all_day_free"] and plain["gaps"] == []
    assert "没填作息表" in plain["note"] and "说不到几点" in plain["note"]
    clocked = schedule.free_slots(TODAY_MON, empty, _times(("08:00", "08:45"), ("08:55", "09:40")))
    assert clocked["all_day_free"] and clocked["gaps"][0]["start"] == "08:00"
    assert "08:00~09:40" in schedule.format_free_slots(clocked)


def test_free_slots_never_calls_an_out_of_term_day_free():
    """开学前那天"没查到课"是真的,"全天有空"是假的 —— 只能不报。"""
    res = schedule.free_slots(date(2026, 9, 1), _data(_row()))
    assert not res["in_term"] and res["gaps"] == [] and not res["all_day_free"]
    assert res["week"] is None
    assert "不报空闲" in schedule.format_free_slots(res)


def test_free_slots_refuses_when_a_class_has_no_period():
    rows = _data(_row(periods="1-2"), _row("选修课", 1, ""))
    res = schedule.free_slots(TODAY_MON, rows)
    assert res["unknown"] == ["选修课"]
    assert res["gaps"] == []                       # 那门课可能正落在"空档"里
    assert "算不准" in res["note"]


def test_an_unlocated_class_can_be_found_by_start_time_when_times_are_filled():
    """节次写歪但填了作息表:靠"上课钟点正好等于某节上课钟点"定位(只认完全相等)。"""
    rows = _data(_row(periods="1"), _row("选修课", 1, "", start_time="10:00"))
    times = _times(("08:00", "08:45"), ("08:55", "09:40"), ("10:00", "10:45"))
    res = schedule.free_slots(TODAY_MON, rows, times)
    assert not res["unknown"] and [g["label"] for g in res["gaps"]] == ["第 2 节"]
    near_miss = _data(_row(periods="1"), _row("选修课", 1, "", start_time="10:05"))
    assert schedule.free_slots(TODAY_MON, near_miss, times)["unknown"] == ["选修课"]


def test_free_slots_without_schedule_says_so():
    res = schedule.free_slots(TODAY_MON, None, {"periods": {}, "total": 0, "source": ""})
    assert not res["registered"] and "登记课表" in schedule.format_free_slots(res)


# ---------------------------------------------------------------- 周概览

def test_week_gaps_lists_every_day_once():
    rows = _data(_row(periods="1-2"), _row("早读", 3, "1-2"), _row("体育课", 3, "5-6"))
    res = schedule.week_gaps(TODAY_MON, rows)
    assert res["week"] == 1 and len(res["days"]) == 7
    assert [d["weekday"] for d in res["days"]] == list("一二三四五六日")
    assert [g["label"] for g in res["days"][2]["gaps"]] == ["第 3-4 节"]   # 周中有空档
    assert res["days"][0]["gaps"] == []                            # 周一两节连着上


def test_week_gaps_puts_the_longest_slot_first_and_honours_the_limit():
    rows = _data(_row(periods="1-2"), _row("实验", 1, "7-8"), _row("班会", 1, "11"))
    text = schedule.format_week_gaps(schedule.week_gaps(TODAY_MON, rows), limit=1)
    line = [ln for ln in text.splitlines() if ln.startswith("- 周一")][0]
    assert "第 3-6 节" in line and "等" in line        # 最长的先说,列不下的承认还有


def test_week_gaps_outside_term_does_not_read_as_a_free_week():
    text = schedule.format_week_gaps(schedule.week_gaps(date(2026, 7, 6), _data(_row())))
    assert "不在学期内" in text and "别说成有空" in text


# ---------------------------------------------------------------- 作息表落盘

def test_save_period_times_roundtrip():
    ok, msg = schedule.save_period_times(
        [{"start": "8:00", "end": "8:45"}, {"start": "08:55", "end": "9:40"}], "教务处")
    assert ok and "第 1-2 节" in msg
    times = schedule.load_period_times()
    assert times["total"] == 2 and times["source"] == "教务处"
    assert times["periods"][1] == {"start": "08:00", "end": "08:45"}     # 补零,字符串比对才稳


def test_save_period_times_rejects_bad_rows_without_touching_the_file():
    assert schedule.save_period_times([{"start": "08:00", "end": "08:45"}])[0]
    for bad in ([{"start": "八点", "end": "08:45"}],                   # 看不懂的时刻
                [{"start": "08:00", "end": "07:45"}],                  # 下课早于上课
                [{"period": 1, "start": "08:00", "end": "08:45"},
                 {"period": 3, "start": "10:00", "end": "10:45"}],     # 中间断了
                [{"period": 1, "start": "08:00", "end": "08:45"},
                 {"period": 1, "start": "08:55", "end": "09:40"}]):    # 同一节两条
        ok, msg = schedule.save_period_times(bad)
        assert not ok, bad
        assert msg.startswith("错误")
        assert schedule.load_period_times()["total"] == 1              # 本机文件原样


def test_saving_nothing_clears_the_timetable():
    assert schedule.save_period_times([{"start": "08:00", "end": "08:45"}])[0]
    assert schedule.save_period_times([])[0]
    assert schedule.load_period_times()["total"] == 0


def test_load_period_times_tolerates_garbage_and_broken_chains():
    schedule.period_times_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.period_times_path().write_text("{坏文件", encoding="utf-8")
    assert schedule.load_period_times()["total"] == 0
    schedule.period_times_path().write_text(
        '{"periods": [{"period": 1, "start": "08:00", "end": "08:45"},'
        '{"period": 3, "start": "10:00", "end": "10:45"}]}', encoding="utf-8")
    times = schedule.load_period_times()
    assert times["total"] == 1          # 第 2 节空着 → 只认连续到第 1 节,不猜第 3 节


def test_free_slots_ignores_timetable_periods_beyond_the_contiguous_chain():
    schedule.period_times_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.period_times_path().write_text(
        '{"periods": [{"period": 1, "start": "08:00", "end": "08:45"},'
        '{"period": 2, "start": "08:55", "end": "09:40"},'
        '{"period": 4, "start": "10:00", "end": "10:45"}]}', encoding="utf-8")
    assert schedule.save("2026-09-07", [_row(periods="1-2")])[0]
    res = schedule.free_slots(TODAY_MON)
    assert res["gaps"] == []                        # 只到第 2 节,第 3 节往后一律不猜


# ---------------------------------------------------------------- 工具与面板

def test_tool_free_and_times_actions():
    from datetime import timedelta
    monday = date.today() - timedelta(days=date.today().weekday())     # 不写死今天,否则换日就假红
    assert schedule.schedule(None, action="save", term_start=monday.isoformat(),
                             classes=[_row(periods="1-2"), _row("早读", 3, "1-2"),
                                      _row("体育课", 3, "5-6")])[0]
    out = schedule.schedule(None, action="free")
    assert "本周(第 1 周" in out and monday.isoformat() in out
    assert "第 3-4 节" in schedule.schedule(None, action="free",
                                            when=(monday + timedelta(days=2)).isoformat())
    assert (monday + timedelta(days=7)).isoformat() in schedule.schedule(
        None, action="free", when="next")
    assert "错误" in schedule.schedule(None, action="free", when="下周三")
    assert "本机没有作息表" in schedule.schedule(None, action="times")
    assert schedule.schedule(None, action="save_times",
                             periods=[{"start": "08:00", "end": "08:45"}])
    assert "第 1-1 节" in schedule.schedule(None, action="times")
    assert "未知 action" in schedule.schedule(None, action="nope")


def test_prompt_section_carries_the_computed_week_gaps():
    assert schedule.save("2026-09-07", [_row(periods="1-2")])[0]
    text = schedule.schedule_prompt_section()
    assert "本周空档" in text and "不要" in text          # 没作息表不许报钟点那条约束在


def test_view_carries_gaps_period_times_and_day_items(monkeypatch):
    from minicode import calendar, reminders
    assert schedule.save("2026-09-07", [_row(periods="1-2"), _row("早读", 3, "1-2"),
                                        _row("体育课", 3, "5-6")])[0]
    assert reminders.add("交实验报告", due="2026-09-09")["ok"]      # 周三
    assert reminders.add("只有文字日期", due="下周三")["ok"]         # 不落日期的不参与
    monkeypatch.setattr(calendar, "load_events", lambda: ([
        {"name": "期中考试周", "start": "2026-09-09", "end": "2026-09-12", "kind": "exam"},
        {"name": "没日期的安排", "start": "", "kind": "note"}], "夹具"))
    view = schedule.view(TODAY_MON)
    assert view["clock"] is False and view["period_times"]["filled"] is False
    wed = view["week_grid"]["3"]
    assert [g["label"] for g in wed["gaps"]] and wed["items"][0]["title"] == "交实验报告"
    assert len(wed["items"]) == 1                     # 只有 due_raw 的那条没摊进来
    assert wed["events"][0]["span"] == "2026-09-09 ~ 2026-09-12"
    assert view["week_grid"]["4"]["events"] == []     # 跨多日只标首日,不铺四天
    assert view["today"]["date"] == "2026-09-07"


def test_view_counts_overdue_separately_and_survives_broken_sources(monkeypatch):
    from minicode import calendar, reminders
    assert schedule.save("2026-09-07", [_row()])[0]
    assert reminders.add("早就过期了", due="2026-09-01")["ok"]
    monkeypatch.setattr(calendar, "load_events",
                        lambda: (_ for _ in ()).throw(OSError("读不动")))
    view = schedule.view(TODAY_MON)                   # 校历读不了不能连累课表面板
    assert view["overdue"] == 1 and view["week_grid"]["1"]["events"] == []


# ------------------------------------------------ 走堂换楼接进工具/面板/提示词(v0.9.35)

def _walk_rows(day=1):
    """同一天的两节紧接课、分处两栋真楼(楼名取自真地图索引)。"""
    return [_row(periods="1-2", day=day, location="J1至善楼"),
            _row("大学英语", day, "3-4", location="S5致用楼")]


def _this_monday():
    from datetime import timedelta
    return date.today() - timedelta(days=date.today().weekday())


def test_tool_risk_action_names_both_buildings_without_inventing_minutes():
    assert schedule.save("2026-09-07", _walk_rows())[0]
    out = schedule.schedule(None, action="risk", when="2026-09-07")
    assert "J1至善楼" in out and "S5致用楼" in out
    assert "要换楼" in out
    assert not re.search(r"\d+\s*分钟", out)        # 没填作息表:给不出分钟数字(那句提醒除外)


def test_tool_risk_action_covers_week_and_rejects_vague_when():
    monday = _this_monday()
    assert schedule.save(monday.isoformat(), _walk_rows())[0]
    out = schedule.schedule(None, action="risk")            # 不传 when = 本周
    assert "本周(第 1 周" in out and monday.isoformat() in out
    from datetime import timedelta
    assert (monday + timedelta(days=7)).isoformat() in \
        schedule.schedule(None, action="risk", when="next")
    assert "错误" in schedule.schedule(None, action="risk", when="下周三")


def test_view_carries_walk_facts_for_the_panel():
    assert schedule.save("2026-09-07", _walk_rows() + [
        _row("体育课", 3, "1-2", location="田径场")])[0]
    view = schedule.view(TODAY_MON)
    assert view["walk_index_ready"] is True
    mon, wed = view["week_grid"]["1"], view["week_grid"]["3"]
    assert [(i["prev"]["building"], i["curr"]["building"]) for i in mon["walks"]] == \
        [("J1至善楼", "S5致用楼")]
    assert mon["walks"][0]["no_gap"] is True
    assert wed["walks"] == [] and wed["walk_skipped"][0]["name"] == "体育课"


def test_view_carries_real_walk_counts_and_a_week_summary():
    """v0.9.36:面板那句"这周几天要换楼、哪天最多"由后端算好,前端只挑出那天加重颜色。"""
    assert schedule.save("2026-09-07", [
        _row("高等数学", 1, "1-2", location="J1至善楼"),
        _row("大学英语", 1, "3-4", location="S5致用楼"),
        _row("数据结构", 1, "5-6", location="S1格致楼"),
        _row("体育", 3, "1-2", location="J1至善楼"),
        _row("体育", 3, "3-4", location="S5致用楼")])[0]
    view = schedule.view(TODAY_MON)
    mon, wed = view["week_grid"]["1"], view["week_grid"]["3"]
    assert mon["walk_total"] == 2 and wed["walk_total"] == 1
    assert view["walk_week"] == {"days": 2, "total": 3,
                                 "worst_date": mon["date"], "worst_weekday": "一",
                                 "worst_count": 2}


def test_view_week_summary_is_zero_for_a_week_without_walks():
    assert schedule.save("2026-09-07", [
        _row(periods="1-2", day=1, location="J1至善楼101"),
        _row("大学英语", 1, "3-4", location="J1至善楼205")])[0]
    assert schedule.view(TODAY_MON)["walk_week"]["days"] == 0


def test_prompt_section_injects_walk_lines_only_when_there_is_one():
    day = date.today().weekday() + 1                # 注入看的是真"今天/明天",不能写死周几
    assert schedule.save(_this_monday().isoformat(), _walk_rows(day))[0]
    text = schedule.schedule_prompt_section()
    assert "要换教学楼的课" in text and "J1至善楼 → S5致用楼" in text
    assert "绝不替他估" in text                     # 不许模型自己补"走几分钟"

    assert schedule.save(_this_monday().isoformat(), [
        _row(periods="1-2", day=day, location="J1至善楼101"),
        _row("大学英语", day, "3-4", location="J1至善楼205")])[0]
    assert "要换教学楼" not in schedule.schedule_prompt_section()   # 同一栋换教室:一行都不注入

# ---------------------------------------------------------------- 桥与门面

def test_bridge_save_times_rejects_unparsable_payloads_without_writing():
    """面板传来的 JSON 看不懂时,本机文件必须原样不动(不能"顺手清空")。"""
    from minicode.gui_bridge import Bridge
    assert schedule.save_period_times([{"start": "08:00", "end": "08:45"}])[0]
    for bad in ("{坏JSON", '{"不是":"一列节次"}'):
        r = Bridge.schedule_save_times(object(), bad)
        assert not r["ok"] and r["schedule"] is None
        assert schedule.load_period_times()["total"] == 1
    ok = Bridge.schedule_save_times(object(), '[{"start":"08:00","end":"08:45"}]')
    # 没登记课表时面板回引导态(不带 period_times):作息表存了,但要等有课表才说空档
    assert ok["ok"] and ok["schedule"]["registered"] is False


def test_facade_forwards_the_timetable_form():
    """前端只碰得到门面:漏一条转发,「填作息表」点了就是 undefined 报错。"""
    from minicode.gui_bridge import PublicApi

    seen = []

    class Recorder:
        def __getattr__(self, name):
            def call(*args):
                seen.append((name, args))
                return {"ok": True}
            return call

    api = PublicApi.__new__(PublicApi)
    api._b = Recorder()
    payload = '[{"start":"08:00","end":"08:45"}]'
    assert PublicApi.schedule_save_times(api, payload)["ok"] is True
    assert seen == [("schedule_save_times", (payload,))]


# ---------------------------------------------------------------- 学期基准对账(v0.9.43)

def _write_calendar(events):
    """在 APP_DIR 放一份校历(与 calendar.load_events 同形)。"""
    from minicode import paths
    p = paths.APP_DIR / "kb" / "school-calendar.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")


def test_term_alignment_silent_when_calendar_has_no_term_events():
    """校历里没有 kind=term 的条目 → 什么都不说(不按名字猜"开学")。"""
    from minicode import paths
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_calendar([{"name": "运动会", "start": "2026-09-09", "kind": "event"}])
    _save()          # term_start 与运动会差 2 天,但 kind 不对,不该对账
    assert schedule.term_alignment() is None


def test_term_alignment_silent_when_dates_agree():
    from minicode import paths
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_calendar([{"name": "2026-2027学年第一学期", "start": "2026-09-07",
                      "kind": "term", "source": "校历"}])
    _save()
    assert schedule.term_alignment() is None


def test_term_alignment_flags_mismatch_with_nearest_term_event():
    from minicode import paths
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_calendar([
        {"name": "另一学期", "start": "2027-03-01", "kind": "term"},
        {"name": "2026-2027学年第一学期", "start": "2026-09-01", "kind": "term"},
    ])
    _save()          # term_start=2026-09-07,校历写 09-01,差 6 天
    res = schedule.term_alignment()
    assert res and res["calendar_name"] == "2026-2027学年第一学期"
    assert res["days_off"] == -6
    assert "差 -6 天" in res["msg"] and "请核对" in res["msg"]


def test_save_message_carries_alignment_warning(monkeypatch):
    from minicode import paths
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_calendar([{"name": "2026-2027学年第一学期", "start": "2026-09-01",
                      "kind": "term"}])
    ok, msg = schedule.save("2026-09-07", [
        {"name": "高数", "day": 1, "periods": "1-2", "weeks": "1-16"}])
    assert ok and "对账" in msg and "差 -6 天" in msg


def test_show_appends_alignment_but_format_day_stays_clean(monkeypatch):
    from minicode import paths
    from minicode.tools.base import ToolContext
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_calendar([{"name": "2026-2027学年第一学期", "start": "2026-09-01",
                      "kind": "term"}])
    _save()
    ctx = ToolContext(workspace=None, config=None)
    out = schedule.schedule(ctx, action="show", when="2026-09-07")
    assert "对账" in out
    # 纯格式函数保持干净:简报/提示词不因对账多一个字节
    assert "对账" not in schedule.format_day(date(2026, 9, 7))


# ---------------------------------------------------------------- 起止钟点(v0.9.58,.ics 导出的公开口)

def test_row_clock_returns_real_period_bounds():
    times = {"periods": {n: {"start": f"{7+n:02d}:00", "end": f"{7+n:02d}:45"}
                         for n in range(1, 13)},
             "total": 12, "source": "测试"}
    assert schedule.row_clock({"periods": "1-2"}, times) == ("08:00", "09:45")
    assert schedule.row_clock({"periods": "3"}, times) == ("10:00", "10:45")
    # 没填节次但填了 start_time:反查那一节(_row_periods 同一份判定)
    assert schedule.row_clock({"start_time": "08:00"}, times) == ("08:00", "08:45")


def test_row_clock_refuses_what_it_cannot_know():
    times = {"periods": {n: {"start": f"{7+n:02d}:00", "end": f"{7+n:02d}:45"}
                         for n in range(1, 13)},
             "total": 4, "source": "测试"}
    empty = {"periods": {}, "total": 0, "source": ""}
    assert schedule.row_clock({"periods": "1-2"}, empty) is None      # 没填作息表
    assert schedule.row_clock({"periods": "5-6"}, times) is None      # 超出连续段
    assert schedule.row_clock({"periods": "1,3"}, times) is None      # 断档:中间那节不知道
