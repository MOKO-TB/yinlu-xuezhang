"""计划模式:复杂任务先列带勾选框的计划(工作区 plans/plan.md),
经用户确认后逐步执行,每完成一步更新状态。文件即状态,可进版本库。

步骤之间可以声明依赖(`depends_on`),用于回答两个问题:
  1. 现在能动手的是哪几步(依赖已满足的 pending 步骤,彼此无依赖即可并行);
  2. 哪几步被上游失败卡住了(必须人工介入,别硬往下做)。
依赖图在写入时校验:未知依赖 / 自依赖 / 环一律拒绝落盘,避免留下跑不动的计划。
"""
from __future__ import annotations

import re
from pathlib import Path

from minicode.tools.base import ToolContext

PLAN_REL = Path("plans") / "plan.md"
STEP_RE = re.compile(r"^- \[([ xX!])\] (\d+)\. (.+)$")
DEP_RE = re.compile(r"^- 依赖:\s*(.+)$")
NOTE_RE = re.compile(r"^- 备注:\s*(.+)$")
MAX_STEPS = 8

# [ ] 待办 · [x] 完成 · [!] 失败(上游失败后由主代理显式标,用来阻断下游)
MARKS = {False: " ", True: "x"}
STATUS_TO_MARK = {"pending": " ", "done": "x", "failed": "!"}
MARK_TO_STATUS = {" ": "pending", "x": "done", "X": "done", "!": "failed"}
VALID_STATUS = set(STATUS_TO_MARK)


def plan_path(ctx: ToolContext) -> Path:
    return ctx.workspace / PLAN_REL


def render_plan(goal: str, steps: list[dict]) -> str:
    """把步骤渲染成计划文件。steps[i] 支持 title/detail/depends_on/status/note。"""
    lines = [f"# 计划:{goal}", ""]
    for i, s in enumerate(steps, start=1):
        mark = STATUS_TO_MARK.get(s.get("status") or "pending", " ")
        line = f"- [{mark}] {i}. {s['title']}"
        if s.get("detail"):
            line += f" —— {s['detail']}"
        lines.append(line)
        deps = sorted({int(d) for d in (s.get("depends_on") or []) if int(d) != i})
        if deps:
            lines.append(f"  - 依赖: {', '.join(str(d) for d in deps)}")
        if s.get("note"):
            lines.append(f"  - 备注: {s['note']}")
    lines += ["", "<!-- 由 plan 工具维护;每完成一步用 action=update 标记状态 -->"]
    return "\n".join(lines) + "\n"


def parse_plan(text: str) -> dict:
    """解析计划文件 → {goal, steps: [{n, title, detail, status, done, depends_on, note}]}。

    老版本的计划文件(没有依赖行/备注行)照常解析,depends_on 为空。
    """
    goal = ""
    steps: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^# 计划:(.+)$", line)
        if m:
            goal = m.group(1).strip()
            continue
        m = STEP_RE.match(line)
        if m:
            mark, num, rest = m.group(1), int(m.group(2)), m.group(3)
            title, _, detail = rest.partition(" —— ")
            status = MARK_TO_STATUS.get(mark, "pending")
            steps.append({"n": num, "title": title.strip(), "detail": detail.strip(),
                          "status": status, "done": status == "done",
                          "depends_on": [], "note": ""})
            continue
        if not steps:                 # 缩进行必须紧跟某个步骤,否则忽略
            continue
        m = DEP_RE.match(line)
        if m:
            steps[-1]["depends_on"] = _parse_ids(m.group(1))
            continue
        m = NOTE_RE.match(line)
        if m:
            steps[-1]["note"] = m.group(1).strip()
    return {"goal": goal, "steps": steps}


def _parse_ids(text: str) -> list[int]:
    out: list[int] = []
    for chunk in re.split(r"[,、\s]+", text or ""):
        if chunk.strip().isdigit():
            out.append(int(chunk.strip()))
    return sorted(set(out))


def validate_deps(steps: list[dict]) -> str | None:
    """校验依赖图;返回错误信息,None 表示合法(可直接落盘)。"""
    ids = {s["n"] for s in steps}
    for s in steps:
        for d in s.get("depends_on") or []:
            if d == s["n"]:
                return f"错误: 第 {s['n']} 步不能依赖自己。"
            if d not in ids:
                return (f"错误: 第 {s['n']} 步依赖了不存在的第 {d} 步"
                        f"(现有步骤: {', '.join(str(i) for i in sorted(ids))})。")
    order, visiting = [], set()

    def visit(n: int, stack: list[int]) -> str | None:
        if n in visiting:
            cyc = stack[stack.index(n):] + [n]
            return "错误: 依赖成环:" + " → ".join(f"第 {i} 步" for i in cyc) + "。"
        if n in order:
            return None
        visiting.add(n)
        for d in sorted(next(s for s in steps if s["n"] == n).get("depends_on") or []):
            err = visit(d, stack + [n])
            if err:
                return err
        visiting.discard(n)
        order.append(n)
        return None

    for s in steps:
        err = visit(s["n"], [])
        if err:
            return err
    return None


def ready_steps(plan: dict) -> list[dict]:
    """依赖已全部完成、可以立即执行的步骤(彼此无依赖时即可并行)。"""
    by_n = {s["n"]: s for s in plan.get("steps") or []}
    out = []
    for s in plan.get("steps") or []:
        if s["status"] != "pending":
            continue
        deps = [by_n[d] for d in s["depends_on"] if d in by_n]
        if all(d["status"] == "done" for d in deps):
            out.append(s)
    return out


def blocked_steps(plan: dict) -> list[dict]:
    """被上游失败卡住的步骤:不能继续做,需要人工介入(改计划或重做上游)。"""
    by_n = {s["n"]: s for s in plan.get("steps") or []}
    out = []
    for s in plan.get("steps") or []:
        if s["status"] != "pending":
            continue
        deps = [by_n[d] for d in s["depends_on"] if d in by_n]
        if any(d["status"] == "failed" for d in deps):
            out.append(s)
    return out


def scheduling_view(plan: dict, limit: int = 3) -> str:
    """给模型看的调度摘要:现在做哪几步、哪几步被卡住。无依赖时返回空串。"""
    steps = plan.get("steps") or []
    if not any(s["depends_on"] for s in steps):
        return ""
    ready, blocked = ready_steps(plan), blocked_steps(plan)
    lines = ["", "[调度视图]"]
    if ready:
        lines.append("* 可执行(依赖已满足): "
                     + ", ".join(f"{s['n']}. {s['title']}" for s in ready[:limit])
                     + ("" if len(ready) <= limit else f" …等 {len(ready)} 步"))
        if len(ready) > 1:
            lines.append("  这几步互不依赖,可以并行派子代理。")
    else:
        lines.append("* 可执行(依赖已满足): 无")
    if blocked:
        lines.append("* 被卡住(上游失败): "
                     + ", ".join(f"{s['n']}. {s['title']}" for s in blocked[:limit]))
        lines.append("  说明上游失败原因,让用户决定是重做上游还是调整计划。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 给界面看的一层

def plan_state(workspace) -> dict:
    """计划面板的数据:当前计划 + 每步的「可执行 / 被卡住」标记。

    没有 plans/plan.md 时给出 has_plan=False(面板显示引导,而不是摆一张空表)。
    只读:这里绝不写文件,面板打开一下不该改动计划状态。
    """
    p = Path(workspace) / PLAN_REL
    empty = {"has_plan": False, "path": PLAN_REL.as_posix(), "goal": "",
             "steps": [], "total": 0, "done": 0,
             "ready": [], "blocked": [], "failed": []}
    if not p.exists():
        return empty
    plan = parse_plan(p.read_text(encoding="utf-8"))
    ready = [s["n"] for s in ready_steps(plan)]
    blocked = [s["n"] for s in blocked_steps(plan)]
    rdy, blk = set(ready), set(blocked)
    steps = [{"n": s["n"], "title": s["title"], "detail": s["detail"],
              "status": s["status"], "depends_on": list(s["depends_on"]),
              "note": s["note"], "ready": s["n"] in rdy, "blocked": s["n"] in blk}
             for s in plan["steps"]]
    return {"has_plan": True, "path": PLAN_REL.as_posix(), "goal": plan["goal"],
            "steps": steps, "total": len(steps),
            "done": sum(1 for s in plan["steps"] if s["status"] == "done"),
            "ready": ready, "blocked": blocked,
            "failed": [s["n"] for s in plan["steps"] if s["status"] == "failed"]}


def dispatch_text(state: dict) -> str:
    """「一键派发」回填给主模型的那句话:按编号依次做依赖已满足的步骤。

    刻意只写"依次执行",不并发派子代理:计划模式的价值是让用户看得见每一步在
    做什么,而并发写文件会互相踩、确认门也会串到别的答案上。真要并行,主模型
    自己会在对话里说明并征求同意。
    """
    steps = list(state.get("steps") or [])
    if not steps:
        return ""
    by_n = {s["n"]: s for s in steps}
    ready = [by_n[n] for n in (state.get("ready") or []) if n in by_n]
    head = f"按计划「{state.get('goal') or '未命名'}」继续。"
    if ready:
        listed = ";".join(
            f"第 {s['n']} 步 {s['title']}"
            + (f"(依赖 {'、'.join(str(d) for d in s['depends_on'])})"
               if s.get("depends_on") else "")
            for s in ready)
        body = f"依赖已满足、现在可以动手的是:{listed}。请按编号顺序依次做完。"
    else:
        body = "现在没有依赖已满足的步骤,请先说明卡在哪里,别硬往下做。"
    tail = ("每完成一步就用 plan(action=update, step=编号, status=done) 标记;"
            "做不成则标 failed 并写清原因。")
    blocked = state.get("blocked") or []
    extra = (f"另有第 {'、'.join(str(n) for n in blocked)} 步被上游失败卡住,"
             "先别动,告诉我该重做上游还是改计划。" if blocked else "")
    return head + body + tail + extra


def notify_plan(ctx: ToolContext) -> None:
    """计划写盘后把最新状态推给界面(图形模式);终端/无界面时什么都不做。"""
    ui = getattr(ctx, "ui", None)
    push = getattr(ui, "push", None)
    if not callable(push):
        return
    try:
        state = plan_state(ctx.workspace)
        # 与 Bridge.get_plan 同构:面板刚收到推送时也要有派发文案,否则按钮会误灰
        push("plan_changed", dispatch_text=dispatch_text(state), **state)
    except Exception:                          # 面板刷新绝不能把工具执行带崩
        pass


def _load_plan(ctx: ToolContext) -> dict | None:
    p = plan_path(ctx)
    if not p.exists():
        return None
    return parse_plan(p.read_text(encoding="utf-8"))


def _write_plan(ctx: ToolContext, plan: dict) -> None:
    p = plan_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    steps = sorted(plan["steps"], key=lambda s: s["n"])
    p.write_text(render_plan(plan["goal"], steps), encoding="utf-8")
    notify_plan(ctx)           # 计划只有这一个落盘出口:write/update 都顺带刷新面板


def _plan_write(ctx: ToolContext, goal: str, steps: list) -> str:
    goal = (goal or "").strip()
    if not goal:
        return "错误: goal(计划目标)不能为空。"
    if not isinstance(steps, list) or not (2 <= len(steps) <= MAX_STEPS):
        return f"错误: steps 必须是 2-{MAX_STEPS} 步的数组(每步一个可独立验证的动作)。"
    clean = []
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict) or not str(s.get("title", "")).strip():
            return f"错误: 第 {i} 步缺少 title。"
        raw_deps = s.get("depends_on") or []
        if not isinstance(raw_deps, list):
            return f"错误: 第 {i} 步的 depends_on 必须是步骤编号数组,如 [1, 2]。"
        deps = []
        for d in raw_deps:
            if isinstance(d, bool) or not str(d).strip().isdigit():
                return f"错误: 第 {i} 步的 depends_on 只接受步骤编号,收到 {d!r}。"
            deps.append(int(d))
        clean.append({"n": i, "title": str(s["title"]).strip(),
                      "detail": str(s.get("detail") or "").strip(),
                      "status": "pending", "done": False,
                      "depends_on": sorted(set(deps)), "note": ""})

    err = validate_deps(clean)
    if err:
        return err + "(计划未写入,请修正依赖后重试。)"

    _write_plan(ctx, {"goal": goal, "steps": clean})
    listed = []
    for s in clean:
        dep = f" ← 依赖 {', '.join(str(d) for d in s['depends_on'])}" if s["depends_on"] else ""
        listed.append(f"  {s['n']}. {s['title']}{dep}")
    return (f"计划已写入 {PLAN_REL.as_posix()}(共 {len(clean)} 步):\n"
            + "\n".join(listed)
            + scheduling_view({"steps": clean})
            + "\n请把计划展示给用户并等待确认;确认后逐步执行,"
              "每完成一步用 action=update 标记 done。")


def _plan_read(ctx: ToolContext) -> str:
    p = plan_path(ctx)
    if not p.exists():
        return "当前没有计划。复杂任务可先用 action=write 创建。"
    text = p.read_text(encoding="utf-8")
    return text + scheduling_view(parse_plan(text))


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
    target["status"] = status
    target["done"] = status == "done"
    if note.strip():
        target["note"] = note.strip()

    _write_plan(ctx, plan)

    done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
    lines = [f"已标记第 {step} 步为 {status}({done_count}/{len(plan['steps'])} 完成)。"]
    if done_count == len(plan["steps"]):
        lines[0] += " 全部步骤已完成!"
    view = scheduling_view(plan)
    if view:
        lines.append(view)
    return "\n".join(lines)


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
