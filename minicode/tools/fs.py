"""文件读写与精确编辑工具。"""
from __future__ import annotations

import os
from pathlib import Path

from minicode.tools.base import (
    PathBlocked, PathOutsideWorkspace, ToolContext, resolve_path,
)

READ_LINE_LIMIT = 2000  # 默认单次最多读取行数
LINE_CHAR_LIMIT = 2000  # 单行超长截断
BINARY_SNIFF_BYTES = 8192

_PATH_ERRORS = (PathOutsideWorkspace, PathBlocked)


def _decode(data: bytes) -> str:
    return _decode_full(data)[0]


def _decode_full(data: bytes) -> "tuple[str, str]":
    """解出文本**与它用的编码**(#49a 的另一面)。

    旧写法是"UTF-8 失败就按 GBK 解",但写回时一律 UTF-8 —— 同学那份 GBK 中文稿
    被学长改一行就整份转成 UTF-8,记事本打开变乱码。这与行尾那件事是同一个毛病,
    而且更坏:行尾错了 `git diff` 会显示"每一行都改过",编码错了是**字直接不对了**。
    所以读侧记下用了哪种编码,写侧沿同一种回去。
    """
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace"), "gbk"


def _unreadable(text: str) -> bool:
    """解出来带替换符(U+FFFD)= 有字节我们没读懂。改下去就是把猜的写进用户文件。"""
    return "\ufffd" in text


def _read_text(p: Path) -> str:
    return _decode(p.read_bytes())


def _read_text_full(p: Path) -> "tuple[str, str]":
    return _decode_full(p.read_bytes())



# ---------------------------------------------------------------- 行尾保真(#49a)
#
# 两件不同的毛病一起修:
# ① 写侧 —— `write_text(..., newline="\n")` 把同学那份 CRLF 文件整份转成 LF,
#    他的 `git diff` 于是写着"每一行都改过"。
# ② 读侧匹配 —— `edit_file` 拿 `text.count(old_string)` 做**逐字**匹配,而文件里是
#    `\r\n`、模型从 read_file 看到的是 `\n`(`splitlines` 把行尾全剥了),所以 CRLF
#    文件上的多行 old_string **今天根本匹配不上**,只会得到"未在文件中找到"。
# 合起来的修法:纯一种行尾的文件,匹配在归一化后的文本上做,写回时按**它自己原来那种**
# 行尾还原 —— 未触碰的行字节完全不变。混用行尾的文件不整体重写(那是别人有意或
# 历史留下的混排,统一它不是学长该替用户做的决定),保持逐字匹配的旧行为。
def _dominant_eol(text: str) -> "str | None":
    """这份文本通篇用的是哪一种行尾;混用或只有一行没有行尾 → None(不替它决定)。"""
    found: "set[str]" = set()
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\r":
            found.add("\r\n" if text[i + 1:i + 2] == "\n" else "\r")
            i += 2 if text[i + 1:i + 2] == "\n" else 1
        elif ch == "\n":
            found.add("\n")
            i += 1
        else:
            i += 1
        if len(found) > 1:
            return None
    return found.pop() if found else None


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_exact(p: Path, text: str, enc: str = "utf-8") -> None:
    """按字节落盘:不让任何一层的换行翻译再动一次已经算清楚的行尾与编码。"""
    p.write_bytes(text.encode(enc, errors="strict"))


def _eol_note(eol: "str | None") -> str:
    return {"\r\n": "，沿用原文件的 CRLF 行尾", "\r": "，沿用原文件的 CR 行尾"}.get(eol or "", "")


def _form_note(eol: "str | None", enc: str) -> str:
    """把"沿用了什么"说出来 —— 悄悄沿用与悄悄改掉同样糟,用户看 diff 时得知道为什么。"""
    tail = _eol_note(eol)
    if enc != "utf-8":
        tail += f"，并沿用原文件的 {enc} 编码"
    return tail


def _mixed_eol_hint(text: str, needle: str) -> bool:
    """该不该说出"这份文件行尾混用"那句:只在真混用、且要匹配的东西跨行时才说。
    单行 needle 与行尾无关,提它就是无端插一句。"""
    return "\n" in needle and _dominant_eol(text) is None and "\r\n" in text


def _existing_form(p: Path) -> "tuple[str | None, str]":
    """已存在那份文件用的行尾与编码。二进制-sniff 命中就回 (None, utf-8):
    那份东西本来不该按文本改,行尾与编码都无从"沿用"。"""
    try:
        raw = p.read_bytes()
    except OSError:
        return None, "utf-8"
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        return None, "utf-8"
    text, enc = _decode_full(raw)
    return _dominant_eol(text), enc



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
    """写入(创建或覆盖)一个文本文件。行尾与编码都沿用原文件那一种(#49a)。"""
    try:
        p = resolve_path(ctx, path, for_write=True)
    except _PATH_ERRORS as e:
        return f"错误: {e}"
    exists = p.exists()
    prev_eol, prev_enc = _existing_form(p) if exists else (None, "utf-8")
    action = "覆盖" if exists else "创建"
    p.parent.mkdir(parents=True, exist_ok=True)
    out = _to_lf(content).replace("\n", prev_eol) if prev_eol in ("\r\n", "\r") else content
    try:
        _write_exact(p, out, prev_enc)
    except (UnicodeEncodeError, LookupError) as e:
        # 模型给的字这份编码装不下(GB2312 里没有的字很常见):不降级成 UTF-8,
        # 那等于替用户决定"你的稿子换个编码吧"。
        return (f"错误: 这些字用 {prev_enc} 编码写不下({type(e).__name__}),"
                "这个文件没被改动。要么让我按 UTF-8 重写(整份编码会变,要你点头),"
                "要么换掉那些字。")
    n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return (f"已{action}文件 {path}({n_lines} 行,{len(content)} 字符)。"
            + _form_note(prev_eol, prev_enc))




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

    text, enc = _read_text_full(p)
    if _unreadable(text):
        return ("错误: 这份文件里有读不出来的字节(多半是 GBK 之外的编码),"
                "学长不猜着改 —— 改下去会把猜出来的字永久写进你的文件。"
                "可以先把它另存成 UTF-8(记事本另存时选 UTF-8),再来找我改。")
    # 纯一种行尾的文件:匹配在归一化(\n)后的文本上做 —— 模型从 read_file 看到的
    # 就是 \n,拿 \n 去逐字比 CRLF 文件永远不中(那是 #49a 的第二面)。
    # 混用行尾的文件:保持逐字匹配的原样,不替它把整份统一掉。

    eol = _dominant_eol(text)
    folded = eol in ("\r\n", "\r")
    hay = _to_lf(text) if folded else text
    needle = _to_lf(old_string) if folded else old_string
    repl = _to_lf(new_string) if folded else new_string
    count = hay.count(needle)
    if count == 0:
        # 反馈"最接近的行"而不是只说没找到:模型拿着行号与相似度一回合就能
        # 自我修正,省掉"再 read_file 一遍"的整轮等待。只报告,绝不替它猜着改。
        hints = _nearest_lines(hay, needle)
        extra = "".join(f"\n  第 {n} 行(相似度 {ratio:.0%}):{line}"
                        for n, ratio, line in hints)
        return ("错误: old_string 未在文件中找到(空格、缩进与换行必须完全一致)。"
                + ("文件里最接近的几行:" + extra if hints else
                   "文件里找不到相近的行 —— 可能要改的是别的文件,先 read_file 核对。")
                + ("" if not _mixed_eol_hint(text, needle) else
                   "\n(这份文件的行尾是 CRLF/LF 混用,学长不替它统一 —— "
                   "old_string 要按原文逐字给,含 \\r\\n 的那种。)")
                )
    if count > 1 and not replace_all:
        starts = _match_lines(hay, needle)
        shown = ", ".join(f"第 {n} 行" for n in starts[:6])
        more = f"(共 {len(starts)} 处)" if len(starts) > 6 else ""
        return (f"错误: old_string 在文件中出现 {count} 次,不唯一({shown}{more})。"
                "请补充更多上下文使其唯一,或设置 replace_all=true 全部替换。")

    new_text = hay.replace(needle, repl) if replace_all else hay.replace(needle, repl, 1)
    if folded:
        new_text = new_text.replace("\n", eol)   # 按原文件那种行尾还原
    try:
        _write_exact(p, new_text, enc)
    except (UnicodeEncodeError, LookupError) as e:
        return (f"错误: 改完的内容用 {enc} 编码装不下({type(e).__name__}),"
                "文件一个字都没动。要么换掉那几个字,要么由你把文件先转成 UTF-8。")
    n = count if replace_all else 1
    return f"已编辑 {path}(替换 {n} 处)。" + _form_note(eol, enc)



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
    # 超长工具输出的转存地:那是"这一步跑完的原始料",不是项目文件,别让它在
    # @ 引用补全与 repo_outline 里冒充代码/文档
    "tool-outputs",
}

_OUTLINE_DIR_DEPTH = 6        # 目录最多下钻几层:再深是依赖或数据,不是代码


def search_files(ctx: ToolContext, query: str = "", limit: int = 20) -> list[str]:
    """工作区内按名字搜文件,返回相对路径(posix 斜杠)—— @ 引用的数据源。

    跳过的目录与 repo_outline 同一份 `_OUTLINE_SKIP`(依赖/产物/会话数据,
    那些地方没有值得引用的文件);空 query 返回工作区顶层与浅层文件。
    只读操作:绝不触碰任何文件内容。
    """
    root = Path(ctx.workspace)
    if not root.is_dir():
        return []
    q = str(query or "").strip().lower()
    limit = max(1, min(int(limit or 20), 50))
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            parts = Path(rel_dir).parts
            if any(part in _OUTLINE_SKIP or part.startswith(".") for part in parts):
                dirnames[:] = []
                continue
        dirnames[:] = [d for d in dirnames
                       if d not in _OUTLINE_SKIP and not d.startswith(".")]
        for fn in filenames:
            rel = Path(os.path.relpath(os.path.join(dirpath, fn), root)).as_posix()
            if not q or q in rel.lower():
                hits.append(rel)
        if len(hits) >= limit * 4:
            break
    # 浅层优先:引用的常是工作区顶层的代码与文档,不是深处依赖
    hits.sort(key=lambda s: (s.count("/"), len(s), s.lower()))
    return hits[:limit]


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
