"""沙箱策略:把"Agent 能碰什么"从硬编码变成可配置的边界。

三层边界(参考星瑶 Y-Code,按本项目规模收敛):
1. **敏感路径**:永远禁止读写(`~/.yinluxs` 凭据、`~/.ssh`、`~/.aws` 等),
   任何模式、任何用户确认都不能越过——白名单和授权都不能突破这条线;
2. **工作区围栏**:工作区内的路径默认放行;工作区外默认拒绝,
   除非 `allow_outside_workspace` 打开、或用户在本回合确认过该路径;
3. **命令管控**:灾难命令照旧硬拒绝(permission.check_disaster_command),
   沙箱额外支持网络命令开关、自定义禁配正则、执行超时上限。

配置(config.json)::

    "sandbox": {
      "enabled": true,
      "allow_outside_workspace": false,
      "allow_network": true,
      "timeout_seconds": 60,
      "deny_patterns": ["git push"]
    }
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
    )


def policy_to_raw(policy: SandboxPolicy) -> dict:
    return {
        "enabled": policy.enabled,
        "allow_outside_workspace": policy.allow_outside_workspace,
        "allow_network": policy.allow_network,
        "timeout_seconds": policy.timeout_seconds,
        "deny_patterns": list(policy.deny_patterns),
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
    """命令里出现的绝对路径是否指向工作区外(用于确认框提示)。"""
    if policy.allow_outside_workspace:
        return None
    ws = Path(workspace).resolve()
    for token in re.findall(r"[A-Za-z]:[\\/][^\s\"';|&]*", command or ""):
        candidate = Path(token.rstrip(".,"))
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if not _is_under(candidate, ws):
            return token
    return None


# --------------------------------------------------------------------- 状态

class SandboxState:
    """一次会话内的沙箱状态:记录用户本回合批准过的工作区外路径。"""

    def __init__(self, policy: SandboxPolicy, workspace: Path):
        self.policy = policy
        self.workspace = Path(workspace)
        self.approved_paths: set[str] = set()

    def new_turn(self) -> None:
        self.approved_paths.clear()

    def path_allowed(self, p: Path) -> bool:
        if self.policy.allow_outside_workspace:
            return True
        return str(p) in self.approved_paths

    def approve_path(self, p: Path) -> None:
        self.approved_paths.add(str(p))

    def retarget(self, workspace: Path) -> None:
        """切换工作区:换围栏根,并清掉本回合批准过的工作区外路径。

        `approved_paths` 是按**绝对路径**攒的,换根以后旧授权不该继续生效 ——
        否则用户在新工作区里会莫名继承上一个工作区的放行。
        """
        self.workspace = Path(workspace)
        self.approved_paths.clear()

    def describe(self) -> str:
        if not self.policy.enabled:
            return "沙箱关闭(工作区外访问仍需逐次确认)"
        parts = ["沙箱开启"]
        parts.append("可访问工作区外" if self.policy.allow_outside_workspace else "仅限工作区")
        parts.append("允许联网" if self.policy.allow_network else "禁网")
        return " · ".join(parts)
