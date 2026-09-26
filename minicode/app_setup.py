"""CLI 与 GUI 共享的应用装配:system prompt 组装 + Agent 构建。"""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.browser import browser_prompt_section
from minicode.calendar import calendar_prompt_section
from minicode.schedule import schedule_prompt_section
from minicode.mood import mood_prompt_section
from minicode.config import Config
from minicode.kb import kb_dirs, kb_prompt_section
from minicode.library import library_prompt_section
from minicode.notices import notices_prompt_section
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


def build_system_prompt(workspace: Path, for_subagent: bool = False) -> str:
    template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (template
              .replace("{workspace}", str(workspace))
              .replace("{date}", time.strftime("%Y-%m-%d"))
              .replace("{os}", platform.platform()))
    skills = load_skills(skill_dirs(workspace))
    # 段序:**静态段在前、动态段在后**(v0.9.46)。动态段(有没有课表、这周记没记
    # 情绪、记忆条数…)任何一段出现或消失,其后的前缀全部变化,KV Cache 从那里
    # 失效 —— 全部排到尾部,静态段(skills/calendar/notices/browser)才能整段
    # 稳定命中缓存。每段自身已遵守"不带当前时间"的口径。
    #
    # for_subagent=True(v0.9.53):子代理是派出去干具体活的独立执行者,**裁掉
    # 个人数据段** —— 情绪日记是主对话的陪伴语境,长期记忆是主代理的偏好上下文,
    # 对"只产出报告"的子代理没有用处,却让每一路都背着主人的私事在工作区里跑
    # (批量只读模式一次四路)。课表/馆藏/校历保留:data 与 english 档真的会用。
    return (prompt + skills_prompt_section(skills)
            + calendar_prompt_section()
            + notices_prompt_section()
            + browser_prompt_section()
            + kb_prompt_section(kb_dirs(workspace))
            + schedule_prompt_section()
            + library_prompt_section(workspace)
            + library_guide_prompt_section(workspace)
            + (mood_prompt_section() if not for_subagent else "")
            + (memory_prompt_section(workspace) if not for_subagent else ""))


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
    # 主/子代理共用一个回滚栈(v0.9.53):先建容器传给 task 工具,Agent 建好后
    # 把自己的栈替换成同一个对象 —— implement 子代理的写快照,主代理 /undo
    # 弹到的就是它,子代理改坏文件救得回来。
    undo_ref: list = []
    registry.append(build_task_tool(ctx, llm, ui, config, registry,
                                    parent_undo=undo_ref))
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
    agent._undo_stack = undo_ref
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
