"""项目级权限规则(§6 第 4 条「项目级白名单」起步,v0.9.116 补上 ask/deny)。

用户在项目根放一份 `permissions.json`,把"每次都要问一遍"的动作预先批下来,
也可以反过来写"这条必须问我"或"这条不许做"。

**它能替换的只有档位那一次确认** —— 红线链一条都不越过:灾难命令硬拒、敏感路径、
工作区外路径、凭据文件、脚本内容扫描、外部内容升级门,全都在它前面就已经 return。
反过来它只会**收紧**,不会放松:`deny` 比红线更严时照旧拒,`ask` 把免确认档拉回来问。

四件事必须先讲清楚,因为**这份文件本身就是一个攻击面**:

1. **写了不等于生效,核对过才生效**。批准记在本机的用户级 `perms-trust.json`
   里(按工作区绝对路径记那份文件的 sha256)。文件内容改一个字节,哈希就对不上,
   整份规则立刻失效、回到逐次确认。要的是这两条路都走不通:
   ①clone 别人的仓库,里面正好带了一份 `permissions.json`(作者批的权限不该是你的,
   作者写的 deny 规则也不该能瘫痪你的工作流);②网页/文件里的注入内容鼓动模型自己
   `write_file` 一份来给自己发通行证。两者与"用户自己想批权限"在文件层面
   **长得一模一样**,所以不能靠文件内容自证,只能靠"用户在会话之外另外点一次头"。
2. **不预授权红线**:规则里出现绝对路径、`..` 上跳、敏感目录名,装载时就被拒掉并
   说明原因 —— 而不是留到运行时看红线是否挡得住。这条对 `deny`/`ask` 同样适用:
   那些路径**本来就进不去**,写一条拒绝只会让人以为"是这条规则拦的"。
3. **可预授权的命令首词只限构建类**(`SAFE_COMMAND_HEADS`),这一条**只管 allow**:
   预授权 rm 会开后门,而**拒绝/加严 rm 不会**。deny/ask 的规则不限首词。
4. **生效时留痕**:每一次按规则放行、加严、拒绝都往安全日志里记一行(与注入防御
   `_log_security_ask` 同一份日志),界面上也推一条 info。静默放行等于没有审计。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from minicode.paths import app_path
from minicode.sandbox import SENSITIVE_DIRS

PERMISSIONS_FILE = "permissions.json"
TRUST_FILE = "perms-trust.json"
MAX_RULES = 50                      # 一份项目文件里最多收多少条(超了截断并说明)
# 一条规则能声明的决定。数组名仍叫 `allow`(历史形状,老文件零改动),条目里用
# `decision` 说要问还是要拒 —— 不改数组名是有意的:那会让 README/SECURITY/
# config.example/《使用说明》四处口径一起漂,而漂掉的权限文档比难听的字段名危险。
DECISION_ALLOW, DECISION_ASK, DECISION_DENY = "allow", "ask", "deny"
DECISIONS = (DECISION_ALLOW, DECISION_ASK, DECISION_DENY)

# 规则的**出处**。这两个值不是装饰:`hit()` 报给用户的每一句"按哪条办的"都必须说清
# 它来自项目文件还是本机那本账 —— 前者是"这个仓库的作者批的"(所以要有 sha256
# 核对才生效),后者是"你自己在确认框上点过头"。混成一个来源就等于把两种授权
# 说成同一种,而那正是「clone 来的仓库替这台机器决定什么不用问」的入口。
ORIGIN_PROJECT, ORIGIN_GRANTED = "project", "granted"

# 「以后都允许」落的那一格(v0.9.133,P0-2)。它住在**本机信任表的同一行**里,
# 与白名单的 sha256 并排 —— 因为它们是同一件事的两面:"这一类动作不用再问我"。
# 上限比项目文件小:`granted` 是点出来的,不是写出来的,攒到几十条没人还记得它拦过什么。
GRANTED_KEY = "granted"
MAX_GRANTED = 20

# 能被预先批下来的命令首词:**与 accept_edits 档同一份名单**(permission.py 引用这里,
# 不在两处各抄一遍)。为什么只能在这一份上收口:白名单若允许 `command_prefix: "rm"`,
# 而灾难黑名单又认不全 `rm` 的各种写法(实测 `rm -rf C:\` 就漏过),等于用户批一次
# 之后破坏性删除从此不问 —— 预授权只该覆盖"可重复执行、不直接毁数据"那类动作。
SAFE_COMMAND_HEADS = frozenset({
    "python", "python3", "py", "pytest", "pip", "pip3",
    "node", "npm", "npx", "yarn", "pnpm",
    "make", "cargo", "go",
})                      # 一份项目文件里最多收多少条(超了截断并说明)

# 一条规则能带的键:三选一,且必须与 tool 搭配
_PATH_KEYS = ("path", "path_prefix")
_COMMAND_KEYS = ("command_prefix",)


def command_head(cmd: str) -> str:
    """取一条命令的"首词":去引号、去 .exe、只留 basename。

    `PermissionGate._is_workspace_build_command` 与本模块的 command_prefix 匹配
    **共用这一份口径** —— 两处各写一遍的话,总有一天会一处认得 `pytest` 另一处不认。
    """
    body = str(cmd or "").strip()
    if not body:
        return ""
    head = re.split(r"[\s|;&]", body, 1)[0].strip("\"'").lower()
    if head.endswith(".exe"):
        head = head[:-4]
    return head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _norm_rel(path: Any) -> str:
    """把工具给的 path 归一成"工作区相对 + 正斜杠 + 小写"的形态;拿不到就返回绝对/异常标记。

    返回以 "/" 开头(绝对路径)、含 ".." 段、或带盘符的,一律当成"不是工作区内相对路径",
    由调用方判不匹配 —— 白名单不参与越界判断,越界该由沙箱那条 ASK 管(它在前面)。
    """
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.startswith("/") or re.match(r"^[a-zA-Z]:", raw) or ".." in [
            seg for seg in raw.split("/")]:
        return "/" + raw                       # 标记:不可比对(绝对/上跳)
    return raw.lstrip("./").lower()


def _targets_redline(pattern: str) -> str:
    """规则本身是否想预授权红线路径(绝对路径 / 上跳 / 敏感目录)。返回拒绝原因或 ""。"""
    norm = pattern.replace("\\", "/").lower()
    if norm.startswith("/") or re.match(r"^[a-zA-Z]:", norm):
        return "是绝对路径(工作区外的路径不能预先批,只能当场确认)"
    if any(seg == ".." for seg in norm.split("/")):
        return "含 .. 上跳(同上,越界路径必须当场确认)"
    for seg in norm.split("/"):
        if seg in {s.lower() for s in SENSITIVE_DIRS}:
            return f"指向敏感目录 {seg}(凭据/密钥那一类永不预授权)"
    return ""


@dataclass(frozen=True)
class Rule:
    tool: str
    kind: str                 # "path" | "path_prefix" | "command_prefix"
    pattern: str              # 已归一(路径类为小写相对 posix;命令类为首词)
    index: int                # 第几条(给人指认用)
    decision: str = DECISION_ALLOW     # allow=免确认;ask=必须问;deny=直接拒
    origin: str = ORIGIN_PROJECT       # 项目文件里的,还是本机点过头记住的(见上面那两个常量)

    def matches(self, name: str, args: dict) -> bool:
        if self.tool != name:
            return False
        if self.kind == "command_prefix":
            return command_head(args.get("command", "")) == self.pattern
        target = _norm_rel(args.get("path"))
        if not target or target.startswith("/"):
            return False
        if self.kind == "path":
            return target == self.pattern
        return target.startswith(self.pattern)

    def source(self) -> str:
        """这一条是从哪本账上读来的 —— 说给用户听的每一句都必须带它。"""
        if self.origin == ORIGIN_GRANTED:
            return f"本机记住的第 {self.index} 条"
        return f"{PERMISSIONS_FILE} 第 {self.index} 条"

    def to_raw(self) -> dict:
        """存回本机那本账的形状 —— **与 permissions.json 里一条规则同一种线格式**。

        这样存是有道理的,不是省事:装载那一路要拿 `parse_rules` 再核一遍(红线路径、
        可预授权首词、工具名存不存在),而它只认这一种形状。换成 `{"kind":...,
        "pattern":...}` 就得再写一个校验器,两把尺子总有一天量出两个数。
        """
        return {"tool": self.tool, self.kind: self.pattern,
                "decision": self.decision}

    def describe(self) -> str:
        subject = (f"以 `{self.pattern}` 开头的命令" if self.kind == "command_prefix"
                   else (f"路径 `{self.pattern}`" if self.kind == "path"
                         else f"`{self.pattern}` 目录下的文件"))
        return {DECISION_DENY: f"拒绝动 {subject}",
                DECISION_ASK: f"{subject} 必须当面确认"}[self.decision] \
            if self.decision != DECISION_ALLOW else f"允许对 {subject} 的操作"


@dataclass
class Spec:
    """一个工作区的规则装载结果(含没生效时该说的原因)。"""

    workspace: Path
    sha: str = ""
    rules: list[Rule] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    trusted: bool = False
    reason: str = ""          # 为什么没生效(给人看的一句人话)
    # 本机点过头记住的那几条(v0.9.133)。与 `rules` 分开放,是因为它们的**生效条件
    # 不同**:项目文件那一份要 sha256 核对过才算数,这一份本来就是这台机器自己的决定,
    # 项目文件在不在、改没改都跟它无关。
    granted: list[Rule] = field(default_factory=list)

    @property
    def effective(self) -> bool:
        return bool(self.rules) and self.trusted

    def hit(self, name: str, args: dict, decision: str) -> str:
        """命中某一种决定就返回"按第几条办的人话",否则空串。

        三种决定**分头扫**,不是"第一条匹配的用它的决定":同一条路径上若既写了
        allow 又写了 deny,后者才是用户该得到的结果 —— 拒绝优先,与 Claude/Codex
        的 `deny > ask > allow` 同一条尺,也和红线排在一切放行之前同构。

        两本账的扫描顺序是"项目文件(核对过才扫)→ 本机记住的",两边报的都是自己的
        出处,不混着数。
        """
        pools = [self.rules] if self.trusted else []
        pools.append(self.granted)
        for pool in pools:
            for rule in pool:
                if rule.decision == decision and rule.matches(name, args):
                    return f"{rule.source()}:{rule.describe()}"
        return ""

    def decide(self, name: str, args: dict) -> str:
        """命中 allow 规则(免确认放行)时的人话,否则空串。"""
        return self.hit(name, args, DECISION_ALLOW)

    def summary(self) -> str:
        """清单里各类规则各有几条(给 `permissions` 那一屏用,别再数两遍)。"""
        if not self.rules:
            return "没有规则"
        n = {d: sum(1 for r in self.rules if r.decision == d) for d in DECISIONS}
        bits = [f"{v} 条 {k}" for k, v in n.items() if v]
        return "、".join(bits)



def file_path(workspace: Path) -> Path:
    return Path(workspace) / PERMISSIONS_FILE


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 信任记录(本机)

def trust_path() -> Path:
    return app_path(TRUST_FILE)


def _read_trust() -> dict:
    p = trust_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_trust(table: dict) -> None:
    p = trust_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)                  # 原子替换:半截文件不能被读成"没批准过"


def approved_sha(workspace: Path) -> str:
    key = str(Path(workspace).resolve())
    row = _read_trust().get(key)
    if isinstance(row, dict):
        return str(row.get("sha256") or "")
    return str(row or "")           # 兼容手写坏格式:不是 dict 就当没记


def _row(workspace) -> dict:
    """这个工作区在本机信任表里的那一行;没记过就是空行。"""
    row = _read_trust().get(str(Path(workspace).resolve()))
    return dict(row) if isinstance(row, dict) else {}


def _put_row(workspace, row: dict) -> None:
    table = _read_trust()
    table[str(Path(workspace).resolve())] = row
    _write_trust(table)


def trust(workspace: Path, sha: str, rules_n: int = 0) -> None:
    # **合并**而不是整行盖掉:同一行还记着"这个项目那份 AGENTS.md 采纳了没有"
    # (§四.2 的点头)。一行两件事,是因为它们本来就是同一件事的两面 ——
    # "别人写进这个目录的文件,你核对过才让它对你说话"。
    row = _row(workspace)
    row.update({"sha256": sha, "rules": rules_n, "approved_at": _now()})
    _put_row(workspace, row)


AGENTS_LAYERS_KEY = "agents_layers"   # 子目录那一层一层的采纳记录(v0.9.149)


def agents_adopted_sha(workspace, rel: str = "") -> str:
    """本机为这个项目采纳过的那份指令文件的内容指纹(没采纳过 = 空串)。

    `rel` 空串 = **工作区根**那一份,记录照旧住在 `agents_sha256` 那几个键上
    (v0.9.127 起就在那儿,不动它 —— 老留档里已经点过头的项目不该因为这一版
    又被问一遍)。`rel` 非空 = 某个子目录那一层,住在 `agents_layers` 这张表里,
    **一层一条**。

    为什么根那一层不一起搬进新表:搬了就要写迁移代码去读老键,而读老键那条路
    一旦漏一处,表现是"我明明采纳过,它又问我一遍"——那是最招人烦的坏法。
    两个家、各一处真相,比一个家加一段迁移代码干净。
    """
    row = _row(workspace)
    if not rel:
        return str(row.get("agents_sha256") or "")
    layers = row.get(AGENTS_LAYERS_KEY)
    one = (layers or {}).get(str(rel)) if isinstance(layers, dict) else None
    return str((one or {}).get("sha") or "")


def adopt_agents(workspace, sha: str, lines: int = 0, rel: str = "") -> None:
    """记一次「这份项目指令我核对过、采纳」—— 同样只认内容指纹。

    文件改一个字,指纹就变,于是又回到"没采纳"的状态(与上面 `trust` 那条
    "改了要重新批"是同一条道理,不是两条规则)。

    **`rel` 非空时只动那一层**,别的层一个字都不碰 —— 一层一次点头,这是
    指令文件第三级在安全上唯一比两级更严的地方(别人 clone 一个仓库就等于把它
    自带的每一份 AGENTS.md 都递到你面前,不能拿一次点头全收了)。
    """
    row = _row(workspace)
    if not rel:
        row.update({"agents_sha256": sha, "agents_lines": int(lines or 0),
                    "agents_at": _now()})
    else:
        layers = row.get(AGENTS_LAYERS_KEY)
        layers = dict(layers) if isinstance(layers, dict) else {}
        layers[str(rel)] = {"sha": sha, "lines": int(lines or 0), "at": _now()}
        row.update({AGENTS_LAYERS_KEY: layers})
    _put_row(workspace, row)


def agents_adopted_layers(workspace) -> dict:
    """这张表当下的原始形状(键 = 相对目录,值 = {sha, lines, at})。

    `instruction_layers` 要按它判"这一层采纳了没有";回的是**拷贝**,
    调用方改它不该改到磁盘上那份。
    """
    layers = _row(workspace).get(AGENTS_LAYERS_KEY)
    return dict(layers) if isinstance(layers, dict) else {}


def granted_raw(workspace) -> list:
    """本机记住的那几条的原始形状(不是 `Rule`,装载时才校验)。"""
    raw = _row(workspace).get(GRANTED_KEY)
    return list(raw) if isinstance(raw, list) else []


def granted_rules(workspace, known_tools: set[str] | None = None
                  ) -> "tuple[list, list]":
    """把本机那本账读成规则清单。**每条都要再过一遍 `parse_rules`**。

    为什么不信任自己写进去的数据:这份文件是本机可读可写的 JSON,用户(或另一个
    会话)改过一字之后,红线路径、`rm` 这种首词、已经不存在的工具名都可能出现在里面。
    一次装载就是一次重新核对 —— 认不了的丢掉并说明,与项目文件那一路同一口径。
    """
    rules, problems = parse_rules(granted_raw(workspace), set(known_tools or ()))
    return [replace(r, origin=ORIGIN_GRANTED) for r in rules], problems


def granted_candidate(name: str, args: dict) -> "tuple[dict, str]":
    """这一次调用能不能收窄成一条本机规则。返回 (候选规则, 为什么不能)。

    **只收窄不放宽**从这里长出来:路径记的是**整份文件**(`path` 而不是 `path_prefix`,
    所以「允许改 notes.txt」永远不会变成「允许改这个目录下的任何东西」);命令记**首词**,
    而首词能不能预授权由 `SAFE_COMMAND_HEADS` 那份名单说了算(在 `parse_rules` 里核)。
    想批一整个目录,得自己去写 `permissions.json` 并按内容指纹核对 —— 那是"别人写的
    授权"该有的门槛,不是点一次头就能挣到的。
    """
    args = args or {}
    bare = str(name or "").rsplit("·", 1)[-1]
    if bare in ("write_file", "edit_file"):
        raw = str(args.get("path") or "").strip()
        if not raw:
            return {}, "这一步没有路径可记"
        return ({"tool": bare, "path": raw, "decision": DECISION_ALLOW}, "")
    if bare == "bash":
        head = command_head(args.get("command", ""))
        if not head:
            return {}, "这条命令取不出首词,没什么可记的"
        return ({"tool": "bash", "command_prefix": head,
                 "decision": DECISION_ALLOW}, "")
    return {}, (f"「{bare}」这一步动的不是路径也不是一条命令,收窄不成一条可复用的规则"
                "(能记住的只有:改哪一个文件、跑哪一类命令)")


def granted_rule(name: str, args: dict, known_tools: set[str] | None = None
                 ) -> "tuple[Rule | None, str]":
    """这一次调用能不能收窄成一条规则 —— **判定只在这里一份**。

    确认框那颗按钮出不出现(`PermissionGate.can_remember`)与真往下写
    (`remember`)必须问同一个函数,否则总有一天出现"按钮给了,点了却说记不成"。
    """
    cand, why = granted_candidate(name, args)
    if not cand:
        return None, why
    rules, problems = parse_rules([cand], set(known_tools or ()))
    if not rules:
        return None, (problems[0] if problems else "这条规则不成立")
    return rules[0], ""


def remember(workspace, name: str, args: dict,
             known_tools: set[str] | None = None) -> "tuple[bool, str]":
    """把这一次点头落成一条本机免确认规则。返回 (成没成, 说给用户的那句话)。

    这句话要**原样推给界面**:记成了什么、为什么记不成,都不能只躺在日志里。
    """
    ws = Path(workspace)
    new, why = granted_rule(name, args, known_tools)
    if new is None:
        return False, why
    existing, _ = granted_rules(ws, known_tools)
    for got in existing:
        if (got.tool, got.kind, got.pattern, got.decision) == \
                (new.tool, new.kind, new.pattern, new.decision):
            return True, f"本来就记住了:{got.source()},{got.describe()}"
    if len(existing) >= MAX_GRANTED:
        return False, (f"本机记住的免确认动作已经 {len(existing)} 条(上限 "
                       f"{MAX_GRANTED}),这一次没往里加。要腾地方请先撤销 —— "
                       "不该由学长替你挑哪一条该删。")
    row = _row(ws)
    stored = row.get(GRANTED_KEY)
    items = list(stored) if isinstance(stored, list) else []
    items.append(new.to_raw())
    row[GRANTED_KEY] = items
    _put_row(ws, row)
    return True, (f"已记住(只在这台机器、只对这个项目):{new.describe()}"
                  f" —— 现在共 {len(items)} 条。不想要了就撤销(permissions_trust "
                  "action=revoke 会把它们一起撤掉)")


def revoke(workspace: Path) -> bool:
    """撤掉这个项目**白名单那半截**的信任。

    同一行还记着指令文件的采纳(§四.2,v0.9.127),它不能被"撤销白名单"顺手带走
    —— 那是两次独立的点头。整行只在这两半都没了之后才删。
    """
    key = str(Path(workspace).resolve())
    table = _read_trust()
    row = table.get(key)
    if key not in table:
        return False
    if not isinstance(row, dict):        # 手写坏格式的那条:本来就什么都没能生效
        table.pop(key)
        _write_trust(table)
        return True
    fields = ("sha256", "rules", "approved_at", GRANTED_KEY)
    if not any(f in row for f in fields):
        return False                     # 这一行只有采纳记录:撤销没做成任何事
    for f in fields:
        row.pop(f, None)
    if row:
        table[key] = row
    else:
        table.pop(key, None)
    _write_trust(table)
    return True


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------ 装载

def parse_rules(raw: Any, known_tools: set[str]) -> tuple[list[Rule], list[str]]:
    """校验规则清单。认不了的规则**丢掉并说明**,不猜它想干什么。"""
    problems: list[str] = []
    rules: list[Rule] = []
    if not isinstance(raw, list):
        return [], ["allow 必须是一个数组"]
    for i, item in enumerate(raw[:MAX_RULES], start=1):
        if not isinstance(item, dict):
            problems.append(f"第 {i} 条不是对象,已忽略")
            continue
        tool = str(item.get("tool") or "").strip()
        if not tool:
            problems.append(f"第 {i} 条缺 tool,已忽略")
            continue
        bare = tool.rsplit("·", 1)[-1]        # 允许写档位前缀的写法,按末段判断
        if known_tools and bare not in known_tools:
            problems.append(f"第 {i} 条指向不存在的工具 {tool},已忽略")
            continue
        keys = [k for k in (*_PATH_KEYS, *_COMMAND_KEYS) if item.get(k)]
        if len(keys) != 1:
            problems.append(f"第 {i} 条要在 {', '.join((*_PATH_KEYS, *_COMMAND_KEYS))} "
                            "里恰好给一个,已忽略")
            continue
        key = keys[0]
        pattern = str(item[key]).strip()
        # decision:缺省 allow(老文件一条都不用改);认不了的值**丢这条并说明**,
        # 不猜它想干什么 —— 把 "deny" 打成 "denay" 然后悄悄当 allow 放行,是这类
        # 字段最贵的一种坏法。
        decision = str(item.get("decision") or DECISION_ALLOW).strip().lower()
        if decision not in DECISIONS:
            problems.append(f"第 {i} 条的 decision {item.get('decision')!r} 不认识,"
                            f"只认 {'、'.join(DECISIONS)},已忽略这条")
            continue
        if key in _PATH_KEYS:
            why = _targets_redline(pattern)
            if why:
                problems.append(f"第 {i} 条被拒:模式 {pattern!r} {why}"
                                + ("(红线本来就拦住,这条写了也不会是它在拦)"
                                   if decision != DECISION_ALLOW else ""))
                continue
            norm = _norm_rel(pattern).rstrip("/") or _norm_rel(pattern)
            if not norm or norm.startswith("/"):
                problems.append(f"第 {i} 条模式 {pattern!r} 归一后是空的,已忽略")
                continue
            rules.append(Rule(bare, key, norm, i, decision))
        else:
            head = command_head(pattern)
            if not head:
                problems.append(f"第 {i} 条的 command_prefix {pattern!r} 取不出首词,已忽略")
                continue
            # 首词白名单**只管 allow**:预授权 rm 会开后门,拒绝/加严 rm 不会。
            if head not in SAFE_COMMAND_HEADS and decision == DECISION_ALLOW:
                problems.append(f"第 {i} 条被拒:{pattern!r} 不是可预授权的构建类命令"
                                f"(只认 {'、'.join(sorted(SAFE_COMMAND_HEADS))} 这类首词;"
                                "删除/强制类命令不许预先批,必须当场确认。"
                                "想拒绝它请给这条加 \"decision\": \"deny\",那条不限首词)")
                continue
            rules.append(Rule(bare, "command_prefix", head, i, decision))
    if len(raw) > MAX_RULES:
        problems.append(f"只取前 {MAX_RULES} 条(文件里写了 {len(raw)} 条)")
    return rules, problems


def load(workspace: Path | None, known_tools: set[str] | None = None) -> Spec:
    """读这台机器关于这个项目的两本账:项目文件的白名单 + 点过头记住的免确认。

    分成两本是刻意的(v0.9.133):前者要 sha256 核对过才说话(它是**仓库作者**写的),
    后者本来就是**这台机器自己**在确认框上点的头,项目文件在不在都跟它无关。
    """
    spec = _load_project_file(workspace, known_tools)
    if workspace:
        rules, problems = granted_rules(Path(workspace), known_tools)
        spec.granted = rules
        if problems:
            # 加在前面:`permissions` 那一屏最先读到的就该是"有东西被拒了",
            # 而不是项目文件那份(它可能一条问题都没有)。
            spec.problems = [f"本机记住的:{p}" for p in problems] + list(spec.problems)
    return spec


def _load_project_file(workspace: Path | None,
                       known_tools: set[str] | None = None) -> Spec:
    """读工作区的 permissions.json + 本机信任记录,得出"这份白名单现在生效吗"。"""
    if not workspace:
        return Spec(workspace=Path("."), reason="没有工作区,白名单不参与判断")
    ws = Path(workspace)
    spec = Spec(workspace=ws)
    p = file_path(ws)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        spec.reason = f"这个项目没有 {PERMISSIONS_FILE}"
        return spec
    except OSError as e:
        spec.reason = f"{PERMISSIONS_FILE} 读不到({type(e).__name__})"
        return spec
    spec.sha = _sha(text)
    try:
        data = json.loads(text)
    except ValueError as e:
        spec.problems = [f"不是合法 JSON({e}) —— 整份不生效"]
        spec.reason = f"{PERMISSIONS_FILE} 解析失败"
        return spec
    raw_rules = data.get("allow") if isinstance(data, dict) else None
    spec.rules, spec.problems = parse_rules(raw_rules, set(known_tools or ()))
    if not spec.rules:
        spec.reason = "白名单里没有可用规则"
        return spec
    if approved_sha(ws) != spec.sha:
        got = approved_sha(ws)
        spec.trusted = False
        spec.reason = ("没核对批准过" if not got else
                       "批准之后文件又改过了(哈希对不上)")
        return spec
    spec.trusted = True
    spec.reason = ""
    return spec


# ---------------------------------------------------------------- 工具的两只眼与一只手

def _known_tools() -> set[str]:
    """注册表里的工具名(运行期取,避开 import 环)。"""
    try:
        from minicode.tools.registry import build_default_registry
        return {t.name.rsplit("·", 1)[-1] for t in build_default_registry()}
    except Exception:
        return set()                 # 取不到就当"没核对过工具名",规则照常装载


def _spec_of(ctx: Any) -> Spec:
    ws = getattr(ctx, "workspace", None)
    return load(Path(ws) if ws else None, _known_tools())


def _digest_lines(spec: Spec) -> str:
    lines = [f"· 项目白名单:{len(spec.rules)} 条({spec.summary()})"
             + ("(已生效 —— 命中 allow 就免确认)" if spec.effective else
                f"(未生效:{spec.reason or '原因未报'})")]
    if spec.sha:
        lines.append(f"· 文件指纹 sha256 {spec.sha[:16]}…"
                     " —— 批准时记的就是这个,改一个字整份规则失效")
    # 两本账分开报,是因为"谁批的"不一样:项目文件那一半要核对内容指纹(它是仓库作者
    # 写的),本机记住的那一半是用户自己在确认框上点的头。混成一个数会让人以为
    # 「clone 一份仓库就能带上免确认动作」—— 那正是这一路要堵的死。
    if spec.granted:
        lines.append(f"· 本机记住的:{len(spec.granted)} 条(点过「以后都允许」,"
                     "换台机器就没了,与这个项目文件无关)")
        lines += [f"    {r.index}. {r.tool} —— {r.describe()}" for r in spec.granted]
    for p in spec.problems:
        lines.append(f"· 装载时拒掉:{p}")
    if spec.rules:
        lines.append("· 清单:")
        lines += [f"    {r.index}. {r.tool} —— {r.describe()}" for r in spec.rules]
    lines.append("· 边界:allow 只替换「权限档位本该问一次」那一步;敏感路径、工作区外、"
                 "凭据文件、脚本内容扫描、外部内容升级门、灾难命令都在它前面,越不过去。"
                 "**ask 与 deny 反过来是加严**:ask 会把 full/accept_all 省掉的那一问要回来,"
                 "deny 在读只放行之前就拒,两者都只能更严、不能更松。")
    return "\n".join(lines)



def permissions(ctx, action: str = "show") -> str:
    """permissions 工具(只读):这个项目白名单现在到底生不生效、为什么。"""
    action = str(action or "show").strip().lower()
    if action not in ("show", "verify"):
        return "错误: action 只支持 show / verify。"
    spec = _spec_of(ctx)
    if not spec.sha:
        # 「这个项目没有 permissions.json」只是半句实话:本机那本账跟这份文件在不在
        # 无关。有记住的条目却不列出来,回话就成了"这里没有任何免确认动作" —— 而
        # 用户马上要问的正是"那你为什么这一步没问我"。
        return (f"这个项目没有 {PERMISSIONS_FILE}(或读不到:{spec.reason})。\n"
                "要预先批一些动作,在项目根放一份 {\"allow\": [...]} 的 JSON,"
                "再让我用 permissions_trust 批准它 —— **写完不等于生效**,要核对过才行。"
                + (("\n\n" + _digest_lines(spec)) if spec.granted else ""))
    head = f"{PERMISSIONS_FILE}(工作区 {spec.workspace}):\n"
    return head + _digest_lines(spec)


def permissions_trust(ctx, action: str = "trust") -> str:
    """permissions_trust 工具:把**当前这份**文件的哈希记进本机信任库(或撤销)。

    记的是内容指纹,不是"这个目录可信":文件一改就失效,这是"clone 别人的仓库"和
    "注入内容诱导模型自己写一份"两条路共同的堵法(见模块开头第 1 条)。
    """
    action = str(action or "trust").strip().lower()
    if action not in ("trust", "revoke"):
        return "错误: action 只支持 trust / revoke。"
    ws = getattr(ctx, "workspace", None)
    if not ws:
        return "错误: 没有工作区,白名单无从谈起。"
    if action == "revoke":
        # 撤销只动白名单这半截:同一行还记着"这个项目的 AGENTS.md 采纳过没有",
        # 那是用户另一次点头,不该被这一次顺手带走(v0.9.127)。
        # 但**本机记住的免确认动作算这一半**(v0.9.133):它们与文件里的规则说的是
        # 同一件事 —— "这类动作不用再问" —— 所以一句"撤销"必须把它们一起带走,
        # 否则用户以为撤干净了,而某个文件从此不再问他。
        n = len(granted_rules(Path(ws))[0])
        ok = revoke(Path(ws))
        if not ok and not n:
            return "这个项目本来也没批准过,不用撤。"
        return ("已撤销对这个项目白名单的批准,回到逐次确认。"
                + (f"同时忘掉本机记住的 {n} 条免确认动作。" if n else "")
                + "(这个项目指令文件的采纳没动 —— 那要单独说不采纳。)")
    spec = _spec_of(ctx)
    if not spec.sha:
        return f"错误: 没有可批准的 {PERMISSIONS_FILE}(或读不到:{spec.reason})。"
    if not spec.rules:
        return "错误: 这份白名单里没有一条能用的规则(见 permissions 的拒收清单),不批准空清单。"
    trust(Path(ws), spec.sha, len(spec.rules))
    n_ask = sum(1 for r in spec.rules if r.decision == DECISION_ASK)
    n_deny = sum(1 for r in spec.rules if r.decision == DECISION_DENY)
    tail = []
    if spec.rules and any(r.decision == DECISION_ALLOW for r in spec.rules):
        tail.append("命中 allow 的动作**不再问你**(只到「权限档位本该问一次」这一层,红线链照旧)")
    if n_ask:
        tail.append(f"{n_ask} 条 ask 会**把问题要回来**,连 full / accept_all 也要问")
    if n_deny:
        tail.append(f"{n_deny} 条 deny 会在读只放行之前就拒掉")
    return (f"已批准这 {len(spec.rules)} 条规则(记的是内容指纹 "
            f"{spec.sha[:16]}…):{spec.workspace}\n"
            + ";".join(tail) + "。文件改一个字就自动失效,要重新批准再说一声。")

