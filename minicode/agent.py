"""Agent 主循环:模型 ↔ 工具的回合内循环。

每回合:请求模型 → 流式输出 → 若有工具调用则逐个执行(过权限门)→
结果回填 → 再次请求,直到模型不再调用工具或步数达到上限。
变更类文件工具执行前自动快照,支持 /undo 逐步回滚;
上下文 token 估算超过阈值时自动把早期对话折叠成摘要。
"""
from __future__ import annotations

import json
from pathlib import Path

from minicode.compaction import compact, estimate_tokens
from minicode.llm import LLMClient, ToolCall
from minicode.permission import ALLOW, ASK, REJECT, PermissionGate
from minicode.session import Session
from minicode.tools.base import PathOutsideWorkspace, Tool, ToolContext, resolve_in_workspace
from minicode.ui import TerminalUI

MAX_STEPS = 40  # 单回合最多工具调用轮数,防止死循环
SNAPSHOT_TOOLS = ("write_file", "edit_file")  # bash 的改动无法安全跟踪,不在回滚范围


class Agent:
    def __init__(self, llm: LLMClient, registry: list[Tool], session: Session,
                 gate: PermissionGate, ui: TerminalUI, ctx: ToolContext):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.gate = gate
        self.ui = ui
        self.ctx = ctx
        self._tool_map = {t.name: t for t in registry}
        self._undo_stack: list[dict] = []

    def run_turn(self, user_input: str) -> None:
        self.gate.new_turn()
        self.session.add_user(user_input)

        for _ in range(MAX_STEPS):
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

            if not turn.tool_calls:
                return

            for call in turn.tool_calls:
                result = self._execute(call)
                self.session.add_tool_result(call.id, call.name, result)
        else:
            self.ui.max_steps_reached(MAX_STEPS)

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
        threshold = int(cfg.context_window * cfg.compact_at_ratio)
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

        decision = self.gate.check(tool, args)
        if decision == REJECT:
            self.ui.tool_rejected_hard(tool, args)
            return ("错误: 该操作被安全策略硬拒绝(可能造成不可恢复的破坏),"
                    "已阻止执行,请勿重试。")
        if decision == ASK:
            choice = self.ui.confirm_tool(tool, args)
            if choice == "always":
                self.gate.approve_for_turn(tool.name)
            elif choice != "yes":
                return "用户拒绝了本次工具调用。请停止重试,询问用户希望如何处理。"

        self._snapshot_before_mutation(tool, args)
        self.ui.tool_start(tool, args)
        try:
            result = tool.handler(self.ctx, **args)
        except TypeError as e:
            result = f"错误: 工具参数与 schema 不匹配: {e}"
        except Exception as e:
            result = f"工具执行出错: {type(e).__name__}: {e}"
        self.ui.tool_done(tool, result)
        return self._truncate(result)

    def _snapshot_before_mutation(self, tool: Tool, args: dict) -> None:
        if tool.name not in SNAPSHOT_TOOLS:
            return
        raw = str(args.get("path", ""))
        try:
            p = resolve_in_workspace(self.ctx, raw)
        except PathOutsideWorkspace:
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
