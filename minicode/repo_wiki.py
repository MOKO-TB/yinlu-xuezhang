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
from datetime import datetime
from pathlib import Path
from typing import Any

from minicode.paths import APP_DIR
from minicode.tools.base import ToolContext

# 扫描时忽略的目录与隐藏项(与 repo_outline 同一哲学)
_IGNORE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "dist", "build",
    ".yinluxs", "checkpoints", "sessions", "uploads", "crawled",
    "browser-profile", "_pwtest", "_pwtest_empty", "repo-wiki", "repo_wiki",
}

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


def _extract_readme_overview(workspace: Path) -> str:
    """提取 README 开头的核心介绍(过滤掉徽标和纯标题)。"""
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = workspace / name
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                lines = []
                for line in content.splitlines()[:50]:
                    s = line.strip()
                    if not s or s.startswith(("[![", "[!", "<!--", "```")):
                        continue
                    if s.startswith("#"):
                        s = s.lstrip("#").strip()
                    lines.append(s)
                    if len(lines) >= 3:
                        break
                if lines:
                    return "；".join(lines)
            except Exception:
                pass
    return "暂无 README 概括"


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


def _extract_conventions(workspace: Path) -> str:
    """从 AGENTS.md / CLAUDE.md / .cursorrules 提取工程约定要点。"""
    for name in ("AGENTS.md", "CLAUDE.md", ".cursorrules", "CONTRIBUTING.md"):
        p = workspace / name
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                key_lines = []
                for line in txt.splitlines():
                    s = line.strip()
                    if s.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")):
                        key_lines.append(s[:80])
                    if len(key_lines) >= 5:
                        break
                if key_lines:
                    return f"来自 {name}:\n" + "\n".join(f"  {line}" for line in key_lines)
            except Exception:
                pass
    return "遵循常规开源工程规范(保持代码清晰、单测完备)"


def scan_workspace(workspace: Path) -> dict[str, Any]:
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
    total_files = 0
    modules: list[dict[str, Any]] = []

    try:
        children = sorted(ws.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        children = []

    for item in children:
        if item.name.startswith((".", "_")) or item.name in _IGNORE_DIRS:
            continue

        if item.is_dir():
            # 扫描模块目录
            mod_files = 0
            mod_exts: dict[str, int] = {}
            key_files: list[str] = []
            try:
                for root, dirs, files in os.walk(item):
                    # 就地剪枝忽略项
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _IGNORE_DIRS]
                    for f in files:
                        if f.startswith("."):
                            continue
                        mod_files += 1
                        ext = Path(f).suffix.lower()
                        if ext:
                            mod_exts[ext] = mod_exts.get(ext, 0)
                            ext_counts[ext] = ext_counts.get(ext, 0)
                        if len(key_files) < 4 and ext in (".py", ".ts", ".js", ".go", ".rs", ".html"):
                            key_files.append(f)
            except OSError:
                pass

            total_files += mod_files
            purpose = _DIR_PURPOSES.get(item.name.lower()) or (f"模块包({item.name})")
            top_exts = sorted(mod_exts.keys(), key=lambda k: mod_exts[k], reverse=True)[:3]
            modules.append({
                "name": item.name,
                "path": item.name,
                "file_count": mod_files,
                "extensions": top_exts,
                "purpose": purpose,
                "key_files": key_files,
            })
        elif item.is_file():
            total_files += 1
            ext = item.suffix.lower()
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0)

    # 判定主要语言
    top_languages = []
    lang_map = {
        ".py": "Python", ".ts": "TypeScript", ".js": "JavaScript",
        ".tsx": "React/TSX", ".jsx": "React/JSX", ".rs": "Rust",
        ".go": "Go", ".html": "HTML", ".css": "CSS", ".java": "Java",
        ".cpp": "C++", ".c": "C", ".vue": "Vue",
    }
    for ext, count in sorted(ext_counts.items(), key=lambda x: x[1], reverse=True):
        lang = lang_map.get(ext)
        if lang and lang not in top_languages:
            top_languages.append(lang)
        if len(top_languages) >= 4:
            break

    wiki_data = {
        "name": name,
        "workspace": str(ws.resolve()).replace("\\", "/"),
        "languages": top_languages or ["通用"],
        "total_files": total_files,
        "overview": _extract_readme_overview(ws),
        "commands": _extract_commands(ws),
        "modules": modules,
        "conventions": _extract_conventions(ws),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return wiki_data


def save_wiki(workspace: Path, data: dict[str, Any]) -> bool:
    """将百科持久化到本地数据目录。"""
    target = get_wiki_file(workspace)
    try:
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
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
    if not force_refresh:
        cached = load_wiki(workspace)
        if cached and cached.get("modules") is not None:
            return cached
    data = scan_workspace(workspace)
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
        return f"【{name}】代码库工程与编码约定:\n{conventions}"

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
        return (
            f"### 模块详情: `{m.get('name')}`\n"
            f"- **职责用途**: {m.get('purpose')}\n"
            f"- **文件数量**: 约 {m.get('file_count')} 个文件\n"
            f"- **主要语言/后缀**: {exts or '无'}\n"
            f"- **代表性文件**: {key_files}\n"
        )

    # 默认 summary
    lines = [
        f"## 📚 代码库全景百科: {name}",
        f"- **技术栈**: {langs} (收录约 {data.get('total_files', 0)} 个源码文件)",
        f"- **项目定位**: {overview}",
        f"- **更新时间**: {updated}",
        "",
        "### 📁 核心模块分工",
    ]
    if modules:
        for m in modules[:12]:
            kf = f" (核心: {', '.join(m.get('key_files', [])[:2])})" if m.get("key_files") else ""
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
