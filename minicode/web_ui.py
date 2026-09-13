"""网页版 UI 适配器:把引擎事件推进队列,由 SSE 端点取走。

与 GuiUI 同接口,所以 Agent 完全不用感知自己在终端、窗口还是浏览器里。
每个访客一个实例;Bridge 会给它再套一层 SessionGuiUI(打上会话标记)。
"""
from __future__ import annotations

import queue

from minicode.gui_ui import GuiUI
from minicode.tools.base import Tool

MAX_QUEUE = 20000        # 单个访客的事件上限:前端读得太慢时丢事件,不阻塞引擎


class WebUI(GuiUI):
    """出站不是 evaluate_js 而是内存队列(SSE 端点从队列里取)。"""

    def __init__(self, on_busy=None, max_queue: int = MAX_QUEUE):
        super().__init__()
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._on_busy = on_busy
        self.closed = False

    # ------------------------------------------------------------ 推送

    def push(self, type_: str, **payload) -> None:
        payload["type"] = type_
        # busy 事件是回合开始/结束的唯一信号,并发计数靠它
        if type_ == "busy" and self._on_busy is not None:
            try:
                self._on_busy(bool(payload.get("value")))
            except Exception:
                pass
        if self.closed:
            return
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            pass    # 丢弃:队列满说明前端卡住了,引擎线程不能跟着卡

    def close(self) -> None:
        """让 SSE 生成器收工(访客被回收时调用)。"""
        self.closed = True
        try:
            self.queue.put_nowait({"type": "__closed__"})
        except queue.Full:
            pass

    # ------------------------------------------------------------ 确认

    def confirm_tool(self, tool: Tool, args: dict, reason: str = "") -> str:
        """对外模式一律拒绝。

        确认通道是「单例 Event + 前端点击唤醒」,在无登录的多访客场景里既没法
        安全配对、一旦没人点就会永久挂住一个线程 —— 所以直接拒绝,绝不放行。
        只读白名单下权限门本来就不会走到这里,这是第二道保险。
        """
        self.push("error", text=f"网页版不提供确认通道,已拒绝 {tool.name}")
        return "no"
