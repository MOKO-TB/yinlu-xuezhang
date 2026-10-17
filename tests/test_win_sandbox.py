# -*- coding: utf-8 -*-
"""Windows Job Object:让"终止"是真的终止(P0-5 的 S5a,v0.9.135)。

这一层只做**进程树可回收 + 资源上限**,不做降权、不做 AppContainer,也**不管网络**。
所以本文件最重要的两条是:

- **真杀得掉**:起一条会自己 spawn 孙进程的命令,关掉句柄之后按 `tasklist` 查,
  父子都得不在 —— 这条不是"我以为 API 会这样",是本机量出来的。
- **起不来时照跑、照说**:这一层是"限制"不是"许可"。任何一步失败都必须降级成
  "命令照常执行 + 回话写明未启用",**绝不因为沙箱起不来而拒绝执行用户的命令**;
  也绝不假装"已启用" —— 界面上说安全而实际没拦,是这个项目列在禁忌里的最贵坏法。

`Popen` 的内核句柄在 CPython 的 Windows 实现里叫 `_handle`,公开属性 `handle` **不是
每个版本都有**(本机 3.12 就没有)。只写 `proc.handle` 的表现是"job 建好了、进程永远
挂不进去,而且不报错" —— 有一条针专门钉这件事,因为它坏起来完全静默。
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from minicode import win_sandbox as ws
from minicode.tools import shell

WIN = sys.platform == "win32"
needs_win = pytest.mark.skipif(not WIN, reason="这一层是 Windows 专属,非 Win 上不适用")

SLEEP = 120


def _spawn_tree(tmp_path: Path, on_start=None, before_spawn=1.0):
    """起一棵两层的进程树(父起子),返回 (父 Popen, [父 pid, 子 pid])。

    pid 走文件不走 stdout:管道要读、要解码、还要防它没 flush —— 一条写文件的语句
    把这三件事一次消掉。

    `before_spawn` 那一秒 + `on_start` 回调是为了**测真实顺序**:产品里
    (`tools/shell.py`)是 `Popen` 之后**立刻**挂 job,所以父进程后来 spawn 的孙进程
    自动落在同一个 job 里。回调就是那个"立刻"。传 None 就变成"先起完树再挂",
    那是另一条测试要故意复现的启动窗口。
    """
    out = tmp_path / "pids.txt"
    code = (
        "import subprocess, sys, time, os; "
        f"time.sleep({before_spawn}); "
        f"g = subprocess.Popen([sys.executable, '-c', "
        f"'import time; time.sleep({SLEEP})']); "
        f"open(r'{out}', 'w').write(str(os.getpid()) + ' ' + str(g.pid)); "
        f"time.sleep({SLEEP})")
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            creationflags=(subprocess.CREATE_NO_WINDOW if WIN else 0))
    if on_start is not None:
        on_start(proc)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if out.exists():
            try:
                text = out.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            parts = text.split()
            if len(parts) == 2:
                return proc, parts
        time.sleep(0.05)
    proc.kill()
    pytest.skip("25 秒内没拿到子进程 pid,这台机器上起不了这棵树")


def _pid_alive(pid: str) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    return f'"{pid}"' in r.stdout


def _reap(*pids):
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def _wait_gone(pids, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(_pid_alive(p) for p in pids):
            return []
        time.sleep(0.1)
    return [p for p in pids if _pid_alive(p)]


# ---------------------------------------------------------------- 真实进程树

@needs_win
def test_job_creates_and_attaches_a_live_process(tmp_path):
    box = {}

    def grab(p):
        box["job"] = ws.Job.create()
        box["attached"] = box["job"].attach(p) if box["job"] else False

    proc, pids = _spawn_tree(tmp_path, on_start=grab)
    try:
        assert box["job"] is not None, f"job 建不出来:{ws.Job.last_error}"
        assert box["attached"] is True, f"挂不上:{ws.Job.last_error}"
    finally:
        box["job"].close()
        proc.kill()
        proc.wait(timeout=10)
        _reap(*pids)


@needs_win
def test_closing_the_job_reclaims_the_whole_tree(tmp_path):
    """验收标准那一条:关句柄之后,父与它 spawn 的子都得没。

    断言的是 `tasklist` 的实测,不是 API 的返回值 —— `AssignProcessToJobObject` 返回
    成功而进程还在跑,是这一族最阴的"测试绿了功能没生效"。
    顺序按产品真实用法:Popen 之后**立刻**挂(Python 的 subprocess 不暴露
    CreateProcess 的 job 槽,所以只能这样)。
    """
    held = {}

    def grab(p):
        held["job"] = ws.Job.create()
        assert held["job"].attach(p) is True

    proc, pids = _spawn_tree(tmp_path, on_start=grab)
    assert all(_pid_alive(p) for p in pids), "前提:两个都活着"
    held["job"].close()
    still = _wait_gone(pids)
    with contextlib_suppress():
        proc.wait(timeout=5)
    _reap(*pids)
    assert not still, f"关掉 job 之后这些进程还活着:{still} —— 这一层没生效"


@needs_win
def test_the_startup_window_is_real_and_is_said_out_loud(tmp_path):
    """先起完树再挂 job:子进程漏在外面 —— 这就是回话里那句"启动那一瞬"的分量。

    这条是**反证**:它证明产品文案里那句限定不是客套。把 assign 挪到 Popen 之前
    (CreateProcess 的 job 参数)能消掉这个窗口,但 Python 的 subprocess 不给那个槽,
    所以这里选择"说出口",而不是在注释里假装不存在。
    """
    proc, pids = _spawn_tree(tmp_path)             # 不传 on_start:树已经起完
    job = ws.Job.create()
    assert job.attach(proc) is True
    job.close()
    still = _wait_gone(pids, seconds=5)
    _reap(*pids)
    with contextlib_suppress():
        proc.wait(timeout=5)
    assert still, "这条要复现的正是漏在外面那个子进程 —— 它不该被算作已回收"


@needs_win
def test_kill_tree_via_shell_closes_the_job(tmp_path):
    """`bash_kill` 与前台超时共用的那一只手:`_bg_kill_tree` 必须真的回收整棵树。

    以前它对孙子进程的口径是"学长无从确认";挂上 job 之后同一句话改成"已随这次终止
    一起回收",所以这条测试断言的是**那句新话对应的实事**,不是文案。
    """
    held = {}

    def grab(p):
        held["job"] = ws.Job.create()
        assert held["job"].attach(p) is True

    proc, pids = _spawn_tree(tmp_path, on_start=grab)
    bg = shell._BgJob("t1", proc, "sleep 120", job=held["job"])
    ok, note = shell._bg_kill_tree(bg)
    still = _wait_gone(pids)
    with contextlib_suppress():
        proc.wait(timeout=5)
    _reap(*pids)
    assert ok and not still, f"kill 之后还在跑:{still}(回话:{note})"
    assert "操作系统记着" in note, note
    assert bg.job is None, "关过一次就该把引用清掉,别说成还挂着"


@needs_win
def test_py_script_timeout_reclaims_spawned_child(tmp_path):
    """真实 py_script 超时必须关闭 job,不能留下仍在执行的孙进程。"""
    child_pid = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(120)\n", encoding="utf-8")
    from minicode.config import Config
    from minicode.tools.base import ToolContext
    pid = None
    try:
        out = shell.py_script(ToolContext(workspace=tmp_path, config=Config()),
                              "spawn.py", timeout=10)
        assert "超过 10 秒被中止" in out, out
        assert child_pid.exists(), "脚本没有跑到 spawn,测试前提不成立"
        pid = child_pid.read_text(encoding="utf-8")
        assert not _wait_gone([pid]), f"超时后子进程仍存活: {pid}"
    finally:
        if pid:
            _reap(pid)


@needs_win
def test_the_main_process_is_never_put_in_the_job(tmp_path):
    """关窗不能把 pywebview 自己杀掉:只关学长 spawn 出来的那些进程。

    取的是句柄背后的真实 pid(`GetProcessId`),不是"我以为这是子进程的句柄"。

    顺手记一笔本机实测到的形状:`.venv/Scripts/python.exe` 是个**转发壳**,它会再
    spawn 一个真实解释器 —— 所以 `proc.pid` 不等于脚本里的 `os.getpid()`,那棵树在
    venv 下其实是三层。这反而让上面那两条"整棵树回收"更有分量:壳在 job 里,它生的
    解释器与解释器生的孙进程都自动落在同一个 job 里。
    """
    import os
    kern = _prototyped_kern()
    proc, pids = _spawn_tree(tmp_path)
    try:
        job = ws.Job.create()
        assert job.attach(proc) is True
        got = kern.GetProcessId(int(proc._handle))
        assert got == proc.pid, f"句柄背后是 pid {got},我们起的进程是 {proc.pid}"
        assert proc.pid != os.getpid(), "挂进去的必须是别人,不是学长自己"
        assert int(pids[0]) != os.getpid(), "子进程里报的 pid 也不能是 ourselves"
    finally:
        _reap(*pids)
        proc.kill()
        proc.wait(timeout=10)


def _prototyped_kern():
    """带原型的 kernel32:GetProcessId 的句柄参数必须按 64 位传。

    ctypes 默认按 32 位 int 传参,截了之后 `GetProcessId` 会返回**另一个进程**的 pid
    (本机实测踩过:表现是断言莫名其妙对不上,而不是报错)。
    """
    import ctypes
    kern = ctypes.windll.kernel32
    kern.GetProcessId.argtypes = [ctypes.c_void_p]
    kern.GetProcessId.restype = ctypes.c_uint32
    return kern


def contextlib_suppress():
    import contextlib
    return contextlib.suppress(Exception)


# ---------------------------------------------------------------- 降级与不撒谎

def test_create_failure_degrades_to_none_not_an_exception(monkeypatch):
    """Win32 层什么都可能抛。这一层的规矩是"起不来就当没启用",不是带崩命令。"""
    class Boom:
        def CreateJobObjectW(self, *a):
            raise OSError("模拟:这台机器不许建 job")

    monkeypatch.setattr(ws, "_kern", lambda: Boom())
    assert ws.Job.create() is None
    assert "模拟" in ws.Job.last_error


def test_status_line_says_not_enabled_when_the_layer_is_missing():
    """未启用必须写"未启用"。假装有沙箱是这个项目列在禁忌里的那种坏法。"""
    line = shell._job_status_line(None)
    assert "未启用" in line and "已启用" not in line
    tail = shell._job_tail(None)
    assert "未启用" in tail


def test_enabled_note_never_claims_network_control():
    """S5a 不管网络。回话里出现"禁网/网络已挡"就是把这一层说成 AppContainer。"""
    line = shell._job_status_line(object())
    assert "已启用" in line
    for banned in ("已禁网", "网络已挡", "断网"):
        assert banned not in line
    assert "不管网络" in line            # 反过来:必须自己把边界说出来


def test_tail_names_the_startup_window():
    """Popen 之后才 assign 的残余风险必须写在话里,不是藏在注释里。"""
    assert "启动那一瞬" in shell._job_tail(object())


@needs_win
def test_attach_reports_the_exited_process_honestly():
    """已经退出的进程没有句柄可挂,要说成"拿不到句柄",不能假装挂上了。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=20)
    job = ws.Job.create()
    assert job is not None
    assert job.attach(proc) is False
    assert ws.Job.last_error


def test_attach_falls_back_to_the_private_handle_attribute():
    """钉住那个静默坏法:CPython 的 Windows `Popen` 只有 `_handle`,没有 `handle`。

    只读 `proc.handle` 的第一版在这台机器上永远返回 False,而 False 被当成"没启用"
    之后命令照跑、界面也不报错 —— 那是"装了个空的"最难查的一种。这条**不需要真进程**:
    它断言的是实现会去看那个私有属性(名字兜不住就是回归)。
    """
    src = Path(ws.__file__).read_text(encoding="utf-8")
    assert "_handle" in src, "attach 必须兜到 Popen._handle"

    class FakeProc:
        handle = None            # 有的版本压根没有这个属性
        _handle = 1234

    assert (getattr(FakeProc, "handle", None) or getattr(FakeProc, "_handle", None)) == 1234


def test_limits_flags_cover_the_three_we_claim():
    flags = ws.Limits().flags()
    assert flags & ws.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert flags & ws.JOB_OBJECT_LIMIT_PROCESS_MEMORY
    assert flags & ws.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    assert ws.Limits(memory_bytes=0, active_process=0, kill_on_close=False).flags() == 0


@pytest.mark.skipif(WIN, reason="这条测的是非 Windows 那一支:一律不启用")
def test_non_windows_returns_none(monkeypatch):
    monkeypatch.setattr(ws, "_IS_WIN", False)
    assert ws.Job.create() is None
    assert ws.Job(1, ws.Limits()).attach(object()) is False
