"""浏览器"这份配置已被占用"那一族:复用 / 换配置 / 说清失败。

钉的是 2026-10-09 真机复现的那个死结:教务导入点「打开登录页」报
「Edge 启动后立刻退出(退出码 0),无法启用浏览器能力」,而点多少次「再读一次」
都是同一句话 —— 因为上一个实例还占着 `browser-profile`,再起一个必然被拒。
复现方式写在 `scripts/e2e_browser_busy.py`(真起一个 Edge 占住目录,再走一遍)。

这一族全部走真入口 `launch_browser`,替身只放在最外层三个地方:
`find_browser_exe`(不依赖本机装了什么)、`subprocess.Popen`(不真起进程)、
`_http_json`(不真占端口)。`_pid_alive` 用真实现 + 一个一定不存在的 pid。
"""
from __future__ import annotations

import json
import os

import pytest

from minicode import browser as br


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把数据目录、浏览器可执行文件、环境变量三样都收进 tmp_path。"""
    monkeypatch.delenv(br.ENV_PROFILE, raising=False)
    monkeypatch.delenv(br.ENV_CDP_URL, raising=False)
    monkeypatch.setattr(br, "app_path", lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr(br, "find_browser_exe",
                        lambda: ("Edge", str(tmp_path / "msedge.exe")))
    monkeypatch.setattr(br, "_free_port", lambda: 21211)
    br.shutdown()
    yield
    br.shutdown()


class _Proc:
    """假进程:`exits` 非 None 时 `poll()` 报"已经退了,退出码是它"。

    `returncode` 与 `poll()` 同步 —— 真 Popen 就是这样的(没退时 None,退了赋值),
    少这一半就会写出"只在 poll 里看退出码"的代码,而报错文案要的正是在 returncode。
    """

    def __init__(self, exits=None, pid=424242):
        self.pid = pid
        self.exits = exits
        self.returncode = exits
        self.args: list[str] = []
        self.terminated = False

    def poll(self):
        return self.exits

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.exits if self.exits is not None else 0


class _Launcher:
    """替住 `subprocess.Popen`,记下每一次调用拿到了哪些参数。"""

    def __init__(self, monkeypatch, procs):
        self.procs = list(procs)
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        proc = self.procs.pop(0) if self.procs else self.procs_last
        self.procs_last = proc
        proc.args = list(args)
        return proc


def _profile(tmp_path, name="browser-profile"):
    p = tmp_path / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_marker(profile, *, pid, port, headless):
    br._write_marker(profile, pid=pid, port=port, headless=headless,
                     exe=str(profile / "msedge.exe"))


def test_reuse_a_live_instance_instead_of_launching_a_second_one(tmp_path, monkeypatch):
    """配置被一个活着、端口探得到、形态相符的实例拿着 → 复用它,一个进程都不起。"""
    profile = _profile(tmp_path)
    _write_marker(profile, pid=424242, port=21211, headless=True)
    monkeypatch.setattr(br, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y",
                                                  "Browser": "Edg/154.0"}
                        if "/json/version" in url else None)
    popen = _Launcher(monkeypatch, [_Proc()])
    monkeypatch.setattr(br.subprocess, "Popen", popen)

    proc, port, info = br.launch_browser(headless=True, profile_dir=profile)

    assert proc is None, "复用来的实例不是本进程起的,proc 必须是 None(shutdown 靠它分清归属)"
    assert port == 21211 and info["Browser"] == "Edg/154.0"
    assert popen.calls == [], "复用这条路一旦又起了进程,就是「两个实例抢同一本配置」的原件"
    assert "复用" in br.last_note() and br.last_note()


def test_window_shape_must_match_before_reusing(tmp_path, monkeypatch):
    """无窗口的实例不许被教务登录复用:那等于让用户对着一张看不见的页面输密码。"""
    profile = _profile(tmp_path)
    _write_marker(profile, pid=424242, port=21211, headless=True)
    monkeypatch.setattr(br, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y"})
    popen = _Launcher(monkeypatch, [_Proc(exits=None)])
    monkeypatch.setattr(br.subprocess, "Popen", popen)

    proc, port, info = br.launch_browser(headless=False, profile_dir=profile)

    assert proc is not None, "形态不符就不该复用,该另起一个有窗口的"
    assert popen.calls and "--headless=new" not in " ".join(popen.calls[0])


def test_no_marker_means_no_reuse_even_if_a_port_file_exists(tmp_path, monkeypatch):
    """没有我们的标记(老版本残留/别的程序占的)就不复用:不知道它有没有窗口。

    这一格故意保守 —— 换一份配置的代价是"重登一次",看得见也说得清;
    而复用一个不知形态的实例的代价是"用户在教务登录界面上等一张不存在的窗口"。
    """
    profile = _profile(tmp_path)
    (profile / "DevToolsActivePort").write_text("21211\n/devtools/browser/z\n", encoding="utf-8")
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y"})
    popen = _Launcher(monkeypatch, [_Proc(exits=None)])
    monkeypatch.setattr(br.subprocess, "Popen", popen)

    proc, port, info = br.launch_browser(headless=True, profile_dir=profile)

    assert proc is not None and popen.calls, "没有标记就该老实起一个新的"


def test_busy_then_fall_back_to_a_sibling_profile(tmp_path, monkeypatch):
    """起来就退出 → 换一份新配置再试,并把"教务要重登"这句话说明白。"""
    profile = _profile(tmp_path)
    busy = _Proc(exits=0)                      # 真机复现的那一种:退出码 0
    fresh = _Proc(exits=None)
    popen = _Launcher(monkeypatch, [busy, fresh])
    monkeypatch.setattr(br.subprocess, "Popen", popen)
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y"})

    proc, port, info = br.launch_browser(headless=False, profile_dir=profile)

    assert proc is fresh and len(popen.calls) == 2
    second = " ".join(popen.calls[1])
    assert f"--user-data-dir={profile.with_name('browser-profile-2')}" in second, \
        "第二次必须落在兄弟目录上,而不是原地再撞一次"
    note = br.last_note()
    assert "重新登录" in note and "browser-profile-2" in note, note
    assert (profile.with_name("browser-profile-2")).exists()


def test_env_pinned_profile_is_never_moved(tmp_path, monkeypatch):
    """用户用环境变量点名了目录 → 绝不换目录(那是他显式挑的那本登录态)。"""
    profile = _profile(tmp_path, "pinned-profile")
    monkeypatch.setenv(br.ENV_PROFILE, str(profile))
    popen = _Launcher(monkeypatch, [_Proc(exits=21), _Proc(exits=21)])
    monkeypatch.setattr(br.subprocess, "Popen", popen)

    with pytest.raises(br.BrowserUnavailable) as exc:
        br.launch_browser(headless=True, profile_dir=None)

    text = str(exc.value)
    assert len(popen.calls) == 1, "被点名的目录只能试一次,换目录等于绕过用户的意图"
    assert "退出码 21" in text and "YINLUXS_BROWSER_PROFILE" in text
    assert not (tmp_path / "pinned-profile-2").exists()


def test_busy_is_still_a_browser_unavailable(tmp_path):
    """新那一类必须是旧那一类的子类:满仓的 `except BrowserUnavailable` 不能因此漏接。"""
    err = br.BrowserProfileBusy("x", exit_code=0, profile=tmp_path)
    assert isinstance(err, br.BrowserUnavailable)


def test_a_second_window_that_grabbed_the_profile_first_is_reused(tmp_path, monkeypatch):
    """两个学长窗口同时点导入:后起的那一个立刻退出,不该报错,该复用先起的那一个。

    这是"检查与启动之间被人抢了"的竞态形状 —— 启动前探测时还没有标记,等我们
    起来并立刻退出之后,先起的那一个已经把标记写好了。真机上的表现就是用户看到的
    那句「Edge 启动后立刻退出(退出码 0)」。
    """
    profile = _profile(tmp_path)
    winner = _Proc(exits=0)

    def fake_popen(args, **kw):
        # 先起的那个窗口把标记写好(有窗口),我们这一发立刻退出
        br._write_marker(profile, pid=424242, port=21211, headless=True,
                         exe=str(tmp_path / "msedge.exe"))
        return winner

    monkeypatch.setattr(br.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(br, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y",
                                                  "Browser": "Edg/154.0"})

    proc, port, info = br.launch_browser(headless=True, profile_dir=profile)

    assert proc is None and port == 21211
    assert "复用" in br.last_note()


def test_marker_written_and_cleared_only_for_our_own_instance(tmp_path, monkeypatch):
    """标记里要认得出我们起的那一个;收工时只清自己那份。"""
    profile = _profile(tmp_path)
    proc = _Proc(exits=None, pid=424244)
    popen = _Launcher(monkeypatch, [proc])
    monkeypatch.setattr(br.subprocess, "Popen", popen)
    monkeypatch.setattr(br, "_http_json",
                        lambda url, timeout=2.0: {"webSocketDebuggerUrl": "ws://x/y"})

    got, port, info = br.launch_browser(headless=True, profile_dir=profile)
    marker = json.loads((profile / "YinluBrowserInstance.json").read_text(encoding="utf-8"))
    assert marker["pid"] == 424244 and marker["port"] == port and marker["headless"] is True

    # 另一个实例改写过这本标记 → 收工时不许把它的标记删掉
    _write_marker(profile, pid=424299, port=21999, headless=False)
    br._STATE.conn = None
    br._STATE.proc = got
    br._STATE.profile = profile
    br.shutdown()
    assert (profile / "YinluBrowserInstance.json").exists(), \
        "删掉别人刚写的标记,等于骗下一个人「这份配置没人用」"


def test_every_attempt_failed_says_what_and_what_next(tmp_path, monkeypatch):
    """全失败那句话要带得上退出码与出路,而且一个字都不许像"成功"。"""
    profile = _profile(tmp_path)
    popen = _Launcher(monkeypatch, [_Proc(exits=0), _Proc(exits=0), _Proc(exits=0)])
    monkeypatch.setattr(br.subprocess, "Popen", popen)

    with pytest.raises(br.BrowserUnavailable) as exc:
        br.launch_browser(headless=False, profile_dir=profile)

    text = str(exc.value)
    assert "退出码 0" in text and "msedge" in text.lower() or "浏览器" in text
    for banned in ("已启用", "成功", "已连接"):
        assert banned not in text, f"起不来却说「{banned}」就是这一路最贵的坏法"
    assert len(popen.calls) == br.MAX_PROFILE_TRIES


def test_pid_alive_is_measured_not_guessed():
    """`_pid_alive` 得真能分清活着与不存在(它决定复用还是换目录)。"""
    assert br._pid_alive(os.getpid()) is True
    assert br._pid_alive(424245) is False
    assert br._pid_alive(0) is False
    assert br._pid_alive(None) is False
