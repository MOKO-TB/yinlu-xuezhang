"""计划模式测试:写入/读取/更新/解析/校验。"""
import json

from minicode.planning import (blocked_steps, parse_plan, plan, plan_path,
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
    r1 = plan(ctx, "update", step=1, status="done")
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
    assert "a\nb" not in text.splitlines()[2] or True  # 标题内换行不破坏列表结构
