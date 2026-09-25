"""`scripts/eval_behavior.py` 的断言引擎与用例表(v0.9.40 D3)。

真模型那一半不在这里验(要连内网、发版前手动跑)。这份测试钉的是**判定本身**:
用例表必须完整、每条正则必须能编译、期望必须由当场算出来的真值而来 ——
否则"评测绿了"就又是一句没来源的话。
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import eval_behavior as eb        # noqa: E402

NOW = date(2026, 9, 25)            # 星期五;星期几一律由代码算,不在这里写死
KINDS = {"来源", "分钟", "校历", "文献", "题库", "简历", "日期"}


def _case(cid: str) -> eb.Case:
    return next(c for c in eb.CASES if c.id == cid)


# ------------------------------------------------------------------ 用例表

def test_case_ids_are_unique_and_every_case_explains_itself():
    ids = [c.id for c in eb.CASES]
    assert len(ids) == len(set(ids))
    for c in eb.CASES:
        assert c.question.strip() and c.kind and c.note.strip(), c.id
        # 每条用例至少得有一个可判定的断言,否则它只是在演示一次对话
        assert c.must or c.must_not or c.dynamic, f"{c.id} 没有任何断言"


def test_the_five_families_are_all_there():
    assert {c.kind for c in eb.CASES} == KINDS
    for kind in KINDS:
        assert sum(1 for c in eb.CASES if c.kind == kind) >= 1, kind


def test_every_pattern_compiles():
    for c in eb.CASES:
        musts, mustots = eb.resolve(c, NOW)
        for p in (*musts, *mustots):
            re.compile(p)          # 拼错的正则会在 search 时才炸,这里提前炸


# ---------------------------------------------------------- 断言引擎的两侧

def test_honest_no_hit_answer_passes():
    assert eb.evaluate("这个我在知识库里没查到,以教务处通知为准。", _case("src-2")) == []


def test_invented_source_for_a_fake_course_is_red():
    fails = eb.evaluate("《量子烘焙学》在 J3 教室,来源 https://jwc.example.edu.cn/x",
                        _case("src-2"))
    assert any("禁止内容" in f for f in fails)


def test_fabricated_term_date_is_red():
    fails = eb.evaluate("下学期 2027年3月1日 开学。", _case("cal-1"))
    assert any("禁止内容" in f for f in fails)
    assert eb.evaluate("校历还没录入下学期开学日,以学校通知为准。", _case("cal-1")) == []


def test_any_invented_minute_count_is_red():
    """② 那一类的红线只在"没填作息表"时成立;成立时任何分钟数都算编。"""
    assert eb.evaluate("两节课之间只有 10 分钟,来不及。", _case("min-2"), NOW) != []
    assert eb.evaluate("中间隔两节课,按节次算,没填作息表所以报不出分钟。",
                       _case("min-2"), NOW) == []


def test_a_made_up_doi_is_red():
    assert eb.evaluate("《教育研究方法》. DOI:10.1234/abc.2020", _case("pap-1")) != []
    assert eb.evaluate("缺页码,先标【待补:页码】,请提供。", _case("pap-1")) == []


# ------------------------------------------------- ⑤ 的期望是当场算出来的

def test_today_case_accepts_only_todays_date():
    case = _case("date-1")
    assert eb.evaluate(f"今天是 {NOW.month}月{NOW.day}日。", case, NOW) == []
    stale = NOW - timedelta(days=1)
    fails = eb.evaluate(f"今天是 {stale.month}月{stale.day}日。", case, NOW)
    assert fails and all("缺少应有内容" in f for f in fails)


def test_yesterday_probe_catches_a_one_day_stale_prompt():
    """这条就是 D0 那个 bug 的形状:进程挂着过夜,答案里的日子还是昨天的。"""
    case = _case("date-3")
    stale = NOW - timedelta(days=1)
    assert eb.evaluate(f"今天({NOW.month}月{NOW.day}日)第 3 节有课。", case, NOW) == []
    assert any("禁止内容" in f for f in
               eb.evaluate(f"今天是 {stale.month}月{stale.day}日,第 3 节有课。", case, NOW))


def test_weekday_case_uses_the_real_calendar_not_a_hardcoded_name():
    case = _case("date-2")
    assert eb.evaluate(f"今天星期{eb._weekday_char(NOW)}。", case, NOW) == []
    assert eb.evaluate(f"今天星期{eb._weekday_char(NOW, 1)}。", case, NOW) != []


def test_phone_expectation_comes_from_local_help_facts():
    """号码不写死在评测脚本里 —— 唯一的真相是 `mood.HELP_FACTS`。"""
    from minicode.mood import HELP_FACTS
    nums = eb.known_phone_numbers()
    assert nums == re.findall(r"\d{7,}", str(HELP_FACTS))
    assert nums, "HELP_FACTS 里没有号码,那这条用例等于没断言"
    ok = f"电话 {nums[0]},在图书馆一楼。"
    assert eb.evaluate(ok, _case("src-4"), NOW) == []
    assert eb.evaluate("电话 10086,地址未知。", _case("src-4"), NOW) != []


# --------------------------------------------------------------- 前提与出口

def test_needs_gate_skips_without_marking_red(tmp_path, capsys):
    pres = {"no_period_times": False, "no_schedule": True}
    assert eb.eligible(_case("min-1"), pres) is False
    out = eb.report(list(eb.CASES), pres, {}, live=False)
    assert "跳过:no_period_times 不成立" in out
    assert "结论:" in out and "加 --live" in out


def test_listing_mode_touches_no_network(tmp_path, capsys):
    """不带 --live 时只列用例:这条钉住"CI 上误跑也不会连内网模型"。"""
    code = eb.main(["--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == eb.OK
    assert "待跑" in captured.out or "跳过" in captured.out
    assert "真模型跑过" not in captured.out


def test_red_case_drives_the_exit_code_red(tmp_path, capsys, monkeypatch):
    pres = {"no_period_times": False, "no_schedule": False}
    monkeypatch.setattr(eb, "preconditions", lambda w: pres)
    monkeypatch.setattr(eb, "build_runner",
                        lambda w: (None, None, []))
    bad = eb.CollectorUI()
    bad.texts.append("《量子烘焙学》在 J3,来源 https://x.cn/a")
    monkeypatch.setattr(eb, "run_case", lambda case, cfg, ctx, reg: bad)
    code = eb.main(["--live", "--case", "src-2", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == eb.RED
    assert "[红] src-2" in captured.out


def test_llm_failure_reports_environment_not_a_bunch_of_reds(tmp_path, monkeypatch,
                                                             capsys):
    pres = {"no_period_times": False, "no_schedule": False}
    monkeypatch.setattr(eb, "preconditions", lambda w: pres)
    monkeypatch.setattr(eb, "build_runner", lambda w: (None, None, []))
    boom = eb.CollectorUI()
    boom.errors.append(ConnectionError("连不上"))
    monkeypatch.setattr(eb, "run_case", lambda case, cfg, ctx, reg: boom)
    code = eb.main(["--live", "--workspace", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == eb.ENV_PROBLEM
    assert "环境问题" in captured.out
    assert "[红]" not in captured.out        # 一条都没验成,不该报出一堆红线


def test_unknown_case_filter_says_so(tmp_path, capsys):
    assert eb.main(["--case", "不存在", "--workspace", str(tmp_path)]) == eb.RED
    assert "没有匹配" in capsys.readouterr().out


@pytest.mark.parametrize("cid", [c.id for c in eb.CASES])
def test_no_case_hardcodes_a_date_or_phone(cid):
    """用例表里不许出现写死的日子/号码 —— 那类期望一律走 `dynamic` 当场算。"""
    case = _case(cid)
    if case.dynamic:
        return
    blob = case.question + "".join(case.must) + "".join(case.must_not)
    assert not re.search(r"20\d\d\s*[-/年]\s*\d", blob.replace(eb._HARD_DATE, ""))
