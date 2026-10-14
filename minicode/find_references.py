"""代码库引用查找与影响面分析模块 (v0.9.166)。

用于在当前工作区跨文件精准定位符号(类、函数、变量、方法)的所有调用、导入、继承、类型注解与使用位置。
纯标准库实现(ast + 正则边界分析),零外部依赖,只读安全。
"""
from __future__ import annotations

import ast
import fnmatch
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

# 支持的代码文件后缀
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
DEFAULT_MAX_RESULTS = 50
MAX_SEARCH_RESULTS = 200


def _safe_rel(ws_dir: Path, p: Path) -> str | None:
    try:
        return p.resolve().relative_to(ws_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _is_test_path(rel_path: str) -> bool:
    norm = rel_path.lower().replace("\\", "/")
    parts = norm.split("/")
    if any(p in ("tests", "test", "__tests__") for p in parts):
        return True
    stem = Path(norm).stem
    return stem.startswith("test_") or stem.endswith("_test") or stem.endswith(".test") or stem.endswith(".spec")


def extract_python_references(code: str, rel_path: str, symbol: str) -> list[dict[str, Any]]:
    """使用 AST 提取 Python 文件中对 symbol 的所有引用。解析失败自动降级到正则。"""
    try:
        tree = ast.parse(code, filename=rel_path)
    except (SyntaxError, ValueError):
        return extract_regex_references(code, rel_path, symbol, ".py")

    lines = code.splitlines()
    raw_refs: list[tuple[int, int, str]] = []  # (lineno, col_offset, kind)

    def _node_has_symbol(node: ast.AST | None) -> bool:
        if node is None:
            return False
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id == symbol:
                return True
            if isinstance(n, ast.Attribute) and n.attr == symbol:
                return True
        return False

    for node in ast.walk(tree):
        # 1. 函数/方法调用
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == symbol:
                raw_refs.append((node.lineno, node.col_offset, "call"))
            elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                raw_refs.append((node.lineno, node.col_offset, "call"))

        # 2. 导入
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == symbol or alias.asname == symbol:
                    raw_refs.append((node.lineno, node.col_offset, "import"))
        elif isinstance(node, ast.ImportFrom):
            mod_parts = (node.module or "").split(".")
            if symbol in mod_parts:
                raw_refs.append((node.lineno, node.col_offset, "import"))
            for alias in node.names:
                if alias.name == symbol or alias.asname == symbol:
                    raw_refs.append((node.lineno, node.col_offset, "import"))


        # 3. 继承基类
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if _node_has_symbol(base):
                    raw_refs.append((node.lineno, node.col_offset, "inherit"))

        # 4. 类型注解
        elif isinstance(node, ast.AnnAssign):
            if _node_has_symbol(node.annotation):
                raw_refs.append((node.lineno, node.col_offset, "type"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _node_has_symbol(node.returns):
                raw_refs.append((node.lineno, node.col_offset, "type"))
            for arg in node.args.args + node.args.kwonlyargs:
                if _node_has_symbol(arg.annotation):
                    raw_refs.append((node.lineno, node.col_offset, "type"))

        # 5. 赋值写入
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if _node_has_symbol(target):
                    raw_refs.append((node.lineno, node.col_offset, "assign"))
        elif isinstance(node, ast.AugAssign):
            if _node_has_symbol(node.target):
                raw_refs.append((node.lineno, node.col_offset, "assign"))

        # 6. 普通名称/属性使用
        elif isinstance(node, ast.Name) and node.id == symbol:
            kind = "assign" if isinstance(node.ctx, ast.Store) else "read"
            raw_refs.append((node.lineno, node.col_offset, kind))
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            kind = "assign" if isinstance(node.ctx, ast.Store) else "read"
            raw_refs.append((node.lineno, node.col_offset, kind))

    # 去重与精化：按行号归纳，高优先级的类型(call > import > inherit > type > assign > read)覆盖低优先级
    kind_priority = {"call": 6, "import": 5, "inherit": 4, "type": 3, "assign": 2, "read": 1}
    by_line: dict[int, tuple[int, str]] = {}
    for lineno, col, kind in raw_refs:
        if lineno not in by_line or kind_priority.get(kind, 0) > kind_priority.get(by_line[lineno][1], 0):
            by_line[lineno] = (col, kind)

    results: list[dict[str, Any]] = []
    is_test = _is_test_path(rel_path)
    for lineno in sorted(by_line.keys()):
        col, kind = by_line[lineno]
        line_idx = lineno - 1
        snippet = lines[line_idx].strip() if 0 <= line_idx < len(lines) else ""
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        results.append({
            "file": rel_path,
            "line": lineno,
            "col": col,
            "kind": kind,
            "is_test": is_test,
            "snippet": snippet,
        })
    return results


def extract_regex_references(code: str, rel_path: str, symbol: str, ext: str) -> list[dict[str, Any]]:
    """针对通用语言使用全词正则边界提取 symbol 引用。"""
    results: list[dict[str, Any]] = []
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    lines = code.splitlines()
    is_test = _is_test_path(rel_path)

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        # 跳过纯注释行
        if stripped.startswith(("//", "#", "/*", "*", "--")):
            continue
        m = pattern.search(line)
        if not m:
            continue

        col = m.start()
        # 推导类型
        kind = "read"
        after_symbol = line[m.end():].lstrip()
        before_symbol = line[:m.start()].strip()

        if any(w in before_symbol for w in ("import", "from", "require(", "include", "use ")):
            kind = "import"
        elif after_symbol.startswith("("):
            kind = "call"
        elif any(w in before_symbol for w in ("extends", "implements", "inherits")):
            kind = "inherit"
        elif (before_symbol.endswith(":") or "<" in before_symbol) and ext in (".ts", ".tsx"):
            kind = "type"
        elif after_symbol.startswith(("=", "+=", "-=", "*=", ":=")):
            kind = "assign"

        snippet = stripped
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."

        results.append({
            "file": rel_path,
            "line": idx,
            "col": col,
            "kind": kind,
            "is_test": is_test,
            "snippet": snippet,
        })

    return results


def analyze_impact(references: list[dict[str, Any]]) -> dict[str, Any]:
    """计算改动影响面与风险评估指标。"""
    total = len(references)
    if total == 0:
        return {
            "level": "NONE",
            "score": 0,
            "files_count": 0,
            "prod_files_count": 0,
            "test_files_count": 0,
            "summary": "未发现代码引用，修改该符号为完全孤立操作，无外部影响。",
        }

    files = sorted(set(r["file"] for r in references))
    test_files = [f for f in files if r_is_test(references, f)]
    prod_files = [f for f in files if f not in test_files]

    # 分类计数
    kinds_count: dict[str, int] = {}
    for r in references:
        k = r.get("kind", "read")
        kinds_count[k] = kinds_count.get(k, 0) + 1

    # 影响面评级
    if len(prod_files) <= 1 and total <= 3:
        level = "LOW"
        risk_desc = "影响面较小(局部调用/单模块使用)，重构风险低。"
    elif len(prod_files) <= 4 and total <= 15:
        level = "MEDIUM"
        risk_desc = f"影响面中等，涉及 {len(prod_files)} 个业务模块与 {len(test_files)} 个测试文件，建议同步修改测试。"
    else:
        level = "HIGH"
        risk_desc = f"影响面广泛(涉及 {len(prod_files)} 个业务模块与 {len(test_files)} 个测试文件)，修改签名或行为具有扩散风险！"

    return {
        "level": level,
        "total_references": total,
        "files_count": len(files),
        "prod_files_count": len(prod_files),
        "test_files_count": len(test_files),
        "kinds": kinds_count,
        "summary": risk_desc,
    }


def r_is_test(refs: list[dict[str, Any]], filename: str) -> bool:
    for r in refs:
        if r["file"] == filename:
            return bool(r.get("is_test"))
    return False


def find_symbol_references(
    workspace: Path,
    symbol: str,
    path_pattern: str = "",
    kind: str = "",
    include_tests: bool = True,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """在工作区中查找符号引用。"""
    clean_sym = symbol.strip()
    if not clean_sym:
        return {
            "ok": False,
            "msg": "必须指定要查找引用的符号名称(symbol)",
            "references": [],
            "impact": {},
        }

    ws = Path(workspace).resolve()
    limit = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_SEARCH_RESULTS))
    collected: list[dict[str, Any]] = []

    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue

            file_path = Path(root) / fname
            try:
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue

            rel = _safe_rel(ws, file_path)
            if not rel:
                continue

            # 路径通配过滤
            if path_pattern and not fnmatch.fnmatch(rel, path_pattern):
                continue

            # 测试用例过滤
            is_test = _is_test_path(rel)
            if not include_tests and is_test:
                continue

            try:
                code = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if ext == ".py":
                refs = extract_python_references(code, rel, clean_sym)
            else:
                refs = extract_regex_references(code, rel, clean_sym, ext)

            if kind:
                refs = [r for r in refs if r.get("kind") == kind]

            collected.extend(refs)
            if len(collected) >= limit:
                break

        if len(collected) >= limit:
            break

    truncated = len(collected) > limit
    final_refs = collected[:limit]
    impact = analyze_impact(final_refs)

    return {
        "ok": True,
        "symbol": clean_sym,
        "total": len(final_refs),
        "truncated": truncated,
        "references": final_refs,
        "impact": impact,
    }


def format_references_view(data: dict[str, Any]) -> str:
    """将引用查找结果格式化为清晰易读的 Markdown 文本。"""
    if not data.get("ok"):
        return f"❌ 引用查找失败: {data.get('msg', '未知错误')}"

    sym = data.get("symbol", "")
    refs = data.get("references", [])
    impact = data.get("impact", {})

    if not refs:
        return f"🔍 代码库中未找到符号 `{sym}` 的任何调用或引用位置。"

    lines: list[str] = [
        f"### 📍 符号引用检索: `{sym}` (共命中 {len(refs)} 处引用)",
        f"- **影响面评级**: `{impact.get('level', 'INFO')}`",
        f"- **影响分布**: {impact.get('summary', '')}",
        "",
        "| 文件路径 | 行号 | 引用类型 | 代码片段 |",
        "|---|---|---|---|",
    ]

    for r in refs:
        kind_tag = {
            "call": "📞 调用",
            "import": "📦 导入",
            "inherit": "🏛️ 继承",
            "type": "🏷️ 类型",
            "assign": "✏️ 写入",
            "read": "📖 读取",
        }.get(r["kind"], r["kind"])
        snip = r["snippet"].replace("|", "\\|")
        lines.append(f"| `{r['file']}` | {r['line']} | {kind_tag} | `{snip}` |")

    if data.get("truncated"):
        lines.append(f"\n> ⚠️ 引用过多，已截断显示前 {len(refs)} 项。")

    return "\n".join(lines)


def find_references_tool(
    symbol: str,
    path_pattern: str = "",
    kind: str = "",
    include_tests: bool = True,
    max_results: int = DEFAULT_MAX_RESULTS,
    ctx: Any = None,
) -> str:
    """第 66 个内置工具入口:查找符号引用与分析影响面。"""
    ws = getattr(ctx, "workspace", None) if ctx else None
    if not ws:
        ws = Path.cwd()
    data = find_symbol_references(
        workspace=Path(ws),
        symbol=symbol,
        path_pattern=path_pattern,
        kind=kind,
        include_tests=include_tests,
        max_results=max_results,
    )
    return format_references_view(data)
