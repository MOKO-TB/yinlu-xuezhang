"""情绪日记(mood 工具 + 情绪面板后端)测试。

这一模块的测试重点不是"功能多",而是**几条边界有没有守住**:
- 只回读用户自己记的东西:不诊断、不替用户打分、不编日期;
- 认不出的日期/分数就明说认不出,绝不猜一个填进去;
- 刻意没有连续天数 / 打卡 / "你已经 N 天没记了"这类催法;
- 自伤这类话触发指路,而指路那段的号码**只能是学校心理中心公众号原文里那几个**
  (白名单钉在本文件里,多一个少一个都红);
- 记录只在本机 mood.json,原子写、能一键导出、能一键清空。
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from minicode import mood


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    from minicode import paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    yield


TODAY = date(2026, 9, 22)


def _recent(back: int) -> str:
    """按**真今天**往前推 N 天的日期串。

    用在哪几支测试上是有原因的:`list_recent` 与 `mood(action=list)` 默认拿真今天算
    时间窗,而窗口一过,写死的绝对日期就掉出去 —— 表现是"什么都没改,过一天自己红了"
    (2026-10-08 就是这样红的:夹具写 2026-10-01,七天窗的边界那天正好是 10-02)。
    要摆"窗口外"的旧记录仍然用绝对日期或显式传 today=,那两类是故意的。
    """
    return (date.today() - timedelta(days=back)).isoformat()


def _put(day, score=None, note="", tags=None):
    """直接落一条记录(绕开 log 的规范化,专门用来摆窗口/阈值)。"""
    rows = mood.load_entries()
    rows = [r for r in rows if r.get("date") != day]
    rows.append({"date": day, "score": score, "note": note,
                 "tags": tags or [], "created_at": f"{day} 21:00"})
    mood.save_entries(rows)


# ---------------------------------------------------------------- 记一条

def test_log_needs_a_score_or_a_note():
    assert mood.log(today=TODAY)["ok"] is False
    assert mood.load_entries() == []          # 拒记就是什么都不写


def test_log_writes_score_label_and_note():
    r = mood.log(score="4", note="答辩练习过了", day="今天", today=TODAY)
    assert r["ok"] and "4 分(还行)" in r["msg"]
    rows = mood.load_entries()
    assert len(rows) == 1 and rows[0]["score"] == 4
    assert rows[0]["date"] == "2026-09-22" and rows[0]["note"] == "答辩练习过了"


def test_same_day_overwrites_and_says_so():
    mood.log(score=3, note="还行", day="今天", today=TODAY)
    r = mood.log(score=2, note="其实不太好", day="今天", today=TODAY)
    rows = mood.load_entries()
    assert len(rows) == 1 and rows[0]["score"] == 2
    assert "覆盖" in r["msg"] and "还行" in r["msg"]      # 覆盖了什么要说清


def test_text_only_entry_is_allowed_and_marked():
    r = mood.log(note="说不清,就是累", today=TODAY)
    assert r["ok"] and r["entry"]["score"] is None
    assert "没给分数" in r["msg"]


def test_unrecognised_score_keeps_the_note_and_admits_it():
    r = mood.log(score="七颗星", note="今天还行", today=TODAY)
    assert r["ok"] and r["entry"]["score"] is None
    assert "认不出" in r["msg"]


def test_no_date_guessing():
    r = mood.log(score=3, note="x", day="下周三", today=TODAY)
    assert r["ok"] is False and "下周三" in r["msg"]
    assert mood.load_entries() == []


def test_future_day_rejected():
    r = mood.log(score=5, note="先把好消息存着", day="2026-12-31", today=TODAY)
    assert r["ok"] is False and "还没到" in r["msg"]


def test_date_formats():
    assert mood.log(day="2026/9/20", score=3, today=TODAY)["entry"]["date"] == "2026-09-20"
    assert mood.log(day="2026.9.21", score=3, today=TODAY)["entry"]["date"] == "2026-09-21"
    assert mood.log(day="昨天", score=3, today=TODAY)["entry"]["date"] == "2026-09-21"


def test_atomic_write_leaves_no_temp_file(tmp_path):
    from minicode import paths
    mood.log(score=3, note="ok", today=TODAY)
    assert not (paths.APP_DIR / "mood.json.tmp").exists()
    assert json.loads((paths.APP_DIR / "mood.json").read_text(encoding="utf-8"))["entries"]


# ---------------------------------------------------------------- 每周回读

def test_review_says_nothing_happened_instead_of_shaming():
    text = mood.weekly_review(today=TODAY)["text"]
    assert "没记过情绪" in text
    # 空窗口的措辞重点是"不想记就不记",不是"你漏了"
    assert "不是任务" in text


def test_review_too_few_scores_refuses_to_describe_a_shape():
    _put("2026-09-21", 4, "还行")
    _put("2026-09-22", 3, "一般")
    text = mood.weekly_review(today=TODAY)["text"]
    assert "还不够看出什么形状" in text and "平均" not in text


def test_review_averages_and_lists_only_what_was_logged():
    for d, s in (("2026-09-19", 3), ("2026-09-20", 4), ("2026-09-22", 5)):
        _put(d, s, "记一句")
    _put("2026-09-10", 1, "太久以前,这条不该出现在这一周")
    r = mood.weekly_review(today=TODAY)
    assert r["count"] == 3
    assert "平均" in r["text"] and "3 天" in r["text"]
    assert "09-10" not in r["text"]


def test_review_has_no_streak_or_checkin_language():
    for d, s in (("2026-09-20", 4), ("2026-09-21", 4), ("2026-09-22", 4)):
        _put(d, s, "还行")
    text = mood.weekly_review(today=TODAY)["text"]
    for banned in ("连续", "打卡", "断签", " streak", "坚持了"):
        assert banned not in text


def test_review_always_ends_with_the_not_a_diagnosis_line():
    _put("2026-09-22", 3, "一般")
    assert "不是医生" in mood.weekly_review(today=TODAY)["text"]


def test_sustained_low_points_to_the_help_block():
    for i, d in enumerate(("2026-09-12", "2026-09-18", "2026-09-22")):
        _put(d, 1 if i < 2 else 2, "很低")
    text = mood.weekly_review(today=TODAY)["text"]
    assert "足够好的理由" in text and "博雅楼" in text
    # 只回读、不贴标签
    assert "抑郁" not in text and "焦虑" not in text


def test_hand_edited_junk_date_does_not_break_the_review():
    mood.save_entries([{"date": "2026-09-22", "score": 3, "note": "好"},
                       {"date": "不是日期", "score": 4, "note": "坏"}])
    r = mood.weekly_review(today=TODAY)
    assert r["count"] == 1
    assert "09-22" in r["text"] and "坏" not in r["text"]


# ---------------------------------------------------------------- 危机与安全边界

def test_crisis_note_flags_and_shows_human_facing_help_only():
    r = mood.log(note="撑不下去了,不想活", today=TODAY)
    assert r["ok"] and r["crisis"] is True
    assert "博雅楼" in r["msg"] and "120" in r["msg"]
    # 面板/toast 直接显示 msg,所以"不许编号码"那类只对模型说的话不能在里面
    assert mood.HELP_CAVEATS not in r["msg"]


def test_tool_log_appends_the_model_facing_relay_rules():
    out = mood.mood(None, action="log", note="不想活了", day="今天")
    assert "博雅楼" in out and "绝对不要" in out and "照原样" in out


def test_everyday_venting_is_not_treated_as_crisis():
    r = mood.log(note="烦死了,想哭", today=TODAY)
    assert r["ok"] and not r.get("crisis")


def test_help_text_only_carries_verified_phone_numbers():
    """号码只能来自那份原文:白名单写死在这里,代码里多出一个号就红。

    来源:学校「广州应用科技学院心理中心」公众号 2025-03-06《这里,有你的依靠 |
    心理健康教育中心简介》。把白名单独立写在测试里(而不是从 HELP_FACTS 里读)是刻意的
    —— 编错一个号码可能把人在最脆弱的时候导向空号,所以想加号码必须**同时**改这里,
    改的人就会被迫去看一眼原文。
    """
    verified = {"2610529", "2211222", "81899120"}      # 0758-2610529 / 0758-2211222 / 020-81899120
    digits = re.findall(r"\d{7,}", mood.HELP_FACTS + mood.HELP_CAVEATS)
    assert set(digits) <= verified, f"求助文本里出现了没核实过的号码:{digits}"
    assert set(digits) == verified, f"核实过的号码不该凭空消失:{digits}"
    assert "0758-2610529" in mood.HELP_FACTS        # 电话预约
    assert "12356" in mood.HELP_FACTS               # 全国统一 24 小时心理援助热线
    assert "2025-03-06" in mood.HELP_FACTS          # 写清出处,便于日后复核
    assert "绝对不要" in mood.HELP_CAVEATS


def test_help_facts_and_caveats_stay_separate():
    assert mood.HELP_BLOCK == mood.HELP_FACTS + mood.HELP_CAVEATS
    assert "绝对不要" in mood.HELP_CAVEATS and "绝对不要" not in mood.HELP_FACTS


# ---------------------------------------------------------------- 注入 / 导出 / 删除

def test_prompt_section_stays_quiet_without_records():
    assert mood.mood_prompt_section(today=TODAY) == ""


def test_prompt_section_reads_back_and_forges_the_boundaries():
    mood.log(score=2, note="一般般", day="今天", today=TODAY)
    s = mood.mood_prompt_section(today=TODAY)
    assert "mood.json" in s and "2026-09-22" in s
    assert "绝不" in s and "诊断" in s
    assert "不催他记录" in s


def test_prompt_section_escalates_on_crisis():
    mood.log(note="不想活", today=TODAY)
    s = mood.mood_prompt_section(today=TODAY)
    assert "优先于" in s and "博雅楼" in s and "不要" in s


def test_export_is_raw_words_plus_a_disclaimer():
    mood.log(score=3, note="一般", day="今天", today=TODAY)
    text = mood.export_text()
    assert "2026-09-22" in text and "一般" in text
    assert "不能当医疗记录" in text


def test_export_empty_returns_nothing():
    assert mood.export_text() == ""


def test_clear_all_reports_how_much_it_removed():
    _put("2026-09-21", 3, "a")
    _put("2026-09-22", 4, "b")
    r = mood.clear_all()
    assert r["ok"] and "2 条" in r["msg"]
    assert mood.load_entries() == []
    assert "备份" in r["msg"]                       # 没备份这件事要说在前面


def test_tool_dispatch_and_unknown_action():
    """工具层不注入日期(它就用真实今天),所以期望值也从真实时钟取。"""
    now = date.today().isoformat()
    mood.mood(None, action="log", score=3, note="记一下", day="今天")
    assert now[5:] in mood.mood(None, action="review", days=7)   # 回读用 MM-DD
    assert now[5:] in mood.mood(None, action="list", days=30)
    assert "记一下" in mood.mood(None, action="export")
    assert mood.mood(None, action="delete", day=now).startswith("已删掉")
    assert "没有记录" in mood.mood(None, action="delete", day=now)
    assert mood.mood(None, action="翻脸").startswith("错误:")


def test_tool_help_returns_the_full_block_for_the_model():
    out = mood.mood(None, action="help")
    assert "博雅楼" in out and "绝对不要" in out


def test_cap_keeps_two_years_and_drops_oldest():
    rows = [{"date": (TODAY - timedelta(days=i)).isoformat(), "score": 3,
             "note": "", "tags": [], "created_at": ""}
            for i in range(mood.MAX_ENTRIES + 20)]
    assert mood.save_entries(rows)[0] is True
    kept = mood.load_entries()
    assert len(kept) == mood.MAX_ENTRIES
    assert kept[-1]["date"] == TODAY.isoformat()      # 截的是最老的,不是最近的
    assert kept[0]["date"] == (TODAY - timedelta(days=mood.MAX_ENTRIES - 1)).isoformat()


# ---------------------------------------------------------------- 面板接桥(v0.9.32)

def _bridge(config, tmp_path):
    from tests.test_gui_bridge import FakeLLM, FakeWindow
    from minicode.gui_bridge import create_app
    bridge, ui, _agent, _session = create_app(
        config, tmp_path, llm=FakeLLM([]), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    window = FakeWindow()
    ui.attach(window)
    return bridge, window


def test_bridge_review_passes_labels_from_the_backend(config, tmp_path):
    """分档文字只有一份:面板不许自己再抄一遍 1~5 的说法。"""
    bridge, _w = _bridge(config, tmp_path)
    mood.log(score=2, note="不太好", day="今天", today=date.today())
    r = bridge.mood_review()
    assert r["ok"] and r["count"] == 1
    assert date.today().strftime("%m-%d") in r["text"]
    assert r["labels"] == {str(k): v for k, v in mood.SCORE_LABELS.items()}
    assert r["labels"]["5"] == "挺好"


def test_bridge_log_never_leaks_model_facing_rules_to_the_panel(config, tmp_path):
    bridge, _w = _bridge(config, tmp_path)
    r = bridge.mood_log(score=None, note="今天还行,就是累")
    assert r["ok"] and not r["crisis"]
    assert bridge.mood_review()["count"] == 1
    crisis = bridge.mood_log(score=None, note="撑不下去了")
    assert crisis["crisis"] is True
    assert "博雅楼" in crisis["msg"] and mood.HELP_CAVEATS not in crisis["msg"]


def test_bridge_log_rejects_empty_without_touching_the_file(config, tmp_path):
    bridge, _w = _bridge(config, tmp_path)
    r = bridge.mood_log(score=None, note="   ")
    assert r["ok"] is False and mood.load_entries() == []


def test_bridge_export_cancelled_dialog_writes_nothing(config, tmp_path):
    """FakeWindow 没有 create_file_dialog → 等同用户按了取消:一个字都不写。"""
    bridge, _w = _bridge(config, tmp_path)
    bridge.mood_log(score=3, note="一般")
    r = bridge.mood_export()
    assert r["ok"] is False and "取消" in r["msg"]
    assert not (tmp_path / "exports").exists()


def test_bridge_export_without_window_falls_back_to_workspace(config, tmp_path):
    from minicode.gui_bridge import create_app
    from tests.test_gui_bridge import FakeLLM
    bridge, ui, _a, _s = create_app(
        config, tmp_path, llm=FakeLLM([]), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")          # 不 attach 窗口 = ui.window None
    bridge.mood_log(score=4, note="导出走备用路径")
    r = bridge.mood_export()
    assert r["ok"] and Path(r["path"]).is_file()
    body = Path(r["path"]).read_text(encoding="utf-8")
    assert "导出走备用路径" in body and "不能当医疗记录" in body


def test_bridge_clear_empties_the_file_and_says_so(config, tmp_path):
    bridge, window = _bridge(config, tmp_path)
    mood.log(score=3, note="a", day="今天")
    mood.log(score=4, note="b", day="昨天")        # 面板只能记今天,这里要两条就得自己错开
    r = bridge.mood_clear()
    assert r["ok"] and "2 条" in r["msg"] and "备份" in r["msg"]
    assert mood.load_entries() == []
    assert bridge.mood_review()["count"] == 0
    assert window.of_type("info"), "清空要有一条看得见的回执"


def test_public_api_exposes_the_mood_panel_wiring(config, tmp_path):
    """js_api 上没转发 = 装好的包里点「🌱 心情」没反应(冻结清单管不到的那一半)。"""
    from minicode.gui_bridge import PublicApi
    bridge, _w = _bridge(config, tmp_path)
    api = PublicApi(bridge)
    for name in ("mood_review", "mood_log", "mood_export", "mood_clear", "mood_help"):
        assert callable(getattr(api, name, None)), name
    assert "博雅楼" in api.mood_help()["text"]


# ------------------------------------------- 标签回读(P6,v0.9.99)

def test_norm_tags_cleans_and_caps():
    assert mood._norm_tags([" 实习 ", "#考试", "", "实习", "x" * 30]) == \
        ["实习", "考试", "x" * 12]          # strip/去#/去重/单条截断
    assert mood._norm_tags("实习") == ["实习"]          # 标量也收
    assert mood._norm_tags(None) == []
    assert len(mood._norm_tags([f"t{i}" for i in range(10)])) == mood.MAX_TAGS


def test_log_stores_tags_and_overwrite_replaces(tmp_path):
    _put(_recent(1), 3, "忙", tags=["实习", "累"])
    rows = mood.list_recent(days=7)
    assert rows[0]["tags"] == ["实习", "累"]
    # 同天覆盖:tags 整体替换(覆盖语义就是"这天换新的"),不悄悄叠加
    mood.log(score=2, note="更忙", day=_recent(1), tags=["实习"])
    rows = mood.list_recent(days=7)
    assert rows[0]["tags"] == ["实习"]


def test_list_and_review_filter_by_tag(tmp_path):
    _put(_recent(2), 2, "实习第一天", tags=["实习"])
    _put(_recent(1), 4, "没事", tags=[])
    _put(_recent(0), 3, "考试周", tags=["考试"])
    got = mood.list_recent(days=7, tag="实习")
    assert [r["date"] for r in got] == [_recent(2)]
    assert mood.list_recent(days=7, tag="#实习")     # 带 # 也能筛到
    text = mood.weekly_review(days=7, tag="实习")["text"]
    assert "实习第一天" in text and "考试周" not in text


def test_mood_tool_tag_passthrough(tmp_path):
    _put(_recent(1), 2, "实习第一天", tags=["实习"])
    out = mood.mood(None, action="list", days=7, tag="实习")
    assert "实习第一天" in out
    out2 = mood.mood(None, action="list", days=7, tag="不存在")
    assert "没有记录" in out2
