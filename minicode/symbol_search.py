"""代码库符号索引与精准搜索模块 (v0.9.165)。

用于在当前工作区所有源码中快速定位类、函数、方法、接口、结构体、类型定义与常量。
纯标准库实现(ast + 正则),零外部依赖,只读安全。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

# 忽略的噪音与产物目录
IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".venv", "venv", "env",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "dist", "build", "target", "out",
    ".idea", ".vscode", "crawled", "uploads", "sessions",
    "vendor", ".turbo", ".next", ".nuxt", "coverage",
}

# 支持的后缀映射
SUPPORTED_EXTS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

MAX_FILE_BYTES = 1024 * 1024  # 单文件 1MB 上限
DEFAULT_MAX_RESULTS = 50
MAX_SEARCH_RESULTS = 200

# 多语言正则定义
_RE_JS_CLASS = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$.]+))?")
_RE_JS_INTERFACE = re.compile(r"^(?:export\s+)?interface\s+([A-Za-z0-9_$]+)")
_RE_JS_TYPE = re.compile(r"^(?:export\s+)?type\s+([A-Za-z0-9_$]+)\s*=")
_RE_JS_FUNC = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)")
_RE_JS_ARROW = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>")

_RE_GO_METHOD = re.compile(r"^func\s+\(([^)]+)\)\s*([A-Za-z0-9_]+)\s*\(([^)]*)\)")
_RE_GO_FUNC = re.compile(r"^func\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)")
_RE_GO_STRUCT = re.compile(r"^type\s+([A-Za-z0-9_]+)\s+struct\b")
_RE_GO_INTERFACE = re.compile(r"^type\s+([A-Za-z0-9_]+)\s+interface\b")

_RE_RS_FN = re.compile(r"^(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)")
_RE_RS_STRUCT = re.compile(r"^(?:pub(?:\([^)]+\))?\s+)?struct\s+([A-Za-z0-9_]+)")
_RE_RS_ENUM = re.compile(r"^(?:pub(?:\([^)]+\))?\s+)?enum\s+([A-Za-z0-9_]+)")
_RE_RS_TRAIT = re.compile(r"^(?:pub(?:\([^)]+\))?\s+)?trait\s+([A-Za-z0-9_]+)")

_RE_JAVA_CLASS = re.compile(r"^(?:public|protected|private)?\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z0-9_]+)")
_RE_JAVA_INTERFACE = re.compile(r"^(?:public|protected|private)?\s*interface\s+([A-Za-z0-9_]+)")
_RE_JAVA_METHOD = re.compile(r"^(?:public|protected|private)\s+(?:static\s+)?[A-Za-z0-9_<>[\]]+\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)")


def _safe_rel(ws_dir: Path, p: Path) -> str | None:
    try:
        return p.resolve().relative_to(ws_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def extract_python_symbols(code: str, rel_path: str) -> list[dict[str, Any]]:
    """使用 AST 提取 Python 文件中的符号。语法异常时安全降级为空列表。"""
    symbols: list[dict[str, Any]] = []
    try:
        tree = ast.parse(code, filename=rel_path)
    except (SyntaxError, ValueError):
        return _extract_python_regex(code, rel_path)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases_str = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
            sig = f"class {node.name}" + (f"({bases_str})" if bases_str else "")
            doc = (ast.get_docstring(node) or "").split("\n", 1)[0].strip()
            symbols.append({
                "name": node.name,
                "kind": "class",
                "file": rel_path,
                "line": node.lineno,
                "parent": "",
                "signature": sig,
                "doc": doc,
            })
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    try:
                        args_str = ast.unparse(item.args)
                    except Exception:
                        args_str = "..."
                    prefix = "async def " if isinstance(item, ast.AsyncFunctionDef) else "def "
                    m_sig = f"{prefix}{item.name}({args_str})"
                    m_doc = (ast.get_docstring(item) or "").split("\n", 1)[0].strip()
                    symbols.append({
                        "name": item.name,
                        "kind": "method",
                        "file": rel_path,
                        "line": item.lineno,
                        "parent": node.name,
                        "signature": m_sig,
                        "doc": m_doc,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                args_str = ast.unparse(node.args)
            except Exception:
                args_str = "..."
            prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
            sig = f"{prefix}{node.name}({args_str})"
            doc = (ast.get_docstring(node) or "").split("\n", 1)[0].strip()
            symbols.append({
                "name": node.name,
                "kind": "function",
                "file": rel_path,
                "line": node.lineno,
                "parent": "",
                "signature": sig,
                "doc": doc,
            })
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1:
                    symbols.append({
                        "name": t.id,
                        "kind": "constant",
                        "file": rel_path,
                        "line": node.lineno,
                        "parent": "",
                        "signature": f"{t.id} = ...",
                        "doc": "",
                    })
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.isupper() and len(node.target.id) > 1:
                symbols.append({
                    "name": node.target.id,
                    "kind": "constant",
                    "file": rel_path,
                    "line": node.lineno,
                    "parent": "",
                    "signature": f"{node.target.id} = ...",
                    "doc": "",
                })
    return symbols


def _extract_python_regex(code: str, rel_path: str) -> list[dict[str, Any]]:
    """AST 解析失败时的正则降级。"""
    symbols: list[dict[str, Any]] = []
    lines = code.splitlines()
    for idx, line in enumerate(lines, 1):
        s = line.strip()
        m_cls = re.match(r"^class\s+([A-Za-z0-9_]+)", s)
        if m_cls:
            symbols.append({
                "name": m_cls.group(1), "kind": "class", "file": rel_path,
                "line": idx, "parent": "", "signature": s.rstrip(":"), "doc": "",
            })
            continue
        m_fn = re.match(r"^(?:async\s+)?def\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)", s)
        if m_fn:
            kind = "method" if line.startswith(("    ", "\t")) else "function"
            symbols.append({
                "name": m_fn.group(1), "kind": kind, "file": rel_path,
                "line": idx, "parent": "", "signature": s.rstrip(":"), "doc": "",
            })
    return symbols


def extract_multilang_symbols(code: str, rel_path: str, lang: str) -> list[dict[str, Any]]:
    """正则提取多语言符号。"""
    symbols: list[dict[str, Any]] = []
    lines = code.splitlines()
    for idx, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith(("//", "/*", "*", "#")):
            continue

        if lang in ("javascript", "typescript"):
            m = _RE_JS_CLASS.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "class", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JS_INTERFACE.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "interface", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JS_TYPE.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "type", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JS_FUNC.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "function", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JS_ARROW.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "function", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue

        elif lang == "go":
            m = _RE_GO_METHOD.match(s)
            if m:
                symbols.append({"name": m.group(2), "kind": "method", "file": rel_path,
                                "line": idx, "parent": m.group(1).strip(), "signature": s, "doc": ""})
                continue
            m = _RE_GO_FUNC.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "function", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_GO_STRUCT.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "struct", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_GO_INTERFACE.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "interface", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue

        elif lang == "rust":
            m = _RE_RS_FN.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "function", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_RS_STRUCT.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "struct", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_RS_ENUM.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "enum", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_RS_TRAIT.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "interface", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue

        elif lang == "java":
            m = _RE_JAVA_CLASS.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "class", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JAVA_INTERFACE.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "interface", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue
            m = _RE_JAVA_METHOD.match(s)
            if m:
                symbols.append({"name": m.group(1), "kind": "method", "file": rel_path,
                                "line": idx, "parent": "", "signature": s, "doc": ""})
                continue

    return symbols


def extract_file_symbols(file_path: Path, rel_path: str) -> list[dict[str, Any]]:
    """提取单个文件中的符号。"""
    ext = file_path.suffix.lower()
    lang = SUPPORTED_EXTS.get(ext)
    if not lang:
        return []
    try:
        st = file_path.stat()
        if st.st_size > MAX_FILE_BYTES or st.st_size == 0:
            return []
        code = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if lang == "python":
        return extract_python_symbols(code, rel_path)
    return extract_multilang_symbols(code, rel_path, lang)


def search_symbols(
    workspace_dir: Path | str,
    query: str = "",
    kind: str = "all",
    path: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """在工作区所有源码中搜索符号。

    query: 符号名关键词(不区分大小写模糊匹配)
    kind: 符号类别,如 class, function, method, interface, struct, constant, all
    path: 限定相对路径或子目录
    max_results: 返回条数上限
    """
    ws = Path(workspace_dir).resolve()
    if not ws.exists():
        return {"ok": False, "msg": f"工作区不存在: {workspace_dir}"}

    search_root = ws
    if path and path.strip():
        p_clean = path.strip().replace("\\", "/").strip("/")
        candidate = (ws / p_clean).resolve()
        try:
            candidate.relative_to(ws)
            if candidate.exists():
                search_root = candidate
        except (ValueError, OSError):
            return {"ok": False, "msg": f"指定路径超出工作区围栏: {path}"}

    query_str = (query or "").strip().lower()
    kind_filter = (kind or "all").strip().lower()

    all_symbols: list[dict[str, Any]] = []

    if search_root.is_file():
        rel = _safe_rel(ws, search_root)
        if rel:
            all_symbols.extend(extract_file_symbols(search_root, rel))
    else:
        for root, dirs, files in os.walk(str(search_root)):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in files:
                p = Path(root) / f
                if p.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                rel = _safe_rel(ws, p)
                if not rel:
                    continue
                all_symbols.extend(extract_file_symbols(p, rel))

    # 过滤符号
    filtered: list[dict[str, Any]] = []
    for sym in all_symbols:
        s_kind = sym["kind"].lower()
        if kind_filter != "all" and s_kind != kind_filter:
            continue
        s_name = sym["name"].lower()
        if query_str:
            if query_str not in s_name:
                continue
        filtered.append(sym)

    # 排序优先级: 完全匹配 > 前缀匹配 > 其他
    if query_str:
        def _score(item: dict[str, Any]) -> tuple[int, str]:
            n = item["name"].lower()
            if n == query_str:
                return (0, n)
            if n.startswith(query_str):
                return (1, n)
            return (2, n)
        filtered.sort(key=_score)

    total_found = len(filtered)
    limit = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_SEARCH_RESULTS))
    truncated = total_found > limit
    returned_symbols = filtered[:limit]

    return {
        "ok": True,
        "query": query,
        "kind": kind_filter,
        "total_found": total_found,
        "truncated": truncated,
        "symbols": returned_symbols,
        "msg": "",
    }


def format_symbols_view(result: dict[str, Any]) -> str:
    """将符号搜索结果格式化为易于阅读的文本。"""
    if not result.get("ok"):
        return f"符号搜索失败: {result.get('msg', '未知错误')}"

    symbols = result.get("symbols", [])
    total = result.get("total_found", 0)
    q = result.get("query", "")
    k = result.get("kind", "all")

    if not symbols:
        return f"未在工作区找到匹配的符号 (query='{q}', kind='{k}')。"

    lines = [f"【符号搜索结果】(匹配 {total} 项" + (f", 显示前 {len(symbols)} 项):" if len(symbols) < total else "):")]

    for s in symbols:
        k_tag = f"[{s['kind']}]"
        parent_tag = f" (in {s['parent']})" if s.get("parent") else ""
        lines.append(f"\n• {k_tag} {s['name']}{parent_tag}")
        lines.append(f"  位置: {s['file']}:{s['line']}")
        if s.get("signature"):
            lines.append(f"  签名: {s['signature']}")
        if s.get("doc"):
            lines.append(f"  说明: {s['doc']}")

    return "\n".join(lines)


def symbol_search_tool(
    query: str = "",
    kind: str = "all",
    path: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
    ctx: Any = None,
) -> str:
    """内置工具 symbol_search 的入口实现。"""
    ws = getattr(ctx, "workspace", None) if ctx else None
    if not ws:
        return "错误: 无法获取当前工作区路径。"
    res = search_symbols(
        workspace_dir=ws,
        query=query,
        kind=kind,
        path=path,
        max_results=max_results,
    )
    return format_symbols_view(res)
