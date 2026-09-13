"""文件读写与精确编辑工具。"""
from __future__ import annotations

from pathlib import Path

from minicode.tools.base import (
    PathBlocked, PathOutsideWorkspace, ToolContext, resolve_path,
)

READ_LINE_LIMIT = 2000  # 默认单次最多读取行数
LINE_CHAR_LIMIT = 2000  # 单行超长截断
BINARY_SNIFF_BYTES = 8192

_PATH_ERRORS = (PathOutsideWorkspace, PathBlocked)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def _read_text(p: Path) -> str:
    return _decode(p.read_bytes())


def _format_lines(lines: list[str], start: int) -> str:
    width = len(str(start + len(lines) - 1))
    out = []
    for i, line in enumerate(lines, start=start):
        if len(line) > LINE_CHAR_LIMIT:
            line = line[:LINE_CHAR_LIMIT] + " …(行超长已截断)"
        out.append(f"{i:>{width}}\t{line}")
    return "\n".join(out)


def read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int | None = None) -> str:
    """读取文本文件,返回带行号的内容。"""
    try:
        p = resolve_path(ctx, path)
    except _PATH_ERRORS as e:
        return f"错误: {e}"
    if not p.exists():
        return f"错误: 文件不存在: {path}"
    if p.is_dir():
        return f"错误: {path} 是目录而不是文件,可用 glob 工具列出目录内容。"

    data = p.read_bytes()
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return f"错误: {path} 看起来是二进制文件,无法按文本读取。"
    lines = _decode(data).splitlines()

    offset = max(1, int(offset))
    limit = int(limit) if limit else READ_LINE_LIMIT
    chunk = lines[offset - 1 : offset - 1 + limit]
    if not chunk:
        return f"文件共 {len(lines)} 行,offset={offset} 超出范围。"
    body = _format_lines(chunk, start=offset)
    if offset - 1 + limit < len(lines):
        body += f"\n…(共 {len(lines)} 行,已截断;可用更大的 offset 继续读取)"
    return body


def write_file(ctx: ToolContext, path: str, content: str) -> str:
    """写入(创建或覆盖)一个文本文件。"""
    try:
        p = resolve_path(ctx, path, for_write=True)
    except _PATH_ERRORS as e:
        return f"错误: {e}"
    action = "覆盖" if p.exists() else "创建"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")
    n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return f"已{action}文件 {path}({n_lines} 行,{len(content)} 字符)。"


def edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str,
              replace_all: bool = False) -> str:
    """精确字符串替换编辑。old_string 必须在文件中唯一,除非 replace_all。"""
    try:
        p = resolve_path(ctx, path, for_write=True)
    except _PATH_ERRORS as e:
        return f"错误: {e}"
    if not p.exists():
        return f"错误: 文件不存在: {path}。先用 read_file 查看内容。"
    if old_string == new_string:
        return "错误: old_string 与 new_string 相同,没有需要修改的内容。"

    text = _read_text(p)
    count = text.count(old_string)
    if count == 0:
        return ("错误: old_string 未在文件中找到。请先用 read_file 核对内容,"
                "注意空格、缩进与换行必须完全一致。")
    if count > 1 and not replace_all:
        return (f"错误: old_string 在文件中出现 {count} 次,不唯一。"
                "请补充更多上下文使其唯一,或设置 replace_all=true 全部替换。")

    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8", newline="\n")
    n = count if replace_all else 1
    return f"已编辑 {path}(替换 {n} 处)。"
