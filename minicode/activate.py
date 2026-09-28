r"""通知点击直达(v0.9.72):点桌面通知 → 窗口弹回 + 落到对应面板。

三块拼图都住这里:
- **协议注册**:`HKCU\Software\Classes\yinlu`(URL Protocol + shell\open\command
  指向 GUI exe + `--activate`)。沿用 AUMID 注册那条教训(v0.9.25):
  **写完必须读回**,对不上就不敢说已注册;命令跟着 GUI exe 走,便携版照样
  有效 —— 「只有安装版才能点」的旧边界就此退役。源码运行不注册(开发机
  不往注册表写东西),toast 保持原行为。
- **落点表**:哪些通知值得落到哪个面板(`KIND_PANELS`,唯一真相);
  没有面板落点的类别给 `yinlu:focus`(只把窗口弹回),**不认识的面板名
  在 parse 这一层就降级成 focus** —— 面板名集合只在后端存在,前端收到的
  永远是白名单内的值。
- **单实例交接**:在跑的实例在 127.0.0.1 起一个激活监听(端口写进
  `activate-port.json`),`--activate` 的第二个进程先 POST 交接,送达即
  退出、绝不双开;交接失败(没在跑/端口文件过期)才自己启动并把落点
  带进首屏(get_info 一次性取走)。

零新依赖:监听用标准库 http.server,交接用 urllib;注册用 winreg(仅
冻结版),测试可注入假 reg 对象,不碰真注册表。
"""
from __future__ import annotations

import json
import threading

SCHEME = "yinlu"
ACTIVATE_FLAG = "--activate"
PROTOCOL_REG = r"Software\Classes\yinlu"
PORT_FILE = "activate-port.json"

# 通知类别 → 落点面板(唯一真相)。键必须是 tray.NOTIFY_KINDS 里的类别,
# 值必须是前端 openPanel 认识的名字(白名单同时是 parse 的降级依据)。
KIND_PANELS: dict[str, str] = {
    "briefing": "schedule", "class": "schedule", "schedule": "schedule",
    "calendar": "schedule", "reminders": "schedule",
    "review": "mood",
}
PANELS = tuple(sorted(set(KIND_PANELS.values())))


def panel_uri(kind: str) -> str:
    """这一类通知的激活 URI:有面板落 `yinlu:panel=xx`,没有落 `yinlu:focus`
    (点通知 = 把窗口弹回来 —— 人不在窗口前,看得见那条字也没法操作);
    类别为空(如「测试一条」)给空串,toast 行为与之前完全一致。"""
    k = str(kind or "").strip().lower()
    if not k:
        return ""
    panel = KIND_PANELS.get(k)
    return f"{SCHEME}:panel={panel}" if panel else f"{SCHEME}:focus"


def parse_uri(uri: str) -> dict:
    """解析激活 URI:{"panel": "schedule"|"None"};不是本应用的 scheme 给 {}。

    落点白名单在这里收口 —— 面板名集合只存在于 KIND_PANELS 的值里,
    前端拿到的永远是它认识的。
    """
    text = str(uri or "").strip()
    if not text.lower().startswith(SCHEME + ":"):
        return {}
    params: dict[str, str] = {}
    for part in text[len(SCHEME) + 1:].split("&"):
        k, _, v = part.partition("=")
        if k.strip():
            params[k.strip().lower()] = v.strip().lower()
    if not params:
        return {"panel": None}
    panel = params.get("panel")
    if panel not in PANELS:
        panel = None
    return {"panel": panel}


# ------------------------------------------------------------ 协议注册

def register_protocol(exe_path: str, reg=None) -> dict:
    """把 `yinlu:` 协议登记进 HKCU(免管理员);写完读回核对。

    reg 可注入假对象(测试不碰真注册表);不注入时:源码运行如实跳过
    (开发机不往注册表写东西),冻结版才真写。注册指向的是 **GUI exe**
    —— 调用方给的是 CLI exe 时,按「同目录的 <应用名>.exe」改指 GUI
    (打包 spec 定死的名字),找不到就跳过:把点击引到 CLI 等于让它把
    URI 当任务跑。
    """
    import os
    import sys
    from minicode import APP_NAME
    exe = str(exe_path or sys.executable)
    if os.path.basename(exe).lower() != APP_NAME.lower() + ".exe":
        sibling = os.path.join(os.path.dirname(exe), APP_NAME + ".exe")
        if os.path.isfile(sibling):
            exe = sibling
        else:
            return {"ok": False, "skipped": "no-gui-exe",
                    "msg": f"旁边没有 {APP_NAME}.exe,不把协议指向 CLI"}
    if reg is None:
        if not getattr(sys, "frozen", False):
            return {"ok": False, "skipped": "source",
                    "msg": "源码运行不注册协议(打包版开箱即得)"}
        try:
            import winreg as reg
        except ImportError:
            return {"ok": False, "skipped": "winreg", "msg": "没有 winreg"}
    cmd = f'"{exe}" {ACTIVATE_FLAG} "%1"'
    try:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, PROTOCOL_REG) as k:
            reg.SetValueEx(k, "", 0, reg.REG_SZ, "引路学长")
            reg.SetValueEx(k, "URL Protocol", 0, reg.REG_SZ, "")
        with reg.CreateKey(reg.HKEY_CURRENT_USER,
                           PROTOCOL_REG + r"\shell\open\command") as k:
            reg.SetValueEx(k, "", 0, reg.REG_SZ, cmd)
        with reg.OpenKey(reg.HKEY_CURRENT_USER,
                         PROTOCOL_REG + r"\shell\open\command") as k:
            got = reg.QueryValueEx(k, "")[0]
        if got != cmd:
            return {"ok": False, "msg": f"注册表读回不一致: {got!r}"}
        return {"ok": True, "cmd": cmd}
    except OSError as e:
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------ 单实例交接

def port_path():
    from minicode.paths import app_path
    return app_path(PORT_FILE)


def start_listener(on_uri, log=None) -> dict:
    """起激活监听(127.0.0.1 随机端口,端口落 activate-port.json)。

    端口文件写不进去(只读目录)= 第二个实例永远找不到这一个 —— 监听
    照样起(本实例的手动启动不受影响)但如实回 ok=False 与原因,绝不为
    装出"支持点击"的样子假装落了盘。返回 {"ok", "port", "server", "msg"}。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/activate":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length") or 0)
            uri = self.rfile.read(n).decode("utf-8", "replace").strip()
            parsed = parse_uri(uri)
            if not parsed:                     # 不是本应用的 scheme:拒
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"bad uri")
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            try:
                on_uri(parsed)
            except Exception as e:                    # 回调炸了不能带走线程
                if log:
                    log(f"激活回调出错: {type(e).__name__}: {e}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    out = {"ok": True, "port": port, "server": srv, "msg": ""}
    try:
        p = port_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"port": port}), encoding="utf-8")
    except OSError as e:
        out.update({"ok": False,
                    "msg": f"端口文件写不进去({type(e).__name__}),"
                           "别的实例交接不到这一个;窗口与通知不受影响"})
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name="activate-listener").start()
    if log and not out["ok"]:
        log(out["msg"])
    return out


def handoff(uri: str, timeout: float = 1.5) -> bool:
    """把激活 URI 交接给在跑的实例;送达返回 True。

    没有端口文件(没在跑/首次启动)或连不上(上一个实例已退出、文件
    过期)都如实回 False —— 调用方自己启动并带走落点。
    """
    try:
        port = json.loads(port_path().read_text(encoding="utf-8")).get("port")
    except (OSError, ValueError):
        return False
    if not port:
        return False
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}/activate",
            data=str(uri).encode("utf-8"), method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------ 待落地面板

def pending_uri_arg(argv=None) -> str | None:
    """命令行里的 `--activate <uri>`;没有返回 None(正常启动)。"""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(args):
        if a == ACTIVATE_FLAG and i + 1 < len(args):
            return args[i + 1]
    return None


_pending = {"panel": None}
_pending_lock = threading.Lock()


def set_pending_panel(panel: str | None) -> None:
    """`--activate` 自己启动时记下要落的面板(交接成功就不用记)。"""
    with _pending_lock:
        _pending["panel"] = panel or None


def take_pending_panel() -> str | None:
    """get_info 一次性取走(取完即清,首屏只落一次)。"""
    with _pending_lock:
        panel = _pending["panel"]
        _pending["panel"] = None
        return panel
