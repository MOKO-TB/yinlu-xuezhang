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


def _nearest_lines(text: str, old_string: str, keep: int = 2,
                   cutoff: float = 0.55) -> "list[tuple[int, float, str]]":
    """与 old_string 首行最接近的几行(行号 1 起、相似度、截断展示)。

    借 difflib 的序列相似度做"指路",不做模糊替换 —— 改错了比不改更糟。
    """
    from difflib import SequenceMatcher
    lines = text.splitlines()
    first = (old_string.splitlines() or [""])[0].strip()
    if not first or not lines:
        return []
    scored = []
    for i, ln in enumerate(lines, start=1):
        s = ln.strip()
        if not s:
            continue
        ratio = SequenceMatcher(None, first, s).ratio()
        if ratio >= cutoff:
            scored.append((i, ratio, s[:60]))
    scored.sort(key=lambda x: -x[1])
    return [(n, r, s) for n, r, s in scored[:keep]]


def _match_lines(text: str, old_string: str, cap: int = 20) -> "list[int]":
    """old_string 每次出现的所在行号(1 起),给"出现 N 次不唯一"的反馈用。"""
    out, start = [], 0
    while len(out) < cap:
        idx = text.find(old_string, start)
        if idx < 0:
            break
        out.append(text.count("\n", 0, idx) + 1)
        start = idx + 1
    return out


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
        # 反馈"最接近的行"而不是只说没找到:模型拿着行号与相似度一回合就能
        # 自我修正,省掉"再 read_file 一遍"的整轮等待。只报告,绝不替它猜着改。
        hints = _nearest_lines(text, old_string)
        extra = "".join(f"\n  第 {n} 行(相似度 {ratio:.0%}):{line}"
                        for n, ratio, line in hints)
        return ("错误: old_string 未在文件中找到(空格、缩进与换行必须完全一致)。"
                + ("文件里最接近的几行:" + extra if hints else
                   "文件里找不到相近的行 —— 可能要改的是别的文件,先 read_file 核对。"))
    if count > 1 and not replace_all:
        starts = _match_lines(text, old_string)
        shown = ", ".join(f"第 {n} 行" for n in starts[:6])
        more = f"(共 {len(starts)} 处)" if len(starts) > 6 else ""
        return (f"错误: old_string 在文件中出现 {count} 次,不唯一({shown}{more})。"
                "请补充更多上下文使其唯一,或设置 replace_all=true 全部替换。")

    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8", newline="\n")
    n = count if replace_all else 1
    return f"已编辑 {path}(替换 {n} 处)。"


# ---------------------------------------------------------------- 结构大纲
# 借鉴 aider 的 repo map 思路,做的是 stdlib 简化版:没有 tree-sitter、没有
# 引用图排序 —— 我们给的是"一眼看清工作区有什么",不是"猜你正在用哪个符号"。
# 探索任务先调它,少几轮 glob+grep。

_OUTLINE_SKIP = {
    # 依赖与缓存
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".idea",
    ".pytest_cache", ".ruff_cache", "kb-vectors", "models",
    # 构建产物与运行时产物
    "dist", "build", "_internal", "crawled", "browser-profile",
    "uploads", "images", "charts", "sessions", "memory", "kb",
}

_OUTLINE_DIR_DEPTH = 6        # 目录最多下钻几层:再深是依赖或数据,不是代码


def _outline_symbols(py_path: Path) -> str:
    """一个 .py 文件的顶层符号(def/class/大写常量);解析不了就照实说。"""
    import ast
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError, ValueError):
        return "(解析不了,可能不是源码或已损坏)"
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(f"def {node.name}")
        elif isinstance(node, ast.ClassDef):
            names.append(f"class {node.name}")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    names.append(t.id)      # 只收大写常量,小写赋值是噪声
    body = ", ".join(names[:12]) + ("…" if len(names) > 12 else "")
    return body or "(无顶层符号)"


def repo_outline(ctx: ToolContext, path: str = "", max_entries: int = 120) -> str:
    """工作区结构大纲:目录树 + 每个 Python 文件的顶层符号,一次调用看清全貌。"""
    root = resolve_path(ctx, path or ".", for_write=False)
    if not root.is_dir():
        return f"错误: {path or '.'} 不是目录。"
    try:
        max_entries = max(10, min(int(max_entries or 120), 400))
    except (TypeError, ValueError):
        max_entries = 120

    lines: list[str] = []
    counted = 0
    truncated = False

    def _walk(d: Path, prefix: str, depth: int) -> None:
        nonlocal counted, truncated
        if truncated or depth > _OUTLINE_DIR_DEPTH:
            return
        try:
            children = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if truncated:
                return
            name = child.name
            if name in _OUTLINE_SKIP or name.startswith("."):
                continue                    # 隐藏文件(含 .env)与运行时目录不成大纲
            if child.is_dir():
                counted += 1
                lines.append(f"{prefix}{name}/")
                _walk(child, prefix + "  ", depth + 1)
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if size > (1 << 20):
                counted += 1
                lines.append(f"{prefix}{name}(大文件 {size // 1048576} MB+,略)")
            elif name.endswith(".py"):
                counted += 1
                lines.append(f"{prefix}{name}: {_outline_symbols(child)}")
            else:
                counted += 1
                lines.append(f"{prefix}{name}")
            if counted >= max_entries:
                truncated = True

    _walk(root, "", 0)
    if not counted:
        return "这个目录是空的(或只有被跳过的运行时目录/隐藏文件)。"
    tail = ("\n…(超出条目上限只列到这里 —— 用 path 参数缩小范围再看)"
            if truncated else "")
    return f"工作区结构大纲({counted} 项):\n" + "\n".join(lines) + tail
