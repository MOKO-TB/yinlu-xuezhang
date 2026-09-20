"""工具基座:工具定义、运行上下文、路径安全。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from minicode.config import Config
from minicode.sandbox import INSIDE


class PathOutsideWorkspace(Exception):
    """工具试图访问工作区之外的路径。"""


class PathBlocked(Exception):
    """工具试图访问敏感路径(凭据/密钥目录),任何模式下都禁止。"""


@dataclass
class ToolContext:
    """工具运行时上下文:工作区根目录 + 全局配置 + 沙箱状态 + 界面通道。"""

    workspace: Path
    config: Config
    sandbox: Any | None = None   # minicode.sandbox.SandboxState,由 app_setup 注入
    ui: Any | None = None        # GuiUI/TerminalUI:工具可把图形指令推给界面(如校园地图)
    llm: Any | None = None       # LLMClient:记忆整理(dream)等内部工具需要一次性补全


Handler = Callable[..., str]  # 签名: handler(ctx, **kwargs) -> str


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema,类型必须是 object
    handler: Handler
    read_only: bool = False
    # 信任级:local 本机产生 / external 外部内容(结果要套溯源围栏,见 provenance.py)
    # / principal 用户意志。子代理工具沿用父工具的值。
    trust: str = "local"

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def resolve_in_workspace(ctx: ToolContext, raw_path: str) -> Path:
    """把路径解析到工作区内;越界(包括指向外部的绝对路径)直接拒绝。"""
    p = Path(raw_path)
    if not p.is_absolute():
        p = ctx.workspace / p
    p = p.resolve()
    try:
        p.relative_to(ctx.workspace)
    except ValueError:
        raise PathOutsideWorkspace(
            f"路径超出工作区范围: {raw_path}(工作区: {ctx.workspace})。请使用相对工作区的路径。"
        )
    return p


def resolve_path(ctx: ToolContext, raw_path: str, *, for_write: bool = False) -> Path:
    """沙箱感知的路径解析:工作区内放行、工作区外看授权、敏感路径一律拒绝。

    没装沙箱(ctx.sandbox 为空)时退化为旧的硬围栏,保证既有调用行为不变。
    """
    state = getattr(ctx, "sandbox", None)
    if state is None:
        return resolve_in_workspace(ctx, raw_path)
    raw = (raw_path or "").strip() or "."

    if for_write:
        from minicode.sandbox import BLOCKED, SECRET, classify_for_write as classify
    else:
        from minicode.sandbox import BLOCKED, SECRET, classify_path as classify
    kind, p = classify(state.workspace, raw)
    if kind == BLOCKED:
        raise PathBlocked(
            f"路径属于敏感目录(凭据/密钥),始终禁止访问: {raw_path}"
        )
    # SECRET = 工作区**内**的凭据文件:权限门已经让用户确认过(或在 full 档前
    # 就拦下),到这一步不能再拿"不是 inside"当越界拒掉 —— 否则用户点了允许还是读不到。
    if kind in (INSIDE, SECRET):
        return p
    if not state.path_allowed(p):
        raise PathOutsideWorkspace(
            f"路径超出工作区范围: {raw_path}(工作区: {state.workspace})。"
            "访问工作区外需要用户在确认框里放行本次操作,"
            "或在 config.json 打开 sandbox.allow_outside_workspace。"
        )
    return p
