"""沙箱策略测试:路径分级、命令管控、超时,以及与权限门的联动。"""
from pathlib import Path

import pytest

from minicode.config import Config
from minicode.permission import ALLOW, ASK, REJECT, PermissionGate
from minicode.sandbox import (
    ALLOW as SB_ALLOW, ASK as SB_ASK, BLOCKED, INSIDE, OUTSIDE,
    REJECT as SB_REJECT,
    SandboxPolicy, SandboxState, check_command, classify_for_write,
    classify_path, command_hits_network, command_hits_stealth,
    command_touches_outside_path,
    is_git_internal, policy_from_raw, policy_to_raw, scan_script_text,
    script_hits, sensitive_roots,
)
from minicode.tools.base import PathBlocked, PathOutsideWorkspace, Tool, ToolContext


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".yinluxs").mkdir(parents=True)
    (h / ".ssh").mkdir()
    return h


def _tool(name="read_file", read_only=False):
    return Tool(name=name, description="d", parameters={"type": "object"},
                handler=lambda ctx, **kw: "ok", read_only=read_only)


def _ctx(ws, policy=None, home=None):
    ctx = ToolContext(workspace=ws, config=Config())
    ctx.sandbox = SandboxState(policy or SandboxPolicy(), ws)
    return ctx


# ------------------------------------------------------------------ 策略解析

def test_policy_from_raw_defaults_and_invalid():
    p = policy_from_raw(None)
    assert p.enabled and not p.allow_outside_workspace and p.allow_network
    assert p.timeout_seconds == 60 and p.deny_patterns == []

    p2 = policy_from_raw({"enabled": False, "allow_outside_workspace": True,
                          "allow_network": False, "timeout_seconds": "9999",
                          "deny_patterns": ["git push", "", 42]})
    assert p2.enabled is False and p2.allow_outside_workspace is True
    assert p2.timeout_seconds == 300            # 夹到上限
    assert p2.deny_patterns == ["git push"]     # 非字符串项被丢弃


def test_policy_roundtrip_and_timeout_clamp():
    p = SandboxPolicy(enabled=True, timeout_seconds=30, deny_patterns=["rm -rf"])
    again = policy_from_raw(policy_to_raw(p))
    assert again.timeout_seconds == 30 and again.deny_patterns == ["rm -rf"]
    assert p.command_timeout(None) == 30
    assert p.command_timeout(999) == 30         # 不超过沙箱上限
    assert p.command_timeout(5) == 5


# ------------------------------------------------------------------ 路径分级

def test_classify_path_inside_outside_blocked(ws, home):
    inside = ws / "a.txt"
    assert classify_path(ws, "a.txt", home)[0] == INSIDE
    assert classify_path(ws, str(inside), home)[0] == INSIDE
    assert classify_path(ws, "../outside.txt", home)[0] == OUTSIDE
    assert classify_path(ws, str(home / ".ssh" / "id_rsa"), home)[0] == BLOCKED
    assert classify_path(ws, str(home / ".yinluxs" / "credentials.json"), home)[0] == BLOCKED


def test_classify_for_write_blocks_sensitive_parent(ws, home):
    # 往敏感目录里写文件必须拦下(不能靠父目录绕过)
    assert classify_for_write(ws, str(home / ".ssh" / "config"), home)[0] == BLOCKED
    assert classify_for_write(ws, "b.txt", home)[0] == INSIDE


def test_sensitive_roots_include_credentials(home):
    roots = [str(r) for r in sensitive_roots(home)]
    assert any(".yinluxs" in r for r in roots)
    assert any(".ssh" in r for r in roots)


# ------------------------------------------------------------------ 命令管控

def test_check_command_deny_patterns_and_network():
    policy = SandboxPolicy(deny_patterns=["git push"])
    assert check_command(policy, "git push origin main")[0] == SB_REJECT
    assert check_command(policy, "git status")[0] == SB_ALLOW

    no_net = SandboxPolicy(allow_network=False)
    level, why = check_command(no_net, "curl https://x")
    assert level == SB_REJECT and "网络" in why
    assert check_command(no_net, "python a.py")[0] == SB_ALLOW
    assert check_command(SandboxPolicy(allow_network=True), "curl https://x")[0] == SB_ALLOW
    # 沙箱关闭时不拦网络
    assert check_command(SandboxPolicy(enabled=False, allow_network=False),
                         "curl https://x")[0] == SB_ALLOW


# ------------------------------------------------ "两层都扫不到"那一类(v0.9.129)
# 命令层只看命令行,脚本内容层只读得到落成文件的那份脚本。这一类补的是从两层之间
# 漏过去的两种写法:**起一个不等它的进程**、**把 base64 交给解释器**。
# 只 ASK 不 REJECT —— 隐藏窗口跑个东西可能是正当需求,而这一层不是防火墙。

STEALTH_ON = SandboxPolicy(enabled=True)


@pytest.mark.parametrize("cmd", [
    'powershell -Command "Start-Process evil.exe -WindowStyle Hidden"',
    "Start-Process -FilePath node -ArgumentList server.js",
    "Start-Job -ScriptBlock { Remove-Item -Recurse }",
    'cmd /c start "" notepad.exe',
    "start /b watch.bat",
    "wscript payload.vbs",
    "mshta http://x/a.hta",
    "powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAKQAgAFgAeAA=",
])
def test_stealth_launches_upgrade_to_ask(cmd):
    level, why = check_command(STEALTH_ON, cmd)
    assert level == SB_ASK, cmd
    assert why, "ASK 必须带一句具体原因,不能只说「沙箱觉得要问」"


@pytest.mark.parametrize("cmd", [
    "npm start",                      # 最高频的误伤候选:段的第二个词才是 start
    "yarn dev && npm run start",
    "docker start mydb",
    'git commit -m "start the rewrite"',
    "python -c \"print(sum(range(10)))\"",   # 刻意不收:见函数上的注释
    "powershell -ExecutionPolicy Bypass -File build.ps1",  # 没有长 base64 那一段
    "python a.py",
    "ls -la",
])
def test_stealth_scan_does_not_nag_ordinary_commands(cmd):
    assert check_command(STEALTH_ON, cmd)[0] == SB_ALLOW, cmd


def test_stealth_is_silent_when_the_sandbox_is_off():
    """整条命令层是沙箱的一部分:关掉沙箱就不该由这一类开口(与网络那条同一立场)。"""
    assert check_command(SandboxPolicy(enabled=False),
                         'cmd /c start "" notepad.exe')[0] == SB_ALLOW


def test_stealth_reason_names_what_we_cannot_see():
    """回话要指名"看不见的是什么",否则用户看到的只是一句吓人的"违规"。"""
    _level, why = check_command(STEALTH_ON, "Start-Process evil.exe")
    assert "不等它结束" in why and ("超时" in why or "输出" in why)
    _level, why2 = check_command(STEALTH_ON,
                                 "powershell -enc " + "QQBFAFgA" * 4 + "AA==")
    assert "base64" in why2


def test_stealth_helper_returns_none_for_clean_commands():
    """直接问那只探针:门在不在都靠它,它自己坏掉时上面那批断言会一起变空转。"""
    assert command_hits_stealth("npm start") is None
    assert command_hits_stealth("") is None
    assert "不等它结束" in command_hits_stealth("wscript a.vbs")


def test_command_hits_network_variants():
    assert command_hits_network("curl -s http://x") == "curl"
    assert command_hits_network("C:\\Tools\\wget.exe http://x") == "C:\\Tools\\wget.exe"
    assert command_hits_network("echo curl") is None       # 只是回显,不算调用
    assert command_hits_network("python curl.py") is None
    assert command_hits_network("git clone https://x && nc -l 80") == "nc"


def test_command_touches_outside_path(ws):
    assert command_touches_outside_path(SandboxPolicy(), ws, "python D:\\data\\x.py") == "D:\\data\\x.py"
    inside = f"{ws}\\a.py"
    assert command_touches_outside_path(SandboxPolicy(), ws, f"python {inside}") is None
    assert command_touches_outside_path(
        SandboxPolicy(allow_outside_workspace=True), ws, "python D:\\x.py") is None


# ------------------------------------------------------------------ 状态

def test_state_approve_and_reset(ws):
    state = SandboxState(SandboxPolicy(), ws)
    outside = Path("D:/data") if not ws.drive else ws.parent / "data"
    assert not state.path_allowed(outside)
    state.approve_path(outside)
    assert state.path_allowed(outside)
    state.new_turn()
    assert not state.path_allowed(outside)


def test_state_describe_mentions_switch(ws):
    assert "沙箱开启" in SandboxState(SandboxPolicy(), ws).describe()
    assert "关闭" in SandboxState(SandboxPolicy(enabled=False), ws).describe()


# ------------------------------------------------------------------ 工具层

def test_resolve_path_allows_inside_and_blocks_sensitive(ws, home, monkeypatch):
    from minicode.tools.base import resolve_path
    ctx = _ctx(ws)
    assert resolve_path(ctx, "sub/a.txt") == (ws / "sub/a.txt").resolve()

    monkeypatch.setattr("minicode.sandbox.SENSITIVE_DIRS", (".ssh",))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(PathBlocked):
        resolve_path(ctx, str(home / ".ssh" / "id_rsa"))


def test_resolve_path_outside_needs_approval(ws):
    from minicode.tools.base import resolve_path
    ctx = _ctx(ws)
    target = ws.parent / "other.txt"
    with pytest.raises(PathOutsideWorkspace):
        resolve_path(ctx, str(target))
    ctx.sandbox.approve_path(target.resolve())
    assert resolve_path(ctx, str(target)) == target.resolve()      # 授权后放行


def test_resolve_path_outside_allowed_by_policy(ws):
    from minicode.tools.base import resolve_path
    ctx = _ctx(ws, SandboxPolicy(allow_outside_workspace=True))
    target = ws.parent / "other.txt"
    assert resolve_path(ctx, str(target)) == target.resolve()


@pytest.mark.slow      # 真等 5 s 超时:这条用例的耗时就是被测行为
def test_shell_timeout_clamped_by_sandbox(ws):
    from minicode.tools.shell import bash
    ctx = _ctx(ws, SandboxPolicy(timeout_seconds=1))
    out = bash(ctx, "sleep 5", timeout=60)
    assert "超时" in out          # 请求 60s,被沙箱压到 1 秒后终止


# ------------------------------------------------------------------ 权限门联动

def test_gate_asks_for_outside_path_even_in_accept_all(ws):
    state = SandboxState(SandboxPolicy(), ws)
    gate = PermissionGate(Config(permission_mode="accept_all"), sandbox=state)
    tool = _tool("read_file", read_only=True)

    assert gate.check(tool, {"path": "inside.txt"}) == ALLOW
    assert gate.check(tool, {"path": "../outside.txt"}) == ASK      # 边界不因 accept_all 放开

    gate.approve_outside(tool, {"path": "../outside.txt"})
    assert gate.check(tool, {"path": "../outside.txt"}) == ALLOW
    gate.new_turn()
    assert gate.check(tool, {"path": "../outside.txt"}) == ASK      # 回合结束授权失效


def test_gate_hard_rejects_sensitive_paths(ws, monkeypatch, tmp_path):
    fake_home = tmp_path / "h"
    (fake_home / ".ssh").mkdir(parents=True)
    monkeypatch.setattr("minicode.sandbox.SENSITIVE_DIRS", (".ssh",))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))

    state = SandboxState(SandboxPolicy(allow_outside_workspace=True), ws)
    gate = PermissionGate(Config(permission_mode="accept_all"), sandbox=state)
    tool = _tool("write_file")
    target = str(fake_home / ".ssh" / "authorized_keys")
    assert gate.check(tool, {"path": target}) == REJECT
    gate.approve_outside(tool, {"path": target})
    assert gate.check(tool, {"path": target}) == REJECT             # 授权也不能越过红线


def test_gate_rejects_denied_network_command(ws):
    state = SandboxState(SandboxPolicy(allow_network=False), ws)
    gate = PermissionGate(Config(), sandbox=state)
    tool = _tool("bash")
    assert gate.check(tool, {"command": "curl http://x"}) == REJECT
    assert gate.check(tool, {"command": "echo hi"}) == ASK          # 常规命令照旧走确认


def test_cli_confirm_denies_on_missing_stdin(monkeypatch):
    """非交互场景(stdin 关闭)下确认框不能崩,应按拒绝处理。"""
    from pathlib import Path
    from minicode.config import Config as Cfg
    from minicode.ui import TerminalUI

    ui = TerminalUI(Cfg(), Path.cwd())

    def boom(*a, **kw):
        raise EOFError

    monkeypatch.setattr(ui.console, "input", boom)
    tool = _tool("read_file")
    assert ui.confirm_tool(tool, {"path": "x"}) == "no"


# ------------------------------------------------------------ 会话内记住批准(v0.9.43)

def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


def test_approve_path_turn_vs_session(tmp_path):
    """「允许」只管本回合;「总是允许」跨回合,但换工作区一起清。"""
    st = SandboxState(SandboxPolicy(), _ws(tmp_path))
    p = tmp_path / "elsewhere" / "note.txt"
    st.approve_path(p)                            # 回合级
    assert st.path_allowed(p)
    st.new_turn()
    assert not st.path_allowed(p)                 # 新回合:忘了
    st.approve_path(p, session=True)              # 会话级
    st.new_turn()
    assert st.path_allowed(p)                     # 还在
    st.retarget(_ws(tmp_path) / "ws2")
    assert not st.path_allowed(p)                 # 换工作区:也清


def test_session_approved_not_cleared_by_new_turn_but_still_gated(tmp_path):
    """new_turn 只清回合级集合;敏感路径判定照旧,会话级不越 BLOCKED 红线。"""
    st = SandboxState(SandboxPolicy(), _ws(tmp_path))
    outside = tmp_path / "docs"
    st.approve_path(outside, session=True)
    st.new_turn()
    assert st.path_allowed(outside)
    # 凭据/敏感目录另有 BLOCKED 门,approve_path 改变不了 classify 的结论
    kind, _ = classify_path(_ws(tmp_path), str(tmp_path / "elsewhere" / "x"),
                            home=tmp_path)
    assert kind == OUTSIDE


def test_describe_mentions_session_paths(tmp_path):
    st = SandboxState(SandboxPolicy(), _ws(tmp_path))
    assert "本会话已记住" not in st.describe()
    st.approve_path(tmp_path / "a", session=True)
    st.approve_path(tmp_path / "b", session=True)
    assert "本会话已记住 2 个工作区外路径" in st.describe()


# --------------------------------------------------------------- `.git` 这一层
#
# 写 `.git/hooks/pre-commit` 等于给以后每次 git 操作装一个**本机自动执行的入口** ——
# 一段网页里的话("先加个提交前检查吧")就能骗出来,而且它从此一直在那儿。
# 所以这一层与凭据文件同一心智模型:不参与权限档位、不被项目白名单批、
# 也不因「本轮总是允许」而连续静默放行。这四条每个都有一根针,少一条就是话写白了
# 但门没关上。

@pytest.mark.parametrize("mode", ["confirm", "accept_edits", "accept_all", "full"])
def test_git_writes_ask_in_every_mode(ws, mode):
    """.git 里面:四种档一律 ASK,包括专门用来"少问几次"的 accept_all 与 full。"""
    gate = PermissionGate(Config(permission_mode=mode), sandbox=SandboxState(SandboxPolicy(), ws))
    assert gate.check(_tool("write_file"), {"path": ".git/hooks/pre-commit"}) == ASK
    assert "钩子" in gate.ask_note, "点头之前要看得见这是什么量级的事"
    assert gate.check(_tool("edit_file"), {"path": ".git/config"}) == ASK
    assert gate.check(_tool("write_file"), {"path": "sub/.git/HEAD"}) == ASK


def test_worktree_pointer_file_counts_too(ws):
    """worktree / submodule 的 `.git` 是个**文件**(`gitdir: ...`),改它同样要点头。"""
    assert is_git_internal(ws / ".git") is True
    assert is_git_internal(ws / ".git" / "hooks" / "pre-commit") is True


@pytest.mark.parametrize("path", [".gitignore", ".gitattributes", ".github/workflows/ci.yml",
                                  "reports/my.git.txt", "gitlab/.gitlab-ci.yml"])
def test_similar_names_are_not_the_git_dir(ws, path):
    """只认整段叫 `.git` 的组件。误伤正常文件,用户就会去关整道门 —— 那不安全。"""
    gate = PermissionGate(Config(permission_mode="accept_edits"),
                          sandbox=SandboxState(SandboxPolicy(), ws))
    assert gate.check(_tool("write_file"), {"path": path}) == ALLOW


def test_reading_repo_history_stays_allowed(ws):
    """这一层只拦**写**:看提交历史、读 HEAD 是日常需求,读也拦等于自废。"""
    gate = PermissionGate(Config(permission_mode="confirm"),
                          sandbox=SandboxState(SandboxPolicy(), ws))
    assert gate.check(_tool("read_file", read_only=True), {"path": ".git/HEAD"}) == ALLOW


def test_turn_always_allow_does_not_silence_git(ws):
    gate = PermissionGate(Config(permission_mode="confirm"),
                          sandbox=SandboxState(SandboxPolicy(), ws))
    tool = _tool("write_file")
    assert gate.check(tool, {"path": ".git/hooks/pre-commit"}) == ASK
    gate.approve_for_turn("write_file")          # 人点了「本轮总是允许」
    assert gate.check(tool, {"path": ".git/hooks/pre-commit"}) == ASK


def test_project_allowlist_cannot_preapprove_git(ws):
    """白名单能批「这个项目的 write_file」,但批不进 `.git` —— 那条路不走白名单。"""
    from minicode import permspec
    gate = PermissionGate(Config(permission_mode="confirm"),
                          sandbox=SandboxState(SandboxPolicy(), ws))
    rules, problems = permspec.parse_rules(
        [{"tool": "write_file", "path_prefix": ".git/"}], {"write_file"})
    assert not problems, problems
    spec = permspec.Spec(workspace=ws, sha="ab" * 32, rules=rules, trusted=True)
    assert spec.effective, "白名单没生效的话这条针就成了空断言"
    gate.project = spec
    assert gate.check(_tool("write_file"), {"path": ".git/hooks/pre-commit"}) == ASK
    assert gate.project_allow_reason == "", "命中白名单才算被绕过 —— 这句要能反证它没被批走"


@pytest.mark.parametrize("command,expect", [
    ("echo x > .git/hooks/pre-commit", ASK),
    ("cp /tmp/evil .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit", ASK),
    ("rm -rf .git", ASK),
    ("python -c \"open('.git/hooks/pre-commit','w')\"", ASK),
    ("git commit -m fix", ALLOW),        # git 自己写 .git 是它的本职工作
    ("git add -A && git commit -m wip", ALLOW),
    ("pytest -q", ALLOW),
])
def test_bash_git_targets_ask_but_git_itself_does_not(ws, command, expect):
    """full 档下验:命令行里指向 `.git` 的写法要问,`git` 子命令不问。

    把 `git commit` 也拦下等于把「帮我提交一下」这条日常用法打死 —— 那条一旦疼,
    整道门就会被关掉,所以这一半与前面那一半同样重要。
    """
    gate = PermissionGate(Config(permission_mode="full"),
                          sandbox=SandboxState(SandboxPolicy(), ws))
    assert gate.check(_tool("bash"), {"command": command}) == expect


def test_script_layer_flags_git(ws):
    """脚本内容里伸手 `.git` 也问(py_script 与 `bash("python x.py")` 共用这一层)。"""
    code = "import pathlib\npathlib.Path('.git/hooks/pre-commit').write_text('x')\n"
    hits = scan_script_text(code, ws)
    assert hits["git"], "脚本扫描没把 .git 认出来"
    level, why = script_hits(SandboxPolicy(enabled=True), code,
                             touched_external=False, workspace=ws)
    assert level == "ask" and ".git" in why
    assert "钩子" in script_hits(SandboxPolicy(enabled=True), code,
                                 touched_external=False, workspace=ws)[1]
