import pytest

from minicode.permission import ALLOW, ASK, REJECT, PermissionGate, check_disaster_command
from minicode.tools.registry import build_default_registry


@pytest.fixture
def registry():
    return {t.name: t for t in build_default_registry()}


# ------------------------------------------------------------ 灾难命令

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr ~",
    "rm -rf .",
    "rm -r ..",
    "rm -rf *",
    "rm --recursive --force $HOME",
    "rm -rf node_modules && rm -rf /",
    "format c:",
    "mkfs.ext4 /dev/sdb1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "Remove-Item C:\\Windows -Recurse -Force",
    "del /s /q C:\\",
])
def test_disaster_blocked(cmd):
    assert check_disaster_command(cmd) is not None


@pytest.mark.parametrize("cmd", [
    "rm file.txt",
    "rm -rf node_modules",
    "rm -r src",
    "ls -la",
    "git status",
    "echo rm -rf /",
    "pip install requests",
    "",
])
def test_disaster_not_triggered(cmd):
    assert check_disaster_command(cmd) is None


# ------------------------------------------------------------ 权限门

def test_read_only_always_allowed(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["read_file"], {"path": "a"}) == ALLOW
    assert gate.check(registry["grep"], {"pattern": "x"}) == ALLOW


def test_write_asks_in_confirm_mode(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["write_file"], {"path": "a", "content": "b"}) == ASK


def test_turn_approval(config, registry):
    gate = PermissionGate(config)
    assert gate.check(registry["bash"], {"command": "ls"}) == ASK
    gate.approve_for_turn("bash")
    assert gate.check(registry["bash"], {"command": "ls"}) == ALLOW
    gate.new_turn()
    assert gate.check(registry["bash"], {"command": "ls"}) == ASK


def test_accept_all_mode(config, registry):
    config.permission_mode = "accept_all"
    gate = PermissionGate(config)
    assert gate.check(registry["write_file"], {"path": "a", "content": "b"}) == ALLOW


def test_disaster_reject_overrides_accept_all(config, registry):
    config.permission_mode = "accept_all"
    gate = PermissionGate(config)
    assert gate.check(registry["bash"], {"command": "rm -rf /"}) == REJECT


# ------------------------------------------------------------ 会话内记住(v0.9.43)

def test_approve_outside_session_flag_reaches_sandbox(tmp_path, config):
    """点「总是允许」时 session 标志要传到沙箱:路径进 session_approved;
    点「允许」(默认)只进回合级集合。"""
    from minicode.sandbox import SandboxPolicy, SandboxState
    from minicode.tools.base import Tool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere" / "doc.txt"
    tool = Tool(name="read_file", description="d",
                parameters={"type": "object"}, handler=lambda ctx, **kw: "ok",
                read_only=True)
    target = str(outside.resolve())

    gate = PermissionGate(config, sandbox=SandboxState(SandboxPolicy(), ws))
    gate.approve_outside(tool, {"path": target}, session=True)
    assert target in gate.sandbox.session_approved
    assert gate.sandbox.path_allowed(outside.resolve())

    gate2 = PermissionGate(config, sandbox=SandboxState(SandboxPolicy(), ws))
    gate2.approve_outside(tool, {"path": target})          # 默认仍是回合级
    assert target not in gate2.sandbox.session_approved
    assert gate2.sandbox.path_allowed(outside.resolve())   # 但本回合放行


# ---------------------------------------------------- 只读预演(并发批次的筛子)
# §6 50(b) 那块卡点:派生一批要并发跑的工具之前,得能问「这一步会不会问人」。
# 这一族针钉的不是"有没有这个函数",而是两件会静默坏掉的事:
# ① 预演与真判**不许有两套结论**(漂移的表现是该问的那一步直接跑了,不报错);
# ② 预演**一个字都不许留痕**(留痕的表现是安全日志与界面各多一行
#    "按项目白名单放行",而那一步根本还没跑)。

def _reports(gate):
    return (gate.ask_note, gate.ask_kind, gate.project_allow_reason,
            gate.reject_reason)


def _gate(tmp_path, mode="confirm"):
    from minicode.config import Config
    from minicode.sandbox import SandboxPolicy, SandboxState
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws, PermissionGate(Config(permission_mode=mode),
                              sandbox=SandboxState(SandboxPolicy(), ws),
                              workspace=ws)


# ------------------------------------------------ 命令层「两层都扫不到」那一类(v0.9.129)

def test_stealth_command_asks_and_says_which_shape(tmp_path, registry):
    """门要开口,而且要把**是哪一种**说进确认框:以前 `sandbox_command` 只回一句
    笼统的"沙箱觉得该问",而"命中禁配规则"与"起了个不等它的进程"是两件事。"""
    _ws, gate = _gate(tmp_path)
    bash = registry["bash"]
    assert gate.check(bash, {"command": 'cmd /c start "" notepad.exe'}) == ASK
    assert "不等它结束" in gate.ask_note, gate.ask_note
    assert gate.ask_kind == "sandbox_command"


def test_stealth_command_still_yields_to_full(tmp_path, registry):
    """立场钉住:这一类是**沙箱命令层**的 ASK,不是红线 —— `full` 档照样放行。
    把它升成"任何档都要问"要先改的是这一句,而不是偷偷在函数里加个 if。"""
    _ws, gate = _gate(tmp_path, mode="full")
    assert gate.check(registry["bash"],
                      {"command": "Start-Process evil.exe"}) != ASK


def test_npm_start_does_not_ask(tmp_path, registry):
    """反面对照:收进段首而不是子串,`npm start` 必须一步都不问。
    没有这条,上面那组"该问的问了"可能只是"什么都拦"。"""
    _ws, gate = _gate(tmp_path, mode="accept_all")
    assert gate.check(registry["bash"], {"command": "npm start"}) != ASK


def test_probe_leaves_the_report_fields_untouched(tmp_path, registry):
    ws, gate = _gate(tmp_path)
    # 先让真 check 留下一份 ASK 的痕迹,当作"上一次判断"的基线
    gate.check(registry["write_file"], {"path": "a.txt", "content": "b"})
    baseline = _reports(gate)
    assert baseline[1] == "mode", baseline          # 基线确实带着 ask_kind

    assert gate.would_ask(registry["bash"], {"command": "ls"}) is True
    assert _reports(gate) == baseline, "预演把上一次的报告字段改了"


def test_probe_agrees_with_the_real_decision(tmp_path, registry, monkeypatch):
    """同一批样本:预演的结论必须与紧随其后的真 `check()` 逐条相等。"""
    ws, gate = _gate(tmp_path)
    cases = [
        ("read_file", {"path": "a"}),                       # 只读放行
        ("grep", {"pattern": "x"}),                         # 只读放行
        ("write_file", {"path": "a", "content": "b"}),      # 该问
        ("bash", {"command": "ls"}),                        # 该问
        ("bash", {"command": "rm -rf /"}),                  # 硬拒(不是问)
        ("read_file", {"path": str((ws.parent / "out").resolve())}),   # 越界该问
    ]
    for name, args in cases:
        before = _reports(gate)
        predicted = gate.would_ask(registry[name], args)
        assert _reports(gate) == before, f"{name} 的预演留痕了"
        assert predicted == (gate.check(registry[name], args) == ASK), (name, args)


def test_probe_does_not_consume_the_external_flag(tmp_path, registry):
    """外部内容升级门的标志是回合级的:预演不能把它"用掉"。"""
    ws, gate = _gate(tmp_path)
    gate.note_external("web_fetch")
    assert gate.would_ask(registry["write_file"],
                          {"path": "a.txt", "content": "b"}) is True
    assert gate.touched_external is True


def test_probe_does_not_approve_anything(tmp_path, registry):
    """预演不是"问过一遍点了允许":它之后真判还得照样问。"""
    ws, gate = _gate(tmp_path)
    assert gate.would_ask(registry["bash"], {"command": "ls"}) is True
    assert gate.check(registry["bash"], {"command": "ls"}) == ASK


def test_full_mode_probe_says_no_ask(tmp_path, registry):
    """筛子要认档位:full 档里工作区的写不该被拖去串行。"""
    ws, gate = _gate(tmp_path, mode="full")
    assert gate.would_ask(registry["write_file"],
                          {"path": "a.txt", "content": "b"}) is False


def test_project_allow_reason_is_not_written_by_a_probe(tmp_path, registry,
                                                        monkeypatch):
    """白名单那一步是**放行**而不是问(所以 would_ask=False),但真 `check()` 会写
    `project_allow_reason`,Agent 拿它去安全日志记一行、往界面推一条 info。
    预演要是把它写了,就等于给一步还没跑过的操作记上"按项目白名单放行"——
    凭空多一条审计,而且顺序还可能反过来。"""
    from minicode import permspec
    ws, gate = _gate(tmp_path)
    monkeypatch.setattr(permspec, "trust_path", lambda: tmp_path / "trust.json")
    (ws / permspec.PERMISSIONS_FILE).write_text(
        '{"allow": [{"tool": "bash", "command_prefix": "pytest"}]}',
        encoding="utf-8")
    spec = permspec.load(ws, {t.name for t in registry.values()})
    assert spec.rules, spec.reason
    permspec.trust(ws, spec.sha, len(spec.rules))
    gate.project = permspec.load(ws, {t.name for t in registry.values()})

    args = {"command": "pytest -q"}
    assert gate.would_ask(registry["bash"], args) is False
    assert gate.project_allow_reason == "", "预演写下了白名单理由"
    # 真判则要留痕:同一件事,预演不留、真跑必留
    assert gate.check(registry["bash"], args) == ALLOW
    assert "第 1 条" in gate.project_allow_reason
