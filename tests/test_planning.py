"""计划模式测试:写入/读取/更新/解析/校验 + 面板状态与「一键派发」指令。"""

from minicode.planning import (blocked_steps, dispatch_text, notify_plan,
                               parse_plan, plan, plan_state,
                               ready_steps, render_plan)
from minicode.tools.base import ToolContext


def _ctx(tmp_path):
    return ToolContext(workspace=tmp_path, config=None)


def _write_call(ctx, goal="完成登录功能", steps=None):
    steps = steps or [
        {"title": "设计数据结构", "detail": "确定字段与存储"},
        {"title": "实现后端接口"},
        {"title": "前端联调"},
    ]
    return plan(ctx, "write", goal=goal, steps=steps)


def test_write_creates_checklist_file(tmp_path):
    r = _write_call(_ctx(tmp_path))
    assert "计划已写入" in r and "共 3 步" in r
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "# 计划:完成登录功能" in text
    assert "- [ ] 1. 设计数据结构 —— 确定字段与存储" in text
    assert "- [ ] 3. 前端联调" in text


def test_write_validation(tmp_path):
    ctx = _ctx(tmp_path)
    assert "不能为空" in plan(ctx, "write", goal="", steps=[{"title": "x"}])
    assert "2-8 步" in plan(ctx, "write", goal="g", steps=[{"title": "only"}])
    assert "缺少 title" in plan(ctx, "write", goal="g",
                                steps=[{"title": "a"}, {"detail": "没标题"}])


def test_parse_plan_roundtrip(tmp_path):
    _write_call(_ctx(tmp_path))
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    plan = parse_plan(text)
    assert plan["goal"] == "完成登录功能"
    assert [s["n"] for s in plan["steps"]] == [1, 2, 3]
    assert not plan["steps"][0]["done"]
    assert plan["steps"][0]["detail"] == "确定字段与存储"


def test_update_marks_done_and_counts(tmp_path):
    ctx = _ctx(tmp_path)
    _write_call(ctx)
    r = plan(ctx, "update", step=2, status="done", note="接口联调通过")
    assert "1/3 完成" in r
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "- [x] 2. 实现后端接口" in text
    assert "备注: 接口联调通过" in text
    assert "- [ ] 1. 设计数据结构" in text


def test_update_all_done_message(tmp_path):
    ctx = _ctx(tmp_path)
    _write_call(ctx, goal="小任务", steps=[{"title": "唯一一步"}, {"title": "第二步"}])
    plan(ctx, "update", step=1, status="done")   # 要的是副作用,这一条不断言返回值
    r2 = plan(ctx, "update", step=2, status="done")
    assert "2/2 完成" in r2 and "全部步骤已完成" in r2


def test_update_errors(tmp_path):
    ctx = _ctx(tmp_path)
    assert "没有计划" in plan(ctx, "update", step=1, status="done")
    _write_call(ctx)
    assert "没有第 5 步" in plan(ctx, "update", step=5, status="done")
    assert "status 只支持" in plan(ctx, "update", step=1, status="doing")


def test_read_without_plan(tmp_path):
    assert "没有计划" in plan(_ctx(tmp_path), "read")


def test_rewrite_replaces_old_plan(tmp_path):
    ctx = _ctx(tmp_path)
    _write_call(ctx, goal="旧计划")
    r = _write_call(ctx, goal="新计划", steps=[{"title": "一"}, {"title": "二"}])
    assert "共 2 步" in r
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "新计划" in text and "旧计划" not in text


# ---------------------------------------------------------------- 依赖图


_DEP_STEPS = [
    {"title": "设计数据结构", "detail": "确定字段与存储"},
    {"title": "实现后端接口", "depends_on": [1]},
    {"title": "前端联调", "depends_on": [2]},
    {"title": "写回归测试", "depends_on": [1]},
]


def test_write_records_depends_on_and_parses_back(tmp_path):
    ctx = _ctx(tmp_path)
    r = plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    assert "计划已写入" in r and "共 4 步" in r
    assert "2. 实现后端接口 ← 依赖 1" in r

    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "  - 依赖: 2" in text
    parsed = parse_plan(text)
    assert [s["depends_on"] for s in parsed["steps"]] == [[], [1], [2], [1]]
    assert all(s["status"] == "pending" for s in parsed["steps"])


def test_write_rejects_bad_dependency_graph(tmp_path):
    ctx = _ctx(tmp_path)
    missing = plan(ctx, "write", goal="g", steps=[
        {"title": "a"}, {"title": "b", "depends_on": [9]}])
    assert "不存在的第 9 步" in missing
    self_dep = plan(ctx, "write", goal="g", steps=[
        {"title": "a"}, {"title": "b", "depends_on": [2]}])
    assert "不能依赖自己" in self_dep
    cycle = plan(ctx, "write", goal="g", steps=[
        {"title": "a", "depends_on": [3]},
        {"title": "b", "depends_on": [1]},
        {"title": "c", "depends_on": [2]}])
    assert "依赖成环" in cycle
    was_bad = plan(ctx, "write", goal="g", steps=[
        {"title": "a"}, {"title": "b", "depends_on": ["x"]}])
    assert "只接受步骤编号" in was_bad
    assert not (tmp_path / "plans" / "plan.md").exists()   # 校验失败不落盘


def test_ready_and_blocked_track_the_chain(tmp_path):
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    parsed = parse_plan(text)

    # 起步:只有第 1 步没依赖 → 唯一可执行
    assert [s["n"] for s in ready_steps(parsed)] == [1]
    assert blocked_steps(parsed) == []

    # 第 1 步完成 → 第 2、4 步同时解锁(可并行)
    plan(ctx, "update", step=1, status="done")
    parsed = parse_plan((tmp_path / "plans" / "plan.md").read_text(encoding="utf-8"))
    assert [s["n"] for s in ready_steps(parsed)] == [2, 4]

    # 第 2 步失败 → 下游第 3 步被卡住,第 4 步仍可做
    r = plan(ctx, "update", step=2, status="failed", note="接口契约没定下来")
    assert "[调度视图]" in r and "被卡住" in r
    parsed = parse_plan((tmp_path / "plans" / "plan.md").read_text(encoding="utf-8"))
    assert [s["n"] for s in blocked_steps(parsed)] == [3]
    assert [s["n"] for s in ready_steps(parsed)] == [4]
    assert "[!] 2. 实现后端接口" in (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")


def test_read_shows_scheduling_view_only_with_deps(tmp_path):
    ctx = _ctx(tmp_path)
    _write_call(ctx)                       # 老式无依赖计划
    assert "[调度视图]" not in plan(ctx, "read")
    plan(ctx, "write", goal="有依赖", steps=_DEP_STEPS)
    out = plan(ctx, "read")
    assert "[调度视图]" in out and "可执行" in out


def test_legacy_plan_without_deps_still_works(tmp_path):
    """v0.9.0 写的计划文件(没有依赖/备注行)必须还能读能更新。"""
    ctx = _ctx(tmp_path)
    p = tmp_path / "plans" / "plan.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# 计划:老计划\n\n- [ ] 1. 第一步\n- [x] 2. 第二步\n", encoding="utf-8")
    parsed = parse_plan(p.read_text(encoding="utf-8"))
    assert [s["depends_on"] for s in parsed["steps"]] == [[], []]
    assert parsed["steps"][1]["status"] == "done"
    assert ready_steps(parsed)[0]["n"] == 1
    assert "没有第 5 步" in plan(ctx, "update", step=5, status="done")
    assert "2/2 完成" in plan(ctx, "update", step=1, status="done")
    assert "- [x] 2. 第二步" in p.read_text(encoding="utf-8")   # 老状态没被抹掉


def test_render_escape_newline_in_step():
    text = render_plan("g", [{"title": "a\nb"}])
    # 换行被单行化:清单行保持完整(or True 恒真掩盖了四年 —— 标题带换行时
    # 清单真被拆断过,渲染出来是 "- [ ] 1. a" 后面悬一行孤立的 "b")
    lines = text.splitlines()
    assert lines[2] == "- [ ] 1. a b"


def test_render_one_line_also_applies_to_goal_detail_note():
    text = render_plan("目\n标", [{"title": "t", "detail": "d1\nd2",
                                   "note": "n1\nn2"}])
    assert "# 计划:目 标" in text
    assert "d1 d2" in text and "n1 n2" in text


# ---------------------------------------------------------------- 面板(v0.9.24)


class _UI:
    """假界面:记下每次 push;raise=True 用来测"刷面板失败不该把工具带崩"。"""

    def __init__(self, raise_it=False):
        self.events = []
        self.raise_it = raise_it

    def push(self, type_, **payload):
        self.events.append((type_, payload))
        if self.raise_it:
            raise RuntimeError("窗口已经关了")


def test_plan_state_without_a_file_is_a_guide_not_an_empty_table(tmp_path):
    st = plan_state(tmp_path)
    assert st["has_plan"] is False and st["steps"] == [] and st["total"] == 0
    assert st["path"] == "plans/plan.md"          # 前端那句引导要指得准
    assert dispatch_text(st) == ""                # 没计划就别派发


def test_plan_state_labels_ready_and_blocked_per_step(tmp_path):
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    st = plan_state(tmp_path)
    assert (st["goal"], st["total"], st["done"]) == ("完成登录功能", 4, 0)
    assert st["ready"] == [1] and st["blocked"] == []
    assert [s["ready"] for s in st["steps"]] == [True, False, False, False]

    plan(ctx, "update", step=1, status="done")
    plan(ctx, "update", step=2, status="failed", note="接口契约没定下来")
    st = plan_state(tmp_path)
    assert st["done"] == 1 and st["failed"] == [2]
    assert st["ready"] == [4] and st["blocked"] == [3]
    by = {s["n"]: s for s in st["steps"]}
    assert by[1]["status"] == "done" and by[1]["ready"] is False   # 做完的不算"可做"
    assert by[3]["blocked"] is True and by[3]["ready"] is False
    assert by[2]["note"] == "接口契约没定下来"          # 备注得跟着上面板


def test_plan_state_is_read_only(tmp_path):
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="g", steps=_DEP_STEPS)
    before = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    plan_state(tmp_path)
    assert (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8") == before


def test_dispatch_text_lists_ready_steps_in_dependency_order(tmp_path):
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    t = dispatch_text(plan_state(tmp_path))
    assert "完成登录功能" in t
    assert "第 1 步 设计数据结构" in t and "第 2 步" not in t   # 只派现在能做的
    assert "按编号顺序依次做完" in t
    assert "plan(action=update" in t                    # 每做完一步要标记,面板才会动
    assert "并行" not in t                              # 默认不并发派子代理


def test_dispatch_text_names_the_blocked_steps(tmp_path):
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="g", steps=_DEP_STEPS)
    plan(ctx, "update", step=1, status="failed", note="上游没了")
    t = dispatch_text(plan_state(tmp_path))
    assert "现在没有依赖已满足的步骤" in t
    assert "被上游失败卡住" in t and "别硬往下做" in t


def test_dispatch_text_for_a_plan_without_deps_offers_everything(tmp_path):
    ctx = _ctx(tmp_path)
    _write_call(ctx)                                    # 老式三步、无依赖
    st = plan_state(tmp_path)
    assert st["ready"] == [1, 2, 3]
    t = dispatch_text(st)
    assert all(f"第 {n} 步" in t for n in (1, 2, 3))


def test_writing_a_plan_refreshes_the_panel(tmp_path):
    ui = _UI()
    ctx = ToolContext(workspace=tmp_path, config=None, ui=ui)
    plan(ctx, "write", goal="g", steps=_DEP_STEPS)
    plan(ctx, "update", step=1, status="done")
    types = [t for t, _ in ui.events]
    assert types == ["plan_changed", "plan_changed"]    # write 与 update 各推一次
    last = ui.events[-1][1]
    assert last["done"] == 1 and last["ready"] == [2, 4]


def test_a_broken_ui_never_breaks_the_plan_tool(tmp_path):
    """窗口正在关闭时 push 会抛:计划照样落盘,工具照样回正常结果。"""
    ctx = ToolContext(workspace=tmp_path, config=None, ui=_UI(raise_it=True))
    r = plan(ctx, "write", goal="g", steps=[{"title": "a"}, {"title": "b"}])
    assert "计划已写入" in r
    assert (tmp_path / "plans" / "plan.md").exists()


def test_notify_plan_without_a_ui_is_a_noop(tmp_path):
    ctx = _ctx(tmp_path)                                 # 终端/CLI:ui 为空
    notify_plan(ctx)                                     # 不该抛
    plan(ctx, "write", goal="g", steps=[{"title": "a"}, {"title": "b"}])
    assert plan_state(ctx.workspace)["total"] == 2


# ------------------------------------------------ 进行中状态与内联卡片(v0.9.33)


def test_in_progress_roundtrips_through_the_file(tmp_path):
    """`[/]` 是这一轮新记号:写进去要读得回来,而且不能被当成"已完成"或"待办"。"""
    ctx = _ctx(tmp_path)
    _write_call(ctx)
    r = plan(ctx, "update", step=1, status="in_progress")
    assert "已标记第 1 步为 in_progress(0/3 完成)" in r
    text = (tmp_path / "plans" / "plan.md").read_text(encoding="utf-8")
    assert "- [/] 1. 设计数据结构" in text
    parsed = parse_plan(text)
    assert parsed["steps"][0]["status"] == "in_progress"
    assert parsed["steps"][0]["done"] is False          # 半途的绝不冒充做完的
    # 标别的步骤不能顺手把这一行的"进行中"抹掉
    plan(ctx, "update", step=2, status="done")
    after = parse_plan((tmp_path / "plans" / "plan.md").read_text(encoding="utf-8"))
    assert [s["status"] for s in after["steps"]] == ["in_progress", "done", "pending"]


def test_in_progress_is_not_ready(tmp_path):
    """正在做的那一步不能再算进「现在能动」:两栏同时亮,用户就不知道到底在做什么。"""
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    plan(ctx, "update", step=1, status="in_progress")
    st = plan_state(tmp_path)
    assert st["running"] == [1] and st["ready"] == []
    assert st["blocked"] == [] and st["failed"] == []
    # 「进行中」的上游不算把下游卡住:只有 failed 才阻断,否则做一半看起来像做坏了
    assert st["done"] == 0 and st["total"] == 4
    plan(ctx, "update", step=1, status="done")
    st = plan_state(tmp_path)
    assert st["running"] == [] and st["ready"] == [2, 4]   # 标完 done 才把下游放出来


def test_a_fresh_plan_has_an_empty_running_list(tmp_path):
    """没有 plans/plan.md 时也要带 running:前端拿它拼脚注,缺字段就是一次 undefined。"""
    st = plan_state(tmp_path)
    assert st["running"] == []
    ctx = _ctx(tmp_path)
    _write_call(ctx)
    assert plan_state(tmp_path)["running"] == []


def test_scheduling_view_reports_running_even_without_dependencies(tmp_path):
    """老早退条件只看依赖,于是"无依赖但正在做第 2 步"什么都不报 —— 卡片脚注就空了。"""
    ctx = _ctx(tmp_path)
    _write_call(ctx)                                     # 三步、无依赖
    assert "[调度视图]" not in plan(ctx, "read")          # 没人动手时仍然没什么可报
    r = plan(ctx, "update", step=2, status="in_progress")
    assert "[调度视图]" in r
    assert "进行中(已标 [/]" in r and "2. 实现后端接口" in r
    assert "可执行(依赖已满足): 1. 设计数据结构, 3. 前端联调" in r   # 另外两步照旧列出


def test_running_step_is_told_first_when_dispatching(tmp_path):
    """「一键派发」的文案得先让模型把手上那一步收掉,否则它会又开一条新战线。"""
    ctx = _ctx(tmp_path)
    _write_call(ctx)                                     # 三步、无依赖
    plan(ctx, "update", step=1, status="in_progress")
    st = plan_state(tmp_path)
    assert st["running"] == [1] and st["ready"] == [2, 3]
    t = dispatch_text(st)
    assert "第 1 步已标了进行中" in t and "先把它做完并标 done" in t
    assert t.index("已标了进行中") < t.index("现在可以动手")
    assert t.index("第 1 步") < t.index("第 2 步")        # 挂着的排最前,不是补在结尾


def test_dispatch_still_names_the_running_step_when_nothing_is_ready(tmp_path):
    """依赖链上游正在做时「可执行」是空的 —— 那句话更不能省,否则用户只能猜在做什么。"""
    ctx = _ctx(tmp_path)
    plan(ctx, "write", goal="完成登录功能", steps=_DEP_STEPS)
    plan(ctx, "update", step=1, status="in_progress")
    t = dispatch_text(plan_state(tmp_path))
    assert "第 1 步已标了进行中" in t
    assert "没有依赖已满足的步骤" in t


def test_marking_in_progress_tells_the_model_to_close_it_out(tmp_path):
    """界面上转着圈的那一行,如果模型转头就忘了标 done,就是在骗用户。"""
    ctx = _ctx(tmp_path)
    _write_call(ctx)
    r = plan(ctx, "update", step=1, status="in_progress")
    assert "界面上这一行现在显示为「进行中」" in r
    assert "别让它一直挂在进行中" in r
    # done 那条不必多这句嘴
    assert "界面上这一行" not in plan(ctx, "update", step=1, status="done")


def test_in_progress_reaches_the_ui_push(tmp_path):
    """卡片是照着 plan_changed 的 payload 画的:running 没推出去 = 卡片永远不转圈。"""
    ui = _UI()
    ctx = ToolContext(workspace=tmp_path, config=None, ui=ui)
    plan(ctx, "write", goal="g", steps=_DEP_STEPS)
    plan(ctx, "update", step=1, status="in_progress")
    payload = ui.events[-1][1]
    assert payload["running"] == [1] and payload["ready"] == []
    assert payload["steps"][0]["status"] == "in_progress"
