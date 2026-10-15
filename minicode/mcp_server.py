"""MCP 服务器模式(`--mcp-serve`):把本项目的**校园只读能力**接给外部 MCP 客户端。

定位:让 Claude Code / Cursor / Qoder 一类 MCP 客户端直接调 `library_search`、
`campus_calendar`、`gpa_calc`、`search_knowledge` 这些已有的校园能力 —— 协议侧
逐行 stdio 的编解码客户端侧已经写全(`minicode/mcp.py`),这里补的是同一形状的
另一半;工具是数据(`tools/base.py` 的 `Tool`),handler 与 CLI/GUI 调的是同一只,
不写第二套实现。

五条先讲清的边界,它们决定了这个模块为什么长这样:

1. **只做 stdio,一个进程一个客户端。** 不开端口、不要证书、不做 http/sse、
   不做并发多客户端 —— 一开 http 就必须同时回答鉴权、多客户端并发、工作区归属
   三个问题,第一版一个都答不上来。
2. **白名单显式列举,绝不用 `Tool.read_only` 过滤。** 那个标志的语义是"执行前
   不需要用户确认",不是"读不到敏感数据" —— `read_file`/`glob`/`grep` 都是
   `read_only=True`,而工作区根下就有含内置 Key 的 `config.local.json`。
3. **外部来的 `tools/call` 走同一道权限门**(`PermissionGate.check`),ASK 一律
   拒绝:即使 `permission_mode` 是 `full`,越界路径、凭据文件、`.git` 写入、
   外部内容升格、项目 ask 规则、规划锁仍会返回 ASK,而外部客户端没有"本机用户
   当场点的那一次头"这条路径 —— 把它当"已授权"就是凭空造一个用户。
   **不做例外档**(没有 `--mcp-yolo`):自动化不是绕过确认的第二条路。
4. **stdout 只走协议报文。** 人读的话、横幅、诊断一律不许往 stdout 打 —— 契约
   同 `headless.py`:调用方按行 `json.loads`,任何一个多余字符都会让整场会话错位。
5. **审计只记"被拒的那一次"**,复用 `provenance.log_escalation()`(那本账的定位
   是"只记安全门开口的动作,量极小"),并带上本次 `initialize` 自述的客户端标签。
   `clientInfo` 是**自述标签,不是身份**:stdio 下一进程一客户端,无凭据无校验,
   只能进日志,不能进判定。

**不**做:http/sse、`prompts`/`sampling`/`elicitation`、服务端发起的通知/进度、
多客户端、远程鉴权、不"顺手放开一点"白名单、不连出站 MCP、不建 LLM/Agent/子代理
(因此也不加载 AGENTS.md —— 它只进系统提示词,而这里没有系统提示词)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from minicode import headless, provenance
from minicode.config import Config
from minicode.permission import ALLOW, PermissionGate
from minicode.sandbox import SandboxState, policy_from_raw
from minicode.tools.base import Tool, ToolContext
from minicode.tools.registry import build_default_registry

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "yinlu-xuezhang"
SERVE_FLAG = "--mcp-serve"

# 对外暴露的只读工具。改这里之前先读模块开头第 2 条。
SERVE_TOOLS: frozenset[str] = frozenset({
    "library_search",           # 图书馆 133 万条离线馆藏
    "library_guide",            # 入馆教学 / 中图法→楼层
    "campus_calendar",          # 校历(放假/考试时间,纯本地数据)
    "gpa_calc",                 # 绩点/加权平均(纯本地计算)
    "search_knowledge",         # 校园语料(带来源与可信度)
    "campus_notices",           # 校园通知(现拉官网,端点由程序写死)
    "get_weather",              # 天气(Open-Meteo,URL 由程序拼死)
    "map_find_place",
    "map_show_list",
    "map_highlight_building",
    "map_drop_pin",
    "map_clear",
})

# 明确排除(留在这里是为了让人一眼看到"为什么没有它"),测试会断言它们不在白名单里。
# 不在本表、也不在 SERVE_TOOLS 里的工具同样进不来 —— 白名单是唯一入口。
EXCLUDED_TOOLS: dict[str, str] = {
    "read_file": "能读工作区任意文件,包括内含校园 Key 的 config.local.json",
    "glob": "同 read_file,可枚举工作区文件",
    "grep": "同 read_file,可在工作区全文搜索",
    "repo_outline": "会把工作区的目录结构与源码顶层符号摊给外部客户端",
    "repo_wiki": "会把工作区的架构全景与目录结构摊给外部客户端",
    "diagnostics": "在服务器上执行工作区里的检查器并泄漏源码诊断信息",
    "test_runner": "在服务器上执行工作区测试用例并泄漏测试输出与错误堆栈",
    "symbol_search": "在服务器上扫描工作区符号结构并泄漏源码符号与定义",
    "find_references": "在服务器上分析工作区符号引用并泄漏源码调用链与拓扑",
    "rename_symbol": "在服务器上跨文件重命名与修改源码文件 —— 外部客户端绝不能远程批量改写代码",
    "call_hierarchy": "在服务器上分析工作区符号调用拓扑并泄漏内部依赖链",
    "symbol_definition": "在服务器上提取符号定义并泄漏源码完整实现代码",
    "code_outline": "在服务器上提取源码结构大纲与类成员树并泄漏代码架构与实现细节",
    "write_file": "写文件 —— 外部客户端没有人能点头,等于无条件远程写盘",
    "edit_file": "改文件,同上",
    "apply_patch": "整包写盘(多文件),同上",
    "bash": "执行命令 —— 等于把本机交出去",
    "bash_status": "列的是本机跑过什么命令、输出了什么,是「本机在跑什么」的痕迹",
    "bash_kill": "终止本机上的进程",
    # v0.9.146:喂输入不终止什么,但能让本机跑着的那条命令往下走 ——
    # 后果与那条命令本身同级,而外部客户端没有人能点头。
    "bash_input": "能给本机上跑着的后台命令喂输入(等于替所有者说 y)",
    "py_script": "能在本机执行任意代码",
    "task": "子代理会携带 implement 类写工具,绕过白名单",
    "mcp": "改的是本机 MCP 配置并重连外部服务 —— 让外部客户端往本机写配置、拉起任意进程",
    "mcp_resource": "读 MCP 服务器的数据并带回客户端,资源不该由外部触发",
    "mcp_prompt": "取回的是本机配的那台服务器写的一段指示 —— 外部客户端不该能替"
                  "所有者决定学长把什么当输入,而且 prompt 里常带要执行的步骤",
    "memory": "读写所有者自己的长期记忆",
    "search_history": "翻的是所有者本人的全部历史会话留档(存本机 sessions/)",
    "scores": "读的是所有者本人的成绩单存档,还能清空它",
    "mood": "情绪日记是所有者本人的记录,还能整份导出",
    "reminder": "会读写本机的提醒清单文件",
    "schedule": "会读写本机的课表文件",
    "calendar_edit": "往本机知识库校历文件里写数据",
    "desktop_notify": "开的是本机桌面通知与托盘开关,还管开机自启",
    "kb_suggest": "会往本机的候选知识文件写数据",
    "export_ics": "把课表/待办/校历写成文件落进本机工作区",
    "export_trace": "把会话原文与每一次工具调用导成可回放的 JSON 落在本机",
    "export_feedback": "把本机的体检表、缺口账与运行状态打包成文件",
    "web_crawl": "SSRF:可让服务端请求内网任意地址;且会写 crawled/ 与 kb/",
    "web_search": "第一版不替外部客户端做通用联网检索",
    "browser_open": "能访内网地址,且 launch 出的实例会留在本机",
    "browser_eval": "等于在本机浏览器里执行外部给的 JS",
    "browser_snapshot": "会列出本机浏览器里的页面内容",
    "browser_click": "驱动本机浏览器点击",
    "browser_fill": "驱动本机浏览器填表单",
    "browser_screenshot": "往本机工作区写文件",
    "browser_close": "同上,浏览器能力整体不进白名单",
    "browser_status": "不暴露本机的浏览器信息",
    "page_parse": "与 browser_* 同一族:要在本机起一个浏览器才读得到页面",
    "ocr_image": "收绝对路径、把本机任意图片读成文字带进对话",
    "render_chart": "往本机工作区 charts/ 写文件",
    "generate_image": "会花本机的 Key 额度,或往本机工作区写 PNG",
    "use_skill": "技能正文是本机整理的经验库,不在对外问答范围里",
    "plan": "写本机工作区文件",
    "ask_user": "这张卡要**本机的人坐在屏幕前点** —— 外部客户端没有人能回答",
    "permissions": "读的是本机这个项目批过哪些免确认动作(含路径与命令模式)",
    "permissions_trust": "批准项目白名单 = 替本机主人交出这台机器的一段授权",
    "hooks": "列的是本机 config.json 里挂着什么命令,preview 还会交出一次性凭据",
    "hooks_apply": "往本机用户配置里写一条会随每次工具调用执行的命令",
    "worktree": "在本机仓库旁边新建目录并跑 `git worktree add`",
}


def _version() -> str:
    from minicode import __version__
    return __version__


def pop_mcp_serve_flag(argv: list[str]) -> tuple[bool, list[str]]:
    """摘出 `--mcp-serve`,其余参数原样返回(给一次性任务/工作区解析用)。

    只认这一种写法:它不是"值型"旗标,`--mcp-serve=1` 之类一律当普通 token 留着,
    免得一个手滑的写法被当成"要起服务端"。
    """
    serve = False
    rest: list[str] = []
    for item in argv:
        if item == SERVE_FLAG:
            serve = True
            continue
        rest.append(item)
    return serve, rest


class _NoUI:
    """服务端没有界面:吞掉渲染,只保证"工具调了不炸"。"""

    def __getattr__(self, _name: str):
        return lambda *a, **k: None


def _spec(tool: Tool) -> dict:
    """`Tool` → MCP `tools/list` 的那一条。`inputSchema` 直接用工具的 JSON Schema。"""
    return {"name": tool.name, "description": tool.description,
            "inputSchema": tool.parameters}


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": code, "message": message}}


def _tool_error(text: str) -> dict:
    """工具级失败(不是协议错误):`isError` + 一段人话。"""
    return {"isError": True, "content": [{"type": "text", "text": text}]}


def _client_label(msg: dict) -> str:
    info = (msg.get("params") or {}).get("clientInfo") or {}
    name = str(info.get("name") or "").strip()
    ver = str(info.get("version") or "").strip()
    return f"{name} {ver}".strip() if name else ""


class _Server:
    """一场 stdio 会话的状态:注册表 + 权限门 + `ToolContext` + 客户端标签。"""

    def __init__(self, config: Config, workspace: Path):
        self.config = config
        self.workspace = workspace
        self.registry = build_default_registry()
        self.by_name = {t.name: t for t in self.registry}
        self.ctx = ToolContext(workspace=workspace, config=config)
        self.ctx.sandbox = SandboxState(policy_from_raw(config.sandbox), workspace)
        self.ctx.ui = _NoUI()
        self.gate = PermissionGate(config, sandbox=self.ctx.sandbox,
                                   workspace=workspace,
                                   tool_names=set(self.by_name))
        self.client = ""

    # ------------------------------------------------------------ 分发

    def handle(self, msg: dict) -> "dict | None":
        """一条入站报文 → 一条响应报文;通知返回 None(不产生响应)。"""
        rid = msg.get("id")
        if rid is None:
            return None                     # notifications/initialized 等:无需响应
        method = msg.get("method")
        if method == "initialize":
            self.client = _client_label(msg)
            return _ok(rid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()},
            })
        if method == "tools/list":
            names = sorted(n for n in SERVE_TOOLS if n in self.by_name)
            return _ok(rid, {"tools": [_spec(self.by_name[n]) for n in names]})
        if method == "tools/call":
            return self._call(rid, msg)
        return _err(rid, -32601, f"未知方法 {method}")

    def _call(self, rid: Any, msg: dict) -> dict:
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        name = str(params.get("name") or "")
        args = params.get("arguments")
        args = args if isinstance(args, dict) else {}
        tool = self.by_name.get(name)
        if tool is None:
            # 名字根本不存在:这是**协议层**的错(未知工具),与"存在但不对外"分开。
            return _err(rid, -32602, f"未知工具 {name}")
        if name not in SERVE_TOOLS:
            self._audit(name, args, EXCLUDED_TOOLS.get(name, "不在对外只读名单里"),
                        "not_exposed")
            return _ok(rid, _tool_error(
                f"这一步没做:`{name}` 不在对外服务的只读名单里(它可能改动这台机器"
                "或读私有数据)。这一步需要人确认,而当前无人在场,所以一律不执行。"))
        self.gate.new_turn()
        verdict = self.gate.check(tool, args)
        if verdict != ALLOW:
            why = (self.gate.ask_note or self.gate.reject_reason
                   or "这一步需要人确认")
            self._audit(name, args, why, self.gate.ask_kind or "reject")
            return _ok(rid, _tool_error(
                f"这一步没做:{why} —— 外部客户端没有人能点头,一律不执行。"))
        try:
            text = tool.handler(self.ctx, **args)
        except Exception as e:
            return _ok(rid, _tool_error(f"工具执行出错:{type(e).__name__}: {e}"))
        return _ok(rid, {"content": [{"type": "text", "text": str(text)}]})

    def _audit(self, tool_name: str, args: dict, why: str, gate_name: str) -> None:
        """被拒的那一次留一行痕(带客户端自述标签);写失败静默。"""
        provenance.log_escalation(gate_name, tool_name, args, why,
                                  choice="no", client=self.client)


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def serve(config: Config, workspace: Path) -> int:
    """stdio 主循环:一行一条 JSON-RPC,读一行、判一条、回一条。返回退出码。

    stdin 关闭(客户端退出)就是正常收场,退出码 0。坏 JSON 行忽略并继续 ——
    协议是逐行的,一行坏不该拖垮整场会话。
    """
    headless.ensure_utf8_stdout()
    server = _Server(config, workspace)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(msg, dict):
            continue
        resp = server.handle(msg)
        if resp is not None:
            _write(resp)
    return 0
