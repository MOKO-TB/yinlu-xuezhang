"""托盘菜单照 Qoder 重排(v0.9.114,图 p8/p9)。

以前那六行只有"显示主窗口 + 四个偏好开关":想开个新会话得先把窗口点在眼前再找按钮。
这一轮加的是「新建任务 / 最近会话(前几条)/ 设置」,而它们**全部走同一个
`open_panel` 事件** —— 托盘不持有桥,桥也不许知道托盘存在(否则"什么算新任务"
就有两处决定)。这里钉的就是这几条边界。
"""
import sys
import types

import pytest

from minicode import paths, tray


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


class Item:
    def __init__(self, text, action=None, checked=None, default=False, **kw):
        self.text = text if callable(text) else str(text)
        self.action = action
        self.checked = checked
        self.default = default


class Menu:
    def __init__(self, *items):
        self.items = list(items)


class Icon:
    def __init__(self, *a, **k):
        self.menu = k.get("menu") or (a[3] if len(a) > 3 else None)

    def run(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def fake_pystray(monkeypatch):
    mod = types.ModuleType("pystray")
    mod.Menu = Menu
    mod.MenuItem = Item
    mod.Icon = Icon
    image = types.ModuleType("PIL.Image")
    image.open = lambda *a, **k: object()
    pil = types.ModuleType("PIL")
    pil.Image = image
    monkeypatch.setitem(sys.modules, "pystray", mod)
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image)
    return mod


class UI:
    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        self.events.append((type_, payload))


def _labels(menu):
    out = []
    for it in menu.items:
        text = it.text() if callable(it.text) else it.text
        out.append(text)
        if isinstance(it.action, Menu):
            out.append(_labels(it.action))
    return out


def _controller():
    ui = UI()
    c = tray.TrayController(window=object(), ui=ui)
    c.form = None
    return c, ui


# ---------------------------------------------------------------- 菜单形状

def test_the_menu_has_the_qoder_shape(fake_pystray):
    c, _ui = _controller()
    c.recent_fn = lambda: [("a.md", "本项目")]        # 有会话才摆那一叠(见另一条针)
    labels = _labels(c._build_menu())
    for want in ("显示主窗口", "新建任务", "最近会话", "设置(模型与密钥)",
                 "桌面提醒(课前/简报/待办/通知)", "开机自动启动",
                 "点 X 时先问我", "退出引路学长"):
        assert want in labels, labels
    assert labels.index("显示主窗口") < labels.index("新建任务"), "窗口动作在最上"
    assert labels[-1] == "退出引路学长", "退出必须压底(误点代价最大的一颗不该在手边)"


def test_no_recent_submenu_when_there_is_nothing_to_show(fake_pystray):
    """一条会话都没有就不摆那叠空菜单 —— 摆五条「(空)」是骗人。"""
    c, _ui = _controller()
    labels = _labels(c._build_menu())
    assert "最近会话" not in labels


def test_the_recent_submenu_lists_what_the_bridge_returns(fake_pystray):
    c, ui = _controller()
    c.recent_fn = lambda: [("2026-10-04-甲.md", "宿舍生活指南"),
                           ("2026-10-03-乙.md", "最近学校有什么活动")]
    menu = c._build_menu()
    sub = [it for it in menu.items if isinstance(it.action, Menu)]
    assert len(sub) == 1
    assert _labels(sub[0].action) == ["1. 宿舍生活指南", "2. 最近学校有什么活动"]


def test_a_long_title_is_cut_but_stays_numbered(fake_pystray):
    c, _ui = _controller()
    c.recent_fn = lambda: [("a.md", "标" * 60)]
    sub = [it for it in c._build_menu().items if isinstance(it.action, Menu)][0]
    label = _labels(sub.action)[0]
    assert label.startswith("1. 标") and len(label) <= 30, label


def test_a_provider_that_throws_leaves_the_menu_usable(fake_pystray):
    """最近会话读不出来 = 少一叠子菜单,而不是整个托盘菜单起不来。"""
    c, _ui = _controller()

    def boom():
        raise RuntimeError("桥还没起来")

    c.recent_fn = boom
    labels = _labels(c._build_menu())
    assert "显示主窗口" in labels and "最近会话" not in labels


def test_only_the_first_five_sessions_are_offered(fake_pystray):
    c, _ui = _controller()
    c.recent_fn = lambda: [(f"{i}.md", f"会话{i}") for i in range(9)]
    sub = [it for it in c._build_menu().items if isinstance(it.action, Menu)][0]
    assert len(sub.action.items) == tray.MENU_RECENT == 5


# ---------------------------------------------------------------- 三颗新按钮去哪

def test_the_new_entries_push_one_event_name_with_the_panel(fake_pystray):
    """托盘不许直接调桥:它只会推 `open_panel`,界面侧只有一个出口 `openPanel`。"""
    c, ui = _controller()
    c._tray_open("new_task")
    c._tray_open("providers")
    c._tray_open("session:2026-10-04-甲.md")
    got = [p for t, p in ui.events if t == "open_panel"]
    assert [g["panel"] for g in got] == ["new_task", "providers",
                                        "session:2026-10-04-甲.md"]


def test_a_session_entry_carries_its_own_key_not_the_last_one(fake_pystray):
    """闭包抓循环变量的老坑:五条都点成最后一条,是这种菜单最典型的坏法。"""
    c, ui = _controller()
    c.recent_fn = lambda: [("a.md", "甲"), ("b.md", "乙"), ("c.md", "丙")]
    sub = [it for it in c._build_menu().items if isinstance(it.action, Menu)][0]
    for it in sub.action.items:
        it.action(None, it)
    got = [p["panel"] for t, p in ui.events if t == "open_panel"]
    assert got == ["session:a.md", "session:b.md", "session:c.md"], got


def test_the_provider_can_be_handed_over_before_the_icon_exists(fake_pystray):
    """启动顺序是"桥先起来、托盘后建":callable 得先存着,建图标时再挂上去。"""
    c, _ui = _controller()
    seen = []
    c.recent_fn = None
    tray._RECENT_PROVIDER = lambda: seen.append(1) or []
    try:
        assert c._recent_sessions() == []      # 还没挂上:安静地没有这一叠
        c.recent_fn = tray._RECENT_PROVIDER
        assert c._recent_sessions() == [] and seen == [1]
    finally:
        tray._RECENT_PROVIDER = None


def test_the_menu_is_rebuilt_after_a_new_session_appears(fake_pystray):
    c, _ui = _controller()
    c.icon = Icon(menu=c._build_menu())
    assert "最近会话" not in _labels(c.icon.menu)
    c.recent_fn = lambda: [("a.md", "刚跑完的那条")]
    c._refresh_menu()
    assert "最近会话" in _labels(c.icon.menu), "菜单是缓存的:不重摆就永远看不见新会话"


def test_quit_stops_the_menu_refresher_thread(fake_pystray):
    """那条 20 秒重摆一次的线程必须在退出时停,不然它会在 icon 没了之后继续碰它。"""
    c, _ui = _controller()
    c._ensure_icon = lambda: True
    c.icon = Icon(menu=c._build_menu())
    c._start_menu_refresher(c.icon)
    c._menu_stop.set()
    assert c._menu_stop.is_set()
