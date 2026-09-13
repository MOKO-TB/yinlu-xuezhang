"""CLI 与 GUI 共享的应用装配:system prompt 组装 + Agent 构建。"""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.config import Config
from minicode.kb import kb_dirs, kb_prompt_section
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


def create_agent(config: Config, workspace: Path, ui,
                 llm=None, sessions_dir: Path | None = None) -> tuple[Agent, Session]:
    session = Session(system_prompt=build_system_prompt(workspace),
                      log_dir=sessions_dir or SESSIONS_DIR)
    llm = llm or LLMClient(config)
    ctx = ToolContext(workspace=workspace, config=config)
    # 沙箱:工具层(路径围栏)与权限门(确认流程)共享同一份状态
    ctx.sandbox = SandboxState(policy_from_raw(config.sandbox), workspace)
    registry = build_default_registry()
    # MCP 服务器:连不上的只在日志里提示,不阻塞应用启动
    mcp = connect_servers(parse_servers(config.mcp_servers), log=_mcp_log(ui))
    registry.extend(mcp.build_tools())
    # 子代理(task 工具):闭包持有 LLM/UI/配置,主循环可派发独立子任务
    registry.append(build_task_tool(ctx, llm, ui, config, registry))
    agent = Agent(
        llm=llm,
        registry=registry,
        session=session,
        gate=PermissionGate(config, sandbox=ctx.sandbox),
        ui=ui,
        ctx=ctx,
    )
    agent.mcp = mcp          # 退出时 close_all(),别留下孤儿进程
    return agent, session
