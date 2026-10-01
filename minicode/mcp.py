"""MCP(Model Context Protocol)客户端:把外部 MCP 服务器的工具挂进本机工具注册表。

实现要点(对齐星瑶 Y-Code 的做法,按本项目的极简风格重写):
- 传输(v0.9.24 起三选一):
    stdio —— 我们替它起进程,一行一个 JSON-RPC 报文(服务器日志走 stderr,不混进协议流);
    http  —— Streamable HTTP:POST 到 url,响应可能是 JSON,也可能是 SSE 一条;
    sse   —— 旧式 HTTP+SSE:GET 一条长流收消息(先给 endpoint),POST 发请求;
- 生命周期:进程级长连接,启动时按配置**惰性宽容**连接——连不上只记日志跳过,
  绝不让一个坏配置把整个应用带崩;
- 握手:initialize → notifications/initialized → tools/list;
- 调用:tools/call,结果按 content[].text 拼接,isError 前置「错误:」;
- 超时:启动 10s、调用 60s(可被服务器配置覆盖);
- 权限:MCP 工具默认**不是只读**——会走确认门;受信任服务器可在配置里标
  read_only: true 免确认。

密钥的规矩(和模型 API Key 同一条):headers 的值写成 ${ENV} 形式即可,
学长在读配置时展开 —— 这样密钥只留在环境变量里,不进 config.json。
headers 与 URL 的查询串都不会出现在侧栏(tools_summary 只给 id/名字/工具数)
或日志里(报错时地址会去掉 ?... 才落日志)。

配置文件(config.json)示例::

    "mcp_servers": [
      {"id": "filesystem", "command": "npx",
       "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"],
       "enabled": true, "read_only": false},
      {"id": "school-mcp", "transport": "http", "url": "https://mcp.school.edu/mcp",
       "headers": {"Authorization": "Bearer ${SCHOOL_MCP_TOKEN}"}, "read_only": true}
    ]
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from minicode.tools.base import Tool

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "yinlu-xuezhang"
# 学长只实现了 tools 这一个原语:resources / prompts / sampling / logging 都没接。
# 握手时客户端声明空 capabilities(不假装支持),而对端声明了什么以前**直接丢掉** ——
# 于是"这台服务器还能给什么、学长用不上哪几样"在界面与探测结果里永远看不见。
CLIENT_PRIMITIVES = ("tools",)

DEFAULT_START_TIMEOUT = 10.0
DEFAULT_CALL_TIMEOUT = 60.0
STDERR_KEEP_LINES = 20
TOOL_NAME_MAX = 60


# --------------------------------------------------------------------- 配置

@dataclass
class McpServerConfig:
    """一个 MCP 服务器的启动配置(传输方式三选一:stdio / http / sse)。"""

    sid: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    read_only: bool = False
    # 信任级(注入防御 M1):MCP 是第三方服务器,返回值默认当外部内容围栏。
    # 确实信任的自建服务器可在 config 里写 "trusted": true 降为 local。
    trusted: bool = False
    start_timeout: float = DEFAULT_START_TIMEOUT
    call_timeout: float = DEFAULT_CALL_TIMEOUT
    description: str = ""
    # v0.9.24:不是每台服务器都得学长替它起进程 —— 学校/公司跑在服务器上的 MCP
    # 只有一个网址。transport: stdio(默认,拉起子进程)/ http(Streamable HTTP,
    # POST 到 url)/ sse(老式 HTTP+SSE:GET 一条长流收消息,POST 发请求)。
    transport: str = "stdio"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.description or self.sid


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _norm_transport(value) -> str | None:
    """配置里的 transport → 内部名。认不出来返回 None(这一项跳过,不猜)。"""
    key = str(value or "").strip().lower()
    if not key:
        return "stdio"
    return {"stdio": "stdio", "local": "stdio",
            "http": "http", "streamable-http": "http", "streamable_http": "http",
            "sse": "sse", "http+sse": "sse", "http_sse": "sse"}.get(key)


def _usable_url(value) -> str:
    """只认 http/https 的绝对地址 —— file:// 之类一律不给进协议层。"""
    url = str(value or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme.lower() in ("http", "https") and parts.netloc else ""


def parse_servers(raw) -> list[McpServerConfig]:
    """从配置原文解析服务器列表;非法项直接跳过(配置永远不该让应用起不来)。"""
    out: list[McpServerConfig] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        url = _usable_url(item.get("url"))
        transport = _norm_transport(item.get("transport"))
        if transport is None:
            continue
        if transport == "stdio":
            if not command:                     # 老配置:没有 command 就没有可拉起的东西
                continue
        elif not url:                           # HTTP/SSE:没有地址就无从连起
            continue
        sid = str(item.get("id") or item.get("name") or f"server{i}").strip()
        sid = re.sub(r"[^0-9A-Za-z_-]+", "_", sid).strip("_") or f"server{i}"
        if sid in seen:
            continue
        seen.add(sid)
        env_raw = item.get("env")
        env = ({str(k): str(v) for k, v in env_raw.items()}
               if isinstance(env_raw, dict) else {})
        hdr_raw = item.get("headers")
        headers = ({str(k): str(v) for k, v in hdr_raw.items()}
                   if isinstance(hdr_raw, dict) else {})
        out.append(McpServerConfig(
            sid=sid,
            command=command,
            args=_as_str_list(item.get("args")),
            env=env,
            enabled=bool(item.get("enabled", True)),
            read_only=bool(item.get("read_only", False)),
            trusted=bool(item.get("trusted", False)),
            start_timeout=float(item.get("start_timeout", DEFAULT_START_TIMEOUT)),
            call_timeout=float(item.get("call_timeout", DEFAULT_CALL_TIMEOUT)),
            description=str(item.get("description") or ""),
            transport=transport,
            url=url,
            headers=headers,
        ))
    return out


def servers_to_raw(servers: list[McpServerConfig]) -> list[dict]:
    """回写 config.json 用的序列化(env/headers 保留,空值不写)。"""
    out = []
    for s in servers:
        item: dict = {"id": s.sid}
        if s.command:
            item["command"] = s.command
        if s.transport and s.transport != "stdio":
            item["transport"] = s.transport
        if s.url:
            item["url"] = s.url
        if s.headers:
            item["headers"] = dict(s.headers)
        if s.args:
            item["args"] = list(s.args)
        if s.env:
            item["env"] = dict(s.env)
        if not s.enabled:
            item["enabled"] = False
        if s.read_only:
            item["read_only"] = True
        if s.trusted:
            item["trusted"] = True
        if s.description:
            item["description"] = s.description
        out.append(item)
    return out


# --------------------------------------------------------------------- 传输层
# 学长和 MCP 服务器之间只有两种活法:要么我们替它起一个进程、用 stdin/stdout 说话
# (stdio,最常见);要么它早就跑在某台机器上、只有一个网址(http / sse)。
# 三种传输只做同一件事:把一条 JSON-RPC 报文送出去,把带同一个 id 的响应拿回来。
# 协议本身(握手、tools/list、错误还原)只写在 McpClient 一处,这里不重复。


class Transport:
    """一个 MCP 服务器的通道:子类只需实现 open/request/notify/close。"""

    kind = "stdio"

    def __init__(self, cfg: McpServerConfig, log=None):
        self.cfg = cfg
        self._log = log

    def open(self) -> None:
        """把通道准备好(拉进程 / 开长流)。失败抛异常,由客户端记成"连接失败"。"""
        raise NotImplementedError

    def request(self, msg: dict, timeout: float) -> dict:
        """送出一条请求报文,返回**原始响应报文**(带 id 与 result/error)。"""
        raise NotImplementedError

    def notify(self, msg: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return False

    def tail(self) -> str:
        """最近一条排错线索(stderr / 网络错误原文);**不含密钥**。"""
        return ""

    def _warn(self, text: str) -> None:
        if callable(self._log):
            try:
                self._log(text)
            except Exception:
                pass


def _clip(text, n=200) -> str:
    return re.sub(r"\s+", " ", str(text or ""))[:n]


def _safe_url(url) -> str:
    """报错/记日志用的地址:去掉查询串 —— 有的托管 MCP 把密钥挂在 ?key=... 上。"""
    try:
        p = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return "(地址读不懂)"
    return f"{p.scheme}://{p.netloc}{p.path}" if p.netloc else _clip(url, 60)


def _json_obj(text):
    """一段文本 → dict(不是 JSON 或不是对象都给 None,不猜)。"""
    if isinstance(text, dict):
        return text
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _iter_sse(lines):
    """SSE 行流 → (event, data) 逐条产出。吃 bytes 也吃 str 行,多行 data 拼起来。"""
    event, data = "", []
    for raw in lines or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if not line:                                   # 空行 = 一个事件结束
            if event or data:
                yield event, "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):                       # 注释/心跳(服务器保活用)
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value.strip()
        elif field == "data":
            data.append(value)
    if event or data:
        yield event, "\n".join(data)


class _StreamedTransport(Transport):
    """「消息陆续从一条长流上回来,得按 id 等」—— stdio 与旧式 SSE 共用这套等待。"""

    def __init__(self, cfg: McpServerConfig, log=None):
        super().__init__(cfg, log)
        self._cv = threading.Condition()
        self._inbox: dict[int, dict] = {}
        self._closed = False

    def _deliver(self, msg: dict) -> None:
        with self._cv:
            self._inbox[msg["id"]] = msg
            self._cv.notify_all()

    def _take(self, rid, timeout: float, method: str) -> dict:
        deadline = time.monotonic() + timeout
        with self._cv:
            while rid not in self._inbox:
                if self._closed:
                    raise McpError(f"服务器 {self.cfg.sid} 已关闭")
                remain = deadline - time.monotonic()
                if remain <= 0:
                    tail = self.tail()
                    hint = f"; 最近输出: {tail[-300:]}" if tail else ""
                    raise McpError(f"{method} 超时({timeout:g}s){hint}")
                self._cv.wait(min(remain, 0.5))
            return self._inbox.pop(rid)

    def _stop_waiting(self) -> None:
        """关掉等待:让还在 _take 里等的线程立刻拿"已关闭",而不是干等到超时。"""
        self._closed = True
        with self._cv:
            self._cv.notify_all()


class StdioTransport(_StreamedTransport):
    """我们拉起子进程:一行一个 JSON-RPC 报文(日志走 stderr,不混进协议流)。"""

    kind = "stdio"

    def __init__(self, cfg: McpServerConfig, log=None):
        super().__init__(cfg, log)
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_KEEP_LINES)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def tail(self) -> str:
        return "\n".join(self._stderr)

    def open(self) -> None:
        self._spawn()

    def request(self, msg: dict, timeout: float) -> dict:
        self._write(msg)
        return self._take(msg.get("id"), timeout, str(msg.get("method") or "?"))

    def notify(self, msg: dict) -> None:
        self._write(msg)

    def _spawn(self) -> None:
        # npx/uvx 在 Windows 上是 .cmd,必须解析成实际路径再交给 CreateProcess
        exe = shutil.which(self.cfg.command) or self.cfg.command
        if not Path(exe).exists() and shutil.which(self.cfg.command) is None:
            raise McpError(f"找不到命令 {self.cfg.command}(检查是否安装并加入 PATH)")
        cmd = [exe, *self.cfg.args]
        env = dict(os.environ)
        env.update({k: os.path.expandvars(v) for k, v in self.cfg.env.items()})
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env, creationflags=creationflags,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name=f"mcp-{self.cfg.sid}")
        self._reader.start()
        threading.Thread(target=self._stderr_loop, daemon=True,
                         name=f"mcp-err-{self.cfg.sid}").start()

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                      # 非协议行(stdout 上的日志)忽略
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id")
            if mid is None:                   # 服务器主动通知/请求:本项目暂不处理
                continue
            self._deliver(msg)

    def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip())

    def _write(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpTransportError(f"服务器 {self.cfg.sid} 未在运行")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except OSError as e:
            raise McpTransportError(f"写入 {self.cfg.sid} 失败: {e}")

    def close(self) -> None:
        proc = self._proc
        self._stop_waiting()
        if proc is None:
            return
        self._proc = None
        try:
            if proc.poll() is None:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass


class HttpTransport(Transport):
    """Streamable HTTP(现行规范):一条 POST 进去、一条响应回来。

    响应可能是 application/json(直接就是那条报文),也可能是 text/event-stream
    (在流里找 id 对上的一条)。后一种**边读边找、找到就走**:有些服务器答完还
    把流留着,等它关流就等于把请求挂死。

    会话:MCP 规定服务器可以在 initialize 的响应头里给 Mcp-Session-Id,之后每条
    请求都得带上 —— 漏了它,多数服务器会把后续请求当成新会话直接拒。关的时候
    尽力 DELETE 一次让服务器自己回收会话(它不接也不影响我们)。
    """

    kind = "http"

    def __init__(self, cfg: McpServerConfig, log=None):
        super().__init__(cfg, log)
        self._session = ""
        self._last = ""
        self._ok = False

    @property
    def alive(self) -> bool:
        return self._ok

    def tail(self) -> str:
        return self._last

    def open(self) -> None:
        pass                                  # HTTP 没有"连接"这一步:第一条请求就是探测

    def request(self, msg: dict, timeout: float) -> dict:
        return self._post(msg, timeout, want_id=msg.get("id"))

    def notify(self, msg: dict) -> None:
        self._post(msg, self.cfg.start_timeout, want_id=None)

    def close(self) -> None:
        sid, self._session = self._session, ""
        self._ok = False
        if not sid:
            return
        req = urllib.request.Request(self.cfg.url, headers=self._headers(body=False),
                                     method="DELETE")
        with contextlib.suppress(Exception):    # 回收会话是礼貌,不是必需
            urllib.request.urlopen(req, timeout=3).read()

    # ---- 内部
    def _headers(self, body: bool = True) -> dict:
        """请求头:协议要的 + 配置里给的。值支持 ${ENV} 展开(密钥别写明文)。"""
        h = {"Accept": "application/json, text/event-stream"}
        if body:
            h["Content-Type"] = "application/json"
        for k, v in self.cfg.headers.items():
            h[str(k)] = os.path.expandvars(str(v))
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _post(self, msg: dict, timeout: float, want_id):
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.cfg.url, data=payload,
                                     headers=self._headers(), method="POST")
        method = str(msg.get("method") or "?")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            text = ""
            with contextlib.suppress(Exception):
                text = exc.read().decode("utf-8", "replace")
            self._last = f"HTTP {exc.code}: {_clip(text)}"
            raise McpError(f"{method} 失败:{self._last}(→ {_safe_url(self.cfg.url)})") from None
        except Exception as exc:
            self._last = f"{type(exc).__name__}: {exc}"
            raise McpError(f"连不上 {_safe_url(self.cfg.url)}({self._last})") from None
        with resp:
            sid = resp.headers.get("Mcp-Session-Id") or ""
            if sid:
                self._session = sid
            ctype = resp.headers.get("Content-Type") or ""
            if want_id is None:                 # 通知:服务器回 200/202 都算收到
                self._ok = True
                return None
            if "text/event-stream" in ctype:
                for _event, data in _iter_sse(resp):
                    obj = _json_obj(data)
                    if obj is not None and obj.get("id") == want_id:
                        self._ok = True
                        return obj
                raise McpError(f"{method} 的响应里没有 id={want_id} 的那条报文")
            text = resp.read().decode("utf-8", "replace")
        obj = _json_obj(text)
        if obj is None:
            if not text.strip():
                raise McpError(f"{method} 回了空响应:确认 url 指向 MCP 端点"
                               "(新版规范通常以 /mcp 结尾)")
            raise McpError(f"{method} 的响应不是 JSON:{_clip(text)}")
        self._ok = True
        return obj


class SseTransport(_StreamedTransport):
    """旧式 HTTP+SSE(2024-11-05 那版):GET 一条长流收消息,POST 发请求。

    流的第一条 `event: endpoint` 告诉我们该往哪儿 POST(通常是 /messages?sessionId=…)。
    现在的服务器多用上面那种 Streamable HTTP,但学校、公司里跑老实现的不少,
    所以两种都留着:配置里写 "transport": "sse" 即可。
    """

    kind = "sse"

    def __init__(self, cfg: McpServerConfig, log=None):
        super().__init__(cfg, log)
        self._endpoint_ready = threading.Event()
        self._post_url = ""
        self._resp = None
        self._thread: threading.Thread | None = None
        self._last = ""

    @property
    def alive(self) -> bool:
        return bool(self._post_url) and not self._closed

    def tail(self) -> str:
        return self._last

    def open(self) -> None:
        self._thread = threading.Thread(target=self._listen, daemon=True,
                                        name=f"mcp-sse-{self.cfg.sid}")
        self._thread.start()
        if not self._endpoint_ready.wait(self.cfg.start_timeout):
            self._stop_waiting()
            raise McpError(f"{self.cfg.start_timeout:g}s 内没等到 SSE 的 endpoint 事件"
                           f"(→ {_safe_url(self.cfg.url)})")
        if not self._post_url:
            self._stop_waiting()
            raise McpError(f"连不上 {_safe_url(self.cfg.url)}"
                           f"({self._last or '这条 SSE 流没给出 endpoint'})")

    def request(self, msg: dict, timeout: float) -> dict:
        self._post(msg)
        return self._take(msg.get("id"), timeout, str(msg.get("method") or "?"))

    def notify(self, msg: dict) -> None:
        self._post(msg)

    def close(self) -> None:
        self._stop_waiting()
        resp, self._resp = self._resp, None
        if resp is not None:
            with contextlib.suppress(Exception):
                resp.close()

    # ---- 内部
    def _headers(self) -> dict:
        h = {"Accept": "text/event-stream"}
        for k, v in self.cfg.headers.items():
            h[str(k)] = os.path.expandvars(str(v))
        return h

    def _listen(self) -> None:
        """长流:一条 GET 用到关。收到 endpoint 就放行 open(),之后的报文按 id 入箱。"""
        req = urllib.request.Request(self.cfg.url, headers=self._headers(), method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=self.cfg.call_timeout)
            self._resp = resp
            for event, data in _iter_sse(resp):
                if self._closed:
                    break
                text = data.strip()
                if event == "endpoint" and text:
                    self._post_url = urllib.parse.urljoin(self.cfg.url, text)
                    self._endpoint_ready.set()
                    continue
                obj = _json_obj(text)
                if obj is not None and obj.get("id") is not None:
                    self._deliver(obj)
        except Exception as exc:
            self._last = f"{type(exc).__name__}: {exc}"
        finally:
            self._endpoint_ready.set()          # 别让 open() 白等一场超时
            with self._cv:
                self._cv.notify_all()           # 也别让在等响应的线程干等到超时

    def _post(self, msg: dict) -> None:
        if not self._post_url:
            raise McpError(f"服务器 {self.cfg.sid} 还没给出 POST 地址(endpoint)")
        req = urllib.request.Request(
            self._post_url,
            data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.start_timeout) as resp:
                resp.read()                     # 这类服务器通常回 202,响应体在长流上
        except urllib.error.HTTPError as exc:
            text = ""
            with contextlib.suppress(Exception):
                text = exc.read().decode("utf-8", "replace")
            self._last = f"HTTP {exc.code}: {_clip(text)}"
            raise McpError(f"{msg.get('method')} 投递失败:{self._last}"
                           f"(→ {_safe_url(self._post_url)})") from None
        except Exception as exc:
            self._last = f"{type(exc).__name__}: {exc}"
            raise McpError(f"{msg.get('method')} 投递失败:{self._last}"
                           f"(→ {_safe_url(self._post_url)})") from None


_TRANSPORTS = {"stdio": StdioTransport, "http": HttpTransport, "sse": SseTransport}


def make_transport(cfg: McpServerConfig, log=None) -> Transport:
    """按配置挑传输。认不出的 transport 走 stdio(parse_servers 已经拦过一道)。"""
    return _TRANSPORTS.get(cfg.transport, StdioTransport)(cfg, log=log)


# --------------------------------------------------------------------- 客户端

class McpError(Exception):
    """MCP 协议层、进程层或网络层错误。"""


class McpTransportError(McpError):
    """**通道层**失败(进程死/管道断/写失败)—— 与"服务器明确报错"分开,
    call_tool 据此决定要不要自动重连:通道死了重连一次,服务器拒绝不重连。"""


class McpClient:
    """单个 MCP 服务器的客户端:握手 + 拉工具清单 + 调用(通道细节在 Transport 里)。"""

    def __init__(self, cfg: McpServerConfig, log=None, transport: Transport | None = None):
        self.cfg = cfg
        self._log = log
        self.transport = transport or make_transport(cfg, log=log)
        self._lock = threading.Lock()          # 串行化请求,避免 id 交叉
        self._next_id = 0
        self._closed = False
        self.tools: list[dict] = []            # tools/list 原始结果
        self.server_info: dict = {}
        self.server_caps: dict = {}            # 对端在握手里声明的能力(原样留着)
        self.error: str = ""

    # ----------------------------------------------------------- 生命周期

    @property
    def _proc(self):
        """我们替它拉起的那个进程(只有 stdio 传输才有;HTTP 传输给 None)。"""
        return getattr(self.transport, "_proc", None)

    @property
    def alive(self) -> bool:
        return not self._closed and self.transport.alive

    def start(self) -> bool:
        """开好通道并完成握手 + 拉取工具清单;失败返回 False 且不抛异常。"""
        try:
            self.transport.open()
            info = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": _version()},
            }, timeout=self.cfg.start_timeout)
            self.server_info = (info or {}).get("serverInfo", {}) if isinstance(info, dict) else {}
            caps = (info or {}).get("capabilities") if isinstance(info, dict) else None
            self.server_caps = caps if isinstance(caps, dict) else {}
            self._notify("notifications/initialized", {})
            listed = self._request("tools/list", {}, timeout=self.cfg.start_timeout)
            tools = (listed or {}).get("tools", []) if isinstance(listed, dict) else []
            self.tools = [t for t in tools if isinstance(t, dict) and t.get("name")]
            return True
        except Exception as e:                 # 配置错/命令不存在/地址不通/握手超时
            self.error = f"{type(e).__name__}: {e}"
            self._warn(f"MCP 服务器 {self.cfg.sid}({self.transport.kind})连接失败: {self.error}")
            self.close()
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.transport.close()

    def restart(self) -> bool:
        """断线重连:换一条**全新通道**并重新握手,本对象身份不变。

        为什么必须原地重启:注册表里的工具 handler 在启动时就绑定了**这个**
        McpClient 实例 —— 换新对象等于旧绑定全部悬空。重连后 tools/list 会
        重新拉一遍(self.tools 刷新),但注册表里的工具清单仍是启动时那一份;
        服务器换了工具集的话,调用会如实报错,不做静默对账。
        """
        with contextlib.suppress(Exception):
            self.transport.close()
        self._closed = False
        self.tools, self.server_info, self.error = [], {}, ""
        self.server_caps = {}
        self.transport = make_transport(self.cfg, log=self._log)
        return self.start()

    def stderr_tail(self) -> str:
        """最近输出(stdio 是 stderr,HTTP 是最近一次网络错误)。"""
        return self.transport.tail()

    def caps_line(self) -> str:
        """对端在握手里声明了什么能力,以及学长实际接了哪几项。

        这一句存在的理由是**不假装**:对端说它提供 resources/prompts,而学长只实现了
        tools —— 不写清的话,用户在面板上看到"连上了、N 个工具",会以为那台服务器的
        其余能力也能用。反过来对端什么都没声明时也照实说,不替它编一份能力表。
        """
        declared = sorted(self.server_caps or {})
        if not declared:
            return "对端没在握手里声明能力(学长按 tools 用)"
        unused = [k for k in declared if k not in CLIENT_PRIMITIVES]
        line = ("对端声明: " + "、".join(declared)
                + ";学长只接了 " + "、".join(CLIENT_PRIMITIVES))
        if unused:
            line += "(" + "、".join(unused) + " 没实现,不会去调)"
        return line

    # ------------------------------------------------------------- 协议

    def _notify(self, method: str, params: dict) -> None:
        self.transport.notify({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, timeout: float) -> dict | None:
        with self._lock:
            if self._closed:
                raise McpError(f"服务器 {self.cfg.sid} 已关闭")
            self._next_id += 1
            msg = self.transport.request(
                {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
                timeout)
        if msg is None:
            return None
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(str(err.get("message") or err))
        return msg.get("result")

    def list_tools(self) -> list[dict]:
        return self.tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用远端工具,返回可直接回填给模型的文本。

        **通道层**失败(McpTransportError:进程死/管道断)→ 自动重连**一次**再试,
        两条结果都如实标注;服务器自己返回的协议错误(McpError)不重连 ——
        那是"它活着但拒绝了这个调用",重连解决不了。
        """
        try:
            result = self._request("tools/call",
                                   {"name": name, "arguments": arguments or {}},
                                   timeout=self.cfg.call_timeout)
        except McpError as e:
            if not (isinstance(e, McpTransportError) or not self.alive):
                return f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e}"
            self._warn(f"MCP 服务器 {self.cfg.sid} 通道异常,自动重连一次…")
            if not self.restart():
                return (f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e}"
                        f"(检测到连接断开,已自动重连但仍连不上: {self.error})")
            try:
                result = self._request("tools/call",
                                       {"name": name, "arguments": arguments or {}},
                                       timeout=self.cfg.call_timeout)
            except Exception as e2:
                return (f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e2}"
                        "(重连已成功,但调用仍失败 —— 可能服务器重启后状态变了)")
            return format_call_result(result) + "\n(检测到连接断开,已自动重连后重试成功)"
        except Exception as e:
            self._warn(f"MCP 服务器 {self.cfg.sid} 通道异常"
                       f"({type(e).__name__}),自动重连一次…")
            if not self.restart():
                return (f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e}"
                        f"(检测到连接断开,已自动重连但仍连不上: {self.error})")
            try:
                result = self._request("tools/call",
                                       {"name": name, "arguments": arguments or {}},
                                       timeout=self.cfg.call_timeout)
            except Exception as e2:
                return (f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e2}"
                        "(重连已成功,但调用仍失败 —— 可能服务器重启后状态变了)")
            return format_call_result(result) + "\n(检测到连接断开,已自动重连后重试成功)"
        return format_call_result(result)

    # ------------------------------------------------------------- 日志

    def _warn(self, text: str) -> None:
        if callable(self._log):
            try:
                self._log(text)
            except Exception:
                pass


def format_call_result(result) -> str:
    """把 tools/call 的 result 拍平成文本(content[].text 拼接)。"""
    if result is None:
        return "(MCP 工具无返回内容)"
    if not isinstance(result, dict):
        return str(result)
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        elif item.get("type") == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or res.get("uri") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    text = "\n".join(p for p in parts if p != "")
    if not text and result.get("content") is not None:
        text = "(工具返回空结果)"
    if not text:
        text = json.dumps(result, ensure_ascii=False)[:2000]
    if result.get("isError"):
        return f"错误: {text}"
    return text


def tool_name_for(sid: str, remote: str, taken: set[str]) -> str:
    """生成合法且唯一的本地工具名:OpenAI 只接受 [A-Za-z0-9_-],长度 <= 64。"""
    base = re.sub(r"[^0-9A-Za-z_-]+", "_", f"mcp_{sid}_{remote}").strip("_")
    name = base[:TOOL_NAME_MAX] or "mcp_tool"
    n = 2
    while name in taken:
        suffix = f"_{n}"
        name = base[:TOOL_NAME_MAX - len(suffix)] + suffix
        n += 1
    taken.add(name)
    return name


# --------------------------------------------------------------------- 管理器

def summary_rows(config, manager=None) -> "list[dict]":
    """MCP 侧栏/体检共用的汇总行(P3,v0.9.99):**配置是唯一事实,活状态补上去**。

    原来这份逻辑只在 Bridge._mcp_summary(需要活的管理器),终端的 /health
    拿不到它 —— 体检那行永远写「没查成」。抽到这里:Bridge 传自己的 manager,
    CLI 传 agent.mcp(都没有就只按配置报),两处消费者同一份形状,不再各抄。
    """
    cfg_servers = [s for s in (getattr(config, "mcp_servers", None) or [])
                   if isinstance(s, dict)]
    if not cfg_servers:
        return []
    live: dict = {}
    if manager is not None:
        for c in manager.clients:
            live[str(c.cfg.sid)] = c
    out: "list[dict]" = []
    for s in cfg_servers:
        sid = str((s.get("id") or s.get("name") or "")).strip()
        c = live.get(sid)
        out.append({
            "id": sid,
            "label": str((c.cfg.label if c else "")
                         or s.get("description") or sid) or sid,
            "enabled": bool(s.get("enabled", True)),
            "transport": str(s.get("transport") or "stdio"),
            "connected": bool(c),
            "server": str((c.server_info or {}).get("name") or "") if c else "",
            "n_tools": len(c.list_tools()) if c else 0,
            # 对端声明的能力与学长实际接了哪几项(没连上就没这句可说)
            "caps": c.caps_line() if c else "",
        })
    return out


class McpManager:
    """管理所有 MCP 服务器,并产出可直接并入注册表的 Tool 列表。"""

    def __init__(self, servers: list[McpServerConfig], log=None):
        self.servers = servers
        self.clients: list[McpClient] = []
        self._log = log

    def connect_all(self) -> None:
        for s in self.servers:
            if not s.enabled:
                continue
            client = McpClient(s, log=self._log)
            if client.start():
                self.clients.append(client)

    def build_tools(self) -> list[Tool]:
        taken: set[str] = set()
        out: list[Tool] = []
        for client in self.clients:
            for spec in client.list_tools():
                remote = str(spec.get("name"))
                name = tool_name_for(client.cfg.sid, remote, taken)
                out.append(Tool(
                    name=name,
                    description=self._describe(client, spec),
                    parameters=_input_schema(spec),
                    handler=_make_handler(client, remote),
                    read_only=client.cfg.read_only,
                    trust="local" if client.cfg.trusted else "external",
                ))
        return out

    def tools_summary(self) -> list[dict]:
        return [
            {"id": c.cfg.sid, "label": c.cfg.label, "n_tools": len(c.list_tools()),
             "server": (c.server_info or {}).get("name", ""),
             "caps": c.caps_line()}
            for c in self.clients
        ]

    def close_all(self) -> None:
        for c in self.clients:
            c.close()
        self.clients = []

    @staticmethod
    def _describe(client: McpClient, spec: dict) -> str:
        desc = str(spec.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 400:
            desc = desc[:400] + "…"
        head = f"[MCP:{client.cfg.label}] {spec.get('name')}"
        return f"{head} —— {desc}" if desc else head


def _input_schema(spec: dict) -> dict:
    schema = spec.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    schema.setdefault("properties", {})
    return schema


def _make_handler(client: McpClient, remote: str):
    def handler(ctx, **kwargs) -> str:
        return client.call_tool(remote, kwargs)
    return handler


def _version() -> str:
    try:
        from minicode import __version__
        return __version__
    except Exception:
        return "0"


def connect_servers(servers: list[McpServerConfig], log=None) -> McpManager:
    """按配置连接全部启用的服务器(失败的跳过),返回管理器。"""
    mgr = McpManager(servers, log=log)
    mgr.connect_all()
    return mgr


# ---------------------------------------------------------------- 管理面(v0.9.61)
#
# 「对话里自助加/删 MCP 服务器」的三块拼图,分别住在这三处:
# - probe_server(这里):真连一次验证配置,连不上就不许落盘;
# - 钩子 set_admin_hook(这里):GUI 桥(create_app)安装 —— 它才知道自己的
#   config_path、_mcp 管理器和 _mcp_tools 列表在哪;CLI 没装钩子,admin 如实
#   说"配置已写入,重启后生效";
# - mcp 工具(catalog_web):模型的手,把上面两块拼起来。

_ADMIN_HOOK = None            # (action, name, raw) -> str;由 create_app 安装


def set_admin_hook(fn) -> None:
    global _ADMIN_HOOK
    _ADMIN_HOOK = fn


# ------------------------------------------------------------ 预设模板库(v0.9.70)
#
# 路线图 #3 的最后半条:v0.9.61 让同学能在对话里自助加服务器,但「从零填
# JSON」仍是门槛。这张表是**预设的引用**,不是第二套传输实现 —— add 仍走
# 同一条探测闸(probe_server 真连一次),连不上照样不落盘。三条硬规矩:
# ①只放公开、免费、官方/通用的服务器,绝不内置任何密钥;
# ②needs 写清本机缺什么(npx 要 Node.js,uvx 要 uv),加不上时原话说给用户;
# ③占位符用 __名字__ 形式,raw 只许提供占位符的值与可选 name,**其余字段
# (command/args/env/headers/read_only/trusted)一律以模板为准** —— raw 能改
# command,预设就成了任意命令执行口子。

MCP_PRESETS: list[dict] = [
    {
        "id": "everything",
        "label": "试水演示(everything)",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "needs": "本机已装 Node.js(npx 命令可用)",
        "read_only": False,
        "trusted": False,
        "description": "官方演示服务器:回显、打印、采样等一堆无害小工具,第一次玩 MCP 拿它试手",
    },
    {
        "id": "sequential-thinking",
        "label": "分步思考(sequential-thinking)",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "needs": "本机已装 Node.js(npx 命令可用)",
        "read_only": True,
        "trusted": False,
        "description": "给模型一块「把复杂问题拆成小步」的草稿板,纯思考、无副作用",
    },
    {
        "id": "filesystem",
        "label": "本地文件夹(filesystem)",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "__PATH__"],
        "placeholder": {"name": "path", "hint": "要开放的文件夹路径(必须是已存在的目录)",
                        "must_be_dir": True},
        "needs": "本机已装 Node.js(npx);加的时候要给一个已存在的文件夹路径",
        "read_only": False,
        "trusted": False,
        "description": "让学长读写工作区之外的某个文件夹(比如课件盘)—— 路径由你定,随时 remove",
    },
    {
        "id": "time",
        "label": "时间与时区(time)",
        "command": "uvx",
        "args": ["mcp-server-time"],
        "needs": "本机已装 uv(uvx 命令可用;Python 官方工具链)",
        "read_only": True,
        "trusted": False,
        "description": "查任意时区的当前时间与互相换算 —— 跨时区约时间/选课时有用",
    },
    {
        "id": "fetch",
        "label": "网页抓取(fetch)",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "needs": "本机已装 uv(uvx 命令可用)",
        "read_only": True,
        "trusted": False,
        "description": "把网页拉成 Markdown 给模型读;学长自带 web_crawl,这个是备用抓手",
    },
]


def find_preset(preset_id: str) -> dict | None:
    text = str(preset_id or "").strip().lower()
    for p in MCP_PRESETS:
        if p["id"].lower() == text:
            return p
    return None


def build_preset_config(preset_id: str,
                        raw: dict | None) -> tuple[dict | None, str]:
    """preset + 用户参数 → 一份完整服务器配置;不合法时返回 (None, 说明)。

    raw 里只有两个键有资格进来:name(覆盖服务器标识)与占位符的值
    (如 filesystem 的 path);其余字段一律以模板为准。占位符要求的路径
    必须真实存在(目录类必须 is_dir)—— 不猜、不替用户建目录。
    """
    p = find_preset(preset_id)
    if p is None:
        known = ", ".join(x["id"] for x in MCP_PRESETS)
        return None, (f"错误: 没有叫「{preset_id}」的预设。可用的:{known}"
                      "(action=list_presets 看每条的说明与本机要求)。")
    raw = raw if isinstance(raw, dict) else {}
    cfg: dict = {
        "id": str(raw.get("name") or p["id"]).strip() or p["id"],
        "command": p["command"],
        "args": list(p["args"]),
        "transport": "stdio",
        "enabled": True,
        "read_only": bool(p.get("read_only")),
        "trusted": bool(p.get("trusted")),
        "description": str(p.get("description") or ""),
    }
    ph = p.get("placeholder")
    if ph:
        val = str(raw.get(ph["name"]) or "").strip().strip('"').strip("'")
        if not val:
            return None, (f"错误: 预设「{p['id']}」需要 {ph['name']} 参数 —— "
                          f"{ph['hint']}。加之前先问用户要。")
        from pathlib import Path as _Path
        if ph.get("must_be_dir") and not _Path(val).is_dir():
            return None, (f"错误: {ph['name']} 指的不是已存在的文件夹:{val}"
                          " —— 不猜、不替你建,确认路径后再加一次。")
        token = f"__{(ph['name'] or '').upper()}__"
        if token not in cfg["args"]:
            return None, f"错误: 预设「{p['id']}」的模板里没有占位符 {token}(模板写错了)。"
        cfg["args"] = [val if a == token else a for a in cfg["args"]]
    return cfg, ""


def probe_server(raw: dict, log=None) -> dict:
    """把一份**候选**服务器配置真连一次:握手 + 拉工具清单,立刻关掉。

    这是 mcp 工具 add/enable 的准入闸:连不上的配置绝不静默写进 config.json
    (写进去的表现是"每次启动失败一条 + 工具莫名缺失",而用户只会在几周后
    偶然发现)。返回 {ok, error, n_tools, server};连接对象用完即弃,不留进程。
    """
    try:
        cfg_list = parse_servers([raw])
    except Exception as e:
        return {"ok": False, "error": f"配置不合法: {type(e).__name__}: {e}",
                "n_tools": 0, "server": ""}
    if not cfg_list:
        return {"ok": False, "error": "配置不合法(缺 command/url 等必要字段)",
                "n_tools": 0, "server": ""}
    client = McpClient(cfg_list[0], log=log)
    ok = client.start()
    out = {"ok": ok, "error": "" if ok else client.error,
           "n_tools": len(client.tools) if ok else 0,
           "server": str((client.server_info or {}).get("name") or ""),
           "caps": client.caps_line() if ok else ""}
    client.close()
    return out


def admin(action: str, name: str = "", raw: dict | None = None) -> str:
    """mcp 工具的落盘与重载,全部委托给环境钩子;没钩子就照实说。"""
    if _ADMIN_HOOK is None:
        return ("错误: 当前环境不支持在对话里管理 MCP 服务器(CLI 请直接编辑 "
                "config.json 的 mcp_servers 数组,改完重启学长)。")
    try:
        return _ADMIN_HOOK(action, name, raw)
    except Exception as e:
        return f"错误: MCP 管理操作失败: {type(e).__name__}: {e}"


def admin_tool(ctx, action: str = "list", name: str = "", raw=None) -> str:
    """mcp 工具处理器:list/list_presets 本地读(到处都能用);增删改委托 admin(钩子)。"""
    action = (action or "list").strip().lower()
    if action == "list":
        servers = getattr(ctx.config, "mcp_servers", None) or []
        servers = [s for s in servers if isinstance(s, dict)]
        if not servers:
            return ("还没有配置任何 MCP 服务器。用 action=list_presets 看预设,"
                    "然后 action=add 加一个(preset=<id>,如 filesystem 还要给"
                    " path);或者手写配置:本地进程给 name+command(+args),"
                    "远程给 name+transport(http/sse)+url;鉴权 headers 里写"
                    "「Authorization: ${MY_TOKEN}」—— 密钥走环境变量,不落 "
                    "config.json。")
        lines = [f"- {s.get('id') or s.get('name')}"
                 f"({'启用' if s.get('enabled', True) else '停用'},"
                 f"{s.get('transport') or 'stdio'})" for s in servers]
        return ("已配置的 MCP 服务器:\n" + "\n".join(lines)
                + "\n(当前挂着的工具与增删改,见 GUI;CLI 改 config.json 后重启生效)")
    if action == "list_presets":
        lines = []
        for p in MCP_PRESETS:
            ph = p.get("placeholder")
            extra = (f";加的时候要给 {ph['name']}({ph['hint']})" if ph else "")
            lines.append(f"- {p['id']} —— {p['description']}"
                         f"(需要:{p['needs']}{extra})")
        return ("可用的 MCP 预设模板(用 action=add 加,preset=<id>;命令与参数"
                "以模板为准,加之前照样真连一次探测,连不上不写入):\n"
                + "\n".join(lines))
    if action == "add":
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return "错误: raw 必须是 JSON 对象(服务器配置)。"
        raw = raw if isinstance(raw, dict) else None
        preset_id = str((raw or {}).get("preset") or "").strip()
        if preset_id:
            cfg, err = build_preset_config(preset_id, raw)
            if cfg is None:
                return err
            raw = cfg
        return admin("add", name=str((raw or {}).get("name")
                                     or (raw or {}).get("id") or ""),
                     raw=raw if isinstance(raw, dict) else None)
    return admin(action, name=name)
