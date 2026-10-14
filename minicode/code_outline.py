"""代码结构大纲与类/成员树提取器 (v0.9.170)。

对标 VS Code documentSymbol LSP、Cursor Outline 与 Windsurf Code Structure:
针对单个源文件高精度提取结构大纲、类继承与成员方法、函数签名、顶级变量与文档摘要，
帮助智能体和开发者免读全量源码文件即可纵览代码架构与精确定位行号。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minicode.tools.base import ToolContext


@dataclass
class OutlineItem:
    """大纲条目。"""
    kind: str  # class, function, method, interface, struct, enum, trait, const, var, import
    name: str
    line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    children: list[OutlineItem] = field(default_factory=list)


class CodeOutlineExtractor:
    """跨语言代码结构大纲提取器。"""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def extract_outline(
        self,
        rel_path: str,
        depth: int = 2,
        show_signatures: bool = True,
        show_docstrings: bool = True,
    ) -> dict[str, Any]:
        """提取指定文件的大纲结构。"""
        p = Path(rel_path)
        if p.is_absolute():
            full_path = p.resolve()
        else:
            full_path = (self.workspace / rel_path).resolve()
        if not full_path.is_file():
            return {
                "ok": False,
                "error": f"文件不存在: {rel_path}",
                "path": rel_path,
                "items": [],
            }

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {
                "ok": False,
                "error": f"无法读取文件 {rel_path}: {e}",
                "path": rel_path,
                "items": [],
            }

        suffix = full_path.suffix.lower()
        total_lines = len(content.splitlines())

        if suffix == ".py":
            items, language, syntax_err = self._extract_python_outline(content)
        else:
            items = self._extract_multilang_outline(content, suffix)
            language = self._detect_language(suffix)
            syntax_err = None

        return {
            "ok": True,
            "path": rel_path.replace("\\", "/"),
            "language": language,
            "total_lines": total_lines,
            "depth": depth,
            "show_signatures": show_signatures,
            "show_docstrings": show_docstrings,
            "syntax_error": syntax_err,
            "items": items,
        }

    def _detect_language(self, suffix: str) -> str:
        mapping = {
            ".py": "Python",
            ".ts": "TypeScript",
            ".tsx": "TypeScript React",
            ".js": "JavaScript",
            ".jsx": "JavaScript React",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".c": "C",
            ".cpp": "C++",
            ".h": "C/C++ Header",
            ".hpp": "C++ Header",
        }
        return mapping.get(suffix, suffix.lstrip(".").upper() or "Text")

    # ------------------ Python AST 解析 ------------------

    def _extract_python_outline(self, content: str) -> tuple[list[OutlineItem], str, str | None]:
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            fallback_items = self._extract_multilang_outline(content, ".py")
            return fallback_items, "Python", f"语法解析提示 (Line {e.lineno}, Col {e.offset}): {e.msg}"
        except Exception as e:
            fallback_items = self._extract_multilang_outline(content, ".py")
            return fallback_items, "Python", f"解析失败: {e}"

        items: list[OutlineItem] = []
        import_count = 0
        first_import_line = 0
        last_import_line = 0

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_count += 1
                if first_import_line == 0:
                    first_import_line = node.lineno
                last_import_line = getattr(node, "end_lineno", node.lineno)
            elif isinstance(node, ast.ClassDef):
                items.append(self._parse_py_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                items.append(self._parse_py_func(node, is_method=False))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                assign_item = self._parse_py_assign(node)
                if assign_item:
                    items.append(assign_item)

        if import_count > 0:
            items.insert(
                0,
                OutlineItem(
                    kind="imports",
                    name=f"imports ({import_count} 项)",
                    line=first_import_line,
                    end_line=last_import_line,
                ),
            )

        return items, "Python", None

    def _parse_py_class(self, node: ast.ClassDef) -> OutlineItem:
        bases = []
        for b in node.bases:
            bases.append(self._format_ast_expr(b))

        decorators = []
        for d in node.decorator_list:
            decorators.append(self._format_ast_expr(d))

        doc = ast.get_docstring(node) or ""
        doc_summary = doc.strip().splitlines()[0] if doc.strip() else ""

        methods: list[OutlineItem] = []
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._parse_py_func(sub, is_method=True))
            elif isinstance(sub, ast.ClassDef):
                methods.append(self._parse_py_class(sub))
            elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                attr_item = self._parse_py_assign(sub)
                if attr_item:
                    attr_item.kind = "field"
                    methods.append(attr_item)

        return OutlineItem(
            kind="class",
            name=node.name,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            bases=bases,
            decorators=decorators,
            docstring=doc_summary,
            children=methods,
        )

    def _parse_py_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool = False) -> OutlineItem:
        sig = self._format_func_signature(node)
        decorators = [self._format_ast_expr(d) for d in node.decorator_list]
        doc = ast.get_docstring(node) or ""
        doc_summary = doc.strip().splitlines()[0] if doc.strip() else ""

        nested: list[OutlineItem] = []
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested.append(self._parse_py_func(sub, is_method=False))

        kind = "method" if is_method else ("async_func" if isinstance(node, ast.AsyncFunctionDef) else "function")

        return OutlineItem(
            kind=kind,
            name=node.name,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            signature=sig,
            decorators=decorators,
            docstring=doc_summary,
            children=nested,
        )

    def _format_func_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args_parts: list[str] = []
        args = node.args

        # Positional args & defaults
        defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
        for arg, default in zip(args.args, defaults):
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {self._format_ast_expr(arg.annotation)}"
            if default is not None:
                arg_str += f" = {self._format_ast_expr(default)}"
            args_parts.append(arg_str)

        # *args
        if args.vararg:
            v_str = f"*{args.vararg.arg}"
            if args.vararg.annotation:
                v_str += f": {self._format_ast_expr(args.vararg.annotation)}"
            args_parts.append(v_str)

        # Keyword-only args
        for kw, default in zip(args.kwonlyargs, args.kw_defaults):
            kw_str = kw.arg
            if kw.annotation:
                kw_str += f": {self._format_ast_expr(kw.annotation)}"
            if default is not None:
                kw_str += f" = {self._format_ast_expr(default)}"
            args_parts.append(kw_str)

        # **kwargs
        if args.kwarg:
            kwarg_str = f"**{args.kwarg.arg}"
            if args.kwarg.annotation:
                kwarg_str += f": {self._format_ast_expr(args.kwarg.annotation)}"
            args_parts.append(kwarg_str)

        ret_str = ""
        if node.returns:
            ret_str = f" -> {self._format_ast_expr(node.returns)}"

        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        return f"{prefix}{node.name}({', '.join(args_parts)}){ret_str}"

    def _parse_py_assign(self, node: ast.Assign | ast.AnnAssign) -> OutlineItem | None:
        target_name = ""
        type_str = ""

        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                target_name = node.target.id
            elif isinstance(node.target, ast.Attribute):
                target_name = node.target.attr
            type_str = self._format_ast_expr(node.annotation)
        elif isinstance(node, ast.Assign) and node.targets:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
            elif isinstance(target, ast.Attribute):
                target_name = target.attr

        if not target_name or target_name.startswith("_"):
            return None

        # 仅收录大写常量或有类型注解的变量
        if not (target_name.isupper() or type_str):
            return None

        sig = f"{target_name}: {type_str}" if type_str else target_name
        return OutlineItem(
            kind="const" if target_name.isupper() else "var",
            name=target_name,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            signature=sig,
        )

    def _format_ast_expr(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Attribute):
            val = self._format_ast_expr(node.value)
            return f"{val}.{node.attr}" if val else node.attr
        if isinstance(node, ast.Subscript):
            val = self._format_ast_expr(node.value)
            slice_val = self._format_ast_expr(node.slice)
            return f"{val}[{slice_val}]"
        if isinstance(node, ast.Tuple):
            return f"({', '.join(self._format_ast_expr(e) for e in node.elts)})"
        if isinstance(node, ast.List):
            return f"[{', '.join(self._format_ast_expr(e) for e in node.elts)}]"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return f"{self._format_ast_expr(node.left)} | {self._format_ast_expr(node.right)}"
        if isinstance(node, ast.Call):
            func = self._format_ast_expr(node.func)
            return f"{func}(...)"
        return getattr(node, "id", "") or ""

    # ------------------ 多语言通用解析 ------------------

    def _extract_multilang_outline(self, content: str, suffix: str) -> list[OutlineItem]:
        lines = content.splitlines()
        items: list[OutlineItem] = []

        patterns: list[tuple[str, str]] = []
        if suffix in (".ts", ".tsx", ".js", ".jsx"):
            patterns = [
                ("class", r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)"),
                ("interface", r"^\s*(?:export\s+)?interface\s+([A-Za-z0-9_$]+)"),
                ("type", r"^\s*(?:export\s+)?type\s+([A-Za-z0-9_$]+)\s*="),
                ("function", r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\("),
                ("const", r"^\s*(?:export\s+)?const\s+([A-Z0-9_$]+)\s*="),
            ]
        elif suffix == ".go":
            patterns = [
                ("struct", r"^\s*type\s+([A-Za-z0-9_]+)\s+struct\b"),
                ("interface", r"^\s*type\s+([A-Za-z0-9_]+)\s+interface\b"),
                ("method", r"^\s*func\s*\([^)]+\)\s*([A-Za-z0-9_]+)\s*\("),
                ("function", r"^\s*func\s+([A-Za-z0-9_]+)\s*\("),
                ("const", r"^\s*const\s+([A-Za-z0-9_]+)\b"),
            ]
        elif suffix == ".rs":
            patterns = [
                ("struct", r"^\s*(?:pub(?:\([^)]+\))?\s+)?struct\s+([A-Za-z0-9_]+)"),
                ("enum", r"^\s*(?:pub(?:\([^)]+\))?\s+)?enum\s+([A-Za-z0-9_]+)"),
                ("trait", r"^\s*(?:pub(?:\([^)]+\))?\s+)?trait\s+([A-Za-z0-9_]+)"),
                ("impl", r"^\s*impl(?:<[^>]+>)?\s+(?:[A-Za-z0-9_]+\s+for\s+)?([A-Za-z0-9_]+)"),
                ("function", r"^\s*(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)"),
            ]
        elif suffix in (".java", ".c", ".cpp", ".h", ".hpp"):
            patterns = [
                ("class", r"^\s*(?:public|private|protected|static|final|\s)*class\s+([A-Za-z0-9_]+)"),
                ("interface", r"^\s*(?:public|private|protected|\s)*interface\s+([A-Za-z0-9_]+)"),
                ("enum", r"^\s*(?:public|private|protected|\s)*enum\s+([A-Za-z0-9_]+)"),
                ("function", r"^\s*(?:[A-Za-z0-9_<>,\[\]]+\s+)+([A-Za-z0-9_]+)\s*\([^;{]*\)\s*(?:\{|throws)"),
            ]
        else:
            patterns = [
                ("class", r"^\s*class\s+([A-Za-z0-9_]+)"),
                ("function", r"^\s*(?:def|func|fn|function)\s+([A-Za-z0-9_]+)"),
            ]

        for idx, line in enumerate(lines, 1):
            trimmed = line.strip()
            if not trimmed or trimmed.startswith(("//", "#", "/*", "*")):
                continue
            for kind, pat in patterns:
                m = re.search(pat, line)
                if m:
                    name = m.group(1)
                    sig = trimmed.rstrip("{").strip()
                    items.append(
                        OutlineItem(
                            kind=kind,
                            name=name,
                            line=idx,
                            end_line=idx,
                            signature=sig,
                        )
                    )
                    break

        return items

    # ------------------ 大纲格式化 ------------------

    def format_outline(self, data: dict[str, Any]) -> str:
        """将大纲数据渲染为优雅可读的 ASCII 树。"""
        if not data.get("ok"):
            return f"❌ 无法提取大纲: {data.get('error', '未知错误')}"

        rel_path = data.get("path", "")
        language = data.get("language", "")
        total_lines = data.get("total_lines", 0)
        max_depth = data.get("depth", 2)
        show_sigs = data.get("show_signatures", True)
        show_docs = data.get("show_docstrings", True)
        items: list[OutlineItem] = data.get("items", [])
        syntax_err = data.get("syntax_error")

        out: list[str] = [
            f"📄 文件大纲: {rel_path} ({language}, 共 {total_lines} 行)",
            "━" * 60,
        ]

        if syntax_err:
            out.append(f"⚠️ {syntax_err}")

        if not items:
            out.append("  (未检测到类、函数、接口或顶级符号)")
            return "\n".join(out)

        out.append(self._render_tree(items, current_depth=1, max_depth=max_depth, show_sigs=show_sigs, show_docs=show_docs))
        out.append("━" * 60)
        total_syms = self._count_items(items)
        out.append(f"📊 统计: 共提取 {total_syms} 个符号节点 (展开深度: {max_depth})")
        return "\n".join(out)

    def _render_tree(
        self,
        items: list[OutlineItem],
        prefix: str = "",
        current_depth: int = 1,
        max_depth: int = 2,
        show_sigs: bool = True,
        show_docs: bool = True,
    ) -> str:
        lines: list[str] = []
        count = len(items)

        for i, item in enumerate(items):
            is_last = (i == count - 1)
            branch = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            # 图标与类型标识
            icon = self._get_icon(item.kind)
            line_range = f"#L{item.line}" if item.line == item.end_line else f"#L{item.line}-L{item.end_line}"

            title_parts = [f"{prefix}{branch}{icon} {item.kind} {item.name}"]
            if item.bases:
                title_parts.append(f"({', '.join(item.bases)})")
            title_parts.append(f"[{line_range}]")

            lines.append(" ".join(title_parts))

            # 装饰器
            if item.decorators and current_depth <= max_depth:
                for dec in item.decorators:
                    lines.append(f"{child_prefix}🔖 @{dec}")

            # 签名
            if show_sigs and item.signature and item.kind in ("function", "method", "async_func"):
                # 如果签名不是简单的 def name，显示参数摘要
                sig_short = item.signature
                if len(sig_short) > 90:
                    sig_short = sig_short[:87] + "..."
                lines.append(f"{child_prefix}📝 {sig_short}")

            # 文档摘要
            if show_docs and item.docstring:
                doc_short = item.docstring
                if len(doc_short) > 80:
                    doc_short = doc_short[:77] + "..."
                lines.append(f"{child_prefix}💬 \"{doc_short}\"")

            # 子条目递归 (当 current_depth < max_depth)
            if item.children:
                if current_depth < max_depth:
                    rendered_children = self._render_tree(
                        item.children,
                        prefix=child_prefix,
                        current_depth=current_depth + 1,
                        max_depth=max_depth,
                        show_sigs=show_sigs,
                        show_docs=show_docs,
                    )
                    lines.append(rendered_children)
                else:
                    lines.append(f"{child_prefix}└── ... 包含 {len(item.children)} 个子成员 (深度折叠)")

        return "\n".join(lines)

    def _get_icon(self, kind: str) -> str:
        mapping = {
            "class": "🏛️",
            "interface": "📐",
            "struct": "🧱",
            "enum": "📑",
            "trait": "🧬",
            "impl": "🔨",
            "function": "⚙️",
            "async_func": "⚡",
            "method": "🔹",
            "field": "🔸",
            "const": "📌",
            "var": "📍",
            "imports": "📦",
        }
        return mapping.get(kind, "•")

    def _count_items(self, items: list[OutlineItem]) -> int:
        total = len(items)
        for item in items:
            total += self._count_items(item.children)
        return total


def code_outline_tool(*args, path: str = "", depth: int = 2, show_signatures: bool = True, show_docstrings: bool = True, **kwargs) -> str:
    """code_outline 工具处理入口。"""
    ctx: ToolContext | None = None
    target_path = path

    for a in args:
        if hasattr(a, "workspace"):
            ctx = a
        elif isinstance(a, str) and not target_path:
            target_path = a

    if not ctx and "ctx" in kwargs and hasattr(kwargs["ctx"], "workspace"):
        ctx = kwargs["ctx"]

    if not target_path:
        return "❌ 请指定要提取大纲的文件路径 (参数: path='...')"

    ws = ctx.workspace if ctx and ctx.workspace else Path.cwd()
    extractor = CodeOutlineExtractor(ws)
    data = extractor.extract_outline(
        rel_path=target_path,
        depth=depth,
        show_signatures=show_signatures,
        show_docstrings=show_docstrings,
    )
    return extractor.format_outline(data)
