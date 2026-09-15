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
MAX_SESSIONS = 6                     # 同时活着的标签页上限(超出关最旧的)
LAUNCH_TIMEOUT = 25.0                # 冷启动耐心值(秒)

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


def probe_endpoint(port: int, timeout: float = 1.5) -> dict | None:
    """探测某个端口上有没有开着的 CDP 端点,有则返回 /json/version 内容。"""
    data = _http_json(f"http://127.0.0.1:{int(port)}/json/version", timeout=timeout)
    if data and data.get("webSocketDebuggerUrl"):
        return data
    return None


def _active_port_files() -> list[int]:
    """读 Edge/Chrome 用户目录里的 DevToolsActivePort(用户自己开了远程调试时)。"""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    roots = [
        Path(local) / "Microsoft" / "Edge" / "User Data",
        Path(local) / "Google" / "Chrome" / "User Data",
    ]
    ports: list[int] = []
    for root in roots:
        f = root / "DevToolsActivePort"
        try:
            first = f.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            ports.append(int(first.strip()))
        except Exception:
            continue
    return ports


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


@dataclass
class _State:
    conn: _Conn | None = None
    browser: str = ""
    mode: str = ""                    # attach / launch
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

def _attach_endpoint() -> tuple[str, dict] | None:
    """找一个可以直接 attach 的调试端点(独立于我们自己的实例)。"""
    env = (os.environ.get(ENV_CDP_URL) or "").strip()
    if env:
        if env.startswith("ws"):
            raise BrowserUnavailable(
                f"{ENV_CDP_URL} 请写成 http://127.0.0.1:<port> 的形式"
                "(具体 WebSocket 地址由程序从 /json/version 取)")
        m = re.search(r"(\d{2,5})", env)
        if not m:
            raise BrowserUnavailable(f"{ENV_CDP_URL} 看不出端口:{env}")
        info = probe_endpoint(int(m.group(1)))
        if not info:
            raise BrowserUnavailable(
                f"{ENV_CDP_URL} 指向的 {env} 连不上:确认浏览器开着且带 --remote-debugging-port")
        return info["webSocketDebuggerUrl"], info
    ports: list[int] = list(DEFAULT_PORTS)
    for p in _active_port_files():
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


def _ensure_conn(*, mode: str = "auto", headless: bool = True) -> tuple[_Conn, str, str]:
    """拿到连接(优先复用),返回 (连接, 浏览器产品串, 接入方式)。"""
    with _LOCK:
        if _STATE.conn is not None:
            return _STATE.conn, _STATE.browser, _STATE.mode
        if mode not in ("auto", "attach", "launch"):
            raise BrowserError(f"mode 只能是 auto/attach/launch,收到:{mode}")

        if mode in ("auto", "attach"):
            hit = _attach_endpoint()
            if hit is not None:
                ws_url, info = hit
                conn = _Conn(ws_url)
                _STATE.conn, _STATE.browser, _STATE.mode = conn, info.get("Browser", "浏览器"), "attach"
                return conn, _STATE.browser, _STATE.mode
            if mode == "attach":
                raise BrowserUnavailable(
                    "没找到开着远程调试的浏览器。要在你自己的登录态里打开页面,请先让 Edge/Chrome "
                    "带 --remote-debugging-port=9222 启动(Edge 的 edge://inspect 里有对应开关),"
                    f"或用环境变量 {ENV_CDP_URL} 指定端口。")
        proc, port, info = launch_browser(headless=headless)
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
        _STATE.proc, _STATE.port = proc, port
        return conn, _STATE.browser, _STATE.mode


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
    """标签页太多时关掉最旧的,别让浏览器堆一屏。"""
    if len(_STATE.sessions) <= MAX_SESSIONS:
        return
    ordered = sorted(_STATE.sessions.items(), key=lambda kv: kv[1].opened_at)
    for k, s in ordered:
        if len(_STATE.sessions) <= MAX_SESSIONS:
            break
        if k == keep:
            continue
        _close_session_locked(k, s)


def _close_session_locked(key: str, sess: PageSession) -> None:
    _STATE.sessions.pop(key, None)
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


def open_page(url: str, *, wait_ms: int = DEFAULT_WAIT_MS, wait_for: str = "",
              max_chars: int = DEFAULT_MAX_CHARS, mode: str = "auto",
              headless: bool = True) -> dict:
    """打开一个网址并返回标题 + 正文文本(JS 渲染后的 DOM)。"""
    target = check_url(url)
    wait_ms = max(0, min(int(wait_ms), MAX_WAIT_MS))
    max_chars = max(200, min(int(max_chars), MAX_CHARS))
    sess = _ensure_session(mode=mode, headless=headless)
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


def shutdown() -> None:
    """收工:关掉我们自己起的浏览器实例(attach 到用户浏览器的只断连接)。"""
    with _LOCK:
        for key, sess in list(_STATE.sessions.items()):
            with contextlib.suppress(Exception):
                sess.conn.call("Target.closeTarget", {"targetId": sess.target_id}, timeout=3)
        _STATE.sessions.clear()
        conn, _STATE.conn = _STATE.conn, None
        proc, _STATE.proc = _STATE.proc, None
        _STATE.mode = ""
        _STATE.browser = ""
        if conn is not None:
            conn.close()
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)


atexit.register(shutdown)


# ---------------------------------------------------------------- 自检

def health() -> dict:
    """能不能用浏览器能力:给 /browser 命令与排障用(不主动弹提示)。"""
    info: dict[str, Any] = {"ok": False, "mode": "", "browser": "", "detail": ""}
    with _LOCK:
        _sweep_dead_sessions_locked()
        if _STATE.conn is not None:
            info.update(ok=True, mode=_STATE.mode, browser=_STATE.browser,
                        detail=f"已连接({len(_STATE.sessions)} 个标签页)")
            return info
    try:
        hit = _attach_endpoint()
    except BrowserUnavailable as exc:
        info["detail"] = str(exc)
        hit = None
    if hit is not None:
        info.update(ok=True, mode="attach", browser=hit[1].get("Browser", "浏览器"),
                    detail="可用:会连你已开远程调试的浏览器(带登录态)")
        return info
    try:
        found = find_browser_exe()
    except BrowserUnavailable as exc:
        found = None
        info["detail"] = str(exc)
    if found is None:
        if not info["detail"]:
            info["detail"] = "本机没找到 Edge/Chrome"
        return info
    info.update(ok=True, mode="launch",
                browser=f"{found[0]}",
                detail=f"可用:需要时用 {found[1]} 起独立实例(零配置)")
    return info


# ---------------------------------------------------------------- 提示词片段

BROWSER_PROMPT = """# 浏览器能力(browser_open / browser_eval / browser_screenshot / browser_close)

需要看**网页的真实内容**时用它,而不是猜:官网通知页、图书馆/教务这类靠 JS 渲染的站点、
要登录才看得到的页面、用户自己写的本地页面(localhost:端口)。web_crawl 抓的是静态正文,
页面靠 JS 渲染或要登录态时它会拿到空壳 —— 这种情况直接换 browser_open。

- 一次任务里同一页面继续操作:再调一次 browser_open 换地址即可(复用同一个标签页)。
- 要精确数据(价格、表格、链接列表、某个元素是否存在)用 browser_eval 跑一段 JS 取回来。
- 用户说「截图看看 / 这页面长什么样」,或要证明页面效果正确时用 browser_screenshot,
  并把工具返回的 `![](路径)` 一段原样写进回答,用户才能看到图。
- 页面看完了(或这一问结束)用 browser_close 关掉,别让浏览器堆一屏标签页。
- 打不开时先调 browser_status 看原因;浏览器能力不可用就用 web_crawl / search_knowledge 兜底,
  并如实说明原因,绝不要凭空编造页面内容。
"""


def browser_prompt_section() -> str:
    """system prompt 里的浏览器说明(固定文本,保持前缀稳定以复用 KV Cache)。"""
    return "\n\n" + BROWSER_PROMPT
