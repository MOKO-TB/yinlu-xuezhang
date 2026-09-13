"""计划模式:复杂任务先列带勾选框的计划(工作区 plans/plan.md),
经用户确认后逐步执行,每完成一步更新状态。文件即状态,可进版本库。
"""
from __future__ import annotations

import re
from pathlib import Path

from minicode.tools.base import ToolContext

PLAN_REL = Path("plans") / "plan.md"
STEP_RE = re.compile(r"^- \[([ xX])\] (\d+)\. (.+)$")
VALID_STATUS = {"pending", "done"}


def plan_path(ctx: ToolContext) -> Path:
    return ctx.workspace / PLAN_REL


def render_plan(goal: str, steps: list[dict]) -> str:
    lines = [f"# 计划:{goal}", ""]
    for i, s in enumerate(steps, start=1):
        line = f"- [ ] {i}. {s['title']}"
        if s.get("detail"):
            line += f" —— {s['detail']}"
        lines.append(line)
    lines += ["", "<!-- 由 plan 工具维护;每完成一步用 action=update 标记状态 -->"]
    return "\n".join(lines) + "\n"


def parse_plan(text: str) -> dict:
    """解析计划文件 → {goal, steps: [{n, title, detail, done}]}。"""
    goal = ""
    steps: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"^# 计划:(.+)$", line.strip())
        if m:
            goal = m.group(1).strip()
            continue
        m = STEP_RE.match(line.strip())
        if m:
            done, num, rest = m.group(1) != " ", int(m.group(2)), m.group(3)
            title, _, detail = rest.partition(" —— ")
            steps.append({"n": num, "title": title.strip(),
                          "detail": detail.strip(), "done": done})
    return {"goal": goal, "steps": steps}


def _load_plan(ctx: ToolContext) -> dict | None:
    p = plan_path(ctx)
    if not p.exists():
        return None
    return parse_plan(p.read_text(encoding="utf-8"))


def _plan_write(ctx: ToolContext, goal: str, steps: list) -> str:
    goal = (goal or "").strip()
    if not goal:
        return "错误: goal(计划目标)不能为空。"
    if not isinstance(steps, list) or not (2 <= len(steps) <= 8):
        return "错误: steps 必须是 2-8 步的数组(每步一个可独立验证的动作)。"
    clean = []
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict) or not str(s.get("title", "")).strip():
            return f"错误: 第 {i} 步缺少 title。"
        clean.append({"title": str(s["title"]).strip(),
                      "detail": str(s.get("detail") or "").strip()})

    p = plan_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_plan(goal, clean), encoding="utf-8")
    return (f"计划已写入 {PLAN_REL.as_posix()}(共 {len(clean)} 步):\n"
            + "\n".join(f"  {i}. {s['title']}" for i, s in enumerate(clean, start=1))
            + "\n请把计划展示给用户并等待确认;确认后逐步执行,"
              "每完成一步用 action=update 标记 done。")


def _plan_read(ctx: ToolContext) -> str:
    p = plan_path(ctx)
    if not p.exists():
        return "当前没有计划。复杂任务可先用 action=write 创建。"
    return p.read_text(encoding="utf-8")


def _plan_update(ctx: ToolContext, step: int, status: str, note: str = "") -> str:
    plan = _load_plan(ctx)
    if plan is None:
        return "错误: 当前没有计划。"
    target = next((s for s in plan["steps"] if s["n"] == int(step)), None)
    if target is None:
        return (f"错误: 计划中没有第 {step} 步。"
                f"可用步骤: {', '.join(str(s['n']) for s in plan['steps'])}")
    if status not in VALID_STATUS:
        return f"错误: status 只支持 {'/'.join(sorted(VALID_STATUS))}。"
    target["done"] = status == "done"

    p = plan_path(ctx)
    lines = [f"# 计划:{plan['goal']}", ""]
    for s in sorted(plan["steps"], key=lambda x: x["n"]):
        mark = "x" if s["done"] else " "
        line = f"- [{mark}] {s['n']}. {s['title']}"
        if s.get("detail"):
            line += f" —— {s['detail']}"
        lines.append(line)
        if note.strip():
            lines.append(f"  - 备注: {note.strip()}")
    lines += ["", "<!-- 由 plan 工具维护;每完成一步用 action=update 标记状态 -->"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    done_count = sum(1 for s in plan["steps"] if s["done"])
    tail = ""
    if done_count == len(plan["steps"]):
        tail = " 全部步骤已完成!"
    return f"已标记第 {step} 步为 {status}({done_count}/{len(plan['steps'])} 完成)。{tail}"


def plan(ctx: ToolContext, action: str, goal: str = "", steps: list | None = None,
         step: int = 0, status: str = "done", note: str = "") -> str:
    """plan 工具处理器:write / read / update 三个动作。"""
    if action == "write":
        return _plan_write(ctx, goal, steps or [])
    if action == "read":
        return _plan_read(ctx)
    if action == "update":
        return _plan_update(ctx, step, status, note)
    return f"错误: 未知 action '{action}'。可用: write / read / update。"
