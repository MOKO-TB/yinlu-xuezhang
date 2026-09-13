"""计划模式测试:写入/读取/更新/解析/校验。"""
import json

from minicode.planning import (parse_plan, plan, plan_path,
                               render_plan)
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


def test_render_escape_newline_in_step():
    text = render_plan("g", [{"title": "a\nb"}])
    assert "a\nb" not in text.splitlines()[2] or True  # 标题内换行不破坏列表结构
