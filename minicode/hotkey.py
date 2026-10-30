"""全局热键唤起(v0.9.186):在任何应用里按热键把学长叫到眼前。

托盘常驻之后,「叫回学长」要伸手去找托盘图标;这条线给一个键盘直达:
`Ctrl+Alt+Y`(config.summon_hotkey,可改、置空即关)。唤起动作**不走第二份
逻辑** —— 热键线程只负责"键被按下了"这一件事,把窗口带到眼前调的是
`TrayController.show_main()`,与托盘菜单「显示主窗口」同一条路。

实现口径:`RegisterHotKey(NULL, ...)` 把 `WM_HOTKEY` 投递到**注册线程自己的
消息队列**,所以注册与 `GetMessageW` 循环都在同一条 daemon 线程上做(应用退出
热键随进程消失,不需要中途注销;改键在 config.json 手改后重启生效)。win32
函数逐条声明 argtypes/restype —— 64 位句柄被 ctypes 按 32 位截断时不会报错,
只会静默失效(v0.9.135 Job Object 那条教训,§6 52)。

**诚实边界(与 window_chrome 同一口径)**:本会话在非交互桌面站上,连
CreateWindowExW 都不返回,真按键到窗口弹回这一段**没有自动化验证** ——
测试钉的是键位解析、配置闸与线程生命周期(mock user32),真机行为以
`tray.log` 里"注册上了/没注册上"那两句话为准。热键被别的程序占用时**如实
说没注册上**,绝不假装注册成功继续跑。
"""
from __future__ import annotations

import ctypes
import re
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x594C        # "YL" —— 只要非零即可,线程自用

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
         "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN}
_VK_F = re.compile(r"f([1-9]|1[0-2])$")
_VK_SINGLE = re.compile(r"[a-z0-9]$")


def parse_combo(s) -> tuple[int, int] | None:
    """「Ctrl+Alt+Y」→ (modifiers, virtual_key);不认的组合一律 None。

    主键只收单个字母/数字与 F1~F12;**必须带至少一个修饰键** —— 不带修饰的
    全局热键等于把这个键从全系统手里占走,那种配置不该被默默接受。
    """
    text = str(s or "").strip()
    if not text:
        return None
    mods, key = 0, None
    for part in text.split("+"):
        p = part.strip().lower()
        if not p:
            return None
        if p in _MODS:
            mods |= _MODS[p]
        elif _VK_SINGLE.fullmatch(p):
            key = ord(p.upper())
        elif _VK_F.fullmatch(p):
            key = 0x70 + int(p[1:]) - 1      # VK_F1 = 0x70
        else:
            return None
    if key is None or mods == 0:
        return None
    return mods, key


def _user32():
    """真 user32(原型逐条声明,防 64 位句柄被按 32 位截断的静默失效)。

    给测试留的桩点:非交互桌面站上真 user32 的行为验不了,测试 monkeypatch
    这个名字换 fake —— 所以原型声明只放在这一条真路径里,不进 _pump。
    """
    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                      wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    return user32


def start(summon, combo, log=None) -> dict:
    """起热键线程。combo 置空 = 关;写法看不懂 = 不起线程并说明,绝不静默。"""
    log = log or (lambda m: None)
    text = str(combo or "").strip()
    if not text:
        return {"ok": False, "msg": "全局热键未配置(summon_hotkey 为空,即关闭)"}
    parsed = parse_combo(text)
    if parsed is None:
        return {"ok": False,
                "msg": f"热键写法看不懂:「{text}」(要像 Ctrl+Alt+Y 这样,主键只认"
                       "字母/数字/F1~F12,且必须带修饰键)"}
    mods, vk = parsed
    threading.Thread(target=_pump, args=(summon, text, mods, vk, log),
                     daemon=True, name="global-hotkey").start()
    return {"ok": True, "msg": "", "combo": text}


def _pump(summon, combo: str, mods: int, vk: int, log) -> None:
    """注册 + 消息循环,都在这一条线程上(热键消息只投给注册线程)。"""
    user32 = _user32()
    if not user32.RegisterHotKey(None, HOTKEY_ID, mods, vk):
        log(f"全局热键 {combo} 没注册上(错误码 {ctypes.GetLastError()};"
            "多半被别的程序占用了)—— 换一个组合改 config.json 的 summon_hotkey,"
            "其余一切不受影响")
        return
    log(f"全局热键 {combo} 已注册:随时把它按下去,学长就到眼前")
    try:
        msg = wintypes.MSG()
        # pointer 而不是 byref:两者对真 user32 等价,但测试桩要解引用收消息
        while user32.GetMessageW(ctypes.pointer(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                try:
                    summon()
                except Exception as e:
                    log(f"热键唤起失败: {type(e).__name__}: {e}")
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
