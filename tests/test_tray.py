"""关窗去向 + 系统托盘(v0.9.22)。

这里**不碰真的 WinForms / pystray**:窗口那条路只在 Windows 桌面上才完整,
测试覆盖的是决策逻辑 —— 什么时候拦下关闭、什么时候真退出、藏不进托盘时
绝不能什么都不做。
"""
import json
import time

import pytest

from minicode import paths, tray


@pytest.fixture(autouse=True)
def _isolate_app_dir(tmp_path, monkeypatch):
    """偏好与日志都落在 APP_DIR:钉到临时目录,别污染真实数据目录。"""
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


class FakeArgs:
    """FormClosingEventArgs 替身:只看 Cancel 有没有被置上。"""

    def __init__(self):
        self.Cancel = False


class FakeUI:
    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        payload["type"] = type_
        self.events.append(payload)

    def of(self, type_):
        return [e for e in self.events if e["type"] == type_]


class FakeForm:
    """BrowserForm 替身:记录 Hide/Close,InvokeRequired 恒 False(当 UI 线程)。"""

    def __init__(self):
        self.InvokeRequired = False
        self.hidden = 0
        self.closed = 0

    def Hide(self):
        self.hidden += 1

    def Close(self):
        self.closed += 1


def _controller():
    ui = FakeUI()
    c = tray.TrayController(window=object(), ui=ui)
    c.form = FakeForm()
    return c, ui


def wait_until(cond, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- 偏好

def test_pref_round_trip_and_default():
    assert tray.read_pref() == {"ask": True, "action": "minimize"}   # 文件还不存在
    tray.write_pref(False, "exit")
    assert tray.read_pref() == {"ask": False, "action": "exit"}
    tray.write_pref(True, "minimize")
    assert tray.read_pref() == {"ask": True, "action": "minimize"}


def test_pref_tolerates_garbage():
    p = tray._pref_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ 这不是 JSON", encoding="utf-8")
    assert tray.read_pref()["ask"] is True          # 读不出来就回到"每次都问"
    p.write_text(json.dumps({"ask": False, "action": "随便写的"}), encoding="utf-8")
    assert tray.read_pref() == {"ask": False, "action": "minimize"}   # 非法动作归默认


# ---------------------------------------------------------------- 拦关闭

def test_first_close_asks_and_cancels():
    c, ui = _controller()
    args = FakeArgs()
    c._on_closing(None, args)
    assert args.Cancel is True                      # 关窗被拦下
    assert wait_until(lambda: ui.of("close_request"))


def test_second_close_while_asking_hides_to_tray():
    """确认框还开着又点一次 X:不再重复问,直接收进托盘(第 35 行注释那条)。"""
    c, _ui = _controller()
    c._asking = True
    c.hide_to_tray = lambda: True
    args = FakeArgs()
    c._on_closing(None, args)
    assert args.Cancel is True
    assert c._asking is False


def test_pref_exit_lets_window_close():
    c, ui = _controller()
    tray.write_pref(False, "exit")
    args = FakeArgs()
    c._on_closing(None, args)
    assert args.Cancel is False                     # 放行原生关闭
    assert c.allow_exit is True
    assert not ui.of("close_request")


def test_pref_minimize_hides_without_asking():
    c, ui = _controller()
    tray.write_pref(False, "minimize")
    hidden = []
    c.hide_to_tray = lambda: hidden.append(1) or True
    args = FakeArgs()
    c._on_closing(None, args)
    assert args.Cancel is True and hidden == [1]


def test_allow_exit_short_circuits_everything():
    """quit() 之后窗口自己关:这时绝不能再拦,否则永远退不出去。"""
    c, _ui = _controller()
    c.allow_exit = True
    args = FakeArgs()
    c._on_closing(None, args)
    assert args.Cancel is False


# ---------------------------------------------------------------- 答复

@pytest.mark.parametrize("action", ["hide", "exit", "cancel"])
def test_close_choice_ok(action):
    c, _ui = _controller()
    c.hide_to_tray = lambda: True
    c.quit = lambda: None
    assert c.choose(action) == {"ok": True, "action": action}


def test_close_choice_rejects_unknown_action():
    c, _ui = _controller()
    r = c.choose("关机等会儿再说")
    assert r["ok"] is False and "未知" in r["msg"]


def test_cancel_clears_asking_so_next_x_asks_again():
    c, _ui = _controller()
    c._asking = True
    assert c.choose("cancel")["ok"] is True
    assert c._asking is False
    args = FakeArgs()
    c._on_closing(None, args)                       # 下次点 X 仍然要问
    assert args.Cancel is True


def test_remember_writes_pref():
    c, _ui = _controller()
    c.hide_to_tray = lambda: True
    c.choose("hide", remember=True)
    assert tray.read_pref() == {"ask": False, "action": "minimize"}
    c2, _ = _controller()
    c2.quit = lambda: None
    c2.choose("exit", remember=True)
    assert tray.read_pref() == {"ask": False, "action": "exit"}


def test_module_close_choice_without_controller_is_honest():
    """没接管成功(网页版/非 Windows)时不能静默吞掉,要说清该怎么办。"""
    assert tray._CONTROLLER is None
    r = tray.close_choice("hide")
    assert r["ok"] is False and "退出" in r["msg"]


# ---------------------------------------------------------------- 藏窗安全线

def test_hide_refuses_when_tray_icon_unavailable():
    """**没有托盘就绝不藏窗口** —— 藏了就没有再叫出来的把手。"""
    c, _ui = _controller()
    c._ensure_icon = lambda: False
    assert c.hide_to_tray() is False
    assert c.form.hidden == 0


def test_hide_hides_when_icon_ready():
    c, _ui = _controller()
    c._ensure_icon = lambda: True
    assert c.hide_to_tray() is True
    assert c.form.hidden == 1


def test_choose_hide_falls_back_to_quit(monkeypatch):
    """藏不下去就退出,而不是"点了 X 没反应"。"""
    c, _ui = _controller()
    monkeypatch.setattr(c, "_ensure_icon", lambda: False)
    quit_calls = []
    monkeypatch.setattr(c, "quit", lambda: quit_calls.append(1))
    assert c.choose("hide")["ok"] is True
    assert quit_calls == [1]


def test_close_path_when_icon_fails_inside_closing():
    """偏好=最小化但托盘起不来:第 101 行的兜底 —— 异步走一次真退出。"""
    c, _ui = _controller()
    tray.write_pref(False, "minimize")
    monkey_allow = []

    def fake_quit():
        monkey_allow.append(c.allow_exit)

    c.quit = fake_quit
    c._ensure_icon = lambda: False
    args = FakeArgs()
    c._on_closing(None, args)
    assert c.allow_exit is True                     # 已经放行
    assert wait_until(lambda: monkey_allow)         # 退出动作在线程里跑起来了
    assert args.Cancel is False                     # 没藏成就不拦,让窗口关


def test_quit_stops_icon_and_closes_form():
    c, _ui = _controller()
    stopped = []

    class Icon:
        def stop(self):
            stopped.append(1)

    c.icon = Icon()
    c.quit()
    assert stopped == [1] and c.icon is None
    assert c.allow_exit is True
    assert c.form.closed == 1                       # 走的是 form.Close(经 UI 线程)
