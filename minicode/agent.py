"""Agent 主循环:模型 ↔ 工具的回合内循环。

每回合:请求模型 → 流式输出 → 若有工具调用则逐个执行(过权限门)→
结果回填 → 再次请求,直到模型不再调用工具或步数达到上限。
变更类文件工具执行前自动快照,支持 /undo 逐步回滚;
上下文 token 估算超过阈值时自动把早期对话折叠成摘要。
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from minicode.compaction import compact, estimate_tokens, schema_tokens
from minicode.llm import LLMClient, ToolCall
from minicode.model_catalog import context_window_for
from minicode.permission import ASK, REJECT, PermissionGate
from minicode.session import Session, repair_tool_pairs
from minicode.tools.base import (
    PathBlocked, PathOutsideWorkspace, Tool, ToolContext, resolve_path,
)
from minicode.ui import TerminalUI

SNAPSHOT_TOOLS = ("write_file", "edit_file")  # bash 的改动无法安全跟踪,不在回滚范围

# 撞步数上限后那一次收尾调用要说的话。它**只活在这次请求里**(不进会话留档),
# 因为没有别的地方需要读它;不给这句,模型不知道自己被"断了工具",收尾常常写成
# "接下来我要继续查……" —— 那正是这段代码要避免的戛然而止。
WRAP_UP_NOTE = ("工具调用轮数已经用完,这一次不许再调用工具。"
                "就上面已经跑完的那些结果,用几句话讲清:已经完成什么、还没完成什么、"
                "下一步建议做什么。不要重复已经说过的内容。")


class Agent:
    def __init__(self, llm: LLMClient, registry: list[Tool], session: Session,
                 gate: PermissionGate, ui: TerminalUI, ctx: ToolContext,
                 max_steps: int = 0):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.gate = gate
        self.ui = ui
        self.ctx = ctx
        # 单回合工具调用轮数上限:唯一的数在 config.py 的 `max_steps` 字段里。
        # 这里不写第二个默认值 —— 显式传参(子代理那一档)优先,没传就回 config。
        self.max_steps = int(max_steps) if max_steps else int(ctx.config.max_steps)
        self._tool_map = {t.name: t for t in registry}
        # 工具 JSON Schema 每轮都随消息一起发出去,量窗口不能只数消息。
        # 注册表运行期不变(MCP 工具在装配时就并进来了),所以这里算一次存住。
        self.tools_tokens = schema_tokens(registry)
        self._undo_stack: list[dict] = []
        self._narration = ""       # 本轮模型写的说明文字:权限确认框里当"为什么要做"的兜底
        self._pending_prior = ""   # B1:下一步 search_knowledge 命中历史差评时的那句系统侧先验
        # 身上这份 system prompt 是哪一天建的。system prompt 里烧着日期与"今天/明天
        # 的课",而本应用是托盘常驻的 —— 记下建它的那天,回合开头一比对就知道过没过期。
        self._prompt_date = date.today()
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

    def run_turn(self, user_input: str, content=None, *, add_user: bool = True) -> None:
        """跑一个回合。content 可选:带附件时的多模态消息体(list[dict])。

        `add_user=False` 给用户点「重新生成」用:那句提问已经在上下文里了(而且带着
        当时的附件消息体),再追加一遍会让模型把同一句话看到两遍,答出来的是"下一题"。
        """
        self._refresh_clock()      # 跨天了先重建日期与课表段,再收这条新消息
        self.gate.new_turn()
        if add_user:
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
                # 与撞步数上限同一口径:此刻工具结果已经躺在上下文里,模型还没
                # 读过一眼 —— 不补这次收尾,长任务就停在最戛然而止的地方。
                # wrap_up 自己失败就静默退回,不会把"到点停止"变成第二次故障。
                self._wrap_up()
                return
            steps += 1
            self._maybe_compact()
            try:
                turn = self.llm.stream_chat(
                    self._messages_for_request(),
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
                try:
                    result = self._execute(call)
                except Exception as e:  # noqa: BLE001 —— 见下面为什么必须兜住
                    # handler 自己抛错(不止 _execute 里那几种可预期的)也必须落一条
                    # 结果:少这一条,上下文里就留下"有 call 没结果"的悬空对儿,从这一
                    # 轮起每次请求都会 400,而用户看不出跟刚才那次报错有什么关系。
                    result = (f"错误: 工具 {call.name} 执行时抛了 "
                              f"{type(e).__name__}: {str(e)[:200]}")
                self.session.add_tool_result(call.id, call.name, result)
        else:
            self.ui.max_steps_reached(self.max_steps)
            self._wrap_up()

    def _wrap_up(self) -> None:
        """步数耗尽后补一次**不带工具**的调用,把悬着的工具结果收成一段话。

        撞上限那一刻,会话尾部正挂着执行完但模型还没读过一眼的工具结果;直接结束
        就是戛然而止,长任务(论文、题库)恰恰最容易撞在这里。这一次不给工具、
        不重进步数循环,文本照常在 `on_text_delta` 流出并入留档。
        末尾那句 `WRAP_UP_NOTE` 只加在这次请求里 —— 模型不然不知道自己已经不能再
        调工具,会照着"下一步我去查一下"的口吻继续说。
        它自己失败就静默退回原样 —— 最坏情况等于本轮改动之前的行为,不新增故障面。
        """
        try:
            turn = self.llm.stream_chat(
                self.session.messages + [{"role": "user", "content": WRAP_UP_NOTE}], [],
                on_text=self.ui.on_text_delta,
                on_reasoning=self.ui.on_reasoning_delta,
                on_retry=self.ui.on_retry)
        except KeyboardInterrupt:
            self.ui.turn_interrupted()
            return
        except Exception:
            return
        self.ui.finish_stream()
        # 这一次没给工具,端点若仍回吐 tool_calls(兼容端点会这么干),把它们摘掉再入
        # 会话 —— 留下一条没人回答的 tool_calls,下一轮每个请求都会 400。
        if turn.tool_calls:
            from dataclasses import replace
            turn = replace(turn, tool_calls=[])
        self.session.add_assistant(turn)
        self._record_usage(turn)
        self._bump_turn_tokens(turn)
        self._push_turn_stats(turn)

    def _refresh_clock(self) -> None:
        """跨过一天就把 system prompt 重算一遍(每个会话每天至多一次)。

        为什么按日重建而不是每轮注入一行当前时间:后者**每一步请求都在破坏
        KV Cache 前缀**(校历段 `calendar.py` 顶层注释就是为了这个才不带时间的),
        前者一天只破一次。重建走 `build_system_prompt` 这同一个函数 —— 日期、课表、
        校历、记忆的真相仍各只有一处,这里不产生第二份时间来源。

        只在回合边界检查:`run_turn` 一开头,第一条 LLM 请求之前。跑到一半的回合
        绝不会被换掉提示词(后台会话运行器各建各的 Agent,各查各自的)。
        """
        today = date.today()
        if today == self._prompt_date:
            return
        self._prompt_date = today
        from minicode.app_setup import build_system_prompt  # 延迟导入:app_setup 顶层 import 本模块
        if self.session.refresh_system_prompt(build_system_prompt(self.ctx.workspace)):
            self.ui.info("已跨天,日期与课表已刷新")

    def _messages_for_request(self) -> list[dict]:
        """这一次请求要发的消息 = 留档那份(先过一遍工具调用配对)+(必要时)一条
        **只活在这一次请求里**的先验。

        B1(差评避让)要"给模型一句提示,但不改用户消息、也不进留档正文":所以这里
        **复制**一份消息列表,把先验并进开头那条 system 提示的末尾。为什么并进 system
        而不是另插一条 —— 有的兼容端点只认开头那一条 system,插在中间会直接 400,
        而这一层我手上没有可核的真端点,不拿用户的对话去赌。

        先验只用一次(`_pending_prior` 用完即清),否则同一条会在后面每一步都重复出现。
        """
        msgs = self.session.messages
        # 发出去前兜一遍配对:`load_from` 只在恢复时清洗,而**压缩**(`replace_messages`
        # 保头+尾)与上面那个异常补记都可能从中间切断一对 call/result。非法序列被端点
        # 拒的是整次请求,症状会出现在"下一轮",回溯不到这一轮 —— 所以这道闸要贴着出口。
        msgs, _fixed = repair_tool_pairs(msgs)
        prior = self._pending_prior
        if not prior:
            return msgs
        self._pending_prior = ""
        out = list(msgs)
        if out and isinstance(out[0], dict) and out[0].get("role") == "system":
            out[0] = {**out[0], "content": f"{out[0].get('content') or ''}\n\n{prior}"}
        else:
            out.insert(0, {"role": "system", "content": prior})
        return out

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
        u = turn.usage if isinstance(turn.usage, dict) else {}
        cached = u.get("cached_tokens")
        try:
            push("turn_stats", elapsed_ms=turn.elapsed_ms,
                 tokens=tokens, tps=tps,
                 reasoning=bool(turn.reasoning),
                 cached_tokens=(cached if isinstance(cached, int) and cached >= 0
                                else None))
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

    def context_tokens(self) -> int:
        """这一次请求真正要发出去的东西占多少 token:消息 + 40 来个工具的定义。

        只数消息会系统性低估几千 token,于是阈值偏晚触发,把真实窗口顶到脸上才想起
        来压(v0.9.40 D2)。CLI 的 `/history` 与任务面板用的是这同一个数。
        """
        return estimate_tokens(self.session.messages) + self.tools_tokens

    def compact_now(self) -> None:
        self._maybe_compact(force=True)

    def _maybe_compact(self, force: bool = False) -> bool:
        cfg = self.ctx.config
        # 与任务面板同一把尺子:优先用运行时探测到的真实窗口(见 model_probe),
        # 探测不到才退回 config.json 里的默认值。
        window = context_window_for(cfg.model, cfg.context_window)
        threshold = int(window * cfg.compact_at_ratio)
        if not force and self.context_tokens() <= threshold:
            return False
        # 压缩是一次**真实的模型调用**(把早期对话发出去做摘要,要走网络):
        # 它必须像 _wrap_up 一样自己兜住 —— 抛出去会把整个回合带走,用户那句
        # 提问连模型都没见到,报的还不是"模型调用失败"这句人话。失败这一轮
        # 不压、原样继续,阈值逻辑还在,下一轮会再试。
        self.ui.info("上下文接近窗口上限,先把早期对话整理成摘要(可能要几秒)…")
        try:
            new_msgs, before = compact(self.session.messages, self.llm)
        except Exception:
            self.ui.info("这次上下文整理没做成(模型暂时连不上?),先直接回答;之后还会再试。")
            return False
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
            # 用户放行后记住工作区外路径:「允许」管本回合;「总是允许」升为
            # 会话级(不落盘,换工作区/重启即失效)
            self.gate.approve_outside(tool, args, session=(choice == "always"))

        self._note_low_rated_prior(tool, args)
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
        result = self._mark_repeat_empty(tool, args, result)
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

    # ---------------------------------------------------- 信号回灌(v0.9.39 B0/B1)
    #
    # 两处都在 `_execute` 里挂钩,因为只有这一层同时看得到"这一步是哪个工具、参数是
    # 什么"和"会话里已经发生过什么";`ToolContext` 里没有会话消息,所以 kb.search_knowledge
    # 自己算不出"这是第几次搜空"(路线图 §4 原先以为能在 handler 里做,读代码后发现不行)。
    # 两处都**失败即退回原样**:回灌是锦上添花,不能因为读一份 JSONL 出异常就带走一轮对话。

    def _recall_enabled(self) -> bool:
        return bool(getattr(self.ctx.config, "feedback_recall", True))

    def _note_low_rated_prior(self, tool: Tool, args: dict) -> None:
        """B1:这一步是 `search_knowledge` 且撞上一条最近的历史 👎 时,准备一句先验。

        先验在**下一次**请求里生效(并进开头那条 system 提示,见 `_messages_for_request`)
        —— 那正是模型拿着检索结果组织回答的时刻。保守边界(用户拍的):只在这一个工具
        前面做匹配,不是每次回答前都算一遍。
        """
        if tool.name != "search_knowledge" or not self._recall_enabled():
            return
        try:
            from minicode import feedback_recall
            matched = feedback_recall.match_low_rated(str(args.get("query") or ""))
            if matched:
                self._pending_prior = feedback_recall.avoidance_hint(matched)
        except Exception:
            self._pending_prior = ""       # 读不到差评就当没有,绝不因此中断这一步

    def _mark_repeat_empty(self, tool: Tool, args: dict, result: str) -> str:
        """B0:同一个词第 3 次(含)还是搜空,就在结果末尾附一行事实 + 下一步。

        附在 `_truncate` 之后、溯源围栏之前:截断不能把这句吃掉,而它本身不是外部内容,
        不该被围栏包进去(包进去会让"×3 未命中"看起来像网页里抄来的话)。
        """
        if tool.name != "search_knowledge" or not self._recall_enabled():
            return result
        try:
            from minicode import feedback_recall, growth, kb
            if not kb.is_no_hit(result):
                return result
            times = feedback_recall.repeat_empty_queries(
                self.session.messages).get(growth.normalize_query(args.get("query", "")), 0) + 1
            if times <= feedback_recall.REPEAT_ASK_N:
                return result          # 前两次空手是正常检索,不催它换策略
            return result + "\n\n" + feedback_recall.repeat_note(times)
        except Exception:
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
