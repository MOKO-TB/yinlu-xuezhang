import re
import time

import pytest
from minicode.config import Config, Provider
from minicode.tools import shell
from minicode.tools.base import ToolContext


def _cfg(shell="auto"):
    return Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                      api_key="k", model="m")],
                  active_provider_id="t", shell=shell)


def test_echo(ctx):
    out = shell.bash(ctx, "echo hello-minicode")
    assert "hello-minicode" in out
    assert "退出码: 0" in out


def test_exit_code_and_stderr(ctx):
    out = shell.bash(ctx, "false")
    assert "退出码: 1" in out


def test_cmd_fallback(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=_cfg(shell="cmd"))
    assert shell.resolve_shell_argv("cmd") is None
    out = shell.bash(ctx, "echo hello-from-cmd")
    assert "hello-from-cmd" in out


@pytest.mark.slow      # 真等 5 s 超时:这条用例的耗时就是被测行为
def test_timeout(tmp_path):
    ctx = ToolContext(workspace=tmp_path, config=_cfg())
    if shell.resolve_shell_argv("auto") is None:
        # 没有 bash 时用 cmd 的 ping 模拟等待
        cmd = "ping -n 6 127.0.0.1 >nul"
    else:
        cmd = "echo start && sleep 5"
    out = shell.bash(ctx, cmd, timeout=1)
    assert "超时" in out


# --------------------------------------------------- Git Bash 探测(非默认安装)


def _fake_git_install(tmp_path, *, layout="cmd"):
    """造一个假的 Git 安装目录,返回 (git.exe, bash.exe)。"""
    root = tmp_path / "Git"
    git_exe = root / layout / "git.exe"
    git_exe.parent.mkdir(parents=True)
    git_exe.write_text("", encoding="utf-8")
    bash_exe = root / "bin" / "bash.exe"
    bash_exe.parent.mkdir(parents=True, exist_ok=True)
    bash_exe.write_text("", encoding="utf-8")
    return git_exe, bash_exe


def test_bash_beside_nonstandard_git(tmp_path, monkeypatch):
    """Git 装在非默认位置(如 D:\\git\\Git)时,靠 git.exe 反推出 bash。

    固定候选表只盖得住 C:\\Program Files\\Git;漏掉就会静默退回 cmd,
    于是 sleep/grep 之类命令直接报"不是内部或外部命令"。
    """
    git_exe, bash_exe = _fake_git_install(tmp_path)
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: str(git_exe) if n == "git" else None)
    assert shell._pick_bash() == str(bash_exe)


def test_bash_beside_git_supports_usr_bin_layout(tmp_path, monkeypatch):
    """有些安装方式只提供 usr/bin/bash。"""
    git_exe, _ = _fake_git_install(tmp_path)
    (tmp_path / "Git" / "bin" / "bash.exe").unlink()
    usr_bash = tmp_path / "Git" / "usr" / "bin" / "bash.exe"
    usr_bash.parent.mkdir(parents=True)
    usr_bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: str(git_exe) if n == "git" else None)
    assert shell._pick_bash() == str(usr_bash)


def test_bash_detection_rejects_wsl_and_missing_git(tmp_path, monkeypatch):
    """System32 下的是 WSL,不能当 Git Bash;连 git 都没有时返回 None。"""
    monkeypatch.setattr(shell, "_GIT_BASH_CANDIDATES", ())
    wsl = r"C:\Windows\System32\bash.exe"
    monkeypatch.setattr(shell.shutil, "which",
                        lambda n: wsl if n == "bash" else None)
    assert shell._pick_bash() is None

    monkeypatch.setattr(shell.shutil, "which", lambda n: None)
    assert shell._pick_bash() is None


# ------------------------------------------------------------- 后台命令(v0.9.84)


def _job_id_of(out: str) -> str:
    m = re.search(r"已放进后台:(j\d+)", out)
    assert m, f"没拿到 job 编号,实际回话:{out[:200]}"
    return m.group(1)


def _wait_done(job_id: str, seconds: float = 8.0) -> bool:
    """等这次后台真跑完(不拿秒数当判据,轮询到 poll() 给结果为止)。"""
    job = shell._bg_jobs[job_id]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not job.running:
            return True
        time.sleep(0.05)
    return False


def test_background_returns_at_once_without_running_to_completion(ctx):
    """background=true 的价值就是"不干等":睡 5 秒的命令 2 秒内就该回话。"""
    t0 = time.monotonic()
    out = shell.bash(ctx, "sleep 5 && echo late", background=True)
    spent = time.monotonic() - t0
    job_id = _job_id_of(out)
    try:
        assert spent < 2, f"后台派发改成了同步等:{spent:.1f}s"
        assert "不受 timeout 限制" in out           # 生命周期边界写在回话里
        assert "内存缓冲" in out or "重启就没了" in out
        assert shell._bg_jobs[job_id].running
    finally:
        shell.bash_kill(ctx, id=job_id)


def test_bash_status_lists_state_then_reads_the_tail(ctx):
    out = shell.bash(ctx, "echo hi-bg-tail", background=True)
    job_id = _job_id_of(out)
    assert _wait_done(job_id), "后台命令没在 8 秒内跑完"
    listing = shell.bash_status(ctx)
    assert job_id in listing and "已结束,退出码 0" in listing
    detail = shell.bash_status(ctx, id=job_id)
    assert "hi-bg-tail" in detail
    assert "echo hi-bg-tail" in detail              # 命令摘要跟着走,认得出是哪一条


def test_bash_status_with_no_jobs_says_so_and_how_to_start_one(monkeypatch, ctx):
    monkeypatch.setattr(shell, "_bg_jobs", {})      # 没起过任务时不许摆空表
    out = shell.bash_status(ctx)
    assert "还没有后台任务" in out and "background=true" in out


def test_bash_status_unknown_id_names_the_ones_that_exist(monkeypatch, ctx):
    monkeypatch.setattr(shell, "_bg_jobs", {})
    out = shell.bash(ctx, "echo x", background=True)
    job_id = _job_id_of(out)
    try:
        bad = shell.bash_status(ctx, id="j999")
        assert "错误" in bad and "没有后台任务 j999" in bad
        assert job_id in bad                        # 把存在的编号报回去,模型一回合自纠
        assert "echo x" in shell.bash_status(ctx, id=job_id.lstrip("j"))  # 传 1 也认
    finally:
        shell.bash_kill(ctx, id=job_id)


def test_bash_kill_terminates_a_running_job(ctx):
    out = shell.bash(ctx, "sleep 30", background=True)
    job_id = _job_id_of(out)
    res = shell.bash_kill(ctx, id=job_id)
    assert "已终止" in res, res
    assert not shell._bg_jobs[job_id].running
    assert "被终止" in shell.bash_status(ctx, id=job_id).splitlines()[0]


def test_bash_kill_on_a_finished_job_is_not_an_error(ctx):
    out = shell.bash(ctx, "echo done-early", background=True)
    job_id = _job_id_of(out)
    assert _wait_done(job_id)
    assert "不用杀" in shell.bash_kill(ctx, id=job_id)


def test_too_many_running_jobs_is_refused_not_silently_evicted(monkeypatch, ctx):
    """超上限必须拒绝:静默挤掉旧的等于偷偷杀用户的任务。"""
    monkeypatch.setattr(shell, "MAX_BG_JOBS", 1)
    first = _job_id_of(shell.bash(ctx, "sleep 20", background=True))
    try:
        second = shell.bash(ctx, "sleep 20", background=True)
        assert "错误" in second and "上限 1" in second
        assert shell._bg_jobs[first].running        # 旧的没被挤掉
    finally:
        shell.bash_kill(ctx, id=first)


def test_buffer_keeps_the_tail_and_reports_what_it_dropped():
    """缓冲只留尾部,但**丢了多少字要说出来** —— 不说就成了静默截断。"""
    import types
    job = shell._BgJob("jT", types.SimpleNamespace(poll=lambda: 0, stdout=None),
                       "pytest -q")
    original = shell.BG_BUFFER_CHARS
    shell.BG_BUFFER_CHARS = 60
    try:
        for i in range(20):
            job._append(f"line-{i:02d}-abcdefghij\n")
        assert job.dropped > 0
        tail = job.tail(200)
        assert "缓冲只留尾部" in tail and f"前面 {job.dropped} 字已丢掉" in tail
        assert "line-19" in tail and "line-00" not in tail
        assert f"{job.dropped} 字" in job.line()    # 状态行也带着这笔账
    finally:
        shell.BG_BUFFER_CHARS = original


def test_no_output_yet_is_stated_not_left_blank():
    """一行输出都没有时回「还没有输出」,不能让模型看到空字符串去猜。"""
    import types
    job = shell._BgJob("jE", types.SimpleNamespace(poll=lambda: None, stdout=None),
                       "sleep 99")
    assert "还没有输出" in job.tail(100)


def test_registry_flags_read_only_split(ctx):
    """看进度是只读、终止是动手:这个分界错了要么弹无用确认框,要么放任乱杀。"""
    from minicode.tools.registry import build_default_registry
    by_name = {t.name: t for t in build_default_registry()}
    assert by_name["bash_status"].read_only is True
    assert by_name["bash_kill"].read_only is False
    assert {"background", "command", "timeout", "reason"} <= set(
        by_name["bash"].parameters["properties"])


# -------------------------------------------------- 状态注入(bg_brief_for_model)

def test_bg_brief_is_empty_when_there_are_no_jobs(monkeypatch):
    """没起过后台任务时一个字都不注入 —— 这是常态,不能每轮都带一段无关话。"""
    monkeypatch.setattr(shell, "_bg_jobs", {})
    assert shell.bg_brief_for_model() == ""


def test_bg_brief_keeps_announcing_a_running_job(monkeypatch, ctx):
    monkeypatch.setattr(shell, "_bg_jobs", {})
    jid = _job_id_of(shell.bash(ctx, "sleep 5", background=True))
    try:
        for _ in range(2):            # 每一步都要播:状态在变,模型也可能忘记查
            brief = shell.bg_brief_for_model()
            assert jid in brief and "运行中" in brief
        assert "别替用户假设" in brief
        assert "bash_status" in brief and "bash_kill" in brief
    finally:
        shell.bash_kill(ctx, id=jid)


def test_bg_brief_announces_a_finished_job_exactly_once(monkeypatch, ctx):
    """死讯播一次就够:每条旧任务每步都向模型汇报一遍是纯噪音。"""
    monkeypatch.setattr(shell, "_bg_jobs", {})
    jid = _job_id_of(shell.bash(ctx, "echo one-two", background=True))
    assert _wait_done(jid)
    first = shell.bg_brief_for_model()
    assert jid in first and "已结束,退出码 0" in first
    assert shell.bg_brief_for_model() == ""


def test_announced_finished_jobs_get_pruned(monkeypatch, ctx):
    """在跑的受上限 12 管,历史的没人管就会只涨不落 —— 播过的要被清掉。"""
    monkeypatch.setattr(shell, "_bg_jobs", {})
    monkeypatch.setattr(shell, "BG_KEEP_FINISHED", 1)
    ids = [_job_id_of(shell.bash(ctx, f"echo p{i}", background=True))
           for i in range(3)]
    for jid in ids:
        assert _wait_done(jid)
        shell.bg_brief_for_model()                 # 播一次,标成 announced
    keeper = _job_id_of(shell.bash(ctx, "echo keeper", background=True))
    left = shell._bg_list_snapshot()
    assert keeper in left, left
    assert ids[0] not in left and ids[1] not in left   # 最旧的两个播过就被清掉
    assert len(left) <= 2, left                        # 留 1 个播过的 + 在跑的这个
    shell.bash_kill(ctx, id=keeper)
