"""文件名匹配(glob)与内容搜索(grep),纯标准库实现。"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from minicode.tools.base import PathOutsideWorkspace, ToolContext, resolve_in_workspace

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".idea", ".vscode", ".pytest_cache", "dist", "build", "sessions",
}
GLOB_RESULT_LIMIT = 300
GREP_MATCH_LIMIT = 200
LINE_DISPLAY_LIMIT = 200
BINARY_SNIFF_BYTES = 8192


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def _inside_workspace(ctx: ToolContext, p: Path) -> bool:
    try:
        p.resolve().relative_to(ctx.workspace)
        return True
    except ValueError:
        return False


def glob(ctx: ToolContext, pattern: str, path: str = ".") -> str:
    """按通配符模式匹配工作区内的文件,返回相对路径列表。"""
    try:
        base = resolve_in_workspace(ctx, path)
    except PathOutsideWorkspace as e:
        return f"错误: {e}"
    if not base.exists():
        return f"错误: 目录不存在: {path}"

    results: list[str] = []
    hit_limit = False
    try:
        for p in base.glob(pattern):
            if not _inside_workspace(ctx, p) or not p.is_file():
                continue
            results.append(p.relative_to(ctx.workspace).as_posix())
            if len(results) >= GLOB_RESULT_LIMIT:
                hit_limit = True
                break
    except (ValueError, NotImplementedError) as e:
        return f"错误: 无效的 glob 模式: {e}"
    if not results:
        return "没有匹配的文件。"
    tail = f"\n…(结果已达 {GLOB_RESULT_LIMIT} 条上限)" if hit_limit else ""
    return "\n".join(results) + tail


def grep(ctx: ToolContext, pattern: str, path: str = ".",
         glob: str | None = None, ignore_case: bool = False) -> str:
    """在工作区内按正则搜索文件内容,输出 路径:行号: 内容。"""
    try:
        base = resolve_in_workspace(ctx, path)
    except PathOutsideWorkspace as e:
        return f"错误: {e}"

    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return f"错误: 正则表达式无效: {e}"

    name_matcher = (lambda name: fnmatch.fnmatch(name, glob)) if glob else (lambda name: True)

    if base.is_file():
        candidates = [base]
    else:
        candidates = []
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                if name_matcher(name):
                    candidates.append(Path(root) / name)

    hits: list[str] = []
    files_with_hits = 0
    truncated = False
    for fp in candidates:
        if len(hits) >= GREP_MATCH_LIMIT:
            truncated = True
            break
        try:
            data = fp.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:BINARY_SNIFF_BYTES]:
            continue
        rel = fp.relative_to(ctx.workspace).as_posix()
        matched_here = False
        for lineno, line in enumerate(_decode(data).splitlines(), start=1):
            if len(hits) >= GREP_MATCH_LIMIT:
                truncated = True
                break
            if rx.search(line):
                matched_here = True
                display = line.strip()[:LINE_DISPLAY_LIMIT]
                hits.append(f"{rel}:{lineno}: {display}")
        if matched_here:
            files_with_hits += 1

    if not hits:
        return "没有匹配的内容。"
    tail = f"\n…(结果已达 {GREP_MATCH_LIMIT} 条上限)" if truncated else ""
    return "\n".join(hits) + tail + f"\n(共 {files_with_hits} 个文件命中)"
