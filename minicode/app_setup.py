"""CLI 与 GUI 共享的应用装配:system prompt 组装 + Agent 构建。"""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.browser import browser_prompt_section
from minicode.calendar import calendar_prompt_section
from minicode.config import Config
from minicode.kb import kb_dirs, kb_prompt_section
from minicode.library import library_prompt_section
from minicode.library_guide import library_guide_prompt_section
from minicode.mcp import connect_servers, parse_servers
from minicode.memory import memory_prompt_section
from minicode.llm import LLMClient
from minicode.paths import SESSIONS_DIR, SYSTEM_PROMPT_PATH
from minicode.permission import PermissionGate
from minicode.sandbox import SandboxState, policy_from_raw
from minicode.session import Session
from minicode.skills import load_skills, skill_dirs, skills_prompt_section
from minicode.subagents import build_task_tool
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry


def build_system_prompt(workspace: Path) -> str:
    template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (template
              .replace("{workspace}", str(workspace))
              .replace("{date}", time.strftime("%Y-%m-%d"))
              .replace("{os}", platform.platform()))
    skills = load_skills(skill_dirs(workspace))
    return (prompt + skills_prompt_section(skills)
            + kb_prompt_section(kb_dirs(workspace))
            + calendar_prompt_section()
            + library_prompt_section(workspace)
            + library_guide_prompt_section(workspace)
            + browser_prompt_section()
            + memory_prompt_section(workspace))


def _mcp_log(ui):
    """MCP 启动失败等信息:GUI 走事件推送,终端走 stderr。"""
    def log(text: str) -> None:
        push = getattr(ui, "push", None)
        if callable(push):
            try:
                push("info", text=text)
                return
            except Exception:
                pass
        print(f"[MCP] {text}", file=sys.stderr)
    return log


mcp_log = _mcp_log      # 公开别名(gui_bridge 复用同一套日志行为)


def build_runner_agent(config: Config, workspace: Path, ui, llm,
                       sessions_dir: Path | None = None,
                       mcp_tools: list | None = None,
                       sandbox=None) -> tuple[Agent, Session]:
    """造一个"会话运行器"(独立 Agent + Session),供后台并发任务使用。

    LLM 客户端、MCP 工具、沙箱状态都是**外部传入共享**的:
    - MCP 连接只建一次,新会话不该重复拉起子进程;
    - 沙箱是全局策略,所有会话看到同一份边界;
    - ui 则是**每个会话一个适配器**(把事件打上会话标记),所以走参数传。
    """
    ctx = ToolContext(workspace=workspace, config=config)
    ctx.sandbox = sandbox if sandbox is not None else \
        SandboxState(policy_from_raw(config.sandbox), workspace)
    ctx.ui = ui      # 工具可把图形指令推给界面(校园地图 map_* 工具用)
    ctx.llm = llm    # 记忆整理(memory action=dream)用它做一次性归纳
    registry = build_default_registry()
    registry.extend(mcp_tools or [])
    # 子代理(task 工具):闭包持有 LLM/UI/配置,主循环可派发独立子任务
    registry.append(build_task_tool(ctx, llm, ui, config, registry))
    session = Session(system_prompt=build_system_prompt(workspace),
                      log_dir=sessions_dir or SESSIONS_DIR,
                      workspace=workspace)
    agent = Agent(
        llm=llm,
        registry=registry,
        session=session,
        gate=PermissionGate(config, sandbox=ctx.sandbox),
        ui=ui,
        ctx=ctx,
    )
    return agent, session


def create_agent(config: Config, workspace: Path, ui,
                 llm=None, sessions_dir: Path | None = None) -> tuple[Agent, Session]:
    llm = llm or LLMClient(config)
    # MCP 服务器:连不上的只在日志里提示,不阻塞应用启动
    mcp = connect_servers(parse_servers(config.mcp_servers), log=_mcp_log(ui))
    sandbox = SandboxState(policy_from_raw(config.sandbox), workspace)
    agent, session = build_runner_agent(
        config, workspace, ui, llm, sessions_dir=sessions_dir,
        mcp_tools=mcp.build_tools(), sandbox=sandbox)
    agent.mcp = mcp          # 退出时 close_all(),别留下孤儿进程
    return agent, session
