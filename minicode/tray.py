"""关窗去向 + 系统托盘 + 桌面提醒(v0.9.22 / v0.9.25)。

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

v0.9.25 又接管了**桌面提醒**:课前 20 分钟、今日简报、临期待办、官网新通知这四路
主动推送以前只落在聊天窗口顶部那条提示栏里 —— 学生不会把学长一直开在前台,窗口一
最小化就等于没提醒。**通道选型是真机试出来的**:托盘图标的 `Shell_NotifyIcon` 气球
在这台 Win11 24H2 上六条全"成功"却一条没显示(库里连记录都没有),所以主通道改成
WinRT Toast(借用系统自带的 PowerShell 5.1 投影,不引第三方包、不新增打包依赖),
气球只留作 Win10 退路、且结果永远标 `verified=False`。三条约定不变:窗口正显示且有
焦点时默认不重复弹(那行字用户已经看得见);开关与类别偏好存 `notify-pref.json`
(读不出来按"开着",绝不因为文件坏了就静默关掉提醒);发不出去一律回 `ok=False`
并写 `tray.log` —— 这条链路只在用户机器上才复现,不能靠猜。
"""
from __future__ import annotations

import json
import threading
import time

from minicode.paths import app_path, resource_path

PREF_FILE = "close-pref.json"
LOG_FILE = "tray.log"

# 桌面提醒的偏好单独一份,不和 close-pref.json 混:那份管"点 X 窗口去哪",
# 这份管"要不要弹到桌面右下角" —— 两件事各自开关,合在一起改一个会撞坏另一个。
NOTIFY_PREF = "notify-pref.json"
NOTIFY_KINDS = ("briefing", "class", "schedule", "reminders",
                "calendar", "notices", "update")
NOTIFY_DEFAULTS = {"enabled": True,
                   "kinds": {k: True for k in NOTIFY_KINDS},
                   "quiet_when_focused": True}
NOTIFY_COOLDOWN = 90          # 同一条(同类同正文)多久内不重复弹
_ICON_WARMUP = 0.6            # 刚建好托盘图标就通知,系统可能还没收下这个句柄

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


# ---------------------------------------------------------- 桌面提醒偏好

def _notify_path():
    return app_path(NOTIFY_PREF)


def _default_notify() -> dict:
    return {"enabled": True,
            "kinds": {k: True for k in NOTIFY_KINDS},
            "quiet_when_focused": True}


def read_notify_pref() -> dict:
    """读桌面提醒偏好;读不出来按默认(开着)。

    坏文件不能变成"提醒静默关":那是用户最察觉不了的失败。未知类别原样丢掉,
    免得以后加一类时,旧文件里手打的错别字一直生效却没人知道。
    """
    base = _default_notify()
    try:
        raw = json.loads(_notify_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(raw, dict):
        return base
    kinds = dict(base["kinds"])
    got = raw.get("kinds")
    if isinstance(got, dict):
        for k in NOTIFY_KINDS:
            if k in got:
                kinds[k] = bool(got[k])
    return {"enabled": raw.get("enabled", True) is not False,
            "kinds": kinds,
            "quiet_when_focused": raw.get("quiet_when_focused", True) is not False}


def write_notify_pref(pref: dict) -> bool:
    """写偏好;写不进去返回 False(目录只读时"勾了没记住"必须能让调用方看见)。"""
    try:
        _ensure_parent(_notify_path()).write_text(
            json.dumps(pref, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return True
    except OSError as e:
        log(f"桌面提醒偏好写入失败: {type(e).__name__}: {e}")
        return False


def notify_enabled(kind: str = "") -> bool:
    """总开关 + 这一类的开关(测试通知 kind 传空 = 只看总开关)。"""
    pref = read_notify_pref()
    if not pref["enabled"]:
        return False
    if not kind:
        return True
    return bool(pref["kinds"].get(kind, True))


def set_notify(enabled: bool, kind: str = "") -> dict:
    """开/关桌面提醒。kind 非空则只动那一类;返回最新状态(写失败也回真实状态)。"""
    pref = read_notify_pref()
    if kind:
        if kind not in NOTIFY_KINDS:
            return {"ok": False, "msg": f"不认识的提醒类别: {kind}",
                    "enabled": pref["enabled"]}
        pref["kinds"][kind] = bool(enabled)
        saved = write_notify_pref(pref)
        return {"ok": saved, "enabled": pref["enabled"], "kind": kind,
                "kind_on": pref["kinds"][kind],
                "msg": ("" if saved else
                        f"开关没能写进 {_notify_path()} (数据目录可能只读),"
                        "这次改动重启后会回到原样")}
    pref["enabled"] = bool(enabled)
    saved = write_notify_pref(pref)
    return {"ok": saved, "enabled": pref["enabled"],
            "msg": ("" if saved else
                    f"开关没能写进 {_notify_path()} (数据目录可能只读),"
                    "这次改动重启后会回到原样")}


# ------------------------------------------------ Windows 的通知通道(实测选型)

# 为什么不是"托盘图标的 `Icon.notify()` 就够了":
# 2026-09-21 在这台 Win11 24H2(26100)上实测 —— 连发六条 `Icon.notify()` 全部
# "成功返回"(pystray 的 `_message()` 压根不看 `Shell_NotifyIcon` 的返回值),
# 用户**一条都没看见**,而 Windows 自己的通知库
# (`%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`)里那几分钟
# **一行记录都没有**:气球(balloon)在 Win11 上已经不被显示。
# 同一时刻改走 WinRT Toast,库里立刻出现 handler=`yinlu.xuezhang.app` 的记录。
# 结论:主通道 = WinRT Toast;气球只当 Win10 的退路,并且**如实标未核实**。
#
# 显示名/图标从哪来(2026-09-22 真机截图实测,两个都踩过):
# - 名字取的是下面这坨注册表键的**默认值**(只写 `DisplayName` 不算);
# - Windows **第一次见到某个 AUMID 时**就把它的显示名/图标缓存进通知库
#   (`wpndatabase.db` 的 `NotificationHandler`),之后再改注册表**刷不回来**。
#   `yinlu.xuezhang.app` 正是"只会写 DisplayName"那会儿建的,所以它永远顶着
#   一串英文 —— 只能换一个新身份,故下面用 `.desktop`。
# - 顺序因此是硬要求:`notify()` 先 `register_notify_identity()` 再 `_toast()`,
#   反过来就等于把裸 AUMID 焊死在用户机器上。
AUMID = "yinlu.xuezhang.desktop"  # 通知的"应用身份"(AppUserModelId)
AUMID_NAME = "引路学长"            # 不登记就只显示上面那串英文
AUMID_ICON = ("assets", "toast-icon.png")
AUMID_REG = r"Software\Classes\AppUserModelId\%s" % AUMID   # HKCU,免管理员
_TOAST_TIMEOUT = 15               # PowerShell 冷启动 ~0.7s,给足余量也别吊死
_NO_WINDOW = 0x08000000           # CREATE_NO_WINDOW:别为了弹通知闪一个黑框

# 正文与标题**用环境变量递进去**,不拼进命令行字符串:课程名、通知标题是用户
# 机器上的任意文本,拼 shell 等于把注入面留给自己。
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode($env:YX_TITLE)) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($env:YX_BODY)) | Out-Null
if ($env:YX_ICON) {
  try {
    $img = $xml.CreateElement('image')
    [void]$img.SetAttribute('src', $env:YX_ICON)
    [void]$img.SetAttribute('placement', 'appLogoOverride')
    [void]$xml.GetElementsByTagName('visual').Item(0).AppendChild($img)
  } catch { }
}
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
if ($env:YX_TAG) { $toast.Tag = $env:YX_TAG }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
    $env:YX_AUMID).Show($toast)
'OK'
"""

_identity_done = False            # 登记一次就够(每次通知都写注册表没必要)


def _powershell() -> str:
    """点名 5.1 那条路径:PS7 不带 WinRT 投影,toast 会当场报错。"""
    import os
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe")


def _file_uri(path) -> str:
    """把本地图片路径写成 `file:///` URI。

    WinRT 的 `<image src>` 对非打包应用只认 `file:///` 或 `http(s)`;**裸盘符
    路径会被静默丢掉** —— 通知照弹、不报错,就是没有图标(真机截图才发现的)。
    注册表里的 `IconUri` 同理。
    """
    import os
    return "file:///" + os.path.abspath(str(path)).replace("\\", "/")


def register_notify_identity(force: bool = False) -> dict:
    """把 AUMID 登记到 HKCU:让通知显示成「引路学长」+ 我们的图标。

    写三样:键的**默认值**(shell 渲染时读的就是它)、`DisplayName`(老代码
    路径按名字查)、`IconUri`(`file:///` 形式,裸盘符路径会被静默丢掉)。

    这是非打包应用拿通知身份的官方路子(不需要管理员),而且**必须在第一次
    发通知之前**登记完:Windows 见到新 AUMID 的那一次就把显示名/图标缓存进
    通知库,之后再改注册表刷不回来(真机上旧身份就是这么被焊成一串英文的)。
    登记只为好看,**失败绝不拦通知** —— 写不进注册表(禁用注册表编辑、组策略)
    时照样发,只是那一条会顶着一串英文身份。全程只写自己这一坨键,不碰别家。
    """
    global _identity_done
    import os
    if _identity_done and not force:
        return {"ok": True, "skipped": "already"}
    try:
        import winreg
        icon = resource_path(*AUMID_ICON)
        if not os.path.isfile(icon):
            icon = ""
        # 默认值(不是 DisplayName)才是 shell 渲染通知时读的那个名字:只写
        # DisplayName 时,真机上通知顶部仍然顶着 `yinlu.xuezhang.app` 一串英文。
        # 两处一起写,DisplayName 留给按名字查的老代码路径。
        want = {"": AUMID_NAME, "DisplayName": AUMID_NAME}
        if icon:
            want["IconUri"] = _file_uri(icon)
        if not force:
            try:                            # 已经登记对了就不重复写
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUMID_REG) as k:
                    if all(winreg.QueryValueEx(k, n)[0] == v for n, v in want.items()):
                        _identity_done = True
                        return {"ok": True, "skipped": "registered"}
            except OSError:
                pass
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUMID_REG) as k:
            for name, value in want.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        _identity_done = True
        return {"ok": True}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log(f"通知身份登记失败(不影响发送): {msg}")
        return {"ok": False, "msg": msg}


def _toast(title: str, body: str, tag: str = "") -> dict:
    """走 WinRT Toast 发一条;返回 ok 只代表 PowerShell 认了这条 Show()。"""
    import base64
    import os
    import subprocess
    exe = _powershell()
    if not os.path.isfile(exe):
        return {"ok": False, "skipped": "no_powershell",
                "msg": f"系统里没有 PowerShell:{exe}"}
    icon = resource_path(*AUMID_ICON)
    # 环境块必须是**纯字符串**:Windows 建进程时塞进 Path/None 会直接抛
    # "environment can only contain strings"(真机踩过一次,三条通知全没发出去),
    # 而这条路唯一的报错点就在 subprocess.run 里,不看日志根本猜不到是这里。
    env = {k: str(v) for k, v in dict(
        os.environ, YX_AUMID=AUMID, YX_TITLE=str(title or AUMID_NAME),
        YX_BODY=str(body), YX_TAG=str(tag or ""),
        # 必须是 file:/// 形式:裸路径会让 <image src> 静默失效(通知没图标)
        YX_ICON=_file_uri(icon) if os.path.isfile(icon) else "").items()}
    encoded = base64.b64encode(_TOAST_PS.encode("utf-16-le")).decode("ascii")
    argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]
    try:
        p = subprocess.run(argv, env=env, capture_output=True,
                           timeout=_TOAST_TIMEOUT,
                           **({"creationflags": _NO_WINDOW} if os.name == "nt" else {}))
    except Exception as e:
        return {"ok": False, "skipped": "toast_spawn",
                "msg": f"{type(e).__name__}: {e}"}
    out = (p.stdout or b"").decode("utf-8", "ignore").strip()
    if p.returncode == 0 and out.endswith("OK"):
        return {"ok": True, "channel": "toast"}
    err = ((p.stderr or b"").decode("utf-8", "ignore") or out).strip()
    return {"ok": False, "skipped": "toast",
            "msg": f"退出码 {p.returncode}: {err[:200] or '无输出'}"}


def _balloon(title: str, body: str) -> dict:
    """退路:托盘图标的 Shell_NotifyIcon 气球。

    **Win11 上它不显示**(见上面 AUMID 那段实测),而且成功与否根本看不出来
    —— pystray 不检查 `Shell_NotifyIcon` 返回值。所以这条一律带
    `verified: False`:能说"交给系统了",不能说"弹出来了"。
    """
    c = _CONTROLLER
    if c is None:
        return {"ok": False, "skipped": "no_tray",
                "msg": "托盘未接管,气球发不出去"}
    was_empty = c.icon is None
    if c._ensure_icon() is False or c.icon is None:
        return {"ok": False, "skipped": "no_icon",
                "msg": "托盘图标起不来,气球发不出去(详情看 tray.log)"}
    if was_empty:                   # 刚建好:等系统收下这个句柄,否则第一条容易丢
        time.sleep(_ICON_WARMUP)
    try:
        c.icon.notify(body, str(title or AUMID_NAME))
    except Exception as e:
        log(f"气球通知失败: {type(e).__name__}: {e}")
        return {"ok": False, "skipped": "error",
                "msg": f"系统没接受这条通知: {type(e).__name__}"}
    return {"ok": True, "channel": "balloon", "verified": False}


# ---------------------------------------------------------- 桌面提醒本体

_seen: dict[str, float] = {}          # 冷却表: key -> 上次弹出的时间戳
_LAST: dict[str, object] = {"ok": None, "channel": "", "verified": None,
                            "kind": "", "msg": "还没发过"}   # 最近一次真实结果


def _in_cooldown(key: str) -> bool:
    now = time.time()
    with _LOCK:
        for k, ts in list(_seen.items()):
            if now - ts > NOTIFY_COOLDOWN * 4:
                _seen.pop(k, None)
        last = _seen.get(key) or 0
        if now - last < NOTIFY_COOLDOWN:
            return True
        _seen[key] = now
        return False


def _window_in_foreground() -> bool:
    """窗口正显示且有焦点 → 聊天里那条提醒本来就看得见,别重复打扰。

    拿不准(控制器没装、属性读不到)时按"不在前台"处理:宁可多弹一条,
    也不要把用户等的那声"快上课了"悄悄吃掉。
    """
    c = _CONTROLLER
    form = getattr(c, "form", None) if c is not None else None
    if form is None:
        return False
    try:
        return bool(form.Visible) and bool(form.ContainsFocus)
    except Exception:
        return False


def notify(title: str, body: str, kind: str = "", force: bool = False) -> dict:
    """弹一条 Windows 桌面通知:先走 WinRT Toast,不通才退回托盘气球。

    只做"怎么弹",不编内容:正文一律由调用方给(主动推送那边复用聊天里
    已有的那行 text)。

    两条通道的可信度**不一样**,所以结果里带 `channel` 与 `verified`:
    - `toast`:Windows 10/11 真正显示的那条通道(PowerShell 投影 WinRT)。
      `ok=True` 意思是系统收下了这条 `Show()`;显不显示还受"专注助手/
      通知设置"影响,那个不是我们能读的。
    - `balloon`:老 `Shell_NotifyIcon` 气球。**Win11 实测不显示**,而
      pystray 又查不到失败,所以这条路永远 `verified=False` —— 宁可以说
      "交给系统了",不可以说"弹出来了"。
    两条都没走通才回 `ok=False`,并把原因留在 `tray.log` 与 `notify_status()`
    里:这条链路只在用户机器上才复现,不能靠猜。
    """
    body = str(body or "").strip()
    if not body:
        return {"ok": False, "skipped": "empty", "msg": "没有可显示的内容"}
    pref = read_notify_pref()
    if not force:
        if not pref["enabled"] or (kind and not pref["kinds"].get(kind, True)):
            return {"ok": False, "skipped": "off"}
        if _in_cooldown(f"{kind}|{body[:60]}"):
            return {"ok": False, "skipped": "cooldown"}
        if kind and pref["quiet_when_focused"] and _window_in_foreground():
            return {"ok": False, "skipped": "focused"}
    text = body if len(body) <= 160 else body[:159] + "…"
    head = str(title or AUMID_NAME)
    register_notify_identity()        # 只为显示名/图标,失败也照发
    t = _toast(head, text, kind)
    if t.get("ok"):
        _LAST.update({"ok": True, "channel": "toast", "verified": True,
                      "kind": kind, "msg": ""})
        return {"ok": True, "channel": "toast", "verified": True,
                "title": head, "body": text, "kind": kind}
    b = _balloon(head, text)
    if b.get("ok"):
        why = t.get("msg") or t.get("skipped")
        _LAST.update({"ok": True, "channel": "balloon", "verified": False,
                      "kind": kind, "msg": f"toast 不通({why})"})
        return {"ok": True, "channel": "balloon", "verified": False,
                "title": head, "body": text, "kind": kind,
                "note": f"toast 通道没走通({why}),这条退回旧气球;"
                        "Windows 11 上气球通常不显示 —— 看 tray.log 排查"}
    why_t = t.get("msg") or t.get("skipped")
    why_b = b.get("msg") or b.get("skipped")
    _LAST.update({"ok": False, "channel": "", "verified": False,
                  "kind": kind, "msg": f"toast:{why_t} / 气球:{why_b}"})
    log(f"桌面通知失败: toast={why_t} 气球={why_b}")
    return {"ok": False, "skipped": "no_channel",
            "msg": f"两条通道都没发出去 —— toast:{why_t};气球:{why_b}"}


def notify_status() -> dict:
    """给工具/排查看:开关状态 + 这次到底能不能弹 + 最近一次结果。"""
    import os
    pref = read_notify_pref()
    c = _CONTROLLER
    return {"enabled": pref["enabled"], "kinds": pref["kinds"],
            "quiet_when_focused": pref["quiet_when_focused"],
            "tray_installed": c is not None,
            "icon_ready": bool(c and c.icon is not None),
            "pref_path": str(_notify_path()),
            # 下面这几条只为一件事:用户说"看不到通知"时,先分清是**我们没发**
            # 还是**系统没显示**。`last` 是最近一次真实结果(含用了哪条通道)。
            "toast_channel": os.path.isfile(_powershell()),
            "aumid": AUMID,
            "identity_registered": _identity_done,
            "last": dict(_LAST)}


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
        self._icon_lock = threading.Lock()   # 只建一个托盘图标(见 _ensure_icon)

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
        # 建图标有两个来路:关窗时藏进托盘(UI/桥接线程)与发桌面通知时懒建
        # (看门狗线程)。撞上会建出两个 Icon —— 托盘里多一颗图标,还多一条
        # 永远停不下来的消息循环。
        with self._icon_lock:
            if self.icon is not None:
                return True
            try:
                import pystray
                from PIL import Image
                image = Image.open(resource_path("assets", "gcode.ico"))
                menu = pystray.Menu(
                    pystray.MenuItem("显示主窗口", self._tray_show, default=True),
                    pystray.MenuItem("桌面提醒(课前/简报/待办/通知)",
                                     self._tray_toggle_notify,
                                     checked=lambda item: notify_enabled()),
                    pystray.MenuItem("测试一条桌面通知", self._tray_test_notify),
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

    def _tray_toggle_notify(self, icon=None, item=None) -> None:
        """翻转桌面提醒总开关(勾选框自己会跟着偏好变,不用另画状态)。"""
        want = not notify_enabled()
        r = set_notify(want)
        log(f"托盘:桌面提醒 → {'开' if want else '关'}"
            + (f" (失败: {r.get('msg')})" if not r.get("ok") else ""))

    def _tray_test_notify(self, icon=None, item=None) -> None:
        """当场发一条测试通知:用户说"看不到"时,这一行日志就是分界线。

        force=True 是有意的 —— 测试要绕开开关与冷却,专门验"系统收不收",
        而不是验用户设了什么。
        """
        r = notify("引路学长 · 测试", "看到这条就说明桌面提醒是通的。", force=True)
        log("托盘:测试通知 " + ("已发出" if r.get("ok") else
                               f"未发出({r.get('skipped')} {r.get('msg', '')})"))

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


# ------------------------------------------------------------------ 工具入口

# 工具文本里给用户看的中文名(内部键仍是英文,别反过来拿中文当键)
KIND_LABELS = {"briefing": "今日简报", "class": "课前提醒", "schedule": "课程提醒",
               "reminders": "待办临期", "calendar": "校历倒计时",
               "notices": "校园新通知", "update": "新版本"}


def desktop_notify(ctx, action: str = "status", kind: str = "",
                   reason: str = "") -> str:
    """desktop_notify 工具处理器:用嘴开关桌面提醒 / 看状态 / 当场试一条。"""
    action = (action or "status").strip().lower()
    kind = (kind or "").strip().lower()
    if kind and kind not in NOTIFY_KINDS:
        return f"不认识的类别「{kind}」。可选项:" + "、".join(NOTIFY_KINDS)
    st = notify_status()
    if action == "status":
        last = st["last"]
        on = [KIND_LABELS.get(k, k) for k in NOTIFY_KINDS if st["kinds"].get(k)]
        off = [KIND_LABELS.get(k, k) for k in NOTIFY_KINDS
               if not st["kinds"].get(k)]
        return ("桌面提醒:" + ("开" if st["enabled"] else "关") + "。\n"
                f"会弹的类别:{'、'.join(on) or '无'}"
                + (f";不弹:{'、'.join(off)}" if off else "") + "。\n"
                + ("窗口在前景有焦点时不重复弹(那行字你看得见)。"
                   if st["quiet_when_focused"] else "即使窗口在前景也照样弹。")
                + "\n通道:" + ("WinRT toast(Windows 10/11 真显示的那条)"
                               if st["toast_channel"]
                               else "系统里没有 PowerShell,只能退回托盘气球"
                                    "(Windows 11 上多半不显示)")
                + f";通知身份:{st['aumid']}"
                  + ("(已登记成「引路学长」)" if st["identity_registered"] else "(未登记)")
                + "。\n最近一次:" + ("没发过" if last.get("ok") is None else
                                     f"{last.get('channel') or '两条通道都没走通'}"
                                     f" · {last.get('msg') or '已交给系统'}")
                + "\n偏好文件:" + st["pref_path"]
                + ("" if st["tray_installed"] else
                   "\n(当前形态没有托盘:不影响走 toast 弹通知,只是没法退回气球;"
                   "两条通道都不通时,提醒就只在聊天窗口顶部那条提示栏里。)"))
    if action in ("on", "off"):
        want = action == "on"
        r = set_notify(want, kind)
        what = f"「{KIND_LABELS.get(kind, kind)}」这一类" if kind else "全部桌面提醒"
        return ((f"已把{what}关掉。" if not want else f"已打开{what}。")
                + ("" if r.get("ok") else f" 注意:{r.get('msg')}"))
    if action == "test":
        r = notify("引路学长 · 测试", "看到这条就说明桌面提醒是通的。", force=True)
        if r.get("ok") and r.get("verified"):
            return ("测试通知已交给系统发出(走 WinRT toast,Windows 自己的通知通道)。"
                    "若你仍没看到,那是系统挡的:设置 → 系统 → 通知 里确认「引路学长」"
                    "允许通知,再看专注助手(游戏/全屏时会自动静音)。")
        if r.get("ok"):
            return ("这条是**退回托盘气球**发的,不能算验证通过 —— "
                    + r.get("note", ""))
        return ("这条测试通知没能发出:" + r.get("msg", r.get("skipped", ""))
                + "\n原因也写在数据目录里的 tray.log;"
                  "主动提醒仍会显示在聊天窗口顶部那条提示栏里。")
    return f"未知的 action「{action}」,可用:status / on / off / test"
