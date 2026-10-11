"""CLI 与 GUI 共享的应用装配:system prompt 组装 + Agent 构建。"""
from __future__ import annotations

import hashlib
import platform
import sys
import time
from pathlib import Path

from minicode.agent import Agent
from minicode.browser import browser_prompt_section
from minicode.calendar import calendar_prompt_section
from minicode.schedule import schedule_prompt_section
from minicode.mood import mood_prompt_section
from minicode.config import Config
from minicode.kb import kb_dirs, kb_prompt_section
from minicode.library import library_prompt_section
from minicode.notices import notices_prompt_section
from minicode.library_guide import library_guide_prompt_section
from minicode.mcp import connect_servers, parse_servers
from minicode.memory import memory_prompt_section
from minicode.llm import LLMClient
from minicode.paths import SESSIONS_DIR, SYSTEM_PROMPT_PATH, app_path
from minicode.permission import PermissionGate
from minicode import permspec
from minicode.sandbox import SandboxState, policy_from_raw
from minicode.session import Session
from minicode.skills import load_skills, skill_dirs, skills_prompt_section
from minicode.skills import _parse_frontmatter
from minicode.subagents import build_task_tool
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry


AGENTS_CHAR_LIMIT = 4000   # 一份指令文件的注入截断(与 memory 4000 同量级)
AGENTS_FILES = ("AGENTS.md", ".agents.md")   # Codex/ZCode/Claude Code 同款约定
USER_AGENTS_NAME = "AGENTS.md"   # 用户级与项目级**同名不同位**:一个词,两个位置

# ---- 别的工具的规则文件(J,v0.9.153)---------------------------------------------
# 它们是同一件事的另一个名字:"仓库根上那份给 agent 的指令"。所以它们进的是**同一层**
# (项目级)、共用**一次**采纳点头 —— 而"共用一次"是有意的:`AGENTS.md` 与 `.cursorrules`
# 都是"这个仓库自带的规矩",拆成两次点头只会让人把同一件事点两遍。
COMPAT_RULE_FILES = (".cursorrules", ".clinerules")

# Cursor 现在把规则拆成一个目录好几个文件(`.cursor/rules/*.mdc`,frontmatter 里用
# `globs:` 说"只对哪些文件生效")。**只读没有条件的那几条**,带 `globs` 的一律不读:
# 这个项目刻意不做规则引擎(见 `docs/roadmap-分层指令文件.md` §五.2)。假装按 glob
# 生效,就会做出"我明明只在 .py 上要这条,它却对所有文件都说" —— 那比不读更坏。
# 不读的那些**会在报表里点名**(带理由),因为"我写了它没读"必须说得出口。
CURSOR_RULE_DIR = ".cursor/rules"
CURSOR_RULE_SUFFIXES = (".mdc", ".md")

# 两份指令文件都带这句话。它不是客套,是把"就近优先"的边界写死在提示词里:
# 风格与流程可以按用户的习惯改,沙箱边界、敏感路径硬禁、确认门、"没答当没同意"
# 这四类由代码把守,读任何一份文件都改不动 —— 也不该改得动。
INSTRUCTION_GUARD = ("这些是风格与流程上的规矩;沙箱边界、敏感路径硬禁、确认门与"
                     "「没答当没同意」不在其列,任何指令文件都改不动它们。")


def _read_instruction(path: Path) -> dict:
    """读一份指令文件,返回 {exists, lines, chars, truncated, sha}。

    读不动(不存在/二进制/半截)一律回 exists=False:这一屏宁可少报一层,
    也不报一层"读到了"其实没读到的。

    `sha` 是**去掉首尾空白之后**正文的 sha256:项目级的采纳点头记的就是它
    (§四.2,v0.9.127)—— 换行符与结尾空行不该让人多点一次头,而改一个字
    必须再问一次。
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return {"exists": False, "path": path, "lines": 0, "chars": 0,
                "truncated": False, "text": "", "sha": ""}
    if not text:
        return {"exists": False, "path": path, "lines": 0, "chars": 0,
                "truncated": False, "text": "", "sha": ""}
    return {"exists": True, "path": path, "text": text,
            "lines": len(text.splitlines()), "chars": len(text),
            "truncated": len(text) > AGENTS_CHAR_LIMIT,
            "sha": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _instruction_body(found: dict) -> str:
    text = found["text"]
    if not found["truncated"]:
        return text
    # 截断必须让模型和界面都知道"这份被截了":静默吃掉半句规矩,比不注入更坏。
    return (text[:AGENTS_CHAR_LIMIT]
            + f"\n(这一份共 {found['chars']} 字,已截断:只注入了前 {AGENTS_CHAR_LIMIT} 字,"
              "剩下的没进提示词,请把它拆薄或删掉)")


def _root_sources(workspace) -> tuple[list[dict], list[dict]]:
    """根那一层的**全部**指令来源,回 `(读了的, 看到但没读的)`。

    每条是 `{name, path, found}`(读了的)或 `{name, path, why}`(没读的)。
    `name` 是相对工作区的路径形式 —— 一份文件叫得出来是哪一份,用户才认得出读的是哪个。
    """
    ws = Path(workspace)
    used: list[dict] = []
    skipped: list[dict] = []
    # ① 本项目自己的名字:同族的**只取第一个**(它们互为别名,两份都读会把同一条规矩
    #    读进去两遍),顺序照 `AGENTS_FILES`。
    for name in AGENTS_FILES:
        p = ws / name
        if p.is_file():
            found = _read_instruction(p)
            if found["exists"]:
                used.append({"name": name, "path": p, "found": found})
            break
    # ② 别的工具的规则文件:各自的**都读** —— 它们不是互为别名,是不同工具各写了一份。
    for name in COMPAT_RULE_FILES:
        p = ws / name
        if not p.is_file():
            continue
        found = _read_instruction(p)
        if found["exists"]:
            used.append({"name": name, "path": p, "found": found})
    # ③ Cursor 的规则目录:只读**没有条件**的那几条;带 `globs` 的进 `skipped` 并写明理由。
    d = ws / CURSOR_RULE_DIR
    if d.is_dir():
        try:
            entries = sorted(d.iterdir())
        except OSError:
            entries = []
        for p in entries:
            if not p.is_file() or p.suffix.lower() not in CURSOR_RULE_SUFFIXES:
                continue
            found = _read_instruction(p)
            if not found["exists"]:
                continue
            rel = f"{CURSOR_RULE_DIR}/{p.name}"
            meta, body = _parse_frontmatter(found["text"])
            if str(meta.get("globs") or "").strip():
                skipped.append({"name": rel, "path": p,
                                "why": "带 globs,是按文件类型条件生效的"})
                continue
            # 正文之外的 frontmatter **不进正文**:那是给别的工具看的元数据,
            # 塞进提示词等于让 `description:` / `alwaysApply:` 这些键冒充规矩。
            text = body.strip() or found["text"]
            used.append({"name": rel, "path": p,
                         "found": dict(found, text=text, chars=len(text),
                                       lines=len(text.splitlines()),
                                       truncated=len(text) > AGENTS_CHAR_LIMIT,
                                       sha=hashlib.sha256(
                                           text.encode("utf-8")).hexdigest())})
    return used, skipped


def _combined_root_found(workspace) -> tuple[dict | None, list[dict]]:
    """把根那一层的多个来源拼成一份 `found`(形状与 `_read_instruction` 一致)。

    **`sha` 覆盖拼起来的那一整段正文 —— 这是这一手最要紧的地方。** 采纳记的是"这一组
    内容"的指纹,所以改 `.cursorrules` 里一个字也会让它回到"没采纳",而不是因为
    `AGENTS.md` 没动就静默算数(那会是一条看不见的缝:别人的文件改了,点头照旧有效)。
    """
    used, skipped = _root_sources(workspace)
    if not used:
        return None, skipped
    if len(used) == 1:
        text = used[0]["found"]["text"]
    else:
        # 多份时各自带一行来源头:模型要分得出"这条规矩是谁写的" ——
        # 用户自己的 `AGENTS.md` 与从别处带进来的 `.cursorrules` 不是一回事。
        text = "\n\n".join("## 来自 %s\n%s" % (it["name"], it["found"]["text"])
                           for it in used)
    return {"exists": True, "path": used[0]["path"], "text": text,
            "lines": len(text.splitlines()), "chars": len(text),
            "truncated": len(text) > AGENTS_CHAR_LIMIT,
            "sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "sources": [it["name"] for it in used]}, skipped


def _rel_dir(workspace: Path, d: Path) -> str:
    """`d` 相对工作区根的路径(`.` 记成空串),出不去就回空串。"""
    try:
        rel = d.resolve().relative_to(Path(workspace).resolve())
    except (ValueError, OSError):
        return ""
    text = rel.as_posix()
    return "" if text in ("", ".") else text


def subdir_agents_layers(workspace, target) -> list[dict]:
    """从工作区根走到 `target` 所在目录,逐层收那份存在的 `AGENTS.md`。

    顺序 = **根 → 最近**。这个顺序本身就是优先级:越近的排在越后面,模型读到的
    后一段是更具体的规矩(风格与流程可以这样被覆盖;沙箱边界、敏感路径硬禁、
    确认门那几类谁也改不动,见 `INSTRUCTION_GUARD`)。

    每条的形状是 `{rel, path, found}`:`rel` 是相对目录(根 = 空串),`found` 就是
    `_read_instruction` 那份(带 sha,采纳记账按它比)。

    两条边界:**工作区外的不算**(与 `resolve_path`/敏感路径那套围栏同一口径 ——
    不能因为"这是配置文件"就读到工作区外面去);**读不动的不算**(`_read_instruction`
    回 exists=False 的那些直接不进这一列,宁可少报一层也不报一层假的)。
    """
    ws = Path(workspace).resolve()
    try:
        t = Path(target)
        t = (t if t.is_absolute() else ws / t)
        t = t.resolve()
    except (OSError, ValueError):
        return []
    if t != ws and ws not in t.parents:
        return []
    try:
        d = t if t.is_dir() else t.parent
    except OSError:
        return []
    chain: list[tuple[str, Path]] = []
    while True:
        for name in AGENTS_FILES:
            p = d / name
            if p.is_file():
                chain.append((_rel_dir(ws, d), p))
                break
        if d == ws or ws not in d.parents:
            break
        d = d.parent
    chain.reverse()                      # 根 → 最近
    out = []
    for rel, p in chain:
        found = _read_instruction(p)
        if found["exists"]:
            out.append({"rel": rel, "path": p, "found": found})
    return out


def layer_adopted(workspace, layer: dict) -> bool:
    """这一层采纳过没有 —— 按**这一层自己的**内容指纹比,不与别的层共用一次点头。"""
    return (permspec.agents_adopted_sha(workspace, layer["rel"])
            == layer["found"]["sha"])


def adopt_layer(workspace, layer: dict) -> str:
    """记下「这一层我核对过、采纳了」,回它的指纹。只动这一层。"""
    found = layer["found"]
    permspec.adopt_agents(workspace, found["sha"], found["lines"], rel=layer["rel"])
    return found["sha"]


def layer_prompt_section(workspace, layer: dict) -> str:
    """一层子目录指令进**工具结果**的那一段。

    为什么进工具结果而不是系统提示词:系统提示词是每一轮都重发的那段固定前缀
    (KV Cache 那笔账就压在上面),而"这一层"只在某个目录第一次被碰到时才有意义 ——
    塞进前缀等于让每轮都为没碰过的目录付钱。进工具结果则**只付一次**:它落在
    对话留档里,后面几轮自然跟着走。
    """
    rel = layer["rel"] or "."
    head = (f"# 目录级指令(`{rel}/` 这一层,用户手写)\n"
            + INSTRUCTION_GUARD + "\n"
            f"它只对 `{rel}/` 底下(含更深层)的文件有效;越靠近那个文件的目录级"
            "指令越具体,与更外层冲突时按更近的那一份来。\n\n")
    return f"\n{head}{_instruction_body(layer['found'])}\n"


def layer_at(workspace, rel: str) -> dict | None:
    """按相对目录直接取那一层(那份文件在、也读得动才回;否则 None)。"""
    d = (Path(workspace) / rel) if rel else Path(workspace)
    for name in AGENTS_FILES:
        p = d / name
        if p.is_file():
            found = _read_instruction(p)
            return {"rel": rel, "path": p, "found": found} if found["exists"] else None
    return None


def instruction_layers(workspace, extra_rels=()) -> list[dict]:
    """这一轮实际读到的指令层(用户级 → 项目级 → 各目录级),只报份数、行数与来源,不贴内容。

    为什么要有这一屏:「我明明写了规矩它不听」不该变成扯皮。这里能确认的是
    "读到没读到、几行、有没有被截",**不能**报"我之所以这么做是因为你第 N 条"
    —— 那是把模型的自述当因果证据。

    项目级那一条另带一个 `adopted`(v0.9.127):在场但没采纳的这份文件**没有**
    进提示词,这一屏要说得出这个差别,否则用户只看见"读到了 37 行"。

    `extra_rels`(v0.9.149)是**这一会话里已经被碰到的**那些子目录层 —— 由调用方
    (Agent)把自己那份会话状态传进来。为什么不在这里自己扫:第三级是"碰到才算",
    而 `instruction_layers` 是无状态的纯查询;它自己去递归扫全工作区,既会慢,
    又会报出一批"根本没被碰过、因此不在这一路上下文里"的层 —— 那一屏的全部意义
    就是"它到底读了没有",多报一层假的比少报一层坏得多。
    """
    out = []
    user = _read_instruction(app_path(USER_AGENTS_NAME))
    if user["exists"]:
        out.append({"scope": "用户级", "name": USER_AGENTS_NAME,
                    "lines": user["lines"], "chars": user["chars"],
                    "truncated": user["truncated"]})
    found, skipped = _combined_root_found(workspace)
    if found is not None:
        out.append({"scope": "项目级", "name": " + ".join(found["sources"]),
                    "sources": found["sources"],
                    "lines": found["lines"], "chars": found["chars"],
                    "truncated": found["truncated"],
                    "adopted": (permspec.agents_adopted_sha(workspace)
                                == found["sha"])})
    for item in skipped:
        # **看到了但没读的也要报。** 这一屏的全部意义是"它到底读了没有";把它们吞掉,
        # 用户就会以为"我明明在 `.cursor/rules/` 里写了这一条,它怎么不听"。
        out.append({"scope": "未读", "name": item["name"], "why": item["why"]})
    for rel in dict.fromkeys(extra_rels):        # 去重且保序
        if not rel:
            # 根那一层已经作为「项目级」报过了(它走的是系统提示词那条路,不是
            # 第三级的工具结果那条路)。不去重的话侧栏那一行会把同一份文件报两遍,
            # 而"报两遍"看起来就像"有两份规矩"。
            continue
        layer = layer_at(workspace, rel)
        if layer is None:
            continue
        out.append({"scope": "目录级", "rel": rel, "name": layer["path"].name,
                    "lines": layer["found"]["lines"], "chars": layer["found"]["chars"],
                    "truncated": layer["found"]["truncated"],
                    "adopted": layer_adopted(workspace, layer)})
    return out


def instructions_line(workspace, extra_rels=()) -> str:
    """一屏话:「内置 + 用户级(12 行)+ 项目级(5 行)」;一份都没有就空串。

    项目级还没点过头采纳时,后面缀一句「(未采纳)」—— 侧栏那一行不能让人以为
    它已经在起作用。

    **「未读」那些(j,v0.9.153)不占一个「(N 行)」格子**:它们没被读,报行数没有意义;
    压成一句尾注(名字 + 理由 + 条数),因为它们正是"我明明写了它怎么不听"的答案。
    """
    layers = instruction_layers(workspace) if not extra_rels else \
        instruction_layers(workspace, extra_rels)
    if not layers:
        return ""
    bits = []
    skipped = []
    for it in layers:
        if it["scope"] == "未读":
            skipped.append(it)
            continue
        tail = "、已截断" if it["truncated"] else ""
        if it["scope"] in ("项目级", "目录级") and not it.get("adopted", True):
            tail += "、未采纳"
        label = f"目录级 {it['rel']}/" if it["scope"] == "目录级" else it["scope"]
        bits.append(f"{label}({it['lines']} 行{tail})")
    line = ("内置 + " + " + ".join(bits)) if bits else ""
    if skipped:
        names = "、".join(it["name"] for it in skipped[:3])
        more = f" 等 {len(skipped)} 份" if len(skipped) > 3 else ""
        note = f"(另有没读的:{names}{more} —— {skipped[0]['why']})"
        line = f"{line} {note}".strip()
    return line


def user_agents_prompt_section() -> str:
    """用户级指令文件:`<本机数据目录>/AGENTS.md`(L1,v0.9.101)。

    存在才注入,不存在就什么都不做 —— **不创建、也不提示"你应该写一份"**,
    那是把工程习惯塞给非技术的同学。它是主人自己写的,换机器靠「我的数据」带走。
    """
    found = _read_instruction(app_path(USER_AGENTS_NAME))
    if not found["exists"]:
        return ""
    head = (f"# 工程规矩(用户级 {USER_AGENTS_NAME},你本人写的)\n"
            + INSTRUCTION_GUARD + "\n\n")
    return f"\n{head}{_instruction_body(found)}\n"


def agents_adoption(workspace) -> dict:
    """这份项目指令文件当下的采纳状态:`{present, adopted, sha, name, lines, chars}`。

    §四.2「首次采纳点头」把这件事拆成两半,而且两半各只有一处真相:
    **读没读到**在这里(照旧由 `_read_instruction` 报),**这台机器点没点头**在
    `permspec` 那本信任账里(按内容指纹记)。所以这一屏能说清"它在场但你还没采纳",
    而 `agents_prompt_section` 只认后者。
    """
    empty = {"present": False, "adopted": False, "sha": "", "name": "",
             "lines": 0, "chars": 0, "found": None, "sources": []}
    found, _skipped = _combined_root_found(workspace)
    if found is None:
        return empty
    return {"present": True,
            "adopted": permspec.agents_adopted_sha(workspace) == found["sha"],
            "sha": found["sha"], "name": " + ".join(found["sources"]),
            "lines": found["lines"], "chars": found["chars"], "found": found,
            "sources": found["sources"]}


def adopt_agents(workspace) -> str:
    """记下「这份项目指令我核对过、采纳了」,回它的指纹(没这份文件就回空串)。"""
    info = agents_adoption(workspace)
    if not info["present"]:
        return ""
    permspec.adopt_agents(workspace, info["sha"], info["lines"])
    return info["sha"]


def agents_prompt_section(workspace) -> str:
    """工作区根的 AGENTS.md 指令文件,自动进系统提示词(F1,v0.9.100)。

    Codex/ZCode/Claude Code 都有这个约定:仓库里放一份给 agent 的指令
    (项目约定、代码风格、禁区),agent 每轮自动读。这里是同一份约定的
    本地落法 —— **工作区根**的两个名字按序找,存在才注入;文件是用户
    手写的,原样注入、截断到 4000 字并写明,绝不替用户改写。子代理也带
    (它就是"这个项目的规矩",对派出去干活的那一路同样有效)。

    v0.9.127 起**还要求本机采纳过这一份内容**(§四.2 的点头):这份文件可能是
    别人 clone 带来的,而"文件里写了规矩"与"你同意它对你说话"是两件事。
    没采纳时这里返回空串 —— 不报错也不解释,那是 `/instructions` 与提问卡的活。
    """
    info = agents_adoption(workspace)
    if not info["adopted"]:
        return ""
    head = (f"# 项目指令(工作区 {info['name']},用户手写,优先级高于你的默认习惯)\n"
            + INSTRUCTION_GUARD + "\n\n")
    return f"\n{head}{_instruction_body(info['found'])}\n"


def instruction_signature(workspace) -> str:
    """指令文件与技能清单的**内容指纹**(D1,v0.9.102)。

    `_refresh_clock` 拿它判断"要不要重建提示词":跨天**或**指纹变了才重建
    —— 内容变了才破 KV Cache,没变照旧稳定命中(与"一天至多破一次"同一条
    口径的推广)。指纹只碰几个小文件的字节与技能名清单,每回合一次,便宜。

    v0.9.127 起这个指纹里**折进项目级的采纳状态**:点了头之后提示词的内容确实
    变了(那一段从没有变成有),所以指纹也该变 —— 否则采纳发生在会话中途时,
    `_refresh_clock` 会判"什么都没变"而不重建,那句点头就当没发生过。
    """
    import hashlib
    h = hashlib.sha1()
    try:
        h.update(app_path(USER_AGENTS_NAME).read_bytes())
    except OSError:
        h.update(b"<missing>")
    # **折的是合并后的正文**,不是"第一份文件的字节":根上现在可能有好几份来源
    # (`AGENTS.md` + `.cursorrules` + `.cursor/rules/*.mdc`),只折第一份的话,
    # 改 `.cursorrules` 不会触发重建 —— 表现是"我改了规矩它还是老样子"。
    combo, skipped = _combined_root_found(workspace)
    h.update((combo["sha"] if combo else "<none>").encode("ascii"))
    # 没读的那些也折进来:多出一条带 globs 的规则,报表那一屏要跟着变
    # (它报的是"看到了但没读",而"看到"这件事本身变了就该重建)。
    for item in skipped:
        h.update(("|skip:" + item["name"]).encode("utf-8"))
    h.update(b"|adopted" if agents_adoption(workspace)["adopted"] else b"|pending")
    try:
        names = "|".join(sorted(x.name for x in load_skills(skill_dirs(workspace))))
        h.update(names.encode("utf-8"))
    except Exception:
        h.update(b"<skills-unreadable>")
    return h.hexdigest()


def instructions_prompt_section(workspace) -> str:
    """两级指令文件拼成提示词尾部那两段:用户级在前、项目级在后(越近越靠后)。"""
    return user_agents_prompt_section() + agents_prompt_section(workspace)


GUIDE_STYLE_SECTION = """

# 回复风格:讲解模式(用户开启)
当前处于**讲解模式** —— 用户要的是学会,不是拿到结果。口径:
- 把活拆成小步骤,每步先讲「这一步在做什么、为什么」,再给一个可以照着试的最小示例;
- 用户自己动手的部分**留给他做**,他贴回你的成果你再看——不整段代做;
- 概念用学生能听懂的话解释,该给例子就给例子;
- 结束时给一句「下一步可以自己试什么」。
与安全边界无关,工具与确认门照常。"""


def build_system_prompt(workspace: Path, for_subagent: bool = False,
                        reply_style: str = "normal") -> str:
    template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (template
              .replace("{workspace}", str(workspace))
              .replace("{date}", time.strftime("%Y-%m-%d"))
              .replace("{os}", platform.platform()))
    skills = load_skills(skill_dirs(workspace))
    # 段序:**静态段在前、动态段在后**(v0.9.46)。动态段(有没有课表、这周记没记
    # 情绪、记忆条数…)任何一段出现或消失,其后的前缀全部变化,KV Cache 从那里
    # 失效 —— 全部排到尾部,静态段(skills/calendar/notices/browser)才能整段
    # 稳定命中缓存。每段自身已遵守"不带当前时间"的口径。
    #
    # for_subagent=True(v0.9.53):子代理是派出去干具体活的独立执行者,**裁掉
    # 个人数据段** —— 情绪日记是主对话的陪伴语境,长期记忆是主代理的偏好上下文,
    # 对"只产出报告"的子代理没有用处,却让每一路都背着主人的私事在工作区里跑
    # (批量只读模式一次四路)。课表/馆藏/校历保留:data 与 english 档真的会用。
    return (prompt + skills_prompt_section(skills)
            + calendar_prompt_section()
            + notices_prompt_section(workspace)
            + browser_prompt_section()
            + kb_prompt_section(kb_dirs(workspace))
            + schedule_prompt_section()
            + library_prompt_section(workspace)
            + library_guide_prompt_section(workspace)
            + (mood_prompt_section() if not for_subagent else "")
            + (memory_prompt_section(workspace) if not for_subagent else "")
            + instructions_prompt_section(workspace)
            + (GUIDE_STYLE_SECTION if reply_style == "guide" and not for_subagent else ""))


def _mcp_log(ui):
    """MCP 启动失败等信息:GUI 走事件推送,终端走 stderr。"""
    def log(text: str) -> None:
        push = getattr(ui, "push", None)
        if callable(push):
            try:
                push("info", text=text)
                return
            except Exception:
                pass
        print(f"[MCP] {text}", file=sys.stderr)
    return log


mcp_log = _mcp_log      # 公开别名(gui_bridge 复用同一套日志行为)


def build_runner_agent(config: Config, workspace: Path, ui, llm,
                       sessions_dir: Path | None = None,
                       mcp_tools: list | None = None,
                       sandbox=None) -> tuple[Agent, Session]:
    """造一个"会话运行器"(独立 Agent + Session),供后台并发任务使用。

    LLM 客户端、MCP 工具、沙箱状态都是**外部传入共享**的:
    - MCP 连接只建一次,新会话不该重复拉起子进程;
    - 沙箱是全局策略,所有会话看到同一份边界;
    - ui 则是**每个会话一个适配器**(把事件打上会话标记),所以走参数传。
    """
    ctx = ToolContext(workspace=workspace, config=config)
    ctx.sandbox = sandbox if sandbox is not None else \
        SandboxState(policy_from_raw(config.sandbox), workspace)
    ctx.ui = ui      # 工具可把图形指令推给界面(校园地图 map_* 工具用)
    ctx.llm = llm    # 记忆整理(memory action=dream)用它做一次性归纳
    registry = build_default_registry()
    registry.extend(mcp_tools or [])
    # 主/子代理共用一个回滚栈(v0.9.53):先建容器传给 task 工具,Agent 建好后
    # 把自己的栈替换成同一个对象 —— implement 子代理的写快照,主代理 /undo
    # 弹到的就是它,子代理改坏文件救得回来。
    undo_ref: list = []
    # 主代理的信箱入口同样先建容器(I,v0.9.154,与 `undo_ref` 同一条先例):
    # `task` 工具在**装配时**就要拿到它,而主 Agent 要到下面才建出来 ——
    # 建好之后再把 `queue_note` 放进去。子代理的 `notify_parent` 就是从这里取的。
    inbox_ref: dict = {}
    registry.append(build_task_tool(ctx, llm, ui, config, registry,
                                    parent_undo=undo_ref, parent_inbox=inbox_ref))
    session = Session(system_prompt=build_system_prompt(
        workspace, reply_style=getattr(config, "reply_style", "normal")),
                      log_dir=sessions_dir or SESSIONS_DIR,
                      workspace=workspace)
    ctx.session = session   # export_trace(v0.9.98):轨迹从活动会话现取,不翻留档文件
    agent = Agent(
        llm=llm,
        registry=registry,
        session=session,
        # workspace/tool_names 是给项目级白名单用的(v0.9.87):装载时就要按工作区
        # 找到 permissions.json 并核对信任,工具名用来拒掉指向不存在的工具的规则。
        gate=PermissionGate(config, sandbox=ctx.sandbox, workspace=workspace,
                             tool_names={t.name.rsplit("·", 1)[-1]
                                         for t in registry}),
        ui=ui,
        ctx=ctx,
    )
    agent._undo_stack = undo_ref
    # 子代理 → 主会话那条路现在通了(I,v0.9.154):放进容器的是**方法本身**,
    # 收不收得下、怎么加锁由 Agent 那一边说了算(这里只递,不判断)。
    inbox_ref["enqueue"] = agent.queue_note
    return agent, session


def create_agent(config: Config, workspace: Path, ui,
                 llm=None, sessions_dir: Path | None = None) -> tuple[Agent, Session]:
    llm = llm or LLMClient(config)
    # MCP 服务器:连不上的只在日志里提示,不阻塞应用启动
    mcp = connect_servers(parse_servers(config.mcp_servers), log=_mcp_log(ui))
    sandbox = SandboxState(policy_from_raw(config.sandbox), workspace)
    agent, session = build_runner_agent(
        config, workspace, ui, llm, sessions_dir=sessions_dir,
        mcp_tools=mcp.build_tools(), sandbox=sandbox)
    agent.mcp = mcp          # 退出时 close_all(),别留下孤儿进程
    return agent, session


def apply_retention(config: Config, ui, current: "Path | None" = None,
                    sessions_dir: Path = SESSIONS_DIR) -> list[str]:
    """启动时按 `config.session_keep_last` 删旧留档,并把「删了几个」说给界面。

    **为什么要有这一个函数**:v0.9.66 那一笔把 `session.prune_sessions()`、配置字段、
    README 与 `config.example.json` 里那句「启动时按时间删旧的」全写齐了,测试也写了 5 条,
    **唯独一个调用者都没写**(`git log -S prune_sessions` 只有那一笔)。函数本身是对的,
    所以每一条测试都绿,而真机上 `sessions/` 只会一直变多 —— 这是"文档写了、代码写了、
    没人接线"这一类里最难发现的一种,因为红的全是别人的想象,没有一处现实。

    收成一处有两个目的:①CLI 与 GUI 说同一句话,不各抄一份话术;②调用点从 0 变成
    "两个入口都调这一处",由 `tests/test_retention.py` 扫源码钉住 —— 扫的是**调用点存在**。

    `current` 是这次启动接着用的那份留档:它永远不进删除名单,哪怕时钟被手改过。
    """
    from minicode import checkpoints
    from minicode.session import prune_sessions
    removed = prune_sessions(sessions_dir, config.session_keep_last,
                             protect={current} if current else None)
    # 留档没了、前像还留着 = 同学课件原文无声地留在盘上。这一趟无条件跑,
    # 因为上面那件只在配了 session_keep_last 时才动。
    orphans = checkpoints.sweep_orphans(sessions_dir)
    if orphans and ui is not None:
        ui.info(f"顺手清掉 {orphans} 份「留档已不存在」的改动前像"
                f"({checkpoints.CKPT_DIR_NAME}/)—— 那些会话已经不在了。")
    if removed and ui is not None:
        ui.info(f"按 session_keep_last={config.session_keep_last} 删掉 {len(removed)} 份"
                f"旧会话留档({sessions_dir})—— 当前接着用的这份没动。")
    return removed
