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


class GuiUI:
    def __init__(self):
        self.window = None                # webview.Window,启动后注入
        self.stop_requested = False       # 停止按钮:在下一个输出增量处抛中断
        self.confirm_handler = None       # Bridge.confirm_tool,由 create_app 注入
        self.on_llm_failure = None        # 模型的看门狗,由桥注入(见 GuiUI.llm_error)
        self._send_lock = threading.Lock()
        # ---- 实时表(v0.9.37):每个工具调用的**真实**开始/结束时刻。
        # 会话消息里没有时间戳(留档只有会话开始那一行),所以"跑了多久"这件事
        # 只有在这里记一次才可能是真的。记不到的行(并行子代理被 _QuietUI 吞掉的、
        # 上一版程序记的)一律不给时间 —— 面板宁可少说,不许编。
        self._live: list[dict] = []
        self._live_open: list[dict] = []   # 栈:子代理的工具套在外面那层里面
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
        self.push("info", text=f"请求失败({type(e).__name__}),第 {attempt} 次重试…")

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
               "start": time.time(), "end": None, "ok": None}
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
        """此刻真正在跑的最深一层 + **真实**已耗时;没有在跑给 None。"""
        with self._live_lock:
            if not self._live_open:
                return None
            row = dict(self._live_open[-1])
        row["elapsed"] = max(0, int(time.time() - float(row["start"])))
        return row

    def live_reset(self) -> None:
        """清空实时表:清空会话/换会话时调,别让上一段任务的"在跑"挂在这一栏。"""
        with self._live_lock:
            self._live = []
            self._live_open = []

    def live_abandon(self) -> int:
        """回合结束时收掉还开着的行(停止键 / 中断会让 `tool_done` 不被调用)。

        不这么做的话「此刻在跑」会永远挂着那一步:用户明明按了停止,监控栏还在
        转圈 —— 这比没有时间更糟,因为它说的是一件正在发生而其实没发生的事。
        """
        now = time.time()
        with self._live_lock:
            n = len(self._live_open)
            for row in self._live_open:
                row["end"] = now
                row["ok"] = False
                row["abandoned"] = True
            self._live_open = []
        return n
