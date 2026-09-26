"""网页版服务:标准库 HTTP 服务 + POST /api/<方法> + SSE 事件流 + 静态前端。

为什么用标准库而不是 FastAPI:对外只读模式没有附件上传(无 multipart)、
只有静态文件与一条 SSE,`ThreadingHTTPServer` 全部够用 —— 零新依赖,打包体积不变。
详见 `D:\\game\\agent网页版-实施方案.md` §4.2。

安全边界(用户选择"校园网内完全开放",所以没有登录):
- **Origin 校验**:挡住"你浏览器里任意网站 POST 本机端口"这一类 CSRF/DNS-rebinding。
  没有它,同学只要能让你打开一个网页,那个网页就能指挥这台服务器执行工具。
- **每访客限流 + 全局并发上限 + 每日调用上限**:无登录态下唯一可用的滥用控制。
- **工具白名单**:见 `web_profile`,同学只能碰校园问答相关的只读工具。
"""
from __future__ import annotations

import json
import mimetypes
import queue
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from minicode.config import CONFIG_PATH, Config
from minicode.llm import LLMClient
from minicode.paths import app_path, resource_path
from minicode.sandbox import SandboxState, policy_from_raw
from minicode.web_profile import PublicFacade, WebBridge
from minicode.web_ui import WebUI

DEFAULT_PORT = 8480
COOKIE_NAME = "ylxz_web"

TURN_CAP = 3             # 全局同时进行的回合上限:保护校园模型端点
RATE_PER_MINUTE = 12     # 每个访客每分钟的发问上限
DAILY_CAP = 2000         # 全局每日发问上限(账本落盘 web-daily.json,重启不清零)
IDLE_EVICT_SECONDS = 1800
MAX_BODY_BYTES = 1 << 20
SWEEP_INTERVAL = 60.0
MAX_VISITORS = 200       # 访客对象数上限:伪造 cookie 或 SSE 风暴不能无限堆对象
USAGE_LOG_CAP_LINES = 5000      # web-usage.log 的行数上限(与 watchdog.log 同款滚动)
USAGE_LOG_TRIM_EVERY = 50       # 每写这么多行才回头收一次盘

# 一开始就建好访客(不等 SSE),这样页面首屏就能拿到数据
INJECT_SCRIPT = '<script src="/bridge-web.js"></script>'


class RateLimiter:
    """每分钟补满的令牌桶(无登录态下的基本限流)。"""

    def __init__(self, per_minute: int):
        self.cap = max(1, per_minute)
        self.tokens = float(self.cap)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.cap, self.tokens + (now - self.updated) * self.cap / 60.0)
            self.updated = now
            if self.tokens < 1.0:
                return False
            self.tokens -= 1.0
            return True


class DailyCounter:
    """全局每日发问上限,账本落盘(web-daily.json):重启不清零。

    旧版只在内存里数,注释自己写着"进程重启清零" —— 滥用者等一次服务重启
    就把额度洗掉,而管理员想知道"今天用了多少"只能去数日志。账本按天滚动+
    原子写,模式与 proactive 的 pushed-today.json 完全同款:
    - 写不进盘(只读目录)退回纯内存:**本次运行**仍然不会超,重启后可能重新
      计数,如实接受 —— 账本不能把额度功能本身弄崩;
    - `path=None` = 纯内存模式(单元测试用,不碰盘)。
    """

    def __init__(self, cap: int, path=None):
        self.cap = cap
        self.path = path
        self.day = time.strftime("%Y-%m-%d")
        self.count = self._load(self.day)
        self._lock = threading.Lock()

    def _load(self, day: str) -> int:
        if self.path is None:
            return 0
        try:
            data = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if isinstance(data, dict) and data.get("date") == day:
            n = data.get("count")
            return n if isinstance(n, int) and n >= 0 else 0
        return 0

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            p = Path(self.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": self.day, "count": self.count},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass                      # 写不进就本次运行内存兜底,下次启动重试

    def allow(self) -> bool:
        with self._lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.count = today, self._load(today)
            if self.count >= self.cap:
                return False
            self.count += 1
            self._save()
            return True

    def used_today(self) -> int:
        with self._lock:
            return self.count


class Visitor:
    """一个浏览器访客:独立的 UI 队列 + 独立的 Bridge(私有 sessions 目录)。"""

    def __init__(self, vid: str, app: "WebApp"):
        self.vid = vid
        self.app = app
        self.ui = WebUI(on_busy=app.on_busy)
        self.bridge = WebBridge(
            app.config, app.workspace, self.ui,
            sessions_dir=app.sessions_root / vid,
            config_path=app.config_path,
            llm=app.llm_factory(app.config),
            mcp_tools=[],                      # 对外模式不接 MCP:不拉起任何第三方进程
            sandbox=SandboxState(policy_from_raw(app.config.sandbox), app.workspace),
        )
        self.facade = PublicFacade(self.bridge)
        self.limiter = RateLimiter(RATE_PER_MINUTE)
        self.last_seen = time.monotonic()
        self.created = time.time()

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_seen

    def close(self) -> None:
        self.ui.close()
        try:
            self.bridge.close()
        except Exception:
            pass


class WebApp:
    """服务端全局状态:访客表 + 全局限流/并发控制 + 用量日志。"""

    def __init__(self, config: Config, workspace: Path, *, frontend_dir: Path | None = None,
                 sessions_root: Path | None = None, config_path: Path | None = None,
                 turn_cap: int = TURN_CAP, daily_cap: int = DAILY_CAP,
                 daily_path=None, max_visitors: int = MAX_VISITORS,
                 llm_factory=None):
        self.config = config
        self.workspace = Path(workspace)
        self.frontend_dir = Path(frontend_dir) if frontend_dir else resource_path("frontend")
        self.sessions_root = Path(sessions_root) if sessions_root else app_path("web-sessions")
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.turn_cap = max(1, turn_cap)
        self.daily = DailyCounter(daily_cap, path=daily_path)
        self.max_visitors = max(1, int(max_visitors))
        self.llm_factory = llm_factory or LLMClient      # 测试可注入假模型

        self._visitors: dict[str, Visitor] = {}
        self._lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self.active_turns = 0
        self.reserved_turns = 0
        self._log_lock = threading.Lock()
        self._log_path = app_path("web-usage.log")
        self._log_since_trim = 0

    # ------------------------------------------------------------ 访客

    def visitor(self, vid: str) -> Visitor | None:
        """按 cookie 取访客;新的就建。满了返回 None(调用方回 503)。

        为什么要有上限:无登录态下 cookie 是访客自己说了算的,一个循环脚本
        可以凭空造出任意多个 vid —— 每个 Visitor 都带着一整套 WebBridge/
        LLMClient/队列,不设上限就是"任何人都能让这台机器吃内存"。
        """
        with self._lock:
            v = self._visitors.get(vid)
            if v is not None:
                v.touch()
                return v
            if len(self._visitors) >= self.max_visitors:
                return None
            self.sessions_root.mkdir(parents=True, exist_ok=True)
            v = Visitor(vid, self)
            self._visitors[vid] = v
            v.touch()
            return v

    def visitor_count(self) -> int:
        with self._lock:
            return len(self._visitors)

    # ------------------------------------------------------------ 并发与限流

    def try_reserve_turn(self) -> bool:
        with self._turn_lock:
            if self.active_turns + self.reserved_turns >= self.turn_cap:
                return False
            self.reserved_turns += 1
            return True

    def release_reservation(self) -> None:
        with self._turn_lock:
            self.reserved_turns = max(0, self.reserved_turns - 1)

    def on_busy(self, value: bool) -> None:
        """来自 WebUI 的回调:回合开始/结束(唯一可靠的回合信号)。"""
        with self._turn_lock:
            if value:
                self.reserved_turns = max(0, self.reserved_turns - 1)
                self.active_turns += 1
            else:
                self.active_turns = max(0, self.active_turns - 1)

    def load(self) -> int:
        with self._turn_lock:
            return self.active_turns + self.reserved_turns

    def log_usage(self, vid: str, ip: str, text: str) -> None:
        line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{ip}\t{vid[:8]}\t"
                f"{text.replace(chr(9), ' ')[:80]}\n")
        try:
            with self._log_lock, self._log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            return
        # 行数滚动(watchdog.log 同款):这份日志装着来源 IP 与发问内容,
        # 是最常被翻的文件之一,不该随挂机天数无限长。
        self._log_since_trim += 1
        if self._log_since_trim >= USAGE_LOG_TRIM_EVERY:
            self._log_since_trim = 0
            self._trim_usage_log()

    def _trim_usage_log(self) -> None:
        try:
            with self._log_lock:
                lines = self._log_path.read_text(encoding="utf-8").splitlines()
                if len(lines) <= USAGE_LOG_CAP_LINES:
                    return
                tmp = self._log_path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines[-USAGE_LOG_CAP_LINES:]) + "\n",
                               encoding="utf-8")
                tmp.replace(self._log_path)
        except OSError:
            pass                      # 收盘失败不影响记账本身

    # ------------------------------------------------------------ 回收线程

    def start_sweeper(self) -> threading.Thread:
        def loop():
            while True:
                time.sleep(SWEEP_INTERVAL)
                try:
                    self.evict_idle()
                except Exception:
                    pass
        t = threading.Thread(target=loop, daemon=True, name="web-sweeper")
        t.start()
        return t

    def evict_idle(self) -> int:
        """回收长时间没动静的访客,顺手停掉他们的空跑回合。"""
        now = time.monotonic()
        with self._lock:
            victims = [v for v in self._visitors.values()
                       if now - v.last_seen > IDLE_EVICT_SECONDS]
            for v in victims:
                self._visitors.pop(v.vid, None)
        for v in victims:            # 在锁外关停:close() 会 request_stop
            v.close()
        return len(victims)


# ----------------------------------------------------------------------------
# HTTP 层
# ----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "YinluxsWeb"

    app: WebApp = None          # 由 create_server 注入

    # ------------------------------------------------------------ 工具方法

    def log_message(self, fmt, *args):      # 静音默认 stderr 噪音
        pass

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._finish_headers()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, ctype: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._finish_headers()
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        """同源校验:只认 Host 一致的 Origin / Referer。

        目的不是鉴权(用户选择了开放访问),而是防止浏览器里**别的网站**
        朝这台服务器发请求 —— 没有这道校验,任意网页都能驱动校园助手。
        非浏览器客户端(如 curl)不带 Origin,放行。
        """
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if not value:
                continue
            host = urlparse(value).netloc
            if host and host != self.headers.get("Host"):
                return False
        return True

    def _visitor(self) -> Visitor | None:
        """按 cookie 取访客;没有就发一个。访客满了返回 None(调用方回 503)。"""
        vid = ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and value:
                vid = value.strip()
                break
        if not vid or len(vid) > 64 or not all(c.isalnum() or c in "-_" for c in vid):
            vid = uuid.uuid4().hex
            self._set_cookie = (f"{COOKIE_NAME}={vid}; Path=/; HttpOnly; "
                                f"SameSite=Strict; Max-Age=86400")
        else:
            self._set_cookie = ""
        return self.app.visitor(vid)

    _set_cookie = ""

    def _finish_headers(self) -> None:
        if self._set_cookie:
            self.send_header("Set-Cookie", self._set_cookie)
            self._set_cookie = ""

    # ------------------------------------------------------------ GET

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/events":
                # SSE 与 POST 过同一道 Origin 校验:跨站虽然读不到流(响应无
                # CORS 头),但**连接本身会建立** —— 任意网页都能让访问者的
                # 浏览器对这台服务器开 SSE 风暴,每条连接都要建一个访客。
                if not self._origin_ok():
                    self._json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
                    return
                self._sse()
                return
            if not self._origin_ok():
                self._json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
                return
            self._static(path)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            try:
                self._json({"ok": False, "msg": f"服务内部错误:{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _static(self, path: str) -> None:
        frontend = self.app.frontend_dir
        if path in ("/", "/index.html"):
            page = frontend / "index.html"
            if not page.is_file():
                self._text("前端资源缺失(需要 frontend/index.html)", "text/plain; charset=utf-8", 500)
                return
            html = page.read_text(encoding="utf-8")
            # 注入桥接 shim:index.html 本身一行不用改(见实施方案 §4)
            html = html.replace("<head>", "<head>\n" + INJECT_SCRIPT, 1)
            self._text(html, "text/html; charset=utf-8")
            return

        rel = unquote(path).lstrip("/")
        target = (frontend / rel).resolve()
        try:
            target.relative_to(frontend.resolve())
        except ValueError:
            self._text("非法路径", "text/plain; charset=utf-8", 403)
            return
        if not target.is_file():
            self._text("未找到", "text/plain; charset=utf-8", 404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix in (".js", ".css", ".html"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ SSE

    def _sse(self) -> None:
        visitor = self._visitor()
        if visitor is None:
            self._json({"ok": False, "msg": "在线访客已满,请稍后再试"}, 503)
            return
        self.close_connection = True        # 流式响应不复用连接
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self._finish_headers()
        self.end_headers()
        q = visitor.ui.queue
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    visitor.touch()
                    continue
                if event.get("type") == "__closed__":
                    break
                payload = json.dumps(event, ensure_ascii=False, default=str)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                visitor.touch()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            # 前端断开:只有当这个访客确实有回合在跑时才停,别让线程空烧校园模型。
            # (停止标志是跨回合的,所以下一次发问前还会再清一次,见 PublicFacade)
            try:
                if visitor.bridge.running_sessions():
                    visitor.bridge.stop_task()
            except Exception:
                pass

    # ------------------------------------------------------------ POST

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                self._json({"ok": False, "msg": "未知接口"}, 404)
                return
            if not self._origin_ok():
                self._json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                self._json({"ok": False, "msg": "请求过大"}, 413)
                return
            raw = self.rfile.read(length) if length else b""
            try:
                args = json.loads(raw.decode("utf-8")) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json({"ok": False, "msg": "请求体不是有效 JSON"}, 400)
                return
            if not isinstance(args, dict):
                self._json({"ok": False, "msg": "请求体必须是 JSON 对象"}, 400)
                return

            name = path[len("/api/"):]
            visitor = self._visitor()
            if visitor is None:
                self._json({"ok": False, "msg": "在线访客已满,请稍后再试"}, 503)
                return
            result = self._dispatch(visitor, name, args)
            self._json(result)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            try:
                self._json({"ok": False, "msg": f"服务内部错误:{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _dispatch(self, visitor: Visitor, name: str, args: dict) -> dict:
        starts_turn = name in ("send_task", "edit_resend")
        if starts_turn:
            text = str(args.get("text") or args.get("new_text") or "")
            if not visitor.limiter.allow():
                return {"ok": False,
                        "msg": f"问得有点快啦,每分钟最多 {RATE_PER_MINUTE} 次,稍等一下再发"}
            if not self.app.daily.allow():
                return {"ok": False, "msg": "今天的提问额度已用完,请明天再来或联系管理员"}
            if not self.app.try_reserve_turn():
                return {"ok": False,
                        "msg": f"现在有 {self.app.load()} 位同学在提问,忙不过来,请稍等十几秒再发"}
            reserved = True
        else:
            text, reserved = "", False

        try:
            result = visitor.facade.call(name, args)
        except Exception:
            if reserved:
                self.app.release_reservation()
            raise
        if reserved:
            # 没真正开跑(比如该会话正忙)就把名额还回去
            if not (isinstance(result, dict) and result.get("ok")):
                self.app.release_reservation()
            else:
                self.app.log_usage(visitor.vid, self.client_address[0], text)
        return result


# ----------------------------------------------------------------------------


def create_server(config: Config, workspace: Path, host: str = "0.0.0.0",
                  port: int = DEFAULT_PORT, **opts) -> ThreadingHTTPServer:
    """建好服务但不起(测试用 port=0 拿随机端口)。"""
    app = WebApp(config, workspace, **opts)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    # 把 app 挂在 httpd 上:关停时要靠它找回 sweeper(有意为之,不是漏写 self)
    httpd.web_app = app
    app.start_sweeper()
    return httpd


def local_ips() -> list[str]:
    """列出本机可用于访问的地址(校园网内同学用这个)。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.223.24.16", 8080))      # 校园模型机:探出真实出口网卡
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def serve(config: Config, workspace: Path, host: str = "0.0.0.0",
          port: int = DEFAULT_PORT, open_browser: bool = False, **opts) -> None:
    httpd = create_server(config, workspace, host, port, **opts)
    real_port = httpd.server_address[1]
    ips = local_ips()
    # flush=True:输出被重定向(日志文件、IDE 面板、管道)时 Python 会缓冲,
    # 不刷的话"启动横幅一个字都看不到",用户会以为没起来。
    print(f"引路学长 · 校园助手网页版已启动(工作区 {workspace})", flush=True)
    print(f"  本机访问:  http://127.0.0.1:{real_port}", flush=True)
    # 额度可见性:账本落盘后这就是一个真数字(重启不清零),不再是"只能去数日志"
    print(f"  今日发问额度:已用 {httpd.web_app.daily.used_today()}/{httpd.web_app.daily.cap}", flush=True)
    for ip in ips:
        print(f"  局域网访问:http://{ip}:{real_port}", flush=True)
    if not any(ip.startswith("10.") for ip in ips):
        # 本机没有校园网地址:同网段设备能开,校园网里的同学打不开
        print("  ⚠ 本机没有校园网(10.x)地址 —— 上面这些地址只有与之处于同一局域网"
              "的设备能打开。", flush=True)
        print("    要让同学从校园网访问,需把服务跑在校园网内的机器上,"
              "并在那台机器上放行本端口。", flush=True)
    if host not in ("127.0.0.1", "localhost"):
        print("  ⚠ 已监听非本机地址:同网段任何人都能用本服务,并消耗校园模型额度。", flush=True)
        print("    同学可用的能力已限制在只读白名单(见 minicode/web_profile.py 的 PUBLIC_TOOLS);", flush=True)
        print("    发问记录与来源 IP 写在 web-usage.log。按 Ctrl+C 停止。", flush=True)
    if not resource_path("frontend", "index.html").is_file():
        print("  ✗ 找不到 frontend/index.html,网页会打不开 —— 请在项目根目录运行,"
              "或确认分发包含 _internal/frontend。", flush=True)
    if open_browser:
        import webbrowser
        threading.Thread(target=lambda: webbrowser.open(f"http://127.0.0.1:{real_port}"),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…", flush=True)
    finally:
        httpd.shutdown()
        httpd.server_close()
