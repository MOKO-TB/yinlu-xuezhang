"""权限门:变更类工具需要用户确认;灾难性命令硬拒绝,不给确认机会。

沙箱联动(参考星瑶 Y-Code 的边界模型):
- 敏感路径(凭据/密钥目录)一律硬拒绝,任何权限模式都不能越过;
- 工作区外的路径即便在 accept_all 模式下也要用户确认一次,
  确认后本回合内该路径放行(allow_outside_workspace 打开则直接放行);
- 沙箱禁配规则/禁网命令同样硬拒绝。
"""
from __future__ import annotations

import re
from pathlib import Path

from minicode.config import Config
from minicode.sandbox import (
    ASK as SB_ASK, BLOCKED, OUTSIDE, REJECT as SB_REJECT,
    check_command, classify_for_write, classify_path,
)
from minicode.tools.base import Tool

ALLOW = "allow"
ASK = "ask"
REJECT = "reject"

WRITE_TOOLS = ("write_file", "edit_file")

# 用户确认后需要记住"这个路径可以访问"的工具
PATH_TOOLS = ("read_file", "write_file", "edit_file")


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

    def __init__(self, config: Config, sandbox=None):
        self.config = config
        self.sandbox = sandbox          # minicode.sandbox.SandboxState
        self._turn_allowed: set[str] = set()

    def new_turn(self) -> None:
        self._turn_allowed.clear()
        if self.sandbox is not None:
            self.sandbox.new_turn()

    # ------------------------------------------------------------ 沙箱判定

    def _path_kind(self, tool_name: str, raw: str) -> tuple[str, Path] | None:
        if self.sandbox is None or tool_name not in PATH_TOOLS:
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        classify = classify_for_write if tool_name in WRITE_TOOLS else classify_path
        return classify(self.sandbox.workspace, raw)

    def approve_outside(self, tool: Tool, args: dict) -> None:
        """用户点了允许:记住这个工作区外路径(本回合内有效)。"""
        if self.sandbox is None:
            return
        name = tool.name.rsplit("·", 1)[-1]
        kind = self._path_kind(name, args.get("path"))
        if kind and kind[0] == OUTSIDE:
            self.sandbox.approve_path(kind[1])

    # ------------------------------------------------------------ 主判定

    def check(self, tool: Tool, args: dict) -> str:
        # 子代理的工具名带类型前缀(如 "explore·bash"),取末段判断
        name = tool.name.rsplit("·", 1)[-1]
        if name == "bash":
            reason = check_disaster_command(args.get("command", ""))
            if reason:
                return REJECT

        if self.sandbox is not None:
            # 敏感路径:任何模式、任何授权都不能碰
            kind = self._path_kind(name, args.get("path"))
            if kind is not None:
                level, path = kind
                if level == BLOCKED:
                    return REJECT
                if level == OUTSIDE and not self.sandbox.path_allowed(path):
                    return ASK          # 沙箱边界不因 accept_all 而放开
            if name == "bash":
                level, why = check_command(self.sandbox.policy,
                                           args.get("command", ""))
                if level == SB_REJECT:
                    return REJECT
                if level == SB_ASK:
                    return ASK
                if not self.sandbox.policy.enabled:
                    pass                # 沙箱关闭:命令仍走下面的常规确认

        if tool.read_only:
            return ALLOW
        if self.config.permission_mode == "accept_all":
            return ALLOW
        if name in self._turn_allowed:
            return ALLOW
        return ASK

    def approve_for_turn(self, tool_name: str) -> None:
        self._turn_allowed.add(tool_name)
