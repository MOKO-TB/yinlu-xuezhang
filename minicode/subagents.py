"""子代理系统:task 工具,把独立子任务派给带全新上下文的子 Agent。

三种类型:
  explore   只读侦察(read_file/glob/grep/search_knowledge/use_skill 等只读工具)
  implement 动手实现(全部工具)
  review    只读审查(同 explore)
子代理:独立会话(看不到主对话)、受限工具集(不能递归派 task)、步数上限、
复用主 Agent 的 LLM 与 UI(工具卡片带类型前缀,变更操作照常弹确认)。

并行派发:type/task 同时传数组即为批量模式(上限 MAX_PARALLEL_TASKS),
只对**只读类型**开放。原因是 PermissionGate 里 read_only 工具直接 ALLOW,
不会走确认握手;写类型并发会争抢同一条确认通道,把「哪个子代理在等确认」
搅乱。批量模式用静默 UI 吞掉各子代理的流式输出与工具卡片——否则几路
text_delta 会在同一个聊天区里互相穿插成乱码——只把合并报告作为工具结果带回。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace as dc_replace
from typing import TYPE_CHECKING

from minicode.agent import Agent
from minicode.config import Config
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext

if TYPE_CHECKING:  # 避免循环导入,运行时才用到这些类型
    from minicode.ui import TerminalUI

MAX_SUB_STEPS = 20        # 子代理步数上限(比主 Agent 小)
REPORT_LIMIT = 6000       # 单个子代理报告回填给主对话的截断长度
MAX_PARALLEL_TASKS = 4    # 一次 task 最多并发的只读子代理数
BATCH_REPORT_LIMIT = 16000  # 批量报告合并后的总截断长度


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

        specs = []
        for t in types:
            spec = SUBAGENT_TYPES.get(t.strip())
            if spec is None:
                return (f"错误: 未知子代理类型 '{t}'。"
                        f"可用: {', '.join(SUBAGENT_TYPES)}")
            specs.append(spec)

        # ---- 单发:保持 v0.9 行为(流式输出进聊天区,报告带回工具结果)----
        if len(specs) == 1:
            report = run_subagent(ctx, specs[0], tasks[0].strip(),
                                  llm, ui, config, registry)
            return (f"[{specs[0].label}子代理完成]\n" + report)[:REPORT_LIMIT]

        # ---- 批量:只读才并发(写类型共享一条确认通道,会串味)----
        writers = [s.type for s in specs if not s.read_only]
        if writers:
            return (f"错误: 批量并行只支持只读类型(explore / review),"
                    f"但收到了 {', '.join(writers)}。"
                    "写操作请逐个派发,或改用单发模式——并发的写子代理会争抢"
                    "同一条权限确认通道,导致确认框对应不上是谁在等。")

        quiet = _QuietUI(ui)
        with ThreadPoolExecutor(max_workers=len(specs),
                                thread_name_prefix="subagent") as pool:
            reports = list(pool.map(
                lambda pair: run_subagent(ctx, pair[0], pair[1].strip(),
                                          llm, quiet, config, registry),
                list(zip(specs, tasks))))
        pairs = [(spec, text, rep) for spec, text, rep in zip(specs, tasks, reports)]
        return _merge_reports(pairs)

    return Tool(
        name="task",
        description=(
            "派出子代理执行独立子任务,子代理拥有全新上下文,完成后返回文字报告。"
            "类型:explore(只读侦察,适合大范围找代码/梳理结构)、"
            "implement(动手实现独立小功能)、review(只读审查)。"
            "子代理看不到当前对话——任务描述必须自包含(目标+涉及路径+验收标准)。"
            "适合:探索范围大、子任务独立、主对话上下文紧张时。"
            "一次要查多件互不相干的事时,type 和 task 都传数组即可并行(最多 4 个),"
            "但只支持只读类型 explore/review;写操作必须逐个单发。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "description": "子代理类型;并行探索时传类型数组,如 [\"explore\", \"review\"]",
                    "anyOf": [
                        {"type": "string", "enum": list(SUBAGENT_TYPES)},
                        {"type": "array", "items": {"type": "string",
                                                    "enum": list(SUBAGENT_TYPES)}},
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
