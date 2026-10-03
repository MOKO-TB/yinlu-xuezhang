"""桌面版起子进程的两条纪律:起不来**不许弹模态框**,而且**不许再试第二遍**。

用户看图 p7:打开任务监控就弹「git.exe - 应用程序无法正常启动(0xc0000142)」,
点掉之前整个窗口卡住,而每次开栏都来一遍。根因不在 git 装没装 —— 在**怎么起它**:
`tools/shell.py` 那条路(bash 工具)一直带着 `CREATE_NO_WINDOW` + `stdin=DEVNULL`,
跑了很久没弹过一次;而 v0.9.111 新加的「环境信息」那一屏走 `worktree._git`,
两个参数都没带 —— 桌面版是 GUI 子系统进程(没有控制台),给一个控制台子进程
留一个继承来的无效 stdin,就是请系统弹那个框。

所以这里钉三层,一层比一层更兜底:
① 起法与 shell.py 一致(无控制台 + stdin 不继承);
② 真起不来(抛 OSError,或退出码就是 0xc0000142)→ **熔断**,同一程不再起第二遍;
   超时**不算**起不来,那是仓库慢,熔断它等于一次慢问让后面永远读不出分支;
③ 进程起来前先看盘:非仓库工作区压根不起 git(`find_repo_root` 只 stat 不开进程)。
外加开机一次 `SetErrorMode`,让"任何"子进程起不来都不再能把模态框摆到用户脸上。
"""
import subprocess
import sys
from pathlib import Path

import pytest

from minicode import worktree as wt
from minicode import window_chrome as wc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _breaker_reset():
    """熔断是进程级的状态,不复位就会串到同目录里那些用真 git 的测试上。"""
    wt._reset_git_breaker()
    yield
    wt._reset_git_breaker()


class _Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _spy(monkeypatch, result):
    """替 subprocess.run 记账:拿到哪些关键字参数、被叫了几次。"""
    calls = []

    def fake_run(argv, **kw):
        calls.append(kw)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------- 起法本身

def test_git_is_spawned_windowless_with_no_inherited_stdin(monkeypatch):
    calls = _spy(monkeypatch, _Done(out="ok\n"))
    ok, out = wt._git(["status"])
    assert ok and out == "ok\n"
    assert calls[0]["stdin"] is subprocess.DEVNULL, "继承 GUI 进程的 stdin = 弹窗邀请函"
    want = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    assert calls[0]["creationflags"] == want, "别替控制台子进程新建控制台"


def test_the_flags_match_the_path_that_never_popped():
    """bash 工具那条路跑了很久没弹过框 —— 两条路起子进程的姿势必须一致,
    否则"哪条路会弹窗"就变成要靠运气发现的事。"""
    src = (ROOT / "minicode" / "tools" / "shell.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in src
    mine = (ROOT / "minicode" / "worktree.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in mine or "_NO_WINDOW" in mine
    assert "stdin=subprocess.DEVNULL" in mine


# ---------------------------------------------------------------- 熔断

def test_a_git_that_cannot_start_is_not_asked_twice(monkeypatch):
    calls = _spy(monkeypatch, OSError(22, "Invalid argument"))
    ok1, why1 = wt._git(["status"])
    ok2, why2 = wt._git(["status"])
    assert not ok1 and not ok2
    assert len(calls) == 1, "第二次根本不该再起进程 —— 那又是一次弹窗机会"
    assert "已停止再试" in why2
    assert wt.git_unavailable(), "降级原因要看得见,界面才知道该说什么"


def test_the_ntstatus_from_a_broken_child_trips_it_too(monkeypatch):
    """0xc0000142 有时不是异常,是**退出码**(有符号 -1073741502)。"""
    _spy(monkeypatch, _Done(rc=-1073741502))
    ok, why = wt._git(["worktree", "list", "--porcelain"])
    assert not ok and "0XC0000142" in why.upper()
    assert wt.git_unavailable()


def test_a_slow_git_does_not_trip_the_breaker(monkeypatch):
    _spy(monkeypatch, subprocess.TimeoutExpired(cmd="git", timeout=1))
    ok, why = wt._git(["status"])
    assert not ok and "TimeoutExpired" in why
    assert wt.git_unavailable() == "", "仓库慢 ≠ git 坏了"


def test_an_ordinary_git_failure_does_not_trip_it_either(monkeypatch):
    """`git diff HEAD` 在零提交仓库里就是会非零退出 —— 那是事实,不是坏了。"""
    _spy(monkeypatch, _Done(rc=128, err="fatal: ambiguous argument 'HEAD'"))
    ok, why = wt._git(["diff", "HEAD", "--numstat"])
    assert not ok and "ambiguous" in why
    assert wt.git_unavailable() == ""


def test_reset_lets_a_later_attempt_try_again(monkeypatch):
    _spy(monkeypatch, OSError(22, "Invalid argument"))
    wt._git(["status"])
    assert wt.git_unavailable()
    wt._reset_git_breaker()
    assert wt.git_unavailable() == ""


# ---------------------------------------------------------------- 先看盘再起进程

def test_find_repo_root_reads_the_disk_instead_of_starting_a_process(tmp_path):
    (tmp_path / "notes").mkdir()
    assert wt.find_repo_root(tmp_path / "notes") is None
    (tmp_path / ".git").mkdir()
    assert wt.find_repo_root(tmp_path / "notes") == tmp_path.resolve()


def test_a_worktree_pointer_file_counts_as_a_repo(tmp_path):
    """`git worktree add` 出来的副本里 `.git` 是个**文件**(gitdir: …)不是目录 ——
    只判 is_dir() 会把所有副本认成非仓库,那正是 worktree 功能自己要用的地方。"""
    sub = tmp_path / "wt-copy"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: D:/elsewhere/.git/worktrees/x", encoding="utf-8")
    assert wt.find_repo_root(sub) == sub.resolve()


def test_changes_on_a_non_repo_never_starts_git(monkeypatch, tmp_path):
    calls = _spy(monkeypatch, _Done())
    d = tmp_path / "just-notes"
    d.mkdir()
    got = wt.changes(d)
    assert not got["ok"] and "没找到 .git" in got["msg"]
    assert calls == [], "这条路上一次 git 都不该起"
    assert wt.worktree_facts(d)["ok"] is False
    assert calls == []


# ---------------------------------------------------------------- 开机那道静默

def test_error_mode_mask_is_the_two_dialog_killing_bits():
    assert wc.SEM_FAILCRITICALERRORS == 0x0001     # 「应用程序无法正常启动」那类
    assert wc.SEM_NOGPFAULTERRORBOX == 0x0002      # 「程序已停止工作」那类


def test_silence_reports_what_the_machine_accepted(monkeypatch):
    """与 apply() 同一口径:成没成、系统收没收,要看得见,不能静默"成功"。"""
    seen = {}

    class _K:
        def SetErrorMode(self, mode):
            seen["mode"] = mode
            return 0x0

    class _W:
        kernel32 = _K()

    monkeypatch.setattr(wc.ctypes, "windll", _W(), raising=False)
    got = wc.silence_child_error_dialogs()
    assert got["ok"] and seen["mode"] == 0x3 and got["msg"] == ""


def test_silence_on_a_non_windows_machine_says_so(monkeypatch):
    monkeypatch.setattr(wc.ctypes, "windll", None, raising=False)
    got = wc.silence_child_error_dialogs()
    assert not got["ok"] and "非 Windows" in got["msg"]


def test_silence_never_raises_when_the_api_refuses(monkeypatch):
    class _K:
        def SetErrorMode(self, mode):
            raise OSError(5, "Access is denied")

    class _W:
        kernel32 = _K()

    monkeypatch.setattr(wc.ctypes, "windll", _W(), raising=False)
    got = wc.silence_child_error_dialogs()
    assert not got["ok"] and "Access is denied" in got["msg"]


def test_gui_sets_it_before_any_child_can_start():
    """设晚了等于没设:MCP 服务器在 `create_app` 那一步就会起子进程。"""
    src = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "silence_child_error_dialogs" in src
    assert src.index("silence_child_error_dialogs") < src.index("create_app(config, workspace)")


def test_the_degradation_reason_reaches_the_screen():
    """熔断之后界面要说的是"这台机器的 git 问不动",不是"没有改动"——
    后者是一句假话,前者用户才知道该去装 git 或重启。"""
    env = (ROOT / "minicode" / "envinfo.py").read_text(encoding="utf-8")
    assert "git_unavailable" in env and "git_note" in env
    fe = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "e.git_note" in fe and "已停止再试" in fe
