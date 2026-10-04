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
                "calendar", "notices", "update", "weather",
                # v0.9.57 A4:两个周期任务上的真类别 —— 不进这张表用户就关不掉
                # (test_proactive 那根"新类别必须进表"的针盯的就是这件事)
                "review", "gaps",
                # v0.9.65:「模型恢复了」从裸 notify 收编 —— 不进表 = 关不掉
                "recovery",
                # v0.9.104:后台命令跑完的那条回执( shell._bg_notify_done )。
                # 同样是"不进表就关不掉"这条规矩 —— 它打扰的频率比简报高得多
                # (一条长命令就跑一次),更必须有开关。
                "background")
NOTIFY_DEFAULTS = {"enabled": True,
                   "kinds": {k: True for k in NOTIFY_KINDS},
                   "quiet_when_focused": True}
NOTIFY_COOLDOWN = 90          # 同一条(同类同正文)多久内不重复弹
_ICON_WARMUP = 0.6            # 刚建好托盘图标就通知,系统可能还没收下这个句柄

_CONTROLLER = None            # 单窗口应用:一个控制器就够
_RECENT_PROVIDER = None       # 桥交来的"最近几条会话"取法(托盘菜单那一叠,见 set_recent_provider)
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
            path = _ensure_parent(app_path(LOG_FILE))
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
            cap_log(path)
    except Exception:
        pass


LOG_CAP_LINES = 500            # v0.9.66:与 watchdog.log 同款滚动,别让诊断日志无限长

# `desktop_notify` 认的 action,**这一处是真相**:工具 schema 的 enum 从这里取,
# 未知 action 那句"可用:…"也从这里拼。v0.9.79 给 kind 做过同一条对账,action 这条
# 线以前是两处手抄 —— 加一个出口忘了改 enum 的表现很坏:模型照着描述调,
# 参数校验先把它挡在门外,用户以为功能没做。
NOTIFY_ACTIONS = ("status", "on", "off", "test", "startup_on", "startup_off",
                  "log", "jobs", "tray_log", "gui_log")

# 托盘菜单里「最近会话」那一叠:摆几条、多久重摆一次(v0.9.114,图 p8/p9)。
MENU_RECENT = 5
MENU_REFRESH_SECONDS = 20


def log_tail(lines: int = 30) -> list[str]:
    """托盘/通知诊断日志最后几行;没有日志回空列表(不是报错)。

    为什么要有这个读法:v0.9.79 给 `watchdog.log` 开过同一个口子(`action=log`),
    而 `tray.log` 一直是**只写不读** —— 代码里那句"原因也写在数据目录里的 tray.log"
    等于让同学自己去开文件。"通知没弹""点 X 没问我了""开机自启没生效"这三件事
    的现场恰好都在这里,而它们是最容易被误报成"学长坏了"的。
    """
    try:
        rows = _ensure_parent(app_path(LOG_FILE)).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return rows[-max(1, int(lines)):]


# 界面层那份警告是 `gui_main.py:80` 的 `_log_warning` 写的 —— 记的就一件事:
# **原生右键菜单与编辑快捷键(Ctrl+C/V/A)开没开起来**(那一段轮询 WebView2 就绪、
# 开成功写一行、失败每个异常写一行、等不到写一行超时)。
WARN_LOG_FILE = "gui_warnings.log"


def warn_log_tail(lines: int = 30) -> list[str]:
    """界面警告日志最后几行;没有日志回空列表(不是报错)。

    为什么这份归这里读:与 `tray.log` 完全同一个形状 —— **只写不读**。上两轮分别给
    `watchdog.log`(v0.9.79)和 `tray.log`(v0.9.96)开了 `desktop_notify` 的口子,
    同学说「对话框里右键粘贴不出来」「复制快捷键没用」时,现场就在 `gui_warnings.log`
    里,而以前只能让他自己去开文件(他大多找不到数据目录在哪)。
    放在 `tray.py` 是因为读者就是这里的 `desktop_notify`;`gui_main.py` 已经引了本模块,
    反过来引会在启动路径上多一条环。
    """
    try:
        rows = (_ensure_parent(app_path(WARN_LOG_FILE))
                .read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return []
    return rows[-max(1, int(lines)):]


def cap_log(path, keep=None) -> None:
    """超上限只留最近的若干行(默认 `LOG_CAP_LINES`);写失败照旧静默(日志不能拖垮托盘)。

    上限在函数体里读全局,不是默认参数 —— 测试要 `monkeypatch` 那个数来验滚动,
    写成 `keep=LOG_CAP_LINES` 会在定义时就把值焊死,滚动那条针等于空跑。
    `gui_main.py` 那份界面警告也走这里:裸日志的滚动只准有一份口径。
    """
    keep = LOG_CAP_LINES if keep is None else int(keep)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > keep:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
            tmp.replace(path)
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
if ($env:YX_ACT) {
  # 点通知直达(v0.9.72):协议激活 —— 点了由系统拉起 yinlu: URI,
  # 已在跑的实例接住并弹回窗口;没注册协议时这个属性不设置,行为不变。
  [void]$xml.DocumentElement.SetAttribute('activationType', 'protocol')
  [void]$xml.DocumentElement.SetAttribute('activation', $env:YX_ACT)
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


_protocol_ready = False            # 协议注册一次就够(与 AUMID 同一条纪律)


def _protocol_uri_for(kind: str) -> str:
    """这一类通知的激活 URI(通知点击直达,v0.9.72)。

    协议没注册成功(源码运行/注册表拒)就给空串 —— toast 行为退回原样,
    **绝不发一个点了没反应的空激活**。注册指向 GUI exe(launch_target 给
    什么就用什么;CLI 进程发起时 activate 层会改指同目录的 GUI exe)。
    """
    global _protocol_ready
    from minicode import activate as _act
    if not _protocol_ready:
        try:
            exe, _args, _cwd = launch_target()
            r = _act.register_protocol(exe)
            _protocol_ready = bool(r.get("ok"))
            if not _protocol_ready and not r.get("skipped") and r.get("msg"):
                log(f"激活协议没注册上: {r['msg']}")
        except Exception as e:
            log(f"激活协议注册出错: {type(e).__name__}: {e}")
    return _act.panel_uri(kind) if _protocol_ready else ""


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


def _run_ps(script: str, env_extra: dict, timeout: int = _TOAST_TIMEOUT,
            need_ok: bool = True) -> dict:
    """起一次系统自带的 PowerShell 5.1 跑脚本。

    三条纪律(都是真机踩出来的,见上面 AUMID 与 env 那两段):
    - 脚本走 `-EncodedCommand`(base64 utf-16-le),不拼引号;
    - **所有文本(标题、正文、路径、描述)只经 `YX_*` 环境变量进去**,不进 argv;
    - 环境块必须是**纯字符串**:Windows 建进程时塞进 Path/None 会直接抛
      "environment can only contain strings",而唯一的报错点就在 subprocess.run 里。
    成功判据是"退出码 0 且 stdout 以 OK 结尾" —— 脚本自己不说 OK 就不算成功。
    """
    import base64
    import os
    import subprocess
    exe = _powershell()
    if not os.path.isfile(exe):
        return {"ok": False, "skipped": "no_powershell",
                "msg": f"系统里没有 PowerShell:{exe}"}
    env = {k: str(v) for k, v in dict(os.environ, **env_extra).items()}
    argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    try:
        p = subprocess.run(argv, env=env, capture_output=True, timeout=timeout,
                           **({"creationflags": _NO_WINDOW} if os.name == "nt" else {}))
    except Exception as e:
        return {"ok": False, "skipped": "ps_spawn",
                "msg": f"{type(e).__name__}: {e}"}
    out = (p.stdout or b"").decode("utf-8", "ignore").strip()
    err = ((p.stderr or b"").decode("utf-8", "ignore") or out).strip()
    if p.returncode != 0:
        return {"ok": False, "skipped": "ps",
                "msg": f"退出码 {p.returncode}: {err[:200] or '无输出'}"}
    if need_ok and not out.endswith("OK"):
        # 退出码 0 但没打印 OK = 脚本半路没走完,不能算成功
        return {"ok": False, "skipped": "ps",
                "msg": f"没有回 OK:{err[:200] or '无输出'}"}
    return {"ok": True, "out": out}


def _toast(title: str, body: str, tag: str = "", act: str = "") -> dict:
    """走 WinRT Toast 发一条;返回 ok 只代表 PowerShell 认了这条 Show()。

    act 是激活 URI(通知点击直达,v0.9.72):非空时 toast 带上
    activationType=protocol —— 点了由系统拉起 yinlu: URI 交给在跑的实例。
    协议没注册成功时不传(点击行为退回"无动作"),绝不发一个点了没反应的空激活。
    """
    import os
    icon = resource_path(*AUMID_ICON)
    r = _run_ps(_TOAST_PS, dict(
        YX_AUMID=AUMID, YX_TITLE=str(title or AUMID_NAME),
        YX_BODY=str(body), YX_TAG=str(tag or ""),
        YX_ACT=str(act or ""),
        # 必须是 file:/// 形式:裸路径会让 <image src> 静默失效(通知没图标)
        YX_ICON=_file_uri(icon) if os.path.isfile(icon) else ""))
    if r.get("ok"):
        return {"ok": True, "channel": "toast"}
    skipped = r.get("skipped")
    if skipped == "ps_spawn":
        return {"ok": False, "skipped": "toast_spawn", "msg": r.get("msg", "")}
    if skipped == "ps":
        return {"ok": False, "skipped": "toast", "msg": r.get("msg", "")}
    return {"ok": False, "skipped": skipped or "toast", "msg": r.get("msg", "")}


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
    t = _toast(head, text, kind, act=_protocol_uri_for(kind))
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


# ------------------------------------------------------- 开机自动启动(v0.9.26)
#
# 为什么算在"桌面提醒"这一摊里:提醒只有**学长在跑**才发得出去。8:30 开机先看
# 一眼消息、再顺手把学长关掉,那 8:40 的课前提醒就已经没了 —— 自启 + 错过的补发
# 才让 v0.9.25 那条桌面通知真正闭环。
#
# 为什么用当前用户的「启动」文件夹快捷方式,而不是注册表 Run 键:两条都免管理员,
# 但快捷方式是**磁盘上一个看得见的文件** —— 用户能自己开资源管理器看到它指向谁、
# 不满意直接删;写注册表反而不好审计。代价是造 .lnk 得借 PowerShell 的
# WScript.Shell,所以建完一定要**读回来核对指向**才算成功。
STARTUP_NAME = "引路学长.lnk"
STARTUP_DESC = "引路学长 —— 开机自动启动(桌面提醒需要学长在跑)"
_STARTUP_MAKE_PS = r"""
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $env:YX_LNK
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($env:YX_LNK)
$s.TargetPath = $env:YX_TARGET
$s.Arguments = $env:YX_ARGS
$s.WorkingDirectory = $env:YX_WORKDIR
$s.Description = $env:YX_DESC
if ($env:YX_ICON) { $s.IconLocation = $env:YX_ICON }
$s.Save()
if (Test-Path -LiteralPath $env:YX_LNK) { 'OK' }
"""
_STARTUP_READ_PS = r"""
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $env:YX_LNK)) { 'MISSING'; exit }
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($env:YX_LNK)
Write-Output ("TARGET=" + $s.TargetPath)
"""


def startup_path() -> str:
    """当前用户的「启动」文件夹里那个快捷方式(没有 APPDATA 时回空串)。"""
    import os
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", STARTUP_NAME)


# 开机自启带这个参数:开机是来用电脑的,不是来看学长窗口的 —— 直接进托盘待命,
# 提醒照弹,想看界面双击托盘图标(「显示主窗口」)。gui_main 读同一个常量。
BACKGROUND_FLAG = "--background"


def starts_hidden(argv=None) -> bool:
    """本次启动该不该直接进托盘(命令行没有 `--background` 就是正常开窗口)。"""
    import sys
    return BACKGROUND_FLAG in list(sys.argv[1:] if argv is None else argv)


def launch_target() -> tuple:
    """(可执行文件, 参数, 工作目录):打包后就是 app 自己,开发态用 pythonw 跑 gui_main。

    开发态刻意选 `pythonw.exe`(而不是 `python.exe`):开机弹一个黑色控制台窗口
    比不自启更烦人。找不到 pythonw 才退回当前解释器。

    参数里带 `BACKGROUND_FLAG`:自启只负责**让学长在跑**,不负责把窗口摆到脸上。
    真起不来托盘时 gui_main 会照常显示窗口(见 `start_in_tray`),不会出现
    "进程在、界面永远叫不出来"那种更糟的结果。
    """
    import os
    import sys
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return exe, BACKGROUND_FLAG, os.path.dirname(exe)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    here = os.path.dirname(os.path.abspath(sys.executable))
    exe = os.path.join(here, "pythonw.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    script = os.path.join(repo, "gui_main.py")
    return (exe, f'"{script}" {BACKGROUND_FLAG}' if os.path.isfile(script) else "",
            repo)


def startup_present() -> bool:
    """快捷方式在不在 —— **只查文件,不起 PowerShell**。

    托盘菜单每次右键都要画勾选态,而 `startup_status()` 要冷启动一次 PowerShell
    读 `.lnk` 指向,拿它当回调会让菜单卡一下。这里只回答"有没有";
    "是不是我们写的那个"仍由 `startup_status()` / `set_startup()` 判。
    """
    import os
    lnk = startup_path()
    return bool(lnk) and os.path.isfile(lnk)


def startup_status() -> dict:
    """自启现状:快捷方式在不在、指向哪个 exe、是不是我们写的那个。"""
    import os
    lnk = startup_path()
    exe, _args, _wd = launch_target()
    st = {"supported": bool(lnk) and os.name == "nt", "lnk": lnk,
          "installed": False, "target": "", "ours": False, "want": exe}
    if not st["supported"]:
        return st
    r = _run_ps(_STARTUP_READ_PS, {"YX_LNK": lnk}, need_ok=False)
    out = (r.get("out") or "").strip()
    if not r.get("ok") or out == "MISSING" or not out.startswith("TARGET="):
        return st
    target = out[len("TARGET="):].strip()
    st["installed"] = bool(target)
    st["target"] = target
    st["ours"] = bool(target) and target.lower() == exe.lower()
    return st


def set_startup(enabled: bool) -> dict:
    """开/关开机自启。**只在真把快捷方式建出来/删掉后才说成功**。

    关的时候如果那个 `.lnk` 指向的不是本应用(用户自己放了同名快捷方式、或换了
    安装目录),**拒绝动手** —— 这是别人的文件,删了不可恢复,只能如实说。
    """
    import os
    st = startup_status()
    if not st["supported"]:
        return {"ok": False, "enabled": False,
                "msg": "这个系统/形态没有可写的「启动」文件夹,开不了开机自启"}
    lnk = st["lnk"]
    if not enabled:
        if not st["installed"]:
            return {"ok": True, "enabled": False, "msg": "本来就没开"}
        if not st["ours"]:
            return {"ok": False, "enabled": True,
                    "msg": f"「启动」里那个 {STARTUP_NAME} 指向的是 "
                           f"{st['target'] or '(空)'},不是本应用,不动别人的文件"}
        try:
            os.remove(lnk)
        except OSError as e:
            return {"ok": False, "enabled": True,
                    "msg": f"删不掉:{type(e).__name__}: {e}"}
        log("开机自启:关(已删启动文件夹快捷方式)")
        return {"ok": True, "enabled": False}
    exe, args, workdir = launch_target()
    icon = resource_path("assets", "gcode.ico")
    r = _run_ps(_STARTUP_MAKE_PS, {
        "YX_LNK": lnk, "YX_TARGET": exe, "YX_ARGS": args,
        "YX_WORKDIR": workdir, "YX_DESC": STARTUP_DESC,
        "YX_ICON": str(icon) if os.path.isfile(icon) else ""})
    if not r.get("ok"):
        return {"ok": False, "enabled": False, "msg": r.get("msg") or "PowerShell 没建成"}
    after = startup_status()
    ours = bool(after["installed"] and after["ours"])
    if not ours:
        return {"ok": False, "enabled": False,
                "msg": f"快捷方式建出来了但指向不对(读到 "
                       f"{after['target'] or '空'}),不敢算已开启"}
    log(f"开机自启:开({lnk} → {exe})")
    return {"ok": True, "enabled": True, "lnk": lnk, "target": exe}


def startup_stale() -> dict:
    """自启「位置变了」探针(§6 #16 剩的账):勾看着是开、开机却不会响。

    程序重装/挪位置后,「启动」文件夹里的快捷方式仍指旧路径 —— 文件在,
    `startup_present()` 照样 True,托盘勾照样打着,开机启动的是旧位置那份
    (或什么都没了)。这里把「指向的不是正在跑的这一份」查实,并分两种:

    - `same_app=True`(目标 exe 与现在的**同名**):是本应用自己的快捷方式
      换过位置/重装过 —— 可以安全重建,`stale=True`;
    - 指向别的程序:`same_app=False`、`stale=False` —— 那是别人的文件,
      与 `set_startup` 关闭分支同一条规矩,报告可以、动手不行。

    冷启动一次 PowerShell(一两秒),只在启动核对与用户问到时跑,
    **不进托盘菜单回调**(§4:右键画勾选态绝不起 PowerShell)。
    """
    import os
    st = startup_status()
    out = {"checked": bool(st["supported"] and st["installed"]),
           "stale": False, "same_app": False,
           "target": st.get("target", ""), "want": st.get("want", ""),
           "lnk": st.get("lnk", "")}
    if not out["checked"]:
        return out
    want_name = os.path.basename(st["want"]).lower()
    tgt_name = os.path.basename(st["target"]).lower()
    out["same_app"] = bool(want_name) and want_name == tgt_name
    out["stale"] = bool(out["same_app"] and not st["ours"])
    return out


def repair_startup_shortcut() -> dict:
    """把「启动」里的快捷方式重新指到**现在这份**程序(仅限 stale 场景)。

    动手前**再查一遍** `startup_stale()`:从探针到修复之间用户可能自己换了
    指向 —— 拿着旧结论动手,就是把别人的文件覆盖掉。指向别的程序时拒绝
    并说清为什么;能重建时走 `set_startup(True)` 同一条路(建完读回指向核对,
    没核对上不会说已开启)。
    """
    st = startup_stale()
    if not st["checked"]:
        return {"ok": False, "msg": "没有可核对的快捷方式(本来就没开自启,或系统不支持)"}
    if not st["same_app"]:
        return {"ok": False,
                "msg": f"「启动」里那个快捷方式指向的是别的程序"
                       f"({st['target'] or '(空)'}),不是本应用的旧位置,不动它"}
    return set_startup(True)


def check_stale_startup(ui) -> dict:
    """启动时顺手核对一遍自启指向(v0.9.98,#16 剩的那句「启动时核对并提示」)。

    只在**同应用的旧位置**时开口:界面 toast 一条 + 日志一行,话里带上怎么修
    (对学长说「修好开机自启」)。指向别的程序的不出声 —— 那是别人的文件,
    每次启动都打扰等于把他自己的安排当故障报。调用方(post_startup)用
    守护线程调它:PowerShell 冷启动要一两秒,别挡窗口起来。
    """
    st = startup_stale()
    if not st["checked"]:
        return st
    if not st["stale"]:
        log("开机自启核对:指向正确")
        return st
    msg = ("开机自启的快捷方式还指向旧位置,开机不会启动现在这份学长 —— "
           "对我说「修好开机自启」就能重新指过来。")
    log(f"开机自启核对:指向旧位置({st['target']}),现在是 {st['want']};"
        "托盘勾还打着是已知边界(画勾不起 PowerShell)")
    try:
        ui.push("info", text=msg)
    except Exception:
        pass        # 界面没起来/正在关:日志里已有这一行,不为一条提示炸启动
    return st


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
        # 托盘菜单里「最近会话」那一叠的取法由桥交进来(v0.9.114):托盘不持有桥,
        # 只拿一个 callable —— 否则这里会开始猜桥的结构。
        self.recent_fn = None
        self._menu_stop = threading.Event()

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
            if self.hide_to_tray():
                return {"ok": True, "action": "hide"}
            # 藏不了就**别退出**:同学点的是"收起来",不是"关掉"。窗口这会儿已经被
            # FormClosing 拦下(args.Cancel),留着它 + 一句原因,比"程序自己没了、
            # 托盘里啥也没有"诚实 —— v0.9.114 那次就是走这条旧兜底把用户的会话窗口
            # 连着进程一起送走了(根因见 _recent_opener 那段)。
            return {"ok": False,
                    "msg": "托盘图标没建起来,窗口先留着没动 —— 原因写在数据目录的 tray.log"}
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
                icon = pystray.Icon("yinluxs", image, "引路学长",
                                    self._build_menu(), visible=True)
            except Exception as e:
                log(f"托盘图标创建失败: {type(e).__name__}: {e}")
                return False
            self.icon = icon
        threading.Thread(target=self._run_icon, args=(icon,), daemon=True).start()
        self._start_menu_refresher(icon)
        log("托盘图标已就绪")
        return True

    # ------------------------------------------------------------ 菜单(v0.9.114 照 Qoder 重排)

    def _build_menu(self):
        """托盘那颗图标右键的菜单:窗口动作在最上、最近会话居中、设置与退出压底。

        以前只有"显示主窗口 + 四个偏好开关",想开个新会话得先把窗口点在眼前再找按钮;
        图 p8/p9 拿 Qoder 的菜单对比,要的就是"从托盘直接落到那件事上"。
        """
        import pystray
        items = [
            pystray.MenuItem("显示主窗口", self._tray_show, default=True),
            pystray.MenuItem("新建任务",
                             lambda i=None, it=None: self._tray_open("new_task")),
        ]
        recent = self._recent_sessions()
        if recent:
            items.append(pystray.MenuItem(
                "最近会话", pystray.Menu(*[
                    pystray.MenuItem(f"{n + 1}. {title}", self._recent_opener(key))
                    for n, (key, title) in enumerate(recent)])))
        items += [
            pystray.MenuItem("设置(模型与密钥)",
                             lambda i=None, it=None: self._tray_open("providers")),
            pystray.MenuItem("桌面提醒(课前/简报/待办/通知)",
                             self._tray_toggle_notify,
                             checked=lambda item: notify_enabled()),
            pystray.MenuItem("测试一条桌面通知", self._tray_test_notify),
            pystray.MenuItem("开机自动启动", self._tray_toggle_startup,
                             checked=lambda item: startup_present()),
            pystray.MenuItem("点 X 时先问我", self._tray_reset_ask),
            pystray.MenuItem("退出引路学长", self._tray_quit),
        ]
        return pystray.Menu(*items)

    def _recent_opener(self, key: str):
        """「最近会话」那一条的点击动作:**必须只收两个位置参数**。

        pystray 在 `MenuItem.action` 赋值处按 `__code__.co_argcount` 分流:0 和 1 它
        自己包一层,2 原样收下,**其他一律 `raise ValueError(action)`**
        (`pystray/_base.py:_assert_action`)。所以"用默认参把循环变量钉住"这个惯常
        写法在这里会连带把整颗图标炸掉:v0.9.114 那句 `lambda i=None, it=None, k=key`
        是三个参数,表现就是用户报的「选了最小化到托盘,程序自己退了、托盘里啥也没有」
        —— 图标建不起来 → `hide_to_tray()` 回 False → 旧兜底当场 quit。
        工厂函数每次调用捕获一个 key,既满足 arity,也不会"五条都是最后一条"。
        """
        def act(icon=None, item=None) -> None:
            self._tray_open(f"session:{key}")
        return act

    def set_recent_provider(self, fn) -> None:
        """桥把"最近几条会话怎么取"交给托盘 —— 托盘自己不持有桥(它只拿得到一个 callable)。"""
        self.recent_fn = fn
        self._refresh_menu()

    def _recent_sessions(self) -> list:
        """[(留档名或序号, 标题)]。取不到就回空 —— 空的话整条「最近会话」不摆,
        而不是摆五条"(空)"骗人。"""
        fn = getattr(self, "recent_fn", None)
        if not callable(fn):
            return []
        try:
            rows = fn() or []
        except Exception as e:
            log(f"托盘读最近会话失败: {type(e).__name__}")
            return []
        out = []
        for row in rows[:MENU_RECENT]:
            try:
                key, title = row
            except (TypeError, ValueError):
                continue
            title = str(title or "").strip().replace("\n", " ")
            if not title:
                continue
            out.append((str(key), title[:26] + "…" if len(title) > 26 else title))
        return out

    def _refresh_menu(self) -> None:
        icon = self.icon
        if icon is None:
            return
        try:
            icon.menu = self._build_menu()   # 赋值即 update_menu(win32 要重建 HMENU)
        except Exception as e:
            log(f"托盘菜单没刷新: {type(e).__name__}")

    def _start_menu_refresher(self, icon) -> None:
        """win32 的托盘菜单是**建好就缓存**的(pystray 只在 update_menu 时重建),
        所以「最近会话」那一叠要定时重摆一次。20 秒一轮、daemon,退出时停。"""
        def loop():
            while not self._menu_stop.wait(MENU_REFRESH_SECONDS):
                try:
                    icon.menu = self._build_menu()
                except Exception:
                    return
        threading.Thread(target=loop, daemon=True, name="tray-menu").start()

    def _tray_open(self, panel: str) -> None:
        """托盘里点「新建任务 / 设置 / 某条会话」:先把窗带到眼前,再把动作交给界面。

        托盘**不碰桥**:它推的还是 `open_panel` 那一个事件,界面侧只有一个出口
        (`openPanel`)。多一条"托盘直接调桥"的路,就会有两处决定"什么算新任务"。
        """
        self._on_ui_thread(self._show_form)
        try:
            self.ui.push("open_panel", panel=panel)
        except Exception as e:
            log(f"托盘动作没送到界面({panel}): {type(e).__name__}")

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

    def _tray_toggle_startup(self, icon=None, item=None) -> None:
        """翻转开机自启。真建好/删掉了才算数,失败就把"想开/想关"和原因一起记下。

        记的是 `want` 而不是结果:失败时 `enabled` 恒为 False,拿结果写日志会把
        "点了开没开成"记成"关",看日志的人反而找不到那次操作。
        """
        want = not startup_present()
        r = set_startup(want)
        log(f"托盘:开机自启 → {'开' if want else '关'}"
            + ("" if r.get("ok") else f" (失败: {r.get('msg')})"))

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
        self._menu_stop.set()          # 那条 20 秒重摆菜单的线程先停,别再碰 icon
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
    if _RECENT_PROVIDER is not None:
        controller.recent_fn = _RECENT_PROVIDER   # 桥比托盘先起来,这里补挂上去
    if not controller.install():
        return None
    _CONTROLLER = controller
    return controller


def set_recent_provider(fn) -> None:
    """桥交来"最近几条会话怎么取"(v0.9.114 托盘那一叠「最近会话」)。

    为什么走模块级:启动顺序是 `create_app` 先出桥、`webview.start` 之后才建托盘,
    所以交进来的 callable 得先存着,等 controller 建出来再挂 —— 两头都不假设顺序。
    """
    global _RECENT_PROVIDER
    _RECENT_PROVIDER = fn
    if _CONTROLLER is not None:
        _CONTROLLER.set_recent_provider(fn)


def start_in_tray() -> bool:
    """`--background` 自启:窗口已按 hidden 创建,这里只保证托盘在、并把话说清楚。

    自启的唯一职责是"让学长在跑",好让课前提醒和补发发得出去;可开机就糊一个
    1280×820 的窗口在脸上,用户下一步就是关掉自启。所以 `gui_main` 建窗口时带
    `hidden=True`,本函数只验托盘图标起没起 —— **起不来一定要返回 False**,
    调用方会照常显示窗口:进程在跑但界面永远叫不出来,比多一个窗口糟得多。
    """
    c = _CONTROLLER
    if c is None:
        log("后台启动:托盘控制器还没就绪,窗口保持显示")
        return False
    ok = c.hide_to_tray()      # 里面先建图标,建不出来就返回 False
    log("后台启动:" + ("已收进托盘,双击托盘图标叫出窗口" if ok else
                       "托盘没起来,窗口照常显示"))
    return bool(ok)


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
               "notices": "校园新通知", "update": "新版本",
               "weather": "天气提醒", "review": "每周回顾", "gaps": "缺口周报",
               "recovery": "模型恢复提醒", "background": "后台命令跑完"}


def desktop_notify(ctx, action: str = "status", kind: str = "",
                   reason: str = "") -> str:
    """desktop_notify 工具处理器:用嘴开关桌面提醒 / 看状态 / 当场试一条。"""
    import os
    action = (action or "status").strip().lower()
    kind = (kind or "").strip().lower()
    if kind and kind not in NOTIFY_KINDS:
        return f"不认识的类别「{kind}」。可选项:" + "、".join(NOTIFY_KINDS)
    st = notify_status()
    if action == "status":
        last = st["last"]
        su = startup_status()
        if not su["supported"]:
            startup_line = "开机自启:这个系统/形态没有可写的「启动」文件夹,开不了。"
        elif su["installed"] and su["ours"]:
            startup_line = f"开机自启:已开启({su['lnk']})。"
        elif su["installed"]:
            # 同名 exe 但路径不同 = 本应用换过位置/重装过(v0.9.98 起 vs 别人的文件)
            same = (os.path.basename(su["want"]).lower()
                    == os.path.basename(su["target"] or "").lower())
            if same:
                startup_line = (f"开机自启:**指错位置** —— 「启动」里的快捷方式指向 "
                                f"{su['target'] or '(空)'},不是现在正在跑的这一份"
                                f"(程序换过位置或重装过),开机不会启动现在这份。"
                                f"说「修好开机自启」我就把它重新指过来。")
            else:
                startup_line = (f"开机自启:没开 —— 「启动」里有个同名快捷方式但指向 "
                                f"{su['target'] or '(空)'},不是本应用,学长不会去动它。")
        else:
            startup_line = "开机自启:没开(说「开机自动启动学长」可以打开)。"
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
                + f"\n{startup_line}\n最近一次:"
                + ("没发过" if last.get("ok") is None else
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
    if action in ("startup_on", "startup_off"):
        want = action == "startup_on"
        r = set_startup(want)
        if not r.get("ok"):
            return ("开机自启没能" + ("打开" if want else "关掉") + ":"
                    + r.get("msg", "未知原因")
                    + "\n学长按原样在跑,现在的提醒一条没少,只是下次开机要先自己打开学长。")
        if want:
            return ("已开启开机自启:开机后学长会在后台等着,课前提醒、今日简报、"
                    "错过的补发都会照常弹。\n快捷方式:" + str(r.get("lnk", ""))
                    + "\n不想要了在托盘菜单里取消勾选,或直接跟学长说一声。")
        return "已关掉开机自启(启动文件夹里那个快捷方式已删除)。开机后需要自己打开学长,提醒才发得出去。"
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
                + "\n再问一句「看看托盘日志」(action=tray_log)就能把原因读出来;"
                  "主动提醒仍会显示在聊天窗口顶部那条提示栏里。")
    if action == "jobs":
        # 定时任务可观测(v0.9.79):「这周的任务跑没跑、为什么没跑」的查询口。
        from minicode import jobs as _jobs
        from minicode import proactive          # 函数内引:proactive 也会引 tray
        return _jobs.snapshot(proactive.JOB_SPECS)
    if action == "log":
        from minicode import proactive          # 函数内引:proactive 也会引 tray
        rows = proactive.wd_log_tail(30)
        path = proactive.watchdog_log_path()
        if not rows:
            return ("看门狗还没有留下一行运行记录(" + str(path) + ")。\n"
                    "通常是这三种情况之一:这个形态没跑桌面版看门狗(命令行/网页版)、"
                    "启动还不到 15 秒、或这台机器的数据目录写不进去。"
                    "先用 action=test 自证通知通道通不通。")
        return ("最近 " + str(len(rows)) + " 条提醒运行记录(" + str(path) + "):\n"
                + "\n".join(rows)
                + "\n读法:每行开头 push=真推了 / skip=被拦下(后面就是原因)/ "
                  "fail=想推但两路都没送到 / round error=这一轮抛异常。"
                  "「今天已推过」是按天账本(pushed-today.json)在去重,"
                  "「今日简报已覆盖」是简报把单条提醒顶掉了。"
                  "日志里只有类别和去重键,没有通知正文。")
    if action == "tray_log":
        rows = log_tail(30)
        path = _ensure_parent(app_path(LOG_FILE))
        if not rows:
            return ("托盘还没留下一行诊断(" + str(path) + ")。\n"
                    "通常是:这个形态没起托盘(命令行/网页版),或这台机器的数据目录写不进去。")
        return ("最近 " + str(len(rows)) + " 条托盘与通知诊断(" + str(path) + "):\n"
                + "\n".join(rows)
                + "\n读法:这里记的是**动作走没走通**——托盘图标建没建起来、桌面通知"
                  "两条通道(toast/气球)各自的结果、开机自启那条快捷方式指向哪个 exe、"
                  "关窗拦截接上没有。查「通知为什么没弹」「点 X 怎么直接退了」"
                  "「开机自启没生效」看这份;查「今天该提醒的提醒了没有」看 action=log。"
                  "里面没有通知正文,但会有本机的绝对路径 —— 截图给同学前自己看一眼。")
    if action == "gui_log":
        rows = warn_log_tail(30)
        path = _ensure_parent(app_path(WARN_LOG_FILE))
        if not rows:
            return ("界面警告日志还是空的(" + str(path) + ")—— "
                    "说明这台机器上没记到任何一次失败,原生右键菜单与编辑快捷键那一段"
                    "没抛过异常。用户说「右键粘贴不出来/Ctrl+C 没用」而这里又是空的,"
                    "就别拿日志当证据,直接让他试一次快捷键,再按 action=test 那条链路查通知通道。")
        return ("最近 " + str(len(rows)) + " 条界面层警告(" + str(path) + "):\n"
                + "\n".join(rows)
                + "\n读法:这份只记**原生右键菜单与编辑快捷键**那一段的成败 —— "
                  "「已开启(验证标记)」= 成功,「打开原生右键菜单失败: 异常类型」= "
                  "开起来抛错了,「等待 WebView2 就绪超时」= 窗口起来了但开关没能挂上"
                  "(表现就是对话框里右键没有粘贴、Ctrl+V 没反应,其余功能不受影响)。"
                  "查「通知为什么没弹」看 action=tray_log,查「今天提醒了没」看 action=log。")
    return (f"未知的 action「{action}」,可用:" + " / ".join(NOTIFY_ACTIONS))
