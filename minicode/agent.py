"""Agent 主循环:模型 ↔ 工具的回合内循环。

每回合:请求模型 → 流式输出 → 若有工具调用则逐个执行(过权限门)→
结果回填 → 再次请求,直到模型不再调用工具或步数达到上限。
变更类文件工具执行前自动快照,支持 /undo 逐步回滚;
上下文 token 估算超过阈值时自动把早期对话折叠成摘要。
"""
from __future__ import annotations

import ast
import difflib
import json
import time
from datetime import date
from pathlib import Path

from minicode.compaction import compact, estimate_tokens, schema_tokens
from minicode.llm import LLMClient, ToolCall
from minicode.model_catalog import context_window_for
from minicode.permission import ASK, REJECT, PermissionGate
from minicode import toolsum
from minicode.session import Session, outbound as session_outbound, repair_tool_pairs
from minicode.tools.base import (
    PathBlocked, PathOutsideWorkspace, Tool, ToolContext, resolve_path,
)
from minicode.ui import TerminalUI

SNAPSHOT_TOOLS = ("write_file", "edit_file")  # bash 的改动无法安全跟踪,不在回滚范围
SYNTAX_CHECK_TOOLS = ("write_file", "edit_file")   # 落盘 .py 的两种来源(借鉴 aider 的 lint-after-edit)
SYNTAX_SCAN_LIMIT = 512 * 1024                     # 只自检 512 KB 以内:再大不是"手写代码"的场景

# 撞步数上限后那一次收尾调用要说的话。它**只活在这次请求里**(不进会话留档),
# 因为没有别的地方需要读它;不给这句,模型不知道自己被"断了工具",收尾常常写成
# "接下来我要继续查……" —— 那正是这段代码要避免的戛然而止。
WRAP_UP_NOTE = ("工具调用轮数已经用完,这一次不许再调用工具。"
                "就上面已经跑完的那些结果,用几句话讲清:已经完成什么、还没完成什么、"
                "下一步建议做什么。不要重复已经说过的内容。")

FAIL_STREAK_LIMIT = 3      # 同一个调用连续失败几次就止损(与 B0「第 3 次搜空才催」同节奏)
FAIL_STREAK_NOTE = ("同一个工具调用已经连续失败 3 次,结果都一样,继续原样重试没有意义。"
                    "这一次不许再调用工具。请向用户如实说明:卡在哪一步、每次失败的具体原因"
                    "(以上面的工具回话为准,不要凭印象编)、以及可选的下一步"
                    "(换一种做法 / 改参数重试 / 放弃这一步先做别的),让用户来决定。")

TOKEN_LIMIT_NOTE = ("本会话累计 token 消耗已到 session_token_limit 上限,这一次不许再调用工具。"
                    "请向用户如实说明:这一轮做到哪了、还剩什么没做;想继续可以提高 "
                    "config.json 里的 session_token_limit,或新开一个会话再接着做。")


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
        # 上一次请求**真发出去多少 prompt token**(服务端返回的,不是估的)与当时的
        # 消息条数。量窗口那把尺子从此有了实测的下限 —— 见 `context_tokens`。
        self._real_prompt: tuple[int, int] | None = None
        self._undo_stack: list[dict] = []
        self._undo_seq = 0         # 快照的稳定编号:摘掉中间一条不会让别的错位
        self._undo_open = None     # 等这次真落盘之后补"改后指纹"的那条 seq
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
        # 成本闸门(v0.9.78):本会话累计真实消耗(只认服务端 usage)的上限。
        # 与 deadline 一样在循环顶部查 —— 用的是"上一回合结束"时的数,正在跑的
        # 那一步不受影响,天然满足"不强杀"的口径。
        token_limit = max(0, int(getattr(self.ctx.config, "session_token_limit", 0) or 0))
        steps = 0
        # 失败连击只在本回合内计数:跨回合的旧失败不该约束新回合的新决定
        # (B0 同款口径:一次任务里换关键词补搜不算)。
        self._fail_streak_key, self._fail_streak_count = None, 0
        for _ in range(self.max_steps):
            if token_limit and int(getattr(self, "turn_tokens", 0) or 0) > token_limit:
                used = int(getattr(self, "turn_tokens", 0) or 0)
                self.ui.token_limit_reached(token_limit, used)
                # 同两条时限/止损的口径:此刻不再开新的工具轮,但正在跑的
                # 步骤照常收尾 —— 收尾那一次调用本身也会耗一点,如实告诉模型。
                self._wrap_up(TOKEN_LIMIT_NOTE)
                return
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
                if self._fail_streak_hit(call):
                    # 连续 N 次一模一样的失败:再原样重试只是烧 token(撞上
                    # max_steps/时间限才停)。收一次尾,把"接下来怎么办"交给
                    # 用户 —— 文案不许说"我已停止了它",停的是这一轮的循环,
                    # 与两条时限同一口径。
                    self.ui.fail_streak_limit(call.name, FAIL_STREAK_LIMIT)
                    self._wrap_up(FAIL_STREAK_NOTE)
                    return
        else:
            self.ui.max_steps_reached(self.max_steps)
            self._wrap_up()

    def _wrap_up(self, note: str = WRAP_UP_NOTE) -> None:
        """步数耗尽(或失败连击触顶)后补一次**不带工具**的调用,收成一段话。

        撞上限那一刻,会话尾部正挂着执行完但模型还没读过一眼的工具结果;直接结束
        就是戛然而止,长任务(论文、题库)恰恰最容易撞在这里。这一次不给工具、
        不重进步数循环,文本照常在 `on_text_delta` 流出并入留档。
        末尾那句 note 只加在这次请求里 —— 模型不然不知道自己已经不能再
        调工具,会照着"下一步我去查一下"的口吻继续说(失败连击走
        FAIL_STREAK_NOTE:说明卡点、列出下一步选项,把决定交给用户)。
        它自己失败就静默退回原样 —— 最坏情况等于本轮改动之前的行为,不新增故障面。
        """
        try:
            turn = self.llm.stream_chat(
                self.session.messages + [{"role": "user", "content": note}], [],
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
        from minicode.app_setup import (build_system_prompt,
                                        instruction_signature)
        sig = (today, instruction_signature(self.ctx.workspace))
        if sig == self._prompt_sig:
            return
        crossed_day = sig[0] != self._prompt_date
        self._prompt_date = today
        self._prompt_sig = sig[1]
        if self.session.refresh_system_prompt(build_system_prompt(self.ctx.workspace)):
            # D1(v0.9.102):重建条件从"跨天"推广为"跨天或指令/技能清单变了"
            # —— 内容变了才破 KV Cache,没变照旧稳定命中。
            if crossed_day:
                self.ui.info("已跨天,日期与课表已刷新")
            else:
                self.ui.info("指令文件或技能清单有变化,已重新载入")

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
        # v0.9.66:剥掉本地记账字段(ts) —— 留档里的键比协议多,严格端点会对
        # 未知字段报 400。拷贝剥离,不动 session.messages 本体。
        msgs = session_outbound(msgs)
        out = list(msgs)
        # 后台命令的现状(v0.9.85):挂在**请求末尾**的一条临时消息,与撞上限时那句
        # WRAP_UP_NOTE 同一个手法 —— 不写进留档,也不动系统提示词(那是整段前缀,
        # 每步往里加变化会把 KV Cache 从那里打断,与 D0 时钟按天重建同一套取舍)。
        # 没有后台任务时它是空串,一个字都不注入。网页版是独立进程(web_main.py),
        # 任务表天生为空,所以访客也看不到主人的运行痕迹。
        try:
            from minicode.tools.shell import bg_brief_for_model
            brief = bg_brief_for_model()
        except Exception:
            brief = ""                      # 注入失败绝不能拖垮这次请求
        if brief:
            out.append({"role": "user", "content": brief})
        prior = self._pending_prior
        if not prior:
            return out
        self._pending_prior = ""
        if out and isinstance(out[0], dict) and out[0].get("role") == "system":
            out[0] = {**out[0], "content": f"{out[0].get('content') or ''}\n\n{prior}"}
        else:
            out.insert(0, {"role": "system", "content": prior})
        return out

    def _effective_config(self):
        """**真正发请求那个客户端**带的配置,不是全局那一份。

        两处会让它与 `ctx.config` 分家:会话内切模型(`Bridge` 重建 `agent.llm`)、
        子代理按档位换模型(v0.9.95 的 `LLMClient.scoped`)。记账与面板读 `ctx.config`
        说的就是"没在跑的那个模型" —— 每日用量按模型分开那张图会把便宜档的字算到
        主模型头上。测试桩的假 LLM 没有 config,退回 `ctx.config`。
        """
        return getattr(self.llm, "config", None) or self.ctx.config

    def _record_usage(self, turn) -> None:
        """把本轮真实 token 用量记进本地统计(见 minicode/usage.py)。

        只在**有真实 usage** 时记账;服务端没返回 usage 就不记,绝不估算。
        记账失败静默忽略——统计功能绝不能拖垮对话主流程。
        """
        try:
            from minicode import usage
            cfg = self._effective_config()
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
        if isinstance(pt, int) and not isinstance(pt, bool) and pt > 0:
            # 记的是"这一次请求发出去的量"与**当时的消息条数** —— 配对才有意义:
            # 只记数字的话,压缩或 /resume 之后它就成了另一份上下文的量。
            self._real_prompt = (len(self.session.messages), pt)
        try:
            from minicode.model_catalog import push_turn_usage
            push_turn_usage(self, self.session, self._effective_config(), turn)
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

    # 审查视图一屏列几处:超过就在载荷里明说"另有 N 处没列出"(与活动栏同一口径)。
    CHANGE_VIEW_LIMIT = 50

    def _change_display(self, p: Path) -> str:
        """改动清单上的文件名口径:一律相对工作区,绝不出现绝对路径。

        快照本身走 `resolve_path(for_write=True)`,工作区外根本进不来,所以这里不是
        在做防御,是在守"绝对路径不出桥"那条既有口径(网页版、导出面同一个理由)。
        """
        try:
            return p.relative_to(Path(self.ctx.workspace)).as_posix()
        except (ValueError, TypeError):
            return p.name

    def undo_records(self) -> dict:
        """「本轮改动」清单(倒序,最新在前)—— 审查视图读的就是这份栈,不另开账。

        每一条都带着"能不能退、退下去会怎样"的事实:`newer` 是同一处在这之后还有几处
        没退,`drift` 是这一处落盘之后又被别的工具动过。栈被 `/clear`、换会话、
        压缩历史清空时这里就是空的,界面必须照实说"没有可退的改动",
        不许留一份看起来还能退的缓存。
        """
        items = []
        for rec in reversed(self._undo_stack):
            created = rec["data"] is None
            items.append({
                "seq": rec["seq"],
                "path": self._change_display(rec["path"]),
                "kind": "新建" if created else "改",
                "verb": "删除" if created else "恢复",
                "at": time.strftime("%H:%M:%S", time.localtime(rec["at"])),
                "newer": sum(1 for r in self._undo_stack
                             if r["seq"] > rec["seq"] and r["path"] == rec["path"]),
                "drift": self._drifted(rec),
            })
        more = max(0, len(items) - self.CHANGE_VIEW_LIMIT)
        return {"ok": True, "items": items[:self.CHANGE_VIEW_LIMIT],
                "total": len(items), "more": more,
                # 抬头那句不是免责声明,是这一屏最容易骗人的一处:bash/py_script
                # 改出来的东西压根不在快照里,点了"全部回退"也退不掉它们。
                "note": ("只列我用 write_file / edit_file 改的文件;"
                         "命令(bash / py_script)与外部程序改的不在这里,也退不掉。"),
                "hint": ("没有可退的改动 —— 这份栈在 /clear、换会话、压缩历史时会清空,"
                         "所以这里空着是真的空,不是还没加载。")}

    # diff 那一屏的量:上下文 2 行、最多 200 行、超过 512 KB 不比(与语法自检同一上限口径 ——
    # 再大的文件不是"手写改动"的场景,硬比只会把一次刷新变成一次大扫描)
    DIFF_CONTEXT = 2
    DIFF_LINE_LIMIT = 200
    DIFF_SCAN_BYTES = 512 * 1024

    def undo_diff(self, seq) -> dict:
        """某一处改动的 diff:栈里那份**前像** 对 **磁盘现值**。

        为什么两侧都摆出来而不是"我以为改完会长什么样":快照只认 write_file / edit_file,
        中间 bash / py_script 也可能动过同一个文件 —— 所以右边必须是现在这一刻读到的那份,
        读不到就说读不到。`drift` 为真时那句话说在前头,不让人拿一份对不上的 diff 做决定。

        算不出来的几种情况一律 `ok: False` 带原因(二进制 / 太大 / 新建的又没了):
        给一份"看起来没有变化"的空 diff,比承认算不出更坏。
        """
        if isinstance(seq, bool) or not str(seq).strip().lstrip("-").isdigit():
            return {"ok": False, "msg": f"seq 只接受清单里的编号,收到 {seq!r}。"}
        want = int(str(seq).strip())
        rec = next((r for r in self._undo_stack if r["seq"] == want), None)
        if rec is None:
            return {"ok": False,
                    "msg": "清单可能已过期:这一处不在栈里了(退过、或栈被 /clear 与压缩清空过)。"}
        p: Path = rec["path"]
        name = self._change_display(p)
        before, after = rec["data"], None
        try:
            after = p.read_bytes() if p.exists() else None
        except OSError as e:
            return {"ok": False, "msg": f"{name} 现在读不出来({type(e).__name__}),没法比。"}
        if before is None and after is None:
            return {"ok": False,
                    "msg": f"{name} 是我新建的,而现在它不在磁盘上 —— 没有可比的两侧。"}
        too_big = [x for x in (before, after) if x and len(x) > self.DIFF_SCAN_BYTES]
        if too_big:
            return {"ok": False,
                    "msg": (f"{name} 有一侧超过 {self.DIFF_SCAN_BYTES // 1024} KB"
                            "(这份大小不像手写的改动),不比 diff;要看得自己打开文件。")}
        try:
            b_txt = (before or b"").decode("utf-8").splitlines()
            a_txt = (after or b"").decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return {"ok": False,
                    "msg": f"{name} 不是 UTF-8 文本(二进制或别的编码),算不出 diff。"}
        body = list(difflib.unified_diff(
            b_txt, a_txt, fromfile=f"{name}(我改之前)", tofile=f"{name}(磁盘上现在)",
            lineterm="", n=self.DIFF_CONTEXT))
        # 数加/减行数用**全量**(那是这一处的真实规模),给界面的行才截 ——
        # 截了还报"共 +N −M"才是假话,但把 N/M 也一起截小同样是假话。
        added = sum(1 for ln in body if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in body if ln.startswith("-") and not ln.startswith("---"))
        shown = [ln for ln in body if not ln.startswith(("---", "+++"))]
        drifted = self._drifted(rec)
        kind = ("新建" if before is None else
                "已删除" if after is None else "改")
        note = ("左边这份是我最后一次改它之前的内容,右边是磁盘上的现在这份。"
                + ("这一处在我改过之后还被别的工具动过,所以这份 diff 里含别人的改动。"
                   if drifted else ""))
        return {"ok": True, "path": name, "kind": kind,
                "added": added, "removed": removed,
                "lines": shown[:self.DIFF_LINE_LIMIT],
                "more": max(0, len(shown) - self.DIFF_LINE_LIMIT),
                "drift": drifted, "note": note,
                "same": not added and not removed}

    def _apply_undo(self, rec: dict) -> dict:
        """把一条快照落回磁盘:新建过的删掉,改过的写回前像。返回这处的事实与话术。"""
        p: Path = rec["path"]
        created = rec["data"] is None
        name = self._change_display(p)
        # 漂移要在动手**之前**判:落盘之后磁盘上就是我们写好的那份,提醒就永远不成立了。
        drifted = self._drifted(rec)
        if created:
            p.unlink(missing_ok=True)
            return {"path": name, "verb": "删除",
                    "msg": f"已撤销:删除新建的 {name}"}
        try:
            p.write_bytes(rec["data"])
        except OSError as e:
            return {"path": name, "verb": "恢复", "failed": True,
                    "msg": (f"撤销失败:{name} 写不回去"
                            f"({type(e).__name__}),这份改动还在栈里。")}
        tail = ("（提醒:这一处在我改过之后还被别的工具动过,那部分也一起没了。）"
                if drifted else "")
        return {"path": name, "verb": "恢复",
                "msg": f"已撤销:{name} 已恢复为改动前的内容{tail}"}

    def _undo_extra_tail(self, rec: dict) -> str:
        """退完之后补一句"这一处上面还有几处没退"——逐文件回退最容易被误当成一键复原。"""
        same = [r for r in self._undo_stack
                if r["path"] == rec["path"] and r["seq"] > rec["seq"]]
        if not same:
            return ""
        return (f"（这一处在这之后还有 {len(same)} 处改动没退,"
                "现在磁盘上是更早的那份;要一路退回本轮开始前请用「全部回退」。）")

    def undo(self, seq=None) -> str:
        """撤销一处文件改动。

        不传 `seq` = 退最近一步(后进先出,`/undo` 与「↶ 撤销」按钮的老行为)。
        传 `seq` = **只退那一条**并从栈里摘掉它,比它更新的条目原样留着 ——
        同一文件被改两次时,退第 2 次不会把第 1 次的成果一起吃掉;这条口径是
        "逐文件回退"与"换个名字的乱序撤销"唯一的区别,所以单独有测试钉住。
        """
        if not self._undo_stack:
            return "没有可撤销的文件改动(bash 产生的改动不在跟踪范围)。"
        rec = None
        if seq is None:
            rec = self._undo_stack.pop()
        else:
            if isinstance(seq, bool) or not str(seq).strip().lstrip("-").isdigit():
                return f"错误: seq 只接受清单里的编号,收到 {seq!r}。"
            want = int(str(seq).strip())
            for i, r in enumerate(self._undo_stack):
                if r["seq"] == want:
                    rec = self._undo_stack.pop(i)
                    break
            if rec is None:
                return (f"没有编号 {want} 的改动 —— 清单可能已经过期"
                        "(退过一条、或栈被 /clear 与压缩清空过),刷新一下再看。")
        out = self._apply_undo(rec)
        if out.get("failed"):
            self._undo_stack.append(rec)     # 没退成的留在栈里,别让它凭空消失
            return out["msg"]
        return out["msg"] + self._undo_extra_tail(rec)

    def undo_all(self) -> str:
        """一路退回本轮开始前:按最新→最旧逐条退,同一文件退到最早那份前像。"""
        if not self._undo_stack:
            return "没有可撤销的文件改动(bash 产生的改动不在跟踪范围)。"
        done, failed = [], []
        while self._undo_stack:
            rec = self._undo_stack.pop()
            out = self._apply_undo(rec)
            (failed if out.get("failed") else done).append(out)
            if out.get("failed"):
                self._undo_stack.append(rec)
        n = len(done)
        listed = "、".join(d["path"] for d in done[:5])
        if n > 5:
            listed += f" 等 {n} 处"
        tail = (f"另有 {len(failed)} 处写不回去(仍在栈里)。" if failed else "")
        return (f"已全部回退 {n} 处:{listed}。" if n else "没有一处退成功。") + tail + (
            " 命令与外部程序改的不在账里。" if n else "")

    # ------------------------------------------------------------ 压缩

    # 实测下限能用多久:同一轮里消息只会多出几条(工具结果 + 助手那句话)。
    # 差得远就说明中间换过一份对话(/resume)或压过一次 —— 那时那个数属于**另一段**
    # 上下文,拿它当底线会凭空触发一次真实的摘要调用(花额度、改留档),比估不准更糟。
    REAL_FLOOR_WINDOW = 8

    def _context_pair(self) -> tuple[int, str]:
        """(这次量出来的 token 数, 这个数的来源)。

        v0.9.97 补的那层:服务端返回过真实 prompt_tokens 时拿它**当底线**。
        估算那条路是「CJK 一字一 token、其余四字符一 token」,英文、代码、JSON 工具
        结果多的时候会明显低估,而低估的后果是撞墙才压。底线只往上顶、不往下压
        (取 `max`):消息序列只会变长,上一次真发出去的量必然是这一次的下界 ——
        所以它只可能让压缩来得更早,绝不会更晚。取不到实测(端点不给 usage)就照旧
        用估算,不假装量过。
        """
        est = estimate_tokens(self.session.messages) + self.tools_tokens
        rec = self._real_prompt
        if (rec and rec[1] > est
                and 0 <= len(self.session.messages) - rec[0] <= self.REAL_FLOOR_WINDOW):
            return rec[1], "real"
        return est, "estimated"

    def context_tokens(self) -> int:
        """这一次请求真正要发出去的东西占多少 token:消息 + 40 来个工具的定义。

        只数消息会系统性低估几千 token,于是阈值偏晚触发,把真实窗口顶到脸上才想起
        来压(v0.9.40 D2)。CLI 的 `/history` 与任务面板用的是这同一个数。
        """
        return self._context_pair()[0]

    def context_source(self) -> str:
        """这把尺子这次是"实测顶上来的"还是"本地估的" —— 报出去要说得出来源。"""
        return self._context_pair()[1]

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

        # 项目白名单放行(v0.9.87):用户当场没点过头的一步必须留两处痕 ——
        # 安全日志一行 + 界面一条 info。静默放行等于没有审计。
        by_project = getattr(self.gate, "project_allow_reason", "") or ""
        if by_project:
            self._note_project_allow(tool, args, by_project)

        self._note_low_rated_prior(tool, args)
        # pre 钩子(F3,v0.9.100):用户 config 配置的命令级审计;非零退出 = 拦截。
        # 放在 tool_start 之前 —— 与"参数坏/硬拒绝不出卡片"的口径一致,拒绝
        # 不该先画一张"正在执行"的卡。
        pre = self._run_config_hook("pre_tool", tool, args)
        if pre is not None and pre.get("blocked"):
            return (f"错误: 该步被工具钩子拦下(pre_tool 非零退出)。"
                    f"钩子输出:{pre.get('output') or '(无)'}")
        self._snapshot_before_mutation(tool, args)
        self.ui.tool_start(tool, args)
        try:
            result = tool.handler(self.ctx, **args)
        except TypeError as e:
            result = f"错误: 工具参数与 schema 不匹配: {e}"
        except Exception as e:
            result = f"工具执行出错: {type(e).__name__}: {e}"
        self.ui.tool_done(tool, result)
        self._mark_after_mutation(tool, args, result)
        # post 钩子:失败不拦截,在结果尾部如实附一行(一行,不刷屏)
        post = self._run_config_hook("post_tool", tool, args, result=result)
        if post is not None and not post.get("ok"):
            result = result + f"\n(工具钩子 post_tool 失败:{post.get('output') or '原因不明'})"
        result = self._truncate(result)
        result = self._mark_repeat_empty(tool, args, result)
        result = self._py_syntax_feedback(tool, args, result)
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

    def _fail_streak_hit(self, call: ToolCall) -> bool:
        """同一个工具 + 同一组参数连续失败 FAIL_STREAK_LIMIT 次(v0.9.77)。

        失败判定问 `toolsum.is_failure`(唯一口径);"同样"= 工具名与参数
        逐字相同(sort_keys 规范化)—— 换参数重试是健康的思路调整,不累计
        (与 B0「换关键词补搜不算」同一条口径);成功或换键都会重置。
        """
        try:
            args = json.loads(call.arguments) if call.arguments.strip() else {}
        except json.JSONDecodeError:
            args = {}
        key = call.name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False)
        result = next((m.get("content") for m in reversed(self.session.messages)
                       if m.get("role") == "tool"
                       and m.get("tool_call_id") == call.id), "")
        if not toolsum.is_failure(str(result)):
            self._fail_streak_key, self._fail_streak_count = None, 0
            return False
        if key == self._fail_streak_key:
            self._fail_streak_count += 1
        else:
            self._fail_streak_key, self._fail_streak_count = key, 1
        return self._fail_streak_count >= FAIL_STREAK_LIMIT

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

    def _note_project_allow(self, tool: Tool, args: dict, why: str) -> None:
        """按项目白名单放行时留痕:安全日志一行 + 界面一句人话。

        两句都是必要的:日志给事后排查("这一步是谁批的"),info 给当场看见
        (不然用户只会发现"今天学长没问我",而不是"因为这份文件里第 2 条")。
        失败静默 —— 留痕不能把工具执行带崩。
        """
        try:
            from minicode import provenance
            provenance.log_escalation("project_allow", tool.name, args, why, "auto")
        except Exception:
            pass
        try:
            info = getattr(self.ui, "info", None)
            if callable(info):
                info(f"这一步没问你:{why}(文件核对批准过;改一个字就失效)")
        except Exception:
            pass

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
        # 子代理的工具名带类型前缀(implement·write_file),取末段比对 ——
        # 不剥前缀的话,子代理的写永远不进快照,「/undo 撤不掉子代理的改动」。
        if tool.name.rsplit("·", 1)[-1] not in SNAPSHOT_TOOLS:
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
        self._undo_seq += 1
        self._undo_stack.append({"seq": self._undo_seq, "path": p, "data": data,
                                 "display": raw, "at": time.time(), "post": None})
        # 这一条还没兑现:工具可能因为参数错/权限失败而根本没落盘。落盘成功后由
        # `_mark_after_mutation` 补上"改后指纹"(见那里的理由),没成功就把这条摘掉 ——
        # 清单里留一条退不动的改动,比少列一条更容易骗人。
        self._undo_open = self._undo_seq

    def _mark_after_mutation(self, tool: Tool, args: dict, result: str) -> None:
        """给刚才那条快照补上"我改完之后它长什么样"的指纹。

        审查视图要回答的不是"我改了什么"而是"现在磁盘上这份还是不是我留下的那份":
        bash / py_script 改的东西不在快照范围(`SNAPSHOT_TOOLS` 那句注释),所以一个
        文件可能在我写完之后被命令又改过。用 size + mtime 认这个漂移,不重读整份内容。
        写失败的那一次顺手从栈里摘掉:没有落盘就没有可退的东西。
        """
        seq, self._undo_open = self._undo_open, None
        if seq is None or tool.name.rsplit("·", 1)[-1] not in SNAPSHOT_TOOLS:
            return
        idx = next((i for i, r in enumerate(self._undo_stack)
                    if r["seq"] == seq), None)
        if idx is None:
            return
        from minicode.toolsum import is_failure
        if is_failure(result):
            self._undo_stack.pop(idx)
            return
        try:
            st = self._undo_stack[idx]["path"].stat()
            self._undo_stack[idx]["post"] = {"size": st.st_size,
                                             "mtime": st.st_mtime}
        except OSError:
            # 拿不到指纹就当"没漂移":宁可少提醒一句,也不给每个文件都扣上
            # "可能被别的工具改过"—— 那会把真提醒淹掉。
            self._undo_stack[idx]["post"] = None

    def _drifted(self, rec: dict) -> bool:
        """这一处改动落盘之后,磁盘上这份还被别的东西动过吗。"""
        post = rec.get("post")
        p: Path = rec["path"]
        if not p.exists():
            return rec["data"] is not None      # 新建的本来就该在;没了就是被动过
        if not post:
            return False
        try:
            st = p.stat()
        except OSError:
            return True
        return (st.st_size != post["size"]
                or st.st_mtime > post["mtime"] + 1.0)

    def _run_config_hook(self, kind: str, tool, args: dict,
                         result: str | None = None) -> "dict | None":
        """跑用户配置的工具钩子;没配返回 None。钩子只从 config.json 的
        hooks 字段来 —— 工作区文件长不出钩子(那是任意命令执行口子)。"""
        from minicode import hooks
        cfg = getattr(getattr(self, "ctx", None), "config", None)
        cfg_hooks = getattr(cfg, "hooks", None)
        if not isinstance(cfg_hooks, dict):
            return None
        try:
            return hooks.run_hook(kind, cfg_hooks, tool.name, args, result)
        except Exception as e:      # 钩子本身崩了也如实算 pre 拦截/post 失败
            return {"ok": False, "blocked": kind == "pre_tool",
                    "output": f"{type(e).__name__}: {e}"}

    def _truncate(self, text: str) -> str:
        limit = self.ctx.config.max_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n…(输出超过 {limit} 字符,已截断)"

    def _py_syntax_feedback(self, tool: Tool, args: dict, result: str) -> str:
        """Python 写后自检(借鉴 aider 的 lint-after-edit):write_file/edit_file
        落盘 .py 成功后立刻 ast.parse 一遍,解析不过就把行号与报错原文回填进
        工具结果 —— 模型当场修,而不是留着"文件已写好"的假象,等到运行或下一
        轮才发现。这条闸对着 HANDOFF §4 反复出现的坑(Edit 锚点吞掉 except、
        生成的脚本被写坏),ast 是标准库,零新依赖。

        只检"真的写成功了"的落盘(失败结果没有新内容可检);路径解析不了/
        文件不在/超过大小上限一律静默跳过 —— 自检是反馈,不能变成第二个故障源。
        """
        if tool.name.rsplit("·", 1)[-1] not in SYNTAX_CHECK_TOOLS:
            return result
        from minicode.toolsum import is_failure
        if is_failure(result):
            return result
        raw = str(args.get("path") or "")
        if not raw.rstrip().lower().endswith(".py"):
            return result
        try:
            p = resolve_path(self.ctx, raw, for_write=True)
        except (PathOutsideWorkspace, PathBlocked, OSError):
            return result
        try:
            if not p.is_file() or p.stat().st_size > SYNTAX_SCAN_LIMIT:
                return result
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return result
        try:
            ast.parse(source, filename=str(p))
        except SyntaxError as e:
            return (result + f"\n\n⚠ 语法自检:这个 Python 文件现在解析不过 —— "
                    f"第 {e.lineno} 行:{e.msg}。请立刻修好它再继续做别的,"
                    "不要留着一个解析不过的文件。")
        return result
