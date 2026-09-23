"""走堂冲突预警(minicode/walk.py):换楼判定 + 四条"不许装懂"的口径。

夹具用的是**真地图索引**(frontend/campus-index.json,36 栋 + 203 条别名)而不是
手编的假建筑表:这一模块的全部风险都出在"地点文字 → 哪栋楼"那一步,拿假索引测
等于把唯一会错的地方绕过去了。课表与作息表走 schedule 的真实存取(APP_DIR 隔离)。
"""
import re
from datetime import date, timedelta

import pytest

from minicode import campus, paths, schedule, walk

MON = date(2026, 9, 7)          # 第 1 周周一;MON+0 就是周一当天
TUE = date(2026, 9, 8)

# 一份像样的作息表:上午 1-4 节课间 10 分钟,第 4→5 节之间是午休(150 分钟)
TIMES = [("08:00", "08:45"), ("08:55", "09:40"), ("09:50", "10:35"),
         ("10:45", "11:30"), ("14:00", "14:45"), ("14:55", "15:40"),
         ("19:00", "19:45"), ("19:55", "20:40")]


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


def _save(classes):
    ok, msg = schedule.save(MON.isoformat(), classes)
    assert ok, msg
    return schedule.load()


def _fill_times(n=8):
    ok, msg = schedule.save_period_times(
        [{"period": i + 1, "start": s, "end": e} for i, (s, e) in enumerate(TIMES[:n])])
    assert ok, msg


def _class(name, periods, location, day=1):
    return {"name": name, "day": day, "periods": periods, "location": location}


# ---------------------------------------------------------------- 地点 → 楼

def test_place_of_resolves_room_suffixes_through_real_aliases():
    got = walk.place_of("S1格致楼101")
    assert got["kind"] == "building" and got["label"] == "S1格致楼"
    assert walk.place_of("J1至善楼205")["label"] == "J1至善楼"


def test_place_of_refuses_aliases_that_match_several_buildings():
    got = walk.place_of("A栋")           # 学思苑A 与 集贤苑A座 都认这个俗称
    assert got["kind"] == "ambiguous"
    assert "不替它认" in got["why"]


def test_place_of_treats_venues_as_not_a_building():
    got = walk.place_of("田径场")
    assert got["kind"] == "venue" and "不是楼" in got["why"]


def test_place_of_blank_and_unknown_locations():
    assert walk.place_of("")["kind"] == "blank"
    assert walk.place_of("线上教学")["kind"] == "miss"
    assert "地图索引里没有" in walk.place_of("线上教学")["why"]


def test_place_of_without_index_says_it_cannot_judge():
    got = walk.place_of("J1至善楼", index={})
    assert got["kind"] == "no_index" and "判断不了" in got["why"]


# ---------------------------------------------------------------- 相邻关系

def _b(name, nearby):
    return {"name": name, "aliases": [name], "nearby": nearby}


def test_adjacency_only_counts_when_both_sides_agree():
    a, b, c = _b("甲楼", ["乙楼"]), _b("乙楼", ["甲楼"]), _b("丙楼", [])
    assert walk.adjacency(a, b) == "both"
    assert walk.adjacency(b, a) == "both"
    assert walk.adjacency(a, c) == "none"


def test_adjacency_of_one_way_index_records_is_not_both():
    """真索引里确实有单向记录(学思苑G 写了 H,H 没写 G)。

    钉成「不许是 both」而不是「必须是 one_way」:哪天交付包把另一边补上,应当
    自然升级成 both;但**悄悄**变成 both(拿单向记录当"就在旁边")必须红。
    """
    idx = campus.load_index()
    by = {b["name"]: b for b in campus.buildings(idx)}
    assert walk.adjacency(by["学思苑G"], by["学思苑H"]) != "both"


# ---------------------------------------------------------------- 一天判定

def test_walk_detected_and_minutes_come_from_the_local_timetable():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼")])
    _fill_times()
    res = walk.day_risks(MON, data)
    assert res["class_count"] == 2 and len(res["items"]) == 1
    it = res["items"][0]
    assert (it["prev"]["building"], it["curr"]["building"]) == ("J1至善楼", "S5致用楼")
    assert it["gap_periods"] == 0 and it["no_gap"] is True
    assert it["minutes"] == 10            # 第 2 节 09:40 下课 → 第 3 节 09:50 上课


def test_without_timetable_no_minute_number_appears_anywhere():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼")])
    res = walk.day_risks(MON, data)
    assert res["clock"] is False and res["items"][0]["minutes"] is None
    text = walk.format_risks(res)
    assert not re.search(r"\d+\s*分钟", text), text
    assert "没填作息表" in text and "不等于来不及" in text


def test_same_building_different_room_is_not_a_walk():
    data = _save([_class("高等数学", "1-2", "J1至善楼101"),
                  _class("高等数学实验", "3-4", "J1至善楼205")])
    _fill_times()
    res = walk.day_risks(MON, data)
    assert res["items"] == [] and res["note"]


def test_classes_the_index_cannot_place_are_skipped_not_guessed():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("体育课", "3-4", "田径场"),
                  _class("毛概", "5-6", "A栋")])
    _fill_times()
    res = walk.day_risks(MON, data)
    why = {s["name"]: s["why"] for s in res["skipped"]}
    assert "不是楼" in why["体育课"] and "不替它认" in why["毛概"]
    # 体育课/毛概 都判不出楼 → 只有"高数→?"这一段可比,而下一段判不了 → 一条不换楼
    assert res["items"] == []


def test_class_between_two_buildings_blocks_the_pair_instead_of_leaking_a_fake_gap():
    """判不出楼的那节课夹在中间时,两头**不算相邻两节** —— 配成一对会把中间那节
    课的时间当成"两节之间的空档",给出一个假的分钟数。"""
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("体育课", "3-4", "田径场"),
                  _class("毛概", "5-6", "S5致用楼")])
    _fill_times()
    res = walk.day_risks(MON, data)
    assert res["items"] == []
    assert [s["name"] for s in res["skipped"]] == ["体育课"]
    assert "体育课" in walk.format_risks(res)      # 没参与判断要当面说清,不是悄悄跳过


def test_class_without_periods_is_skipped_with_a_clear_reason():
    data = _save([_class("高等数学", "", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼")])
    res = walk.day_risks(MON, data)
    assert res["skipped"][0]["name"] == "高等数学"
    assert "没写节次" in res["skipped"][0]["why"]


def test_same_period_two_classes_report_as_clash_separate_from_walk():
    data = _save([_class("体育课", "5-6", "田径场"),
                  _class("文献检索", "5-6", "图书馆301")])
    _fill_times()
    res = walk.day_risks(MON, data)
    assert len(res["clashes"]) == 1
    clash = res["clashes"][0]
    assert clash["label"] == "第 5-6 节"
    assert {clash["a"]["name"], clash["b"]["name"]} == {"体育课", "文献检索"}
    text = walk.format_risks(res)
    assert "同一节排了两门课" in text and "不是走路问题" in text


def test_out_of_term_day_refuses_to_judge():
    data = _save([_class("高等数学", "1-2", "J1至善楼")])
    before = walk.day_risks(MON - timedelta(days=3), data)
    assert before["in_term"] is False and before["items"] == []
    assert "不在本学期内" in walk.format_risks(before)


def test_missing_schedule_says_register_first():
    res = walk.day_risks(MON)               # 这台机器还没存过课表
    assert res["registered"] is False and res["items"] == []
    assert "还没有登记课表" in walk.format_risks(res)
    assert walk.upcoming_lines(today=MON) == []


def test_missing_index_degrades_to_a_clear_refusal(monkeypatch):
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼")])
    monkeypatch.setattr(walk, "_index", lambda: {})
    res = walk.day_risks(MON, data)
    assert res["index_ready"] is False and res["items"] == []
    assert "地图建筑索引不在" in walk.format_risks(res)


# ---------------------------------------------------------------- 排序与上限

def test_items_are_ordered_by_what_to_leave_first():
    """中间空两节的不该抢在"紧接着"前面;同样是紧接着的,分钟小的在前。"""
    data = _save([_class("A 课", "1-2", "J1至善楼"),
                  _class("B 课", "3-4", "S5致用楼"),          # 紧接,10 分钟
                  _class("C 课", "5-6", "J3修齐楼"),          # 紧接,150 分钟(午休后)
                  _class("D 课", "8", "S4笃行楼")])           # 中间空 1 节
    _fill_times()
    res = walk.day_risks(MON, data)
    assert [i["minutes"] for i in res["items"]] == [10, 150, 255]
    assert [i["gap_periods"] for i in res["items"]] == [0, 0, 1]
    assert res["items"][-1]["no_gap"] is False


def test_lunch_break_walk_states_minutes_and_never_calls_it_dangerous():
    data = _save([_class("高等数学", "3-4", "J1至善楼"),
                  _class("大学英语", "5-6", "S5致用楼")])
    _fill_times()
    text = walk.format_risks(walk.day_risks(MON, data))
    assert "150 分钟" in text                       # 事实说清楚
    for word in ("来不及", "危险", "要迟", "肯定赶"):
        assert word not in text                     # 判断留给人


def test_day_caps_items_and_reports_the_rest():
    """排得再满也只列 MAX_ITEMS 处,其余报个数 —— 面板不该被一屏 ⚠ 淹没。"""
    buildings = ["J1至善楼", "S5致用楼", "J3修齐楼", "S4笃行楼", "J2明德楼", "S1格致楼",
                 "J4治平楼", "S2S3博雅楼", "J1至善楼", "S5致用楼", "J3修齐楼", "S4笃行楼"]
    rows = [_class(f"课{i + 1}", str(i + 1), loc) for i, loc in enumerate(buildings)]
    data = _save(rows)
    _fill_times(6)                      # 作息表只填到第 6 节:7 节往后给不出钟点
    res = walk.day_risks(MON, data)
    assert len(res["items"]) == walk.MAX_ITEMS
    assert res["items_more"] == 3                      # 12 门课 → 11 处换楼,只列 8 处
    assert "没列出" in walk.format_risks(res)
    assert any(i["minutes"] is None for i in res["items"])   # 超出作息表的就是没分钟
    text = walk.format_risks(res)
    # 只有真给了钟点的条目才能出现分钟数:作息表填到第 6 节,第 7 节往后一个数字都不许冒出来
    assert len(re.findall(r"\d+ 分钟", text)) == sum(1 for i in res["items"] if i["clock"])


# ---------------------------------------------------------------- 周视图与注入

def test_week_risks_covers_seven_days_and_only_surfaces_the_tightest():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼"),
                  _class("体育", "5", "桃园饭堂"),
                  _class("毛概", "7", "J3修齐楼")])
    _fill_times()
    res = walk.week_risks(MON, data)
    assert len(res["days"]) == 7 and res["week"] == 1
    assert res["days"][0]["items"] and res["days"][1]["items"] == []
    assert res["days"][0]["date"] == MON.isoformat()
    text = walk.format_week_risks(res)
    assert "周一" in text and "没有要换楼的课" in text
    assert text.count("大学英语") == 1               # 每天只出最紧的一处


def test_week_view_when_not_in_term_says_so():
    res = walk.week_risks(MON - timedelta(days=30))
    assert res["week"] is None
    assert "不在学期内" in walk.format_week_risks(res)


def test_upcoming_lines_are_empty_when_there_is_nothing_to_say():
    _save([_class("高等数学", "1-2", "J1至善楼")])
    assert walk.upcoming_lines(today=MON) == []


def test_upcoming_lines_only_include_back_to_back_walks():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼"),        # 紧接 → 该注入
                  _class("选修", "7", "J3修齐楼"),              # 中间空 2 节 → 不注入
                  _class("晚点名", "3-4", "S4笃行楼", day=2),   # 明天两节紧接 → 该注入
                  _class("班会", "5-6", "J2明德楼", day=2)])
    _fill_times()
    lines = walk.upcoming_lines(days=2, today=MON, data=data)
    assert any("今天" in x and "10 分钟" in x for x in lines)
    assert not any("空 2 节" in x for x in lines)
    assert len(lines) == 2                            # 今天一处 + 明天一处
    assert sum(1 for x in lines if "明天" in x) == 1


def test_upcoming_lines_without_timetable_admit_no_minutes():
    data = _save([_class("高等数学", "1-2", "J1至善楼"),
                  _class("大学英语", "3-4", "S5致用楼")])
    lines = walk.upcoming_lines(days=1, today=MON, data=data)
    assert len(lines) == 1
    assert "没编分钟" in lines[0] and "分钟)" not in lines[0].split("没编分钟")[0]


# ---------------------------------------------------------------- 返回形状

def test_day_shape_mirrors_free_slots_vocabulary():
    """面板与空档看板共用一套字段名,少一个键前端就要多写一处判空。"""
    data = _save([_class("高等数学", "1-2", "J1至善楼")])
    res = walk.day_risks(MON, data)
    free = schedule.free_slots(MON, data)
    for key in ("date", "weekday", "week", "registered", "in_term", "clock", "note"):
        assert key in res and key in free, key
    assert res["items"][0]["kind"] if res["items"] else True
