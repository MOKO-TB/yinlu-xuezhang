"""子代理系统:task 工具,把独立子任务派给带全新上下文的子 Agent。

按分工方式分两组:
  通用档  explore(只读侦察) / implement(动手实现) / review(只读审查)
  领域档  code / writing / data / english —— 由 minicode.router 登记派生,
          提示词只说"先加载哪几个已有技能",领域规矩本体仍在 skills/*.md 里。
子代理:独立会话(看不到主对话)、受限工具集(不能递归派 task)、步数上限、
复用主 Agent 的 LLM 与 UI(工具卡片带类型前缀,变更操作照常弹确认)。

type 还可以传 "auto":由 router 看任务描述选档,选中的理由会写进返回给主代理
(也就是用户看得到)的报告抬头 —— 路由不偷偷替用户决定。

并行派发:type/task 同时传数组即为批量模式(上限 MAX_PARALLEL_TASKS),
只对**只读类型**开放。原因是 PermissionGate 里 read_only 工具直接 ALLOW,
不会走确认握手;写类型并发会争抢同一条确认通道,把「哪个子代理在等确认」
搅乱。批量模式用静默 UI 吞掉各子代理的流式输出与工具卡片——否则几路
text_delta 会在同一个聊天区里互相穿插成乱码——只把合并报告作为工具结果带回。

超时:每一路各占一个守护线程,主代理最多等 config.subagent_time_limit_seconds
秒。到点只是**不再等它的报告**,Python 没有安全的强杀,那一路当前这一步还会
跑完(它的工具确认框也照常会弹) —— 报告里按这个口径写,不写成"子代理失败"。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import TYPE_CHECKING

from minicode import router
from minicode.agent import Agent
from minicode.config import Config
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext

if TYPE_CHECKING:  # 避免循环导入,运行时才用到这些类型
    pass

MAX_SUB_STEPS = 20        # 子代理步数上限(比主 Agent 小)
REPORT_LIMIT = 6000       # 单个子代理报告回填给主对话的截断长度
MAX_PARALLEL_TASKS = 4    # 一次 task 最多并发的只读子代理数
BATCH_REPORT_LIMIT = 16000  # 批量报告合并后的总截断长度


def _timeout_note(seconds: int) -> str:
    return (f"子代理超时未返回:主代理已停止等待(上限 {seconds} 秒)。"
            "它自己当前那一步可能仍在后台跑完,只是报告不再回填 —— "
            "这不算它失败。要等更久把 config.json 的 "
            "subagent_time_limit_seconds 调大(0=不限)。")


@dataclass(frozen=True)
class SubAgentSpec:
    type: str
    label: str
    description: str
    read_only: bool
    extra_prompt: str


SUBAGENT_TYPES: dict[str, SubAgentSpec] = {
    "explore": SubAgentSpec(
        "explore", "侦察", "大范围只读探索:找文件、梳理结构、定位代码", True,
        "\n# 角色:只读侦察子代理\n你只做调查,不做任何修改。输出:发现清单(文件路径+关键结论)、"
        "与任务相关的代码位置、建议的后续动作。控制在 300 字内。\n",
    ),
    "implement": SubAgentSpec(
        "implement", "实现", "按任务描述动手写代码、改文件、跑验证", False,
        "\n# 角色:实现子代理\n只完成给定任务,不扩大范围。输出:改动文件清单(每个文件一句话说明)、"
        "验证命令与真实输出、遗留问题。控制在 400 字内。\n",
    ),
    "review": SubAgentSpec(
        "review", "审查", "只读审查代码质量与正确性", True,
        "\n# 角色:审查子代理\n只读不改。输出:按 [P0-P3] 分级的问题列表(文件:位置 问题 → 建议)"
        ",最后一句总体结论。控制在 300 字内。\n",
    ),
}

# 领域档的键(注册在 router 里,这里只借它的登记,不复制它的知识)
DOMAIN_KEYS = tuple(router.DOMAINS)
TYPE_KEYS = tuple(SUBAGENT_TYPES) + DOMAIN_KEYS + ("auto",)


def _available_skill_names(workspace) -> list[str] | None:
    """本机技能库里叫得出名字的技能;读不到时返回 None(=这次不核对)。"""
    try:
        from minicode import skills as skills_mod
        dirs = skills_mod.skill_dirs(Path(workspace) if workspace else None)
        return [s.name for s in skills_mod.load_skills(dirs)]
    except Exception:
        return None


def type_spec(key: str, workspace=None) -> SubAgentSpec | None:
    """把类型名解析成子代理规格:通用档取静态表,领域档按本机技能库现拼提示词。"""
    key = str(key or "").strip()
    spec = SUBAGENT_TYPES.get(key)
    if spec is not None:
        return spec
    domain = router.DOMAINS.get(key)
    if domain is None:
        return None
    return SubAgentSpec(
        domain.key, domain.label, domain.description, domain.read_only,
        router.extra_prompt(domain, _available_skill_names(workspace)))



class _QuietUI:
    """批量并发时的静默 UI:吞掉子代理的流式输出与工具卡片。

    只读子代理串行跑时,文本会流进主聊天区(用户能看到侦察过程);
    一旦并发,几路 text_delta 会在同一处互相穿插,所以批量模式改走
    「只回填最终报告」这一条出口。错误类回调照常透传,避免把问题也吞掉。
    """

    _SILENT = frozenset({
        "on_text_delta", "on_reasoning_delta", "finish_stream",
        "tool_start", "tool_done", "tool_rejected_hard", "compacted",
        "info", "on_retry", "banner", "max_steps_reached",
    })

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in _QuietUI._SILENT:
            return lambda *a, **k: None
        return getattr(self._inner, name)


def sub_tool_registry(registry: list[Tool], spec: SubAgentSpec) -> list[Tool]:
    """按类型裁剪并重命名子代理工具集(排除 task 防递归;只读类型只留只读工具)。"""
    tools = [t for t in registry
             if t.name != "task" and (not spec.read_only or t.read_only)]
    return [dc_replace(t, name=f"{spec.type}·{t.name}") for t in tools]


def run_subagent(ctx: ToolContext, spec: SubAgentSpec, task_text: str,
                 llm, ui, config: Config, registry: list[Tool]) -> str:
    """跑一个子代理,返回它的最终文字报告(不含前缀)。异常收敛成报告文本。"""
    from minicode.app_setup import build_system_prompt  # 延迟导入避免循环
    # 子代理不参与跨天重建:`_refresh_clock` 只在 `run_turn` 开头查一次,而子代理
    # 生命周期以分钟计、建会话当天就跑完了。将来要"统一"到别处请先想清楚这点。
    try:
        sub_session = Session(
            system_prompt=build_system_prompt(ctx.workspace) + spec.extra_prompt,
            log_dir=None)
        sub_agent = Agent(
            llm=llm,
            registry=sub_tool_registry(registry, spec),
            session=sub_session,
            gate=PermissionGate(config),
            ui=ui,
            ctx=ctx,
            max_steps=MAX_SUB_STEPS,
        )
        sub_agent.run_turn(task_text)
    except Exception as e:                      # 单路失败不该带走整批
        return f"子代理执行出错: {type(e).__name__}: {e}"

    report = next((m.get("content") for m in reversed(sub_session.messages)
                   if m.get("role") == "assistant" and m.get("content")),
                  None)
    return report or "(子代理已结束,但未返回文本报告)"


def _as_list(value, field: str) -> list[str] | str:
    """把标量/数组统一成非空字符串列表;返回 str 表示校验失败的原因。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [v if isinstance(v, str) else str(v) for v in value]
    else:
        return f"错误: {field} 必须是字符串或字符串数组。"
    if not items or any(not i.strip() for i in items):
        return f"错误: {field} 不能为空。"
    return items


def _await(runs: list, seconds: int) -> list:
    """每个 callable 各起一个守护线程,合起来最多等 seconds 秒;超时位返回 None。

    用守护线程而不是 ThreadPoolExecutor:线程池的收尾会 join 线程,一条卡在
    网络上的子代理能让退出都退不干净。守护线程不挡退出,代价是它仍在跑 ——
    所以超时文案必须说"不再等它的报告",不能说"已停止它"。
    """
    boxes = [{"done": threading.Event()} for _ in runs]

    def wrap(box, fn):
        try:
            box["value"] = fn()
        except Exception as e:                        # 单路失败不该带走整批
            box["value"] = f"子代理执行出错: {type(e).__name__}: {e}"
        finally:
            box["done"].set()

    for box, fn in zip(boxes, runs):
        threading.Thread(target=wrap, args=(box, fn),
                         daemon=True, name="subagent").start()
    deadline = time.monotonic() + seconds if seconds > 0 else None
    out = []
    for box in boxes:
        if deadline is None:
            box["done"].wait()
            out.append(box.get("value"))
            continue
        left = max(0.0, deadline - time.monotonic())
        out.append(box.get("value") if box["done"].wait(left) else None)
    return out


def _merge_reports(pairs: list[tuple[SubAgentSpec, str, str]]) -> str:
    """按请求顺序合并多份报告:序号 + 类型 + 任务摘要 + 正文。"""
    chunks = []
    for i, (spec, task_text, report) in enumerate(pairs, start=1):
        head = f"## {i}. {spec.type} · {task_text.strip()[:60]}"
        chunks.append(f"{head}\n{report}")
    body = "\n\n".join(chunks)
    if len(body) > BATCH_REPORT_LIMIT:
        body = body[:BATCH_REPORT_LIMIT] + f"\n…(合并报告超过 {BATCH_REPORT_LIMIT} 字符,已截断)"
    return f"[并行子代理完成 · {len(pairs)} 个]\n\n{body}"


def build_task_tool(ctx: ToolContext, llm, ui, config: Config,
                    registry: list[Tool]) -> Tool:
    """构造 task 工具,闭包持有主 Agent 的 LLM/UI/配置。"""

    def task(ctx: ToolContext, type, task) -> str:
        types = _as_list(type, "type")
        if isinstance(types, str):
            return types
        tasks = _as_list(task, "task")
        if isinstance(tasks, str):
            return tasks
        if len(types) != len(tasks):
            return (f"错误: type 有 {len(types)} 项、task 有 {len(tasks)} 项,"
                    "两者数量必须一致。")
        if len(types) > MAX_PARALLEL_TASKS:
            return (f"错误: 一次最多并行 {MAX_PARALLEL_TASKS} 个子代理"
                    f"(收到 {len(types)} 个)。请拆成多轮。")

        limit = max(0, int(getattr(config, "subagent_time_limit_seconds", 0) or 0))
        workspace = getattr(ctx, "workspace", None)
        specs = []
        for t, text in zip(types, tasks):
            key, note = router.resolve_type(str(t).strip(), text)
            spec = type_spec(key, workspace)
            if spec is None:
                return (f"错误: 未知子代理类型 '{t}'。"
                        f"可用: {', '.join(TYPE_KEYS)}")
            specs.append((spec, text.strip(), note))

        # ---- 单发:保持 v0.9 行为(流式输出进聊天区,报告带回工具结果)----
        if len(specs) == 1:
            spec, text, note = specs[0]
            report = _await([lambda: run_subagent(
                ctx, spec, text, llm, ui, config, registry)], limit)[0]
            if report is None:
                report = _timeout_note(limit)
            head = f"[{spec.label}子代理完成]" + (f"\n{note}" if note else "")
            return (head + "\n" + report)[:REPORT_LIMIT]

        # ---- 批量:只读才并发(写类型共享一条确认通道,会串味)----
        writers = [s.type for s, _, _ in specs if not s.read_only]
        if writers:
            return (f"错误: 批量并行只支持只读类型"
                    f"(explore / review / english),"
                    f"但收到了 {', '.join(writers)}。"
                    "写操作请逐个派发,或改用单发模式——并发的写子代理会争抢"
                    "同一条权限确认通道,导致确认框对应不上是谁在等。")

        quiet = _QuietUI(ui)
        reports = _await([
            (lambda s=spec, x=text: run_subagent(
                ctx, s, x, llm, quiet, config, registry))
            for spec, text, _note in specs], limit)
        pairs = [(spec, text, rep if rep is not None else _timeout_note(limit))
                 for (spec, text, _note), rep in zip(specs, reports)]
        notes = [note for _s, _t, note in specs if note]
        merged = _merge_reports(pairs)
        return ("\n".join(notes) + "\n\n" + merged) if notes else merged

    return Tool(
        name="task",
        description=(
            "派出子代理执行独立子任务,子代理拥有全新上下文,完成后返回文字报告。"
            "通用档:explore(只读侦察,适合大范围找代码/梳理结构)、"
            "implement(动手实现独立小功能)、review(只读审查)。"
            "领域档(会先加载该领域已有的 SKILL 再动手):"
            "code(写代码/改 bug/跑测试)、writing(论文/申请书/PPT/简历)、"
            "data(表格统计/名单批量/题库导出)、english(四六级与考研英语的经验查询,只读)。"
            "拿不准派哪一档时传 type=\"auto\":由路由按任务描述里的领域特征选,"
            "选中的理由会写在报告抬头给你和用户看(命中哪些词、加载哪些技能);"
            "没命中任何领域特征时 auto 会退回 explore,不会硬凑一档。"
            "子代理看不到当前对话——任务描述必须自包含(目标+涉及路径+验收标准)。"
            "适合:探索范围大、子任务独立、主对话上下文紧张时。"
            "一次要查多件互不相干的事时,type 和 task 都传数组即可并行(最多 4 个),"
            "但只支持只读类型 explore/review/english;写操作必须逐个单发。"
            "每一路最多等 config.subagent_time_limit_seconds 秒,超时会如实标成"
            "「未返回」而不是失败。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "description": "子代理类型;并行探索时传类型数组,如 [\"explore\", \"review\"];"
                                   "\"auto\" 表示按任务特征路由(理由会回显)",
                    "anyOf": [
                        {"type": "string", "enum": list(TYPE_KEYS)},
                        {"type": "array", "items": {"type": "string",
                                                    "enum": list(TYPE_KEYS)}},
                    ],
                },
                "task": {
                    "description": "自包含的任务描述(目标/路径/验收标准);与 type 数组一一对应时同样传数组",
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
            },
            "required": ["type", "task"],
        },
        handler=task,
    )
