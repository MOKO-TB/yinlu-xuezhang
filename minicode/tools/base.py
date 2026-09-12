"""工具基座:工具定义、运行上下文、路径安全。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from minicode.config import Config


class PathOutsideWorkspace(Exception):
    """工具试图访问工作区之外的路径。"""


@dataclass
class ToolContext:
    """工具运行时上下文:工作区根目录 + 全局配置。"""

    workspace: Path
    config: Config


Handler = Callable[..., str]  # 签名: handler(ctx, **kwargs) -> str


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema,类型必须是 object
    handler: Handler
    read_only: bool = False

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
