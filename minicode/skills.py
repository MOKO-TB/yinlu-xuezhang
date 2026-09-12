"""SKILL.md 技能系统:skills/<目录>/SKILL.md(frontmatter: name/description + 指导正文)。

加载来源:应用目录 skills/(随应用分发)与工作区 skills/(项目级,同名时后者忽略——
先到先得)。系统提示词注入"可用技能"清单;模型通过 use_skill 工具按需读取全文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from minicode.paths import app_path

SKILL_BODY_LIMIT = 8000  # 技能全文回填给模型的截断长度


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str


def skill_dirs(workspace: Path | None = None) -> list[Path]:
    """技能目录:打包内置资源 + exe 旁 + 工作区(去重,重复加载由 name 兜底)。"""
    import os
    from minicode.paths import resource_path
    dirs = [app_path("skills"), resource_path("skills")]
    if workspace:
        dirs.append(Path(workspace) / "skills")
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(str(Path(d).resolve()))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
        if m:
            meta: dict = {}
            for line in m.group(1).splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            return meta, m.group(2)
    return {}, text


def load_skills(dirs: list[Path]) -> list[Skill]:
    """扫描目录,支持 skills/<目录>/SKILL.md 与 skills/<名字>.md 两种布局。"""
    skills: list[Skill] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.is_dir():
                md = entry / "SKILL.md"
            elif entry.suffix == ".md":
                md = entry
            else:
                continue
            if not md.exists():
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            default_name = entry.stem if entry.suffix == ".md" else entry.name
            name = str(meta.get("name") or default_name)
            if name in seen:
                continue
            seen.add(name)
            skills.append(Skill(name=name,
                                description=str(meta.get("description") or ""),
                                path=md, body=body.strip()))
    return skills


def find_skill(skills: list[Skill], name: str) -> Skill | None:
    for s in skills:
        if s.name == name.strip():
            return s
    return None


def skills_prompt_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [
        "",
        "# 可用技能",
        "以下技能拓展了你的能力。需要时先调用 use_skill 工具(name=技能名)读取完整指导,再按指导操作:",
    ]
    for s in skills:
        desc = f" —— {s.description}" if s.description else ""
        lines.append(f"- {s.name}{desc}")
    return "\n".join(lines) + "\n"


def use_skill(ctx, name: str) -> str:
    """工具处理器:按名字返回技能全文(截断)。"""
    skills = load_skills(skill_dirs(ctx.workspace))
    skill = find_skill(skills, name)
    if skill is None:
        available = ", ".join(s.name for s in skills) or "(无)"
        return f"错误: 未找到技能 '{name}'。可用技能: {available}"
    body = skill.body[:SKILL_BODY_LIMIT]
    tail = "\n…(技能内容过长,已截断)" if len(skill.body) > SKILL_BODY_LIMIT else ""
    return f"# 技能: {skill.name}\n\n{body}{tail}"
