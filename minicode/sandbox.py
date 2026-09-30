"""沙箱策略:把"Agent 能碰什么"从硬编码变成可配置的边界。

三层边界(参考星瑶 Y-Code,按本项目规模收敛):
1. **敏感路径**:永远禁止读写(`~/.yinluxs` 凭据、`~/.ssh`、`~/.aws` 等),
   任何模式、任何用户确认都不能越过——白名单和授权都不能突破这条线;
2. **工作区围栏**:工作区内的路径默认放行;工作区外默认拒绝,
   除非 `allow_outside_workspace` 打开、或用户在本回合确认过该路径;
3. **命令管控**:灾难命令照旧硬拒绝(permission.check_disaster_command),
   沙箱额外支持网络命令开关、自定义禁配正则、执行超时上限;
4. **脚本内容一层**(v0.9.39 起):`py_script`/`bash python x.py` 要跑的文件
   先按行扫一遍,碰到密钥位置、走出工作区、或有外传出口时把**命中的那几行**
   摆进确认框;子进程环境里名字像凭据的变量默认摘掉(`strip_env_secrets`)。

配置(config.json)::

    "sandbox": {
      "enabled": true,
      "allow_outside_workspace": false,
      "allow_network": true,
      "timeout_seconds": 60,
      "deny_patterns": ["git push"],
      "strip_env_secrets": true
    }

**这一层不是隔离**:子进程仍以当前用户权限运行,禁网也只是按命令名/导入名拦
(见 `KNOWN_LIMITS`)。所以宁可"把可疑行给你看一眼再决定",不假装挡住了。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 路径分级
INSIDE = "inside"      # 工作区内:直接放行
OUTSIDE = "outside"    # 工作区外:看策略(允许 / 需确认)
BLOCKED = "blocked"    # 敏感路径:永远拒绝
SECRET = "secret"      # 工作区内的凭据文件:不禁止,但永远要用户确认(注入防御 M3.1)

ALLOW = "allow"
ASK = "ask"
REJECT = "reject"

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300

# 永不开放读写的目录(相对用户主目录);大小写不敏感比对
SENSITIVE_DIRS = (
    ".yinluxs",            # 本应用自己的凭据文件
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gcloud",
    ".azure",
    "appdata/roaming/microsoft/credentials",
)

# 工作区内的凭据类文件名(注入防御 M3.1)。
# 语义:它们**不**像 SENSITIVE_DIRS 那样硬禁(用户确实可能要让 agent 改配置),
# 但 read_file / grep / bash cat 这类"免确认只读"路径必须被升级为 ASK ——
# 否则一条网页里的指令就能让模型在工作区内无声读走密钥。
# 比对只看路径最后一段的文件名,大小写不敏感。
CREDENTIAL_FILENAMES = (
    "config.local.json",    # 本应用工作区配置(可能含用户自填 key)
    "credentials.json",
    "token-usage.db",       # 用量库不含密钥,但属于本机运行数据
    ".env",
    "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
    "servers.config",
)
CREDENTIAL_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".kdbx")
# 任何分级下都直接拒绝对话模型读写的目录名(工作区里出现这些目录也当敏感目录)
CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".azure", ".yinluxs")

# 只在沙箱开启且 allow_network=false 时拦截
NETWORK_COMMANDS = (
    "curl", "wget", "ssh", "scp", "sftp", "telnet", "nc", "ncat",
    "ping", "tracert", "ftp", "rsync",
)
_PIPE_SPLIT = re.compile(r"[;&|\n]")


@dataclass
class SandboxPolicy:
    enabled: bool = True
    allow_outside_workspace: bool = False
    allow_network: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT
    deny_patterns: list[str] = field(default_factory=list)
    # 子进程环境剥离:默认开。关掉只影响 bash/py_script 起的子进程,
    # 不影响本应用自己读环境变量里的 Key(那条路在 config.py)。
    strip_env_secrets: bool = True

    def command_timeout(self, requested: int | None) -> int:
        """把工具请求的超时夹到沙箱上限内。"""
        want = int(requested) if requested else self.timeout_seconds
        return max(1, min(want, self.timeout_seconds, MAX_TIMEOUT))


def policy_from_raw(raw) -> SandboxPolicy:
    """从 config.json 的 sandbox 段解析;非法值退回默认,绝不抛异常。"""
    if not isinstance(raw, dict):
        return SandboxPolicy()
    patterns: list[str] = []
    for p in raw.get("deny_patterns") or []:
        if isinstance(p, str) and p.strip():
            patterns.append(p.strip())
    try:
        timeout = int(raw.get("timeout_seconds", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return SandboxPolicy(
        enabled=bool(raw.get("enabled", True)),
        allow_outside_workspace=bool(raw.get("allow_outside_workspace", False)),
        allow_network=bool(raw.get("allow_network", True)),
        timeout_seconds=max(1, min(timeout, MAX_TIMEOUT)),
        deny_patterns=patterns,
        strip_env_secrets=bool(raw.get("strip_env_secrets", True)),
    )


def policy_to_raw(policy: SandboxPolicy) -> dict:
    return {
        "enabled": policy.enabled,
        "allow_outside_workspace": policy.allow_outside_workspace,
        "allow_network": policy.allow_network,
        "timeout_seconds": policy.timeout_seconds,
        "deny_patterns": list(policy.deny_patterns),
        "strip_env_secrets": policy.strip_env_secrets,
    }


# --------------------------------------------------------------------- 路径

def sensitive_roots(home: Path | None = None) -> list[Path]:
    base = Path(home) if home is not None else Path.home()
    return [(base / rel).resolve() for rel in SENSITIVE_DIRS]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_credential_filename(name: str) -> bool:
    """单个文件名是否属于凭据类(大小写不敏感)。"""
    low = str(name or "").strip().lower()
    if not low:
        return False
    if low in CREDENTIAL_FILENAMES:
        return True
    if any(low.endswith(suf) for suf in CREDENTIAL_SUFFIXES):
        return True
    # .env.local / .env.production 这类变体
    if low.startswith(".env") and low != ".env.example":
        return True
    return False


def _has_credential_dir(p: Path) -> bool:
    parts = {str(x).lower() for x in p.parts}
    return bool(parts & set(CREDENTIAL_DIRS))


def classify_path(workspace: Path, raw: str, home: Path | None = None) -> tuple[str, Path]:
    """返回 (分级, 绝对路径)。路径解析失败时按工作区外处理。

    工作区内的凭据类文件返回 SECRET(比 INSIDE 严:只读也要确认);
    真正的敏感目录(~/​.ssh 等)照旧 BLOCKED。
    """
    text = (raw or "").strip() or "."
    p = Path(text)
    if not p.is_absolute():
        p = Path(workspace) / p
    try:
        p = p.resolve()
    except OSError:
        p = Path(workspace).resolve() / text

    for root in sensitive_roots(home):
        if _is_under(p, root):
            return BLOCKED, p
    if _is_under(p, Path(workspace).resolve()):
        if is_credential_filename(p.name) or _has_credential_dir(
                p.relative_to(Path(workspace).resolve())):
            return SECRET, p
        return INSIDE, p
    return OUTSIDE, p


def classify_for_write(workspace: Path, raw: str, home: Path | None = None) -> tuple[str, Path]:
    """写入场景:文件本身可以位于敏感目录之外,但父目录落在敏感目录里也要拦下
    (避免先写 `~/.ssh/config` 的父目录再落文件这类绕过)。"""
    kind, p = classify_path(workspace, raw, home)
    if kind == BLOCKED:
        return kind, p
    for root in sensitive_roots(home):
        if _is_under(root, p) or root == p:
            return BLOCKED, p
    return kind, p


# --------------------------------------------------------------------- 命令

def command_hits_network(command: str) -> str | None:
    """命令片段里是否调用了网络类命令(只看每段的首个 token)。"""
    for seg in _PIPE_SPLIT.split(command or ""):
        tokens = [t.strip("\"'") for t in seg.split()]
        if not tokens:
            continue
        head = tokens[0].lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        head = head[:-4] if head.endswith(".exe") else head
        if head in NETWORK_COMMANDS:
            return tokens[0]
    return None


def check_command(policy: SandboxPolicy, command: str) -> tuple[str, str]:
    """返回 (决策, 说明)。REJECT 表示沙箱直接拦下;ASK 表示交给确认流程。"""
    cmd = (command or "").strip()
    if not cmd:
        return ALLOW, ""
    for pattern in policy.deny_patterns:
        try:
            hit = re.search(pattern, cmd, re.IGNORECASE)
        except re.error:
            hit = pattern.lower() in cmd.lower()
        if hit:
            return REJECT, f"命令命中沙箱禁配规则「{pattern}」"

    if policy.enabled and not policy.allow_network:
        found = command_hits_network(cmd)
        if found:
            return REJECT, (f"沙箱已关闭网络访问,禁止执行 {found}"
                            f"(需要联网请在 config.json 把 sandbox.allow_network 设为 true)")
    return ALLOW, ""


def command_hits_credential(workspace: Path, command: str) -> Path | None:
    """命令里提到的路径 token 是否指向凭据类文件(注入防御 M3.1 的 bash 一头)。

    `read_file` 看参数就能拦住,但 `bash("type config.local.json")` 同样能读到内容,
    所以命令里的路径也得扫。只把"真正像文件引用"的 token 算进来(带路径分隔符,
    或本身就是已知凭据名/凭据后缀)。

    **注意一个已知的保守面**:只看 token 不看引号,所以
    `echo "别读 config.local.json"` 这种"只是提到"也会要确认一次。宁多问一次、
    不无声放行(真误伤到日常用法再按命令前缀收窄,而不是关掉整个门)。
    """
    ws = Path(workspace).resolve()
    for token in re.findall(r"[^\s\"';|&]+", command or ""):
        cleaned = token.strip("\"'(),").replace("\\", "/")
        if not cleaned or cleaned.startswith("-"):
            continue
        # 只认真正像文件引用的:路径分隔符,或已知凭据名/后缀
        name = cleaned.rsplit("/", 1)[-1]
        if not is_credential_filename(name):
            continue
        if ("/" not in cleaned and "\\" not in cleaned
                and name not in CREDENTIAL_FILENAMES
                and not cleaned.startswith(".")):
            continue
        p = Path(cleaned)
        if not p.is_absolute():
            p = ws / p
        try:
            p = p.resolve()
        except OSError:
            continue
        if _is_under(p, ws):
            return p
    return None


def command_touches_outside_path(policy: SandboxPolicy, workspace: Path,
                                 command: str) -> str | None:
    r"""命令里出现的绝对路径是否指向工作区外(用于确认框提示)。

    除了 `D:\...` 这种盘符路径,也认 `~/...`、`~\...`、`$HOME`、`%USERPROFILE%`、
    `$env:USERPROFILE` —— 只拦盘符的话,`cat ~/.ssh/id_rsa` 会从门缝里走过去
    (`.ssh` 那一路有 `command_hits_credential` 兜,但 `~/notes` 这种"只是工作区外"
    的没有任何人管)。

    盘符前面要求不是字母/数字,否则 `curl https://x` 里的 `p://x` 会被当路径
    认出来 —— 那会让每条联网命令都白挨一次确认(这个坑是接线时实测撞出来的)。
    """
    if policy.allow_outside_workspace:
        return None
    ws = Path(workspace).resolve()
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"';|&]*",
                            command or ""):
        candidate = Path(token.rstrip(".,"))
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if not _is_under(candidate, ws):
            return token
    m = re.search(r"(?:~/|~\\|\$HOME|%USERPROFILE%|\$env:USERPROFILE)",
                  command or "", re.IGNORECASE)
    if m:
        return m.group(0)
    return None


# --------------------------------------------------------- 脚本内容级检测
#
# 为什么要扫内容:`py_script` 的 path 参数受 resolve_path 围栏约束,但**子进程
# 本身不受任何约束** —— 脚本里写 `open(os.path.expanduser("~/.ssh/id_rsa"))`
# 就能拿到沙箱承诺"永远不碰"的东西。这一层是"读一遍要跑的代码再问用户",
# 不是真隔离(见文末 KNOWN_LIMITS),所以只升级到确认,不做拦截。

SCRIPT_SCAN_BYTES = 200_000          # 只扫前 200 KB:再大的脚本先给人看的那几行
SCRIPT_LINE_CLIP = 90

# 伸手到凭据/密钥位置(与 SENSITIVE_DIRS / CREDENTIAL_FILENAMES 同一套语义)
SCRIPT_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("密钥/凭据目录", re.compile(r"(\.ssh\b|\.aws\b|\.gnupg\b|\.yinluxs\b|\.azure\b"
                                 r"|config/gcloud|credentials)", re.IGNORECASE)),
    ("私钥文件", re.compile(r"(id_rsa|id_ed25519|id_dsa|id_ecdsa|\.pem\b|\.p12\b"
                             r"|\.pfx\b|\.key\b|\.kdbx\b)", re.IGNORECASE)),
    ("本应用的密钥文件", re.compile(r"(config\.local\.json|\.env(?![a-z])"
                                    r"|token-usage\.db)", re.IGNORECASE)),
    ("系统凭据存储", re.compile(r"(wincred|credential_manager|keyring)", re.IGNORECASE)),
)

# 越出工作区围栏(不一定是密钥,但沙箱默认承诺"仅限工作区")。
# 写死的盘符绝对路径另由 _outside_abs_paths 判断 —— 它要知道工作区在哪才算得出"外"。
SCRIPT_ESCAPE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("访问用户主目录", re.compile(
        r"(expanduser\s*\(|Path\s*\.\s*home\s*\(|USERPROFILE"
        r"|getenv\s*\(\s*[\"']HOME|environ\s*\[\s*[\"']HOME)",
        re.IGNORECASE)),
)

# 内容外传的出口(联网 / 再起子进程)。只按名字与导入扫 —— 是"看过一眼再问",
# 不是防火墙。
SCRIPT_EGRESS_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("联网", re.compile(r"(^\s*(?:import|from)\s+(urllib|requests|httpx|aiohttp"
                        r"|socket|ftplib|smtplib|telnetlib|websocket|websockets)\b"
                        r"|urllib\.request\.urlopen|requests\.(?:get|post|put|request)"
                        r"|httpx\.(?:get|post|Client)|socket\.socket)",
                        re.IGNORECASE | re.MULTILINE)),
    ("再起子进程", re.compile(r"(\bsubprocess\.(?:run|call|Popen|check_output)\b"
                              r"|\bos\.system\s*\(|\bos\.p?open\s*\()", re.IGNORECASE)),
)

_ENV_SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|apikey|secret|token|passwor|passwd|credential"
    r"|private[_-]?key|access[_-]?key|session[_-]?(?:id|token)|cookie"
    r"|^auth[_-]|[_-]auth$|auth[_-]?(?:token|key))",
    re.IGNORECASE,
)


def _scan_lines(text: str, patterns) -> list[dict]:
    """按行扫,命中即记(行号 + 原文片段)—— 让人一眼看出是"真读"还是"提了一句"。"""
    out: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        for label, rx in patterns:
            if rx.search(line):
                code = line.strip()
                out.append({"kind": label, "line": i,
                            "code": code[:SCRIPT_LINE_CLIP] +
                            ("…" if len(code) > SCRIPT_LINE_CLIP else "")})
    return out


# 引号把"单个字母 + :\…"锚住,所以 `"http://x"` 不会被当成盘符路径
_ABS_IN_STRING = re.compile(r"[\"']([A-Za-z]:[\\/][^\"']{2,})[\"']")


def _outside_abs_paths(text: str, workspace: Path | None) -> list[dict]:
    """脚本里写死的绝对路径(引号里的盘符路径)是否落在工作区外。

    不带 workspace 就不判这一项:工作区内的 `D:\\...\\out.pptx` 是正常产物路径,
    拿它吓人是误伤;只有真越出围栏才报。
    """
    if workspace is None:
        return []
    ws = Path(workspace).resolve()
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _ABS_IN_STRING.finditer(line):
            try:
                p = Path(m.group(1)).resolve()
            except OSError:
                continue
            if not _is_under(p, ws):
                code = line.strip()
                out.append({"kind": "工作区外的写死路径", "line": i,
                            "code": code[:SCRIPT_LINE_CLIP]})
    return out


def scan_script_text(text: str, workspace: Path | None = None) -> dict:
    """扫一份要跑的脚本,返回 {credential, escape, egress, truncated}。

    命中不算定罪:注释、字符串、文档里提到 `.ssh` 一样会命中。所以这里只产出
    "给用户看的那几行",决定权留给用户(见 KNOWN_LIMITS 的取舍说明)。
    """
    body = str(text or "")
    raw = body.encode("utf-8", errors="replace")
    truncated = len(raw) > SCRIPT_SCAN_BYTES
    if truncated:
        body = raw[:SCRIPT_SCAN_BYTES].decode("utf-8", errors="replace")
    escape = sorted(_scan_lines(body, SCRIPT_ESCAPE_PATTERNS)
                    + _outside_abs_paths(body, workspace), key=lambda r: r["line"])
    return {
        "credential": _scan_lines(body, SCRIPT_CREDENTIAL_PATTERNS),
        "escape": sorted(escape, key=lambda r: r["line"]),
        "egress": _scan_lines(body, SCRIPT_EGRESS_PATTERNS),
        "truncated": truncated,
    }


def script_risk_note(hits: dict, source: str = "") -> str:
    """把 scan_script_text 的结果写成确认框里的那一段话。"""
    lines = [f"要运行的脚本{(' ' + source) if source else ''}里有这些内容"
             "(可能只是提到,不是真读取):"]
    for key, label in (("credential", "碰密钥/凭据位置"),
                       ("escape", "走出工作区"),
                       ("egress", "外传出口")):
        rows = hits.get(key) or []
        if rows:
            merged: dict[str, list[dict]] = {}
            for r in rows:
                merged.setdefault(r["kind"], []).append(r)
            for kind, rs in merged.items():
                text = "; ".join(f"第 {r['line']} 行 {r['code']}" for r in rs[:4])
                more = f"(另有 {len(rs) - 4} 处)" if len(rs) > 4 else ""
                lines.append(f"· {label} · {kind}:{text}{more}")
    if hits.get("truncated"):
        lines.append(f"· 只扫了前 {SCRIPT_SCAN_BYTES // 1000} KB,后面的没看")
    return "\n".join(lines) if len(lines) > 1 else ""


def script_hits(policy: SandboxPolicy, text: str, *, touched_external: bool,
                workspace: Path | None = None) -> tuple[str, str]:
    """脚本内容的沙箱决策:(ALLOW | ASK | REJECT, 说明)。

    - 禁网 + 脚本里有联网出口 → REJECT,和 bash 的 allow_network 同一口径
      (但说明里如实讲:这是名字级门禁,不是断网);
    - 碰到密钥/凭据位置、或走出工作区 → ASK,**不因权限档位而放开**
      (与工作区外路径、凭据文件同一心智模型);
    - 本回合接触过外部内容 + 有外传出口 → ASK(注入防御 M3.2 的脚本一头)。
    """
    hits = scan_script_text(text, workspace)
    if policy.enabled and not policy.allow_network and hits["egress"]:
        return REJECT, ("沙箱已关闭网络访问,而这份脚本里有联网/起子进程的迹象:"
                        + " ;".join(f"第 {r['line']} 行 {r['code']}"
                                    for r in hits["egress"][:3])
                        + KNOWN_LIMITS["network"])
    if hits["credential"] or hits["escape"]:
        return ASK, script_risk_note(hits)
    if touched_external and hits["egress"]:
        return ASK, (script_risk_note(hits)
                     + "\n(本回合刚接触过外部内容,外传出口请确认它确实是你要的)")
    return ALLOW, ""


def scrub_env(env: dict) -> tuple[dict, list[str]]:
    """把名字看起来是凭据的环境变量从**子进程**环境里摘掉。

    只影响 bash / py_script 起的子进程。应用自己要用的变量在启动时已经读过
    (`config.py` 里的 `YINLUXS_*`),摘掉子进程那份不影响对话,只影响
    "脚本顺手把 key 打印出去 / 发走"这条路。
    """
    keep, dropped = {}, []
    for k, v in env.items():
        if _ENV_SECRET_PATTERNS.search(str(k)):
            dropped.append(str(k))
            continue
        keep[k] = v
    return keep, sorted(dropped)


# 已知边界:写在这里而不是埋在注释里,便于文档与自述引用同一份口径。
KNOWN_LIMITS = {
    "network": "(说明:这是按命令名/脚本导入扫的**门禁**,不是操作系统防火墙 —— "
               "想真断网请用手机热点级隔离或虚拟机)",
    "script": "沙箱不给子进程做隔离:扫脚本内容只能提前把可疑行摆到你面前,"
              "决定权在确认框那一步。",
    # 后台命令(bash background=true)唯一的生命周期边界:它天生绕开 timeout,
    # 所以收手点不在"跑多久",而在"学长还在不在"。这句话由 explain.risk_notes
    # 在真的传了 background=true 时摆进确认框,不无脑贴在每条命令上。
    "background": "后台命令不受 300 秒超时收手,也不会因为这一轮结束而停止;"
                  "学长退出时才尝试收掉它(收不掉会照实说)。",
}


# --------------------------------------------------------------------- 状态

class SandboxState:
    """一次会话内的沙箱状态:记录用户批准过的工作区外路径。

    两档记忆(v0.9.43 起):
    - `approved_paths`:本回合内有效(每次 new_turn 清空) —— 用户点「允许」的默认;
    - `session_approved`:用户点「总是允许」时的路径,本会话内不再问,换工作区即清空。
    都**不落盘**:进程退出就没了,下一次会话从零开始 —— "会话内记住"是给同一份
    工作的连续操作省确认,不是把授权存成永久白名单。
    """

    def __init__(self, policy: SandboxPolicy, workspace: Path):
        self.policy = policy
        self.workspace = Path(workspace)
        self.approved_paths: set[str] = set()
        self.session_approved: set[str] = set()

    def new_turn(self) -> None:
        self.approved_paths.clear()      # session_approved 跨回合保留,见类注释

    def path_allowed(self, p: Path) -> bool:
        if self.policy.allow_outside_workspace:
            return True
        return str(p) in self.approved_paths or str(p) in self.session_approved

    def approve_path(self, p: Path, session: bool = False) -> None:
        (self.session_approved if session else self.approved_paths).add(str(p))

    def retarget(self, workspace: Path) -> None:
        """切换工作区:换围栏根,并清掉本回合批准过的工作区外路径。

        `approved_paths` 是按**绝对路径**攒的,换根以后旧授权不该继续生效 ——
        否则用户在新工作区里会莫名继承上一个工作区的放行。会话级那一份同理,
        而且更要清:它跨回合还活着,不清的话旧工作区的授权会跟到新工作区来。
        """
        self.workspace = Path(workspace)
        self.approved_paths.clear()
        self.session_approved.clear()

    def describe(self) -> str:
        if not self.policy.enabled:
            return "沙箱关闭(工作区外访问仍需逐次确认)"
        parts = ["沙箱开启"]
        parts.append("可访问工作区外" if self.policy.allow_outside_workspace else "仅限工作区")
        parts.append("允许联网" if self.policy.allow_network
                     else "禁网(按命令名/脚本导入拦,不是防火墙)")
        if self.policy.strip_env_secrets:
            parts.append("子进程已摘密钥环境变量")
        if self.session_approved:
            parts.append(f"本会话已记住 {len(self.session_approved)} 个工作区外路径")
        return " · ".join(parts)
