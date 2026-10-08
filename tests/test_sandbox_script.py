"""沙箱收口(v0.9.39):要跑的脚本先扫内容、子进程环境摘密钥、输出限长。

这一层的定位写在 `sandbox.KNOWN_LIMITS` 里:**不是隔离**,是"把可疑的那几行
摆到确认框里"。所以测试同时钉住两件事 ——
① 可疑内容一定被升到确认(哪怕 full 档);
② 干净脚本、普通联网命令**不被骚扰**,且自述不许吹成"已断网"。
"""
import subprocess
import time
from io import BytesIO
from pathlib import Path

import pytest

from minicode.config import Config
from minicode.permission import ALLOW, ASK, REJECT, PermissionGate
from minicode.sandbox import (
    KNOWN_LIMITS, SandboxPolicy, SandboxState,
    command_touches_outside_path, scan_script_text, script_hits,
    script_risk_note, scrub_env,
)
from minicode.tools import shell
from minicode.tools.base import Tool, ToolContext


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _tool(name, read_only=False, props=None):
    return Tool(name=name, description="d",
                parameters={"type": "object", "properties": props or {}},
                handler=lambda ctx, **kw: "ok", read_only=read_only)


BASH = _tool("bash", props={"command": {"type": "string"}})
PYSCRIPT = _tool("py_script", props={"path": {"type": "string"}})


def _gate(root, mode="full", **policy_kw):
    cfg = Config()
    cfg.permission_mode = mode
    return PermissionGate(cfg, sandbox=SandboxState(SandboxPolicy(**policy_kw), root))


def _write(root, name, text):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


CLEAN = """
import json
data = json.load(open("notes.json", encoding="utf-8"))
print(len(data))
"""

CRED = """
from pathlib import Path
key = Path.home() / ".ssh" / "id_rsa"
print(open(key).read())
"""

NET = """
import requests
print(requests.post("https://example.com", json={"a": 1}).status_code)
"""


# ------------------------------------------------------------------ 内容扫描

def test_clean_script_has_nothing_to_report():
    hits = scan_script_text(CLEAN)
    assert not (hits["credential"] or hits["escape"] or hits["egress"])
    assert script_risk_note(hits) == ""


def test_scan_reports_the_line_it_hit_not_just_a_verdict():
    """确认框要能让人一眼判断"是真读还是提了一句" —— 只报"有风险"等于没报。"""
    hits = scan_script_text(CRED)
    assert hits["credential"], hits
    row = hits["credential"][0]
    assert row["line"] == 3 and ".ssh" in row["code"]   # CRED 首行是空行
    note = script_risk_note(hits, source="notes.py")
    assert "notes.py" in note and "第 3 行" in note and "可能只是提到" in note


def test_home_dir_access_counts_as_leaving_the_workspace():
    hits = scan_script_text("import os\np = os.path.expanduser('~')\nprint(p)\n")
    assert hits["escape"], hits
    assert not hits["credential"]          # 只是主目录,不是密钥


def test_paths_inside_the_workspace_are_not_scared_of():
    """转换类脚本常写死工作区内的产物路径,这类不该报"走出工作区"。"""
    text = 'out = "notes/out.pptx"\nopen(r"C:\\\\Somewhere\\\\Else\\\\x.txt").close()\n'
    inside = scan_script_text('open("D:\\\\game\\\\ws\\\\a.pptx","w")\n',
                              workspace=Path("D:/game/ws"))
    assert not inside["escape"], inside
    outside = scan_script_text(text, workspace=Path("D:/game/ws"))
    assert outside["escape"] and "工作区外" in outside["escape"][0]["kind"]
    assert not scan_script_text(text)["escape"]   # 不知道围栏在哪就不瞎判


def test_scan_only_reads_the_head_and_says_so():
    big = CLEAN + ("# 注释\n" * 40000) + NET
    hits = scan_script_text(big)
    assert hits["truncated"] is True
    assert "KB" in script_risk_note(hits)


# ------------------------------------------------------------------ 决策口径

def test_forbidden_network_rejects_and_admits_it_is_not_a_firewall():
    level, why = script_hits(SandboxPolicy(allow_network=False), NET,
                             touched_external=False)
    assert level == REJECT
    assert "不是操作系统防火墙" in why          # 不许把名字级门禁说成断网
    assert KNOWN_LIMITS["network"] in why
    # 同一份脚本,允许联网时不拦(这层只在开关关掉时才升级成拒绝)
    assert script_hits(SandboxPolicy(), NET, touched_external=False)[0] == ALLOW


def test_credential_lines_ask_even_in_full_mode(ws):
    _write(ws, "creep.py", CRED)
    gate = _gate(ws, "full")
    assert gate.check(PYSCRIPT, {"path": "creep.py"}) == ASK
    assert "第 3 行" in gate.ask_note and "id_rsa" in gate.ask_note
    assert "不给子进程做隔离" in gate.ask_note     # 自述不许夸成"挡住了"


def test_benign_script_still_runs_silently_in_full_mode(ws):
    _write(ws, "ok.py", CLEAN)
    assert _gate(ws, "full").check(PYSCRIPT, {"path": "ok.py"}) == ALLOW


def test_bash_running_a_script_is_not_a_side_door(ws):
    """`bash("python creep.py")` 绕不过去:同一份内容,同一道门。"""
    _write(ws, "creep.py", CRED)
    assert _gate(ws, "full").check(BASH, {"command": "python creep.py"}) == ASK
    assert _gate(ws, "full").check(BASH, {"command": "python -m pytest -q"}) == ALLOW


def test_egress_only_escalates_after_touching_external_content(ws):
    _write(ws, "net.py", NET)
    gate = _gate(ws, "full")
    assert gate.check(PYSCRIPT, {"path": "net.py"}) == ALLOW   # 没接触外部内容
    gate.note_external("web_crawl", {"url": "http://e"})
    assert gate.check(PYSCRIPT, {"path": "net.py"}) == ASK
    assert "外传出口" in gate.ask_note


def test_sandbox_disabled_does_not_pretend_to_scan(ws):
    _write(ws, "creep.py", CRED)
    assert _gate(ws, "full", enabled=False).check(PYSCRIPT, {"path": "creep.py"}) == ALLOW


def test_unreadable_script_is_not_a_reason_to_block(ws):
    """路径合法但读不出来(占位符、二进制):不因此拒,交给工具自己报错。"""
    (ws / "ghost.py").write_bytes(b"\x00\x01not a script")
    assert _gate(ws, "full").check(PYSCRIPT, {"path": "ghost.py"}) == ALLOW
    assert _gate(ws, "full").check(PYSCRIPT, {"path": "missing.py"}) == ALLOW


# ------------------------------------------------------------------ 工作区外路径

def test_url_in_command_is_not_mistaken_for_a_drive_path():
    """接线时真撞出来的坑:`http://x` 里的 `p://x` 曾被当成盘符路径。"""
    policy = SandboxPolicy()
    for cmd in ("curl https://example.com/a", "python dl.py https://x/y"):
        assert command_touches_outside_path(policy, Path("D:/ws"), cmd) is None, cmd


@pytest.mark.parametrize("cmd", [
    "cat ~/.ssh/id_rsa", "type %USERPROFILE%\\notes.txt",
    "python D:\\elsewhere\\x.py", "ls $HOME/docs",
])
def test_outside_paths_in_bash_need_confirmation_even_in_full_mode(cmd, ws):
    assert _gate(ws, "full").check(BASH, {"command": cmd}) == ASK


def test_allow_outside_workspace_takes_the_bash_gate_off(ws):
    gate = _gate(ws, "full", allow_outside_workspace=True)
    assert gate.check(BASH, {"command": "python D:\\elsewhere\\x.py"}) == ALLOW


# ------------------------------------------------------------------ 环境剥离

def test_scrub_drops_credential_shaped_names_and_keeps_the_rest():
    env = {
        "PATH": "C:\\Windows", "LOCALAPPDATA": "C:\\Users\\me\\AppData\\Local",
        "YINLUXS_LLM_MODEL": "deepseek",       # 应用自己的非密钥项要留着
        "YINLUXS_LLM_API_KEY": "sk-secret",    # 子进程没资格拿走 Key
        "OPENAI_API_KEY": "sk-x", "GH_TOKEN": "ghp", "AWS_SECRET_ACCESS_KEY": "z",
        "DB_PASSWORD": "p", "SESSION_COOKIE": "c",
    }
    kept, dropped = scrub_env(env)
    assert set(kept) == {"PATH", "LOCALAPPDATA", "YINLUXS_LLM_MODEL"}
    assert "YINLUXS_LLM_API_KEY" in dropped and len(dropped) == 6


def test_policy_roundtrip_keeps_the_switch():
    from minicode.sandbox import policy_from_raw, policy_to_raw
    again = policy_from_raw(policy_to_raw(SandboxPolicy(strip_env_secrets=False)))
    assert again.strip_env_secrets is False
    assert policy_from_raw({}).strip_env_secrets is True   # 默认开


# ------------------------------------------------------------------ 子进程真的看不到 Key

def _fake_children(monkeypatch, out=b"ok", err=b"", ret=0, wait_raises=False):
    """把 shell 起子进程的**两种方式**都换成"只记账、不真跑"的桩。

    v0.9.91 起前台 `bash` 走 `Popen` + 两条管道各一个读线程(边跑边读才看得到进度);
    v0.9.135 起 `py_script` 也改成 `Popen` —— 它要在进程起来之后立刻挂进 Job Object,
    `subprocess.run` 里没有那个时机(等它返回时进程都已经退完了,"终止整棵树"这件事
    就发生在返回之后)。只换一种会让另一种的桩静默失效 —— 表现是"环境没摘干净"这类
    断言其实在真起进程,所以这里两个一起换。
    """
    seen = {}

    class FakeDone:
        returncode = ret
        stdout, stderr = out, err

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw.get("env"))
        return FakeDone()

    class FakeStream:
        def __init__(self, data):
            self._f = BytesIO(data)

        def readline(self):
            return self._f.readline()

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, env=kw.get("env"))
            self.stdout, self.stderr = FakeStream(out), FakeStream(err)
            self.pid = 4242
            self._dead = True       # 桩里的命令"一叫就完",不模拟还在跑
            self.returncode = ret   # 真 Popen 也是靠 poll/wait 填这个字段

        def poll(self):
            return self.returncode if self._dead else None

        def wait(self, timeout=None):
            if wait_raises:
                # 演"这段时间它确实还在跑":真 wait() 会阻塞,桩不睡就变成空转几千圈
                time.sleep(min(timeout or 0, 0.2))
                raise subprocess.TimeoutExpired(cmd="whatever", timeout=timeout)
            self._dead = True
            return self.returncode

        def kill(self):
            self._dead = True

        def communicate(self, timeout=None):
            # v0.9.135 之后 py_script 走的这一只:`run(timeout=)` 到点只杀直接子进程,
            # 换成 Popen + communicate 才能先挂 job 再超时收整棵树。
            if wait_raises:
                raise subprocess.TimeoutExpired(cmd="whatever", timeout=timeout)
            return out, err

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    monkeypatch.setattr(shell.subprocess, "Popen", FakePopen)
    return seen


def _capture_env(monkeypatch, root, policy, call):
    """验证交给子进程的那份 env(两种起法都记账,见 `_fake_children`)。"""
    seen = _fake_children(monkeypatch)
    monkeypatch.setenv("MY_PROBE_API_KEY", "sk-leak")
    monkeypatch.setenv("HARMLESS_FLAG", "1")
    ctx = ToolContext(workspace=root, config=Config())
    ctx.sandbox = SandboxState(policy, root)
    return seen, call(ctx)


def test_bash_child_env_has_no_credential_vars(monkeypatch, ws):
    seen, out = _capture_env(monkeypatch, ws, SandboxPolicy(),
                             lambda c: shell.bash(c, "echo hi"))
    assert "MY_PROBE_API_KEY" not in seen["env"]
    assert "HARMLESS_FLAG" in seen["env"] and "PATH" in seen["env"]
    assert "MY_PROBE_API_KEY" in out          # 摘了什么要如实说


def test_py_script_child_env_follows_the_switch(monkeypatch, ws):
    _write(ws, "x.py", CLEAN)
    seen, out = _capture_env(monkeypatch, ws, SandboxPolicy(),
                             lambda c: shell.py_script(c, "x.py"))
    assert "MY_PROBE_API_KEY" not in seen["env"]
    assert "strip_env_secrets" in out         # 想知道怎么放开,答案就在提示里

    seen2, out2 = _capture_env(monkeypatch, ws,
                               SandboxPolicy(strip_env_secrets=False),
                               lambda c: shell.py_script(c, "x.py"))
    assert seen2["env"]["MY_PROBE_API_KEY"] == "sk-leak"
    assert "strip_env_secrets" not in out2    # 没摘就不该编一段提示


def test_no_sandbox_keeps_the_env_as_it_was(monkeypatch, ws):
    """没装沙箱(老配置/测试环境)时不该有第三种行为,更不该凭空报错。"""
    seen = _fake_children(monkeypatch)
    monkeypatch.setenv("MY_PROBE_API_KEY", "sk-leak")
    out = shell.bash(ToolContext(workspace=ws, config=Config()), "echo hi")
    assert seen["env"]["MY_PROBE_API_KEY"] == "sk-leak"
    assert "凭据" not in out


def test_bash_output_is_capped_and_says_how_much_was_dropped(monkeypatch, ws):
    _fake_children(monkeypatch, out=b"x" * 20000)
    out = shell.bash(ToolContext(workspace=ws, config=Config()), "produce lots")
    assert "输出过长" in out and "20000" in out
    assert len(out) < 20000


def test_timeout_message_still_arrives(monkeypatch, ws):
    _fake_children(monkeypatch, wait_raises=True)
    ctx = ToolContext(workspace=ws, config=Config())
    ctx.sandbox = SandboxState(SandboxPolicy(), ws)
    # timeout=1:桩每次都"还在跑",循环要熬到预算用完才算超时 —— 别让它真等默认那 60 秒
    assert "超时" in shell.bash(ctx, "sleep 999", timeout=1)


def test_describe_names_what_it_actually_does(ws):
    d = SandboxState(SandboxPolicy(allow_network=False), ws).describe()
    assert "禁网" in d and "不是防火墙" in d
    assert "摘密钥环境变量" in d
    assert "不是防火墙" not in SandboxState(SandboxPolicy(), ws).describe()
