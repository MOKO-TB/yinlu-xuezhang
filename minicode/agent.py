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
HOOK_BAD_STREAK = 3        # 同一只钩子连着三次"没跑成"就提醒用户一次(不是拦)

# 红线硬拒的回话。抽成常量是因为 H1 之后它有两个出口(钩子之前的预检、
# 改写之后重跑门),两处说的话必须一字不差 —— 两处各写一遍迟早对不上。
# 后半句是 #49d:硬拒过去只丢一句"请勿重试",模型手里没有出口,于是要么原地
# 打转、要么换个写法试探绕过 —— 后者比前者危险。给的是**说话的对象**(向用户
# 申请),不是**绕过的通道**。
HARD_REJECT_NOTE = (
    "错误: 该操作被安全策略硬拒绝(可能造成不可恢复的破坏),这一步没有执行,不要重试。"
    "这条路只有用户本人能放开(改 config.json 的权限档/沙箱开关,或项目白名单)。"
    "请向用户如实说明你想做什么、被哪条红线挡住、需要什么权限,由他决定;"
    "**不要换一种写法绕过它** —— 绕过红线不是完成任务的备选路径。")

# 这三类 ASK 不接受 permission_request 钩子代答(H3,v0.9.115):凭据文件、仓库
# `.git`、以及"本回合接触过外部内容"之后升格的敏感动作。共同点是它们在自己的
# 注释里就写着"权限档位、项目白名单、本轮总是允许都不替它背书" —— 最后一道是
# **人的眼睛**。把它换成一条命令的 stdout,等于给注入防御装了个内部开关:
# 钩子的输出会受它刚读到的内容影响,而那正是这几类要防的东西。
NON_DELEGABLE_ASKS = ("credential", "git", "external")

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
                 max_steps: int = 0, is_sub: bool = False):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.gate = gate
        self.ui = ui
        # H4:`session_start`/`user_prompt_submit` 说的是一次**用户开的会话**。子代理
        # 拿到的是任务文本、活几轮就结束,不是会话 —— 让它也叫这两个事件,一条
        # "每次开会话说一句"的钩子就会在每路子代理上各叫一遍(而用户只会觉得
        # "我只配了一次")。这个旗标是这件事唯一的闸口,由 subagents 显式传 False 侧。
        self._is_sub = bool(is_sub)
        self._started = False      # session_start 只叫一次(一个实例 = 一次会话)

        self.ctx = ctx
        # 单回合工具调用轮数上限:唯一的数在 config.py 的 `max_steps` 字段里。
        # 这里不写第二个默认值 —— 显式传参(子代理那一档)优先,没传就回 config。
        self.max_steps = int(max_steps) if max_steps else int(ctx.config.max_steps)
        self._tool_map = {t.name: t for t in registry}
        # 上一次请求**真发出去多少 prompt token**(服务端返回的,不是估的)与当时的
        # 消息条数。量窗口那把尺子从此有了实测的下限 —— 见 `context_tokens`。
        self._real_prompt: tuple[int, int] | None = None
        self._undo_stack: list[dict] = []
        self._undo_seq = 0         # 快照的稳定编号:摘掉中间一条不会让别的错位
        # 钩子健康计数(按 kind):连着几次"没跑成"就说一次话。不跨会话累计 ——
        # 它是"这轮你一直在叫一条坏命令"的提醒,不是故障台账。
        self._hook_bad: dict[str, int] = {}
        # 「这一处改动是在第几问之后做的」——编辑重发要按这个回退文件(R3)。
        # `_epoch` 管的是另一件事:压过一次历史后前面的提问被折进摘要,序号整体前移,
        # 老记录上那个号就不再对得上任何东西 —— 纪元不同的记录一律不许按号回退。
        self._turn_ordinal = 0
        self._epoch = 0
        self._undo_open = None     # 等这次真落盘之后补"改后指纹"的那条 seq
        self._narration = ""       # 本轮模型写的说明文字:权限确认框里当"为什么要做"的兜底
        self._pending_prior = ""   # B1:下一步 search_knowledge 命中历史差评时的那句系统侧先验
        # W1:这次请求成功之前撞了几次重试。挂在 agent 上而不是 UI 上,因为
        # 它要跟 `_record_usage` 一起落库(UI 的那份是给监控栏的会话级计数)。
        self._llm_retries = 0
        # W10:本步实测毫秒(handler 全程)。_execute 每步开头清零;替身/子类
        # 覆盖 _execute 时这里保底 None —— add_tool_result 拿到 None 不带键。
        self._step_ms = None
        self._hook_ms = None      # 同一条保底:H2 的钩子实测毫秒,没跑钩子就不带键

        # 身上这份 system prompt 是哪一天建的。system prompt 里烧着日期与"今天/明天
        # 的课",而本应用是托盘常驻的 —— 记下建它的那天,回合开头一比对就知道过没过期。
        self._prompt_date = date.today()
        # D1 加的"指令文件/技能清单指纹"同样要在这里落一次底:None = 还没比过,
        # 第一回合只记账不重建(见 _refresh_clock 那句理由)。漏了这行不是"少刷一次",
        # 是每一路新会话的第一条消息直接 AttributeError —— 整个回合崩掉。
        self._prompt_sig = None
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
        self._note_daily_budget()  # C1:日预算接近/超支,当天各提醒一次
        self.gate.new_turn()
        if add_user:
            self.session.add_user(user_input, content)
        # 这一回合是第几问(1 起)。`add_user=False`(重新生成)时那句提问本来就在,
        # 数出来的还是它的序号 —— 两种入口拿到同一个坐标。
        self._turn_ordinal = sum(1 for m in self.session.messages
                                 if m.get("role") == "user")
        # H4:开会话的第一问之前叫一次 session_start,此后每问叫一次 user_prompt_submit。
        # 放在消息入会话**之后** —— 钩子读到的 args 里就该带着用户刚说的那句话。
        self._fire_start_hooks(user_input, add_user)

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
                    on_retry=self._on_retry,
                    fallback_provider_id=self._fallback_provider(),
                    on_fallback=lambda e, n: self.ui.push(
                        "info", text=str(e)) if hasattr(self.ui, "push") else None,
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
                self.session.add_tool_result(call.id, call.name, result,
                                             elapsed_ms=self._step_ms,
                                             hook_ms=self._hook_ms)
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
                on_retry=self._on_retry)
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
        fp = instruction_signature(self.ctx.workspace)
        if self._prompt_sig is None:
            # 建 Agent 时这份提示词刚由 build_system_prompt 拼好,第一遍把指纹记下来就收工:
            # 往下走会重写一遍内容一模一样的 system,还会冒出一句「指令文件或技能清单有变化,
            # 已重新载入」—— 那是没发生过的事。**但日期本身已经过期时不能跳过**,
            # 否则零点后才建的会话会一直拿着昨天的日子。
            self._prompt_sig = fp
            if today == self._prompt_date:
                return
        # 两个事实分开比,别写成"元组 == 字符串":那样永远不相等,于是每一步都重建
        # system、每一步都说一句已重新载入 —— 既把 KV Cache 前缀破光(这套按日重建
        # 的全部理由就是为了别破),又每句都在骗人。
        elif today == self._prompt_date and fp == self._prompt_sig:
            return
        crossed_day = today != self._prompt_date
        self._prompt_date = today
        self._prompt_sig = fp
        if self.session.refresh_system_prompt(build_system_prompt(
                self.ctx.workspace,
                reply_style=getattr(getattr(self.ctx, "config", None),
                                    "reply_style", "normal"))):
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
            brief = bg_brief_for_model(self.ctx)
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

    def _on_retry(self, e: Exception, attempt: int) -> None:
        """重试的中转(W1):先记一笔账,再把原话转给界面。

        为什么不把 `ui.on_retry` 直接递给 LLM 层:重试次数要跟着 `_record_usage`
        落进本地账本(usage.retries 列),而那条账记在 agent 这一层 —— 中转一次,
        两头(落库的数、屏幕上的话)才出自同一个计数。转交经 getattr 且兜异常:
        界面坏了绝不能把重试本身带走。
        """
        self._llm_retries += 1
        forward = getattr(self.ui, "on_retry", None)
        if callable(forward):
            try:
                forward(e, attempt)
            except Exception:
                pass

    def _record_usage(self, turn) -> None:
        """把本轮真实 token 用量记进本地统计(见 minicode/usage.py)。

        只在**有真实 usage** 时记账;服务端没返回 usage 就不记,绝不估算。
        W0 起随账多带三个实测事实:会话身份(留档文件名,子代理没有留档就是
        空串)、这次请求的实测耗时、成功之前撞了几次重试。重试计数只在**真记上
        账时**清零 —— 这次请求没 usage,这笔重试挂在下一条有账的记录上,
        不让它凭空消失。记账失败静默——统计功能绝不能拖垮对话主流程。
        """
        try:
            from minicode import usage
            cfg = self._effective_config()
            u = turn.usage if isinstance(turn.usage, dict) else {}
            ttft = getattr(turn, "ttft_ms", None)
            logged = usage.record_usage(
                provider=cfg.provider.id if cfg.provider else "",
                model=cfg.model,
                usage=turn.usage,
                session_id=(self.session.log_path.stem
                            if self.session.log_path else ""),
                elapsed_ms=(turn.elapsed_ms if isinstance(turn.elapsed_ms, int)
                            and turn.elapsed_ms >= 0 else None),
                retries=self._llm_retries,
                # W5/W6:端点回报了才有的两格实测。record_usage 里 None=未记录、
                # 绝不落 0 —— 0 是"瞬间首字/零思考",NULL 是"端点根本没说"。
                ttft_ms=(ttft if isinstance(ttft, int) and ttft >= 0 else None),
                reasoning_tokens=u.get("reasoning_tokens"),
            )
            if logged:
                self._llm_retries = 0
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
                "turn": rec.get("turn", 0),
                "stale": rec.get("epoch", self._epoch) != self._epoch,
                "drift": self._drifted(rec),
            })
        more = max(0, len(items) - self.CHANGE_VIEW_LIMIT)
        return {"ok": True, "items": items[:self.CHANGE_VIEW_LIMIT],
                "total": len(items), "more": more,
                # 抬头那句不是免责声明,是这一屏最容易骗人的一处:bash/py_script
                # 改出来的东西压根不在快照里,点了"全部回退"也退不掉它们。
                "note": ("只列我用 write_file / edit_file 改的文件;"
                         "命令(bash / py_script)与外部程序改的不在这里,也退不掉。"),
                "hint": ("没有可退的改动 —— 这份栈在 /clear 与换会话时会清空;"
                         "压缩历史不清它(文件确实还被改着),但压过一次之后"
                         "「第几问」这个坐标就不准了,逐处回退照旧可用。")}

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

    def _rollback_arg(self, ordinal) -> "dict | None":
        """编号合法性:错了回那个错误 dict,能用回 None。preview 与动手共用一把尺。"""
        if isinstance(ordinal, bool) or not str(ordinal).strip().lstrip("-").isdigit():
            return {"ok": False, "reverted": [],
                    "msg": f"错误:ordinal 只接受第几问(整数),收到 {ordinal!r}。"}
        if int(str(ordinal).strip()) < 1:
            return {"ok": False, "reverted": [],
                    "msg": "错误:第几问要从 1 开始数。"}
        return None

    def _rollback_scope(self, want: int) -> "tuple[list[dict], list[dict]]":
        """(第 want 问及之后改过的记录, 其中属于别的纪元的)。

        这份筛选只有一处:`rollback_preview`(给人看的那一屏)与
        `rollback_to_ordinal`(真动手)必须说同一个数 —— 两处各筛一遍,
        总有一天一处在预览里说"2 处",另一处退完说"没东西可退"。
        """
        in_scope = [r for r in self._undo_stack if r.get("turn", 0) >= want]
        return in_scope, [r for r in in_scope
                          if r.get("epoch", self._epoch) != self._epoch]

    def rollback_preview(self, ordinal) -> dict:
        """"退到第 N 问之前"会退掉哪些文件 —— 只算,**一个字节都不动**。

        存在的理由:动手面前得让人看见要动什么(设计稿 §四.3 那条规矩在
        命令行 /rewind 上的落地)。这里不复制筛选逻辑,只读 `_rollback_scope`。
        """
        bad = self._rollback_arg(ordinal)
        if bad:
            return {"ok": False, "count": 0, "paths": [], "stale": 0,
                    "msg": bad["msg"]}
        want = int(str(ordinal).strip())
        in_scope, stale = self._rollback_scope(want)
        paths = [self._change_display(r["path"]) for r in
                 sorted(in_scope, key=lambda r: -r["seq"])]
        return {"ok": True, "ordinal": want, "count": len(in_scope),
                "paths": paths[:5], "more": max(0, len(paths) - 5),
                "stale": len(stale),
                "kinds": [(("新建" if r["data"] is None else "改")) for r in
                          sorted(in_scope, key=lambda r: -r["seq"])][:5],
                "msg": (f"第 {want} 问之后没有我用写文件工具改过的东西"
                        "(命令与外部程序改的一直不在这份账里)。" if not in_scope else "")}

    def rollback_to_ordinal(self, ordinal) -> dict:
        """R3:把「第 ordinal 问之后我改过的文件」全部退回,给编辑重发用。

        为什么需要纪元这一道闸:回合坐标是"第几问",而压缩会把前面的提问折进摘要 ——
        同一个"第 3 问"在压缩后指的是另一句话。拿错的号去退文件,退的可能是别人那几问
        的成果,而且这是**动手面**,错了不是一句错话而是丢东西。所以宁可拒绝:
        让界面改用「本轮改动」那一栏逐处退(那里退的是具体某一处,不需要坐标)。

        退的顺序与 `undo_all` 一致(最新→最旧),同一文件退到最早那份前像;
        只动 write_file / edit_file 那本账 —— 命令改的从来不在里面,这句必须说出口。
        """
        bad = self._rollback_arg(ordinal)
        if bad:
            return bad
        want = int(str(ordinal).strip())
        in_scope, _stale = self._rollback_scope(want)
        if not in_scope:
            return {"ok": True, "reverted": [], "kept": len(self._undo_stack),
                    "msg": f"第 {want} 问之后没有我用写文件工具改过的东西"
                           "(命令与外部程序改的一直不在这份账里)。"}
        stale = self._rollback_scope(want)[1]
        if stale:
            return {"ok": False, "reverted": [], "stale": len(stale),
                    "msg": (f"这中间压过一次历史:第 {want} 问的位置已经对不上了,"
                            f"有 {len(stale)} 处改动我不敢按号退 —— 一个字都没动。"
                            "要退就用「任务监控 → 本轮改动」逐处退(那里退具体某一处,不需要编号)。")}
        done, failed = [], []
        for rec in sorted(in_scope, key=lambda r: -r["seq"]):
            if rec not in self._undo_stack:
                continue
            self._undo_stack.remove(rec)
            out = self._apply_undo(rec)
            if out.get("failed"):
                self._undo_stack.append(rec)
                failed.append(out["path"])
            else:
                done.append(out["path"])
        names = "、".join(done[:5]) + (f" 等 {len(done)} 处" if len(done) > 5 else "")
        tail = (f"另有 {len(failed)} 处写不回去(仍在栈里:{'、'.join(failed[:3])})。"
                if failed else "")
        return {"ok": bool(done), "reverted": done, "failed": failed,
                "kept": len(self._undo_stack),
                "msg": (f"工作区已退回第 {want} 问之前:{names}。" if done
                        else "一处都没退成。") + tail
                + (" 命令与外部程序改的没被退(不在账里)。" if done else "")}

    # ---------------------------------------------------------------- 工具表

    @property
    def tools_tokens(self) -> int:
        """工具 JSON Schema 那份占用 —— 随注册表现算,不存副本。

        每轮请求都把这些定义连着消息一起发出去,量窗口不能只数消息(v0.9.40 D2)。
        存过副本的代价:MCP 热重载往注册表里并新工具(gui_bridge)、对外模式把注册表
        收窄成只读那一档(web_profile),两处都只换列表、没有重算那个数 —— 于是压缩
        触发器按换之前的清单算,面板按换之后的算,同一个会话两个"占用"。
        现算一次实测 1.8ms(本轮注册表那份数量下量的),而它每轮只被读两三次,不值得为它维护副本。
        """
        return schema_tokens(self.registry)

    def refresh_registry(self) -> None:
        """注册表被就地换过内容之后,重新对齐"模型真能调用的那批工具"。

        热重载只改 `registry`:新工具会出现在发给模型的定义清单里,却在 `_execute`
        查不到,表现是「它说有这个工具、一用就说找不到」。
        """
        self._tool_map = {t.name: t for t in self.registry}

    # ------------------------------------------------------------ 压缩

    # 实测下限能用多久:同一轮里消息只会多出几条(工具结果 + 助手那句话)。
    # 差得远就说明中间换过一份对话(/resume)或压过一次 —— 那时那个数属于**另一段**
    # 上下文,拿它当底线会凭空触发一次真实的摘要调用(花额度、改留档),比估不准更糟。
    REAL_FLOOR_WINDOW = 8

    def context_pair(self) -> tuple[int, str]:
        """(这次量出来的 token 数, 这个数的来源) —— 一次读数,两个值一起拿。

        拆成两个方法调用会各量一遍,中间消息序列可能已经变了:面板于是显示
        "实测 12,345"却配了一个"本地估"的来源标签。要给界面的两个数出自同一次读数。

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
        """这一次请求真正要发出去的东西占多少 token:消息 + 几十只工具的定义。

        只数消息会系统性低估几千 token,于是阈值偏晚触发,把真实窗口顶到脸上才想起
        来压(v0.9.40 D2)。CLI 的 `/history` 与任务面板用的是这同一个数。
        """
        return self.context_pair()[0]

    def context_source(self) -> str:
        """这把尺子这次是"实测顶上来的"还是"本地估的" —— 报出去要说得出来源。"""
        return self.context_pair()[1]

    def compact_now(self) -> bool:
        """立刻压一次,返回**是否真的压掉了**(False = 没得压 / 这次摘要调用失败)。

        返回值是给界面的:按钮按 `ok` 决定是刷新数字还是把原因写在屏上,
        压完不回话就等于让用户以为"点了没反应是它坏了"。
        """
        return self._maybe_compact(force=True)

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
        # 前面的用户消息被折进摘要了 —— 快照上记的"第几问"从此对不上,别再拿它回退。
        self._epoch += 1
        self.ui.compacted(before, len(new_msgs))
        return True

    # ------------------------------------------------------------------

    def _execute(self, call: ToolCall) -> str:
        # W10:每步先清零 —— 早退路径(工具不存在/参数坏/被拒/钩子拦)不残留
        # 上一步的实测,add_tool_result 拿到 None 就不带 elapsed_ms 键。
        self._step_ms = None
        self._hook_ms = None      # 同上:这一步没跑钩子就不带 hook_ms

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

        # ---- H1(v0.9.115):pre 钩子挪到权限门**之前**。
        # 理由只有一条:钩子现在能改写这一步的参数,而门看的必须是最终真要执行
        # 的那份 —— 否则"用户点过头的内容"和"被执行的内容"就是两回事。
        # 没认领的钩子时 `matched` 为假,下面整块跳过:门的调用次数、回话、卡片
        # 全与基线逐字节相同(方案 §五.1 的第一条口径)。
        hook_ctx = ""
        rewrite_note = ""
        ask_extra = ""          # H3:钩子代答过一次"允许"就要一路跟到回话里
        if self._matched_config_hook("pre_tool", tool, args):
            # 硬拒根本不叫钩子(方案 H1 红线②):先只读跑一次门只为拿这个判断。
            # check() 自己会清 ask_note/project_allow_reason,重复调没有副作用。
            if self.gate.check(tool, args) == REJECT:
                self.ui.tool_rejected_hard(tool, args)
                return HARD_REJECT_NOTE
            pre = self._run_config_hook("pre_tool", tool, args)
            if pre is not None:
                self._hook_ms = int(pre.get("elapsed_ms") or 0)
                self._note_hook_health("pre_tool", tool.name, pre)
                if pre.get("blocked"):
                    return self._hook_blocked_text(pre)
                hook_ctx = str(pre.get("context") or "")
                original = dict(args)
                args, rewrite_note = self._apply_hook_input(tool, args, pre)
                if rewrite_note:
                    self._log_hook_rewrite(tool, args, original, rewrite_note)

        decision = self.gate.check(tool, args)
        if decision == REJECT:
            self.ui.tool_rejected_hard(tool, args)
            return HARD_REJECT_NOTE

        if decision == ASK:
            note = getattr(self.gate, "ask_note", "") or ""
            kind = getattr(self.gate, "ask_kind", "") or ""
            # ---- H3(v0.9.115):弹框之前,给 permission_request 钩子一次表态的机会。
            # 三类红线级的门不进来(见 NON_DELEGABLE_ASKS);钩子没跑成也不进来
            # —— 失败的归宿是"照旧问人",既不替人拒也不替人放。
            answer = self._ask_via_permission_hook(tool, args, kind)
            if answer and answer[0] == "deny":
                return ("错误: 这一步被你配的 permission_request 钩子拒了 —— "
                        f"**不是用户拒的**,用户根本没看到这一问。钩子给的理由:"
                        f"{answer[1] or '(没写)'}。请如实告诉用户是钩子拦的(别把它说成"
                        "用户的决定),并问他要不要改那条钩子或换个做法。")
            if not answer:
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
            else:
                # 「允许」只这一条、只这一回合:不落盘、不升 always —— 钩子替用户
                # 点一次头不等于替他签一份长期授权。工作区外路径仍要记本回合。
                self.gate.approve_outside(tool, args, session=False)
                ask_extra = ("(这一步本该弹确认框,由你配的 permission_request 钩子答了"
                             "\"允许\"(%s 那条门)—— **用户没有点过头**,别把这次放行说成用户批准的。)"
                             % kind)


        # 项目白名单放行(v0.9.87):用户当场没点过头的一步必须留两处痕 ——
        # 安全日志一行 + 界面一条 info。静默放行等于没有审计。
        by_project = getattr(self.gate, "project_allow_reason", "") or ""
        if by_project:
            self._note_project_allow(tool, args, by_project)

        self._note_low_rated_prior(tool, args)
        # pre 钩子已在权限门之前跑过(H1,v0.9.115)。这里只剩一件事要守:
        # 改写过的参数必须在快照之前定稿 —— 快照、卡片、handler 拿到的是同一份,
        # 而"原本要做什么"落在安全日志里(_log_hook_rewrite),不是丢了。
        self._snapshot_before_mutation(tool, args)
        self.ui.tool_start(tool, args)
        t0 = time.monotonic()
        try:
            result = tool.handler(self.ctx, **args)
        except TypeError as e:
            result = f"错误: 工具参数与 schema 不匹配: {e}"
        except Exception as e:
            result = f"工具执行出错: {type(e).__name__}: {e}"
        # W10:这一步的实测耗时(handler 全程,含异常路径 —— 抛错也是真实
        # 耗时)。口径与实时表一致(tool_start→tool_done);确认框等待不算
        # (handler 还没跑),与实时表同一条规矩。
        self._step_ms = max(0, int((time.monotonic() - t0) * 1000))
        self.ui.tool_done(tool, result)
        self._mark_after_mutation(tool, args, result)
        # post 钩子:失败不拦截,在结果尾部如实附一行(一行,不刷屏)
        post = self._run_config_hook("post_tool", tool, args, result=result)
        post_ctx = ""
        if post is not None:
            self._hook_ms = (self._hook_ms or 0) + int(post.get("elapsed_ms") or 0)
            self._note_hook_health("post_tool", tool.name, post)
            if not post.get("ok"):
                result = result + f"\n(工具钩子 post_tool 失败:{post.get('output') or '原因不明'})"
            # 钩子要回灌的正文(lint/测试的失败原文)**追加**在结果里,不替换 ——
            # 原结果被吞掉就是真没了,这个项目不写"我把它换成了别的"这种账。
            # 放在 `_truncate` **之前**:绕过它等于给一条外部命令开了无上限的出口。
            # 副作用是它可能一起被围栏包住(对 web_crawl 那类结果)—— 宁可把自家
            # 附注多标一层"外部内容",也不让无上限的正文逃过截断。
            body = str(post.get("updated_output") or "")
            if body:
                result = result + f"\n〔post_tool 钩子回灌〕\n{body}"
            post_ctx = str(post.get("context") or "")
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
        # 两句钩子附注留在围栏**之外**:它们是我方本地附注,不该被标成外部内容;
        # 而改写那一句必须活着到模型面前 —— 它被截掉等于用户点过头的事没人告知。
        for extra in (post_ctx, hook_ctx, rewrite_note, ask_extra):
            if extra:
                result = result + ("\n" + extra if extra.startswith("(") else
                                   f"\n(工具钩子附注:{extra})")
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
                                 "display": raw, "at": time.time(), "post": None,
                                 "turn": self._turn_ordinal, "epoch": self._epoch})
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

    def _note_daily_budget(self) -> None:
        """C1(v0.9.108):日预算接近 80%/达到 100% 时各提醒一次,当天不重复。

        提醒走 ui.push("info")(与 token_limit 同一家族);账记在实例内存
        —— 重启后再达阈值会再提醒一次,那是可接受的(进程内去重,不落盘
        不新增账本)。读不到用量(库损坏)就静默:预算是提醒,不是闸门。
        """
        cfg = getattr(self.ctx, "config", None)
        limit = int(getattr(cfg, "daily_token_budget", 0) or 0)
        if limit <= 0:
            return
        if getattr(self, "_budget_noted", None) is None:
            self._budget_noted = set()   # 进程内去重:同一档位当天只提醒一次
        from minicode import usage
        st = usage.budget_state(limit)
        if not st.get("enabled"):
            return
        level = st.get("level")
        if level not in ("near", "over") or level in self._budget_noted:
            return
        self._budget_noted.add(level)
        spent, remaining = st.get("spent", 0), st.get("remaining", 0)
        msg = (f"日预算提醒:今天已花 {spent:,} tokens,达到预算 {limit:,} 的"
               f"{int(st.get('ratio', 0) * 100)}%。"
               + (f"已超支 {spent - limit:,}。" if level == "over"
                  else f"还剩 {remaining:,}。"))
        try:
            self.ui.push("info", text=msg)
        except Exception:
            pass

    def _fallback_provider(self) -> str:
        """C2(v0.9.108):主供应商 401/404/403 时的备选 —— 第一个「非活动、
        有可用 Key」的供应商;没有就空串(不回落)。**只挑不切**:真切换在
        llm.stream_chat 里,且切换必须经 on_fallback 说出口。"""
        cfg = getattr(self.ctx, "config", None)
        if cfg is None:
            return ""
        active = cfg.active_provider_id
        for p in cfg.providers:
            if p.id != active and p.has_key and p.enabled and p.enabled_models():
                return p.id
        return ""

    def _fire_start_hooks(self, user_input: str, add_user: bool) -> None:
        """`session_start`(一次会话一回)与 `user_prompt_submit`(一问一回)—— H4。

        两样只做一件事:往 `_pending_prior` 那条**只活在这次请求里**的通道**追加**
        一句话。为什么不写进 system prompt:①system 是整段 KV Cache 前缀,而
        `_refresh_clock` 会按天/按指令指纹重建它 —— 钩子那句一旦被重建掉,表现是
        "我配的话有时生效有时不生效",比根本不生效难查十倍;②system 在留档里,
        会话正文中间冒出一段没人承认写过的话,不好看。
        **代价照实说**:这一句只到下一次请求为止,**不是常驻上下文** —— 与 Claude 的
        SessionStart(整段会话都带着)不同,这里不假装等价。要常驻得先解决"谁能往
        system prompt 里说话"这笔账,不在这一格。
        """
        if self._is_sub:
            return
        cfg = getattr(getattr(self, "ctx", None), "config", None)
        cfg_hooks = getattr(cfg, "hooks", None)
        if not isinstance(cfg_hooks, dict) or not cfg_hooks:
            return
        if not self._started:
            self._started = True
            self._collect_event_hook("session_start", "", cfg_hooks)
        # 「重新生成」(add_user=False)不算用户又开了一次口:同一句话叫两遍
        # user_prompt_submit,钩子会以为自己收到了两条新指令。
        if add_user:
            self._collect_event_hook("user_prompt_submit", str(user_input or ""), cfg_hooks)

    def _collect_event_hook(self, kind: str, text: str, cfg_hooks: dict) -> None:
        """跑一类事件钩子,只收它要附的那句话。它不是闸:坏了、超时、拦不住都照跑这一轮。"""
        from minicode import hooks
        payload = {"event": kind}
        if kind == "user_prompt_submit":
            payload["prompt"] = text
        try:
            got = hooks.run_hook(kind, cfg_hooks, "", args=payload)
        except Exception:
            return                       # 附不上话就算了,不许因为读配置断掉这一轮
        if got is None:
            return
        self._note_hook_health(kind, "", got)
        bits = [b for b in (str(got.get("context") or ""),
                            "" if got.get("ok") else str(got.get("output") or "")) if b]
        if not bits:
            return
        line = "〔%s 钩子〕%s" % (kind, " / ".join(bits))
        # **追加**而不是赋值:`_pending_prior` 另有住户(差评避让那条线在
        # `_note_low_rated_prior` 里写它),谁覆盖谁就有一句会静默消失。
        self._pending_prior = (self._pending_prior + "\n" + line).strip()

    def _matched_config_hook(self, kind: str, tool, args: dict) -> bool:
        """这一步会不会跑某条钩子 —— **不启动进程**,只为红线预检服务。"""
        cfg = getattr(getattr(self, "ctx", None), "config", None)
        cfg_hooks = getattr(cfg, "hooks", None)
        if not isinstance(cfg_hooks, dict):
            return False
        from minicode import hooks
        try:
            return hooks.matched(kind, cfg_hooks, tool.name, args)
        except Exception:
            return False      # 预检判不了就当没有钩子:门照样会在后面跑第二遍

    def _hook_blocked_text(self, pre: dict) -> str:
        """pre 拦下这一步时回给模型的话。

        两种拦下要分开说:钩子**声明拒绝**(exit 2 或 decision:deny)与钩子**没跑成**
        (非零退出/超时,on_error=block)。前者用户该问的是"这一步为什么要换做法",
        后者该问的是"你那条钩子坏了" —— 混成一句,他就只能两条都试。
        """
        out = pre.get("output") or "(无输出)"
        if pre.get("declared"):
            return (f"错误: 该步被工具钩子拦下(pre_tool 声明拒绝)。"
                    f"钩子给的理由:{out}")
        return (f"错误: 该步被工具钩子拦下(pre_tool 没跑成,按 on_error=block 处理)。"
                f"钩子情况:{out}")

    def _apply_hook_input(self, tool, args: dict,
                          pre: dict) -> "tuple[dict, str]":
        """采纳钩子对这一步参数的改写。返回(要执行的参数, 那句要留痕的话)。

        只查"这条调用本来就该满足的形状"(必填齐、键都在这只工具的 schema 里),
        **不查业务合法性、不查权限** —— 那是 handler 与 `gate.check` 的事,在这里
        复刻第二套判定迟早跟门对不上(方案 H1 契约红线①:门必须看最终参数,
        所以这里只换 `args`,下面那道门照原样再跑一遍)。
        """
        new = pre.get("updated_input")
        if not isinstance(new, dict) or not new:
            return args, ""
        schema = tool.parameters or {}
        props = schema.get("properties") or {}
        required = [str(k) for k in (schema.get("required") or [])]
        missing = [k for k in required if k not in new]
        unknown = [k for k in new if props and k not in props]
        if missing or unknown:
            bits = []
            if missing:
                bits.append("少了必填的 " + "、".join(missing))
            if unknown:
                bits.append("多出这只工具没有的参数 " + "、".join(sorted(unknown)))
            return args, ("(pre_tool 钩子想改写这一步的参数,但那份参数不合这只工具的"
                          "参数形状:" + "、".join(bits) + "。这一步按原参数执行。)")
        if new == args:
            return args, ""
        keys = sorted(k for k in set(new) | set(args) if new.get(k) != args.get(k))
        return new, ("(这一步的参数由 pre_tool 钩子改写,改动项:"
                     + "、".join(keys) + "。原始参数已记入安全日志。)")

    def _log_hook_rewrite(self, tool, new_args: dict, old_args: dict,
                          note: str) -> None:
        """改写留痕的第二处(第一处是卡片画的是改写后的参数,第三处是模型回话)。

        走 `provenance.log_escalation` 同一个方 —— 它本来就是"敏感动作事后线索"
        那本账(方案 M6),再开一份钩子专记迟早两本账对不上。写失败静默:
        留痕不能拖垮对话。
        """
        try:
            from minicode import provenance
            provenance.log_escalation(
                "hook_rewrite", tool.name, new_args,
                f"{note} 原参数:"
                f"{json.dumps(old_args, ensure_ascii=False)[:400]}",
                "applied")
        except Exception:
            pass

    def _ask_via_permission_hook(self, tool, args: dict, kind: str) -> "tuple[str, str] | None":
        """问人之前先问一句钩子(H3,v0.9.115)。回 ("allow"|"deny", 理由) 或 None=照常弹框。

        三道闸门,少一道就做出一个"机器替人点头"的产品:

        ① `NON_DELEGABLE_ASKS` 那三类门**根本不叫钩子** —— 凭据、仓库 `.git`、
           刚接触过外部内容的升格确认。它们的共同点正是各自注释里那句"权限档位、
           项目白名单、本轮总是允许都不替它背书";而钩子读得到的输入里就包含这一步
           要做的事,那条命令的输出又可能受外部内容影响 —— 最后一道换成 stdout
           的一个字,注入防御就只剩纸了。
        ② 只有钩子**真跑成并明确表过态**才算数(`declared`)。没跑成、超时、
           退出码既不是 0 也不是 2、JSON 里没有 decision —— 一律退回问人。
           这一类与 pre/post 最大的不同:`on_error` 在这里**不适用**,失败的归宿
           既不是拦也不是放,而是"照旧弹框"。一条坏掉的策略钩子既不配替人拒绝、
           更不配替人放行。
        ③ 代答必留痕:安全日志一行(`provenance.log_escalation`,不开第二本账)
           + 回给模型那句"用户没有点过头"。少了第③处,事后就查不到这一步是谁应的。
        """
        if kind in NON_DELEGABLE_ASKS:
            return None
        if not self._matched_config_hook("permission_request", tool, args):
            return None
        got = self._run_config_hook("permission_request", tool, args)
        if got is None:
            return None
        self._hook_ms = (self._hook_ms or 0) + int(got.get("elapsed_ms") or 0)
        self._note_hook_health("permission_request", tool.name, got)
        decision = str(got.get("decision") or "")
        if not got.get("declared") or decision not in ("allow", "deny"):
            return None
        reason = str(got.get("output") or "")
        try:
            from minicode import provenance
            provenance.log_escalation(
                "hook_answer", tool.name, args,
                f"门:{kind or '(按档位)'} 钩子代答:{decision} 理由:{reason[:200]}",
                "yes" if decision == "allow" else "no")
        except Exception:
            pass
        return decision, reason

    def _note_hook_health(self, kind: str, tool_name: str, got: dict) -> None:
        """同一只钩子连着三次没跑成 → 提醒用户一次,然后清零。

        只算"没跑成"(`declared` 为假的那种):一条真在拒绝的钩子工作得好好的,
        把它算进"坏了"三次就叫用户去修,那是骗人。提醒完**不改变任何判定** ——
        拦还是拦、放还是放,由 on_error 说;这里只是把这件事说到用户听得见的地方。
        """
        if got.get("ok") or got.get("declared"):
            self._hook_bad.pop(kind, None)
            return
        n = self._hook_bad.get(kind, 0) + 1
        self._hook_bad[kind] = n
        if n < HOOK_BAD_STREAK:
            return
        self._hook_bad[kind] = 0
        try:
            self.ui.info(f"你配的工具钩子({kind})连着 {HOOK_BAD_STREAK} 次没跑成,"
                         f"最近一次:{str(got.get('output') or '')[:160]}。"
                         "它没跑成时按 on_error 办(默认拦下),该修的那条在 config.json 的 hooks 里")
        except Exception:
            pass

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
