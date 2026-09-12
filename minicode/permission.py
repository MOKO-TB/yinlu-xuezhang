"""权限门:变更类工具需要用户确认;灾难性命令硬拒绝,不给确认机会。"""
from __future__ import annotations

import re

from minicode.config import Config
from minicode.tools.base import Tool

ALLOW = "allow"
ASK = "ask"
REJECT = "reject"


def _check_rm(segment: str) -> str | None:
    """检查单个 rm 命令片段是否在递归删除危险目标。"""
    tokens = [t.strip("\"'") for t in segment.split()]
    if not tokens or tokens[0].rstrip(".exe") != "rm":
        return None
    recursive = any(t.startswith("-") and "r" in t.lower() for t in tokens[1:])
    if not recursive:
        return None
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    for t in targets:
        tl = t.lower().replace("\\", "/")
        if (tl in ("*", ".", "..", "~", "$home", "%userprofile%", "/*", "~/", "/")
                or tl.startswith(("/", "~", "$home"))
                or re.fullmatch(r"[a-z]:/?", tl)):
            return f"rm 递归删除危险目标: {t}"
    return None


def check_disaster_command(command: str) -> str | None:
    """返回硬拒绝原因;None 表示不构成灾难,进入正常确认流程。

    这是兜底黑名单,宁可错杀不可放过;正常危险操作(如删除单个目录)
    仍会走用户确认,不会被这里拦截。
    """
    cmd = (command or "").strip()
    if not cmd:
        return None
    low = cmd.lower()

    # fork 炸弹
    if ":(){:|:&};" in low.replace(" ", ""):
        return "fork 炸弹"

    # rm 逐段检查(按 shell 分隔符切分,避免 echo "rm -rf /" 之类误伤)
    for seg in re.split(r"[;&|\n]", cmd):
        reason = _check_rm(seg)
        if reason:
            return reason

    # 磁盘级破坏
    if (re.search(r"\bformat\s+[a-z]:", low)
            or "diskpart" in low
            or re.search(r"\bmkfs", low)
            or re.search(r"\bdd\b[^|;&]*\bof=/dev/", low)
            or re.search(r"cipher\s+/w", low)):
        return "磁盘级破坏性命令"

    # Windows 批量删除系统位置
    if ("remove-item" in low and "-recurse" in low
            and re.search(r"[a-z]:\\(windows|users|program)|\\windows|\\users|\$home|~", low)):
        return "递归强制删除系统目录"
    if re.search(r"\bdel\b[^&|;]*/s[^&|;]*[a-z]:\\(\s|$)", low):
        return "批量删除盘符根目录"

    return None


class PermissionGate:
    """每回合开始时清空"本轮已放行"集合;确认时用户可选只放行一次或本轮全放。"""

    def __init__(self, config: Config):
        self.config = config
        self._turn_allowed: set[str] = set()

    def new_turn(self) -> None:
        self._turn_allowed.clear()

    def check(self, tool: Tool, args: dict) -> str:
        if tool.name == "bash":
            reason = check_disaster_command(args.get("command", ""))
            if reason:
                return REJECT
        if tool.read_only:
            return ALLOW
        if self.config.permission_mode == "accept_all":
            return ALLOW
        if tool.name in self._turn_allowed:
            return ALLOW
        return ASK

    def approve_for_turn(self, tool_name: str) -> None:
        self._turn_allowed.add(tool_name)
