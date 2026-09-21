"""关窗去向 + 系统托盘(v0.9.22)。

点窗口 X 以前等于"退出应用":托盘图标从来没有过(gui_main 的模块头当时就写了
"无系统托盘"),而会话里的后台任务、MCP 子进程都挂在主进程上 —— 手滑一下,
正在跑的任务就没了。现在改成三选一:**最小化到托盘 / 退出应用 / 不再询问**
(参考用户给的截图 p2)。

技术路线与取舍:
- pywebview 的 `window.events.closing` 是**普通事件,不能取消关闭**(实测
  webview/window.py 里 `closing = Event(self, True)`,返回值没人看)。所以要
  在 WinForms 那一层动手:`window.native` 就是 BrowserForm,给它挂
  `FormClosing` 处理,`e.Cancel = True` 才是真正拦得住的开关。
- 拦下之后问谁?对话框用**前端 HTML** 画(样式与全应用一致,还能放
  「不再询问」勾选框)。WinForms 原生 MessageBox 只有固定按钮文案,放不进
  勾选框;而在 UI 线程里弹模态框会自锁(FormClosing 本就跑在 UI 线程)。
- **托盘是藏窗口的前置条件**:没有托盘就藏窗口 = 窗口再也找不回来,只能
  任务管理器杀进程。所以 `_ensure_icon()` 不成功就绝不 hide,直接按退出处理。
- 非 Windows / 缺 pystray / pythonnet 不可用时,整套机制原样退回 v0.9.21
  的行为(X = 退出),并且往 `tray.log` 写一行原因,方便远程定位。
"""
from __future__ import annotations

import json
import threading
import time

from minicode.paths import app_path, resource_path

PREF_FILE = "close-pref.json"
LOG_FILE = "tray.log"

_CONTROLLER = None            # 单窗口应用:一个控制器就够
_LOCK = threading.Lock()


def _ensure_parent(path):
    """数据目录可能还没有文件(装在只读盘、首次运行):先建父目录再写。

    不这么做的话 write_pref 的 OSError 会被静默吞掉,表现为"勾了不再询问
    却没记住"—— 一个几乎不可能被猜到的 bug。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def log(msg: str) -> None:
    """托盘/关窗这条链路只在用户机器上才复现,留一行现场记录比猜有用。"""
    try:
        with _LOCK:
            with open(_ensure_parent(app_path(LOG_FILE)), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ------------------------------------------------------------------ 偏好

def _pref_path():
    return app_path(PREF_FILE)


def read_pref() -> dict:
    """{"ask": bool, "action": "minimize"|"exit"};读不出来就按"每次都问"。"""
    try:
        raw = json.loads(_pref_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ask": True, "action": "minimize"}
    if not isinstance(raw, dict):
        return {"ask": True, "action": "minimize"}
    action = str(raw.get("action") or "minimize")
    return {"ask": raw.get("ask", True) is not False,
            "action": action if action in ("minimize", "exit") else "minimize"}


def write_pref(ask: bool, action: str) -> None:
    payload = {"ask": bool(ask), "action": action if action in ("minimize", "exit")
               else "minimize"}
    try:
        _ensure_parent(_pref_path()).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    except OSError as e:      # 目录只读(装进 Program Files):偏好留不住,但不影响用
        log(f"关闭偏好写入失败: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ 控制器

class TrayController:
    """把 WinForms 的关闭动作接下来,转成"问一句 → 藏进托盘 / 真退出"。"""

    def __init__(self, window, ui) -> None:
        self.window = window
        self.ui = ui
        self.form = None
        self.icon = None
        self.installed = False        # FormClosing 挂上了吗
        self.allow_exit = False       # True = 这一次别拦
        self._asking = False          # 前端确认框已经弹出去了(防连点 X)

    # ---------------------------------------------------------------- 安装

    def install(self) -> bool:
        try:
            form = getattr(self.window, "native", None)
            if form is None:
                log("窗口原生对象还没就绪,暂不接管关闭动作")
                return False
            if not hasattr(form, "FormClosing"):
                log(f"当前后端不支持拦截关闭({type(form).__name__}),X 仍等于退出")
                return False
            from System.Windows.Forms import FormClosingEventHandler
            self.form = form
            form.FormClosing += FormClosingEventHandler(self._on_closing)
            self.installed = True
            log("已接管窗口关闭:点 X 会先问一句")
            return True
        except Exception as e:
            log(f"接管关闭动作失败: {type(e).__name__}: {e}")
            return False

    # ---------------------------------------------------------------- 关闭

    def _on_closing(self, sender=None, args=None) -> None:
        """UI 线程回调:这里**绝不能**弹模态框,也绝不能同步 ui.push。"""
        try:
            if self.allow_exit:
                return
            pref = read_pref()
            if pref["ask"]:
                if self._asking:
                    # 确认框已经开着,但用户又点了一次 X:别再弹,直接藏进托盘,
                    # 免得点两下就"怎么都关不掉"。
                    self._asking = False
                    self._hide_or_exit(args)
                else:
                    if args is not None:
                        args.Cancel = True
                    self._asking = True
                    threading.Thread(target=self._ask_frontend, daemon=True).start()
                return
            if pref["action"] == "minimize":
                self._hide_or_exit(args)
                return
            self.allow_exit = True            # 偏好=退出:放行原生关闭
        except Exception as e:
            log(f"处理关闭时出错: {type(e).__name__}: {e}")

    def _hide_or_exit(self, args=None) -> None:
        """能藏就藏,藏不了就退出 —— 但绝不留下"点了 X 没反应"第三种结果。"""
        if self.hide_to_tray():
            if args is not None:
                args.Cancel = True
            return
        self.allow_exit = True
        threading.Thread(target=self.quit, daemon=True).start()

    def _ask_frontend(self) -> None:
        try:
            self.ui.push("close_request")
        except Exception as e:
            log(f"推送关闭确认框失败: {type(e).__name__}: {e}")

    def choose(self, action: str, remember: bool = False) -> dict:
        """前端确认框的答复:action = "hide" | "exit" | "cancel"。

        cancel 只把"正在询问"的标记清掉:用户点了取消就该照常干活,
        下次点 X 仍然会问(而不是被当成"又点了一次"直接藏进托盘)。
        """
        action = str(action or "").strip().lower()
        self._asking = False
        if action == "cancel":
            return {"ok": True, "action": "cancel"}
        if action not in ("hide", "exit"):
            return {"ok": False, "msg": f"未知的关闭选择: {action}"}
        if remember:
            write_pref(False, "minimize" if action == "hide" else "exit")
        if action == "hide":
            if not self.hide_to_tray():
                self.quit()
            return {"ok": True, "action": "hide"}
        self.quit()
        return {"ok": True, "action": "exit"}

    # ---------------------------------------------------------------- 托盘

    def hide_to_tray(self) -> bool:
        """收进托盘。**没有托盘图标就不许藏** —— 藏了就没有再叫出来的把手。"""
        if not self._ensure_icon():
            return False
        self._on_ui_thread(lambda: self.form.Hide())
        return True

    def _ensure_icon(self) -> bool:
        if self.icon is not None:
            return True
        try:
            import pystray
            from PIL import Image
            image = Image.open(resource_path("assets", "gcode.ico"))
            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._tray_show, default=True),
                pystray.MenuItem("点 X 时先问我", self._tray_reset_ask),
                pystray.MenuItem("退出引路学长", self._tray_quit),
            )
            icon = pystray.Icon("yinluxs", image, "引路学长", menu, visible=True)
        except Exception as e:
            log(f"托盘图标创建失败: {type(e).__name__}: {e}")
            return False
        self.icon = icon
        threading.Thread(target=self._run_icon, args=(icon,), daemon=True).start()
        log("托盘图标已就绪")
        return True

    def _run_icon(self, icon) -> None:
        try:
            icon.run()
        except Exception as e:
            log(f"托盘消息循环退出: {type(e).__name__}: {e}")

    def _tray_show(self, icon=None, item=None) -> None:
        self._on_ui_thread(self._show_form)

    def _tray_reset_ask(self, icon=None, item=None) -> None:
        """勾过「不再询问」之后,这里是唯一的反悔入口(托盘菜单)。"""
        pref = read_pref()
        write_pref(True, pref["action"])
        log("托盘:已恢复「点 X 时先问我」")   # 只改偏好,不把窗口弹出来打扰

    def _tray_quit(self, icon=None, item=None) -> None:
        self.quit()

    def _show_form(self) -> None:
        """从托盘把窗口叫回来:显示 + 取消最小化 + 抢焦点。"""
        try:
            from System.Windows.Forms import FormWindowState
            self.form.Show()
            self.form.WindowState = FormWindowState.Normal
            self.form.BringToFront()
            self.form.Activate()
        except Exception as e:
            log(f"从托盘恢复窗口失败: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 退出

    def quit(self) -> None:
        """真正退出:停托盘 → 关窗口 → webview.start() 返回 → bridge.close() 收尾。"""
        self.allow_exit = True
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        try:
            if self.form is not None:
                self._on_ui_thread(self.form.Close)
            else:
                self.window.destroy()
        except Exception as e:
            log(f"退出时关窗失败: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 线程

    def _on_ui_thread(self, fn) -> None:
        """WinForms 的控件只能在 UI 线程动(和 gui_main 开右键菜单同理)。"""
        form = self.form
        if form is None:
            return
        try:
            from System import Action
            if getattr(form, "InvokeRequired", False):
                form.Invoke(Action(fn))
            else:
                fn()
        except Exception as e:
            log(f"切 UI 线程执行失败: {type(e).__name__}: {e}")
            try:
                fn()
            except Exception:
                pass


# ------------------------------------------------------------------ 入口

def install(window, ui) -> TrayController | None:
    """接管窗口关闭动作;失败返回 None(退回"X = 退出"的老行为)。"""
    global _CONTROLLER
    controller = TrayController(window, ui)
    if not controller.install():
        return None
    _CONTROLLER = controller
    return controller


def controller() -> TrayController | None:
    return _CONTROLLER


def close_choice(action: str, remember: bool = False) -> dict:
    """JS 侧调用:用户在关闭确认框里按了哪个钮。"""
    c = _CONTROLLER
    if c is None:
        return {"ok": False, "msg": "托盘未就绪,请直接再次点窗口 X 退出"}
    return c.choose(action, bool(remember))
