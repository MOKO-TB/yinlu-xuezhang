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


# v0.9.25:通知有两条真通道(WinRT toast / 托盘气球),测试**一条都不许真发**
# —— 不然每次跑测试都在用户桌面上弹通知,还会往 HKCU 写东西。
# 默认把 toast 做成"这台机器没有 PowerShell",原有那些"退回气球"的用例照旧成立;
# 要验 toast 的用例自己调 `_toast_works()`。
_TOAST_CALLS: list[dict] = []
_REAL_TOAST = tray._toast
_REAL_IDENTITY = tray.register_notify_identity
_REAL_STARTUP_PATH = tray.startup_path    # 默认被下面的夹具清空,少数用例要真身


@pytest.fixture(autouse=True)
def _no_real_notify(monkeypatch):
    def fake_toast(title, body, tag="", act=""):
        _TOAST_CALLS.append({"title": title, "body": body, "tag": tag,
                             "act": act})
        return {"ok": False, "skipped": "no_powershell",
                "msg": "测试环境没有 PowerShell"}

    _TOAST_CALLS.clear()
    monkeypatch.setattr(tray, "_toast", fake_toast)
    monkeypatch.setattr(tray, "register_notify_identity",
                        lambda force=False: {"ok": True, "skipped": "test"})
    monkeypatch.setattr(tray, "_identity_done", False)
    monkeypatch.setattr(tray, "_LAST", {"ok": None, "channel": "",
                                        "verified": None, "kind": "",
                                        "msg": "还没发过"})
    # v0.9.26 同一套路:开机自启也是"起 PowerShell 动本机文件",默认判为
    # "这台机器没有「启动」文件夹" —— 它在 `_run_ps` 之前就会返回,不会真起进程。
    # 要测自启的用例各自用 `_startup_env` 覆盖回来。
    monkeypatch.setattr(tray, "startup_path", lambda: "")


def _real_toast(monkeypatch):
    """要用真 `_toast`(测命令构造)时先还原,否则拿到的是上面那个替身。"""
    monkeypatch.setattr(tray, "_toast", _REAL_TOAST)


def _real_identity(monkeypatch):
    monkeypatch.setattr(tray, "register_notify_identity", _REAL_IDENTITY)


def _toast_works(monkeypatch, result=None):
    """把 toast 通道打通(默认成功),用于验"优先走 toast、不碰气球"。"""
    def ok_toast(title, body, tag="", act=""):
        _TOAST_CALLS.append({"title": title, "body": body, "tag": tag,
                             "act": act})
        return result or {"ok": True, "channel": "toast"}

    monkeypatch.setattr(tray, "_toast", ok_toast)


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


def test_ensure_icon_creates_the_icon_only_once(monkeypatch):
    """藏窗口(UI/桥接线程)与发桌面通知(看门狗线程)都要图标:只能有一个 Icon,
    多建一颗 = 托盘里两个图标 + 一条停不下来的消息循环。"""
    import sys
    import types

    created = []

    class SpyIcon:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def run(self):
            pass

    pystray_stub = types.ModuleType("pystray")
    pystray_stub.Icon = SpyIcon
    pystray_stub.Menu = lambda *a, **k: None
    pystray_stub.MenuItem = lambda *a, **k: None
    image_stub = types.ModuleType("PIL.Image")
    image_stub.open = lambda *a, **k: object()
    pil_stub = types.ModuleType("PIL")
    pil_stub.Image = image_stub
    monkeypatch.setitem(sys.modules, "pystray", pystray_stub)
    monkeypatch.setitem(sys.modules, "PIL", pil_stub)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_stub)

    c, _ui = _controller()
    assert c._ensure_icon() is True and c._ensure_icon() is True
    assert len(created) == 1 and c.icon is created[0]


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


# ============================================================ 桌面提醒(v0.9.25)

class FakeIcon:
    """pystray.Icon 替身:只记 notify 收到什么,不碰 Shell_NotifyIcon。"""

    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    def notify(self, text, title=None):
        if self.fail is not None:
            raise self.fail
        self.calls.append((text, title))


class FakeClock:
    """替 tray.time:把 warm-up 的 sleep 记下来,不让测试真等 0.6 秒。"""

    def __init__(self):
        self.slept: list[float] = []

    def sleep(self, seconds):
        self.slept.append(seconds)

    def time(self):
        return _REAL_TIME()

    def strftime(self, *args, **kwargs):
        return "2026-09-21 08:00:00"


_REAL_TIME = time.time


@pytest.fixture
def tray_icon(monkeypatch):
    """一个「有托盘图标、可弹通知」的现场:控制器就位、冷却表清空、不等 warm-up。"""
    c, ui = _controller()
    icon = FakeIcon()
    c.icon = icon
    c._ensure_icon = lambda: True
    monkeypatch.setattr(tray, "_CONTROLLER", c)
    monkeypatch.setattr(tray, "_ICON_WARMUP", 0)
    monkeypatch.setattr(tray, "_seen", {})
    return c, icon, ui


# ---------------------------------------------------------------- 提醒偏好

def test_notify_pref_defaults_and_round_trip():
    assert tray.read_notify_pref() == tray.NOTIFY_DEFAULTS      # 文件还没有:全开
    pref = tray.read_notify_pref()
    pref["kinds"]["class"] = False
    assert tray.write_notify_pref(pref) is True
    got = tray.read_notify_pref()
    assert got["kinds"]["class"] is False and got["kinds"]["briefing"] is True
    assert got["enabled"] is True


def test_notify_pref_tolerates_garbage_and_drops_unknown_kinds():
    p = tray._notify_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 坏文件绝不能变成"提醒被静默关掉"—— 那是用户察觉不了的失败
    p.write_text("{不是 JSON", encoding="utf-8")
    assert tray.read_notify_pref() == tray.NOTIFY_DEFAULTS
    p.write_text("[]", encoding="utf-8")
    assert tray.read_notify_pref() == tray.NOTIFY_DEFAULTS
    p.write_text(json.dumps({"enabled": False,
                             "kinds": {"class": True, "课前": False, "cat": True}}),
                 encoding="utf-8")
    got = tray.read_notify_pref()
    assert got["enabled"] is False
    assert set(got["kinds"]) == set(tray.NOTIFY_KINDS)          # 未知类别一律丢掉
    assert got["kinds"]["class"] is True


def test_set_notify_master_switch_and_kind():
    assert tray.set_notify(False)["enabled"] is False
    assert tray.notify_enabled("class") is False               # 总开关压过一切
    assert tray.notify_enabled() is False
    tray.set_notify(True)
    assert tray.notify_enabled("class") is True
    assert tray.set_notify(False, "class")["kind_on"] is False
    assert tray.notify_enabled("class") is False
    assert tray.notify_enabled("briefing") is True             # 只影响那一类


def test_set_notify_rejects_unknown_kind_without_writing():
    r = tray.set_notify(False, "课前")
    assert r["ok"] is False and "不认识的提醒类别" in r["msg"]
    assert tray._notify_path().exists() is False               # 没落盘
    assert tray.notify_enabled("class") is True


def test_set_notify_is_honest_when_dir_readonly(monkeypatch, tmp_path):
    """装在只读盘时"勾了没记住"必须回话,不能假装成功。"""
    blocker = tmp_path / "app" / "locked"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("占位", encoding="utf-8")                # 父目录位置被文件占了
    monkeypatch.setattr(tray, "_notify_path", lambda: blocker / "notify-pref.json")
    r = tray.set_notify(False)
    assert r["ok"] is False and "重启后会回到原样" in r["msg"]
    assert r["enabled"] is False                                # 但仍报真实意图


# ---------------------------------------------------------------- 弹通知本体

def test_notify_sends_title_and_body(tray_icon):
    _c, icon, _ui = tray_icon
    r = tray.notify("快上课了", "高等数学 08:00 开始 · B101", kind="class")
    assert r["ok"] is True
    assert icon.calls == [("高等数学 08:00 开始 · B101", "快上课了")]


def test_notify_truncates_long_body(tray_icon):
    _c, icon, _ui = tray_icon
    r = tray.notify("今日简报", "字" * 300, kind="briefing")
    assert r["ok"] is True
    body = icon.calls[0][0]
    assert len(body) == 160 and body.endswith("…")


def test_notify_empty_body_and_default_title(tray_icon):
    _c, icon, _ui = tray_icon
    assert tray.notify("标题", "   ")["skipped"] == "empty"
    assert icon.calls == []
    tray.notify("", "有正文", force=True)
    assert icon.calls[-1][1] == "引路学长"                       # 空标题给个兜底


def test_notify_respects_switch_and_kind(tray_icon):
    _c, icon, _ui = tray_icon
    tray.set_notify(False, "reminders")
    assert tray.notify("待办临期", "交材料", kind="reminders") == \
        {"ok": False, "skipped": "off"}
    assert icon.calls == []
    # 同一正文换成没关的类别仍要能弹(冷却键含类别)
    assert tray.notify("今日简报", "交材料", kind="briefing")["ok"] is True
    tray.set_notify(False)
    assert tray.notify("今日简报", "另一条完全不同的正文", kind="briefing")["skipped"] == "off"
    assert tray.notify("引路学长 · 测试", "强制也要能出", force=True)["ok"] is True


def test_notify_cooldown_hides_duplicate(tray_icon):
    """同一节课/同一条待办被重复推时,桌面只响一次。"""
    _c, icon, _ui = tray_icon
    assert tray.notify("校园新通知", "教务处:同一标题", kind="notices")["ok"] is True
    again = tray.notify("校园新通知", "教务处:同一标题", kind="notices")
    assert again == {"ok": False, "skipped": "cooldown"}
    assert len(icon.calls) == 1
    # 过了冷却窗口就该再允许弹(直接改表,不真等 90 秒)
    tray._seen.clear()


def test_notify_quiet_when_window_in_foreground(tray_icon, monkeypatch):
    """窗口在前台有焦点:聊天栏那条已经看得见,别再弹一遍。"""
    c, icon, _ui = tray_icon

    class FocusedForm(FakeForm):
        Visible = True
        ContainsFocus = True

    c.form = FocusedForm()
    assert tray.notify("快上课了", "高等数学", kind="class")["skipped"] == "focused"
    assert icon.calls == []
    assert tray.notify("引路学长 · 测试", "测试不受焦点影响", force=True)["ok"] is True
    # 关掉了这个体贴行为 → 前台也照弹
    pref = tray.read_notify_pref()
    pref["quiet_when_focused"] = False
    tray.write_notify_pref(pref)
    assert tray.notify("快上课了", "大学英语", kind="class")["ok"] is True


def test_notify_failopen_when_foreground_state_unreadable(tray_icon):
    """FakeForm 没有 Visible/ContainsFocus:读不到状态按"不在前台"处理,宁可多弹。"""
    _c, icon, _ui = tray_icon
    assert tray.notify("快上课了", "线性代数", kind="class")["ok"] is True
    assert len(icon.calls) == 1


def test_notify_without_tray_is_honest(monkeypatch):
    """toast 通道不通 + 又没托盘 = 两条都没了,必须如实回"没发出去"。"""
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    monkeypatch.setattr(tray, "_seen", {})
    r = tray.notify("快上课了", "高等数学", kind="class")
    assert r["ok"] is False and r["skipped"] == "no_channel"
    assert "PowerShell" in r["msg"] and "气球" in r["msg"]


def test_notify_when_icon_cannot_start(monkeypatch):
    c, _ui = _controller()
    c._ensure_icon = lambda: False
    monkeypatch.setattr(tray, "_CONTROLLER", c)
    monkeypatch.setattr(tray, "_seen", {})
    r = tray.notify("快上课了", "高等数学", kind="class")
    assert r["ok"] is False and r["skipped"] == "no_channel"
    assert "图标" in r["msg"]


def test_notify_surfaces_system_refusal(monkeypatch):
    """pystray 抛异常(通知被关闭/句柄失效)要写进日志并原样带进话术。"""
    c, _ui = _controller()
    c.icon = FakeIcon(fail=RuntimeError("0x80070005"))
    c._ensure_icon = lambda: True
    monkeypatch.setattr(tray, "_CONTROLLER", c)
    monkeypatch.setattr(tray, "_seen", {})
    r = tray.notify("快上课了", "高等数学", kind="class")
    assert r["ok"] is False and r["skipped"] == "no_channel"
    assert "RuntimeError" in r["msg"]
    assert "桌面通知失败" in tray._ensure_parent(
        tray.app_path(tray.LOG_FILE)).read_text(encoding="utf-8")


def test_first_notify_waits_for_icon_warmup(monkeypatch):
    """图标刚建好就通知,系统可能还没收下句柄 —— 第一条要等一下。"""
    c, _ui = _controller()
    icon = FakeIcon()
    c.icon = None
    c._ensure_icon = lambda: (setattr(c, "icon", icon), True)[1]
    clock = FakeClock()
    monkeypatch.setattr(tray, "_CONTROLLER", c)
    monkeypatch.setattr(tray, "_seen", {})
    monkeypatch.setattr(tray, "_ICON_WARMUP", 0.6)
    monkeypatch.setattr(tray, "time", clock)
    assert tray.notify("快上课了", "高等数学", kind="class")["ok"] is True
    assert clock.slept == [0.6]
    # 第二条起图标已在位,不再等待
    assert tray.notify("今日简报", "另一条正文", kind="briefing")["ok"] is True
    assert clock.slept == [0.6]


def test_notify_status_reports_whether_it_can_fire(tray_icon):
    _c, _icon, _ui = tray_icon
    st = tray.notify_status()
    assert st["enabled"] is True and st["tray_installed"] is True
    assert st["icon_ready"] is True
    assert st["pref_path"].endswith("notify-pref.json")
    assert set(st["kinds"]) == set(tray.NOTIFY_KINDS)
    # 通道信息:说清"这台机器走哪条",以及上一次真实结果
    assert st["aumid"] == tray.AUMID
    assert st["last"] == {"ok": None, "channel": "", "verified": None,
                          "kind": "", "msg": "还没发过"}
    tray.notify("快上课了", "高等数学", kind="class")
    after = tray.notify_status()["last"]
    assert after["channel"] == "balloon" and after["verified"] is False
    assert "PowerShell" in after["msg"]                 # 断的是 toast 那条原因


# ---------------------------------------------------------------- 通道选型

def test_notify_prefers_toast_and_never_touches_the_balloon(tray_icon, monkeypatch):
    """WinRT toast 是主通道;它通了就不该再碰 Win11 根本不显示的气球。"""
    _c, icon, _ui = tray_icon
    _toast_works(monkeypatch)
    r = tray.notify("快上课了", "高等数学 08:00", kind="class")
    assert r["ok"] is True and r["channel"] == "toast" and r["verified"] is True
    assert icon.calls == []
    assert _TOAST_CALLS == [{"title": "快上课了", "body": "高等数学 08:00",
                             "tag": "class", "act": ""}]
    # 测试环境协议没注册上(source 跳过),激活属性必须为空 —— 不发点了没反应的空激活


def test_toast_channel_needs_no_tray(monkeypatch):
    """命令行版在 Windows 上也弹得出去:通道不依赖托盘图标。"""
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    monkeypatch.setattr(tray, "_seen", {})
    _toast_works(monkeypatch)
    r = tray.notify("今日简报", "今天三节课", kind="briefing")
    assert r["ok"] is True and r["channel"] == "toast"


def test_truncation_happens_before_the_channel(tray_icon, monkeypatch):
    """160 字截断在进任何通道之前做完:两条通道收到的必须是同一份正文。"""
    _c, icon, _ui = tray_icon
    _toast_works(monkeypatch)
    tray.notify("今日简报", "字" * 300, kind="briefing")
    assert len(_TOAST_CALLS[-1]["body"]) == 160
    assert _TOAST_CALLS[-1]["body"].endswith("…")
    # 退回气球时收到的是同一份已截断正文,不是各截各的
    _toast_works(monkeypatch, {"ok": False, "skipped": "toast", "msg": "炸了"})
    tray.notify("今日简报", "另一条足够不同的正文" + "长" * 300,
                kind="briefing", force=True)
    assert len(icon.calls[-1][0]) == 160
    assert icon.calls[-1][0].endswith("…")


def test_identity_registration_failure_does_not_block_notify(tray_icon, monkeypatch):
    """注册表写不进(组策略禁用)只影响好不好看,绝不能影响提不提醒。"""
    _c, _icon, _ui = tray_icon
    _toast_works(monkeypatch)
    monkeypatch.setattr(tray, "register_notify_identity",
                        lambda force=False: {"ok": False, "msg": "PermissionError"})
    assert tray.notify("快上课了", "高等数学", kind="class")["ok"] is True


class FakeProc:
    def __init__(self, code=0, out=b"OK\n", err=b""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _fake_ps(tmp_path, monkeypatch):
    """给 _powershell 指一个"存在的"文件:非 Windows 上也能验命令构造。"""
    exe = tmp_path / "powershell.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(tray, "_powershell", lambda: str(exe))
    return str(exe)


def _fake_icon(tmp_path, monkeypatch):
    """让"图标存在"这一支真被走到:resource_path 生产上返回 **Path**,
    而环境块只吃字符串 —— 只在图标不存在时测是测不到这个 bug 的。"""
    icon = tmp_path / "toast-icon.png"
    icon.write_bytes(b"\x89PNG")
    monkeypatch.setattr(tray, "resource_path", lambda *a: icon)
    return icon


def test_toast_powershell_is_the_5_1_path():
    """WinRT 投影只在 Windows PowerShell 5.1 里有;pwsh 7 会当场报错。"""
    p = tray._powershell().replace("\\", "/")
    assert "WindowsPowerShell/v1.0/powershell.exe" in p


def test_toast_puts_user_text_only_in_the_environment(tmp_path, monkeypatch):
    """课名/通知标题是任意文本:绝不拼进命令行,否则注入面留给自己。"""
    _real_toast(monkeypatch)
    import subprocess as sp
    seen = {}
    evil = '删库 "; Remove-Item C:\\*; " 结尾'
    real_run = sp.run

    def fake_run(argv, **kw):
        seen["argv"] = list(argv)
        seen.update(kw)
        return FakeProc()

    monkeypatch.setattr(sp, "run", fake_run)
    exe = _fake_ps(tmp_path, monkeypatch)
    icon = _fake_icon(tmp_path, monkeypatch)            # 图标真存在才走得到那一支
    r = tray._toast("标题", evil, tag="class")
    assert r["ok"] is True and r["channel"] == "toast"
    assert seen["argv"][0] == exe
    assert evil not in " ".join(seen["argv"])           # 命令行里干干净净
    assert any(a == "-EncodedCommand" for a in seen["argv"])
    assert seen["env"]["YX_BODY"] == evil               # 文本只从环境变量进去
    assert seen["env"]["YX_TITLE"] == "标题"
    assert seen["env"]["YX_AUMID"] == tray.AUMID
    # Windows 建进程只吃纯字符串环境块:塞进 Path 会当场抛
    # "environment can only contain strings"(真机踩过),所以逐个查类型
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in seen["env"].items()), \
        {k: type(v).__name__ for k, v in seen["env"].items()
           if not isinstance(v, str)}
    # 图标必须是 file:/// 形式:裸路径进 <image src> 会静默没有图标(真机截图)
    assert seen["env"]["YX_ICON"] == tray._file_uri(icon)
    assert seen["timeout"] == tray._TOAST_TIMEOUT
    assert seen["creationflags"] & tray._NO_WINDOW      # 别闪黑框
    assert real_run is not fake_run


def test_toast_reports_what_powershell_said(tmp_path, monkeypatch):
    _real_toast(monkeypatch)
    """退出码非 0:把 stderr 原样带出来,"已成功"这种话一个字的不能出现。"""
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda *a, **k: FakeProc(
        1, b"", b"Cannot find type [Windows.UI.Notifications.ToastNotificationManager]"))
    _fake_ps(tmp_path, monkeypatch)
    r = tray._toast("t", "b")
    assert r["ok"] is False and r["skipped"] == "toast"
    assert "退出码 1" in r["msg"] and "Cannot find type" in r["msg"]


def test_toast_says_when_there_is_no_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(tray, "_powershell", lambda: str(tmp_path / "gone.exe"))
    r = tray._toast("t", "b")
    assert r["ok"] is False and r["skipped"] == "no_powershell"


def test_toast_says_when_the_spawn_itself_fails(tmp_path, monkeypatch):
    _real_toast(monkeypatch)
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda *a, **k: (_ for _ in ()).throw(
        sp.TimeoutExpired(cmd="powershell", timeout=15)))
    _fake_ps(tmp_path, monkeypatch)
    r = tray._toast("t", "b")
    assert r["ok"] is False and r["skipped"] == "toast_spawn"
    assert "TimeoutExpired" in r["msg"]


class FakeKey:
    """一坨注册表键的替身:能用 with 打开,能按名字取值。"""

    def __init__(self, fake, path):
        self.fake = fake
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeWinreg:
    """替 winreg:只记"动了哪一坨键、写了什么值",并盯住有没有碰别家。"""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self, existing=None, write_error=None):
        self.store = {k: dict(v) for k, v in (existing or {}).items()}
        self.written: list[tuple] = []
        self.opened: list[str] = []
        self.created: list[str] = []
        self.write_error = write_error

    def OpenKey(self, hive, path):
        self.opened.append(path)
        if path not in self.store:
            raise OSError(2, "找不到指定的文件")        # 真 winreg 就是这个 errno
        return FakeKey(self, path)

    def CreateKey(self, hive, path):
        self.created.append(path)
        if self.write_error is not None:
            raise self.write_error
        self.store.setdefault(path, {})
        return FakeKey(self, path)

    def QueryValueEx(self, key, name):
        try:
            return self.store[key.path][name], self.REG_SZ
        except KeyError:
            raise OSError(2, name)

    def SetValueEx(self, key, name, reserved, typ, value):
        self.written.append((key.path, name, value))
        self.store.setdefault(key.path, {})[name] = value


def _use_fake_winreg(monkeypatch, fake):
    """把整个 winreg 换成替身:测试绝不动真注册表。"""
    import sys
    import types
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = "HKCU"
    mod.REG_SZ = 1
    for fn in ("OpenKey", "CreateKey", "QueryValueEx", "SetValueEx"):
        setattr(mod, fn, getattr(fake, fn))
    monkeypatch.setitem(sys.modules, "winreg", mod)


def test_register_notify_identity_writes_only_our_own_key(monkeypatch):
    _real_identity(monkeypatch)
    fake = FakeWinreg()
    _use_fake_winreg(monkeypatch, fake)
    monkeypatch.setattr(tray, "_identity_done", False)
    assert tray.register_notify_identity() == {"ok": True}
    assert fake.created == [tray.AUMID_REG]                 # 只碰自己那一坨
    vals = {n: v for _k, n, v in fake.written}
    # 默认值才是 shell 渲染通知时读的那个名字 —— 只写 DisplayName 时真机上
    # 通知顶部仍然显示 `yinlu.xuezhang.desktop` 一串英文(截图实证)
    assert vals[""] == "引路学长"
    assert vals["DisplayName"] == "引路学长"
    assert vals["IconUri"].startswith("file:///")
    assert set(vals) <= {"", "DisplayName", "IconUri"}


def test_register_notify_identity_skips_when_already_right(monkeypatch):
    _real_identity(monkeypatch)
    icon = tray.resource_path(*tray.AUMID_ICON)
    fake = FakeWinreg({tray.AUMID_REG: {"": "引路学长",
                                        "DisplayName": "引路学长",
                                        "IconUri": tray._file_uri(icon)}})
    _use_fake_winreg(monkeypatch, fake)
    monkeypatch.setattr(tray, "_identity_done", False)
    r = tray.register_notify_identity()
    assert r["ok"] is True and r["skipped"] == "registered"
    assert fake.written == []                               # 不重复写
    assert tray._identity_done is True


def test_aumid_is_not_the_burned_in_old_identity():
    """Windows 在**第一次见到 AUMID** 时就把显示名缓存进通知库,之后改注册表
    刷不回来。`yinlu.xuezhang.app` 是"只会写 DisplayName"那会儿建的,已经焊死
    成一串英文 —— 沿用等于让所有老用户永远看不到「引路学长」。"""
    assert tray.AUMID != "yinlu.xuezhang.app"
    assert tray.AUMID_REG.endswith(tray.AUMID)


def test_file_uri_is_what_winrt_image_src_accepts(tmp_path):
    """裸盘符路径喂给 <image src> 会**静默**不显示图标,所以统一走 file:///。"""
    got = tray._file_uri(tmp_path / "assets" / "toast-icon.png")
    assert got.startswith("file:///")
    assert "\\" not in got
    assert got.endswith("toast-icon.png")


def test_register_notify_identity_failure_is_logged_not_raised(monkeypatch):
    _real_identity(monkeypatch)
    fake = FakeWinreg(write_error=PermissionError("策略禁止"))
    _use_fake_winreg(monkeypatch, fake)
    monkeypatch.setattr(tray, "_identity_done", False)
    r = tray.register_notify_identity()
    assert r["ok"] is False and "PermissionError" in r["msg"]
    assert "通知身份登记失败" in tray._ensure_parent(
        tray.app_path(tray.LOG_FILE)).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 工具入口

def test_desktop_notify_tool_status_without_tray(monkeypatch):
    """没托盘不再是"发不出去"的理由:说清 toast 不依赖托盘。"""
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    out = tray.desktop_notify(None, action="status")
    assert "桌面提醒:开" in out and "没有托盘" in out and "toast" in out
    assert "提示栏" in out


def test_desktop_notify_tool_status_lists_kinds(tray_icon):
    out_before = tray.desktop_notify(None, action="status")
    tray.set_notify(False, "calendar")
    out = tray.desktop_notify(None, action="status")
    assert "桌面提醒:开" in out
    assert "校历倒计时" in out_before.split("不弹")[0]     # 原本在"会弹"里
    head, rest = out.split("会弹的类别:", 1)
    on_part, off_part = rest.split("不弹:", 1)
    assert "校历倒计时" not in on_part and "校历倒计时" in off_part
    assert "课前提醒" in on_part


def test_desktop_notify_tool_on_off_and_kind_labels(tray_icon):
    assert "已把「课前提醒」这一类关掉" in tray.desktop_notify(
        None, action="off", kind="class")
    assert tray.notify_enabled("class") is False
    assert "已打开「课前提醒」这一类" in tray.desktop_notify(
        None, action="on", kind="class")
    assert tray.notify_enabled("class") is True
    assert "已把全部桌面提醒关掉" in tray.desktop_notify(None, action="off")


def test_desktop_notify_tool_rejects_bad_input(tray_icon):
    out = tray.desktop_notify(None, action="off", kind="课前")
    assert out.startswith("不认识的类别") and "class" in out        # 给出可选项
    assert "未知的 action" in tray.desktop_notify(None, action="弹窗")
    assert tray.notify_enabled("class") is True                     # 都没改动


def test_desktop_notify_tool_test_acts(tray_icon, monkeypatch):
    _c, icon, _ui = tray_icon
    _toast_works(monkeypatch)
    out = tray.desktop_notify(None, action="test")
    assert "WinRT toast" in out and icon.calls == []      # 说清走了哪条,没碰气球
    assert _TOAST_CALLS[-1]["title"] == "引路学长 · 测试"
    # 关掉总开关后 test 仍要能出(force),否则用户没法验证到底通不通
    tray.set_notify(False)
    assert "WinRT toast" in tray.desktop_notify(None, action="test")


def test_desktop_notify_tool_balloon_fallback_is_not_a_pass(tray_icon):
    """退回气球(autouse 夹具就是这情形)绝不能回"已发出":Win11 上它不显示。"""
    out = tray.desktop_notify(None, action="test")
    assert "不能算验证通过" in out and "Windows 11" in out


def test_desktop_notify_tool_test_explains_no_channel(monkeypatch):
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    monkeypatch.setattr(tray, "_seen", {})
    out = tray.desktop_notify(None, action="test")
    assert out.startswith("这条测试通知没能发出") and "PowerShell" in out


def test_desktop_notify_tool_log_answers_why_no_reminder(tmp_path, monkeypatch):
    """"提醒怎么没发"要能用嘴问到:action=log 就是读看门狗那份运行日志。"""
    from minicode import paths, proactive
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(proactive, "_logged_since_trim", 0)
    assert "还没有留下一行运行记录" in tray.desktop_notify(None, action="log")

    proactive.wd_log("skip calendar [国庆|2026-10-01]:今天已推过")
    out = tray.desktop_notify(None, action="log")
    assert "skip calendar" in out and "push=真推了" in out
    assert "watchdog.log" in out                       # 告诉用户文件在哪


# ============================================== 开机自动启动(v0.9.26)
#
# 一条红线:这些测试**绝不碰真机的「启动」文件夹**,也绝不真起 PowerShell ——
# 全都把 `startup_path` 钉到 tmp_path、把 `_run_ps` 换成替身。

class FakePS:
    """替身 `_run_ps`:照那两个真脚本的样子**动临时目录里的文件**。

    建快捷方式 → 真的在临时「启动」文件夹里落一个同名占位文件;读回 → 按文件在不在
    答 `MISSING` / `TARGET=<指向>`。这样 `startup_present()`(纯文件检查)和
    `startup_status()`(读指向)在测试里说的是同一套事实,不会两边各演一遍。
    两个脚本都含 `CreateShortcut`,靠**只有读回脚本才会打印的 `TARGET=`** 区分 ——
    于是"建完有没有真去读回来核对"从实现细节变成了被测行为。
    """

    def __init__(self, lnk, target=None, fail=None):
        self.lnk = lnk
        self.target = target            # None = 建成后就指向传进去的 YX_TARGET
        self.fail = fail                # 演 PowerShell 那一层失败
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, script, env_extra, timeout=None, need_ok=True):
        import os
        env = dict(env_extra)
        self.calls.append((script, env))
        if self.fail is not None:
            return dict(self.fail)
        if "TARGET=" in script:                         # 读回
            if not os.path.isfile(str(self.lnk)):
                return {"ok": True, "out": "MISSING"}
            return {"ok": True, "out": "TARGET=" + (self.target or "")}
        self.lnk.parent.mkdir(parents=True, exist_ok=True)   # 建快捷方式
        self.lnk.write_text("占位", encoding="utf-8")
        if self.target is None:
            self.target = env.get("YX_TARGET", "")
        return {"ok": True, "out": "OK"}

    def made(self):
        return [c for c in self.calls if "TARGET=" not in c[0]]


def _startup_env(monkeypatch, tmp_path, fake=None, target=None, installed=False):
    """把「启动」文件夹钉到临时目录 + 换上 `_run_ps` 替身,返回 (.lnk 路径, 替身)。

    `installed=True` 先把那个占位 .lnk 落下去 —— "已经开着"这件事在替身里和真
    脚本一样是**文件在不在**,不是另设一个布尔。
    """
    lnk = tmp_path / "Startup" / tray.STARTUP_NAME
    if installed:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk.write_text("占位", encoding="utf-8")
    monkeypatch.setattr(tray, "startup_path", lambda: str(lnk))
    fake = fake or FakePS(lnk, target=target)
    monkeypatch.setattr(tray, "_run_ps", fake)
    return lnk, fake


def test_launch_target_carries_the_background_flag(monkeypatch):
    """自启只该让学长在跑,不该开机糊一窗口的界面。"""
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\引路学长.exe")
    exe, args, wd = tray.launch_target()
    assert exe == r"C:\Apps\引路学长.exe"
    assert args == tray.BACKGROUND_FLAG and tray.BACKGROUND_FLAG in args
    assert wd == r"C:\Apps"


def test_launch_target_dev_uses_pythonw_and_script(monkeypatch):
    """开发态:参数是"脚本路径 + 进托盘标记",脚本路径要带引号(路径有空格也不裂)。"""
    import os
    import sys
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    exe, args, wd = tray.launch_target()
    assert os.path.basename(exe) in ("pythonw.exe", os.path.basename(sys.executable))
    assert "gui_main.py" in args
    assert args.startswith('"') and tray.BACKGROUND_FLAG in args.split('"')[-1]
    assert wd and os.path.isdir(wd)


def test_starts_hidden_only_when_the_flag_is_on_argv(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["gui_main.py", "--background"])
    assert tray.starts_hidden() is True
    monkeypatch.setattr(sys, "argv", ["gui_main.py"])
    assert tray.starts_hidden() is False
    assert tray.starts_hidden([]) is False
    assert tray.starts_hidden([tray.BACKGROUND_FLAG]) is True


def test_startup_path_follows_appdata_and_none_without_it(monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(tray, "startup_path", _REAL_STARTUP_PATH)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = tray.startup_path()
    assert p.endswith(os.path.join("Startup", tray.STARTUP_NAME))
    assert "Microsoft" in p and str(tmp_path) in p
    monkeypatch.delenv("APPDATA")
    assert tray.startup_path() == ""
    # 没有 APPDATA(或不是 Windows)必须"开不了",而不是建到别处去
    fake = FakePS(tmp_path / "Startup" / tray.STARTUP_NAME)
    monkeypatch.setattr(tray, "_run_ps", fake)
    st = tray.startup_status()
    assert st["supported"] is False and st["installed"] is False
    r = tray.set_startup(True)
    assert r["ok"] is False and "启动" in r["msg"]
    assert fake.calls == []                           # 一次 PowerShell 都没起


def test_startup_present_never_spawns_powershell(monkeypatch, tmp_path):
    """托盘每次右键都要画勾选态 —— 那一下绝不能冷启动 PowerShell。"""
    monkeypatch.setattr(tray, "startup_path",
                        lambda: str(tmp_path / "Startup" / tray.STARTUP_NAME))

    def boom(*a, **k):
        raise AssertionError("startup_present 不该起 PowerShell")

    monkeypatch.setattr(tray, "_run_ps", boom)
    assert tray.startup_present() is False
    (tmp_path / "Startup").mkdir(parents=True)
    (tmp_path / "Startup" / tray.STARTUP_NAME).write_text("", encoding="utf-8")
    assert tray.startup_present() is True


def test_startup_status_reads_missing_foreign_and_ours(monkeypatch, tmp_path):
    import os
    exe, _a, _w = tray.launch_target()

    _startup_env(monkeypatch, tmp_path)
    st = tray.startup_status()
    assert st["installed"] is False and st["ours"] is False
    assert st["want"] == exe

    # 别人放了同名快捷方式:装上了但不是我们的 —— 后面"关"的时候不能误删
    _startup_env(monkeypatch, tmp_path, target=r"C:\Other\notepad.exe",
                 installed=True)
    st = tray.startup_status()
    assert st["installed"] is True and st["ours"] is False
    assert st["target"] == r"C:\Other\notepad.exe"

    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=exe.upper().replace(os.path.basename(exe),
                                            os.path.basename(exe).upper()))
    assert tray.startup_status()["ours"] is True        # Windows 路径不分大小写

    _startup_env(monkeypatch, tmp_path, installed=True,
                 fake=FakePS(tmp_path / "Startup" / tray.STARTUP_NAME,
                             fail={"ok": False, "skipped": "no_powershell",
                                   "msg": "没有 PowerShell"}))
    assert tray.startup_status()["installed"] is False   # 读不到就当作没装


def test_set_startup_only_says_yes_after_reading_the_link_back(monkeypatch,
                                                               tmp_path):
    """建出来 ≠ 开好了:必须读回 .lnk 的指向、确认是本应用才算数。"""
    lnk = tmp_path / "Startup" / tray.STARTUP_NAME
    monkeypatch.setattr(tray, "startup_path", lambda: str(lnk))
    exe, _a, _w = tray.launch_target()

    lnk, fake = _startup_env(monkeypatch, tmp_path)      # 建成后就指向 YX_TARGET
    r = tray.set_startup(True)
    assert r["ok"] is True and r["enabled"] is True
    assert r["target"] == exe and lnk.exists()
    assert tray.startup_present() is True
    assert len(fake.made()) == 1 and len(fake.calls) - len(fake.made()) == 2

    # 脚本回 OK 但 .lnk 指向别人 → 不敢算已开启
    _startup_env(monkeypatch, tmp_path, target=r"C:\Elsewhere\x.exe")
    r = tray.set_startup(True)
    assert r["ok"] is False and r["enabled"] is False
    assert "指向不对" in r["msg"]


def test_set_startup_passes_text_only_through_pure_string_env(monkeypatch,
                                                              tmp_path):
    """v0.9.25 那条坑的回归:env 里塞 Path 会让 Windows 建进程当场抛 TypeError,
    而图标不存在那一支测不到 —— 所以这里让图标"真的存在"再逐个查类型。

    走**真的** `_run_ps`(只换掉 subprocess.run),把 -EncodedCommand 解回脚本文本
    来区分"建快捷方式"和"读回指向":这样连"建完有没有再读一次"都一起验了。
    """
    import base64
    import subprocess as sp
    # 只钉路径,**不**换 `_run_ps`:这一条要走的正是真的 `_run_ps` + 假 subprocess
    lnk = tmp_path / "Startup" / tray.STARTUP_NAME
    monkeypatch.setattr(tray, "startup_path", lambda: str(lnk))
    calls = []
    made = {}

    def fake_run(argv, **kw):
        env = kw["env"]
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        calls.append({"argv": list(argv), "env": dict(env), "script": script})
        if "TARGET=" in script:                      # 读回:照建的时候说
            return FakeProc(out=f"TARGET={made.get('target', '')}\r\n".encode())
        made["target"] = env["YX_TARGET"]
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk.write_text("占位", encoding="utf-8")
        return FakeProc()                            # stdout = "OK"

    monkeypatch.setattr(sp, "run", fake_run)
    _fake_ps(tmp_path, monkeypatch)
    icon = _fake_icon(tmp_path, monkeypatch)          # resource_path 返回 Path
    r = tray.set_startup(True)
    assert r["ok"] is True and lnk.exists()
    made_call = [c for c in calls if "TARGET=" not in c["script"]]
    assert len(calls) == 3 and len(made_call) == 1    # 先看现状 → 建 → 再读回核对
    seen = made_call[0]
    assert any(a == "-EncodedCommand" for a in seen["argv"])
    assert {"YX_LNK", "YX_TARGET", "YX_ARGS", "YX_WORKDIR", "YX_DESC",
            "YX_ICON"} <= set(seen["env"])
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in seen["env"].items()), \
        {k: type(v).__name__ for k, v in seen["env"].items()
           if not isinstance(v, str)}
    assert seen["env"]["YX_ICON"] == str(icon)        # 是字符串,不是 Path 对象
    assert tray.STARTUP_NAME not in " ".join(seen["argv"])    # 中文名字不进 argv
    assert "TARGET=" in calls[-1]["script"]           # 最后那次是读回来核对


def test_set_startup_close_never_deletes_a_foreign_shortcut(monkeypatch,
                                                            tmp_path):
    lnk, _f = _startup_env(monkeypatch, tmp_path, installed=True,
                           target=r"C:\Other\thing.exe")
    r = tray.set_startup(False)
    assert r["ok"] is False and r["enabled"] is True
    assert "不动别人的文件" in r["msg"]
    assert lnk.exists()                                  # 删了不可恢复,所以没删

    # 是我们的那个才动手
    exe = tray.launch_target()[0]
    lnk, _f = _startup_env(monkeypatch, tmp_path, installed=True, target=exe)
    r = tray.set_startup(False)
    assert r["ok"] is True and r["enabled"] is False
    assert not lnk.exists() and tray.startup_present() is False
    # 本来就没开:如实说"本来就没开",不报错也不甩锅
    assert tray.set_startup(False)["msg"] == "本来就没开"


def test_set_startup_reports_powershell_failure(monkeypatch, tmp_path):
    _startup_env(monkeypatch, tmp_path,
                 fake=FakePS(tmp_path / "Startup" / tray.STARTUP_NAME,
                             fail={"ok": False, "skipped": "ps",
                                   "msg": "退出码 1:找不到驱动器"}))
    r = tray.set_startup(True)
    assert r["ok"] is False and r["enabled"] is False
    assert "退出码 1" in r["msg"]
    assert tray.startup_present() is False              # 没开就是没开


def test_tray_menu_toggle_flips_autostart(tray_icon, monkeypatch):
    """托盘那一下点的是"翻转当前状态",不是"永远开"。"""
    c, _icon, _ui = tray_icon
    calls = []
    monkeypatch.setattr(tray, "startup_present", lambda: False)
    monkeypatch.setattr(tray, "set_startup",
                        lambda on: (calls.append(on), {"ok": True,
                                                       "enabled": on})[1])
    c._tray_toggle_startup()
    assert calls == [True]
    monkeypatch.setattr(tray, "startup_present", lambda: True)
    c._tray_toggle_startup()
    assert calls == [True, False]


def test_tray_menu_toggle_logs_the_real_failure(tray_icon, monkeypatch):
    """菜单勾上却没真开成,日志要说清原因(否则下次开机才发现是空的)。"""
    c, _icon, _ui = tray_icon
    monkeypatch.setattr(tray, "startup_present", lambda: False)
    monkeypatch.setattr(tray, "set_startup",
                        lambda on: {"ok": False, "enabled": False,
                                    "msg": "PowerShell 没建成"})
    c._tray_toggle_startup()
    tail = tray._ensure_parent(tray.app_path(tray.LOG_FILE)
                               ).read_text(encoding="utf-8")
    assert "开机自启 → 开 (失败: PowerShell 没建成)" in tail


# ---------------------------------------------------------------- 工具入口

def test_desktop_notify_tool_startup_on_and_off(tray_icon, monkeypatch, tmp_path):
    lnk, _f = _startup_env(monkeypatch, tmp_path)
    out = tray.desktop_notify(None, action="startup_on")
    assert "已开启开机自启" in out and tray.STARTUP_NAME in out
    assert lnk.exists() and tray.startup_present() is True
    out = tray.desktop_notify(None, action="startup_off")
    assert "已关掉开机自启" in out
    assert not lnk.exists() and tray.startup_present() is False


def test_desktop_notify_tool_startup_failure_is_not_played_off(tray_icon,
                                                               monkeypatch,
                                                               tmp_path):
    """回「已开启」而其实没开 = 用户下次开机才发现提醒全哑,所以必须回失败。"""
    lnk, _f = _startup_env(monkeypatch, tmp_path, installed=True,
                           target=r"C:\Other\a.exe")
    out = tray.desktop_notify(None, action="startup_off")
    assert "开机自启没能关掉" in out and "不动别人的文件" in out
    assert "现在的提醒一条没少" in out
    assert lnk.exists()


def test_desktop_notify_tool_startup_without_startup_folder(monkeypatch):
    """没有「启动」文件夹的环境:说开不了,而不是"已开启"。"""
    monkeypatch.setattr(tray, "startup_path", lambda: "")
    out = tray.desktop_notify(None, action="startup_on")
    assert "没能打开" in out and "启动" in out


def test_desktop_notify_tool_status_reports_autostart(tray_icon, monkeypatch,
                                                     tmp_path):
    _startup_env(monkeypatch, tmp_path)
    out = tray.desktop_notify(None, action="status")
    assert "开机自启:没开" in out
    exe = tray.launch_target()[0]
    _startup_env(monkeypatch, tmp_path, installed=True, target=exe)
    assert "开机自启:已开启" in tray.desktop_notify(None, action="status")
    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=r"C:\Other\b.exe")
    out = tray.desktop_notify(None, action="status")
    assert "开机自启:没开" in out and "不是本应用" in out


def test_desktop_notify_tool_unknown_action_lists_autostart(tray_icon):
    out = tray.desktop_notify(None, action="开机")
    assert "未知的 action" in out
    assert "startup_on" in out and "startup_off" in out


def test_start_in_tray_is_honest_when_tray_missing(tray_icon, monkeypatch):
    """藏不进托盘就必须回 False:调用方据此把窗口显示出来,别留个隐形进程。"""
    c, _icon, _ui = tray_icon
    assert tray.start_in_tray() is True          # 夹具里 _ensure_icon 已可用
    c2, _ui2 = _controller()
    c2._ensure_icon = lambda: False
    monkeypatch.setattr(tray, "_CONTROLLER", c2)
    assert tray.start_in_tray() is False
    monkeypatch.setattr(tray, "_CONTROLLER", None)
    assert tray.start_in_tray() is False



def test_tray_log_rolls_at_cap(tmp_path, monkeypatch):
    from minicode import tray, paths
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(tray, "LOG_CAP_LINES", 10)
    for i in range(15):
        tray.log(f"line {i}")
    log = tmp_path / "app" / "tray.log"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert "line 14" in lines[-1] and "line 0" not in "".join(lines)


# ------------------------------------------- 工具描述对账 + 任务查询(v0.9.79)

def test_desktop_notify_kind_enum_matches_notify_kinds():
    """v0.9.79 审计实锤:工具描述里的 kind 枚举曾只列 8 类(实际 11 类)——
    模型照描述列不出 review/gaps/recovery,教不会用户关周报。这条闸钉住:
    描述与参数里出现过的类别集合必须逐类等于 NOTIFY_KINDS。"""
    import re
    from pathlib import Path
    # v0.9.98:desktop_notify 的定义随提醒一族搬进了 catalog_remind
    from minicode.tools import catalog_remind
    src = Path(catalog_remind.__file__).read_text(encoding="utf-8")
    block = src[src.index('name="desktop_notify"'):]
    block = block[:block.index("handler=tray.desktop_notify")]
    mentioned = set()
    for m in re.finditer(r"(briefing|class|schedule|reminders|calendar|notices|"
                         r"update|weather|review|gaps|recovery)", block):
        mentioned.add(m.group(1))
    assert mentioned == set(tray.NOTIFY_KINDS), (
        f"desktop_notify 描述的类别集与 NOTIFY_KINDS 漂了:少 {set(tray.NOTIFY_KINDS) - mentioned},"
        f"多 {mentioned - set(tray.NOTIFY_KINDS)} —— 加类别时两处要一起改")


def test_desktop_notify_jobs_answers_did_it_run():
    """「这周的任务跑没跑」的查询口:报出注册的任务与节奏。"""
    out = tray.desktop_notify(None, action="jobs")
    assert "定时任务 2 个" in out
    assert "mood_weekly_review" in out and "kb_gap_digest" in out
    assert "每周周日" in out and "每周周六" in out


# ------------------------------------------------ tray.log 有人读(v0.9.96)


def test_log_tail_reads_back_what_log_wrote_in_order():
    """`log()` 写的东西要能从这里读回来 —— 以前 tray.log 是只写不读的。"""
    tray.log("托盘图标已就绪")
    tray.log("桌面通知失败: toast=没有 PowerShell 气球=没试")
    rows = tray.log_tail(10)
    assert len(rows) == 2, rows
    assert rows[0].endswith("托盘图标已就绪")          # 先后顺序不反
    assert "气球=没试" in rows[1]
    assert rows[0][:10] == time.strftime("%Y-%m-%d")   # 每行开头是当天时刻,不是凑的


def test_log_tail_is_silent_when_there_is_no_log(tmp_path):
    """没日志 = 空列表,不是抛异常:查状态的那条路不能因为文件不存在而红。"""
    assert tray.log_tail(5) == []


def test_action_tray_log_answers_the_three_real_questions():
    """「通知没弹 / 点 X 直接退了 / 开机自启没生效」这三件事从此问得出来。"""
    tray.log(r"开机自启:开(C:\Start\x.lnk → D:\app\x.exe)")
    out = tray.desktop_notify(None, action="tray_log")
    assert "开机自启:开" in out
    assert "没有通知正文" in out                    # 口径照写:不谎称什么都有
    assert "action=log" in out                      # 说清与看门狗日志的分工


def test_action_tray_log_says_so_when_empty():
    out = tray.desktop_notify(None, action="tray_log")
    assert "还没留下一行诊断" in out
    assert "命令行/网页版" in out                   # 给的是原因,不是一句"没有"


def test_notify_actions_are_one_source_not_two_copies():
    """工具 schema 的 enum 与 handler 认的 action 必须同一份 —— 手抄的第二份一定漂。

    漂了的后果不是报错,是"模型照着描述调,参数校验先把它挡在门外",
    而用户看到的正是"这个功能没做"。
    """
    from minicode.tools.registry import build_default_registry
    tool = next(t for t in build_default_registry() if t.name == "desktop_notify")
    assert tool.parameters["properties"]["action"]["enum"] == list(tray.NOTIFY_ACTIONS)
    for action in tray.NOTIFY_ACTIONS:
        assert action in tool.parameters["properties"]["action"]["description"], action
    unknown = tray.desktop_notify(None, action="弹窗")
    for action in tray.NOTIFY_ACTIONS:
        assert action in unknown, f"未知 action 那句没把所有出口列全: {unknown}"


# ------------------------------------------- 自启「位置变了」探针与修复(v0.9.98)

class _PushRecorder:
    """最小 ui 替身:只记 push 的事件,check_stale_startup 只用这一条通道。"""

    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        self.events.append((type_, payload))


def test_startup_stale_flags_same_app_old_path_only(monkeypatch, tmp_path):
    import os
    exe, _a, _w = tray.launch_target()

    # 没开自启:checked=False,谈不上陈旧
    _startup_env(monkeypatch, tmp_path)
    st = tray.startup_stale()
    assert st["checked"] is False and st["stale"] is False

    # 本应用换过位置:同名 exe、路径不同 → stale
    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=exe.replace(os.path.basename(exe),
                                    os.path.basename(exe)))  # 先放一个同路径的
    # 换成「旧目录」:同 basename、不同目录
    old = os.path.join(os.path.dirname(exe), "old", os.path.basename(exe))
    _startup_env(monkeypatch, tmp_path, installed=True, target=old)
    st = tray.startup_stale()
    assert st["checked"] is True and st["same_app"] is True and st["stale"] is True

    # 别人的程序:同名都不同 → 不是 stale,也绝不给出"可重建"的口实
    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=r"C:\Other\notepad.exe")
    st = tray.startup_stale()
    assert st["same_app"] is False and st["stale"] is False


def test_repair_only_touches_our_own_stale_shortcut(monkeypatch, tmp_path):
    import os
    exe, _a, _w = tray.launch_target()
    old = os.path.join(os.path.dirname(exe), "old", os.path.basename(exe))

    # 别人的文件:拒绝动手,set_startup 一次都不该被调
    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=r"C:\Other\notepad.exe")
    called = []
    monkeypatch.setattr(tray, "set_startup", lambda v: called.append(v) or {"ok": True})
    r = tray.repair_startup_shortcut()
    assert r["ok"] is False and "不动它" in r["msg"] and called == []

    # 没开自启:没得修
    _startup_env(monkeypatch, tmp_path)
    r = tray.repair_startup_shortcut()
    assert r["ok"] is False and "没开" in r["msg"]

    # 同应用旧位置:走 set_startup(True) 重建
    _startup_env(monkeypatch, tmp_path, installed=True, target=old)
    r = tray.repair_startup_shortcut()
    assert called == [True], "重建必须走 set_startup 那条已验证的路"


def test_check_stale_startup_speaks_once_with_the_fix(monkeypatch, tmp_path):
    import os
    exe, _a, _w = tray.launch_target()
    old = os.path.join(os.path.dirname(exe), "old", os.path.basename(exe))
    ui = _PushRecorder()

    # 指向正确:一声不吭
    _startup_env(monkeypatch, tmp_path, installed=True, target=exe)
    tray.check_stale_startup(ui)
    assert ui.events == []

    # 别人的文件:也不吭声(每次启动都打扰等于把用户自己的安排当故障报)
    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=r"C:\Other\notepad.exe")
    tray.check_stale_startup(ui)
    assert ui.events == []

    # 同应用旧位置:toast 一条,话里带怎么修
    _startup_env(monkeypatch, tmp_path, installed=True, target=old)
    tray.check_stale_startup(ui)
    assert len(ui.events) == 1
    kind, payload = ui.events[0]
    assert kind == "info" and "修好开机自启" in payload["text"]


def test_status_names_the_stale_case_and_how_to_fix(monkeypatch, tmp_path):
    import os
    exe, _a, _w = tray.launch_target()
    old = os.path.join(os.path.dirname(exe), "old", os.path.basename(exe))

    _startup_env(monkeypatch, tmp_path, installed=True, target=old)
    out = tray.desktop_notify(None, action="status")
    assert "指错位置" in out and "修好开机自启" in out

    _startup_env(monkeypatch, tmp_path, installed=True,
                 target=r"C:\Other\b.exe")
    out = tray.desktop_notify(None, action="status")
    assert "开机自启:没开" in out and "不是本应用" in out
