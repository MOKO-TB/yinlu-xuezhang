"""权限门:变更类工具需要用户确认;灾难性命令硬拒绝,不给确认机会。

沙箱联动(参考星瑶 Y-Code 的边界模型):
- 敏感路径(凭据/密钥目录)一律硬拒绝,任何权限模式都不能越过;
- 工作区外的路径即便在 full 模式下也要用户确认一次,
  确认后本回合内该路径放行(allow_outside_workspace 打开则直接放行);
- 沙箱禁配规则/禁网命令同样硬拒绝。

四档权限模式(对齐星瑶 Y-Code 的 app_context,2026-09-16 起):
- confirm      变更前确认 —— 查询自动放行;任何变更类工具都要确认(默认);
- accept_edits 自动编辑 —— 工作区内的写文件/改文件/构建类命令自动放行;
               删除、跨区写、敏感命令等仍要确认(写子代理批量干活的主力档);
- accept_all   自动放行 —— 沙箱边界外的仍逐次确认(老档位,保留兼容旧 config);
- full         完全访问 —— 沙箱内放行所有非灾难性操作;敏感路径/灾难命令/
               沙箱红线依然硬拒绝,档位再高也越不过安全底线。
"""
from __future__ import annotations

import re
from pathlib import Path

from minicode.config import Config
from minicode.sandbox import (
    ASK as SB_ASK, BLOCKED, KNOWN_LIMITS, OUTSIDE,
    REJECT as SB_REJECT, SECRET, check_command, classify_for_write,
    classify_path, command_hits_credential, command_hits_network,
    command_touches_outside_path, script_hits,
)
from minicode.tools.base import Tool

ALLOW = "allow"
ASK = "ask"
REJECT = "reject"

WRITE_TOOLS = ("write_file", "edit_file")

# 用户确认后需要记住"这个路径可以访问"的工具。
# ocr_image 也在这里:它自己 resolve 路径交给识别引擎,不经过 resolve_path 围栏,
# 少了这道门就等于"给个绝对路径就能把工作区外的图片读走"(注入防御要堵的洞)。
PATH_TOOLS = ("read_file", "write_file", "edit_file", "py_script", "ocr_image")

# accept_edits(自动编辑)档放行的构建类命令首词。只放"跑测试/装依赖/编译"
# 这类可重复执行、不直接破坏数据的动作;删改类(rm/del/Remove-Item/git push…)
# 一律不进表 —— 宁可多问一次,不放走一个破坏性动词。
BUILD_COMMANDS = frozenset({
    "python", "python3", "py", "pytest", "pip", "pip3",
    "node", "npm", "npx", "yarn", "pnpm",
    "make", "cargo", "go",
})

# 权限档位全集(前端下拉、CLI /permission 都按这个顺序展示)
PERMISSION_MODES = ("confirm", "accept_edits", "accept_all", "full")

MODE_LABELS = {
    "confirm": "变更前确认",
    "accept_edits": "自动编辑",
    "accept_all": "自动放行",
    "full": "完全访问",
}


def normalize_mode(mode: str) -> str:
    """未知/空档位一律归回 confirm(旧配置里只会有 confirm/accept_all)。"""
    return mode if mode in PERMISSION_MODES else "confirm"


def _check_rm(segment: str) -> str | None:
    """检查单个 rm 命令片段是否在递归删除危险目标。"""
    tokens = [t.strip("\"'") for t in segment.split()]
    if not tokens or tokens[0].rstrip(".exe") != "rm":
        return None
    recursive = any(t.startswith("-") and "r" in t.lower() for t in tokens[1:])
    if not recursive:
        return None
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    for t in targets:
        tl = t.lower().replace("\\", "/")
        if (tl in ("*", ".", "..", "~", "$home", "%userprofile%", "/*", "~/", "/")
                or tl.startswith(("/", "~", "$home"))
                or re.fullmatch(r"[a-z]:/?", tl)):
            return f"rm 递归删除危险目标: {t}"
    return None


def check_disaster_command(command: str) -> str | None:
    """返回硬拒绝原因;None 表示不构成灾难,进入正常确认流程。

    这是兜底黑名单,宁可错杀不可放过;正常危险操作(如删除单个目录)
    仍会走用户确认,不会被这里拦截。
    """
    cmd = (command or "").strip()
    if not cmd:
        return None
    low = cmd.lower()

    # fork 炸弹
    if ":(){:|:&};" in low.replace(" ", ""):
        return "fork 炸弹"

    # rm 逐段检查(按 shell 分隔符切分,避免 echo "rm -rf /" 之类误伤)
    for seg in re.split(r"[;&|\n]", cmd):
        reason = _check_rm(seg)
        if reason:
            return reason

    # 磁盘级破坏
    if (re.search(r"\bformat\s+[a-z]:", low)
            or "diskpart" in low
            or re.search(r"\bmkfs", low)
            or re.search(r"\bdd\b[^|;&]*\bof=/dev/", low)
            or re.search(r"cipher\s+/w", low)):
        return "磁盘级破坏性命令"

    # Windows 批量删除系统位置
    if ("remove-item" in low and "-recurse" in low
            and re.search(r"[a-z]:\\(windows|users|program)|\\windows|\\users|\$home|~", low)):
        return "递归强制删除系统目录"
    if re.search(r"\bdel\b[^&|;]*/s[^&|;]*[a-z]:\\(\s|$)", low):
        return "批量删除盘符根目录"

    return None


def _same_site(origin: str, target: str) -> bool:
    """两个 URL/域名的站点是否相同(只看 host,忽略大小写与 www 前缀)。"""
    def host_of(text: str) -> str:
        t = str(text or "").strip().lower()
        if "://" in t:
            t = t.split("://", 1)[1]
        t = t.split("/", 1)[0].split("?", 1)[0]
        t = t.split("@")[-1].split(":")[0]
        return t[4:] if t.startswith("www.") else t
    a, b = host_of(origin), host_of(target)
    return bool(a) and bool(b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


class PermissionGate:
    """每回合开始时清空"本轮已放行"集合;确认时用户可选只放行一次或本轮全放。

    除四档权限/沙箱路径/灾难黑名单之外,本类还管两个**高于档位**的门
    (注入防御 M3):工作区内凭据文件永远要确认、以及"本回合接触过外部内容后
    的敏感动作"强制确认。设计按的是"档位越高越信任**用户**,不更信任**外部内容**"。
    """

    def __init__(self, config: Config, sandbox=None):
        self.config = config
        self.sandbox = sandbox          # minicode.sandbox.SandboxState
        self._turn_allowed: set[str] = set()
        # 回合内的"外部接触"标志(注入防御 M3.2);Agent._execute 调 note_external 置位
        self._touched_external = False
        self._external_source = ""
        self._ask_note = ""

    def new_turn(self) -> None:
        self._turn_allowed.clear()
        self._touched_external = False
        self._external_source = ""
        self._ask_note = ""
        if self.sandbox is not None:
            self.sandbox.new_turn()

    # -------------------------------------------------- 外部内容接触标志

    def note_external(self, tool_name: str = "", args: dict | None = None) -> None:
        """标记"本回合刚接触过外部内容"(由 Agent 在围栏处同一个方调用)。"""
        self._touched_external = True
        self._external_source = str(tool_name or "external")

    @property
    def touched_external(self) -> bool:
        return self._touched_external

    @property
    def ask_note(self) -> str:
        """上一次 check() 为什么强制确认(给确认框多打一行用)。"""
        return self._ask_note

    def _note(self, text: str) -> None:
        self._ask_note = text

    # ------------------------------------------------------------ 沙箱判定

    def _path_kind(self, tool_name: str, raw: str) -> tuple[str, Path] | None:
        if self.sandbox is None or tool_name not in PATH_TOOLS:
            return None
        if not isinstance(raw, str) or not raw.strip():
            return None
        classify = classify_for_write if tool_name in WRITE_TOOLS else classify_path
        return classify(self.sandbox.workspace, raw)

    def approve_outside(self, tool: Tool, args: dict, session: bool = False) -> None:
        """用户点了允许:记住这个工作区外路径。

        `session=False`(点「允许」):本回合内有效;`session=True`(点「总是允许」):
        本会话内该路径不再询问(不落盘,换工作区/重启即失效)。
        """
        if self.sandbox is None:
            return
        name = tool.name.rsplit("·", 1)[-1]
        kind = self._path_kind(name, args.get("path"))
        if kind and kind[0] == OUTSIDE:
            self.sandbox.approve_path(kind[1], session=session)

    # ------------------------------------------------------------ 升级门判定

    # 会污染后续会话信任源的目录:写进去的内容下次会被当可信资料读回来
    TRUST_SOURCE_DIRS = ("kb", "skills")

    def _is_sensitive_action(self, name: str, args: dict) -> str | None:
        """这个动作是否属于"外部接触后要升级确认"的范围;返回 None 表示不算。

        宁窄勿宽:误报一次用户就会把功能关掉(方案 §3.3.2)。只收三类出口:
        联网外传、往信任源目录写、跳域提交表单。
        """
        if name == "bash":
            cmd = str(args.get("command", "") or "")
            hit = command_hits_network(cmd)
            if hit:
                return f"这条命令会联网访问外部服务({hit}),是内容外传的出口"
            if re.search(r"https?://", cmd) and re.search(
                    r"\b(python|py|node|curl|wget)\b", cmd):
                return "这条命令会带着本地内容访问一个网址"
            return None
        if name in WRITE_TOOLS:
            rel = self._rel_to_trust_source(args.get("path"))
            if rel:
                return (f"这一步会写入 {rel} —— 那个目录的内容会被以后的会话"
                        "当可信资料读回来")
            return None
        # 跳域提交表单检测属 M5(P2):要看 form action 得先查 DOM,
        # 在权限门里做会阻塞主流程。本期不落,不留永不触发的死分支。
        if name == "browser_fill" and args.get("submit"):
            off = self._cross_domain_fill(args)
            if off:
                return ("这一步要往**另一个站点**提交表单(刚看的页面是 "
                        f"{off})——跨域提交是表单钓鱼的常见出路")
        return None

    def _rel_to_trust_source(self, raw) -> str | None:
        """路径是否落在工作区的 kb/ 或 skills/ 下(返回相对路径供文案用)。"""
        if self.sandbox is None or not isinstance(raw, str) or not raw.strip():
            return None
        ws = Path(self.sandbox.workspace).resolve()
        p = Path(raw)
        if not p.is_absolute():
            p = ws / p
        try:
            rel = p.resolve().relative_to(ws)
        except (ValueError, OSError):
            return None
        parts = [str(x) for x in rel.parts[:-1]]
        if parts and parts[0].lower() in self.TRUST_SOURCE_DIRS:
            return rel.as_posix()
        return None

    def _cross_domain_fill(self, args: dict) -> str | None:
        """提交目标域 ≠ 本回合最近一次打开的域时返回那个域。"""
        origin = getattr(self, "_browser_origin", "") or ""
        target = getattr(self, "_fill_target_origin", "") or ""
        if not target:
            return None
        if not origin:
            return None          # 没见过页面,没有比较基准
        if _same_site(origin, target):
            return None
        return target

    def _credential_hit(self, name: str, args: dict):
        """本次动作是否碰到工作区内的凭据类文件(注入防御 M3.1)。

        文件工具/grep 看 path 参数;bash 扫命令里的路径 token。
        返回绝对路径或 None。
        """
        if self.sandbox is None:
            return None
        raw = args.get("path")
        if isinstance(raw, str) and raw.strip():
            try:
                kind, path = classify_path(self.sandbox.workspace, raw)
            except Exception:
                return None
            if kind == SECRET:
                return path
            kind_w, path_w = classify_for_write(self.sandbox.workspace, raw)
            if kind_w == SECRET:
                return path_w
        if name == "bash":
            hit = command_hits_credential(
                self.sandbox.workspace, str(args.get("command", "")))
            if hit:
                return hit
        return None

    # ------------------------------------------------------------ 脚本内容一层

    PY_HEADS = frozenset({"python", "python3", "py", "python.exe", "python3.exe"})
    SCRIPT_SCAN_BYTES = 200_000

    def _script_targets(self, name: str, args: dict) -> list:
        """这次动作会跑起来的脚本文件(只收工作区内、真存在的)。

        `py_script` 的 path 参数由 resolve_path 围栏管着,但**文件内容没人看过**;
        `bash("python x.py")` 更是绕过 py_script 的现成后门。两处一起扫。
        """
        if self.sandbox is None:
            return []
        ws = Path(self.sandbox.workspace).resolve()
        raws: list[str] = []
        if name == "py_script":
            raw = args.get("path")
            if isinstance(raw, str) and raw.strip().endswith(".py"):
                raws.append(raw.strip())
        elif name == "bash":
            cmd = str(args.get("command", "") or "")
            for seg in re.split(r"[;&|\n]", cmd):
                tokens = [t.strip("\"'") for t in seg.split()]
                if not tokens:
                    continue
                head = tokens[0].lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                if head not in self.PY_HEADS:
                    continue
                raws.extend(t for t in tokens[1:]
                            if t.lower().endswith(".py") and not t.startswith("-"))
        out = []
        for raw in raws:
            kind, p = classify_path(ws, raw)
            # 工作区外的脚本更要扫(不是豁免理由);敏感目录下的文件本来就碰不到
            if kind != BLOCKED and p.is_file():
                out.append(p)
        return out

    def _read_script(self, p: Path) -> str | None:
        """读脚本文本(只读前 200 KB;读不出来就当没内容,不因此放行也不报错)。"""
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:self.SCRIPT_SCAN_BYTES]
        except OSError:
            return None

    def _script_gate(self, name: str, args: dict) -> str:
        """脚本文案的沙箱判定:ALLOW / ASK / REJECT(ASK 时已写好 _note)。"""
        for p in self._script_targets(name, args):
            text = self._read_script(p)
            if text is None:
                continue
            level, why = script_hits(self.sandbox.policy, text,
                                     touched_external=self._touched_external,
                                     workspace=self.sandbox.workspace)
            if level == SB_REJECT:
                self._note(why)
                return REJECT
            if level == SB_ASK:
                self._note(f"{why}\n{KNOWN_LIMITS['script']}(文件:{p.name})")
                return ASK
        return ALLOW

    def _outside_path_gate(self, name: str, args: dict) -> str:
        """bash 命令里出现工作区外路径:和 read_file 越界同一口径(不因档位放开)。"""
        token = command_touches_outside_path(self.sandbox.policy,
                                             self.sandbox.workspace,
                                             str(args.get("command", "")))
        if not token:
            return ALLOW
        self._note(f"这条命令引用了工作区外的路径 {token} —— 沙箱默认只让它在工作区内活动。"
                   "确认不是你要的请点拒绝;点「总是允许」则本会话内记住这个路径"
                   "(不落盘,换工作区或重启即失效);需要长期放开请在 config.json 打开 "
                   "sandbox.allow_outside_workspace。")
        return ASK

    # ------------------------------------------------------------ 主判定

    def check(self, tool: Tool, args: dict) -> str:
        # 子代理的工具名带类型前缀(如 "explore·bash"),取末段判断
        name = tool.name.rsplit("·", 1)[-1]
        self._ask_note = ""
        if name == "bash":
            reason = check_disaster_command(args.get("command", ""))
            if reason:
                return REJECT

        if self.sandbox is not None:
            # 敏感路径:任何模式、任何授权都不能碰
            kind = self._path_kind(name, args.get("path"))
            if kind is not None:
                level, path = kind
                if level == BLOCKED:
                    return REJECT
                if level == OUTSIDE and not self.sandbox.path_allowed(path):
                    return ASK          # 沙箱边界不因权限档位而放开
            # 凭据文件:工作区内也不免确认(full 档同样拦),而且不因
            # "本轮总是允许"而连续静默放行 —— 每个动作都重新确认。
            cred = self._credential_hit(name, args)
            if cred is not None:
                self._note("⚠ 这是一个含密钥/凭据的文件——模型读它,能带出去的只有"
                           "密钥本身。如果这一步不是你要求的,请点拒绝。")
                return ASK
            if name == "bash":
                level, why = check_command(self.sandbox.policy,
                                           args.get("command", ""))
                if level == SB_REJECT:
                    return REJECT
                if level == SB_ASK and self.config.permission_mode != "full":
                    return ASK
                if level == SB_ASK and self.config.permission_mode == "full":
                    pass                # full 档:沙箱内可配命令放行(见下)
                if not self.sandbox.policy.enabled:
                    pass                # 沙箱关闭:命令仍走下面的常规确认
                # 命令里引用了工作区外的路径:与 read_file 越界同一口径
                if self.sandbox.policy.enabled:
                    if self._outside_path_gate(name, args) == ASK:
                        return ASK
            # 脚本内容一层:py_script 与 bash 里跑的 python 脚本都要先扫
            if self.sandbox.policy.enabled and name in ("py_script", "bash"):
                gate = self._script_gate(name, args)
                if gate != ALLOW:
                    return gate

        if tool.read_only:
            return ALLOW
        # 外部内容升级门:优先级高于权限档位(与沙箱红线同一心智模型)。
        # 只在**本回合接触过外部内容**后生效,没接触时行为与基线逐字节一致。
        if self._touched_external:
            why = self._is_sensitive_action(name, args)
            if why:
                self._note(f"⚠ 本回合此前接触过外部内容({self._external_source}),"
                           f"{why}。这一步可能是外部内容诱导的,请确认它确实是你要的。")
                return ASK
        mode = self.config.permission_mode
        if mode == "full":
            # 完全访问:沙箱内的操作全部放行(敏感路径/灾难命令在上面已被硬拒)。
            # 工作区外路径已在前面 ASK 过一次,这里只放行剩余情况。
            return ALLOW
        if mode == "accept_all":
            return ALLOW
        if mode == "accept_edits":
            # 自动编辑:工作区内的写文件/改文件自动放行;删除、跨区、
            # 敏感命令(上面 SB_ASK 已 return)仍走确认。浏览器打开网页这类
            # 会产生页面跳转的操作不算"编辑",维持逐次确认。
            if name in WRITE_TOOLS:
                return ALLOW
            if name == "bash" and self._is_workspace_build_command(args):
                return ALLOW
            return ASK
        if name in self._turn_allowed:
            return ALLOW
        return ASK

    @staticmethod
    def _is_workspace_build_command(args: dict) -> bool:
        """accept_edits 档放行的"构建类"命令:python/pytest/pip 等无破坏性前缀。

        只认命令的**第一个词**(venv 包装器也算),避免 `-rf` 这类参数混进来;
        删除/格式化/强制类命令不在这里,维持逐次确认。
        """
        cmd = str(args.get("command", "")).strip()
        if not cmd:
            return False
        head = re.split(r"[\s|;&]", cmd, 1)[0].strip("\"'").lower()
        if head.endswith(".exe"):
            head = head[:-4]
        head = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return head in BUILD_COMMANDS

    def approve_for_turn(self, tool_name: str) -> None:
        self._turn_allowed.add(tool_name)
