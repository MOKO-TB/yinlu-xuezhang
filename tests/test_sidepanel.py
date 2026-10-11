# -*- coding: utf-8 -*-
"""侧边栏底座的桥层(v0.9.159):终端页的闸、扩展清单的转发、网页版的墙。

终端页是这轮唯一一个"能让本机起进程"的新口,所以它的闸有三支针:
沙箱硬闸不认人(REJECT 级谁来了都拒)、后台账只有一本(terminal_facts
看得见用户自己跑的那条)、网页版的墙(PublicFacade 没有这些名字 ——
访客不装代码也不跑命令)。
"""
from __future__ import annotations

import json


from minicode.config import Config, Provider
from minicode.sandbox import SandboxState, policy_from_raw
from minicode.ui import TerminalUI


def _config(sandbox_raw=None) -> Config:
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m",
                            models=[{"name": "m", "enabled": True}])],
        active_provider_id="t", context_window=8192,
        sandbox=sandbox_raw if sandbox_raw is not None else {"enabled": False})


def _bridge(tmp_path, sandbox_raw=None):
    from minicode.gui_bridge import Bridge
    cfg = _config(sandbox_raw)
    return Bridge(cfg, tmp_path, TerminalUI(cfg, tmp_path),
                  sandbox=SandboxState(policy_from_raw(cfg.sandbox), tmp_path))


# ------------------------------------------------------------- 终端页的闸

def test_hard_gate_rejects_denied_command_even_from_the_user(tmp_path):
    """沙箱硬闸不认人:deny 命中就是拒 —— 终端页不是绕开沙箱的门。"""
    b = _bridge(tmp_path, {"enabled": True,
                           "deny_patterns": ["^rm\\s+-rf"]})
    got = b.terminal_run("rm -rf C:/")
    assert not got["ok"] and "沙箱" in got["msg"]


def test_hard_gate_rejects_network_when_policy_says_off(tmp_path):
    b = _bridge(tmp_path, {"enabled": True, "allow_network": False})
    got = b.terminal_run("curl https://example.invalid")
    assert not got["ok"] and "网络" in got["msg"]


def test_user_command_lands_in_the_one_and_only_bg_ledger(tmp_path):
    """用户自己跑的命令进的是 `_bg_jobs` 那一本账 —— 「后台命令」面板与
    terminal_facts 看见的就是它。另立一本账,两处迟早对不上。"""
    from minicode.tools import shell
    b = _bridge(tmp_path)
    got = b.terminal_run("echo sidepanel-ledger-test")
    assert got["ok"] and got["id"], got
    try:
        facts = shell.terminal_facts()
        assert any(j["id"] == got["id"] for j in facts["jobs"]), \
            "用户跑的命令必须出现在同一本后台账里"
    finally:
        shell.user_kill(got["id"])


def test_empty_and_oversized_commands_are_refused(tmp_path):
    b = _bridge(tmp_path)
    assert not b.terminal_run("   ")["ok"]
    assert not b.terminal_run("x" * 5000)["ok"]


def test_kill_unknown_id_is_an_honest_no(tmp_path):
    b = _bridge(tmp_path)
    got = b.terminal_kill("j999999")
    assert not got["ok"] and "没有" in got["msg"]


# ------------------------------------------------- 终端坞:PowerShell 与 cd

def test_terminal_run_uses_powershell_and_carries_cwd(tmp_path):
    """坞的命令走 PowerShell 前缀 + 桥记的当前目录 —— `PS D:\...>` 那种体验
    归体验,沙箱硬闸在它前面一格不少。"""
    b = _bridge(tmp_path)
    got = b.terminal_run("echo sidepanel-ps-test")
    assert got["ok"] and got["cwd"] == str(tmp_path)
    try:
        from minicode.tools import shell
        with shell._bg_lock:
            job = shell._bg_jobs[got["id"]]
        assert job.command == "echo sidepanel-ps-test"
    finally:
        from minicode.tools import shell
        shell.user_kill(got["id"])


def test_cd_moves_inside_workspace_and_refuses_outside(tmp_path):
    """cd 是坞体验的核心:工作区内落账生效;**出围栏如实拒** —— 坞不为
    体验把写侧安全放宽一格。"""
    b = _bridge(tmp_path)
    (tmp_path / "sub").mkdir()
    r = b.terminal_run("cd sub")
    assert r["ok"] and r["moved"] and r["cwd"] == str(tmp_path / "sub")
    assert b._dock_cwd == tmp_path / "sub"
    r2 = b.terminal_run("cd ..")
    assert r2["ok"] and b._dock_cwd == tmp_path
    r3 = b.terminal_run("cd C:/Windows")
    assert not r3["ok"] and "工作区" in r3["msg"]
    r4 = b.terminal_run("cd 没这个目录")
    assert not r4["ok"] and "没有这个目录" in r4["msg"]
    assert b._dock_cwd == tmp_path, "失败的 cd 不许动当前目录"


def test_non_cd_commands_run_in_the_dock_cwd(tmp_path, monkeypatch):
    """非 cd 命令带着坞的当前目录进 Popen —— `_bg_start` 收到的 shim 就是证据。"""
    from minicode.tools import shell
    b = _bridge(tmp_path)
    (tmp_path / "sub").mkdir()
    b.terminal_run("cd sub")
    seen = {}
    real_start = shell._bg_start

    def spy(ctx, command, argv, env, creationflags, env_note):
        seen["workspace"] = str(ctx.workspace)
        seen["argv"] = argv
        return real_start(ctx, command, argv, env, creationflags, env_note)

    monkeypatch.setattr(shell, "_bg_start", spy)
    got = b.terminal_run("echo hi")
    assert got["ok"]
    assert seen["workspace"] == str(tmp_path / "sub"), \
        "cd 之后的命令必须跑在挪过去的目录里"
    assert seen["argv"][:2] == ["powershell", "-NoLogo"], \
        "坞的命令走 PowerShell 前缀(-NoLogo -NoProfile)"
    shell.user_kill(got["id"])


# ------------------------------------------------------------- 转发与网页版的墙

def test_facade_exposes_the_sidebar_methods():
    from minicode.gui_bridge import PublicApi
    for n in ("get_extensions", "git_overview", "git_status", "git_diff",
              "git_log", "git_stage", "git_unstage", "git_commit",
              "git_restore", "git_restore_snapshot", "file_read",
              "file_write", "terminal_run", "terminal_kill"):
        assert callable(getattr(PublicApi, n, None)), n


def test_web_facade_has_none_of_it():
    """访客不装扩展、不看文件、不跑命令 —— 这些名字一个都不进网页白名单。"""
    from minicode.web_profile import PublicFacade
    facade = PublicFacade(object())
    allowed = facade.allowed_methods()
    leaked = [n for n in ("get_extensions", "git_overview", "file_read",
                          "file_write", "terminal_run", "terminal_kill",
                          "git_stage", "git_commit") if n in allowed]
    assert not leaked, f"网页版白名单漏了:{leaked}"


def test_bridge_extensions_forward(tmp_path, monkeypatch):
    from minicode import extensions
    from minicode.gui_bridge import Bridge
    from minicode.ui import TerminalUI
    monkeypatch.setattr(extensions, "extension_root", lambda: tmp_path / "extensions")
    cfg = _config()
    b = Bridge(cfg, tmp_path, TerminalUI(cfg, tmp_path))
    got = b.get_extensions()
    assert got["ok"] and got["items"] == []
    (tmp_path / "extensions" / "db").mkdir(parents=True)
    (tmp_path / "extensions" / "db" / "manifest.json").write_text(
        json.dumps({"title": "库", "entry": "page.js"}), encoding="utf-8")
    (tmp_path / "extensions" / "db" / "page.js").write_text("// hi", encoding="utf-8")
    got2 = b.get_extensions()
    assert [x["id"] for x in got2["items"]] == ["db"]
