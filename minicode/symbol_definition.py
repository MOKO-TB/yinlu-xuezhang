"""跨文件代码符号定义提取与实现跳转模块 (v0.9.169)。

用于在当前工作区中精准定位函数、类、方法、类型与常量的定义位置与完整实现代码块。
基于 Python AST 语法树与多语言词法/语法边界分析，支持导入追踪(resolve import)与
定义代码直读(peek implementation)，零额外读文件开销，只读安全。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from minicode.tools.base import ToolContext

# 忽略的构建噪音与产物目录
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
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

MAX_FILE_BYTES = 1024 * 1024  # 单文件 1MB 上限
DEFAULT_MAX_RESULTS = 10


def _safe_rel(ws_dir: Path, p: Path) -> str | None:
    try:
        return p.resolve().relative_to(ws_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def extract_python_definition(code: str, symbol: str, rel_path: str) -> list[dict[str, Any]]:
    """使用 AST 从 Python 源码中精确提取符号的定义位置与完整代码块。"""
    definitions: list[dict[str, Any]] = []
    lines = code.splitlines(keepends=True)

    try:
        tree = ast.parse(code, filename=rel_path)
    except (SyntaxError, ValueError):
        return _extract_python_regex(code, symbol, rel_path)

    # 检查是否包含类名限定, 如 `MyClass.my_method`
    parent_target = ""
    member_target = symbol
    if "." in symbol:
        parts = symbol.split(".", 1)
        parent_target, member_target = parts[0], parts[1]

    for node in tree.body:
        # 1. 顶层类定义
        if isinstance(node, ast.ClassDef):
            if (not parent_target and node.name == member_target) or (parent_target and node.name == parent_target and not member_target):
                def_info = _build_py_node_info(node, lines, rel_path, "class")
                definitions.append(def_info)

            # 类内部方法或嵌套类查找
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (parent_target and node.name == parent_target and child.name == member_target) or \
                       (not parent_target and child.name == member_target):
                        kind = "async_method" if isinstance(child, ast.AsyncFunctionDef) else "method"
                        def_info = _build_py_node_info(child, lines, rel_path, kind, parent=node.name)
                        definitions.append(def_info)
                elif isinstance(child, ast.ClassDef):
                    if (parent_target and node.name == parent_target and child.name == member_target) or \
                       (not parent_target and child.name == member_target):
                        def_info = _build_py_node_info(child, lines, rel_path, "class", parent=node.name)
                        definitions.append(def_info)

        # 2. 顶层函数定义
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not parent_target and node.name == member_target:
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                def_info = _build_py_node_info(node, lines, rel_path, kind)
                definitions.append(def_info)

        # 3. 顶层变量/常量赋值定义
        elif isinstance(node, ast.Assign) and not parent_target:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == member_target:
                    def_info = _build_py_node_info(node, lines, rel_path, "constant" if member_target.isupper() else "variable")
                    definitions.append(def_info)
                    break
        elif isinstance(node, ast.AnnAssign) and not parent_target:
            if isinstance(node.target, ast.Name) and node.target.id == member_target:
                def_info = _build_py_node_info(node, lines, rel_path, "constant" if member_target.isupper() else "variable")
                definitions.append(def_info)

    return definitions


def _build_py_node_info(
    node: ast.AST, lines: list[str], rel_path: str, kind: str, parent: str = ""
) -> dict[str, Any]:
    # 起始行号包含装饰器
    start_line = node.lineno
    if hasattr(node, "decorator_list") and node.decorator_list:
        start_line = min(d.lineno for d in node.decorator_list)

    end_line = getattr(node, "end_lineno", node.lineno)

    # 提取完整实现代码片段
    snippet_lines = lines[start_line - 1:end_line]
    code_snippet = "".join(snippet_lines).rstrip()

    # 签名与文档
    sig = ""
    doc = ""
    name = getattr(node, "name", "")
    if hasattr(node, "name"):
        if kind in ("function", "method", "async_function", "async_method"):
            prefix = "async def " if "async" in kind else "def "
            try:
                args_str = ast.unparse(node.args)  # type: ignore[attr-defined]
            except Exception:
                args_str = "..."
            ret_str = ""
            if getattr(node, "returns", None):
                try:
                    ret_str = f" -> {ast.unparse(node.returns)}"  # type: ignore[attr-defined]
                except Exception:
                    pass
            sig = f"{prefix}{name}({args_str}){ret_str}"
            doc = (ast.get_docstring(node) or "").strip()
        elif kind == "class":
            bases = [ast.unparse(b) for b in getattr(node, "bases", [])]  # type: ignore[attr-defined]
            bases_str = f"({', '.join(bases)})" if bases else ""
            sig = f"class {name}{bases_str}:"
            doc = (ast.get_docstring(node) or "").strip()
    elif kind in ("constant", "variable"):
        name = lines[node.lineno - 1].strip().split("=")[0].strip()
        sig = lines[node.lineno - 1].strip()

    return {
        "name": name,
        "kind": kind,
        "file": rel_path,
        "line": start_line,
        "end_line": end_line,
        "parent": parent,
        "signature": sig,
        "docstring": doc,
        "code": code_snippet,
    }


def _extract_python_regex(code: str, symbol: str, rel_path: str) -> list[dict[str, Any]]:
    """AST 解析失败时的正则回退。"""
    definitions: list[dict[str, Any]] = []
    lines = code.splitlines()
    pattern = re.compile(rf"^\s*(?:async\s+)?(?:def|class)\s+({re.escape(symbol)})\b")

    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            # 简单估计结束行: 寻找相同或更小缩进的下一非空行
            indent = len(line) - len(line.lstrip())
            end_line = i
            for j in range(i, min(i + 150, len(lines))):
                next_line = lines[j]
                if next_line.strip() and (len(next_line) - len(next_line.lstrip())) <= indent:
                    end_line = j
                    break
                end_line = j + 1

            definitions.append({
                "name": symbol,
                "kind": "class" if "class " in line else "function",
                "file": rel_path,
                "line": i,
                "end_line": end_line,
                "parent": "",
                "signature": line.strip(),
                "docstring": "",
                "code": "\n".join(lines[i - 1:end_line]),
            })
    return definitions


def extract_multilang_definition(code: str, symbol: str, rel_path: str, lang: str) -> list[dict[str, Any]]:
    """提取 JS/TS、Go、Rust、Java、C/C++ 等语言中的符号定义与代码块。"""
    definitions: list[dict[str, Any]] = []
    lines = code.splitlines()

    # 针对不同语言的声明正则模式
    patterns: list[tuple[re.Pattern, str]] = []
    sym_escaped = re.escape(symbol)

    if lang in ("javascript", "typescript"):
        patterns.append((re.compile(rf"(?:export\s+)?(?:default\s+)?class\s+{sym_escaped}\b"), "class"))
        patterns.append((re.compile(rf"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+{sym_escaped}\b"), "function"))
        patterns.append((re.compile(rf"(?:export\s+)?(?:const|let|var)\s+{sym_escaped}\s*=\s*(?:async\s*)?\("), "function"))
        patterns.append((re.compile(rf"(?:export\s+)?interface\s+{sym_escaped}\b"), "interface"))
        patterns.append((re.compile(rf"(?:export\s+)?type\s+{sym_escaped}\s*="), "type"))
    elif lang == "go":
        patterns.append((re.compile(rf"func\s+(?:\([^)]+\)\s+)?{sym_escaped}\s*\("), "function"))
        patterns.append((re.compile(rf"type\s+{sym_escaped}\s+struct\b"), "struct"))
        patterns.append((re.compile(rf"type\s+{sym_escaped}\s+interface\b"), "interface"))
    elif lang == "rust":
        patterns.append((re.compile(rf"(?:pub\s+)?(?:async\s+)?fn\s+{sym_escaped}\b"), "function"))
        patterns.append((re.compile(rf"(?:pub\s+)?struct\s+{sym_escaped}\b"), "struct"))
        patterns.append((re.compile(rf"(?:pub\s+)?enum\s+{sym_escaped}\b"), "enum"))
        patterns.append((re.compile(rf"(?:pub\s+)?trait\s+{sym_escaped}\b"), "trait"))
    elif lang in ("java", "c", "cpp"):
        patterns.append((re.compile(rf"(?:class|struct|interface)\s+{sym_escaped}\b"), "class"))
        patterns.append((re.compile(rf"(?:[A-Za-z0-9_<>[\]*&]+\s+)+{sym_escaped}\s*\("), "function"))

    for i, line in enumerate(lines, 1):
        for pat, kind in patterns:
            if pat.search(line):
                # 寻找匹配的花括号结束行
                end_line = _find_matching_brace_end(lines, i - 1)
                definitions.append({
                    "name": symbol,
                    "kind": kind,
                    "file": rel_path,
                    "line": i,
                    "end_line": end_line,
                    "parent": "",
                    "signature": line.strip(),
                    "docstring": "",
                    "code": "\n".join(lines[i - 1:end_line]),
                })
                break
    return definitions


def _find_matching_brace_end(lines: list[str], start_idx: int) -> int:
    """从起始行向后寻找匹配的花括号闭合行。若无花括号则默认返回至单行或下一个顶格行。"""
    brace_depth = 0
    found_open = False

    for idx in range(start_idx, min(start_idx + 250, len(lines))):
        line = lines[idx]
        for ch in line:
            if ch == "{":
                brace_depth += 1
                found_open = True
            elif ch == "}":
                brace_depth -= 1
                if found_open and brace_depth <= 0:
                    return idx + 1
        # 如果未找到任何花括号且遇到空行或分号
        if not found_open and (line.strip().endswith(";") or (idx > start_idx and not line.strip())):
            return idx + 1

    return min(start_idx + 30, len(lines))


def resolve_import_definition(ws_dir: Path, from_file: str, symbol: str) -> dict[str, Any] | None:
    """若在当前文件中该符号是通过 import/from 导入的，追踪其源文件与源定义位置。"""
    src_path = ws_dir / from_file
    if not src_path.exists() or src_path.suffix != ".py":
        return None

    try:
        code = src_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(code, filename=from_file)
    except Exception:
        return None

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name_used = alias.asname or alias.name
                if name_used == symbol:
                    # 获取原始导入名字与模块
                    orig_name = alias.name
                    mod = node.module or ""
                    # 处理相对导入
                    if node.level > 0:
                        curr_dir = (ws_dir / from_file).parent
                        for _ in range(node.level - 1):
                            curr_dir = curr_dir.parent
                        # 查找相对路径文件
                        candidate = curr_dir / (mod.replace(".", "/") + ".py")
                        if candidate.exists():
                            rel_target = _safe_rel(ws_dir, candidate)
                            if rel_target:
                                return _extract_from_resolved_file(ws_dir, rel_target, orig_name)
                    else:
                        # 绝对导入: 如 minicode.call_hierarchy
                        mod_path = mod.replace(".", "/") + ".py"
                        candidate = ws_dir / mod_path
                        if candidate.exists():
                            rel_target = _safe_rel(ws_dir, candidate)
                            if rel_target:
                                return _extract_from_resolved_file(ws_dir, rel_target, orig_name)
                        # 包的 __init__.py
                        init_candidate = ws_dir / mod.replace(".", "/") / "__init__.py"
                        if init_candidate.exists():
                            rel_target = _safe_rel(ws_dir, init_candidate)
                            if rel_target:
                                return _extract_from_resolved_file(ws_dir, rel_target, orig_name)

    return None


def _extract_from_resolved_file(ws_dir: Path, rel_file: str, orig_name: str) -> dict[str, Any] | None:
    full_path = ws_dir / rel_file
    if not full_path.exists():
        return None
    code = full_path.read_text(encoding="utf-8", errors="replace")
    defs = extract_python_definition(code, orig_name, rel_file)
    if defs:
        res = defs[0].copy()
        res["resolved_from_import"] = True
        return res
    return None


def find_symbol_definition(
    workspace: str | Path,
    symbol: str,
    path: str | None = None,
    peek: bool = True,
    resolve_import: bool = True,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """在工作区中查找符号定义。"""
    ws_dir = Path(workspace).resolve()
    if not ws_dir.exists() or not ws_dir.is_dir():
        return {"ok": False, "msg": f"工作区不存在: {ws_dir}"}

    clean_symbol = symbol.strip()
    if not clean_symbol:
        return {"ok": False, "msg": "未指定要查找的符号名称"}

    results: list[dict[str, Any]] = []

    # 1. 若指定了限定文件路径
    if path:
        target_file = ws_dir / path.strip().lstrip("/\\")
        if target_file.exists() and target_file.is_file():
            rel_file = _safe_rel(ws_dir, target_file) or path
            ext = target_file.suffix.lower()
            lang = SUPPORTED_EXTS.get(ext)
            if lang:
                code = target_file.read_text(encoding="utf-8", errors="replace")
                if lang == "python":
                    direct_defs = extract_python_definition(code, clean_symbol, rel_file)
                else:
                    direct_defs = extract_multilang_definition(code, clean_symbol, rel_file, lang)

                if direct_defs:
                    results.extend(direct_defs)
                elif resolve_import and lang == "python":
                    # 尝试导入解析
                    imported_def = resolve_import_definition(ws_dir, rel_file, clean_symbol)
                    if imported_def:
                        results.append(imported_def)

        if results:
            if not peek:
                for r in results:
                    r.pop("code", None)
            return {
                "ok": True,
                "symbol": clean_symbol,
                "count": len(results),
                "definitions": results[:max_results],
            }

    # 2. 全工作区扫描
    seen_keys: set[tuple[str, int]] = set()

    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        for file in files:
            p = Path(root) / file
            ext = p.suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                continue

            rel_file = _safe_rel(ws_dir, p)
            if not rel_file:
                continue

            lang = SUPPORTED_EXTS[ext]
            try:
                code = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # 快速字面量过滤: 符号名未在文本中出现则直接跳过
            base_sym = clean_symbol.split(".")[-1]
            if base_sym not in code:
                continue

            if lang == "python":
                file_defs = extract_python_definition(code, clean_symbol, rel_file)
            else:
                file_defs = extract_multilang_definition(code, clean_symbol, rel_file, lang)

            for d in file_defs:
                key = (d["file"], d["line"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(d)
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break

    if not peek:
        for r in results:
            r.pop("code", None)

    return {
        "ok": True,
        "symbol": clean_symbol,
        "count": len(results),
        "definitions": results[:max_results],
    }


def symbol_definition_tool(
    *args: Any,
    symbol: str = "",
    path: str = "",
    peek: bool = True,
    resolve_import: bool = True,
    **kwargs: Any,
) -> str:
    """symbol_definition 工具处理入口。"""
    ctx = kwargs.get("ctx")
    target_sym = symbol
    if args:
        if isinstance(args[0], ToolContext) or hasattr(args[0], "workspace"):
            ctx = args[0]
            if len(args) > 1 and isinstance(args[1], str):
                target_sym = args[1]
        elif isinstance(args[0], str):
            target_sym = args[0]
            if len(args) > 1 and (isinstance(args[1], ToolContext) or hasattr(args[1], "workspace")):
                ctx = args[1]

    ws = getattr(ctx, "workspace", None) if ctx else None
    if not ws:
        ws = Path.cwd()
    else:
        ws = Path(ws)

    res = find_symbol_definition(
        workspace=ws,
        symbol=target_sym,
        path=path or None,
        peek=peek,
        resolve_import=resolve_import,
    )

    if not res.get("ok"):
        return f"❌ 查找失败: {res.get('msg', '未知错误')}"

    defs = res.get("definitions", [])
    if not defs:
        target_info = f" 在 `{path}`" if path else ""
        return f"未找到符号 `{target_sym}` 的定义{target_info}。建议使用 `symbol_search` 进行模糊搜索。"

    output_lines: list[str] = [
        f"📍 找到 {len(defs)} 处符号 `{target_sym}` 的定义与实现位置:"
    ]

    for i, d in enumerate(defs, 1):
        rel_f = d.get("file", "")
        line_start = d.get("line", 1)
        line_end = d.get("end_line", line_start)
        kind = d.get("kind", "definition")
        sig = d.get("signature", "")
        code = d.get("code", "")
        doc = d.get("docstring", "")
        resolved_note = " (追踪自模块导入)" if d.get("resolved_from_import") else ""

        output_lines.append("")
        output_lines.append(f"### {i}. `{d['name']}` ({kind}){resolved_note}")
        output_lines.append(f"- 📄 位置: `{rel_f}:{line_start}` (行 {line_start} ~ {line_end})")
        if sig:
            output_lines.append(f"- 🏷 签名: `{sig}`")
        if doc:
            doc_preview = doc.splitlines()[0][:100]
            output_lines.append(f"- 📝 说明: {doc_preview}")

        if peek and code:
            ext = Path(rel_f).suffix.lstrip(".")
            lang = SUPPORTED_EXTS.get(f".{ext}", ext)
            output_lines.append(f"```{lang}")
            output_lines.append(code)
            output_lines.append("```")

    return "\n".join(output_lines)
