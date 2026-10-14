"""工具基座:工具定义、运行上下文、路径安全。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from minicode.config import Config
from minicode.sandbox import INSIDE


class PathOutsideWorkspace(Exception):
    """工具试图访问工作区之外的路径。"""


class PathBlocked(Exception):
    """工具试图访问敏感路径(凭据/密钥目录),任何模式下都禁止。"""


@dataclass
class ToolContext:
    """工具运行时上下文:工作区根目录 + 全局配置 + 沙箱状态 + 界面通道。"""

    workspace: Path
    config: Config
    sandbox: Any | None = None   # minicode.sandbox.SandboxState,由 app_setup 注入
    ui: Any | None = None        # GuiUI/TerminalUI:工具可把图形指令推给界面(如校园地图)
    llm: Any | None = None       # LLMClient:记忆整理(dream)等内部工具需要一次性补全
    # 本会话的 Session 对象(v0.9.98,export_trace 用):只有主代理的 ctx 带着它;
    # 子代理的 ctx 由 dc_replace 派生、字段原样保留 —— 但 export_trace 已从子代理
    # 工具集摘掉(轨迹归主对话,子代理导出父会话是张冠李戴)。
    session: Any | None = None
    # 往主会话留一句话的入口(I,v0.9.154,`notify_parent` 那只工具用)。
    # **只有子代理的 ctx 上才不为 None** —— 由 `run_subagents.run_subagent` 在
    # `dc_replace` 那里**显式**写上去(父 ctx 上它是空的:主会话自己调那只工具
    # 会变成自言自语,所以那条路要在 handler 里如实说"你就是主会话")。
    # 值是主代理 `Agent.queue_note` 这个可调用对象,不是字符串 —— 收不收得下、
    # 带不带锁由主代理那一边说了算,这里只负责递。
    notify_parent: Any | None = None
    # 这一条会话 / 子代理自己的**递话盒** handle(v0.9.173,`session_message` 用):
    # 由 `Agent.__init__` 在开盒之后写上来 —— 那一只工具既要说得出现在能递给谁,
    # 也得认得出"列表里那一条就是你自己"。子代理的 ctx 是 `dc_replace` 派生的,
    # 这个值会在子 Agent 构造时被它自己的 handle 覆盖(与 `notify_parent`
    # **显式写、不吃继承**同一个道理)。
    mail_handle: str = ""
    # 子代理的**深度**(L,v0.9.155):主会话是 0,子代理是 1,孙辈是 2。
    # `run_subagent` 在派生 ctx 时 +1;`task` 那只工具据它决定还要不要往下派。
    # 开关默认关,所以这个数今天恒为 0 或 1 —— 它先在这里把路铺好。
    subagent_depth: int = 0
    # **这一批**的并行写占用表(L,v0.9.155)。父批给了就接着用(**跨层同一张**):
    # 嵌套时父批与子批若各建一张,同一个文件可以跨层同时被写而两边都不报错 ——
    # 那道程序闸就漏了(HANDOFF §6 63 ①)。主会话那份是 None,由第一批自己建。
    batch_locks: Any | None = None


Handler = Callable[..., str]  # 签名: handler(ctx, **kwargs) -> str


# OpenAI 协议允许 JSON Schema 的并集类型(`"type": ["array", "object"]`),
# 但按 pydantic 校验请求体的网关(LiteLLM 那一类)会把 `type` 当字符串字段读:
# 并集一来,整个请求体判成 `Invalid Field [failed to parse request body]`。
# 坏的不是那一只工具,是这一整轮对话 —— 而且每发一次请求坏一次,界面上只有一张红卡。
# 同学实测的形状正是「模型测试是通的、对话发不出去」:测试请求不带 tools。
_SCHEMA_TYPES = ("array", "object", "string", "integer", "number", "boolean")


def _pick_type(types: list) -> str:
    """并集里挑一个能表达这份数据的类型(顺序按"信息量大的优先",拿不准退回 string)。"""
    got = {str(t) for t in types}
    for cand in _SCHEMA_TYPES:
        if cand in got:
            return cand
    return "string"


def _flatten_schema_types(node: Any) -> Any:
    """把 schema 里所有并集 `type` 收成单类型;**没得改就原样返回同一个对象**。

    原样返回同一个对象是有意的:每轮请求都要过 56 只工具,能少一份拷贝就少一份,
    也保证 `Tool.parameters` 这份共享数据不会被这里改到(工具的 handler 读的还是原 schema)。
    """
    if isinstance(node, dict):
        out = None
        for key, val in node.items():
            if key == "type" and isinstance(val, list):
                out = dict(node)
                out["type"] = _pick_type(val)
                continue
            fixed = _flatten_schema_types(val)
            if fixed is not val:
                out = dict(node) if out is None else out
                out[key] = fixed
            elif out is not None:
                out[key] = val
        return out if out is not None else node
    if isinstance(node, list):
        items = [_flatten_schema_types(v) for v in node]
        return items if any(a is not b for a, b in zip(items, node)) else node
    return node


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema,类型必须是 object
    handler: Handler
    read_only: bool = False
    # 这只工具的 handler 能不能和**同一条消息里的另一只**同时跑(§6 50(b) 那批并发)。
    # 与 `read_only` 是两回事:只读说的是"要不要弹框",这里说的是"能不能同跑"。
    # 默认 False —— 没逐条看过实现就不许进线程池(浏览器走 CDP 同步 API、ask_user
    # 的弹框发生在 handler 里,这两类进了池子都是**不报错**的坏法)。
    # 认这个旗标的只有 `agent._lane_ok` 一处,名单在 `registry.CONCURRENT_SAFE`。
    concurrent_safe: bool = False
    # 信任级:local 本机产生 / external 外部内容(结果要套溯源围栏,见 provenance.py)
    # / principal 用户意志。子代理工具沿用父工具的值。
    trust: str = "local"

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                # 出口处收一次并集类型:内置目录里不该有,但 MCP 工具的对端 schema
                # 不归我们管 —— 一只坏类型会把整个请求体连带着 56 只工具一起打死。
                "parameters": _flatten_schema_types(self.parameters),
            },
        }


def resolve_in_workspace(ctx: ToolContext, raw_path: str) -> Path:
    """把路径解析到工作区内;越界(包括指向外部的绝对路径)直接拒绝。"""
    p = Path(raw_path)
    if not p.is_absolute():
        p = ctx.workspace / p
    p = p.resolve()
    try:
        p.relative_to(ctx.workspace)
    except ValueError:
        raise PathOutsideWorkspace(
            f"路径超出工作区范围: {raw_path}(工作区: {ctx.workspace})。请使用相对工作区的路径。"
        )
    return p


def resolve_path(ctx: ToolContext, raw_path: str, *, for_write: bool = False) -> Path:
    """沙箱感知的路径解析:工作区内放行、工作区外看授权、敏感路径一律拒绝。

    没装沙箱(ctx.sandbox 为空)时退化为旧的硬围栏,保证既有调用行为不变。
    """
    state = getattr(ctx, "sandbox", None)
    if state is None:
        return resolve_in_workspace(ctx, raw_path)
    raw = (raw_path or "").strip() or "."

    if for_write:
        from minicode.sandbox import BLOCKED, SECRET, classify_for_write as classify
    else:
        from minicode.sandbox import BLOCKED, SECRET, classify_path as classify
    kind, p = classify(state.workspace, raw)
    if kind == BLOCKED:
        raise PathBlocked(
            f"路径属于敏感目录(凭据/密钥),始终禁止访问: {raw_path}"
        )
    # SECRET = 工作区**内**的凭据文件:权限门已经让用户确认过(或在 full 档前
    # 就拦下),到这一步不能再拿"不是 inside"当越界拒掉 —— 否则用户点了允许还是读不到。
    if kind in (INSIDE, SECRET):
        return p
    if not state.path_allowed(p):
        raise PathOutsideWorkspace(
            f"路径超出工作区范围: {raw_path}(工作区: {state.workspace})。"
            "访问工作区外需要用户在确认框里放行本次操作,"
            "或在 config.json 打开 sandbox.allow_outside_workspace。"
        )
    return p
