"""计划模式:复杂任务先列带勾选框的计划(工作区 plans/plan.md),
经用户确认后逐步执行,每完成一步更新状态。文件即状态,可进版本库。

步骤之间可以声明依赖(`depends_on`),用于回答两个问题:
  1. 现在能动手的是哪几步(依赖已满足的 pending 步骤,彼此无依赖即可并行);
  2. 哪几步被上游失败卡住了(必须人工介入,别硬往下做)。
依赖图在写入时校验:未知依赖 / 自依赖 / 环一律拒绝落盘,避免留下跑不动的计划。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from minicode.tools.base import ToolContext

PLAN_REL = Path("plans") / "plan.md"
STEP_RE = re.compile(r"^- \[([ /xX!])\] (\d+)\. (.+)$")
DEP_RE = re.compile(r"^- 依赖:\s*(.+)$")
NOTE_RE = re.compile(r"^- 备注:\s*(.+)$")
MAX_STEPS = 8

# 步骤的验收判据(V1):`verify:` 跟着标题走,写在 detail 尾巴上 —— 不新增文件、
# 不给 plan.md 加第二套方言,老计划文件里没有它就当没这回事(解析零变化)。
VERIFY_RE = re.compile(r"(?:^|[\s,;，；])verify\s*[:：]\s*(.+)$", re.IGNORECASE)
# 跑过哪些命令这本账只在内存里,且只留最近这么多条:验收输出不进留档(与 B0/B1、
# 后台状态同一取舍),而"最近跑过没跑过"这个问题本身只需要尾巴。
VERIFY_RUNS_LIMIT = 30

# [ ] 待办 · [/] 进行中 · [x] 完成 · [!] 失败(上游失败后由主代理显式标,用来阻断下游)
# "/" 这个记号不是随手挑的:它是单字符,parse_plan 那套正则照样吃得下;
# 而"斜杠填一半"的视觉正好是"这步正在做"。别的渲染器不认识它也只会显示成未勾,
# 不会把已完成/已失败的两类骗成人。
MARKS = {False: " ", True: "x"}
STATUS_TO_MARK = {"pending": " ", "in_progress": "/", "done": "x", "failed": "!"}
MARK_TO_STATUS = {" ": "pending", "/": "in_progress",
                  "x": "done", "X": "done", "!": "failed"}
VALID_STATUS = set(STATUS_TO_MARK)


def plan_path(ctx: ToolContext) -> Path:
    return ctx.workspace / PLAN_REL


# ------------------------------------------------- 验收命令的执行账(V1 的硬闸依据)


def verify_of(text: str) -> str:
    """从步骤文本里取出 `verify:` 后面那条命令;没有就返回空串(留空是正常态)。"""
    m = VERIFY_RE.search(text or "")
    return " ".join((m.group(1) or "").split()) if m else ""


def note_verify_run(ctx: ToolContext, command: str, code: int | None,
                    output: str = "") -> None:
    """把一次**跑完了的** bash 命令记进这本内存账 —— 它是"这一步真被验证过"的唯一凭据。

    为什么放在 bash 而不是 plan 里:验收命令跑的就是用户自己也能敲的那条路,
    权限门、沙箱、超时全都原样生效(V1 不给验收开任何捷径)。两条入口都记账:
    前台跑完那一刻,以及**后台任务被 poll 到结束的那一刻**(`shell._bg_note_verify`)。
    还没结束的和超时的不记 —— 它们没有"退出码是几"这个结论,记进去等于给"跑过了"开门。
    不落盘:输出留在会话里已经是历史,再抄一份进 KV 前缀只会白占上下文。
    """
    cmd = " ".join(str(command or "").split())
    if not cmd:
        return
    runs = getattr(ctx, "verify_runs", None)
    if runs is None:
        runs = ctx.verify_runs = []      # ToolContext 是普通 dataclass,子代理那份是派生物
    runs.append({"command": cmd, "code": code,
                 "at": time.strftime("%H:%M:%S"),
                 "tail": " ".join(str(output or "").split())[-300:]})
    del runs[:-VERIFY_RUNS_LIMIT]
    if code is not None:
        _mark_plan_verify(ctx, cmd, code)      # V2(v0.9.102):结论写进计划文件


def _mark_plan_verify(ctx: ToolContext, command: str, code: int) -> None:
    """V2:这次 bash 命中某步的验收判据 → 把 ✓/✗ + 时刻写进该步的**备注**。

    计划文件是唯一落盘处,对话里的计划卡与右侧监控段读的都是它 ——
    一次执行,三处同步,不另设第二份状态。整段兜异常:备注写不进去
    绝不能影响 bash 那条回话(它自己已经带着退出码与输出尾了)。
    """
    try:
        plan = _load_plan(ctx)
        if plan is None:
            return
        hit = next((s for s in plan["steps"]
                    if " ".join(verify_of(s["title"] + " " + s["detail"]).split()) == command),
                   None)
        if hit is None:
            return                   # 普通命令,不碰任何一步
        mark = ("verify ✓ " + time.strftime("%H:%M:%S") if code == 0
                else f"verify ✗ {time.strftime('%H:%M:%S')}(退出码 {code})")
        old_note = re.sub(r"verify [✓✗][^|;]*[|;]?\s*", "",
                          str(hit.get("note") or "")).strip()
        hit["note"] = (mark + (" | " + old_note if old_note else ""))[:300]
        _write_plan(ctx, plan)
    except Exception:
        pass


def find_verify_run(ctx: ToolContext, command: str) -> dict | None:
    """这条验收命令最近一次跑完的记录。按归一化后的整条命令严格相等 ——
    模型跑的是 `pytest -q x --tb=short` 而判据写的是 `pytest -q x`,那不算验过。"""
    want = " ".join(str(command or "").split())
    for rec in reversed(getattr(ctx, "verify_runs", None) or []):
        if rec["command"] == want:
            return rec
    return None


def _with_verify(detail: str, verify: str) -> str:
    """把独立的 `verify` 字段拼进 detail 尾巴 —— plan.md 只有"标题 —— detail"一种方言。

    detail 里已经写了 verify 就以它为准(同一句话不写两遍),不静默覆盖也不重复。
    """
    want = " ".join(str(verify or "").split())
    if not want or verify_of(detail):
        return detail
    return (detail + " " if detail else "") + f"verify: {want}"


def render_plan(goal: str, steps: list[dict]) -> str:
    """把步骤渲染成计划文件。steps[i] 支持 title/detail/depends_on/status/note。

    标题/detail/note 一律**单行化**(换行折叠成空格):模型给的步骤标题可能
    带换行,直拼会让清单行断成两截、后面悬一行孤立文本 —— 列表结构就坏了
    (test_render_escape_newline_in_step 钉的就是这个,当年被一条恒真断言
    放了四年)。
    """
    one_line = lambda t: " ".join(str(t or "").split())  # noqa: E731
    lines = [f"# 计划:{one_line(goal)}", ""]
    for i, s in enumerate(steps, start=1):
        mark = STATUS_TO_MARK.get(s.get("status") or "pending", " ")
        line = f"- [{mark}] {i}. {one_line(s['title'])}"
        if s.get("detail"):
            line += f" —— {one_line(s['detail'])}"
        lines.append(line)
        deps = sorted({int(d) for d in (s.get("depends_on") or []) if int(d) != i})
        if deps:
            lines.append(f"  - 依赖: {', '.join(str(d) for d in deps)}")
        if s.get("note"):
            lines.append(f"  - 备注: {one_line(s['note'])}")
    lines += ["", "<!-- 由 plan 工具维护;每完成一步用 action=update 标记状态 -->"]
    return "\n".join(lines) + "\n"


def parse_plan(text: str) -> dict:
    """解析计划文件 → {goal, steps: [{n, title, detail, status, done, depends_on, note, verify}]}。

    老版本的计划文件(没有依赖行/备注行/验收判据)照常解析,depends_on 与 verify 为空。
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
                          "depends_on": [], "note": "",
                          # 判据可能写在标题那截(模型直接把它当步骤文字打出来),
                          # 所以整行都搜,不只看 detail。
                          "verify": verify_of(rest)})
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
    """给模型看的调度摘要:哪几步在进行中、现在做哪几步、哪几步被卡住。
    既没有依赖也没有进行中的步骤时返回空串(那就没什么可报的)。"""
    steps = plan.get("steps") or []
    running = [s for s in steps if s["status"] == "in_progress"]
    if not any(s["depends_on"] for s in steps) and not running:
        return ""
    ready, blocked = ready_steps(plan), blocked_steps(plan)
    lines = ["", "[调度视图]"]
    if running:
        lines.append("* 进行中(已标 [/],先把这几步做完再标 done): "
                     + ", ".join(f"{s['n']}. {s['title']}" for s in running[:limit])
                     + ("" if len(running) <= limit else f" …等 {len(running)} 步"))
    if ready:
        lines.append("* 可执行(依赖已满足): "
                     + ", ".join(f"{s['n']}. {s['title']}" for s in ready[:limit])
                     + ("" if len(ready) <= limit else f" …等 {len(ready)} 步"))
        if len(ready) > 1:
            lines.append("  这几步互不依赖,可以并行派子代理(task 带上 step=编号,"
                         "这一步会标成进行中并在备注里记下派了哪几路)。")
    else:
        lines.append("* 可执行(依赖已满足): 无")
    with_v = [s for s in steps if s.get("verify") and s["status"] != "done"]
    if with_v:
        lines.append("* 带验收判据(标 done 前必须用 bash 原样跑一遍,整条命令一字不差): "
                     + ", ".join(f"{s['n']}.`{s['verify']}`" for s in with_v[:limit])
                     + ("" if len(with_v) <= limit else f" …等 {len(with_v)} 步"))
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
             "steps": [], "total": 0, "done": 0, "verify": [],
             "ready": [], "blocked": [], "failed": [], "running": []}
    if not p.exists():
        return empty
    plan = parse_plan(p.read_text(encoding="utf-8"))
    ready = [s["n"] for s in ready_steps(plan)]
    blocked = [s["n"] for s in blocked_steps(plan)]
    rdy, blk = set(ready), set(blocked)
    steps = [{"n": s["n"], "title": s["title"], "detail": s["detail"],
              "status": s["status"], "depends_on": list(s["depends_on"]),
              "note": s["note"], "verify": s.get("verify", ""),
              "ready": s["n"] in rdy, "blocked": s["n"] in blk}
             for s in plan["steps"]]
    return {"has_plan": True, "path": PLAN_REL.as_posix(), "goal": plan["goal"],
            "steps": steps, "total": len(steps),
            "verify": [s["n"] for s in plan["steps"]
                       if s.get("verify") and s["status"] != "done"],
            "done": sum(1 for s in plan["steps"] if s["status"] == "done"),
            "ready": ready, "blocked": blocked,
            "running": [s["n"] for s in plan["steps"]
                        if s["status"] == "in_progress"],
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
    running = [by_n[n] for n in (state.get("running") or []) if n in by_n]
    head = f"按计划「{state.get('goal') or '未命名'}」继续。"
    if running:
        head += ("第 " + "、".join(str(s["n"]) for s in running)
                 + " 步已标了进行中,先把它做完并标 done,再动别的。")
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
            "做不成则标 failed 并写清原因。派子代理做某一步时给 task 传 step=编号,"
            "计划里会记下这一步正由哪几路在做(报告回来只写事实,成没成仍由你标)。")
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


# ------------------------------------------------------- 与子代理派发对上号(task step=)

def dispatch_start(ctx: ToolContext, step, kinds: list[str]) -> str | None:
    """把计划第 `step` 步标成进行中、备注记下派了哪几档;返回错误文案或 None。

    为什么不直接把这一步标 done:子代理回来的是**一段转述**,不是这一步做成的证据。
    这一步只负责"计划里看得出谁在做这件事",成败仍由主代理按报告如实标。
    已经 failed/done 的步骤拒绝派活:上游失败还往下派,就是在替用户跳过失败。
    依赖没做完的步骤同样拒绝(与 `ready_steps` 一个口径):否则面板上会出现一个
    上游还空着、自己却「进行中」的步,调度视图那套「可执行/被卡住」就白算了。
    同一步被派两次时**后一次覆盖备注** —— 计划文件是状态不是历史,要留痕去看
    监控栏的时间线。
    """
    plan = _load_plan(ctx)
    if plan is None:
        return "错误: 当前没有计划,step 参数用不上(先 plan action=write 建计划)。"
    n = _as_step_no(step)
    if isinstance(n, str):
        return n
    target = next((s for s in plan["steps"] if s["n"] == n), None)
    if target is None:
        return (f"错误: 计划中没有第 {n} 步。"
                f"可用步骤: {', '.join(str(s['n']) for s in plan['steps'])}")
    if target["status"] in ("failed", "done"):
        return (f"错误: 第 {n} 步已标 {target['status']},不在它身上派子代理。"
                "要重做请先 plan action=update 改回 pending,或换一步。")
    by_n = {s["n"]: s for s in plan["steps"]}
    unmet = [d for d in target["depends_on"]
             if by_n.get(d, {}).get("status") != "done"]
    if unmet:
        return (f"错误: 第 {n} 步的依赖还没做完(第 "
                + "、".join(str(d) for d in unmet)
                + " 步),不在它身上派子代理 —— 先做上游,或改计划。")
    target["status"] = "in_progress"
    target["done"] = False
    target["note"] = f"子代理在跑:{'、'.join(kinds)}({len(kinds)} 路)"
    _write_plan(ctx, plan)
    return None


def dispatch_end(ctx: ToolContext, step, facts: list[str]) -> None:
    """把这一批子代理的**真实结果摘要**写进该步备注(只说事实,不改状态)。

    `facts` 由 task 侧按跑出来的东西拼(每路档名+报告字数、超时几路),
    这里不猜、不补:没有的事实就不写,备注宁可短也不要出现没发生过的话。
    写盘失败只吞掉(面板少刷一次),绝不让它把子代理的结果带崩。
    """
    try:
        plan = _load_plan(ctx)
        if plan is None or not facts:
            return
        n = _as_step_no(step)
        if isinstance(n, str):
            return
        target = next((s for s in plan["steps"] if s["n"] == n), None)
        if target is None:
            return
        target["note"] = "子代理已回:" + ";".join(facts)
        _write_plan(ctx, plan)
    except Exception:
        pass


def _as_step_no(step) -> int | str:
    """步骤编号归一:返回 int,或返回一句错误文案(bool 与小数都不认)。"""
    if isinstance(step, bool) or not str(step).strip().isdigit():
        return f"错误: step 只接受计划里的步骤编号(整数),收到 {step!r}。"
    return int(str(step).strip())


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


REVIEW_MODES = ("approve", "edit", "revise", "unanswered")


def _apply_review_edit(ctx: ToolContext, verdict: dict) -> str:
    """「改一条」:把改后的那一句写回 plan.md,再告诉模型按新单子走。

    只动**标题**:状态归 `action=update`,依赖与整体顺序归重写一版 —— 一颗按钮
    同时改三处,后面查"这一步为什么变成这样"就查不清了。
    """
    text = str(verdict.get("text") or "").strip()
    try:
        n = int(verdict.get("step"))
    except (TypeError, ValueError):
        return "\n(「改一条」没带上步骤编号,这次改动没落盘 —— 请直接说要改第几步。)"
    if not text:
        return f"\n(第 {n} 步改成了空话,这次改动没落盘 —— 空标题的计划没法执行。)"
    p = plan_path(ctx)
    if not p.exists():
        return "\n(计划文件不在,这一处改动没处落。)"
    plan = parse_plan(p.read_text(encoding="utf-8"))
    hit = [s for s in plan["steps"] if s["n"] == n]
    if not hit:
        return f"\n(第 {n} 步不在这张计划里,改动没落盘。现有:{[s['n'] for s in plan['steps']]})"
    old = hit[0]["title"]
    hit[0]["title"] = text[:200]
    _write_plan(ctx, plan)
    return (f"\n用户把第 {n} 步从「{old}」改成「{hit[0]['title']}」,已按改后的写回 "
            f"{PLAN_REL.as_posix()}。请按**改后的这一版**执行,不要照你原来那份走。")


def _set_plan_lock(ctx: ToolContext, on: bool, goal: str = "") -> bool:
    """上/解规划锁(v0.9.129)。状态住在 `ctx.sandbox` 那一份共享对象上 ——
    子代理各自新建权限门、但共用父会话的 sandbox,所以锁放在这里才传得下去
    (放 gate 上等于给写型子代理留一条后门:主代理锁着,派出去那一路照样能写)。

    返回"这一次调用是不是真的改变了状态",调用方据此决定要不要说一声。
    没有 sandbox 的形态(测试桩 / 某些内部工具路径)一律算"锁不上",也不报错 ——
    这把锁不该让没有权限门的场景崩掉。
    """
    sb = getattr(ctx, "sandbox", None)
    if sb is None or not hasattr(sb, "lock_plan"):
        return False
    return sb.lock_plan(goal) if on else sb.unlock_plan()


def review_plan(ctx: ToolContext, goal: str, steps: list) -> str:
    """计划刚写完:把**整张单子**摊给人核对(批准 / 改一条 / 重排)。

    与 `ask_user` 分家(v0.9.114 用户点名):那是问路,这是批单。
    没有可弹卡的一侧(命令行 / 网页版 / 桥没接)就**直接放行** —— 闸门不许
    把不需要它的人卡住;等不到回答时按"没确认"收口,而不是替他批准。
    """
    review = getattr(getattr(ctx, "ui", None), "plan_review", None)
    if not callable(review):
        return ""
    try:
        verdict = review({"goal": goal, "steps": steps}) or {}
    except Exception as exc:                    # 卡弹不出来不该把整轮带崩
        return f"\n(核对单没弹成:{type(exc).__name__};先把计划讲给用户,别直接开工。)"
    mode = str(verdict.get("mode") or "unanswered")
    if mode == "approve":
        # 批准这一步**真的解那道锁**(v0.9.129)。以前这里只回一句话给模型看,
        # 而"没批准也不许写"这件事在 permission 里没有任何一处执行 —— 那等于
        # 一张装饰性的核对单。解铃还须系铃人:锁是 `plan(action=write)` 上的。
        _set_plan_lock(ctx, False, goal)
        return "\n用户已在界面上**批准这张计划**,可以按顺序开工(每完成一步照旧 update 标记)。"
    if mode == "edit":
        return _apply_review_edit(ctx, verdict)
    if mode == "revise":
        note = str(verdict.get("note") or "").strip()
        return ("\n用户点了「重排」:这张单子**没通过**。请照他写的意见重新 "
                "plan(action=write) 一版再走一遍核对 —— 这一轮别开工。"
                + (f"\n他写的意见:{note[:500]}" if note
                   else "\n(他没写具体意见 —— 先问一句哪里不对,别自己猜着重排。)"))
    return ("\n(这张核对单没等到回答,按**未确认**处理:先把计划讲给用户,"
            "拿到他一句话的同意再开工。)")


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
                      "detail": _with_verify(str(s.get("detail") or "").strip(),
                                             str(s.get("verify") or "").strip()),
                      "status": "pending", "done": False,
                      "depends_on": sorted(set(deps)), "note": ""})
        clean[-1]["verify"] = verify_of(clean[-1]["detail"])

    err = validate_deps(clean)
    if err:
        return err + "(计划未写入,请修正依赖后重试。)"

    _write_plan(ctx, {"goal": goal, "steps": clean})
    listed = []
    for s in clean:
        dep = f" ← 依赖 {', '.join(str(d) for d in s['depends_on'])}" if s["depends_on"] else ""
        v = f" · 验收 {s['verify']}" if s.get("verify") else ""
        listed.append(f"  {s['n']}. {s['title']}{dep}{v}")
    with_verify = [s for s in clean if s.get("verify")]
    gate = (f"\n其中 {len(with_verify)} 步带验收命令:标 done 之前必须用 bash 把它"
            "原样跑一遍(整条命令一字不差),没跑过的这一条我不收。"
            if with_verify else
            "\n能给判据的步骤请在这一步的 detail 里补一条 `verify: <命令>`"
            "(跑测试/跑构建/`--check` 这类);填不出就留空,别编一条空跑的命令。")
    # 单子写出来了 → 上规划锁:在这张纸被人批准之前,动手面一律拒(v0.9.129)。
    # 顺序很重要 —— 先锁再弹核对单,核对单没弹成/没人答都留在"锁着"那一侧。
    _set_plan_lock(ctx, True, goal)
    return (f"计划已写入 {PLAN_REL.as_posix()}(共 {len(clean)} 步):\n"
            + "\n".join(listed)
            + gate
            + scheduling_view({"steps": clean})
            + "\n请把计划展示给用户并等待确认;确认后逐步执行,"
              "每完成一步用 action=update 标记 done。"
            + "\n⚠ 规划锁已开:这张单子被用户批准之前,写文件/改文件/跑命令那一路"
              "都会被拒(读与查仍然随便)。不要试着绕过它,请用户点头是唯一的路。"
            # 整单核对单:桌面版当场弹一张卡(批准/改一条/重排),别处照旧走对话
            + review_plan(ctx, goal, clean))


def _plan_read(ctx: ToolContext) -> str:
    p = plan_path(ctx)
    if not p.exists():
        return "当前没有计划。复杂任务可先用 action=write 创建。"
    text = p.read_text(encoding="utf-8")
    return text + scheduling_view(parse_plan(text))


def _verify_gate(ctx: ToolContext, target: dict) -> str:
    """带判据的步骤想过 done 只有一条路:那一轮真跑过那条命令,且退出码是 0。

    只管判,**不写状态**:结论那一行(`verify ✓ 时刻`)由 `note_verify_run` 在跑完那一刻
    落进 plan.md —— 那是唯一落盘处,计划卡与监控段读的都是它。
    """
    verify = target.get("verify") or ""
    if not verify:
        return ""
    n = target["n"]
    run = find_verify_run(ctx, verify)
    if run is None:
        return (f"错误:第 {n} 步自带验收命令 `{verify}`,而这一轮没有它跑完的记录。"
                "先用 bash 原样跑一遍这条命令(整条一字不差),跑过之后我再收 done;"
                "预计跑过 300 秒就传 background=true 放后台,它跑完的那一刻这一步"
                "自己会认出来(不必你回来补一句);确实不打算跑,就标 failed 写清原因,"
                "或改计划把这条判据去掉 —— 别在没有凭据的情况下把它标成完成。")
    if run["code"] != 0:
        return (f"错误:第 {n} 步的验收没通过(退出码 {run['code']},跑于 {run['at']});"
                f"输出尾部:{run['tail'] or '(无输出)'}。我没有把它标成完成。"
                "修好再原样跑一次,或标 failed 说清原因 —— 悄悄改回 pending 也算没做。")
    # 备注不在这里写:V2 的 note_verify_run 在跑完那一刻就把 `verify ✓ 时刻` 落进了
    # plan.md(那是这份结论唯一的落盘处)。这里再写一遍就是两处各写一句真相 ——
    # 实测过一次覆盖:闸门那句"验收已过"把 V2 那句"verify ✓"顶掉了。
    return ""


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
    # V1 的硬闸:这一步自带判据,而这一轮没有它跑完的记录 → 拒绝标 done。
    # 这是"标 done 的是模型自己的一句话"那条链上唯一一处外部凭据,所以宁可拒,
    # 也不接受"看起来差不多"——跑不过、跑崩、超时都算没过。
    if status == "done":
        gate = _verify_gate(ctx, target)
        if gate:
            return gate
    target["status"] = status
    target["done"] = status == "done"
    if note.strip():
        target["note"] = note.strip()

    _write_plan(ctx, plan)

    done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
    lines = [f"已标记第 {step} 步为 {status}({done_count}/{len(plan['steps'])} 完成)。"]
    if status == "in_progress":
        lines[0] += (" 界面上这一行现在显示为「进行中」——这一步真做完后要标 done,"
                     "做不成的话标 failed 并写清原因,别让它一直挂在进行中。")
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
