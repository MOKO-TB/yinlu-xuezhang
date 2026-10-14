"""代码调用层次结构与依赖拓扑分析模块 (v0.9.168)。

用于跨文件深度剖析函数的调用关系(Incoming/Callers 与 Outgoing/Callees),
支持多层递归追溯、循环调用防环、度量统计(Fan-In/Fan-Out)及 Mermaid 流程图输出。
纯 Python 标准库实现(ast + 正则边界分析),零外部依赖,只读安全。
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from minicode.find_references import IGNORED_DIRS, SUPPORTED_EXTS, MAX_FILE_BYTES, _safe_rel


class _CallVisitor(ast.NodeVisitor):
    """AST 访问器: 提取当前文件中所有定义的函数/方法, 以及其内部调用的符号。"""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.current_class: list[str] = []
        self.current_func: list[str] = []
        # 定义点: symbol -> (rel_path, lineno)
        self.definitions: dict[str, tuple[str, int]] = {}
        # Outgoing: caller_symbol -> list of (callee_name, lineno)
        self.outgoing: dict[str, list[tuple[str, int]]] = {}
        # Incoming: callee_name -> list of (caller_symbol, rel_path, lineno)
        self.incoming: dict[str, list[tuple[str, str, int]]] = {}

    def _scope_name(self) -> str:
        if not self.current_func:
            return f"<module:{Path(self.rel_path).name}>"
        if self.current_class:
            return f"{self.current_class[-1]}.{self.current_func[-1]}"
        return self.current_func[-1]

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.definitions[node.name] = (self.rel_path, node.lineno)
        self.current_class.append(node.name)
        self.generic_visit(node)
        self.current_class.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._handle_func(node)

    def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Any:
        self.current_func.append(node.name)
        scope = self._scope_name()
        self.definitions[node.name] = (self.rel_path, node.lineno)
        if self.current_class:
            self.definitions[scope] = (self.rel_path, node.lineno)
        if scope not in self.outgoing:
            self.outgoing[scope] = []
        self.generic_visit(node)
        self.current_func.pop()

    def visit_Call(self, node: ast.Call) -> Any:
        callee: str | None = None
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr

        if callee:
            caller = self._scope_name()
            if caller not in self.outgoing:
                self.outgoing[caller] = []
            self.outgoing[caller].append((callee, node.lineno))

            if callee not in self.incoming:
                self.incoming[callee] = []
            self.incoming[callee].append((caller, self.rel_path, node.lineno))

        self.generic_visit(node)


def extract_python_calls(code: str, rel_path: str) -> _CallVisitor:
    """解析单份 Python 代码, 提取调用与被调关系。"""
    visitor = _CallVisitor(rel_path)
    try:
        tree = ast.parse(code, filename=rel_path)
        visitor.visit(tree)
    except (SyntaxError, ValueError):
        # 语法错误时尝试走正则提取
        _extract_regex_calls_into(code, rel_path, visitor)
    return visitor


# 正则匹配通用函数定义与调用模式 (多语言回退)
_FUNC_DEF_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*(?:func|fn)\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[A-Za-z0-9_<>[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
]
_CALL_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _extract_regex_calls_into(code: str, rel_path: str, visitor: _CallVisitor) -> None:
    lines = code.splitlines()
    current_fn: str = f"<module:{Path(rel_path).name}>"
    visitor.outgoing[current_fn] = []

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "/*", "*")):
            continue

        # 检查是否进入新函数定义
        matched_def = False
        for pat in _FUNC_DEF_PATTERNS:
            m = pat.match(line)
            if m:
                fn_name = m.group(1)
                current_fn = fn_name
                visitor.definitions[fn_name] = (rel_path, lineno)
                if current_fn not in visitor.outgoing:
                    visitor.outgoing[current_fn] = []
                matched_def = True
                break

        if matched_def:
            continue

        # 提取当前行中的调用
        for m in _CALL_PATTERN.finditer(line):
            callee = m.group(1)
            # 过滤常见控制流关键字
            if callee in ("if", "while", "for", "switch", "catch", "return", "print", "range", "len"):
                continue
            visitor.outgoing[current_fn].append((callee, lineno))
            if callee not in visitor.incoming:
                visitor.incoming[callee] = []
            visitor.incoming[callee].append((current_fn, rel_path, lineno))


class WorkspaceCallGraph:
    """全工作区调用图谱。"""

    def __init__(self, workspace: Path, path_pattern: str = ""):
        self.workspace = workspace
        self.path_pattern = path_pattern
        self.definitions: dict[str, tuple[str, int]] = {}
        # Outgoing: caller -> list of (callee, rel_path, lineno)
        self.outgoing: dict[str, list[dict[str, Any]]] = {}
        # Incoming: callee -> list of (caller, rel_path, lineno)
        self.incoming: dict[str, list[dict[str, Any]]] = {}
        self._scanned = False

    def scan(self) -> None:
        if self._scanned:
            return
        self._scanned = True

        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext not in SUPPORTED_EXTS:
                    continue

                full_p = Path(root) / f
                try:
                    if full_p.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue

                rel = _safe_rel(self.workspace, full_p)
                if not rel:
                    continue

                if self.path_pattern:
                    match_posix = fnmatch.fnmatch(rel, self.path_pattern)
                    match_name = fnmatch.fnmatch(f, self.path_pattern)
                    if not (match_posix or match_name):
                        continue

                try:
                    code = full_p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                if ext == ".py":
                    visitor = extract_python_calls(code, rel)
                else:
                    visitor = _CallVisitor(rel)
                    _extract_regex_calls_into(code, rel, visitor)

                # 合并定义
                for sym, loc in visitor.definitions.items():
                    if sym not in self.definitions:
                        self.definitions[sym] = loc

                # 合并 outgoing
                for caller, callees in visitor.outgoing.items():
                    if caller not in self.outgoing:
                        self.outgoing[caller] = []
                    for callee, line in callees:
                        self.outgoing[caller].append({
                            "callee": callee,
                            "path": rel,
                            "line": line,
                        })

                # 合并 incoming
                for callee, callers in visitor.incoming.items():
                    if callee not in self.incoming:
                        self.incoming[callee] = []
                    for caller, p, line in callers:
                        self.incoming[callee].append({
                            "caller": caller,
                            "path": p,
                            "line": line,
                        })


def _build_tree(
    graph: WorkspaceCallGraph,
    symbol: str,
    direction: str,
    max_depth: int,
    current_depth: int,
    visited_chain: set[str],
) -> dict[str, Any]:
    """递归构建调用树节点。"""
    loc = graph.definitions.get(symbol)
    node: dict[str, Any] = {
        "symbol": symbol,
        "path": loc[0] if loc else "",
        "line": loc[1] if loc else 0,
        "depth": current_depth,
        "cycle": False,
        "calls": [],
    }

    if symbol in visited_chain:
        node["cycle"] = True
        return node

    if current_depth >= max_depth:
        return node

    new_chain = visited_chain | {symbol}

    if direction == "incoming":
        # 谁调用了 symbol (Callers)
        incoming_items = graph.incoming.get(symbol, [])
        # 按 caller 去重, 保留首次出现的行号位置
        seen_callers: set[str] = set()
        for item in incoming_items:
            caller_name = item["caller"]
            if caller_name in seen_callers or caller_name == symbol:
                continue
            seen_callers.add(caller_name)
            child = _build_tree(
                graph,
                caller_name,
                direction,
                max_depth,
                current_depth + 1,
                new_chain,
            )
            child["call_path"] = item["path"]
            child["call_line"] = item["line"]
            node["calls"].append(child)

    elif direction == "outgoing":
        # symbol 调用了谁 (Callees)
        outgoing_items = graph.outgoing.get(symbol, [])
        seen_callees: set[str] = set()
        for item in outgoing_items:
            callee_name = item["callee"]
            if callee_name in seen_callees or callee_name == symbol:
                continue
            seen_callees.add(callee_name)
            child = _build_tree(
                graph,
                callee_name,
                direction,
                max_depth,
                current_depth + 1,
                new_chain,
            )
            child["call_path"] = item["path"]
            child["call_line"] = item["line"]
            node["calls"].append(child)

    return node


def format_hierarchy_tree(node: dict[str, Any], direction: str = "incoming") -> str:
    """将调用树格式化为 ASCII 缩进文本树。"""
    lines: list[str] = []
    dir_label = "Incoming (Callers 向上追溯)" if direction == "incoming" else "Outgoing (Callees 向下依赖)"
    lines.append(f"Call Hierarchy [{dir_label}]: `{node['symbol']}`")
    if node.get("path"):
        lines.append(f"  定义位置: {node['path']}:{node['line']}")

    def _render(curr: dict[str, Any], prefix: str = "", is_last: bool = True):
        children = curr.get("calls", [])
        for idx, child in enumerate(children):
            last_child = (idx == len(children) - 1)
            branch = "└─ " if last_child else "├─ "
            info = f"{child['symbol']}"
            if child.get("call_path"):
                info += f" ({child['call_path']}:{child['call_line']})"
            elif child.get("path"):
                info += f" ({child['path']}:{child['line']})"

            if child.get("cycle"):
                info += " [循环调用/Cycle]"

            lines.append(f"{prefix}{branch}{info}")
            new_prefix = prefix + ("   " if last_child else "│  ")
            if not child.get("cycle"):
                _render(child, new_prefix, last_child)

    _render(node)
    if len(lines) <= 2:
        lines.append("  (无检测到的层级调用)")
    return "\n".join(lines)


def format_hierarchy_mermaid(node: dict[str, Any], direction: str = "incoming") -> str:
    """生成 Mermaid 流程图代码。"""
    lines: list[str] = ["```mermaid", "graph TD"]
    edges: set[tuple[str, str]] = set()

    def _clean_id(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", s)

    def _collect_edges(curr: dict[str, Any]):
        curr_id = _clean_id(curr["symbol"])
        for child in curr.get("calls", []):
            child_id = _clean_id(child["symbol"])
            if direction == "incoming":
                # child (调用者) -> curr (被调用者)
                edges.add((child_id, curr_id, child["symbol"], curr["symbol"]))
            else:
                # curr (调用者) -> child (被调用者)
                edges.add((curr_id, child_id, curr["symbol"], child["symbol"]))
            if not child.get("cycle"):
                _collect_edges(child)

    _collect_edges(node)

    for src_id, dst_id, src_label, dst_label in edges:
        lines.append(f'  {src_id}["{src_label}"] --> {dst_id}["{dst_label}"]')

    if not edges:
        root_id = _clean_id(node["symbol"])
        lines.append(f'  {root_id}["{node["symbol"]} (无调用边)"]')

    lines.append("```")
    return "\n".join(lines)


def build_call_hierarchy(
    workspace: Path,
    symbol: str,
    direction: str = "incoming",
    depth: int = 2,
    path_pattern: str = "",
) -> dict[str, Any]:
    """主执行函数: 分析当前工作区指定符号的调用拓扑。"""
    graph = WorkspaceCallGraph(workspace, path_pattern=path_pattern)
    graph.scan()

    direction = direction.lower().strip()
    if direction not in ("incoming", "outgoing", "both"):
        direction = "incoming"

    depth = max(1, min(depth, 4))  # 深度约束在 1~4 层

    result: dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
        "depth": depth,
        "definition": graph.definitions.get(symbol),
        "fan_in": len(graph.incoming.get(symbol, [])),
        "fan_out": len(graph.outgoing.get(symbol, [])),
    }

    if direction in ("incoming", "both"):
        result["incoming_tree"] = _build_tree(graph, symbol, "incoming", depth, 0, set())
    if direction in ("outgoing", "both"):
        result["outgoing_tree"] = _build_tree(graph, symbol, "outgoing", depth, 0, set())

    return result


def call_hierarchy_tool(
    symbol: str,
    direction: str = "incoming",
    depth: int = 2,
    path_pattern: str = "",
    format: str = "tree",
    **kwargs: Any,
) -> str:
    """内置工具入口函数: call_hierarchy。"""
    if not symbol or not symbol.strip():
        return "错误: 必须指定要分析的符号名称 symbol。"

    symbol = symbol.strip()
    ctx = kwargs.get("ctx")
    workspace = getattr(ctx, "workspace", None) if ctx else None
    if not workspace:
        workspace = Path.cwd()
    else:
        workspace = Path(workspace)

    data = build_call_hierarchy(
        workspace=workspace,
        symbol=symbol,
        direction=direction,
        depth=depth,
        path_pattern=path_pattern,
    )

    fmt = format.lower().strip()
    sections: list[str] = []

    # 概要统计行
    def_info = f"{data['definition'][0]}:{data['definition'][1]}" if data["definition"] else "未在工作区内找到直接定义"
    sections.append(
        f"**调用层次结构拓扑分析**: `{symbol}`\n"
        f"- 符号定义: {def_info}\n"
        f"- 分析方向: {data['direction']} | 最大深度: {data['depth']}\n"
        f"- 入度 (Fan-In 被调用数): {data['fan_in']} 处\n"
        f"- 出度 (Fan-Out 外部调用数): {data['fan_out']} 处\n"
    )

    if fmt == "mermaid":
        if "incoming_tree" in data:
            sections.append("### Incoming 调用方流程图\n" + format_hierarchy_mermaid(data["incoming_tree"], "incoming"))
        if "outgoing_tree" in data:
            sections.append("### Outgoing 被调用依赖流程图\n" + format_hierarchy_mermaid(data["outgoing_tree"], "outgoing"))
    else:
        if "incoming_tree" in data:
            sections.append("```text\n" + format_hierarchy_tree(data["incoming_tree"], "incoming") + "\n```")
        if "outgoing_tree" in data:
            sections.append("```text\n" + format_hierarchy_tree(data["outgoing_tree"], "outgoing") + "\n```")

    return "\n\n".join(sections)
