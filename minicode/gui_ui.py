"""GUI 适配器:实现引擎的 UI 协议(Agent 依赖的那组方法),
把事件推送到 webview 前端;停止按钮通过抛 KeyboardInterrupt 中断回合。

与 TerminalUI 同接口,因此 Agent 无需感知自己跑在终端还是窗口里。
"""
from __future__ import annotations

import json
import threading
import time

from minicode import toolsum
from minicode.tools.base import Tool

LIVE_CAP = 200            # 实时表最多记多少条(只丢已完成的最老行,在跑的永不丢)
CHILD_CAP = 40            # 一路子代理最多往父行下挂几步(超了丢最老的,并如实报丢了几个)


class GuiUI:
    def __init__(self):
        self.window = None                # webview.Window,启动后注入
        self.stop_requested = False       # 停止按钮:在下一个输出增量处抛中断
        self.confirm_handler = None       # Bridge.confirm_tool,由 create_app 注入
        self.ask_handler = None           # Bridge.ask_user,同一条注入形状(v0.9.96)
        # Bridge.plan_review:计划核对单那一条通道,与提问分家(v0.9.114)
        self.plan_review_handler = None
        self.abandon_pending = None       # 停止/关窗时叫醒正在等回答的那两条线程
        self.on_llm_failure = None        # 模型的看门狗,由桥注入(见 GuiUI.llm_error)
        # W1:本会话模型层重试的**事件数**(每 on_retry 一次 +1)。每会话一份
        # (SessionGuiUI 各自实例化),是本程序这次开着期间的账 —— 重启清零,
        # 监控栏据此如实说,不假装翻得出历史。落库的那份按请求记,在 agent 层。
        self._retries = 0
        self._send_lock = threading.Lock()
        # ---- 实时表(v0.9.37):每个工具调用的**真实**开始/结束时刻。
        # 会话消息里没有时间戳(留档只有会话开始那一行),所以"跑了多久"这件事
        # 只有在这里记一次才可能是真的。记不到的行(并行子代理被 _QuietUI 吞掉的、
        # 上一版程序记的)一律不给时间 —— 面板宁可少说,不许编。
        self._live: list[dict] = []
        self._live_open: list[dict] = []   # 栈:子代理的工具套在外面那层里面
        # 每一路子代理当前开着的那一步(键=路标签)。一路同一时刻只跑一个工具,
        # 所以这一个键就够 —— 不需要每路一个栈,那会把"嵌套"演成真的复杂。
        self._child_open: dict[str, dict] = {}
        self._live_lock = threading.Lock()

    def attach(self, window) -> None:
        self.window = window

    # ------------------------------------------------------------ 推送

    def push(self, type_: str, **payload) -> None:
        payload["type"] = type_
        data = json.dumps(payload, ensure_ascii=False)
        with self._send_lock:
            if self.window is not None:
                try:
                    self.window.evaluate_js(f"window.__gcodeEvent({data})")
                except Exception:
                    pass  # 窗口关闭中/未就绪时丢弃事件

    def request_stop(self) -> None:
        self.stop_requested = True
        # 光立旗子叫醒不了正在等人点按钮的那两条线程 —— 它们卡在 `event.wait()`,
        # 而旗子只有 `_check_stop` 会看。用户按了停止却还挂着确认框/提问卡,
        # 表现是"停了,但学长再也不问东西了"(锁在后面那条线程手里)。
        wake = self.abandon_pending
        if callable(wake):
            try:
                wake("你按了停止")
            except Exception:
                pass        # 叫醒失败绝不能把停止本身弄坏

    def ask_user(self, questions: list[dict], note: str = "",
                 label: str = "") -> dict:
        """弹一张提问卡并等用户回答(v0.9.96);由 Bridge 注入实现。

        没接上通道时回 `unanswered` 而不是抛异常:调用方(工具)拿到的是
        "没问到"这个事实,它会把这句原样带回模型上下文 —— 比在子代理里
        AttributeError 好,也比凭空造一个"用户同意了"诚实。
        """
        if self.ask_handler is None:
            return {"mode": "unanswered", "reason": "这个界面没接提问通道"}
        return self.ask_handler(questions, note, label)

    def plan_review(self, plan: dict) -> dict:
        """弹一张**整单核对单**并等人点(批准 / 改一条 / 重排,v0.9.114)。

        与上面那条 `ask_user` 分家不是重复建设:那是"问路"(这一步该怎么走),
        这是"批单"(这一整张单子要不要照做)。共用一条通道,界面就没法说清
        "此刻等你的是一个问题还是一张计划"。
        没接上通道(命令行、网页版、桥没注入)回 `unanswered` —— 调用方据此
        放行或改口,绝不凭空造一个"用户批准了"。
        """
        if self.plan_review_handler is None:
            return {"mode": "unanswered", "reason": "这个界面没接计划核对单"}
        return self.plan_review_handler(plan)

    def _check_stop(self) -> None:
        if self.stop_requested:
            self.stop_requested = False
            raise KeyboardInterrupt

    # --------------------------------------------------- Agent 依赖的接口

    def on_text_delta(self, delta: str) -> None:
        self._check_stop()
        self.push("text_delta", text=delta)

    def on_reasoning_delta(self, text: str) -> None:
        self._check_stop()
        self.push("reasoning_delta", text=text)

    def finish_stream(self) -> None:
        self.push("finish_stream")

    def tool_start(self, tool: Tool, args: dict) -> None:
        summary = self._arg_summary(tool, args)
        # 先记账再推事件:窗口正关着的时候 push 会被吞掉,但那一步真的在跑,
        # 监控栏下一次自己扫表就该看得见它。
        self._live_begin(tool.name, summary)
        self.push("tool_start", name=tool.name, summary=summary,
                  trust=getattr(tool, "trust", "local"))

    def tool_progress(self, text: str) -> None:
        """长命令的实时进度:**只改"此刻"那一行**,不往聊天里叠新消息。

        为什么走实时表而不是另开一份状态:`live_now()` 已经是"最深一层在跑什么"的唯一
        来源(耗时也是从它算的)。进度塞进同一行,面板刷新时不用猜"这行字属于哪一步";
        而那一步一结束,行从 `_live_open` 里移除,进度自然跟着消失 —— 不会留下一条
        "还在跑"的假象(与 `live_abandon` 那条是同一个顾虑)。
        """
        with self._live_lock:
            if self._live_open:
                self._live_open[-1]["progress"] = str(text or "")
        self.push("tool_progress", text=str(text or ""))

    def tool_done(self, tool: Tool, result: str) -> None:
        # 成没成、截多长都由 toolsum 一处说了算(原来这两行各写了一份字面量:
        # 一份 startswith 元组、一个 [:300],与 activity 那份是三份真相)
        failed = toolsum.is_failure(result)
        self._live_end(tool.name, not failed)
        self.push("tool_done", ok=not failed,
                  error_preview=toolsum.error_preview(result))

    def tool_rejected_hard(self, tool: Tool, args: dict) -> None:
        self.push("hard_reject", command=str(args.get("command", "")))

    def on_retry(self, e: Exception, attempt: int) -> None:
        # 见 ui.on_retry 的同一口径:「已切到备选」「摘掉某字段」是整句话,只报类型等于没说。
        self._retries += 1
        note = " ".join(str(e).split())[:160]
        what = f"{type(e).__name__}: {note}" if note else type(e).__name__
        self.push("info", text=f"请求失败({what}),第 {attempt} 次重试…")

    def retry_count(self) -> int:
        """本会话重试事件数(监控栏「翻车排查」那格读的就是它)。"""
        return self._retries

    def turn_interrupted(self) -> None:
        self.push("finish_stream")
        self.push("info", text="已中断当前回合")

    def llm_error(self, e: Exception) -> None:
        self.push("finish_stream")
        hint = "(请检查 config.json 中的 api_key)" if "401" in str(e) else ""
        # `llm: true` 是给前端的信号:只有"这句根本没送到模型"才该把提问放回输入框。
        # 工具跑到一半才错的情况不是没发出去,放回来说明会误导人。
        self.push("error", text=f"模型调用失败: {e} {hint}", llm=True)
        if self.on_llm_failure is not None:
            try:
                self.on_llm_failure(str(e))
            except Exception:
                pass      # 看门狗自己起不来,不该连带把这条错误提示也弄丢

    def max_steps_reached(self, limit: int) -> None:
        # 与 TerminalUI 同一句口径:只说"停止调用工具",不承诺后面一定有收尾。
        self.push("info", text=f"本回合工具调用已达 {limit} 轮上限,已停止调用工具。"
                               f"想接着做直接说「继续」。")

    def turn_time_limited(self, seconds: int, steps: int) -> None:
        # 走已有的 info 通道:时限到点只是"这一轮先停",不是错误,
        # 用 error 会让前端标红成一次失败。
        self.push("info", text=f"本回合已跑 {steps} 步、超过一轮总时限 {seconds} 秒,"
                               f"已停下(正在执行的那一步不会被强杀)。"
                               f"想接着做直接说「继续」。")

    def fail_streak_limit(self, tool: str, limit: int) -> None:
        # 同两条时限的口径:停的是这一轮的循环,不承诺"已停止了它"。
        # 走 info 不走 error —— 止损是护栏起效,不是一次故障。
        self.push("info", text=f"「{tool}」已连续 {limit} 次以同样方式失败,"
                               f"本轮先停在这里,不再原样重试。"
                               f"让它换个做法,或直接说「继续」。")

    def token_limit_reached(self, limit: int, used: int) -> None:
        # 与两条时限同一口径:停的是这一轮的循环,不是错误。
        self.push("info", text=f"本会话累计消耗约 {used} token,已到 "
                               f"session_token_limit({limit})上限,本轮先停在这里"
                               f"(正在执行的那一步不会被强杀)。"
                               f"可提高上限或新开会话再继续。")

    def compacted(self, before: int, after: int) -> None:
        self.push("compacted", before=before, after=after)

    def info(self, text: str) -> None:
        self.push("info", text=text)

    def error(self, text: str) -> None:
        self.push("error", text=text)

    def confirm_tool(self, tool: Tool, args: dict, reason: str = "",
                     note: str = "", label: str = "") -> str:
        """弹确认框并等待用户选择;由 Bridge 注入实现。reason=为什么要做这一步。

        `label` 是并发子代理路的标签(v0.9.60):原样传给 handler,确认框标题
        带上「第几路[类型]」,用户才分得清是谁在等。
        """
        if self.confirm_handler is None:
            self.push("error", text="确认通道未就绪,已自动拒绝")
            return "no"
        # 旧版注入的 handler 只有三个参数;按签名判断,不能靠 try/except TypeError
        # ——handler 自己抛 TypeError 时会被误吞,然后重复弹一次确认框。
        import inspect
        try:
            arity = len(inspect.signature(self.confirm_handler).parameters)
        except (TypeError, ValueError):
            arity = 3
        if arity >= 5:
            # 绑定方法的 signature 不含 self:5 参 = (tool, args, reason, note, label)
            return self.confirm_handler(tool, args, reason, note, label)
        if arity >= 4:
            return self.confirm_handler(tool, args, reason, note)
        return self.confirm_handler(tool, args, reason)

    @staticmethod
    def _arg_summary(tool: Tool, args: dict) -> str:
        # 口径收在 toolsum 一处:监控栏的时间线是拿**会话消息里的原始参数**复算
        # 同一句话来对号的,两边各写一份就会对不上(表现为同一次调用两栏说法不同)。
        return toolsum.summarize(tool.name, args)

    # ------------------------------------------------------------ 实时表

    def _live_begin(self, name: str, summary: str) -> None:
        row = {"name": str(name or ""), "summary": str(summary or ""),
               "start": time.time(), "end": None, "ok": None, "progress": ""}
        with self._live_lock:
            self._live.append(row)
            self._live_open.append(row)

    def _live_end(self, name: str, ok: bool) -> None:
        """收栈:优先收同名那层(子代理的工具名带类型前缀,外层是 task)。
        对不上就收最上面一层 —— 宁可时间记在"最后开始的那个"上,也不让栈漏了
        永远收不回:一旦漏了,面板会一直显示"某步在跑"而它其实早就结束了。"""
        now = time.time()
        with self._live_lock:
            stack = self._live_open
            if not stack:
                return
            target = None
            for row in reversed(stack):
                if toolsum.short_name(row["name"]) == toolsum.short_name(name):
                    target = row
                    break
            target = target or stack[-1]
            target["end"] = now
            target["ok"] = bool(ok)
            stack.remove(target)
            # 只丢已完成的最老行:在跑的行丢了面板就再也找不到它了
            if len(self._live) > LIVE_CAP:
                for row in list(self._live):
                    if row["end"] is not None:
                        self._live.remove(row)
                        if len(self._live) <= LIVE_CAP:
                            break

    def live_rows(self) -> list[dict]:
        """实时表快照(按开始先后)。给 activity 的时间线配对用。"""
        with self._live_lock:
            return [dict(r) for r in self._live]

    def live_now(self) -> dict | None:
        """此刻真正在跑的**最深一层** + 真实已耗时;没有在跑给 None。

        v0.9.92:并行批次里子代理内部的每一步也会挂进实时表(挂在父行下面的
        `children` 里),所以"最深一层"要往下钻一层拿 —— 否则「此刻」写着
        「task: 派 6 路」而真正在跑的是第 3 路的 `grep`,那既是假的也帮不上忙。
        """
        with self._live_lock:
            if not self._live_open:
                return None
            row = dict(self._live_open[-1])
            open_kids = [c for c in row.get("children") or [] if c.get("end") is None]
            if open_kids:
                kid = dict(open_kids[-1])
                kid["parent_tool"] = row.get("name")
                row = kid
        row["elapsed"] = max(0, int(time.time() - float(row["start"])))
        return row

    # ------------------------------------------------ 子代理内部步骤的记账通道

    def live_child_begin(self, route: str, name: str, summary: str) -> None:
        """把并行子代理内部的每一步挂在**当时开着的那一行**下面 —— 只记账,不进聊天区。

        为什么要有这条:`_QuietUI` 吞掉每路的 `tool_start`/`tool_done` 是对的
        (几路 `text_delta`/卡片会在全屏一处互相穿插成乱码),但连"它先做了什么、
        哪一路挂了"也一起吞掉,用户拿到的就只有一份合并报告 —— "分别处理"变成了
        看不见过程的黑箱(v0.9.37 就在 §6 #20 ② 记着这笔欠账)。挂成父行的子行,
        既不刷屏,又保住了真实先后与真实时刻。
        """
        with self._live_lock:
            if not self._live_open:
                return                      # 父行都没开着就没处挂:不硬造一行
            parent = self._live_open[-1]
            kids = parent.setdefault("children", [])
            row = {"name": str(name or ""), "summary": str(summary or ""),
                   "route": str(route or ""), "start": time.time(),
                   "end": None, "ok": None, "progress": ""}
            kids.append(row)
            while len(kids) > CHILD_CAP:
                kids.pop(0)
                parent["children_dropped"] = int(parent.get("children_dropped") or 0) + 1
            self._child_open[str(route or "")] = row
        self.push("child_step", state="start", route=row["route"], name=row["name"])

    def live_child_progress(self, route: str, text: str) -> None:
        """长命令的进度落到**它那一路的那一行**上,不再盖住父会话那一行。"""
        with self._live_lock:
            row = self._child_open.get(str(route or ""))
            if row is None:
                return
            row["progress"] = str(text or "")
        self.push("child_step", state="progress", route=str(route or ""),
                  name=row.get("name", ""))

    def live_child_end(self, route: str, ok: bool) -> None:
        """收掉这一路当前开着的那一步。找不到就什么都不做(不拿别的行凑数)。"""
        with self._live_lock:
            row = self._child_open.pop(str(route or ""), None)
            if row is None:
                return
            row["end"] = time.time()
            row["ok"] = bool(ok)
        self.push("child_step", state="end", route=str(route or ""),
                  name=row["name"], ok=bool(ok))

    def live_reset(self) -> None:
        """清空实时表:清空会话/换会话时调,别让上一段任务的"在跑"挂在这一栏。"""
        with self._live_lock:
            self._live = []
            self._live_open = []
            self._child_open = {}

    def live_abandon(self) -> int:
        """回合结束时收掉还开着的行(停止键 / 中断会让 `tool_done` 不被调用)。

        不这么做的话「此刻在跑」会永远挂着那一步:用户明明按了停止,监控栏还在
        转圈 —— 这比没有时间更糟,因为它说的是一件正在发生而其实没发生的事。
        子代理挂在下面的那几步一起收:它们同样不会有 `tool_done`。
        """
        now = time.time()
        with self._live_lock:
            n = len(self._live_open)
            for row in self._live_open:
                row["end"] = now
                row["ok"] = False
                row["abandoned"] = True
                for kid in row.get("children") or []:
                    if kid.get("end") is None:
                        kid["end"] = now
                        kid["ok"] = False
                        kid["abandoned"] = True
                        n += 1
            self._live_open = []
            self._child_open = {}
        return n
