"""代码库全景百科引擎 (Repo-Wiki): 提取并维护工作区架构概览、模块分工与工程约定。

对标 ZCode repo-wiki 与 Qoder 知识中心:
- 扫描目录树与项目配置(README/pyproject/package.json/Makefile/AGENTS.md 等);
- 建立结构化项目百科,落盘于 APP_DIR/repo-wiki/<hash>.json(绝不污染用户 Git 仓库);
- 支持 summary(概览)、module(查特定模块)、commands(查命令)、conventions(查约定)、refresh(重刷);
- 纯标准库实现,零重依赖,不阻塞主循环。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from minicode.paths import APP_DIR
from minicode import symbol_search
from minicode.sandbox import CREDENTIAL_DIRS, CREDENTIAL_FILENAMES, CREDENTIAL_SUFFIXES
from minicode.tools.base import ToolContext

WIKI_SCHEMA = 3
_SOURCE_EXTS = frozenset(symbol_search.SUPPORTED_EXTS)
_SKIP_FILES = frozenset(CREDENTIAL_FILENAMES) | {"config.json", "config.example.json"}
_HASH_MAX_BYTES = 512 * 1024

# 扫描时忽略的目录与隐藏项(与 repo_outline 同一哲学)
_IGNORE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "dist", "build",
    ".yinluxs", "checkpoints", "sessions", "uploads", "crawled",
    "browser-profile", "_pwtest", "_pwtest_empty", "repo-wiki", "repo_wiki",
}
_IGNORE_DIRS.update(CREDENTIAL_DIRS)

# 常见顶层目录用途推断表
_DIR_PURPOSES = {
    "tests": "单元测试与自动化验证套件",
    "test": "测试用例目录",
    "docs": "项目文档、设计规约与交接记录",
    "doc": "文档目录",
    "frontend": "前端界面、组件与静态资源",
    "ui": "界面与展示层代码",
    "web": "网页端实现与静态资源",
    "scripts": "自动化脚本、出包与运维工具",
    "tools": "内部辅助工具与命令实现",
    "skills": "Agent 技能定义与指导手册",
    "assets": "静态资源(图片、字体、媒体)",
    "images": "图像资源",
    "packaging": "应用打包、发布与安装配置",
    "build": "构建输出与临时目录",
    "dist": "打包发布物输出目录",
    "src": "核心源代码目录",
    "lib": "核心代码或依赖库",
    "pkg": "公共包与子模块",
}


def _workspace_hash(workspace: Path) -> str:
    """按工作区绝对路径的规范形式算哈希(跨平台保真)。"""
    try:
        raw = str(workspace.resolve()).replace("\\", "/").lower()
    except Exception:
        raw = str(workspace).replace("\\", "/").lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_wiki_file(workspace: Path) -> Path:
    """百科 JSON 落盘路径:APP_DIR/repo-wiki/<hash>.json。"""
    d = APP_DIR / "repo-wiki"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / f"{_workspace_hash(workspace)}.json"


def _extract_readme_overview(workspace: Path) -> tuple[str, str]:
    """提取 README 开头的核心介绍(过滤掉徽标和纯标题)。"""
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = workspace / name
        if p.is_file() and not p.is_symlink():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                lines = []
                first = 0
                for number, line in enumerate(content.splitlines()[:50], 1):
                    s = line.strip()
                    if not s or s.startswith(("[![", "[!", "<!--", "```")):
                        continue
                    if s.startswith("#"):
                        s = s.lstrip("#").strip()
                    if not first:
                        first = number
                    lines.append(s)
                    if len(lines) >= 3:
                        break
                if lines:
                    return "；".join(lines), f"{name}:{first}"
            except Exception:
                pass
    return "暂无 README 概括", ""


def _extract_commands(workspace: Path) -> dict[str, str]:
    """从 pyproject.toml / package.json / Makefile 中自动嗅探构建与测试命令。"""
    cmds: dict[str, str] = {}

    # 1. Python (pyproject.toml, pytest.ini, Makefile)
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
            if "pytest" in txt:
                cmds["test"] = "pytest"
            if "ruff" in txt:
                cmds["lint"] = "ruff check ."
            if "mypy" in txt:
                cmds["typecheck"] = "mypy ."
        except Exception:
            pass

    if (workspace / "pytest.ini").is_file() and "test" not in cmds:
        cmds["test"] = "pytest"

    # 2. Node.js (package.json)
    pkg_json = workspace / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            scripts = data.get("scripts", {})
            if isinstance(scripts, dict):
                if "test" in scripts and "test" not in cmds:
                    cmds["test"] = f"npm test (即: {scripts['test']})"
                if "build" in scripts:
                    cmds["build"] = f"npm run build (即: {scripts['build']})"
                if "lint" in scripts and "lint" not in cmds:
                    cmds["lint"] = f"npm run lint (即: {scripts['lint']})"
                if "dev" in scripts:
                    cmds["dev"] = f"npm run dev (即: {scripts['dev']})"
        except Exception:
            pass

    # 3. Rust (Cargo.toml)
    if (workspace / "Cargo.toml").is_file():
        cmds["test"] = cmds.get("test") or "cargo test"
        cmds["build"] = cmds.get("build") or "cargo build"
        cmds["lint"] = cmds.get("lint") or "cargo clippy"

    # 4. Go (go.mod)
    if (workspace / "go.mod").is_file():
        cmds["test"] = cmds.get("test") or "go test ./..."
        cmds["build"] = cmds.get("build") or "go build"

    # 5. Makefile targets
    makefile = workspace / "Makefile"
    if makefile.is_file():
        try:
            mtxt = makefile.read_text(encoding="utf-8", errors="replace")
            targets = re.findall(r"^([a-zA-Z0-9_\-]+):", mtxt, re.M)
            if targets:
                cmds["make_targets"] = "make " + " / ".join(targets[:6])
        except Exception:
            pass

    return cmds


def _extract_conventions(workspace: Path) -> tuple[str, str]:
    """从 AGENTS.md / CLAUDE.md / .cursorrules 提取工程约定要点。"""
    for name in ("AGENTS.md", "CLAUDE.md", ".cursorrules", "CONTRIBUTING.md"):
        p = workspace / name
        if p.is_file() and not p.is_symlink():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                key_lines = []
                first = 0
                for number, line in enumerate(txt.splitlines(), 1):
                    s = line.strip()
                    if s.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")):
                        if not first:
                            first = number
                        key_lines.append(s[:80])
                    if len(key_lines) >= 5:
                        break
                if key_lines:
                    return (f"来自 {name}:\n" + "\n".join(f"  {line}" for line in key_lines),
                            f"{name}:{first}")
            except Exception:
                pass
    return "未从项目约定文件提取到规则", ""


def _eligible(path: Path) -> bool:
    """不跟随目录链接,也不读取凭据类文件；Wiki 缓存可能进入模型上下文。"""
    name = path.name.lower()
    return (not path.is_symlink() and not name.startswith(".")
            and name not in _SKIP_FILES
            and path.suffix.lower() not in CREDENTIAL_SUFFIXES)


def _fingerprint(path: Path, size: int) -> str:
    """小文件用内容指纹兜住 size+mtime 都没变的修改；大文件只记元信息。"""
    if size > _HASH_MAX_BYTES:
        return ""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _inventory(
    workspace: Path,
) -> tuple[dict[str, list[tuple[str, int, int, str]]],
           list[tuple[str, int, int, str]]]:
    """仅取文件元信息；按目录分指纹,未变化的模块复用之前的索引。"""
    groups: dict[str, list[tuple[str, int, int, str]]] = {}
    root_files: list[tuple[str, int, int, str]] = []
    for root, dirs, files in os.walk(workspace, followlinks=False):
        parent = Path(root)
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_"))
                         and d not in _IGNORE_DIRS and _eligible(parent / d))
        rel_parent = parent.relative_to(workspace)
        if rel_parent.parts and rel_parent.parts[0].startswith("_"):
            dirs[:] = []
            continue
        for name in sorted(files):
            path = parent / name
            if not _eligible(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entry = (path.relative_to(workspace).as_posix(), stat.st_size,
                     stat.st_mtime_ns, _fingerprint(path, stat.st_size))
            if rel_parent.parts:
                groups.setdefault(rel_parent.parts[0], []).append(entry)
            else:
                root_files.append(entry)
    return groups, root_files


def _module_from_inventory(workspace: Path, name: str,
                           entries: list[tuple[str, int, int, str]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    key_files: list[str] = []
    sources: list[dict[str, Any]] = []
    for rel, _size, _mtime, _digest in entries:
        ext = Path(rel).suffix.lower()
        counts[ext] = counts.get(ext, 0) + 1
        if ext in _SOURCE_EXTS and len(key_files) < 4:
            key_files.append(rel)
            # 引用的是实际定义位置,不是对模块职责的模型推断。
            for symbol in symbol_search.extract_file_symbols(workspace / rel, rel):
                if symbol.get("kind") in ("class", "function", "interface"):
                    sources.append({"name": symbol["name"], "location":
                                    f"{rel}:{symbol['line']}"})
                if len(sources) >= 8:
                    break
    top_exts = sorted((e for e in counts if e), key=lambda e: (-counts[e], e))[:3]
    return {"name": name, "path": name, "file_count": len(entries),
            "extensions": top_exts, "purpose": _DIR_PURPOSES.get(name.lower(),
            f"模块包({name});职责未验证"), "key_files": key_files, "sources": sources}


def scan_workspace(workspace: Path, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """全自动轻量启发式扫描当前工作区,生成结构化百科。"""
    ws = Path(workspace)
    name = ws.name

    # 1. 尝试从配置获取规范项目名
    pyproject = ws / "pyproject.toml"
    if pyproject.is_file():
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', txt)
            if m:
                name = m.group(1)
        except Exception:
            pass
    elif (ws / "package.json").is_file():
        try:
            pdata = json.loads((ws / "package.json").read_text(encoding="utf-8", errors="replace"))
            if pdata.get("name"):
                name = str(pdata["name"])
        except Exception:
            pass

    # 2. 统计技术栈与语言分布
    ext_counts: dict[str, int] = {}
    modules: list[dict[str, Any]] = []
    groups, root_files = _inventory(ws)
    old_modules = {m["name"]: m for m in (previous or {}).get("modules", [])}
    old_inventory = (previous or {}).get("inventory", {}).get("groups", {})
    for group_name, entries in sorted(groups.items()):
        modules.append(old_modules[group_name] if old_inventory.get(group_name) == entries
                       and group_name in old_modules else _module_from_inventory(ws, group_name, entries))
    # 空目录也保留在结构里。
    for item in sorted(ws.iterdir()):
        if item.is_dir() and _eligible(item) and item.name not in _IGNORE_DIRS \
                and not item.name.startswith("_") and item.name not in groups:
            modules.append(_module_from_inventory(ws, item.name, []))
    for rel, _size, _mtime, _digest in [
            *root_files, *(entry for rows in groups.values() for entry in rows)]:
        ext = Path(rel).suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    total_files = len(root_files) + sum(map(len, groups.values()))

    # 判定主要语言
    top_languages = []
    lang_map = {
        ".py": "Python", ".ts": "TypeScript", ".js": "JavaScript",
        ".tsx": "React/TSX", ".jsx": "React/JSX", ".rs": "Rust",
        ".go": "Go", ".html": "HTML", ".css": "CSS", ".java": "Java",
        ".cpp": "C++", ".c": "C", ".vue": "Vue",
    }
    for ext, count in sorted(ext_counts.items(), key=lambda x: (-x[1], x[0])):
        lang = lang_map.get(ext)
        if lang and lang not in top_languages:
            top_languages.append(lang)
        if len(top_languages) >= 4:
            break

    overview, overview_source = _extract_readme_overview(ws)
    conventions, conventions_source = _extract_conventions(ws)
    wiki_data = {
        "schema": WIKI_SCHEMA,
        "inventory": {"groups": groups, "root": root_files},
        "name": name,
        "workspace": str(ws.resolve()).replace("\\", "/"),
        "languages": top_languages or ["通用"],
        "total_files": total_files,
        "overview": overview,
        "overview_source": overview_source,
        "commands": _extract_commands(ws),
        "modules": modules,
        "conventions": conventions,
        "conventions_source": conventions_source,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return wiki_data


def save_wiki(workspace: Path, data: dict[str, Any]) -> bool:
    """将百科持久化到本地数据目录。"""
    target = get_wiki_file(workspace)
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp",
                                        dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        return True
    except OSError:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        return False


def load_wiki(workspace: Path) -> dict[str, Any] | None:
    """读取本地缓存的百科。"""
    target = get_wiki_file(workspace)
    if target.is_file():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def get_or_build_wiki(workspace: Path, force_refresh: bool = False) -> dict[str, Any]:
    """获取工作区百科(若未生成或强制刷新则实时扫描并落盘)。"""
    cached = None if force_refresh else load_wiki(workspace)
    if cached and cached.get("schema") == WIKI_SCHEMA:
        groups, root = _inventory(Path(workspace))
        if cached.get("inventory") == {"groups": groups, "root": root}:
            return cached
    data = scan_workspace(workspace, previous=cached if cached and
                          cached.get("schema") == WIKI_SCHEMA else None)
    save_wiki(workspace, data)
    return data


def format_wiki_view(data: dict[str, Any], action: str = "summary", target: str = "") -> str:
    """根据动作格式化百科为人读友好的 Markdown 文本。"""
    name = data.get("name", "未命名项目")
    langs = " / ".join(data.get("languages", ["通用"]))
    overview = data.get("overview", "暂无概览")
    cmds = data.get("commands", {})
    modules = data.get("modules", [])
    conventions = data.get("conventions", "遵循标准工程约定")
    updated = data.get("updated_at", "")

    if action == "commands":
        if not cmds:
            return f"【{name}】代码库常用命令:\n未从配置文件中嗅探到预设构建或测试命令。"
        lines = [f"【{name}】代码库常用构建与测试命令:"]
        for k, v in cmds.items():
            lines.append(f"- **{k}**: `{v}`")
        return "\n".join(lines)

    if action == "conventions":
        src = data.get("conventions_source", "")
        return f"【{name}】代码库工程与编码约定:\n{conventions}" + (f"\n来源: {src}" if src else "")

    if action == "module":
        tgt = str(target or "").strip().lower()
        if not tgt:
            return "请指定要查看的目标模块名(如 target='minicode' 或 target='frontend')。"
        matched = [m for m in modules if tgt in m.get("name", "").lower() or tgt in m.get("path", "").lower()]
        if not matched:
            mod_names = ", ".join(m.get("name", "") for m in modules)
            return f"未找到名为「{target}」的模块。现有顶层模块: {mod_names}"
        m = matched[0]
        exts = ", ".join(m.get("extensions", []))
        key_files = ", ".join(m.get("key_files", [])) or "无主要源文件"
        sources = ", ".join(f"{s['name']} ({s['location']})" for s in m.get("sources", []))
        return (
            f"### 模块详情: `{m.get('name')}`\n"
            f"- **职责用途**: {m.get('purpose')}\n"
            f"- **文件数量**: 约 {m.get('file_count')} 个文件\n"
            f"- **主要语言/后缀**: {exts or '无'}\n"
            f"- **代表性文件**: {key_files}\n"
            f"- **源码定义**: {sources or '未检出'}\n"
        )

    # 默认 summary
    lines = [
        f"## 📚 代码库全景百科: {name}",
        f"- **技术栈**: {langs} (收录约 {data.get('total_files', 0)} 个源码文件)",
        f"- **项目定位**: {overview}" + (f" (来源: {data['overview_source']})" if data.get("overview_source") else ""),
        f"- **更新时间**: {updated}",
        "",
        "### 📁 核心模块分工",
    ]
    if modules:
        for m in modules[:12]:
            kf = f" (样例文件: {', '.join(m.get('key_files', [])[:2])})" if m.get("key_files") else ""
            lines.append(f"- **`{m.get('name')}/`** ({m.get('file_count')} 文件): {m.get('purpose')}{kf}")
    else:
        lines.append("- (根目录下暂无分层子模块)")

    if cmds:
        lines.append("")
        lines.append("### 🛠 常用命令")
        for k, v in list(cmds.items())[:4]:
            lines.append(f"- **{k}**: `{v}`")

    if conventions and "暂无" not in conventions:
        lines.append("")
        lines.append("### 📋 工程约定摘要")
        lines.append(conventions)
        if data.get("conventions_source"):
            lines.append(f"来源: {data['conventions_source']}")

    return "\n".join(lines)


def repo_wiki_tool(ctx: ToolContext, action: str = "summary", target: str = "") -> str:
    """`repo_wiki` 工具 handler 入口。"""
    act = (action or "summary").strip().lower()
    force_refresh = (act == "refresh")
    try:
        data = get_or_build_wiki(ctx.workspace, force_refresh=force_refresh)
        return format_wiki_view(data, action=act, target=target)
    except Exception as e:
        return f"读取或生成代码库百科时发生错误: {type(e).__name__}: {e}"
