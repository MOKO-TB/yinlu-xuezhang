"""子代理档位的**本地定义**:工作区 `agents/*.md` 可以往窄里改一档,绝不能扩权。

为什么要有这一层(§6 #50(d) 那笔对标账):档位此前是代码里写死的 5 档加 router 的
领域档 —— 用户想让"侦察"这一档按自己学校的口径输出,只能改源码。竞品两家都是
`agents/*.md` + frontmatter,这一层补的是同一个入口。

四条红线先定死再动手(与 §6 #49(3)「hooks 只从安装目录 config.json 读」同一条根:
**仓库里不能长出权限**):

1. **只能收窄**。`tools:` 只能是那一档本来就拿得到的工具的子集;`read_only:` 只允许
   由 false 改成 true(更严),不许 true→false(那是放权);`max_steps:` 只能比内置上限
   小。程序闸在 `subagents.sub_tool_registry()` 那一层核 —— 只有那里才知道注册表里有
   哪些工具,在装载时核等于对着名单猜。
2. **撞内置档名的定义整份丢掉**。允许覆盖就等于同一个名字两套权限,而"哪套生效"变成
   看装载顺序。要定制就起个新名字、`base:` 指向你想改的那一档。
3. **提权字段一律剥掉并说给用户听**(`permission_mode` / `allow_tools` / `model` 等)。
   这一条抄 ZCode 项目级 profile 的做法:它的 `permissionMode` 字段被运行时剥掉。
   档位要换模型有正路(`config.subagent_models`),权限档位归 `config.permission_mode`
   与确认框管,不由一个仓库里的 markdown 决定。
4. **丢掉与剥掉都不静默**。每一条都进 notes,派发那一次在 task 的回话里说一遍;
   用户写了 `read_only: false` 却被当成 true,必须有声音,不然是"配了不生效还以为生效"。

被定义出来的档**不参与 `auto` 路由**(它不在 `router.DOMAINS` 里,分类器自然选不到),
与 `audit`/`testaudit` 那两档同一个口径:角色要靠显式点名,不让模型猜。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minicode.skills import _csv_decl, _parse_frontmatter

PROFILE_DIR = "agents"
# 别的工具的**子代理档位**目录(v0.9.152,E「子代理外部目录兼容」)。
#
# **只认工作区里的那几个** —— 不去翻你本机的 `~/.claude/agents`、`~/.codex/agents`。
# 这一层的语义是"仓库自带的档位定义跟着仓库走";读工作区外面是另一笔账,与
# `resolve_path`/敏感路径那套围栏同一条:不能因为"这是配置文件"就读出去。
#
# **顺序即优先级**:`profile_dirs` 把本项目自己的 `agents/` 排在前面,这几个排在最后,
# 而装载规则是"同名先装载的那份赢" —— 也就是**你在这个项目里写的压过从别处带过来的**。
COMPAT_PROFILE_DIRS = (".cursor/agents", ".claude/agents", ".codex/agents")
# 这些键出现在定义文件里一律剥掉(红线 3)。名单一次列全比逐条争论"这个算不算提权"
# 便宜:它们各自的正路都在 config.json 里,不需要 markdown 来开这条口子。
PRIVILEGED_KEYS = ("permission_mode", "permissionmode", "permission", "auto_approve",
                   "yolo", "allow_tools", "deny_tools", "sandbox", "model",
                   "provider", "api_key", "max_steps_hard")
# 子代理本来就被摘掉的三只(见 subagents.sub_tool_registry 的 docstring):
# 定义文件写不写都拿不到,与其让它生效不如说清"这三只不在任何档手里"。
NEVER_CHILD_TOOLS = ("task", "ask_user", "export_trace")


@dataclass(frozen=True)
class Profile:
    """一份被接受的档位定义(还没核对过工具宇宙 —— 那一步在派发时做)。"""
    name: str
    base: str
    label: str
    description: str
    tools: tuple
    read_only: bool | None          # None = 没写,照 base
    steps: int                      # 0 = 没写
    extra_prompt: str
    source: str


def profile_dirs(workspace) -> list[Path]:
    """先应用数据目录(没有工作区时的落点),再工作区;同名先装载的那份赢。

    工作区那一段是**两截**:本项目自己的 `agents/` 在前,别的工具的那几个
    (`COMPAT_PROFILE_DIRS`)在后 —— 后者优先级天然更低,因为"同名先装载的赢"。
    """
    dirs: list[Path] = []
    try:
        from minicode.paths import app_path
        dirs.append(app_path(PROFILE_DIR))
    except Exception:
        pass
    ws = str(workspace or "").strip()
    if ws:
        dirs.append(Path(ws) / PROFILE_DIR)
        dirs.extend(Path(ws) / d for d in COMPAT_PROFILE_DIRS)
    return dirs


def _compat_origin(workspace, d: Path) -> str:
    """`d` 是不是别的工具的子代理目录;是就回它的相对名(如 `.cursor/agents`),否则空串。

    只用来**说话**(认到一份就要讲清它是从哪儿认来的),不参与任何判定 ——
    判定只看"哪一份先装载"。
    """
    ws = str(workspace or "").strip()
    if not ws:
        return ""
    try:
        rel = d.resolve().relative_to(Path(ws).resolve())
    except (ValueError, OSError):
        return ""
    text = rel.as_posix()
    return text if text in COMPAT_PROFILE_DIRS else ""


def _as_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "是"):
        return True
    if text in ("false", "0", "no", "否"):
        return False
    return "bad"


def _as_steps(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return -1
    return n if n > 0 else -1


def load(workspace=None, bases: dict | None = None, step_cap: int = 0) -> tuple[dict, list]:
    """读定义文件。返回 ({档名: Profile}, 说给用户听的那些话)。

    `bases` 是内置档与领域档的规格表(由 subagents 传进来 —— 这里不 import 它,
    否则循环导入)。`step_cap` 是内置步数上限,写了比它大的会被夹回来。
    """
    bases = bases or {}
    cap = int(step_cap or 0)
    profiles: dict[str, Profile] = {}
    notes: list[str] = []
    for d in profile_dirs(workspace):
        if not d.is_dir():
            continue
        origin = _compat_origin(workspace, d)
        for entry in sorted(d.iterdir()):
            if entry.suffix.lower() not in (".md", ".markdown"):
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                notes.append("%s 读不出来(%s),这一档没装" % (entry.name, type(e).__name__))
                continue
            meta, body = _parse_frontmatter(text)
            name = str(meta.get("name") or entry.stem).strip()
            base = str(meta.get("base") or "").strip()
            if not name or "·" in name or name == "auto":
                notes.append("%s:档名 %r 不能用(空、含「·」或就叫 auto),整份丢掉"
                             % (entry.name, name or "(空)"))
                continue
            if name in bases:
                notes.append("%s:%s 与内置档同名 —— 定义文件不许覆盖内置档(同名两套权限,"
                             "谁生效变成看装载顺序),整份丢掉。要改它就写 base: %s 起个新名字。"
                             % (entry.name, name, name))
                continue
            if name in profiles:
                notes.append("%s:%s 已经有另一份定义了(先装载的那份赢),这一份丢掉"
                             % (entry.name, name))
                continue
            if not base:
                notes.append("%s:%s 没写 base:(要收窄哪一档?),整份丢掉 —— "
                             "空 base 等于凭空造一档,那一档拿得到什么工具没人说得清"
                             % (entry.name, name))
                continue
            spec = bases.get(base)
            if spec is None:
                notes.append("%s:base: %s 不存在(内置档与领域档里都没有这一档),丢掉"
                             % (entry.name, base))
                continue
            stripped = [k for k in list(meta) if str(k).lower() in PRIVILEGED_KEYS]
            for k in stripped:
                meta.pop(k, None)
            if stripped:
                notes.append("%s:剥掉了 %s —— 权限与模型不由仓库里的 markdown 决定"
                             "(权限档位看 config.permission_mode,换档模型写 "
                             "config.subagent_models)。**没有剥掉就一定没生效**,别按它们改行为。"
                             % (entry.name, "、".join(sorted(stripped))))
            read_only = _as_bool(meta.get("read_only"))
            if read_only == "bad":
                notes.append("%s:read_only 写的不是 true/false,按 %s 那一档原样处理"
                             % (entry.name, base))
                read_only = None
            elif read_only is False and getattr(spec, "read_only", False):
                notes.append("%s:想 %s 但 base: %s 本来是只读档 —— 只允许往严里改,"
                             "所以仍按只读跑(放开那一档就是扩权)" % (entry.name, "改成可写", base))
                read_only = True
            steps = _as_steps(meta.get("max_steps"))
            if steps < 0:
                steps = 0
            elif cap and steps > cap:
                notes.append("%s:max_steps %d 比内置上限 %d 还大,夹回 %d —— 步数只能往下收"
                             % (entry.name, steps, cap, cap))
                steps = cap
            tools = tuple(t for t in _csv_decl(meta.get("tools")) if t)
            blocked = [t for t in tools if t in NEVER_CHILD_TOOLS]
            if blocked:
                notes.append("%s:%s 这一档本来就摘不掉(子代理没有递归派活/没有可以问的人/"
                             "轨迹是父会话的),写进 tools 也不会有"
                             % (entry.name, "、".join(blocked)))
                tools = tuple(t for t in tools if t not in NEVER_CHILD_TOOLS)
            profiles[name] = Profile(
                name=name, base=base,
                label=str(meta.get("label") or ""),
                description=str(meta.get("description") or ""),
                tools=tools, read_only=read_only, steps=steps,
                extra_prompt=body.strip(), source=str(entry))
            if origin:
                # **认到了别人的档位要说一声。** 不说的话,用户看到自己多了一档
                # 会以为"我没定义过它,怎么冒出来的" —— 而这一层最容易让人困惑的
                # 正是这件事。同时讲清优先级:本项目自己那份压过它。
                notes.append("%s:%s 是从 `%s/` 认来的(别的工具的子代理目录),"
                             "不是你在这个项目里写的 —— 同名时本项目自己的 `%s/` 压过它"
                             % (entry.name, name, origin, PROFILE_DIR))
    return profiles, notes
