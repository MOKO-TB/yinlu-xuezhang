"""Agent 主循环:模型 ↔ 工具的回合内循环。

每回合:请求模型 → 流式输出 → 若有工具调用则逐个执行(过权限门)→
结果回填 → 再次请求,直到模型不再调用工具或步数达到上限。
变更类文件工具执行前自动快照,支持 /undo 逐步回滚;
上下文 token 估算超过阈值时自动把早期对话折叠成摘要。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from minicode.compaction import compact, estimate_tokens
from minicode.llm import LLMClient, ToolCall
from minicode.model_catalog import context_window_for
from minicode.permission import ALLOW, ASK, REJECT, PermissionGate
from minicode.session import Session
from minicode.tools.base import (
    PathBlocked, PathOutsideWorkspace, Tool, ToolContext, resolve_path,
)
from minicode.ui import TerminalUI

MAX_STEPS = 40  # 单回合默认工具调用轮数上限
SNAPSHOT_TOOLS = ("write_file", "edit_file")  # bash 的改动无法安全跟踪,不在回滚范围


class Agent:
    def __init__(self, llm: LLMClient, registry: list[Tool], session: Session,
                 gate: PermissionGate, ui: TerminalUI, ctx: ToolContext,
                 max_steps: int = MAX_STEPS):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.gate = gate
        self.ui = ui
        self.ctx = ctx
        self.max_steps = max_steps
        self._tool_map = {t.name: t for t in registry}
        self._undo_stack: list[dict] = []
        self._narration = ""       # 本轮模型写的说明文字:权限确认框里当"为什么要做"的兜底
        self.mcp = None            # app_setup 注入:McpManager,退出时关停

    def close(self) -> None:
        """释放外部资源(当前是 MCP 子进程);重复调用安全。"""
        mcp = getattr(self, "mcp", None)
        if mcp is not None:
            try:
                mcp.close_all()
            except Exception:
                pass
            self.mcp = None

    def run_turn(self, user_input: str, content=None) -> None:
        """跑一个回合。content 可选:带附件时的多模态消息体(list[dict])。"""
        self.gate.new_turn()
        self.session.add_user(user_input, content)

        # 一轮总时限:只圈"这一轮一共转了多少次",不圈单条命令(那是沙箱的
        # timeout_seconds)也不圈单次请求(那是 request_timeout_seconds)。
        # 到点只在两步之间收手 —— 正在执行的那一步不会被强杀。
        limit = max(0, int(getattr(self.ctx.config, "turn_time_limit_seconds", 0) or 0))
        deadline = time.monotonic() + limit if limit else None
        steps = 0
        for _ in range(self.max_steps):
            if deadline is not None and time.monotonic() > deadline:
                self.ui.turn_time_limited(limit, steps)
                return
            steps += 1
            self._maybe_compact()
            try:
                turn = self.llm.stream_chat(
                    self.session.messages,
                    self.registry,
                    on_text=self.ui.on_text_delta,
                    on_reasoning=self.ui.on_reasoning_delta,
                    on_retry=self.ui.on_retry,
                )
            except KeyboardInterrupt:
                self.ui.turn_interrupted()
                return
            except Exception as e:
                self.ui.llm_error(e)
                return

            self.ui.finish_stream()
            self.session.add_assistant(turn)
            self._record_usage(turn)
            self._bump_turn_tokens(turn)
            self._push_turn_stats(turn)
            self._narration = str(turn.text or "")   # 供本步工具确认框解释"为什么要做"

            if not turn.tool_calls:
                return

            for call in turn.tool_calls:
                result = self._execute(call)
                self.session.add_tool_result(call.id, call.name, result)
        else:
            self.ui.max_steps_reached(self.max_steps)

    def _record_usage(self, turn) -> None:
        """把本轮真实 token 用量记进本地统计(见 minicode/usage.py)。

        只在**有真实 usage** 时记账;服务端没返回 usage 就不记,绝不估算。
        记账失败静默忽略——统计功能绝不能拖垮对话主流程。
        """
        try:
            from minicode import usage
            cfg = self.ctx.config
            usage.record_usage(
                provider=cfg.provider.id if cfg.provider else "",
                model=cfg.model,
                usage=turn.usage,
            )
        except Exception:
            pass

    def _bump_turn_tokens(self, turn) -> None:
        """累计本任务的真实消耗(有服务端 usage 才累计),并推面板事件。

        agent.turn_tokens 供 session_metrics 用;turn_usage 事件给前端
        实时刷新任务面板(消耗/上下文占用),失败静默——绝不能影响对话。
        """
        u = turn.usage if isinstance(turn.usage, dict) else {}
        pt = u.get("prompt_tokens")
        ct = u.get("completion_tokens")
        if isinstance(pt, int) and pt >= 0 and isinstance(ct, int) and ct >= 0:
            self.turn_tokens = getattr(self, "turn_tokens", 0) + pt + ct
        try:
            from minicode.model_catalog import push_turn_usage
            push_turn_usage(self, self.session, self.ctx.config, turn)
        except Exception:
            pass

    def _push_turn_stats(self, turn) -> None:
        """给前端推本段输出的实测数据(耗时/每秒 token),纯展示,失败静默。

        elapsed_ms 由 LLM 屄记录(流式收包的真实耗时);tokens 优先
        turn.completion_tokens(_stream_once 已按 usage>估算兜底),再从
        usage 直取兜底(兼容不经过 _stream_once 的适配器)。
        TerminalUI 没有 push 属性,getattr 兜底(CLI 零影响)。
        """
        push = getattr(self.ui, "push", None)
        if not callable(push):
            return
        if not (isinstance(turn.elapsed_ms, int) and turn.elapsed_ms > 0):
            return
        seconds = turn.elapsed_ms / 1000.0
        tokens = turn.completion_tokens
        if not (isinstance(tokens, int) and tokens > 0):
            u = turn.usage if isinstance(turn.usage, dict) else {}
            maybe = u.get("completion_tokens")
            tokens = maybe if isinstance(maybe, int) and maybe > 0 else None
        tps = round(tokens / seconds, 1) if tokens else None
        try:
            push("turn_stats", elapsed_ms=turn.elapsed_ms,
                 tokens=tokens, tps=tps,
                 reasoning=bool(turn.reasoning))
        except Exception:
            pass

    # ------------------------------------------------------------ 回滚

    def undo(self) -> str:
        """撤销最近一次文件改动(后进先出)。"""
        if not self._undo_stack:
            return "没有可撤销的文件改动(bash 产生的改动不在跟踪范围)。"
        rec = self._undo_stack.pop()
        p: Path = rec["path"]
        if rec["data"] is None:
            p.unlink(missing_ok=True)
            return f"已撤销:删除新建的 {rec['display']}"
        p.write_bytes(rec["data"])
        return f"已撤销:{rec['display']} 已恢复为改动前的内容"

    # ------------------------------------------------------------ 压缩

    def compact_now(self) -> None:
        self._maybe_compact(force=True)

    def _maybe_compact(self, force: bool = False) -> bool:
        cfg = self.ctx.config
        # 与任务面板同一把尺子:优先用运行时探测到的真实窗口(见 model_probe),
        # 探测不到才退回 config.json 里的默认值。
        window = context_window_for(cfg.model, cfg.context_window)
        threshold = int(window * cfg.compact_at_ratio)
        if not force and estimate_tokens(self.session.messages) <= threshold:
            return False
        new_msgs, before = compact(self.session.messages, self.llm)
        if len(new_msgs) >= before:
            if force:
                self.ui.info("没有可压缩的早期对话。")
            return False
        self.session.replace_messages(new_msgs)
        self.ui.compacted(before, len(new_msgs))
        return True

    # ------------------------------------------------------------------

    def _execute(self, call: ToolCall) -> str:
        tool = self._tool_map.get(call.name)
        if tool is None:
            return (f"错误: 未找到工具 '{call.name}'。"
                    f"可用工具: {', '.join(self._tool_map)}")
        try:
            args = json.loads(call.arguments) if call.arguments.strip() else {}
        except json.JSONDecodeError as e:
            return f"错误: 工具参数不是有效 JSON({e})。原始参数: {call.arguments[:200]}"
        if not isinstance(args, dict):
            return "错误: 工具参数必须是 JSON 对象。"

        # reason 是给用户看的"这一步为什么做",不是工具参数:声明了才摘掉,避免
        # 误吃 MCP 工具自己同名的参数。
        reason = ""
        props = (tool.parameters or {}).get("properties") or {}
        if "reason" in props:
            reason = str(args.pop("reason", "") or "")

        decision = self.gate.check(tool, args)
        if decision == REJECT:
            self.ui.tool_rejected_hard(tool, args)
            return ("错误: 该操作被安全策略硬拒绝(可能造成不可恢复的破坏),"
                    "已阻止执行,请勿重试。")
        if decision == ASK:
            note = getattr(self.gate, "ask_note", "") or ""
            # 无升级警示时按原调用路径走(不传 note):既有三参数实现/测试代理
            # 不受影响,而且这是"未接触外部内容时行为与基线一致"的前提。
            if note:
                choice = self.ui.confirm_tool(tool, args, reason or self._narration, note)
                self._log_security_ask(tool, args, note, choice)
            else:
                choice = self.ui.confirm_tool(tool, args, reason or self._narration)
            if choice == "always":
                self.gate.approve_for_turn(tool.name)
            elif choice != "yes":
                return "用户拒绝了本次工具调用。请停止重试,询问用户希望如何处理。"
            # 用户放行后记住工作区外路径(本回合内有效)
            self.gate.approve_outside(tool, args)

        self._snapshot_before_mutation(tool, args)
        self.ui.tool_start(tool, args)
        try:
            result = tool.handler(self.ctx, **args)
        except TypeError as e:
            result = f"错误: 工具参数与 schema 不匹配: {e}"
        except Exception as e:
            result = f"工具执行出错: {type(e).__name__}: {e}"
        self.ui.tool_done(tool, result)
        result = self._truncate(result)
        # 外部内容围栏:在 _truncate **之后**包壳,否则长内容会把结束标记裁掉。
        # 收口只在这里,禁止各 handler 自行拼壳(方案 §8 决策 1)。
        if self._is_external_result(tool, args):
            from minicode import provenance
            result = provenance.wrap(tool.name, args, result)
            # 本回合接触过外部内容 —— 升级门(M3)据此对敏感动作强制确认
            note = getattr(self.gate, "note_external", None)
            if callable(note):
                note(tool.name, args)
        return result

    def _log_security_ask(self, tool: Tool, args: dict, note: str, choice: str) -> None:
        """安全门(凭据/升级)开口就留一行痕(方案 M6)。失败静默。"""
        try:
            from minicode import provenance
            gate = "credential" if ("密钥" in note or "凭据" in note) else "escalation"
            provenance.log_escalation(gate, tool.name, args, note, choice)
        except Exception:
            pass

    def _is_external_result(self, tool: Tool, args: dict) -> bool:
        """这次工具结果是否算"外部内容"(注入防御 M1 的两个特殊判定之一)。

        - 工具自身标了 trust=external(web_crawl / browser_* / search_knowledge /
          library_* / 全部 MCP 工具);
        - `bash` 的条件升格:命令命中沙箱已有的联网识别(curl/wget/ssh/git fetch…)
          时,输出可能就是网页内容,按 external 处理 —— 复用现成解析,不另写一套。

        `read_file` **不**升格:文件是本地字节,不做污点传播(方案 §7 局限 2),
        靠 M4 堵住 to_kb 入库这条最现实的"外部内容本地化"路径。
        """
        from minicode import provenance
        if provenance.is_external(getattr(tool, "trust", "local")):
            return True
        name = tool.name.rsplit("·", 1)[-1]
        if name == "bash":
            from minicode.sandbox import command_hits_network
            if command_hits_network(str(args.get("command", ""))):
                return True
        return False

    def _snapshot_before_mutation(self, tool: Tool, args: dict) -> None:
        if tool.name not in SNAPSHOT_TOOLS:
            return
        raw = str(args.get("path", ""))
        try:
            p = resolve_path(self.ctx, raw, for_write=True)
        except (PathOutsideWorkspace, PathBlocked):
            return
        try:
            data = p.read_bytes() if p.exists() else None
        except OSError:
            return
        self._undo_stack.append({"path": p, "data": data, "display": raw})

    def _truncate(self, text: str) -> str:
        limit = self.ctx.config.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n…(输出超过 {limit} 字符,已截断)"
