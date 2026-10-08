"""Windows 的 Job Object:让"终止"是真的终止,让"跑飞了"有上限(P0-5 的 S5a)。

现在的安全形状是**黑名单 + 确认框**,也就是"问人"那一派;Codex(Landlock+seccomp /
Seatbelt)、Claude Code、Cursor、Cline 都有操作系统级的约束。这一层补的是其中最没有
争议的一半 —— 不做降权、不做 AppContainer,只做**进程树可回收 + 资源上限**:

- `bash_kill` 今天靠 `taskkill /T /F` 数进程树。那是对的,但它是"用户态数一遍",
  而 Job Object 是操作系统记着这笔账:一条命令 fork 出来的孙子、曾孙都在同一个 job 里,
  关句柄就一起没。**"只杀了直接子进程 —— 下面的若还活着,学长无从确认"这句道歉
  从此有了确定的替代**。
- 前台超时那条路以前只杀直接子进程(`proc.kill()`),留下孤儿继续吃 CPU;挂上 job 之后
  超时收尾连整棵树一起收。
- 内存与进程数上限是**兜底不是判据**:防的是"模型写了一段递归把这台机器填满",
  默认给得很宽,而且报得出口(被 job 限制杀掉的那一次会说出来)。

四条规矩:
① **绝不为沙箱拒绝执行**。这一层是"限制",不是"许可":起不来(非 Windows、API 失败、
   父进程自己已被关进一个不许嵌套的 job)就照常跑命令,只是**照实说没启用** ——
   与项目"不许假装有沙箱"同一条根,`status()` 那句是唯一真相来源。
② **主进程绝不进 job**。job 只装学长** spawn 出来**的那些进程;把自己关进去,关窗时
   pywebview 会被自己杀掉(这条有针,因为它的坏法是"偶发"的)。
③ **降级要看得见**,不是静默少一层:`bash` 的回话尾部带一句"约束:整棵进程树可回收"
   或"约束:未启用(原因)",用户与模型都看得到现在这一层在不在。
④ **不做网络**。Job Object 不管网络(AppContainer 才管),所以文档与回话里永远不许
   出现"网络被沙箱挡住"这种话 —— 那会把 S5a 说成 S5c。

`ctypes` 全走 stdlib,零新依赖;所有 Win32 调用失败都收敛成"不可用",不抛出去。
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JobObjectBasicLimitInformation = 2          # JOBOBJECTINFOCLASS 枚举值
JobObjectExtendedLimitInformation = 9

# 默认上限。**刻意给得宽**:这一层不是为了掐死正常活儿,是为了"跑飞了能收场"。
# 全量 pytest、构建、装依赖都要真跑,默认值把这些都装得下。
DEFAULT_MEMORY_BYTES = 2 * 1024 * 1024 * 1024      # 单进程 2 GB
DEFAULT_ACTIVE_PROCESS = 512                        # 树里同时活的进程数

_IS_WIN = sys.platform == "win32"


@dataclass
class Limits:
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    active_process: int = DEFAULT_ACTIVE_PROCESS
    kill_on_close: bool = True

    def flags(self) -> int:
        n = 0
        if self.kill_on_close:
            n |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if self.memory_bytes > 0:
            n |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
        if self.active_process > 0:
            n |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        return n


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kern():
    """取 kernel32 并把**函数原型**设好。

    这一步不是讲究,是必须:`ctypes` 对没声明原型的函数按 `int` 处理返回值与参数,
    而 64 位 Windows 上的 HANDLE 是 64 位 —— 截成 32 位之后 `AssignProcessToJobObject`
    拿到的是一个假句柄,表现是"job 建出来了、进程却挂不进去",而且**不报错**。
    实测过这条路(第一版就是这么写的),所以原型逐条写全。
    """
    kern = ctypes.windll.kernel32
    if getattr(kern, "_minicode_protos", False):
        return kern
    BOOL = ctypes.c_int
    kern.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kern.CreateJobObjectW.restype = ctypes.c_void_p
    kern.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_void_p, ctypes.c_ulong]
    kern.SetInformationJobObject.restype = BOOL
    kern.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kern.AssignProcessToJobObject.restype = BOOL
    kern.CloseHandle.argtypes = [ctypes.c_void_p]
    kern.CloseHandle.restype = BOOL
    kern.GetLastError.restype = ctypes.c_uint32
    kern._minicode_protos = True
    return kern


def _last_error() -> int:
    try:
        return int(_kern().GetLastError())
    except Exception:
        return 0


class Job:
    """一个 Job Object 句柄。用法:`job = Job.create(limits)`;`job.attach(proc)`;
    用完 `job.close()` —— **关闭句柄就是回收整棵树**(KILL_ON_JOB_CLOSE)。"""

    last_error = ""

    def __init__(self, handle, limits: Limits):
        self._h = handle
        self.limits = limits

    @property
    def handle(self):
        return self._h

    @classmethod
    def create(cls, limits: Limits | None = None) -> "Job | None":
        """建一个 job;任何一步失败返回 None(调用方按"未启用"处理,不抛)。"""
        if not _IS_WIN:
            cls.last_error = "不是 Windows"
            return None
        lim = limits or Limits()
        try:
            kern = _kern()
            handle = kern.CreateJobObjectW(None, None)
            if not handle:
                cls.last_error = f"CreateJobObjectW 失败(错误码 {_last_error()})"
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = lim.flags()
            info.BasicLimitInformation.ActiveProcessLimit = lim.active_process
            info.ProcessMemoryLimit = lim.memory_bytes
            ok = kern.SetInformationJobObject(
                handle, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                kern.CloseHandle(handle)
                cls.last_error = (f"SetInformationJobObject 失败(错误码 {_last_error()})"
                                  " —— 这台机器不许设上限")
                return None
            cls.last_error = ""
            return cls(handle, lim)
        except Exception as e:                    # noqa: BLE001  Win32 层什么都可能抛
            cls.last_error = f"{type(e).__name__}: {e}"
            return None

    def attach(self, proc) -> bool:
        """把一个已启动的子进程关进这个 job。失败返回 False(命令照跑,只是没这层)。

        句柄取的是 `Popen._handle`:CPython 的 Windows `Popen` 把这个内核句柄存在
        `_handle` 里(一个 `subprocess.Handle`,是 int 的子类),而公开属性 `handle`
        **不是每个版本都有** —— 本机实测 3.12 就没有,只写 `proc.handle` 的表现是
        "job 建好了、进程永远挂不进去,而且不报错"。两个名字都试,取到整数才算数。

        为什么在 Popen **之后** assign 而不是用 CreateProcess 的 job 参数:Python 的
        `subprocess` 不暴露那个槽。这里的窗口期是"进程刚起来到被关进去之间"—— 它若
        在这几十微秒里已经 spawn 了下一代,那一代会漏在 job 外面。这个残余风险写在
        回话里(`_job_tail` 那句"启动那一瞬在 job 之外起来的进程不在此列"),不假装没有。
        """
        if not _IS_WIN or self._h is None:
            return False
        handle = getattr(proc, "handle", None) or getattr(proc, "_handle", None)
        if not handle:
            self.__class__.last_error = "拿不到进程句柄(进程可能已经退出)"
            return False
        try:
            ok = _kern().AssignProcessToJobObject(self._h, int(handle))
            if not ok:
                self.__class__.last_error = (
                    f"AssignProcessToJobObject 失败(错误码 {_last_error()})")
                return False
            return True
        except Exception as e:                    # noqa: BLE001
            self.__class__.last_error = f"{type(e).__name__}: {e}"
            return False

    def close(self) -> None:
        """关句柄。设了 KILL_ON_JOB_CLOSE 时,这一步就是"整棵树一起没"。"""
        if _IS_WIN and self._h:
            try:
                _kern().CloseHandle(self._h)
            except Exception:
                pass
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def note(job, attached: bool) -> str:
    """给回话与确认框那一句"这一层在不在"的人话。说不了假话:未启用就写未启用。"""
    if job is None:
        why = getattr(Job, "last_error", "") or "这台机器上建不出 job"
        return f"进程约束:**未启用**({why};这一条只影响能不能连进程树一起收,不影响命令照常跑)"
    if not attached:
        return ("进程约束:**未启用**(进程没关进 job —— 关掉时只杀直接子进程,"
                "下面的子进程可能还活着,学长不会替你说『已全部终止』)")
    return ("进程约束:已启用 —— 这一棵进程树被操作系统记着,终止时连孙子进程一起回收;"
            "单进程内存上限 2 GB、树内进程数上限 512(**不管网络**,禁网要另一层)")
