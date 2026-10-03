"""SKILL.md 技能系统:skills/<目录>/SKILL.md(frontmatter: name/description + 指导正文)。

加载来源:应用目录 skills/(随应用分发)与工作区 skills/(项目级,同名时后者忽略——
先到先得)。系统提示词注入"可用技能"清单;模型通过 use_skill 工具按需读取全文。

**依赖声明(v0.9.98)**:frontmatter 可选 `tools:`(逗号分隔的内置工具名)与
`libs:`(逗号分隔的 import 名,如 `pptx, pypdf`)。声明了才核对 —— `use_skill`
读取时真查一遍(工具名对注册表、库对 importlib),缺了就在技能正文**之前**摆一句
"缺什么、别硬跑",而不是等模型把模板抄进 py_script 跑到一半才报
ModuleNotFoundError。没声明的技能一个字不多问(声明是承诺,不是猜测;
库里已有技能的 libs 声明是逐个 grep 过模板里真 import 的,不是按名字猜的)。
"""
from __future__ import annotations

import json
import re
import time
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
    tools: tuple[str, ...] = ()   # 声明依赖的内置工具名(frontmatter `tools:`)
    libs: tuple[str, ...] = ()    # 声明依赖的 Python 库 import 名(frontmatter `libs:`)


def _csv_decl(value) -> tuple[str, ...]:
    """frontmatter 里逗号分隔的声明 → 元组;全角逗号也认(技能是中文用户手编的)。"""
    return tuple(x.strip() for x in str(value or "").replace("，", ",").split(",")
                 if x.strip())


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
                                path=md, body=body.strip(),
                                tools=_csv_decl(meta.get("tools")),
                                libs=_csv_decl(meta.get("libs"))))
    return skills


def dependency_report(skill: Skill, available_tools=None, probe=None) -> dict:
    """对技能声明的依赖做**真核对**(v0.9.98)。返回:

    - `missing_tools`:声明了但注册表里没有的工具名;`available_tools=None`
      表示这次没核对(与 router 的 None/[] 两种语义同一条规矩,不把"没查"
      说成"本机没有");
    - `missing_libs`:声明了但本机 import 不到的库。probe 按顶层包名查
      (`pptx.utils` 查的是 `pptx`),缺省 `importlib.util.find_spec`,
      测试可注入替身。

    只核**声明过的**:没声明的技能既不查也不警告 —— 依赖声明是技能作者的
    承诺,程序不猜"这份技能大概要用什么"。
    """
    import importlib.util
    if probe is None:
        probe = lambda mod: importlib.util.find_spec(mod) is not None
    missing_tools = (None if available_tools is None
                     else [t for t in skill.tools if t not in set(available_tools)])
    missing_libs = [lib for lib in skill.libs if not probe(lib.split(".")[0])]
    return {"missing_tools": missing_tools, "missing_libs": missing_libs}


def _warning_from(r: dict) -> str:
    """从一份 dependency_report 的结果拼那句给模型看的警语;什么都不缺回空串。"""
    bits = []
    if r["missing_tools"]:
        bits.append("工具本机没有:" + "、".join(r["missing_tools"]))
    if r["missing_libs"]:
        bits.append("Python 库没装:" + "、".join(r["missing_libs"]))
    if not bits:
        return ""
    return ("(⚠ 依赖缺:" + ";".join(bits) + " —— 这份技能里有一部分代码/工具用不了。"
            "把缺的**照实**告诉用户并给安装/替代办法,不要照模板硬跑,更不要假装跑过。)")


def _dependency_warning(skill: Skill, available_tools) -> str:
    """缺依赖时给模型看的那句警语;什么都不缺(或没声明)回空串。"""
    return _warning_from(dependency_report(skill, available_tools=available_tools))


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


# ---------------------------------------------------- 技能使用账(v0.9.63)
#
# 「这么多技能里到底哪几份真有人用」一直没有账 —— 这份 jsonl 就是那本账。
# **每次成功调 use_skill 都记一条**(失败的不算:没读到的技能谈不上用过);
# 不去重,次数就是频次本身。审计脚本 scripts/review_skill_usage.py 汇总它。
#
# v0.9.109 起这一行还带上**这次依赖核对的结果**(`miss_tools` / `miss_libs`)。
# 缘由:§6 #45 那条欠账写的是「这一档最近老失败进不了决策」,而原实现连"失败"
# 都没记 —— 缺 pandas 的技能照样记成"用过一次",账就成了假账。先有列,再谈读者。

USAGE_FILE = "skill-usage.jsonl"


def usage_path() -> Path:
    return app_path(USAGE_FILE)


def log_skill_use(name: str, missing: "dict | None" = None) -> bool:
    """记一次技能使用,并把**这次依赖核对的结果**一起记下来。写失败静默回 False。

    `missing` 只在核对过时传(就是 `dependency_report` 那份字典);不传就不写这两个
    字段。这两种情况必须分得开:**"没查过"不等于"查过、什么都不缺"** —— 前者不能拿去
    说"这技能能跑"。v0.9.63 起的老行都没有这两个字段,它们永远算"没查过"。
    """
    text = str(name or "").strip()
    if not text:
        return False
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "skill": text[:60]}
    if isinstance(missing, dict):
        rec["miss_tools"] = [str(t)[:40] for t in (missing.get("missing_tools") or [])]
        rec["miss_libs"] = [str(l)[:40] for l in (missing.get("missing_libs") or [])]
    try:
        path = usage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def usage_gaps() -> "dict[str, dict]":
    """每个技能**最近一次核对**还缺的依赖;不缺的、没核对过的都不在表里。

    取的是**文件里最后一条带核对结果**的记录(这本账按追加写,后写的就是后发生的)——
    不比时间字符串:这个仓库历史上把时刻写过两种格式(`%H:%M` 与 `%H:%M:%S`),
    比字符串会把旧格式的行当成"更早",那条坑记在 §4。
    某技能最近一次核对什么都不缺,它就从表里消失 —— 这本账说的是"现在还缺什么",
    不是"历史上缺过什么"。
    """
    try:
        raw = usage_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    last_checked: dict[str, dict] = {}
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("skill") or "").strip()
        if not name or not ("miss_tools" in rec or "miss_libs" in rec):
            continue                     # 没核对过的那些行,一个字都不采信
        last_checked[name] = {
            "tools": [str(t) for t in (rec.get("miss_tools") or []) if str(t).strip()],
            "libs": [str(l) for l in (rec.get("miss_libs") or []) if str(l).strip()],
            "ts": str(rec.get("ts") or ""),
        }
    return {name: g for name, g in last_checked.items() if g["tools"] or g["libs"]}


def gap_note(skill_names: "list[str]", gaps: "dict | None") -> str:
    """把「这一档要用的技能里,哪几份在这台机器上还缺什么」压成一句人读的话;没缺就回空串。"""
    if not gaps:
        return ""
    bits = []
    for name in skill_names:
        g = gaps.get(name)
        if not g:
            continue
        tools = [str(t) for t in (g.get("tools") or []) if str(t).strip()]
        libs = [str(l) for l in (g.get("libs") or []) if str(l).strip()]
        if not tools and not libs:
            continue        # 空缺口不写成一句话:那等于对着用户念"还缺(什么都不缺)"
        what = "、".join([f"工具 {t}" for t in tools] + [f"库 {l}" for l in libs])
        when = f"(最近一次核对 {g['ts']})" if g.get("ts") else ""
        bits.append(f"{name} 还缺 {what}{when}")
    return ";".join(bits)


def usage_summary() -> "list[dict]":
    """按技能聚合:用了几次、最近一次什么时候。没有记录回空表。"""
    try:
        raw = usage_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    agg: dict[str, dict] = {}
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("skill") or "").strip()
        ts = str(rec.get("ts") or "")
        if not name:
            continue
        row = agg.setdefault(name, {"skill": name, "uses": 0, "last": ""})
        row["uses"] += 1
        if ts > row["last"]:
            row["last"] = ts
    return sorted(agg.values(), key=lambda r: -r["uses"])


def merge_usage(skills: list[dict]) -> list[dict]:
    """把使用账并进 get_skills 的技能清单:用过几次、最近一次何时。

    没用过的技能不加这两个键 —— 几十张卡片全都写「还没用过」是噪音不是信息;
    界面只给真用过的摆一笔,从没动过的保持安静。
    """
    usage = {r["skill"]: r for r in usage_summary()}
    out: list[dict] = []
    for s in skills:
        u = usage.get(str(s.get("name") or ""))
        out.append({**s, "uses": u["uses"], "last": u["last"]} if u else s)
    return out


def use_skill(ctx, name: str) -> str:
    """工具处理器:按名字返回技能全文(截断)。每次成功调用记一笔使用账。

    声明了依赖的技能先真核对一遍(v0.9.98):缺了就把警语摆在正文**之前**
    —— 模型读到的第一眼就是"哪些用不了",不是抄完模板跑一半才炸。
    核对用的工具清单现取自注册表(与模型实际拿到的同一份);全部齐(或没声明)
    时输出与旧版逐字一致。

    落账时把这次核对的结果一起落下(v0.9.109):账上因此第一次有了"这台机器上
    这份技能跑不齐"这一列,派档那侧才有话可说(见 `usage_gaps` / router 的 gap 那句)。
    """
    skills = load_skills(skill_dirs(ctx.workspace))
    skill = find_skill(skills, name)
    if skill is None:
        available = ", ".join(s.name for s in skills) or "(无)"
        return f"错误: 未找到技能 '{name}'。可用技能: {available}"
    from minicode.tools.registry import build_default_registry
    report = dependency_report(
        skill, available_tools={t.name for t in build_default_registry()})
    log_skill_use(skill.name, missing=report)
    warn = _warning_from(report)
    body = _clip_body(skill.body)
    tail = ("\n…(技能内容过长,已截断;若尾部有未完整的代码模板,不要凭空补全 —— "
            "按已有的部分说明缺什么即可)" if len(skill.body) > SKILL_BODY_LIMIT else "")
    head = f"# 技能: {skill.name}\n\n" + (f"{warn}\n\n" if warn else "")
    return f"{head}{body}{tail}"


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
