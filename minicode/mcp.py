"""MCP(Model Context Protocol)客户端:把外部 MCP 服务器的工具挂进本机工具注册表。

实现要点(对齐星瑶 Y-Code 的做法,按本项目的极简风格重写):
- 传输:stdio,一行一个 JSON-RPC 2.0 报文(服务器日志走 stderr,不混进协议流);
- 生命周期:进程级长连接,启动时按配置**惰性宽容**连接——连不上只记日志跳过,
  绝不让一个坏配置把整个应用带崩;
- 握手:initialize → notifications/initialized → tools/list;
- 调用:tools/call,结果按 content[].text 拼接,isError 前置「错误:」;
- 超时:启动 10s、调用 60s(可被服务器配置覆盖);
- 权限:MCP 工具默认**不是只读**——会走确认门;受信任服务器可在配置里标
  read_only: true 免确认。

配置文件(config.json)示例::

    "mcp_servers": [
      {"id": "filesystem", "command": "npx",
       "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"],
       "enabled": true, "read_only": false}
    ]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from minicode.tools.base import Tool

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "yinlu-xuezhang"

DEFAULT_START_TIMEOUT = 10.0
DEFAULT_CALL_TIMEOUT = 60.0
STDERR_KEEP_LINES = 20
TOOL_NAME_MAX = 60


# --------------------------------------------------------------------- 配置

@dataclass
class McpServerConfig:
    """一个 MCP 服务器的启动配置。"""

    sid: str
    command: str
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
        if not command:
            continue
        sid = str(item.get("id") or item.get("name") or f"server{i}").strip()
        sid = re.sub(r"[^0-9A-Za-z_-]+", "_", sid).strip("_") or f"server{i}"
        if sid in seen:
            continue
        seen.add(sid)
        env_raw = item.get("env")
        env = ({str(k): str(v) for k, v in env_raw.items()}
               if isinstance(env_raw, dict) else {})
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
        ))
    return out


def servers_to_raw(servers: list[McpServerConfig]) -> list[dict]:
    """回写 config.json 用的序列化(env 保留,空值不写)。"""
    out = []
    for s in servers:
        item: dict = {"id": s.sid, "command": s.command}
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


# --------------------------------------------------------------------- 客户端

class McpError(Exception):
    """MCP 协议层或进程层错误。"""


class McpClient:
    """单个 MCP 服务器的 stdio 客户端(进程级长连接)。"""

    def __init__(self, cfg: McpServerConfig, log=None):
        self.cfg = cfg
        self._log = log
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()          # 串行化请求,避免 id 交叉
        self._cv = threading.Condition()
        self._inbox: dict[int, dict] = {}      # id -> 响应
        self._next_id = 0
        self._stderr: deque[str] = deque(maxlen=STDERR_KEEP_LINES)
        self._closed = False
        self.tools: list[dict] = []            # tools/list 原始结果
        self.server_info: dict = {}
        self.error: str = ""

    # ----------------------------------------------------------- 生命周期

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """拉起进程并完成握手 + 拉取工具清单;失败返回 False 且不抛异常。"""
        try:
            self._spawn()
            info = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": _version()},
            }, timeout=self.cfg.start_timeout)
            self.server_info = (info or {}).get("serverInfo", {}) if isinstance(info, dict) else {}
            self._notify("notifications/initialized", {})
            listed = self._request("tools/list", {}, timeout=self.cfg.start_timeout)
            tools = (listed or {}).get("tools", []) if isinstance(listed, dict) else []
            self.tools = [t for t in tools if isinstance(t, dict) and t.get("name")]
            return True
        except Exception as e:                 # 配置错/命令不存在/握手超时
            self.error = f"{type(e).__name__}: {e}"
            self._warn(f"MCP 服务器 {self.cfg.sid} 连接失败: {self.error}")
            self.close()
            return False

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
            with self._cv:
                self._inbox[mid] = msg
                self._cv.notify_all()

    def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
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
        finally:
            self._proc = None
            with self._cv:
                self._cv.notify_all()

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    # ------------------------------------------------------------- 协议

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpError(f"服务器 {self.cfg.sid} 未在运行")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except OSError as e:
            raise McpError(f"写入 {self.cfg.sid} 失败: {e}")

    def _request(self, method: str, params: dict, timeout: float) -> dict | None:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            with self._cv:
                while rid not in self._inbox:
                    if self._closed:
                        raise McpError(f"服务器 {self.cfg.sid} 已关闭")
                    remain = deadline - time.monotonic()
                    if remain <= 0:
                        tail = self.stderr_tail()
                        hint = f"; stderr: {tail[-300:]}" if tail else ""
                        raise McpError(f"{method} 超时({timeout:g}s){hint}")
                    self._cv.wait(min(remain, 0.5))
                msg = self._inbox.pop(rid)
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(str(err.get("message") or err))
        return msg.get("result")

    def list_tools(self) -> list[dict]:
        return self.tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用远端工具,返回可直接回填给模型的文本。"""
        try:
            result = self._request("tools/call",
                                   {"name": name, "arguments": arguments or {}},
                                   timeout=self.cfg.call_timeout)
        except McpError as e:
            return f"错误: 调用 MCP 工具 {self.cfg.sid}/{name} 失败: {e}"
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
             "server": (c.server_info or {}).get("name", "")}
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
