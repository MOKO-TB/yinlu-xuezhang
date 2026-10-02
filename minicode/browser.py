"""浏览器能力:用 CDP(Chrome DevTools Protocol)直连真实浏览器。

为什么不用 Playwright:
- 轻量版安装包明确排除了 playwright/patchright 与 270MB 浏览器内核
  (见 packaging/gcode.spec 的 excludes),而 CDP 只需要一个 WebSocket 客户端
  (`websockets` 是纯 Python 包,可以随包分发);
- 自带内核的完整版也不冲突:同一套 CDP 代码照样能驱动。

两种接入方式:
- attach:连**用户自己开着远程调试**的浏览器(带他的登录态,适合要登录的页面);
- launch:用本机 Edge/Chrome 起一个独立 profile 的实例(零配置,默认 headless)。

并发:每个线程(= 每个会话 / 每个子代理)一份 PageSession,互不串台;
多条会话共用一条 WebSocket 连接,连接上的命令用锁串行化。

安全:只允许 http/https;file:// 等本地/浏览器内部协议一律拒绝。
"""
from __future__ import annotations

import atexit
import base64
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minicode.paths import app_path

# ---------------------------------------------------------------- 常量

DEFAULT_PORTS = (9222, 9333)          # 常见远程调试端口
ENV_CDP_URL = "YINLUXS_CDP_URL"       # 指向已有调试端点,如 http://127.0.0.1:9222
ENV_BROWSER = "YINLUXS_BROWSER"       # 指定浏览器 exe 路径(不填则自动找 Edge→Chrome→内核)
ENV_PROFILE = "YINLUXS_BROWSER_PROFILE"   # 覆盖独立实例的 user-data-dir(测试用)

DEFAULT_WAIT_MS = 8000
MAX_WAIT_MS = 30000
DEFAULT_MAX_CHARS = 6000
MAX_CHARS = 20000
# 按帧读取的天花板:一张学期课表的 #kbtable 文本也就几万字,6000 的默认值
# 会把后半张切掉 —— 那时候"没课"其实是"没读到"。给足,但仍要有上限防失控。
FRAME_MAX_CHARS = 200_000
MAX_SESSIONS = 6                     # 同时活着的标签页上限(超出关最旧的)
LAUNCH_TIMEOUT = 25.0                # 冷启动耐心值(秒)
# attach 握手限时:实测用户开着几十个标签页的 Edge **首次** WS 握手可超过 3 s
# (把它当"冷启动"看),紧接着重试只要 ~1.1 s。所以:
#   真要用 attach 时给够耐心(10 s);/browser 这种交互自检给短一点(6 s)。
ATTACH_TIMEOUT = 10.0
HEALTH_PROBE_TIMEOUT = 6.0

_EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


class BrowserUnavailable(RuntimeError):
    """本机没有可用的浏览器/调试端点,能力不可用(附怎么修)。"""


class BrowserError(RuntimeError):
    """CDP 调用失败 / 网址不合法等,可展示给用户。"""


# ---------------------------------------------------------------- 网址校验

_BLOCKED_SCHEMES = {
    "file", "javascript", "data", "blob", "chrome", "edge", "about",
    "devtools", "view-source", "ftp", "chrome-extension", "edge-extension",
}


def check_url(raw: str) -> str:
    """把用户/模型给的地址规范化成 http(s),并挡掉本地文件与浏览器内部页。"""
    url = (raw or "").strip().strip("<>")
    if not url:
        raise BrowserError("网址不能为空。")
    if re.search(r"\s", url):
        raise BrowserError(f"网址里不能有空格(看起来不是一条完整链接):{raw}")
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", url)
    if m:
        scheme = m.group(1).lower()
        if scheme in _BLOCKED_SCHEMES:
            raise BrowserError(
                f"出于安全考虑不允许打开 {scheme}: 开头的地址。"
                "本地文件请用 read_file/grep,浏览器内部页请让用户自己打开。"
            )
        if scheme not in ("http", "https"):
            raise BrowserError(f"只支持 http/https 网址,收到:{raw}")
    else:
        url = "https://" + url
    host = urllib.parse.urlsplit(url).netloc
    # 主机名要像样:有端口/有点号(域名)、是 IP,或 localhost —— 否则多半是随手打的词
    if not host or not re.fullmatch(
            r"(localhost|\d+(\.\d+){3}|[\w.\-]*\.[\w\-]+)(:\d+)?", host, re.I):
        raise BrowserError(f"网址看起来不合法:{raw}")
    return url


# ---------------------------------------------------------------- HTTP 探测

def _http_json(url: str, timeout: float = 2.0) -> dict | None:
    """读一个本地 JSON 接口(绕过系统代理,只服务 127.0.0.1)。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "yinluxs-browser/1"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _devtools_files() -> list[Path]:
    """Edge/Chrome 用户目录里的 DevToolsActivePort 文件(开了远程调试才有)。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    roots = [
        Path(local) / "Microsoft" / "Edge" / "User Data",
        Path(local) / "Google" / "Chrome" / "User Data",
    ]
    return [root / "DevToolsActivePort" for root in roots]


def _port_from_file() -> list[int]:
    """从 DevToolsActivePort 文件里读端口(用户自己开了远程调试时)。"""
    ports: list[int] = []
    for f in _devtools_files():
        try:
            ports.append(int(f.read_text(encoding="utf-8", errors="replace")
                             .splitlines()[0].strip()))
        except Exception:
            continue
    return ports


def _ws_path_from_file(port: int) -> str | None:
    """从 DevToolsActivePort 第二行拿浏览器级 WebSocket 路径。

    为什么要这条兼容路:实测用户自己的 Edge 153 在 9222 上对
    `127.0.0.1:9222/json/version` **返回 404**(只认 localhost 作 Host),
    而文件里直接记着可用的 ws 路径 —— 只依赖 HTTP 探测就会在这类环境里
    把可用的登录态浏览器误判成「没找到」。
    """
    for f in _devtools_files():
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            if int(lines[0].strip()) != int(port):
                continue
            path = lines[1].strip()
            if path.startswith("/"):
                return f"ws://127.0.0.1:{port}{path}"
        except Exception:
            continue
    return None


def probe_endpoint(port: int, timeout: float = 1.5) -> dict | None:
    """探测某个端口上有没有开着的 CDP 端点,有则返回可用的连接信息。

    三种探测依次试:127.0.0.1 → localhost(部分 Edge 只认后者)
    → DevToolsActivePort 里的 ws 路径。
    """
    for host in ("127.0.0.1", "localhost"):
        data = _http_json(f"http://{host}:{int(port)}/json/version", timeout=timeout)
        if data and data.get("webSocketDebuggerUrl"):
            return data
    ws = _ws_path_from_file(port)
    if ws:
        return {"webSocketDebuggerUrl": ws, "Browser": "浏览器(由 DevToolsActivePort 探测)"}
    return None


def find_browser_exe() -> tuple[str, str] | None:
    """找一个可用的浏览器可执行文件:返回 (名称, 路径);找不到返回 None。"""
    env = (os.environ.get(ENV_BROWSER) or "").strip()
    if env:
        p = Path(env)
        if not p.exists():
            raise BrowserUnavailable(f"环境变量 {ENV_BROWSER} 指向的文件不存在:{env}")
        return p.name, str(p)
    for p in _EDGE_PATHS:
        if os.path.exists(p):
            return "Edge", p
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return "Chrome", p
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        # 开发态/完整版会带 playwright 内核,顺带当兜底浏览器用
        for pattern in ("chromium-*/chrome-win64/chrome.exe",
                        "chromium_headless_shell-*/chrome-win64/headless_shell.exe"):
            for hit in sorted(Path(local, "ms-playwright").glob(pattern), reverse=True):
                if hit.exists():
                    return "Chromium(playwright 内核)", str(hit)
    for name in ("msedge.exe", "chrome.exe"):
        hit = shutil.which(name)
        if hit:
            return name, hit
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ---------------------------------------------------------------- WebSocket / CDP

class _Conn:
    """一条 CDP WebSocket 连接:命令-响应配对,忽略事件,锁内串行。"""

    def __init__(self, ws_url: str | None = None, *, ws: Any | None = None,
                 timeout: float = 30.0):
        self._lock = threading.Lock()
        self._id = 0
        if ws is None:
            from websockets.sync.client import connect     # 延迟导入,便于测试替身
            stack = contextlib.ExitStack()
            try:
                ws = stack.enter_context(
                    connect(ws_url, max_size=None, open_timeout=timeout, close_timeout=2))
            except Exception as exc:          # 连不上:说明端点没了
                stack.close()
                raise BrowserUnavailable(f"连接浏览器调试端口失败:{exc}") from exc
            self._stack: contextlib.ExitStack | None = stack
        else:
            self._stack = None
        self.ws = ws

    def call(self, method: str, params: dict | None = None,
             session: str | None = None, timeout: float = 30.0) -> dict:
        with self._lock:
            self._id += 1
            mid = self._id
            msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            try:
                self.ws.send(json.dumps(msg))
            except Exception as exc:
                raise BrowserUnavailable(f"浏览器连接已断开:{exc}") from exc
            deadline = time.monotonic() + timeout
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise BrowserError(f"CDP 命令超时({timeout:.0f}s):{method}")
                try:
                    raw = self.ws.recv(timeout=remain)
                except TimeoutError as exc:
                    raise BrowserError(f"CDP 命令超时({timeout:.0f}s):{method}") from exc
                except Exception as exc:
                    raise BrowserUnavailable(f"浏览器连接已断开:{exc}") from exc
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if data.get("id") != mid:
                    continue                      # 事件或别的命令的响应
                if "error" in data:
                    err = data["error"] or {}
                    raise BrowserError(f"{method} 失败:{err.get('message') or err}")
                return data.get("result") or {}

    def close(self) -> None:
        if self._stack is not None:
            with contextlib.suppress(Exception):
                self._stack.close()
        else:
            with contextlib.suppress(Exception):
                self.ws.close()


# ---------------------------------------------------------------- 会话

@dataclass
class PageSession:
    """一个标签页(= 一个线程的「当前页面」)。"""

    conn: _Conn
    target_id: str
    cdp_session: str
    browser: str = ""
    mode: str = "attach"
    url: str = ""
    title: str = ""
    opened_at: float = field(default_factory=time.time)
    owner: Any = None        # weakref 到底创建它的线程:线程死了标签页就该回收
    adopted: bool = False    # True = 用户本来就开着的页,我们只是接管:**绝不关它**


@dataclass
class _State:
    conn: _Conn | None = None
    browser: str = ""
    mode: str = ""                    # attach / launch
    headless: bool = True             # 只说 launch 那一支:有没有窗口(教务登录必须有)
    proc: subprocess.Popen | None = None
    port: int | None = None
    sessions: dict[str, PageSession] = field(default_factory=dict)


_LOCK = threading.RLock()
_STATE = _State()
_KEY = "browser"                    # 每个线程/子代理一份会话的键前缀


def _session_key() -> str:
    return f"{_KEY}:{threading.get_ident()}"


def _cmd(method: str, params: dict | None = None, session: PageSession | None = None,
         timeout: float = 30.0) -> dict:
    """在当前线程的会话上执行一条 CDP 命令。"""
    conn = session.conn if session is not None else _require_conn()
    return conn.call(method, params, session=session.cdp_session if session else None,
                     timeout=timeout)


def _require_conn() -> _Conn:
    with _LOCK:
        if _STATE.conn is not None:
            return _STATE.conn
    raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址。")


# ---------------------------------------------------------------- 端点与启动

def _endpoint_port(raw: str) -> int:
    """从 YINLUXS_CDP_URL 的值里取端口:http://127.0.0.1:9222 / localhost:9222 / 9222。

    为什么不直接 re.search(r"(\\d{2,5})"):那个正则命中的是**第一段数字**,
    而对文档里推荐的写法 `http://127.0.0.1:9222` 来说那是 IP 的 "127" ——
    于是永远去探 127 端口,attach 这条路"怎么都连不上"(真机踩到)。
    """
    v = (raw or "").strip()
    if v.isdigit():
        return int(v)
    try:
        port = urllib.parse.urlsplit(v).port
    except ValueError:                       # 端口越界/非数字,交给下面的兜底
        port = None
    if port:
        return int(port)
    m = re.search(r":(\d{2,5})(?:[/?#\s]|$)", v)
    port = int(m.group(1)) if m else 0
    return port if 0 < port <= 65535 else 0


def _attach_endpoint() -> tuple[str, dict] | None:
    """找一个可以直接 attach 的调试端点(独立于我们自己的实例)。"""
    env = (os.environ.get(ENV_CDP_URL) or "").strip()
    if env:
        if env.startswith("ws"):
            raise BrowserUnavailable(
                f"{ENV_CDP_URL} 请写成 http://127.0.0.1:<port> 的形式"
                "(具体 WebSocket 地址由程序从 /json/version 取)")
        port = _endpoint_port(env)
        if not port:
            raise BrowserUnavailable(f"{ENV_CDP_URL} 看不出端口:{env}")
        info = probe_endpoint(port)
        if not info:
            raise BrowserUnavailable(
                f"{ENV_CDP_URL} 指向的 {env} 连不上:确认浏览器开着且带 --remote-debugging-port")
        return info["webSocketDebuggerUrl"], info
    ports: list[int] = list(DEFAULT_PORTS)
    for p in _port_from_file():
        if p not in ports:
            ports.append(p)
    for port in ports:
        info = probe_endpoint(port)
        if info:
            return info["webSocketDebuggerUrl"], info
    return None


def launch_browser(*, headless: bool = True, port: int | None = None,
                   profile_dir: str | Path | None = None) -> tuple[subprocess.Popen, int, dict]:
    """起一个独立 profile 的浏览器实例(零配置路线),返回 (进程, 端口, /json/version)。"""
    found = find_browser_exe()
    if found is None:
        raise BrowserUnavailable(
            "本机没找到 Edge/Chrome。装一个 Edge/Chrome,"
            f"或用环境变量 {ENV_BROWSER} 指定浏览器 exe 路径。")
    name, exe = found
    port = int(port or _free_port())
    profile = Path(profile_dir or os.environ.get(ENV_PROFILE)
                   or app_path("browser-profile"))
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--disable-extensions", "--disable-background-networking",
        # 限制磁盘缓存:不设的话这个常驻 profile 会慢慢涨(实测开发机 79 MB/1100 文件),
        # 而引路学长拿它只为了看页面,不需要缓存网页资源。
        "--disk-cache-size=1", "--disable-dev-shm-usage",
        "--window-size=1280,900",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    flags = 0
    if headless and os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise BrowserUnavailable(f"{name} 启动后立刻退出(退出码 {proc.returncode}),无法启用浏览器能力。")
        info = probe_endpoint(port, timeout=1.0)
        if info:
            return proc, port, info
        time.sleep(0.25)
    with contextlib.suppress(Exception):
        proc.terminate()
    raise BrowserUnavailable(f"{name} 启动超时({LAUNCH_TIMEOUT:.0f}s),没等到调试端口 {port}。")


def _try_attach(*, timeout: float = ATTACH_TIMEOUT) -> tuple[tuple['_Conn', str] | None, str]:
    """探一下能不能 attach,能就连上并真握手。

    返回 ((连接, 浏览器产品串) 或 None, 失败原因) —— 失败原因要带回去给用户看,
    但不能把探不到当成错误:attach 只是一条可选路线。
    为何要真跑一次握手:DevToolsActivePort 可能是浏览器关后的残留文件,也可能
    端口被别的程序占着;不验证就会把"已死端点"当成可 attach,然后让用户等完超时。
    """
    try:
        hit = _attach_endpoint()
    except BrowserUnavailable as exc:
        return None, str(exc)
    if hit is None:
        return None, "没找到开着远程调试的浏览器"
    ws_url, info = hit
    conn = None
    try:
        conn = _Conn(ws_url, timeout=timeout)
        product = conn.call("Browser.getVersion", timeout=timeout).get("product", "")
    except Exception:
        if conn is not None:
            conn.close()
        # 文案要诚实:超时不能肯定地说"浏览器已关"(实测也可能是首次握手慢),
        # 也不能只报一个技术词——用户得知道下一步干什么。
        return None, (f"调试端点 {ws_url} 没在 {timeout:.0f}s 内完成握手"
                      "(浏览器可能已关、正忙,或首次被唤起的很慢;重试一次往往就通)")
    return (conn, product or info.get("Browser", "浏览器")), ""


def _ensure_conn(*, mode: str = "auto", headless: bool = True) -> tuple[_Conn, str, str]:
    """拿到连接(优先复用),返回 (连接, 浏览器产品串, 接入方式)。

    auto 的默认是 **launch(独立无窗口实例)**,不是 attach:
    用户自己那个浏览器里有几十个标签页与真实登录态,拿它去自动化属于
    "静默代办"——对要分发给他人的程序太侵入,而且实测很容易卡住。
    只有两种情况 auto 会先走 attach:
      - 用户显设了 YINLUXS_CDP_URL(这就是他的意图);
      - 本机根本 launch 不起来(没浏览器可执行文件),拿 attach 当兵底。
    需要登录态时让模型显式传 mode="attach"(确认框会单独警告)。
    """
    with _LOCK:
        if _STATE.conn is not None:
            return _STATE.conn, _STATE.browser, _STATE.mode
        if mode not in ("auto", "attach", "launch"):
            raise BrowserError(f"mode 只能是 auto/attach/launch,收到:{mode}")

        if mode == "attach":
            got, why = _try_attach()
            if got is None:
                raise BrowserUnavailable(
                    f"连不上你开着远程调试的浏览器:{why}。要使用这条路,"
                    "请先让 Edge/Chrome 带 --remote-debugging-port=9222 启动"
                    f"(Edge 的 edge://inspect 里有开关),或用环境变量 {ENV_CDP_URL} 指定端口。"
                    "不需要登录态的页面可以直接用默认模式自己起一个实例。")
            conn, browser = got
            _STATE.conn, _STATE.browser, _STATE.mode = conn, browser, "attach"
            return conn, browser, "attach"

        if mode == "auto" and (os.environ.get(ENV_CDP_URL) or "").strip():
            got, _why = _try_attach()          # 用户显式设过端点:按他的意思走
            if got is not None:
                conn, browser = got
                _STATE.conn, _STATE.browser, _STATE.mode = conn, browser, "attach"
                return conn, browser, "attach"

        try:
            proc, port, info = launch_browser(headless=headless)
        except BrowserUnavailable as launch_exc:
            # 本机起不了浏览器(比如没装 Edge/Chrome):attach 是最后的兵底
            got, why = _try_attach()
            if got is None:
                raise BrowserUnavailable(
                    f"{launch_exc}(用已开远程调试的浏览器也不行:{why})") from launch_exc
            conn, browser = got
            _STATE.conn, _STATE.browser, _STATE.mode = conn, browser, "attach"
            return conn, browser, "attach"

        try:
            conn = _Conn(info["webSocketDebuggerUrl"])
        except Exception as exc:
            # 连不上就别把刚起的实例漏在这儿:收掉进程再报错
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)
            raise BrowserUnavailable(f"浏览器已启动但连不上它的调试端口:{exc}") from exc
        _STATE.conn = conn
        _STATE.browser = info.get("Browser", "浏览器")
        _STATE.mode = "launch"
        _STATE.headless = bool(headless)
        _STATE.proc, _STATE.port = proc, port
        return conn, _STATE.browser, _STATE.mode


def ensure_visible(*, timeout: float = LAUNCH_TIMEOUT) -> dict:
    """保证"接下来打开的页用户看得见"(教务登录这种必须人来输验证码的路子)。

    三种情况:
      - 已经 attach 在用户自己的浏览器上 → 他那本来就有窗口,直接返回;
      - 我们自己起的实例**本来就带窗口** → 复用;
      - 起的是无窗口实例(自动浏览默认) → 收掉重起一个有窗口的;
        但**有别的线程正在用标签页时不动它**,否则等于把同伴的现场掀了。
    """
    with _LOCK:
        if _STATE.conn is not None:
            if _STATE.mode == "attach" or not _STATE.headless:
                return {"ok": True, "mode": _STATE.mode, "browser": _STATE.browser}
            live = [k for k, s in _STATE.sessions.items()
                    if s.owner is None or (s.owner() is not None and s.owner().is_alive())]
            if live:
                raise BrowserUnavailable(
                    "现在有一个**无窗口**的浏览器任务还在跑(自动浏览中),"
                    "不能半路给它换窗口。等那条任务结束后再点导入。")
            shutdown()
        proc, port, info = launch_browser(headless=False)
        try:
            conn = _Conn(info["webSocketDebuggerUrl"])
        except Exception as exc:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)
            raise BrowserUnavailable(f"浏览器已启动但连不上它的调试端口:{exc}") from exc
        _STATE.conn, _STATE.browser, _STATE.mode = conn, info.get("Browser", "浏览器"), "launch"
        _STATE.proc, _STATE.port, _STATE.headless = proc, port, False
        return {"ok": True, "mode": "launch", "browser": _STATE.browser}


def _ensure_session(*, mode: str = "auto", headless: bool = True) -> PageSession:
    """当前线程的页面会话;没有就新建一个标签页。"""
    key = _session_key()
    with _LOCK:
        sess = _STATE.sessions.get(key)
        if sess is not None:
            return sess
        conn, browser, how = _ensure_conn(mode=mode, headless=headless)
        res = conn.call("Target.createTarget", {"url": "about:blank"})
        target_id = res.get("targetId")
        if not target_id:
            raise BrowserError("新建标签页失败(Target.createTarget 没返回 targetId)")
        attached = conn.call("Target.attachToTarget",
                            {"targetId": target_id, "flatten": True})
        cdp = attached.get("sessionId")
        if not cdp:
            raise BrowserError("附着到标签页失败(Target.attachToTarget 没返回 sessionId)")
        for method in ("Page.enable", "Runtime.enable"):
            conn.call(method, session=cdp)
        sess = PageSession(conn=conn, target_id=target_id, cdp_session=cdp,
                           browser=browser, mode=how,
                           owner=weakref.ref(threading.current_thread()))
        _STATE.sessions[key] = sess
        _sweep_dead_sessions_locked()
        _prune_locked(keep=key)
        return sess


def _sweep_dead_sessions_locked() -> None:
    """回收已结束线程(后台会话跑完、子代理交差)留下的标签页。

    不回收会越积越多:每个线程一份会话,线程退出后 ident 还可能被复用,
    新线程拿不到旧会话但旧标签页还留在浏览器里。
    """
    for key, sess in list(_STATE.sessions.items()):
        ref = sess.owner
        if ref is None:
            continue                     # 没记录归属(测试直接塑的会话)不动它
        owner = ref() if callable(ref) else None
        if owner is None or not owner.is_alive():
            _close_session_locked(key, sess)


def _prune_locked(*, keep: str) -> None:
    """标签页太多时关掉最旧的,别让浏览器堆一屏。

    用户自己开着的页(adopted)不参与:那是他的登录窗口,不是我们堆出来的。
    """
    if len([s for s in _STATE.sessions.values() if not s.adopted]) <= MAX_SESSIONS:
        return
    ordered = sorted(_STATE.sessions.items(), key=lambda kv: kv[1].opened_at)
    for k, s in ordered:
        if s.adopted or k == keep:
            continue
        if len([x for x in _STATE.sessions.values() if not x.adopted]) <= MAX_SESSIONS:
            break
        _close_session_locked(k, s)


def _close_session_locked(key: str, sess: PageSession) -> None:
    """丢掉一份会话。**接管的页只脱手、不关闭** —— 那是用户自己的标签页。"""
    _STATE.sessions.pop(key, None)
    if sess.adopted:
        return
    with contextlib.suppress(Exception):
        sess.conn.call("Target.closeTarget", {"targetId": sess.target_id}, timeout=5)


def _current() -> PageSession | None:
    with _LOCK:
        return _STATE.sessions.get(_session_key())


# ---------------------------------------------------------------- 页面操作

_JS_READY = "document.readyState"
_JS_TITLE = "document.title"
_JS_TEXT = ("(() => { const b = document.body; if (!b) return '';"
            " const t = b.innerText || b.textContent || '';"
            " return t.replace(/\\u00a0/g, ' '); })()")


def _clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit] + "\n…(已截断)", True


def evaluate(expression: str, *, await_promise: bool = False,
             timeout: float = 20.0) -> Any:
    """在「当前页面」里执行一段 JS 并把结果按值返回。"""
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址。")
    res = _cmd("Runtime.evaluate",
               {"expression": expression, "returnByValue": True,
                "awaitPromise": bool(await_promise), "userGesture": False},
               session=sess, timeout=timeout)
    if res.get("exceptionDetails"):
        det = res["exceptionDetails"]
        msg = (det.get("exception") or {}).get("description") or det.get("text") or "JS 执行出错"
        raise BrowserError(f"页面里执行 JS 出错:{msg.splitlines()[0][:300]}")
    out = res.get("result") or {}
    if "value" in out:
        return out["value"]
    return {"type": out.get("type"), "description": (out.get("description") or "")[:400]}


def _wait_loaded(sess: PageSession, *, wait_ms: int, wait_for: str = "") -> tuple[bool, float, str]:
    """等页面加载完成(轮询 readyState);可选等某个 CSS 选择器出现。"""
    deadline = time.monotonic() + max(0, wait_ms) / 1000
    started = time.monotonic()
    selector = (wait_for or "").strip()
    js_found = f"!!document.querySelector({json.dumps(selector)})" if selector else ""
    last_state = ""
    while True:
        try:
            state = evaluate(_JS_READY, timeout=10)
            last_state = str(state)
            if last_state == "complete":
                if not js_found:
                    return True, (time.monotonic() - started) * 1000, last_state
                try:
                    if evaluate(js_found, timeout=10):
                        return True, (time.monotonic() - started) * 1000, last_state
                except BrowserError:
                    pass
        except (BrowserError, BrowserUnavailable):
            pass                       # 导航中途上下文会被销毁,下一轮再试
        if time.monotonic() >= deadline:
            return False, (time.monotonic() - started) * 1000, last_state
        time.sleep(0.15)


def _origin_allowed(url: str, allowed: "list[str] | None") -> bool:
    """S2(v0.9.105):attach 会话的域名授权判定。

    allowed 为空 = 不限制(老行为);非空时 URL 的 host(去 www. 前缀)必须
    与名单中某项相等或是其子域 —— `example.com` 放行 `www.example.com` 与
    `api.example.com`,反向不放行。判定只认 host,不认 path(整站授权)。
    """
    if not allowed:
        return True
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    for entry in allowed:
        e = str(entry).strip().lower().removeprefix("www.").removeprefix("https://").removeprefix("http://").rstrip("/")
        if not e:
            continue
        if host == e or host.endswith("." + e):
            return True
    return False


def open_page(url: str, *, wait_ms: int = DEFAULT_WAIT_MS, wait_for: str = "",
              max_chars: int = DEFAULT_MAX_CHARS, mode: str = "auto",
              headless: bool = True,
              allowed_attach_origins: "list[str] | None" = None) -> dict:
    """打开一个网址并返回标题 + 正文文本(JS 渲染后的 DOM)。"""
    target = check_url(url)
    wait_ms = max(0, min(int(wait_ms), MAX_WAIT_MS))
    max_chars = max(200, min(int(max_chars), MAX_CHARS))
    sess = _ensure_session(mode=mode, headless=headless)
    # S2(v0.9.105):attach 带的是用户浏览器的全部登录态 —— 配了授权名单时,
    # 非名单域名在导航前就拒绝(名单空 = 不限制,老行为)。launch 是我们自己的
    # 独立实例,不受名单约束。这是**开页入口的一层闸**,不是沙箱:页内跳转
    # 另开页面仍会再过这道,页内 JS 的跳转管不住 —— 边界如实说。
    if sess.mode == "attach" and not _origin_allowed(target, allowed_attach_origins):
        close_current()
        raise BrowserError(
            f"域名 {target} 不在 attach 授权名单里(allowed_attach_origins)。"
            "attach 用的是你浏览器的全部登录态,名单外不放行;"
            "把域名加进 config.json 的 allowed_attach_origins,或用默认的独立实例。")
    nav = _cmd("Page.navigate", {"url": target}, session=sess, timeout=30)
    if nav.get("errorText"):
        raise BrowserError(f"浏览器拒绝导航到 {target}:{nav['errorText']}")
    ok, waited, state = _wait_loaded(sess, wait_ms=wait_ms, wait_for=wait_for)
    title = ""
    with contextlib.suppress(Exception):
        title = str(evaluate(_JS_TITLE, timeout=10) or "")
    text = ""
    with contextlib.suppress(Exception):
        text = _clean_text(str(evaluate(_JS_TEXT, timeout=15) or ""))
    body, truncated = _truncate(text, max_chars)
    real_url = target
    with contextlib.suppress(Exception):
        real_url = str(evaluate("location.href", timeout=10) or target)
    sess.url, sess.title = real_url, title
    return {
        "url": real_url, "title": title, "text": body, "truncated": truncated,
        "chars": len(text), "loaded": ok, "state": state, "waited_ms": int(waited),
        "mode": sess.mode, "browser": sess.browser, "wait_for": (wait_for or "").strip(),
    }


# -------------------------------------------------- 接管现有页 / 按帧读取

def adopt_page(url_like: str = "", *, mode: str = "attach",
               timeout: float = 20.0) -> dict | None:
    """认领浏览器里**已经开着**的那一页,而不是另起一张空白页。

    为什么要这条:`_ensure_session` 只会 `Target.createTarget(about:blank)`,
    新标签页看不到用户那张已登录的页面 —— 「读他刚登录好的教务页」就实现不了。
    接管而来的会话标成 adopted:回收/收工都只脱手、**绝不替用户关他的标签页**。
    找不到匹配页返回 None(由调用方如实说"没找到",而不是猜)。
    """
    key = _session_key()
    with _LOCK:
        if _STATE.conn is not None:
            conn, browser_name, how = _STATE.conn, _STATE.browser, _STATE.mode
        elif mode == "launch":
            return None            # 没有连接 = 没有"已经开着的页",不去为找一个页而开浏览器
        else:
            conn, browser_name, how = _ensure_conn(mode="attach")
        targets = conn.call("Target.getTargets", timeout=timeout).get("targetInfos") or []
        pages = [t for t in targets
                 if t.get("type") == "page" and url_like in str(t.get("url", ""))]
        if not pages:
            return None
        t = pages[0]
        cdp = conn.call("Target.attachToTarget", {"targetId": t["targetId"],
                                                 "flatten": True},
                        timeout=timeout).get("sessionId")
        if not cdp:
            raise BrowserError("接管标签页失败(Target.attachToTarget 没返回 sessionId)")
        for method in ("Page.enable", "Runtime.enable"):
            conn.call(method, session=cdp)
        old = _STATE.sessions.get(key)
        if old is not None and old.target_id != t["targetId"]:
            _close_session_locked(key, old)      # 我们自己那张旧页可以让它走
        sess = PageSession(conn=conn, target_id=t["targetId"], cdp_session=cdp,
                           browser=browser_name, mode=how,
                           url=str(t.get("url", "")), title=str(t.get("title", "")),
                           owner=weakref.ref(threading.current_thread()), adopted=True)
        _STATE.sessions[key] = sess
        return {"url": sess.url, "title": sess.title, "mode": how,
                "browser": browser_name}


def _walk_frame_tree(node: dict, out: list[dict], depth: int = 0) -> None:
    """摊平 Page.getFrameTree(帧里还能套帧)。"""
    f = node.get("frame") or {}
    if f.get("id"):
        out.append({"frameId": f["id"], "url": f.get("url") or "",
                    "name": f.get("name") or "", "depth": depth})
    for child in node.get("childFrames") or []:
        _walk_frame_tree(child, out, depth + 1)


def page_frames(*, timeout: float = 20.0) -> list[dict]:
    """当前页里的所有 frame,含**进程内 iframe**。

    为什么不能只看 Target.getTargets:同源的进程内 iframe 根本不作为 target 出现,
    而强智教务的课表就塞在这样一个 iframe 里(`xskb/xskb_list.do`)——
    按 target 找会得出"这页是空的"这种假结论(真机踩过)。
    """
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:先 browser_open 或用 adopt_page 接管一页。")
    tree = _cmd("Page.getFrameTree", session=sess, timeout=timeout).get("frameTree") or {}
    out: list[dict] = []
    _walk_frame_tree(tree, out)
    return out


def eval_in_frame(frame_id: str, expression: str, *, timeout: float = 25.0) -> Any:
    """在指定 frame(顶层帧同样可以)里跑一段 JS,按值取回结果,**不截断**。

    绕一圈 createIsolatedWorld 是因为 Runtime.evaluate 默认打在**顶层帧**的上下文上;
    它的返回字段名是 executionContextId(取错键会每帧静默拿到 None)。
    """
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:先 browser_open 或用 adopt_page 接管一页。")
    world = _cmd("Page.createIsolatedWorld",
                 {"frameId": frame_id, "worldName": "yinluxs"},
                 session=sess, timeout=timeout)
    ctx = world.get("executionContextId") or world.get("contextId")
    if not ctx:
        raise BrowserError("拿不到该 frame 的执行上下文(页面可能刚被换掉)")
    res = _cmd("Runtime.evaluate",
               {"expression": expression, "returnByValue": True, "contextId": ctx},
               session=sess, timeout=timeout)
    if res.get("exceptionDetails"):
        det = res["exceptionDetails"]
        msg = (det.get("exception") or {}).get("description") or det.get("text") or "JS 出错"
        raise BrowserError(f"页面里执行 JS 出错:{msg.splitlines()[0][:300]}")
    return (res.get("result") or {}).get("value")


def read_frames(expression: str, *, url_like: str = "",
                max_chars: int = FRAME_MAX_CHARS, timeout: float = 25.0) -> list[dict]:
    """在所有帧(或 URL 含 url_like 的帧)里跑同一段 JS,返回 [{url, value, truncated}]。"""
    out: list[dict] = []
    for fr in page_frames(timeout=timeout):
        if url_like and url_like not in fr["url"]:
            continue
        try:
            val = eval_in_frame(fr["frameId"], expression, timeout=timeout)
        except (BrowserError, BrowserUnavailable) as exc:
            out.append({"url": fr["url"], "frameId": fr["frameId"],
                        "error": str(exc)[:200], "value": None})
            continue
        text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        cut = bool(max_chars and len(text) > max_chars)
        out.append({"url": fr["url"], "frameId": fr["frameId"],
                    "value": text[:max_chars] if cut else val, "truncated": cut,
                    "chars": len(text)})
    return out


def screenshot(path: str | Path, *, full_page: bool = True) -> dict:
    """把「当前页面」截成 PNG 存到 path。"""
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址,再截图。")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    params: dict[str, Any] = {"format": "png", "fromSurface": True,
                              "captureBeyondViewport": bool(full_page)}
    try:
        res = _cmd("Page.captureScreenshot", params, session=sess, timeout=60)
    except BrowserError:
        if not full_page:
            raise
        res = _cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True},
                   session=sess, timeout=60)
    data = res.get("data")
    if not data:
        raise BrowserError("截图失败:浏览器没有返回图像数据。")
    raw = base64.b64decode(data)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise BrowserError("截图失败:返回的不是 PNG 数据。")
    p.write_bytes(raw)
    size = (0, 0)
    with contextlib.suppress(Exception):
        m = _cmd("Page.getLayoutMetrics", session=sess, timeout=10)
        css = m.get("cssLayoutViewport") or m.get("layoutViewport") or {}
        size = (int(css.get("clientWidth") or 0), int(css.get("clientHeight") or 0))
    return {"path": str(p), "bytes": len(raw), "viewport": size,
            "url": sess.url, "title": sess.title}


# ---------------------------------------------------------------- 页面交互

_JS_INTERACTABLE = r"""
(() => {
  document.querySelectorAll('[data-ylx]').forEach(el => el.removeAttribute('data-ylx'));
  const SEL = ['a[href]', 'button', 'input', 'select', 'textarea',
               '[role="button"]', '[role="link"]', '[role="tab"]',
               '[role="checkbox"]', '[role="menuitem"]', 'summary'].join(',');
  const nodes = [...document.querySelectorAll(SEL)];
  const out = [];
  let n = 0;
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && ['hidden', 'image', 'reset', 'file'].includes(type)) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    if (el.disabled) continue;
    const aria = (el.getAttribute('aria-label') || '').trim();
    const label = ((el.labels && el.labels[0] && el.labels[0].innerText) || '').trim();
    const placeholder = (el.getAttribute('placeholder') || '').trim();
    const text = ((el.innerText || el.value || '') + '').trim().replace(/\s+/g, ' ');
    let kind = 'button';
    if (tag === 'a') kind = 'link';
    else if (tag === 'select') kind = 'select';
    else if (tag === 'textarea') kind = 'textarea';
    else if (tag === 'input') {
      kind = (type === 'checkbox' || type === 'radio') ? 'check' : 'input';
    } else if (el.getAttribute('role') === 'tab') kind = 'tab';
    const item = { n: ++n, kind };
    if (text) item.text = text.slice(0, 60);
    if (aria) item.aria = aria.slice(0, 80);
    if (label) item.label = label.slice(0, 60);
    if (placeholder) item.ph = placeholder.slice(0, 60);
    if (el.name) item.name = el.name;
    if (el.id) item.id = el.id;
    if (type) item.type = type;
    if (kind === 'check') item.checked = !!el.checked;
    if (kind === 'select') {
      const cur = [...(el.selectedOptions || [])].map(o => o.textContent.trim());
      const all = [...el.options].map(o => o.textContent.trim()).filter(Boolean);
      item.value = cur.join(', ');
      if (all.length) item.options = all.slice(0, 20).join(' | ').slice(0, 200);
    }
    if (!item.text && !item.aria && !item.label && !item.ph
        && !item.name && !item.id && !item.value) continue;
    el.setAttribute('data-ylx', String(item.n));
    out.push(item);
    if (out.length >= 150) break;
  }
  return JSON.stringify(out);
})()
""".strip()


def snapshot(max_items: int = 120) -> dict:
    """列出「当前页面」上可见、可交互的元素(带序号,给 click/fill 定位用)。"""
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址,再交互。")
    limit = max(10, min(int(max_items or 120), 150))
    js = _JS_INTERACTABLE.replace("out.length >= 150", f"out.length >= {limit}")
    raw = evaluate(js, timeout=15)
    try:
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        items = []
    return {"url": sess.url, "title": sess.title, "count": len(items), "items": items}


# 点击:三种定位。序号 = 快照里打上的 data-ylx 标记(页面一变标记自动失效,绝不点错);
# 文字 = 精确匹配 innerText/value/aria-label,多于一个就拒;选择器 = 原样交给 querySelector。
_JS_CLICK_BY_INDEX = r"""
(n => {
  const el = document.querySelector('[data-ylx="' + n + '"]');
  if (!el) return JSON.stringify({ ok: false, error: '序号 ' + n + ' 已失效(页面变过或还没做过 snapshot),请先 browser_snapshot' });
  el.scrollIntoView({ block: 'center' });
  el.click();
  return JSON.stringify({ ok: true, tag: el.tagName.toLowerCase(),
    desc: String(el.innerText || el.value || el.getAttribute('aria-label') || el.id || '').trim().replace(/\s+/g, ' ').slice(0, 60) });
})"""

_JS_CLICK_BY_TEXT = r"""
(t => {
  const want = String(t).trim();
  const low = want.toLowerCase();
  const texts = el => [el.innerText, el.value, el.getAttribute('aria-label') || '']
      .map(x => String(x || '').trim().replace(/\s+/g, ' '));
  const cand = [...document.querySelectorAll(
      'a[href], button, input[type="submit"], input[type="button"], [role="button"], [role="link"], [role="tab"], [role="menuitem"], summary')]
      .filter(el => {
        if (el.disabled) return false;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return false;
        return texts(el).some(s => s === want || s.toLowerCase() === low);
      });
  if (cand.length === 0) return JSON.stringify({ ok: false, error: '页面上没有文字正好是「' + want + '」的可点元素,先 browser_snapshot 看看' });
  if (cand.length > 1) return JSON.stringify({ ok: false, error: '有 ' + cand.length + ' 个元素都叫「' + want + '」,先 browser_snapshot 拿序号再点' });
  const el = cand[0];
  el.scrollIntoView({ block: 'center' });
  el.click();
  return JSON.stringify({ ok: true, tag: el.tagName.toLowerCase(),
    desc: texts(el)[0].slice(0, 60) });
})"""

_JS_CLICK_BY_SELECTOR = r"""
(s => {
  let el = null;
  try { el = document.querySelector(String(s)); } catch (e) { return JSON.stringify({ ok: false, error: '选择器不合法:' + e.message }); }
  if (!el) return JSON.stringify({ ok: false, error: '选择器没匹配到元素:' + s });
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return JSON.stringify({ ok: false, error: '目标元素不可见(display:none/visibility:hidden),先 snapshot 看看页面上有什么' });
  el.scrollIntoView({ block: 'center' });
  el.click();
  return JSON.stringify({ ok: true, tag: el.tagName.toLowerCase(),
    desc: String(el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60) });
})"""

# 填单个字段:按 name/id/placeholder/aria/label 找字段;select 下拉按选项文字/值匹配;
# checkbox/radio 按真假值切换;文本类用「原生 setter + input/change 事件」——
# 这样 React/Vue 的受控组件也能收到变化(直接 el.value= 它们感知不到)。
_JS_FILL_FIELD = r"""
(rec => {
  const want = String(rec.field).trim().toLowerCase();
  const squeeze = x => String(x || '').trim().toLowerCase().replace(/\s+/g, '');
  const wantKeys = new Set([want, squeeze(want)]);
  const fields = [...document.querySelectorAll('input, select, textarea')].filter(el => {
    if (el.disabled || el.readOnly) return false;
    if (['hidden', 'file', 'submit'].includes(el.type)) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    return true;
  });
  const names = el => {
    const bag = new Set();
    const add = x => { const s = squeeze(x); if (s) { bag.add(s); } };
    add(el.name); add(el.id);
    add(el.getAttribute('placeholder'));
    add(el.getAttribute('aria-label'));
    if (el.labels && el.labels[0]) add(el.labels[0].innerText);
    return bag;
  };
  let el = null;
  for (const c of fields) {
    const ns = names(c);
    if ([...ns].some(x => wantKeys.has(x))) { el = c; break; }
  }
  if (!el) {
    const loose = fields.filter(c => [...names(c)].some(x => x && (x.includes(want) || want.includes(x))));
    if (loose.length === 1) el = loose[0];
  }
  if (!el) return JSON.stringify({ ok: false, error: '页面上找不到字段「' + rec.field + '」(试过 name/id/placeholder/aria-label/label),先 browser_snapshot 看字段名' });
  if (el.tagName === 'SELECT') {
    const v = String(rec.value == null ? '' : rec.value).trim().toLowerCase();
    const opts = [...el.options];
    let opt = opts.find(o => o.value.toLowerCase() === v || o.textContent.trim().toLowerCase() === v);
    if (!opt) opt = v ? opts.find(o => o.textContent.trim().toLowerCase().includes(v)) : null;
    if (!opt) return JSON.stringify({ ok: false, error: '下拉框「' + rec.field + '」里没有「' + rec.value + '」,现有选项: ' + opts.map(o => o.textContent.trim()).filter(Boolean).join(' | ') });
    el.value = opt.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return JSON.stringify({ ok: true, field: rec.field, tag: 'select', set: opt.textContent.trim() });
  }
  if (el.type === 'checkbox' || el.type === 'radio') {
    if (el.type === 'radio' && rec.value != null && rec.value !== true && rec.value !== false) {
      const v = String(rec.value).trim().toLowerCase();
      const opt = [...document.querySelectorAll('input[type="radio"][name="' + (el.name || '') + '"]')]
          .find(r => r.value.toLowerCase() === v);
      if (opt) { opt.click(); return JSON.stringify({ ok: true, field: rec.field, tag: 'radio', set: opt.value }); }
    }
    const truthy = !['0', 'false', 'no', 'off', '', '否'].includes(String(rec.value).trim().toLowerCase());
    if (el.checked !== truthy) el.click();
    return JSON.stringify({ ok: true, field: rec.field, tag: el.type, set: truthy });
  }
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
  desc.set.call(el, String(rec.value == null ? '' : rec.value));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return JSON.stringify({ ok: true, field: rec.field, tag: el.tagName.toLowerCase(), set: String(rec.value == null ? '' : rec.value).slice(0, 30) });
})"""

_JS_SUBMIT = r"""
(() => {
  const btn = [...document.querySelectorAll(
      'button[type="submit"], input[type="submit"], button:not([type]), [role="button"]')]
      .find(el => {
        if (el.disabled) return false;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width >= 2 && r.height >= 2;
      });
  if (!btn) return JSON.stringify({ ok: false, error: '页面上没有可用的提交按钮;可以让模型自己 browser_click 指定,或请用户手动提交' });
  btn.click();
  return JSON.stringify({ ok: true, desc: String(btn.innerText || btn.value || '').trim().replace(/\s+/g, ' ').slice(0, 40) });
})"""


def _run_js_json(sess: PageSession, expression: str, *, timeout: float = 20.0) -> dict:
    """跑一段「返回 JSON 字符串」的页面 JS;解析失败/结构不对都当作页面侧失败。"""
    raw = _cmd("Runtime.evaluate",
               {"expression": expression, "returnByValue": True,
                "awaitPromise": False, "userGesture": True},
               session=sess, timeout=timeout)
    if res_err := raw.get("exceptionDetails"):
        msg = (res_err.get("exception") or {}).get("description") or res_err.get("text") or "JS 执行出错"
        raise BrowserError(f"页面里执行 JS 出错:{msg.splitlines()[0][:300]}")
    out = raw.get("result") or {}
    try:
        data = json.loads(out.get("value") or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def _after_action_state(sess: PageSession, wait_ms: int) -> dict:
    """点击/提交后的短暂等待 + 新页面状态(导航常在几百毫秒后才开始)。"""
    time.sleep(max(0, wait_ms) / 1000)
    ok, waited, state = _wait_loaded(sess, wait_ms=DEFAULT_WAIT_MS)
    title = ""
    with contextlib.suppress(Exception):
        title = str(evaluate(_JS_TITLE, timeout=10) or "")
    url = sess.url
    with contextlib.suppress(Exception):
        url = str(evaluate("location.href", timeout=10) or sess.url)
    sess.url, sess.title = url, title
    return {"url": url, "title": title, "loaded": ok, "waited_ms": int(waited)}


def click(target: str, *, by: str = "auto", wait_ms: int = 1500) -> dict:
    """点击「当前页面」上的一个元素,返回结果与点击后的页面状态。"""
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址,再交互。")
    target = (target or "").strip()
    if not target:
        raise BrowserError("点击目标不能为空:传快照序号、元素文字或 CSS 选择器。")
    wait_ms = max(0, min(int(wait_ms), 10000))
    how = by
    if how == "auto":
        how = "index" if target.isdigit() else "text"
    builders = {"index": lambda t: _JS_CLICK_BY_INDEX + f"({int(t)})",
                "text": lambda t: _JS_CLICK_BY_TEXT + "(" + json.dumps(t, ensure_ascii=False) + ")",
                "selector": lambda t: _JS_CLICK_BY_SELECTOR + "(" + json.dumps(t, ensure_ascii=False) + ")"}
    if how not in builders:
        raise BrowserError(f"by 只能是 auto/index/text/selector,收到:{by}")
    res = _run_js_json(sess, builders[how](target))
    if not res.get("ok"):
        raise BrowserError(str(res.get("error") or "点击失败(页面没有报出原因)"))
    after = _after_action_state(sess, wait_ms)
    return {"clicked": res.get("desc") or target, "tag": res.get("tag", ""),
            "by": how, "after": after}


def fill(fields: dict, *, submit: bool = False, wait_ms: int = 1500) -> dict:
    """在「当前页面」上按字段名批量填表单;可选点提交按钮。"""
    sess = _current()
    if sess is None:
        raise BrowserError("还没有打开的页面:请先用 browser_open 打开一个网址,再交互。")
    if not fields:
        raise BrowserError("fields 不能为空:至少给一对「字段名: 值」。")
    done: list[dict] = []
    failed: list[dict] = []
    for name, value in fields.items():
        arg = json.dumps({"field": str(name), "value": value}, ensure_ascii=False)
        try:
            res = _run_js_json(sess, _JS_FILL_FIELD + f"({arg})")
        except BrowserError as exc:
            failed.append({"field": str(name), "error": str(exc)})
            continue
        if res.get("ok"):
            done.append(res)
        else:
            failed.append({"field": str(name), "error": str(res.get("error") or "未知错误")})
    submitted = ""
    after: dict = {}
    if submit and not failed:
        res = _run_js_json(sess, _JS_SUBMIT + "()")
        if res.get("ok"):
            submitted = str(res.get("desc") or "已提交")
            after = _after_action_state(sess, wait_ms)
        else:
            failed.append({"field": "(submit)", "error": str(res.get("error") or "提交失败")})
    return {"filled": done, "failed": failed, "submitted": submitted, "after": after}


def current_info() -> dict | None:
    """当前线程页面的概况(没打开过则 None)。"""
    sess = _current()
    if sess is None:
        return None
    return {"url": sess.url, "title": sess.title, "mode": sess.mode,
            "browser": sess.browser, "target_id": sess.target_id}


def close_current() -> bool:
    """关掉当前线程的标签页;返回是否真有关掉。"""
    key = _session_key()
    with _LOCK:
        _sweep_dead_sessions_locked()
        sess = _STATE.sessions.get(key)
        if sess is None:
            return False
        _close_session_locked(key, sess)
        return True


def list_tabs() -> list[dict]:
    """所有还活着的标签页(v0.9.98,§6 #8①:标签页第一次「可见可管」)。

    每行都由 `_STATE.sessions` 里的真实会话撑起来 —— 死线程的先 sweep 掉,
    不给"看着还开着其实早回收了"的行。`key` 就是管理用的编号
    (browser_close target=编号);`current` 标出**打这条命令的线程**自己的页。
    """
    with _LOCK:
        _sweep_dead_sessions_locked()
        cur = _session_key()
        return [{"key": key,
                 "current": key == cur,
                 "url": sess.url, "title": sess.title,
                 "mode": sess.mode, "browser": sess.browser,
                 "adopted": sess.adopted,
                 "age_s": int(time.time() - sess.opened_at)}
                for key, sess in _STATE.sessions.items()]


def close_tab(key: str) -> "dict | None":
    """按键关掉一份标签页(管理动作;当前线程自己的仍走 close_current)。

    返回 {"ok": True, "adopted": …};没有这个键(已回收/写错)返回 None ——
    调用方要照实说"没有这一个",不静默装成关掉了。接管的页(adopted)只脱手
    不关闭,与收工(shutdown)同一条口径:**绝不替用户关他自己的标签页**。
    被关的那一路如果还在用浏览器,它的下一步会照常报错并自己重开页面。
    """
    with _LOCK:
        _sweep_dead_sessions_locked()
        sess = _STATE.sessions.get(str(key or ""))
        if sess is None:
            return None
        adopted = sess.adopted
        _close_session_locked(str(key), sess)
        return {"ok": True, "adopted": adopted}


def shutdown() -> None:
    """收工:关掉我们自己起的浏览器实例(attach 到用户浏览器的只断连接)。"""
    with _LOCK:
        for key, sess in list(_STATE.sessions.items()):
            if sess.adopted:
                continue        # 用户自己的标签页:收工时只脱手,不替他关
            with contextlib.suppress(Exception):
                sess.conn.call("Target.closeTarget", {"targetId": sess.target_id}, timeout=3)
        _STATE.sessions.clear()
        conn, _STATE.conn = _STATE.conn, None
        proc, _STATE.proc = _STATE.proc, None
        _STATE.mode = ""
        _STATE.browser = ""
        _STATE.headless = True
        if conn is not None:
            conn.close()
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)


atexit.register(shutdown)


# ---------------------------------------------------------------- 自检

def health() -> dict:
    """能不能用浏览器能力:给 /browser 命令与排障用(不主动弹提示)。

    口径与 _ensure_conn 一致:**launch 是主路线**,attach 只是可选的登录态路线。
    attach 探针限时很短(3 s):/browser 是交互命令,不能卡在这儿。
    """
    info: dict[str, Any] = {"ok": False, "mode": "", "browser": "", "detail": ""}
    # 只把"读全局状态"放进锁内;下面的握手探针是网络 I/O,不能持锁(最长几秒,
    # 会把其它线程的浏览器操作全堵住)。
    with _LOCK:
        _sweep_dead_sessions_locked()
        if _STATE.conn is not None:
            info.update(ok=True, mode=_STATE.mode, browser=_STATE.browser,
                        detail=f"已连接({len(_STATE.sessions)} 个标签页)")
            return info
    # 主路线:能不能自己起实例
    try:
        found = find_browser_exe()
    except BrowserUnavailable as exc:
        found = None
        info["detail"] = str(exc)
    # 可选路线:有没有开着远程调试的浏览器(短超时,连不上不报错)
    attach_note = ""
    try:
        hit = _attach_endpoint()
    except Exception:
        hit = None
    if hit is not None:
        probe = None
        try:
            probe = _Conn(hit[0], timeout=HEALTH_PROBE_TIMEOUT)
            product = probe.call("Browser.getVersion",
                                 timeout=HEALTH_PROBE_TIMEOUT).get("product", "")
            attach_note = f"{product or hit[1].get('Browser', '浏览器')} 开着远程调试"
        except Exception:
            attach_note = (f"探到调试端点但 {HEALTH_PROBE_TIMEOUT:.0f}s 内握手不通"
                           "(浏览器已关、正忙,或首次唤起很慢)")
        finally:
            if probe is not None:
                probe.close()
    if found is not None:
        detail = f"可用:需要时用 {found[1]} 起独立实例(零配置)"
        if attach_note:
            detail += f";另可选 attach:{attach_note}"
        info.update(ok=True, mode="launch", browser=found[0], detail=detail)
        return info
    if attach_note.startswith("另") or (hit is not None and not info["detail"]):
        pass
    if not info["detail"]:
        info["detail"] = attach_note or "本机没找到 Edge/Chrome,也没有可连的调试端口"
    return info


# ---------------------------------------------------------------- 提示词片段

BROWSER_PROMPT = """# 浏览器能力(browser_open / browser_snapshot / browser_click / browser_fill / browser_eval / browser_screenshot / browser_close)

需要看**网页的真实内容**时用它,而不是猜:官网通知页、图书馆/教务这类靠 JS 渲染的站点、
要登录才看得到的页面、用户自己写的本地页面(localhost:端口)。web_crawl 抓的是静态正文,
页面靠 JS 渲染或要登录态时它会拿到空壳 —— 这种情况直接换 browser_open。

- 默认自己起一个干净的浏览器实例(零配置、**没有**你现有的登录态)。
  只有页面必须登录才看得到(教务系统、我的课表、站内通知)时才传 mode="attach",
  它会改用你自己开着远程调试的浏览器、以你的身份取页(确认框会单独向你警告)。
- 一次任务里同一页面继续操作:再调一次 browser_open 换地址即可(复用同一个标签页)。
- 要精确数据(价格、表格、链接列表、某个元素是否存在)用 browser_eval 跑一段 JS 取回来。
- 要**操作页面**(点按钮、翻页、填表单、勾选项):先 browser_snapshot 拿到元素序号与
  字段名,再 browser_click(序号最稳)/ browser_fill(fields 传「字段名: 值」,可
  submit=true 自动提交)。不要凭空猜选择器;页面变过之后旧序号会失效,重新 snapshot。
- 用户说「截图看看 / 这页面长什么样」,或要证明页面效果正确时用 browser_screenshot,
  并把工具返回的 `![](路径)` 一段原样写进回答,用户才能看到图。
- 页面看完了(或这一问结束)用 browser_close 关掉,别让浏览器堆一屏标签页。
- 打不开时先调 browser_status 看原因;浏览器能力不可用就用 web_crawl / search_knowledge 兜底,
  并如实说明原因,绝不要凭空编造页面内容。
"""


def browser_prompt_section() -> str:
    """system prompt 里的浏览器说明(固定文本,保持前缀稳定以复用 KV Cache)。"""
    return "\n\n" + BROWSER_PROMPT
