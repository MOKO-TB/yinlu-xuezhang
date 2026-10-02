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


@pytest.fixture(autouse=True)
def _no_real_desktop_notify(monkeypatch):
    """本文件其余用例都在**真起后台任务**,不该在测试机上把 PowerShell 拉起来弹窗。

    返回的那份 list 就是"回执都弹了什么",要看通知的用例直接读它。
    """
    from minicode import tray
    calls: list[dict] = []

    def _stub(title, body, kind="", force=False, **kw):
        calls.append({"title": title, "body": body, "kind": kind})
        return {"ok": True, "channel": "stub"}

    monkeypatch.setattr(tray, "notify", _stub)
    return calls


def _wait_notify(calls: list, seconds: float = 6.0) -> bool:
    """弹通知的是读输出那条线程,不是主线程 —— 有界等一会儿,别睡死。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if calls:
            return True
        time.sleep(0.05)
    return False


# ---------------- 跑完的回执:桌面通知(v0.9.104)
#
# 后台命令存在的理由就是"人可以走开",而它原先结束时没有任何人知道 ——
# 下一次提到它,要等模型自己再走一步,或者人自己去点 📋 看板。


def test_finished_background_job_sends_one_receipt(ctx, _no_real_desktop_notify):
    out = shell.bash(ctx, "echo receipt-one", background=True)
    job_id = _job_id_of(out)
    assert _wait_done(job_id), "后台命令没在 8 秒内跑完"
    assert _wait_notify(_no_real_desktop_notify), "跑完了却没弹回执"
    shot = _no_real_desktop_notify[0]
    assert shot["kind"] == shell.BG_NOTIFY_KIND
    assert job_id in shot["title"] and "跑完了" in shot["title"]
    assert "echo receipt-one" in shot["body"]          # 认得出是哪一条
    # 人再去查一遍,不许弹第二条
    shell.bash_status(ctx)
    shell.bash_status(ctx, id=job_id)
    shell.bg_brief_for_model(ctx)
    assert len(_no_real_desktop_notify) == 1


def test_failing_background_job_names_the_exit_code(ctx, _no_real_desktop_notify):
    out = shell.bash(ctx, "echo before-fail; exit 7", background=True)
    job_id = _job_id_of(out)
    assert _wait_done(job_id)
    assert _wait_notify(_no_real_desktop_notify)
    title = _no_real_desktop_notify[0]["title"]
    assert "退出码 7" in title, title
    assert "跑完了" not in title                       # 非零退出别写成好消息


def test_running_job_is_not_announced_as_finished(ctx, _no_real_desktop_notify):
    out = shell.bash(ctx, "sleep 6", background=True)
    job_id = _job_id_of(out)
    try:
        assert shell._bg_jobs[job_id].running
        assert shell.bash_status(ctx)                  # 查一遍状态不该"提前报丧"
        assert _no_real_desktop_notify == []
    finally:
        shell.bash_kill(ctx, id=job_id)


def test_a_broken_notify_channel_does_not_eat_the_status(ctx, monkeypatch):
    """弹不出去是通道的事,不能把"这条任务什么状态"一起吞掉。"""
    from minicode import tray

    def _boom(*a, **k):
        raise RuntimeError("PowerShell 不在")

    monkeypatch.setattr(tray, "notify", _boom)
    out = shell.bash(ctx, "echo still-reported", background=True)
    job_id = _job_id_of(out)
    assert _wait_done(job_id)
    time.sleep(0.3)                                    # 给那条线程一点时间炸完
    assert "已结束,退出码 0" in shell.bash_status(ctx, id=job_id)


def test_receipt_kind_is_switchable_by_the_user():
    """关不掉的一类打扰,最后结局是整个通道被关掉 —— 所以这张表必须有它。

    这一条不靠 desktop_notify 那段描述(那是另一根闸),它钉的是跨模块的那句约定:
    `shell.BG_NOTIFY_KIND` 必须是 `tray.NOTIFY_KINDS` 里的一类,且有给人看的名字。
    """
    from minicode import tray
    assert shell.BG_NOTIFY_KIND in tray.NOTIFY_KINDS
    assert tray.KIND_LABELS.get(shell.BG_NOTIFY_KIND) == "后台命令跑完"


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


# -------------------------------------------------- 验收执行账(v0.9.101 ②V1 的唯一凭据)

def test_bash_notes_completed_run(ctx):
    out = shell.bash(ctx, "echo verify-me")
    assert "退出码: 0" in out
    runs = getattr(ctx, "verify_runs", [])
    assert runs and runs[-1]["command"] == "echo verify-me"
    assert runs[-1]["code"] == 0 and runs[-1]["at"]


def test_bash_records_nonzero_too(ctx):
    """跑过但没过也要进账:拒绝标 done 的那句话要报得出退出码与输出尾部。"""
    shell.bash(ctx, "exit 3")
    run = ctx.verify_runs[-1]
    assert run["code"] == 3 and "exit 3" in run["command"]


def test_background_run_is_not_a_verified_record(ctx):
    """background=true 立刻返回,没有"退出码是几"这个结论 —— 记进去就等于给"跑过了"开门。"""
    out = shell.bash(ctx, "echo bg-verify", background=True)
    assert "后台" in out or "job" in out.lower()
    assert not [r for r in getattr(ctx, "verify_runs", [])
                if "bg-verify" in r["command"]]


# --------------------------- 后台任务与验收账(v0.9.102 之后补的那条锁死)

def _wait_bg(job, seconds: float = 20.0) -> bool:
    import time as _t
    end = _t.monotonic() + seconds
    while job.running and _t.monotonic() < end:
        _t.sleep(0.15)
    return not job.running


def _runs_of(ctx, command):
    want = " ".join(str(command).split())
    return [r for r in getattr(ctx, "verify_runs", []) if r["command"] == want]


def test_finished_background_run_enters_the_verify_ledger(ctx):
    """`MAX_TIMEOUT` 是 300 秒,跑全量测试这类验收只能走后台;不补这一笔就等于
    「凡是跑得慢的验收都不许标 done」—— 那道闸拦的是没验过,不该把验过的也拦掉。"""
    long_cmd = "echo " + "x" * 140 + " 收尾"     # 比 _BgJob.command 的 120 字盖还长
    out = shell.bash(ctx, long_cmd, background=True)
    assert "后台" in out
    job = shell._bg_jobs[shell._bg_list_snapshot()[-1]]
    assert _wait_bg(job), "后台命令没在时限内跑完,这条测试的环境有问题"
    assert not _runs_of(ctx, long_cmd), "还在跑的时候不能进账"
    shell.bg_brief_for_model(ctx)               # 模型下一步请求前的那一次播报
    runs = _runs_of(ctx, long_cmd)
    assert len(runs) == 1, "跑完的那一刻该有一条账"
    assert runs[0]["code"] == 0
    # 用的是**原条命令**:拿截成 120 字那份去比,判据永远对不上
    assert runs[0]["command"] == " ".join(long_cmd.split())


def test_background_run_is_recorded_only_once(ctx):
    shell.bash(ctx, "echo once-only", background=True)
    jid = shell._bg_list_snapshot()[-1]
    job = shell._bg_jobs[jid]
    assert _wait_bg(job)
    shell.bg_brief_for_model(ctx)
    shell.bash_status(ctx, id=jid)              # 主动查也算一次"它知道了"
    shell.bg_brief_for_model(ctx)
    assert len(_runs_of(ctx, "echo once-only")) == 1


def test_no_ctx_means_no_ledger_write(ctx):
    """老调用形状(不传 ctx)照旧能播报,只是不碰账 —— 五处既有测试就是那样调的。"""
    shell.bash(ctx, "echo no-ctx", background=True)
    job = shell._bg_jobs[shell._bg_list_snapshot()[-1]]
    assert _wait_bg(job)
    assert shell.bg_brief_for_model() != ""
    assert not _runs_of(ctx, "echo no-ctx")


def test_background_verify_unblocks_the_plan_gate(ctx, tmp_path):
    """把三条线接在一起验:V1 的硬闸 + 后台补账 + V2 写进 plan.md 的那一行。

    这是那条锁死的端到端形状:判据是"跑一遍要超过 300 秒的东西"时,唯一的跑法是
    background=true,而它跑完必须能让那一步标得上 done,否则硬闸就把慢验收锁死了。
    """
    from minicode import planning
    planning.plan(ctx, "write", goal="慢验收", steps=[
        {"title": "第一件事"},
        {"title": "跑一遍全量", "verify": "echo 慢一点的验收"}])
    refused = planning.plan(ctx, "update", step=2, status="done")
    assert "没有它跑完的记录" in refused and "background=true" in refused
    shell.bash(ctx, "echo 慢一点的验收", background=True)
    job = shell._bg_jobs[shell._bg_list_snapshot()[-1]]
    assert _wait_bg(job)
    shell.bg_brief_for_model(ctx)
    ok = planning.plan(ctx, "update", step=2, status="done")
    assert "已标记第 2 步为 done" in ok, ok
    note = planning.plan_state(tmp_path)["steps"][1]["note"]
    assert note.startswith("verify ✓") or "verify ✓" in note, note
