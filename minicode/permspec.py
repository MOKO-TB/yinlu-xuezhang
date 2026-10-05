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
from dataclasses import dataclass, field
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

    @property
    def effective(self) -> bool:
        return bool(self.rules) and self.trusted

    def hit(self, name: str, args: dict, decision: str) -> str:
        """命中某一种决定就返回"按第几条办的人话",否则空串。

        三种决定**分头扫**,不是"第一条匹配的用它的决定":同一条路径上若既写了
        allow 又写了 deny,后者才是用户该得到的结果 —— 拒绝优先,与 Claude/Codex
        的 `deny > ask > allow` 同一条尺,也和红线排在一切放行之前同构。
        """
        if not self.effective:
            return ""
        for rule in self.rules:
            if rule.decision == decision and rule.matches(name, args):
                return f"permissions.json 第 {rule.index} 条:{rule.describe()}"
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


def trust(workspace: Path, sha: str, rules_n: int = 0) -> None:
    table = _read_trust()
    table[str(Path(workspace).resolve())] = {
        "sha256": sha, "rules": rules_n, "approved_at": _now()}
    _write_trust(table)


def revoke(workspace: Path) -> bool:
    table = _read_trust()
    key = str(Path(workspace).resolve())
    if key not in table:
        return False
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
    lines = [f"· 规则:{len(spec.rules)} 条({spec.summary()})"
             + ("(已生效 —— 命中 allow 就免确认)" if spec.effective else
                f"(未生效:{spec.reason or '原因未报'})")]
    if spec.sha:
        lines.append(f"· 文件指纹 sha256 {spec.sha[:16]}…"
                     " —— 批准时记的就是这个,改一个字整份规则失效")
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
        return (f"这个项目没有 {PERMISSIONS_FILE}(或读不到:{spec.reason})。\n"
                "要预先批一些动作,在项目根放一份 {\"allow\": [...]} 的 JSON,"
                "再让我用 permissions_trust 批准它 —— **写完不等于生效**,要核对过才行。")
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
        return ("已撤销对这个项目白名单的批准,回到逐次确认。"
                if revoke(Path(ws)) else "这个项目本来也没批准过,不用撤。")
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

