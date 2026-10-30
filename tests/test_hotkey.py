"""全局热键模块的口径测试。

非交互桌面站上真 RegisterHotKey/GetMessage 验不了(hotkey.py 的 docstring 记了
这条边界),所以这里钉三件事:**键位解析的纯函数口径**、**start 的配置闸**
(没配/写错绝不起线程)、以及**线程生命周期与唤起路径**(mock user32,断"键
按下真的调到 summon、注册失败真的说出口、唤起抛错循环不死")。
"""
import threading

import pytest

from minicode import hotkey
from minicode.hotkey import (MOD_ALT, MOD_CONTROL, MOD_SHIFT, WM_HOTKEY,
                             parse_combo)


# ---------------------------------------------------------------- 键位解析

def test_parse_combo_accepts_case_insensitive_named_mods():
    assert parse_combo("Ctrl+Alt+Y") == (MOD_CONTROL | MOD_ALT, 0x59)
    assert parse_combo("ctrl+shift+1") == (MOD_CONTROL | MOD_SHIFT, 0x31)


def test_parse_combo_maps_f_keys_to_virtual_codes():
    mods, vk = parse_combo("Alt+F5")
    assert mods == MOD_ALT and vk == 0x70 + 5 - 1


def test_parse_combo_rejects_bare_key_and_bare_modifier():
    assert parse_combo("Y") is None            # 不带修饰 = 占死全系统的一个键
    assert parse_combo("Ctrl") is None         # 没有主键
    assert parse_combo("") is None
    assert parse_combo(None) is None


def test_parse_combo_rejects_unknown_names_and_empty_parts():
    assert parse_combo("Ctrl+Hyper+Y") is None
    assert parse_combo("Ctrl++Y") is None
    assert parse_combo("Ctrl+Alt+Enter") is None


# ---------------------------------------------------------------- 配置闸

def test_start_does_not_spawn_a_thread_for_disabled_or_bad_combo():
    before = len(threading.enumerate())
    for combo in ("", None, "Ctrl+Hyper+Y"):
        r = hotkey.start(lambda: None, combo)
        assert r["ok"] is False and r["msg"]
    assert len(threading.enumerate()) == before    # 一条线程都没起


# ---------------------------------------------------------------- 线程生命周期

class _FakeUser32:
    """按脚本放消息的假 user32:注册结果可控,消息队列喂完即退出循环。"""

    def __init__(self, register_ok=True, script=None):
        self.register_ok = register_ok
        self.script = list(script or [])
        self.calls = []

    def RegisterHotKey(self, hwnd, kid, mods, vk):
        self.calls.append(("reg", kid, mods, vk))
        return self.register_ok

    def GetMessageW(self, pmsg, hwnd, lo, hi):
        self.calls.append("get")
        if not self.script:
            return 0                                # 喂完收摊
        mid, wparam = self.script.pop(0)
        pmsg.contents.message = mid
        pmsg.contents.wParam = wparam
        return 1

    def UnregisterHotKey(self, hwnd, kid):
        self.calls.append(("unreg", kid))
        return 1


@pytest.fixture
def fake_user32(monkeypatch):
    holder = {}

    def _install(fake):
        holder["fake"] = fake
        monkeypatch.setattr(hotkey, "_user32", lambda: fake)

    return _install


def test_register_failure_speaks_and_never_summons(fake_user32, caplog):
    logs = []
    fake_user32(_FakeUser32(register_ok=False))
    hotkey.start(lambda: pytest.fail("注册失败还唤起了"), "Ctrl+Alt+Y",
                 log=logs.append)
    _wait_for(lambda: any("没注册上" in m for m in logs))
    assert any("Ctrl+Alt+Y" in m for m in logs)


def test_hotkey_press_invokes_summon_once(fake_user32):
    calls = []
    fake = _FakeUser32(script=[(WM_HOTKEY, hotkey.HOTKEY_ID)])
    fake_user32(fake)
    hotkey.start(lambda: calls.append(1), "Ctrl+Alt+Y", log=lambda m: None)
    _wait_for(lambda: calls)                       # 循环退出即线程自然结束
    assert calls == [1]
    assert ("reg", hotkey.HOTKEY_ID, MOD_CONTROL | MOD_ALT, 0x59) in fake.calls
    assert fake.calls[-1][0] == "unreg"            # 退出前注销


def test_summon_exception_is_logged_and_loop_survives(fake_user32):
    calls, logs = [], []
    fake = _FakeUser32(script=[(WM_HOTKEY, hotkey.HOTKEY_ID),
                               (WM_HOTKEY, hotkey.HOTKEY_ID)])
    fake_user32(fake)

    def _boom():
        calls.append(1)
        raise RuntimeError("界面还没就绪")

    hotkey.start(_boom, "Ctrl+Alt+Y", log=logs.append)
    _wait_for(lambda: len(calls) == 2 and len(logs) >= 2)
    assert any("热键唤起失败" in m for m in logs)


def _wait_for(cond, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.02)
    pytest.fail("条件在超时内没有成立")


def test_started_thread_is_daemon_and_named(fake_user32):
    # summon 里等放行:否则空脚本的线程瞬间跑完,enumerate 里抓不到它
    release, arrived = threading.Event(), threading.Event()
    fake_user32(_FakeUser32(script=[(WM_HOTKEY, hotkey.HOTKEY_ID)]))

    def _hold():
        arrived.set()
        release.wait(2.0)

    hotkey.start(_hold, "Alt+F5", log=lambda m: None)
    _wait_for(arrived.is_set)
    t = next(t for t in threading.enumerate() if t.name == "global-hotkey")
    assert t.daemon is True
    release.set()
