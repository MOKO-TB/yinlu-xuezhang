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

# 用户自建技能的入口渠道(渠道页会按这几个目录展示/落盘):
#   builtin  = 打包进应用的只读技能(随应用分发,升级不丢但不可写)
#   app      = exe/源码旁的 skills/ 目录(共享给所有工作区,推荐写这里)
#   workspace= 当前工作区 skills/(项目级,同名时先到先得,优先级最低)
SKILL_CHANNELS = (
    ("builtin", "内置技能", "随应用分发的只读技能,升级应用后仍在"),
    ("app", "应用目录", "exe/源码旁边的 skills/,所有工作区共用,推荐放这里"),
    ("workspace", "当前工作区", "工作区里的 skills/,只在当前项目生效"),
)


def app_skills_dir(app_dir: Path | None = None) -> Path:
    """「应用目录」渠道的落点:app_dir/skills;不传 app_dir 用全局 APP_DIR。

    Bridge 传自己的 app_dir(测试里被钉到临时目录,与 workspaces.json 同级),
    直接调 describe_channels/create_skill 的旧代码路径仍走全局 APP_DIR。
    """
    if app_dir is not None:
        return Path(app_dir) / "skills"
    return app_path("skills")


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


def _clip_body(body: str, limit: int = SKILL_BODY_LIMIT) -> str:
    """超限截断,但**回退到最后一个闭合代码围栏之后**:技能正文里是模型要照着
    抄的脚本模板,硬切在 ```python 块中间等于给模型半截模板(§4 老坑的同型)。
    统计截断点之前的围栏数,奇数 = 切在块内 → 回退到上一个闭合围栏(含);
    回退点不存在(整段就是一个超长围栏)时保持硬切,尾注会提醒。
    """
    if len(body) <= limit:
        return body
    clipped = body[:limit]
    if clipped.count("```") % 2 == 1:
        last = clipped.rfind("```")
        prev = clipped.rfind("```", 0, last)
        if prev > 0:
            clipped = clipped[:prev + 3]
    return clipped


def use_skill(ctx, name: str) -> str:
    """工具处理器:按名字返回技能全文(截断)。"""
    skills = load_skills(skill_dirs(ctx.workspace))
    skill = find_skill(skills, name)
    if skill is None:
        available = ", ".join(s.name for s in skills) or "(无)"
        return f"错误: 未找到技能 '{name}'。可用技能: {available}"
    body = _clip_body(skill.body)
    tail = ("\n…(技能内容过长,已截断;若尾部有未完整的代码模板,不要凭空补全 —— "
            "按已有的部分说明缺什么即可)" if len(skill.body) > SKILL_BODY_LIMIT else "")
    return f"# 技能: {skill.name}\n\n{body}{tail}"


# ------------------------------------------------------------ 技能面板(渠道)

def _channel_slug(text: str) -> str:
    """技能名/目录名清洗:中文保留(v0.9.55 —— 校园用户最自然的起名就是中文,
    原来只留 ASCII 等于让技能面板对中文用户第一步就撞墙),其余只留文件名安全
    字符;清空后给空串(由调用方拒绝)。"""
    return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "-", text.strip()).strip("-")


def describe_channels(workspace: Path | None = None,
                      app_dir: Path | None = None) -> list[dict]:
    """渠道页「渠道」tab 的数据源:三个技能目录的存在状态与技能数。"""
    from minicode.paths import resource_path
    dirs = {"app": app_skills_dir(app_dir), "builtin": resource_path("skills")}
    if workspace:
        dirs["workspace"] = Path(workspace) / "skills"
    out: list[dict] = []
    for key, label, note in SKILL_CHANNELS:
        d = dirs.get(key)
        if d is None:
            out.append({"key": key, "label": label, "note": note,
                        "path": "", "exists": False, "count": 0})
            continue
        count = _count_skill_files(d)
        out.append({"key": key, "label": label, "note": note,
                    "path": str(d), "exists": d.is_dir(), "count": count})
    return out


def _count_skill_files(d: Path) -> int:
    """数一个技能目录里有几个技能(SKILL.md 目录布局 + 单 .md 文件布局)。"""
    if not d.is_dir():
        return 0
    count = 0
    for entry in d.iterdir():
        md = entry / "SKILL.md" if entry.is_dir() else (
            entry if entry.suffix == ".md" else None)
        if md is not None and md.exists():
            count += 1
    return count


def create_skill(workspace: Path | None, name: str, description: str = "",
                 body: str = "", channel: str = "app",
                 app_dir: Path | None = None) -> dict:
    """面板「添加技能」:把用户填的 name/description/body 写成 SKILL.md。

    builtin 渠道只读(打包资源里写不进去),一律拒绝;同名技能已存在时拒绝
    (load_skills 是先到先得,静默覆盖会让用户以为自己改了内置技能)。
    返回 {"ok", "msg", "name", "path"}。
    """
    name = _channel_slug(name or "")
    if not name:
        return {"ok": False, "msg": "技能名不能为空"}
    if channel == "builtin" or channel not in {c[0] for c in SKILL_CHANNELS}:
        return {"ok": False, "msg": "内置技能目录只读:请选择「应用目录」或「当前工作区」"}
    target_dir = (Path(workspace) / "skills" if channel == "workspace"
                  else app_skills_dir(app_dir))
    d = target_dir / name
    if d.exists():
        return {"ok": False, "msg": f"同名技能目录已存在:{name}(换个名字,或先删掉旧的)"}
    try:
        d.mkdir(parents=True, exist_ok=False)
        desc = (description or "").strip().replace("\n", " ")[:200]
        # 描述进 frontmatter 后由 skills_prompt_section **每一轮**全文携带:
        # 不截断的话,面板里一句长描述就把系统提示词永久撑大(创建时截,后端不管)。
        front = f"---\nname: {name}\ndescription: {desc}\n---\n"
        (d / "SKILL.md").write_text(front + (body or "").strip() + "\n",
                                    encoding="utf-8")
    except OSError as exc:
        return {"ok": False,
                "msg": f"写入技能目录失败:{type(exc).__name__}: {exc}"}
    return {"ok": True, "name": name, "path": str(d),
            "msg": f"技能「{name}」已添加到{dict((c[0], c[1]) for c in SKILL_CHANNELS)[channel]},新任务即可使用"}
