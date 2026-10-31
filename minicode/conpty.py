"""Windows ConPTY 交互式通道(计划书阶段 6,v0.9.196)。

管道(`_bg_jobs` 那条)对"跑完给输出"的命令是对的,但 `python`(REPL)、
`npm init`、分页器这类**交互式程序**在管道里会检测到"没有控制台":不出提示符、
不逐屏刷,体验是死的。ConPTY(伪控制台,Win10 1809+ 的 kernel32 API)给子进程
一个**真的控制台**,这类程序就能正常交互。

这一格的边界(计划书点名的,每条都有测试或真跑验证):
- **Job Object 接入**:进程起来即关进 job,**关闭句柄就是回收整棵树**(复用
  win_sandbox.Job,与 bash_kill 同一套)—— 关窗/停/杀都能收干净。
- **降级要明说**:非 Windows 或 ConPTY API 不在(Win10 1809 前)→ `available()`
  返回 False,调用方如实说"PTY 模式不可用",**不假装是 PTY**。
- **不绕权限门**:会话的**起手命令**照走 `check_command` 的硬闸(与终端坞同一条);
  会话内后续输入进的是"已放行的那个 shell"—— 与真终端语义一致,闸管的是
  "起哪条命令",不是"跟已放行的会话说什么"。
- **已知边界如实写**:ConPTY 输出是控制台编码的字节流 + VT 序列;首行会自动发
  `[Console]::OutputEncoding=UTF8` 让 PowerShell 转成 UTF-8,残余的 VT 控制序列
  与非 PS 程序的编码问题不做翻译(界面上可能看到少量控制符)。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

# wintypes 没有 STARTUPINFOW(只在 ctypes.windll 场景的私有表里)—— 自己摆:
# 与 Win32 的 STARTUPINFOW 二进制兼容的最小子集(本用途不读它的其余字段)。
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STILL_ACTIVE = 259
INFINITE = 0xFFFFFFFF


def _setup_argtypes():
    """全部句柄相关函数逐条声明原型 —— 64 位句柄被 ctypes 按 32 位截断时
    **不报错、只静默失效**(v0.9.135 Job Object 的教训,§6 52)。"""
    k = ctypes.windll.kernel32
    k.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                             ctypes.POINTER(wintypes.HANDLE),
                             ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.DWORD]
    k.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    k.CreatePseudoConsole.argtypes = [_COORD, wintypes.HANDLE, wintypes.HANDLE,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    k.ResizePseudoConsole.argtypes = [wintypes.HANDLE, _COORD]
    k.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                    wintypes.DWORD,
                                                    ctypes.POINTER(ctypes.c_size_t)]
    k.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.DWORD)]
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]



def available() -> bool:
    """这台机器有没有 ConPTY(Win10 1809+ 的 kernel32 才有)。非 Windows = False。"""
    import sys
    if sys.platform != "win32":
        return False
    return hasattr(ctypes.windll.kernel32, "CreatePseudoConsole")


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW),
                ("lpAttributeList", ctypes.c_void_p)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]

_setup_argtypes()


class PtyError(RuntimeError):
    """起会话失败。message 是给人看的一句话。"""


class PtySession:
    """一个 ConPTY 会话:交互式 shell 跑在伪控制台里。

    用法:`s = PtySession.spawn([...], cwd=...)`;`s.write("命令\r\n")`;
    `s.tail()` 取输出尾部;`s.resize(cols, rows)`;`s.close()` 杀树收干净。
    `s.running` / `s.exit_code` 随时可问。
    """

    def __init__(self, pi: _PROCESS_INFORMATION, conpty, h_in_write, h_out_read,
                 job, cwd: str):
        self._hpc = conpty
        # 句柄是 64 位的:不设 argtypes 时 ctypes 按 32 位 int 传,溢出报
        # OverflowError(v0.9.135 Job Object 那条教训的同族,§6 52)。
        k = ctypes.windll.kernel32
        k.ResizePseudoConsole.argtypes = [wintypes.HANDLE, _COORD]
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.DWORD)]
        k.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        self._h_in_w = h_in_write
        self._h_out_r = h_out_read
        self._proc = pi.hProcess
        self._thread = pi.hThread
        self._job = job
        self.cwd = cwd
        self._buf: list[str] = []
        self._buf_lock = threading.Lock()
        self._closed = False
        self._exit_code: int | None = None
        self._pump = threading.Thread(target=self._pump_loop, daemon=True,
                                      name="conpty-read")
        self._pump.start()

    def _pump_loop(self) -> None:
        k = ctypes.windll.kernel32
        buf = ctypes.create_string_buffer(65536)
        read = wintypes.DWORD(0)
        while not self._closed:
            if not k.ReadFile(self._h_out_r, buf, 65536, ctypes.byref(read), None):
                break
            if read.value == 0:
                break
            text = buf.raw[:read.value].decode("utf-8", errors="replace")
            with self._buf_lock:
                self._buf.append(text)
                joined = "".join(self._buf)
                if len(joined) > 200_000:
                    self._buf = [joined[-100_000:]]
        with self._buf_lock:
            if self._closed:
                return
            code = wintypes.DWORD(0xFFFFFFFF)
            k.GetExitCodeProcess(self._proc, ctypes.byref(code))
            self._exit_code = code.value
            k.ClosePseudoConsole(self._hpc)

    @property
    def running(self) -> bool:
        if self._closed or self._exit_code is not None:
            return False
        k = ctypes.windll.kernel32
        code = wintypes.DWORD(0xFFFFFFFF)
        k.GetExitCodeProcess(self._proc, ctypes.byref(code))
        if code.value == STILL_ACTIVE:
            return True
        self._exit_code = code.value
        return False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def write(self, text: str) -> None:
        if self._closed:
            raise PtyError("会话已经关闭")
        k = ctypes.windll.kernel32
        data = text.encode("utf-8")
        written = wintypes.DWORD(0)
        if not k.WriteFile(self._h_in_w, data, len(data),
                           ctypes.byref(written), None):
            raise PtyError("写 stdin 失败(会话可能已退出)")

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        size = _COORD(max(10, min(int(cols), 500)), max(5, min(int(rows), 300)))
        ctypes.windll.kernel32.ResizePseudoConsole(self._hpc, size)

    def tail(self, cap: int = 8000) -> str:
        with self._buf_lock:
            return "".join(self._buf)[-cap:]

    def close(self) -> None:
        """杀树收干净:TerminateProcess → ClosePseudoConsole → 句柄/Job 全收。"""
        if self._closed:
            return
        self._closed = True
        k = ctypes.windll.kernel32
        k.TerminateProcess(self._proc, 1)
        k.WaitForSingleObject(self._proc, 3000)
        try:
            k.ClosePseudoConsole(self._hpc)
        except Exception:
            pass
        for h in (self._h_in_w, self._h_out_r, self._thread, self._proc):
            try:
                k.CloseHandle(h)
            except Exception:
                pass
        if self._job is not None:
            self._job.close()

    @classmethod
    def spawn(cls, argv: list[str], *, cwd: str = "", cols: int = 110,
              rows: int = 24, first_line: str = "") -> "PtySession":
        """在伪控制台里起一个交互式程序;失败抛 PtyError(点名原因)。"""
        if not available():
            raise PtyError("这台机器没有 ConPTY(Win10 1809 前的 Windows 不带)")
        k = ctypes.windll.kernel32
        inherit = _SECURITY_ATTRIBUTES()
        inherit.nLength = ctypes.sizeof(inherit)
        inherit.bInheritHandle = True
        in_r, in_w = wintypes.HANDLE(), wintypes.HANDLE()
        out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
        if not k.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w),
                            ctypes.byref(inherit), 0) or \
           not k.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w),
                            ctypes.byref(inherit), 0):
            raise PtyError("建管道失败(错误码 %d)" % k.GetLastError())
        HANDLE_FLAG_INHERIT = 0x1
        # 我们自己拿的两端不再可继承:ConPTY 那两端才该传下去
        k.SetHandleInformation(in_w, HANDLE_FLAG_INHERIT, 0)
        k.SetHandleInformation(out_r, HANDLE_FLAG_INHERIT, 0)

        hpc = wintypes.HANDLE(0)
        hr = k.CreatePseudoConsole(_COORD(cols, rows), in_r, out_w, 0,
                                   ctypes.byref(hpc))
        if hr != 0:
            for h in (in_r, in_w, out_r, out_w):
                k.CloseHandle(h)
            raise PtyError("CreatePseudoConsole 失败(0x%08X)—— "
                           "这台机器的 ConPTY 不可用" % (hr & 0xFFFFFFFF))
        # ConPTY 已持有两根管的另一端:父进程把自己那两根"给 ConPTY 的"关掉
        # (MS 样本同款)—— 不关的话管道永远不发 EOF,而且可能干扰句柄继承。
        k.CloseHandle(in_r)
        k.CloseHandle(out_w)
        attr_size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
        attr = ctypes.create_string_buffer(attr_size.value)
        if not k.InitializeProcThreadAttributeList(attr, 1, 0,
                                                   ctypes.byref(attr_size)):
            k.ClosePseudoConsole(hpc)
            for h in (in_r, in_w, out_r, out_w):
                k.CloseHandle(h)
            raise PtyError("初始化进程属性失败")
        if not k.UpdateProcThreadAttribute(
                attr, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                ctypes.c_void_p(hpc.value), ctypes.sizeof(ctypes.c_void_p),
                None, None):
            k.DeleteProcThreadAttributeList(attr)
            k.ClosePseudoConsole(hpc)
            for h in (in_r, in_w, out_r, out_w):
                k.CloseHandle(h)
            raise PtyError("绑定伪控制台失败")

        siex = _STARTUPINFOEXW()
        siex.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        siex.lpAttributeList = ctypes.addressof(attr)
        pi = _PROCESS_INFORMATION()
        cmdline = ctypes.c_wchar_p(" ".join('"%s"' % a for a in argv))
        creflags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
        cwdp = ctypes.c_wchar_p(cwd) if cwd else None
        ok = k.CreateProcessW(None, cmdline, None, None, False, creflags,
                              None, cwdp, ctypes.byref(siex), ctypes.byref(pi))
        k.DeleteProcThreadAttributeList(attr)
        if not ok:
            k.ClosePseudoConsole(hpc)
            for h in (in_r, in_w, out_r, out_w):
                k.CloseHandle(h)
            raise PtyError("起进程失败(错误码 %d)" % k.GetLastError())

        job = None
        try:
            from minicode.win_sandbox import Job
            job = Job.create()
            if job is not None:
                job.attach(int(pi.hProcess))
        except Exception:
            job = None

        sess = cls(pi, hpc.value if hpc.value else hpc, in_w.value, out_r.value,
                   job, cwd)
        if first_line:
            sess.write(first_line)
        return sess
