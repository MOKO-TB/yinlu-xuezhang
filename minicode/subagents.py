"""子代理系统:task 工具,把独立子任务派给带全新上下文的子 Agent。

三种类型:
  explore   只读侦察(read_file/glob/grep/search_knowledge/use_skill/bash? 否,只留只读)
  implement 动手实现(全部工具)
  review    只读审查(同 explore)
子代理:独立会话(看不到主对话)、受限工具集(不能递归派 task)、步数上限、
复用主 Agent 的 LLM 与 UI(工具卡片带类型前缀,变更操作照常弹确认)。
"""
from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from typing import TYPE_CHECKING

from minicode.agent import Agent
from minicode.config import Config
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext

if TYPE_CHECKING:  # 避免循环导入,运行时才用到这些类型
    from minicode.ui import TerminalUI

MAX_SUB_STEPS = 20   # 子代理步数上限(比主 Agent 小)
REPORT_LIMIT = 6000  # 子代理报告回填给主对话的截断长度


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


def sub_tool_registry(registry: list[Tool], spec: SubAgentSpec) -> list[Tool]:
    """按类型裁剪并重命名子代理工具集(排除 task 防递归;只读类型只留只读工具)。"""
    tools = [t for t in registry
             if t.name != "task" and (not spec.read_only or t.read_only)]
    return [dc_replace(t, name=f"{spec.type}·{t.name}") for t in tools]


def build_task_tool(ctx: ToolContext, llm, ui, config: Config,
                    registry: list[Tool]) -> Tool:
    """构造 task 工具,闭包持有主 Agent 的 LLM/UI/配置。"""

    def task(ctx: ToolContext, type: str, task: str) -> str:
        spec = SUBAGENT_TYPES.get((type or "").strip())
        if spec is None:
            return (f"错误: 未知子代理类型 '{type}'。"
                    f"可用: {', '.join(SUBAGENT_TYPES)}")
        if not (task or "").strip():
            return "错误: 任务描述不能为空。"

        tools = sub_tool_registry(registry, spec)

        from minicode.app_setup import build_system_prompt  # 延迟导入避免循环
        sub_session = Session(
            system_prompt=build_system_prompt(ctx.workspace) + spec.extra_prompt,
            log_dir=None)
        sub_agent = Agent(
            llm=llm,
            registry=tools,
            session=sub_session,
            gate=PermissionGate(config),
            ui=ui,
            ctx=ctx,
            max_steps=MAX_SUB_STEPS,
        )
        sub_agent.run_turn(task.strip())

        report = next((m.get("content") for m in reversed(sub_session.messages)
                       if m.get("role") == "assistant" and m.get("content")),
                      None)
        prefix = f"[{spec.label}子代理完成]\n"
        return (prefix + (report or "(子代理已结束,但未返回文本报告)"))[:REPORT_LIMIT]

    return Tool(
        name="task",
        description=(
            "派出子代理执行独立子任务,子代理拥有全新上下文,完成后返回文字报告。"
            "类型:explore(只读侦察,适合大范围找代码/梳理结构)、"
            "implement(动手实现独立小功能)、review(只读审查)。"
            "子代理看不到当前对话——任务描述必须自包含(目标+涉及路径+验收标准)。"
            "适合:探索范围大、子任务独立、主对话上下文紧张时。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(SUBAGENT_TYPES),
                         "description": "子代理类型"},
                "task": {"type": "string", "description": "自包含的任务描述(目标/路径/验收标准)"},
            },
            "required": ["type", "task"],
        },
        handler=task,
    )
