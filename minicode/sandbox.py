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


def classify_path(workspace: Path, raw: str, home: Path | None = None) -> tuple[str, Path]:
    """返回 (分级, 绝对路径)。路径解析失败时按工作区外处理。"""
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

    def describe(self) -> str:
        if not self.policy.enabled:
            return "沙箱关闭(工作区外访问仍需逐次确认)"
        parts = ["沙箱开启"]
        parts.append("可访问工作区外" if self.policy.allow_outside_workspace else "仅限工作区")
        parts.append("允许联网" if self.policy.allow_network else "禁网")
        return " · ".join(parts)
