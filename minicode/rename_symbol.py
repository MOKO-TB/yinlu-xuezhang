"""跨文件代码符号重命名与重构预演工具(v0.9.167)。

对标 Cursor/Codex/VS Code 的 Symbol Rename 与重构能力:
1. 基于 `minicode.find_references` 获得全代码库中精确的引用位置与类型(call, import, inherit, type, assign, read);
2. 校验新符号合法性(必须为合法标识符, 禁止非法字符注入);
3. 纯 Python 标准库实现, 零外部第三方依赖;
4. 默认 `dry_run=True` 安全只读模式, 仅生成改动统计、影响面风险评估(LOW/MEDIUM/HIGH)与 Unified Diff 预览, 绝不触碰磁盘;
5. 执行 `dry_run=False` 模式前, 在内存中对 Python 代码调用 `ast.parse` 进行语法完备性检验, 若存在语法冲突立即中断并安全回滚;
6. 严格受沙箱和确认门管控(read_only=False), MCP 及网页访客端登记隔离。
"""
from __future__ import annotations

import ast
import difflib
import keyword
from pathlib import Path
import re
from typing import Any

from minicode.find_references import (
    analyze_impact,
    find_symbol_references,
    IGNORED_DIRS,
)

IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def is_valid_identifier(name: str) -> bool:
    """验证符号名称是否为合法的编程语言标识符(排除保留关键字)。"""
    if not name or not isinstance(name, str):
        return False
    clean = name.strip()
    if not IDENTIFIER_RE.match(clean):
        return False
    if keyword.iskeyword(clean):
        return False
    return True


def _is_ignored_path(rel_path: str) -> bool:
    """检查相对路径是否在忽略目录中。"""
    parts = Path(rel_path).parts
    return any(p in IGNORED_DIRS for p in parts)


def rename_symbol_in_code(
    code: str,
    old_symbol: str,
    new_symbol: str,
    ext: str = ".py",
) -> tuple[str, int, bool, str]:
    """在单个文件的代码字符串中安全替换符号。

    返回: (新代码, 替换次数, 是否成功, 错误信息)
    """
    if not old_symbol or not new_symbol or old_symbol == new_symbol:
        return code, 0, True, ""

    # 使用全词边界匹配避免部分子串被误替换(例如 get_user 不能误替换 user)
    pattern = re.compile(r"\b" + re.escape(old_symbol) + r"\b")
    matches = list(pattern.finditer(code))
    if not matches:
        return code, 0, True, ""

    new_code = pattern.sub(new_symbol, code)
    replacement_count = len(matches)

    # 针对 Python 源码, 进行 ast.parse 语法自检
    if ext.lower() == ".py":
        try:
            ast.parse(new_code)
        except SyntaxError as e:
            return (
                code,
                0,
                False,
                f"语法校验失败 (SyntaxError at line {e.lineno}): 重命名导致代码语法不合法, 操作已拦截",
            )
        except Exception as e:
            return code, 0, False, f"AST 解析异常: {e}"

    return new_code, replacement_count, True, ""


def rename_symbol(
    workspace: str,
    symbol: str,
    new_name: str,
    dry_run: bool = True,
    path_pattern: str | None = None,
    kind: str | None = None,
    max_files: int = 50,
) -> dict[str, Any]:
    """在工作区内跨文件重命名符号。

    Args:
        workspace: 工作区绝对路径
        symbol: 原符号名称
        new_name: 目标新名称
        dry_run: True 表示仅预演输出 diff, False 表示真实写入磁盘
        path_pattern: 可选路径通配模式过滤
        kind: 可选限定引用类型 (call/import/inherit/type/assign/read)
        max_files: 最大处理文件数上限
    """
    ws = Path(workspace).resolve()
    if not ws.exists() or not ws.is_dir():
        return {
            "ok": False,
            "error": f"工作区不存在: {workspace}",
            "symbol": symbol,
            "new_name": new_name,
        }

    symbol = (symbol or "").strip()
    new_name = (new_name or "").strip()

    if not symbol:
        return {"ok": False, "error": "原符号名称不能为空", "symbol": symbol}
    if not is_valid_identifier(symbol):
        return {
            "ok": False,
            "error": f"原符号名称 '{symbol}' 不是合法的标识符",
            "symbol": symbol,
            "new_name": new_name,
        }
    if not new_name:
        return {"ok": False, "error": "新符号名称不能为空", "new_name": new_name}
    if symbol == new_name:
        return {
            "ok": False,
            "error": "新旧符号名称相同, 无需重命名",
            "symbol": symbol,
            "new_name": new_name,
        }
    if not is_valid_identifier(new_name):
        return {
            "ok": False,
            "error": f"新符号名称 '{new_name}' 不是合法的标识符(只能包含字母、数字和下划线, 且不能为保留关键字)",
            "symbol": symbol,
            "new_name": new_name,
        }

    # 1. 查找全部引用 (借助 find_symbol_references 获取精确调用与影响分析)
    ref_report = find_symbol_references(
        workspace=ws,
        symbol=symbol,
        path_pattern=path_pattern or "",
        kind=kind or "",
        include_tests=True,
        max_results=500,
    )
    references = ref_report.get("references", [])

    # 2. 收集所有包含该符号的文件候选 (包括定义处与调用处)
    candidate_files: set[str] = set()
    for ref in references:
        rel = ref.get("file", "")
        if rel and not _is_ignored_path(rel):
            candidate_files.add(Path(rel).as_posix())

    # 补充扫描工作区内包含该单词的文本文件(包含声明/定义处, 避免仅有定义无调用时漏改)
    sym_pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
    TEXT_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt", ".yaml", ".yml"}

    # 路径匹配过滤辅助
    def _matches_path_pattern(rel: str) -> bool:
        if not path_pattern:
            return True
        import fnmatch
        return fnmatch.fnmatch(rel.replace("\\", "/"), path_pattern.replace("\\", "/"))

    for p in ws.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            rel = p.relative_to(ws).as_posix()
        except ValueError:
            continue
        if _is_ignored_path(rel) or not _matches_path_pattern(rel):
            continue
        if rel in candidate_files:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            if sym_pattern.search(content):
                candidate_files.add(rel)
        except Exception:
            pass

    if not candidate_files:
        return {
            "ok": True,
            "dry_run": dry_run,
            "symbol": symbol,
            "new_name": new_name,
            "files_count": 0,
            "references_count": 0,
            "modified_files": [],
            "total_matches": 0,
            "diff": "",
            "message": f"未在工作区内找到符号 '{symbol}' 的有效引用或定义, 无需重命名。",
            "changes": [],
            "risk": {"level": "NONE", "score": 0},
            "written_files": 0,
        }

    # 3. 在内存中预处理所有受影响的文件
    planned_writes: list[tuple[Path, str, str, str, int]] = []
    changes: list[dict[str, Any]] = []
    total_replacements = 0

    for rel_path in sorted(candidate_files):
        if len(planned_writes) >= max_files:
            break

        fpath = ws / rel_path
        if not fpath.is_file():
            continue

        try:
            old_code = fpath.read_text(encoding="utf-8")
        except Exception:
            try:
                old_code = fpath.read_text(encoding="utf-8-sig")
            except Exception:
                continue

        ext = fpath.suffix
        new_code, count, success, err = rename_symbol_in_code(
            old_code, symbol, new_name, ext=ext
        )
        if not success:
            return {
                "ok": False,
                "error": f"文件 {rel_path} 替换失败: {err}",
                "symbol": symbol,
                "new_name": new_name,
                "dry_run": dry_run,
            }

        if count > 0 and new_code != old_code:
            diff_lines = list(
                difflib.unified_diff(
                    old_code.splitlines(keepends=True),
                    new_code.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                    n=3,
                )
            )
            diff_text = "".join(diff_lines)
            planned_writes.append((fpath, rel_path, old_code, new_code, count))
            changes.append({
                "file": rel_path,
                "replacements": count,
                "diff": diff_text,
            })
            total_replacements += count

    # 4. 评估影响面风险
    risk = analyze_impact(references)

    # 5. 若非 dry_run, 执行真实写入
    written_files = 0
    if not dry_run:
        for fpath, rel_path, _, new_code, _ in planned_writes:
            try:
                fpath.write_text(new_code, encoding="utf-8")
                written_files += 1
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"写入文件 {rel_path} 失败: {e}",
                    "symbol": symbol,
                    "new_name": new_name,
                    "written_files": written_files,
                }

    return {
        "ok": True,
        "dry_run": dry_run,
        "symbol": symbol,
        "new_name": new_name,
        "files_count": len(planned_writes),
        "references_count": total_replacements,
        "modified_files": [ch["file"] for ch in changes],
        "total_matches": total_replacements,
        "diff": "\n".join(ch["diff"] for ch in changes if ch.get("diff")),
        "changes": changes,
        "risk": risk,
        "written_files": written_files if not dry_run else 0,
    }


def rename_symbol_tool(
    symbol: str,
    new_name: str,
    dry_run: bool = True,
    path_pattern: str | None = None,
    workspace: str | None = None,
) -> str:
    """内置工具入口函数: 跨文件代码符号重命名与重构预演。"""
    ws = workspace or str(Path.cwd().resolve())
    res = rename_symbol(
        workspace=ws,
        symbol=symbol,
        new_name=new_name,
        dry_run=dry_run,
        path_pattern=path_pattern,
    )

    if not res.get("ok"):
        return f"❌ 符号重命名失败: {res.get('error', '未知错误')}"

    files_count = res.get("files_count", 0)
    refs_count = res.get("references_count", 0)
    is_dry = res.get("dry_run", True)
    risk = res.get("risk", {})
    risk_level = risk.get("level", "LOW")
    changes = res.get("changes", [])

    mode_str = "🔍 预演模式 (未修改磁盘文件)" if is_dry else "✍️ 执行模式 (已写入修改)"

    lines = [
        f"### 符号重构报告: `{symbol}` ➜ `{new_name}`",
        f"- **运行模式**: {mode_str}",
        f"- **涉及文件**: {files_count} 个",
        f"- **替换引用**: {refs_count} 处",
        f"- **风险等级**: **{risk_level}** (影响说明: {risk.get('summary', '正常')})",
    ]

    if files_count == 0:
        lines.append(f"\n{res.get('message', '未发现需要重命名的文件引用。')}")
        return "\n".join(lines)

    lines.append("\n#### 变更文件与 Diff 摘要:")
    for ch in changes:
        lines.append(f"\n- **`{ch['file']}`** (替换 {ch['replacements']} 处):")
        diff = ch.get("diff", "")
        if diff:
            diff_display = diff[:2000] + ("\n... [diff 截断]" if len(diff) > 2000 else "")
            lines.append("```diff\n" + diff_display + "\n```")

    if is_dry:
        lines.append(
            "\n> 💡 **提示**: 以上为预演变更。如需真正写入磁盘，请传入参数 `dry_run=False` 重新执行。"
        )
    else:
        lines.append(
            f"\n✅ 成功将 {res.get('written_files', 0)} 个文件写入磁盘。请运行测试验证重构正确性。"
        )

    return "\n".join(lines)
