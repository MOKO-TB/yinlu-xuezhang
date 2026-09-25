"""首页「接着做」chips 的规矩:只说真在推进的事,一条都不编(本轮功能 3)。

`suggestions.py` 从前只有话题打分("活动""四级"…哪个词最近出现过),而数据源里明明
躺着任务状态 —— `plans/plan.md` 的调度视图与今天到期的待办。这一行补的是"打开应用
第一眼看到的应该是有什么接着做",不是"可以问什么"。

这份文件主要守三条诚实规矩:
1. 计划与待办都没有 → 空列表(首页那一行整行不出现,而不是摆个"继续加油");
2. 只有真写了钟点的待办才说钟点(没写就不出现「下午」这类猜);
3. 三天开外的事不归这行(那是「倒计时」那一行的活),不在首页重复出现两遍。
"""
from minicode.suggestions import CONTINUE_CAP, continue_chips


def _plan(**kw):
    base = {"has_plan": True, "steps": [], "ready": [], "running": [],
            "failed": [], "blocked": [], "done": 0, "total": 0}
    return {**base, **kw}


def _step(n, title, status="pending"):
    return {"n": n, "title": title, "detail": "", "status": status,
            "depends_on": [], "note": "", "ready": False, "blocked": False}


# ---------------------------------------------------------------- 什么都不做时

def test_no_plan_and_no_todos_yields_no_chips():
    assert continue_chips(_plan(), []) == []
    assert continue_chips({}, None) == []
    assert continue_chips(None, [{"title": "x", "days_left": 0}]) != []


def test_a_plan_without_anything_actionable_says_nothing():
    """全部步骤都做完了,不该再假装"接着做第 1 步"。"""
    plan = _plan(steps=[_step(1, "查资料", "done"), _step(2, "写大纲", "done")])
    assert continue_chips(plan, []) == []


# ---------------------------------------------------------------- 计划状态

def test_running_step_is_the_one_we_resume():
    plan = _plan(steps=[_step(1, "查资料", "done"), _step(2, "生成幻灯片", "in_progress"),
                        _step(3, "排版")],
                 running=[2], ready=[3])
    got = continue_chips(plan, [])
    assert len(got) == 1
    assert "接着做" in got[0]["label"] and "第 2 步" in got[0]["label"]
    assert "生成幻灯片" in got[0]["label"]
    assert got[0]["ask"].startswith("接着做计划的第 2 步")
    assert got[0]["urgent"] is False


def test_when_nothing_is_running_the_ready_step_is_shown():
    plan = _plan(steps=[_step(1, "查资料"), _step(2, "写大纲")], ready=[1, 2])
    got = continue_chips(plan, [])
    assert got and "下一步可做" in got[0]["label"] and "第 1 步" in got[0]["label"]


def test_a_failed_step_outranks_the_next_ready_one():
    """失败了还催"下一步可做",等于让用户跳过没做完的事 —— 那正是要先看的。"""
    plan = _plan(steps=[_step(1, "跑数据", "failed"), _step(2, "画图")],
                 failed=[1], ready=[2])
    got = continue_chips(plan, [])
    assert len(got) == 1 and "失败" in got[0]["label"] and "第 1 步" in got[0]["label"]


def test_step_numbers_not_in_the_plan_are_ignored():
    """running 里出现计划中没有的编号(文件被手改过)不能 KeyError,也不能编一枚。"""
    assert continue_chips(_plan(steps=[_step(1, "甲")], running=[7]), []) == []


# ---------------------------------------------------------------- 待办状态

def test_overdue_ranks_first_and_shows_real_days():
    plan = _plan(steps=[_step(1, "生成幻灯片", "in_progress")], running=[1])
    soon = [{"title": "实验报告", "due": "2026-09-24", "days_left": -2},
            {"title": "读书清单", "due": "2026-09-26", "days_left": 0}]
    got = continue_chips(plan, soon)
    assert [c["kind"] for c in got] == ["due", "due", "plan"]
    assert "已逾期 2 天" in got[0]["label"] and "实验报告" in got[0]["label"]
    assert got[0]["urgent"] is True


def test_only_a_real_clock_makes_an_hour_appear():
    with_clock = continue_chips({}, [{"title": "交表", "due": "2026-09-26",
                                      "due_at": "2026-09-26T17:00:00", "days_left": 0}])
    assert "17:00" in with_clock[0]["label"]
    no_clock = continue_chips({}, [{"title": "交表", "due": "2026-09-26", "days_left": 0}])
    assert no_clock[0]["label"] == "今天要交：交表"     # 没钟点就不该多出任何时间字样
    # 没有钟点就绝不出现「下午/晚上/点」这类猜法
    assert all(w not in no_clock[0]["label"] for w in ("下午", "晚上", "点", "（"))


def test_far_future_todos_do_not_join_this_row():
    """days_left=2 的事归「倒计时」那行,首页不该同一件事出现两遍。"""
    assert continue_chips({}, [{"title": "后天交", "due": "2026-09-28",
                               "days_left": 2}]) == []


def test_items_without_a_title_or_a_broken_days_left_are_dropped():
    soon = [{"title": "", "days_left": 0}, {"days_left": "今天"}, "不是字典",
            {"title": "没有 days_left"}]
    assert continue_chips({}, soon) == []


# ---------------------------------------------------------------- 总量与截断

def test_the_row_is_capped():
    plan = _plan(steps=[_step(1, "甲", "in_progress")], running=[1])
    soon = [{"title": f"逾期{i}", "due": "2026-09-20", "days_left": -1 - i}
            for i in range(6)]
    got = continue_chips(plan, soon)
    assert len(got) == CONTINUE_CAP


def test_long_titles_get_truncated_not_rewritten():
    long = "关于本学期第四周高等数学期中复习安排与三科作业提交时间的详细说明"
    got = continue_chips({}, [{"title": long, "due": "2026-09-26", "days_left": 0}])
    label = got[0]["label"]
    assert label.endswith("…") and long[:24] in label and long[26:] not in label
