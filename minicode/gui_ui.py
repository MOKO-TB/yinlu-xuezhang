"""GUI 适配器:实现引擎的 UI 协议(Agent 依赖的那组方法),
把事件推送到 webview 前端;停止按钮通过抛 KeyboardInterrupt 中断回合。

与 TerminalUI 同接口,因此 Agent 无需感知自己跑在终端还是窗口里。
"""
from __future__ import annotations

import json
import threading

from minicode.tools.base import Tool


class GuiUI:
    def __init__(self):
        self.window = None                # webview.Window,启动后注入
        self.stop_requested = False       # 停止按钮:在下一个输出增量处抛中断
        self.confirm_handler = None       # Bridge.confirm_tool,由 create_app 注入
        self._send_lock = threading.Lock()

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
        self.push("tool_start", name=tool.name, summary=self._arg_summary(tool, args))

    def tool_done(self, tool: Tool, result: str) -> None:
        failed = result.startswith(("错误", "工具执行出错", "用户拒绝"))
        self.push("tool_done", ok=not failed,
                  error_preview="" if not failed else result[:300])

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
        self.push("error", text=f"模型调用失败: {e} {hint}")

    def max_steps_reached(self, limit: int) -> None:
        self.push("info", text=f"本回合工具调用已达 {limit} 轮上限,强制停止")

    def compacted(self, before: int, after: int) -> None:
        self.push("compacted", before=before, after=after)

    def info(self, text: str) -> None:
        self.push("info", text=text)

    def error(self, text: str) -> None:
        self.push("error", text=text)

    def confirm_tool(self, tool: Tool, args: dict, reason: str = "") -> str:
        """弹确认框并等待用户选择;由 Bridge 注入实现。reason=为什么要做这一步。"""
        if self.confirm_handler is None:
            self.push("error", text="确认通道未就绪,已自动拒绝")
            return "no"
        return self.confirm_handler(tool, args, reason)

    @staticmethod
    def _arg_summary(tool: Tool, args: dict) -> str:
        name = tool.name.rsplit("·", 1)[-1]      # 子代理的工具名带类型前缀
        if name == "bash":
            s = str(args.get("command", ""))
        elif name in ("write_file", "edit_file", "read_file"):
            s = str(args.get("path", ""))
        elif name == "browser_open":
            s = str(args.get("url", ""))         # 卡片上直接看到要开哪个网址
        elif name == "browser_eval":
            s = "JS: " + " ".join(str(args.get("js", "")).split())
        elif name in ("browser_screenshot", "browser_close", "browser_status"):
            s = str(args.get("path", "")) or "当前页面"
        else:
            s = "  ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
        return s[:160]
