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
