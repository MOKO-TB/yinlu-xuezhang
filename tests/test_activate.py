"""通知点击直达(v0.9.72):落点表 / URI 解析 / 协议注册读回 / 单实例交接。

钉住的口径:
- **KIND_PANELS 是唯一落点真相**:键 ⊆ tray.NOTIFY_KINDS(新通知类别忘了
  考虑落点也不会静默长出错误面板),值 ⊆ PANELS 白名单;
- **parse 在后端收口**:不认识的面板名降级成 focus(前端收到的永远是
  白名单内的值);不是本应用 scheme 的 URI 一律拒;
- **注册写完必须读回**(v0.9.26 自启那条教训);指向 CLI 等于让它把
  URI 当任务跑,所以非 GUI exe 要改指同目录的 GUI exe;
- **交接失败就是失败**:没在跑/端口文件过期都如实 False,绝不假装送达。
"""

import json
from pathlib import Path

from minicode import activate as act
from minicode import tray


# ------------------------------------------------------------ 落点表

def test_kind_panels_keys_are_real_notification_kinds():
    assert set(act.KIND_PANELS) <= set(tray.NOTIFY_KINDS), \
        "落点表里出现 NOTIFY_KINDS 之外的类别 —— 新类别忘了考虑落点或写错名字"
    assert set(act.KIND_PANELS.values()) <= set(act.PANELS)


def test_panel_uri_mapping():
    assert act.panel_uri("briefing") == "yinlu:panel=schedule"
    assert act.panel_uri("class") == "yinlu:panel=schedule"
    assert act.panel_uri("reminders") == "yinlu:panel=schedule"
    assert act.panel_uri("review") == "yinlu:panel=mood"
    # 没有面板落点的类别:点通知 = 把窗口弹回来
    assert act.panel_uri("notices") == "yinlu:focus"
    assert act.panel_uri("update") == "yinlu:focus"
    assert act.panel_uri("") == ""                 # 「测试一条」不设激活


# ------------------------------------------------------------ URI 解析

def test_parse_uri_valid_and_focus():
    assert act.parse_uri("yinlu:panel=schedule") == {"panel": "schedule"}
    assert act.parse_uri("YINLU:panel=mood") == {"panel": "mood"}
    assert act.parse_uri("yinlu:") == {"panel": None}       # 只弹回窗口


def test_parse_uri_rejects_and_downgrades():
    assert act.parse_uri("http://evil") == {}               # 不是本应用的 scheme
    assert act.parse_uri("") == {}
    assert act.parse_uri(None) == {}
    assert act.parse_uri("yinlu:panel=registry") == {"panel": None}, \
        "白名单外的落点降级成 focus —— 前端收到的永远是认识的值"


def test_pending_panel_take_once():
    act.set_pending_panel("schedule")
    assert act.take_pending_panel() == "schedule"
    assert act.take_pending_panel() is None                 # 一次性取走


def test_pending_uri_arg():
    assert act.pending_uri_arg(["--activate", "yinlu:focus"]) == "yinlu:focus"
    assert act.pending_uri_arg(["--background"]) is None
    assert act.pending_uri_arg([]) is None


# ------------------------------------------------------------ 协议注册(假 reg)

class _FakeReg:
    """最小 winreg 替身:记录写值,读回时给"写过的"或被注入篡改的值。"""

    HKEY_CURRENT_USER = 0x80000001        # 常量值无所谓,产品代码只透传
    REG_SZ = "REG_SZ"

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):
        return _Key(self, path)

    def OpenKey(self, root, path):
        if path not in self.values:
            raise OSError("not found")
        return _Key(self, path)

    def SetValueEx(self, key, name, _r, _t, value):
        key._store(name, value)

    def QueryValueEx(self, key, name):
        got = self.values.get(key.path, {}).get(name)
        if got is None:
            raise OSError("missing")
        return got, None


class _Key:
    def __init__(self, reg, path):
        self._reg = reg
        self.path = path

    def _store(self, name, value):
        self._reg.values.setdefault(self.path, {})[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_register_protocol_writes_and_reads_back(tmp_path):
    reg = _FakeReg()
    exe = tmp_path / "引路学长.exe"
    r = act.register_protocol(str(exe), reg=reg)
    assert r["ok"] is True
    cmd = reg.values[r"Software\Classes\yinlu\shell\open\command"][""]
    assert cmd == f'"{exe}" --activate "%1"'
    assert reg.values[r"Software\Classes\yinlu"]["URL Protocol"] == ""


def test_register_protocol_readback_mismatch_is_honest(tmp_path):
    reg = _FakeReg()
    # 注入篡改:读回时永远给错的命令
    original = _FakeReg.QueryValueEx

    def bad_query(self, key, name):
        return "被改过的命令", None

    _FakeReg.QueryValueEx = bad_query
    try:
        r = act.register_protocol(str(tmp_path / "引路学长.exe"), reg=reg)
    finally:
        _FakeReg.QueryValueEx = original
    assert r["ok"] is False and "读回不一致" in r["msg"]


def test_register_protocol_cli_exe_points_to_gui_sibling(tmp_path):
    """从 CLI 进程发起注册:协议必须指向同目录的 GUI exe —— 指向 CLI 等于
    让它把 URI 当任务跑。旁边没有 GUI exe 就如实跳过。"""
    reg = _FakeReg()
    cli = tmp_path / "引路学长-CLI.exe"
    cli.write_bytes(b"")
    gui = tmp_path / "引路学长.exe"
    gui.write_bytes(b"")
    r = act.register_protocol(str(cli), reg=reg)
    assert r["ok"] is True
    cmd = reg.values[r"Software\Classes\yinlu\shell\open\command"][""]
    assert str(gui) in cmd and str(cli) not in cmd
    # 旁边没有 GUI exe:跳过,绝不把协议指向 CLI
    reg2 = _FakeReg()
    lone = tmp_path / "lonely" / "引路学长-CLI.exe"
    lone.parent.mkdir()
    r2 = act.register_protocol(str(lone), reg=reg2)
    assert r2["ok"] is False and r2.get("skipped") == "no-gui-exe"


# ------------------------------------------------------------ 单实例交接

def test_listener_and_handoff_roundtrip(tmp_path, monkeypatch):
    got: list = []
    monkeypatch.setattr(act, "port_path", lambda: tmp_path / "activate-port.json")
    r = act.start_listener(got.append)
    try:
        assert r["ok"] is True
        port = json.loads((tmp_path / "activate-port.json").read_text())["port"]
        assert port == r["port"]
        assert act.handoff("yinlu:panel=schedule") is True
    finally:
        r["server"].shutdown()
    assert got == [{"panel": "schedule"}]


def test_handoff_without_port_file_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(act, "port_path",
                        lambda: tmp_path / "nope.json")
    assert act.handoff("yinlu:focus") is False


def test_handoff_to_dead_port_is_false(tmp_path, monkeypatch):
    p = tmp_path / "activate-port.json"
    p.write_text(json.dumps({"port": 1}))          # 1 号端口不会有服务在听
    monkeypatch.setattr(act, "port_path", lambda: p)
    assert act.handoff("yinlu:focus", timeout=0.3) is False


def test_listener_rejects_foreign_scheme(tmp_path, monkeypatch):
    got: list = []
    monkeypatch.setattr(act, "port_path", lambda: tmp_path / "activate-port.json")
    r = act.start_listener(got.append)
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{r['port']}/activate",
            data=b"http://evil", method="POST")
        try:
            urllib.request.urlopen(req, timeout=1.5)
            raised = False
        except Exception:
            raised = True
        assert raised, "不是本应用 scheme 的激活必须被拒"
    finally:
        r["server"].shutdown()
    assert got == [], "拒绝的 URI 不进回调"


def test_listener_port_file_write_failure_is_honest(tmp_path, monkeypatch):
    """端口文件写不进去 = 交接永远找不到这一个:如实回 ok=False,不装支持。"""
    got: list = []
    readonly = tmp_path / "ro"
    readonly.mkdir()

    def broken_path():
        return readonly / "sub" / "activate-port.json"   # 父目录不存在且建不出

    monkeypatch.setattr(act, "port_path", broken_path)
    monkeypatch.setattr(Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    r = act.start_listener(got.append)
    try:
        assert r["ok"] is False and "端口文件" in r["msg"]
    finally:
        r["server"].shutdown()
