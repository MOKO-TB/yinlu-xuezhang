"""CLI 与 GUI 共享的应用装配:system prompt 组装 + Agent 构建。"""
from __future__ import annotations

import platform
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.config import Config
from minicode.kb import kb_dirs, kb_prompt_section
from minicode.llm import LLMClient
from minicode.paths import SESSIONS_DIR, SYSTEM_PROMPT_PATH
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.skills import load_skills, skill_dirs, skills_prompt_section
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry


def build_system_prompt(workspace: Path) -> str:
    template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (template
              .replace("{workspace}", str(workspace))
              .replace("{date}", time.strftime("%Y-%m-%d"))
              .replace("{os}", platform.platform()))
    skills = load_skills(skill_dirs(workspace))
    return prompt + skills_prompt_section(skills) + kb_prompt_section(kb_dirs(workspace))


def create_agent(config: Config, workspace: Path, ui,
                 llm=None, sessions_dir: Path | None = None) -> tuple[Agent, Session]:
    session = Session(system_prompt=build_system_prompt(workspace),
                      log_dir=sessions_dir or SESSIONS_DIR)
    agent = Agent(
        llm=llm or LLMClient(config),
        registry=build_default_registry(),
        session=session,
        gate=PermissionGate(config),
        ui=ui,
        ctx=ToolContext(workspace=workspace, config=config),
    )
    return agent, session
