"""定时任务底座 A0/A1 的测试:到期判定 + 那本"上次何时跑"的账本。

这一层刻意只有纯函数与一个 JSON 文件(设计文档 `docs/roadmap-v0.9.42-定时任务底座.md`),
所以下面每一条都不用起线程、不用等时间 —— 喂两个 `datetime` 就能判。
最看重的几条都是**"宁可少跑一次也不要多刷屏"**那一侧:时钟回拨、账本坏了、写盘失败。
"""
from datetime import datetime

import pytest

from minicode import jobs
from minicode.jobs import JobSpec, due


def dt(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


MONDAY = dt("2026-09-28 09:00")        # 2026-09-28 是周一
TUESDAY = dt("2026-09-29 09:00")
SUNDAY = dt("2026-10-04 23:30")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "data" / jobs.STATE_FILE
    monkeypatch.setattr(jobs, "state_path", lambda: path)
    return path


# ---------------------------------------------------------------- 任务表本身

def test_unknown_cadence_is_refused_at_construction():
    with pytest.raises(ValueError):
        JobSpec("x", cadence="monthly")


def test_weekly_without_a_weekday_would_never_fire():
    """不给 weekday 的 weekly 任务是"永远等不到那一天"的静默失效,当场拒。"""
    with pytest.raises(ValueError):
        JobSpec("x", cadence="weekly")


@pytest.mark.parametrize("kwargs", [
    {"cadence": "every_days", "every": 0},
    {"cadence": "interval", "every": -1},
    {"hour": 24},
    {"minute": 60},
])
def test_nonsensical_schedules_are_refused(kwargs):
    with pytest.raises(ValueError):
        JobSpec("x", **kwargs)


# ---------------------------------------------------------------- 五种周期

def test_daily_fires_once_per_day_only():
    j = JobSpec("brief", cadence="daily", hour=8)
    assert due(j, dt("2026-09-26 08:00"), None) is True            # 从没跑过
    assert due(j, dt("2026-09-26 07:59"), None) is False           # 还没到点
    assert due(j, dt("2026-09-26 09:30"), dt("2026-09-26 08:05")) is False
    assert due(j, dt("2026-09-27 08:30"), dt("2026-09-26 08:05")) is True


def test_daily_without_an_hour_fires_on_the_next_day():
    j = JobSpec("any", cadence="daily")
    assert due(j, TUESDAY, MONDAY) is True


def test_weekly_only_fires_on_its_day_and_only_once_that_week():
    j = JobSpec("review", cadence="weekly", weekday=0, hour=20)     # 周一 20:00 之后
    assert due(j, dt("2026-09-28 20:30"), dt("2026-09-21 21:00")) is True
    assert due(j, dt("2026-09-28 19:00"), dt("2026-09-21 21:00")) is False   # 未到点
    assert due(j, SUNDAY, dt("2026-09-28 20:30")) is False                   # 不是周一
    # 周日深夜跑过一轮,下周一到点应算新的一周
    assert due(j, dt("2026-10-05 20:30"), dt("2026-10-04 23:00")) is True


def test_every_days_counts_calendar_days_not_clock_hours():
    j = JobSpec("digest", cadence="every_days", every=7)
    assert due(j, dt("2026-10-04 09:00"), dt("2026-09-28 09:00")) is False   # 整 6 天
    assert due(j, dt("2026-10-05 09:00"), dt("2026-09-28 09:00")) is True    # 满 7 天


def test_interval_uses_minutes():
    j = JobSpec("probe", cadence="interval", every=30)
    assert due(j, dt("2026-09-26 10:29"), dt("2026-09-26 10:00")) is False
    assert due(j, dt("2026-09-26 10:30"), dt("2026-09-26 10:00")) is True


def test_on_demand_never_becomes_due_by_itself():
    """只由人或界面点名触发的任务,不能被心跳自己跑掉。"""
    assert due(JobSpec("manual", cadence="on_demand"), MONDAY, None) is False


def test_a_clock_that_went_backwards_does_not_make_everything_fire():
    """账本里的时间在"现在"之后(改过表/同步过时钟):宁可少跑一次,不要整点刷屏。"""
    for j in (JobSpec("d", cadence="daily"),
              JobSpec("w", cadence="weekly", weekday=0),
              JobSpec("e", cadence="every_days", every=2)):
        assert due(j, MONDAY, TUESDAY) is False


# ---------------------------------------------------------------- 账本

def test_mark_run_then_last_run_round_trips(ledger):
    assert jobs.mark_run("review", when=MONDAY, result="说了两句") is True
    assert jobs.last_run("review") == MONDAY
    row = ledger.read_text(encoding="utf-8")
    assert "2026-09-28 09:00:00" in row and "review" in row


def test_runs_counter_accumulates_and_result_is_stamped(ledger):
    jobs.mark_run("review", when=MONDAY)
    jobs.mark_run("review", when=TUESDAY, result="ok")
    assert jobs.runs_of("review") == 2
    assert jobs.last_run("review") == TUESDAY


def test_a_missing_ledger_reads_as_no_record(ledger):
    assert jobs.load_state() == {}
    assert jobs.last_run("review") is None
    assert jobs.runs_of("review") == 0


def test_a_corrupt_ledger_is_treated_as_empty_not_as_a_crash(ledger):
    """坏了当"没记录"是安全方向:后果是到点的任务跑一次,不是看门狗崩在半路。"""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{半截的 json\n", encoding="utf-8")
    assert jobs.load_state() == {}
    assert jobs.due_jobs([JobSpec("d", cadence="daily")], now=MONDAY) != []


def test_a_ledger_of_the_wrong_shape_does_not_crash_it_either(ledger):
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('["不是字典"]', encoding="utf-8")
    assert jobs.load_state() == {}
    ledger.write_text('{"x": "不是记录"}', encoding="utf-8")
    assert jobs.last_run("x", {"x": "不是记录"}) is None


def test_write_failure_reports_failure(tmp_path, monkeypatch):
    """写不进去必须如实回 False —— 一边没记上一边说"已记录",下一轮就重复刷屏。

    这里的"写不进去"是父目录其实是个文件,`mkdir` 直接 `NotADirectoryError`(OSError 的一种)。
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("我是个文件,不是目录", encoding="utf-8")
    monkeypatch.setattr(jobs, "state_path",
                        lambda: blocker / "sub" / jobs.STATE_FILE)
    assert jobs.mark_run("review", when=MONDAY) is False


def test_due_jobs_uses_the_ledger(ledger):
    specs = [JobSpec("d", cadence="daily"), JobSpec("m", cadence="on_demand")]
    assert [s.id for s in jobs.due_jobs(specs, now=MONDAY)] == ["d"]
    jobs.mark_run("d", when=MONDAY)
    assert jobs.due_jobs(specs, now=MONDAY) == []


def test_log_outcome_goes_to_the_watchdog_log_with_no_content(ledger, monkeypatch):
    """"跑没跑、为什么"要留现场,但正文一律不进日志(与提醒看门狗同一口径)。"""
    lines = []
    from minicode import proactive
    monkeypatch.setattr(proactive, "wd_log", lines.append)
    jobs.log_outcome(JobSpec("review", cadence="weekly", weekday=0), True, "两句")
    jobs.log_outcome(JobSpec("digest", cadence="daily"), False, "未到点")
    assert lines == ["job review: 跑了[两句]", "job digest: 未跑[未到点]"]


# ---------------------------------------------------------------- 可观测(v0.9.79)

def test_snapshot_never_run_and_rhythm(ledger):
    """没人动过账本:如实说「从未跑过」,节奏翻译成人话。"""
    specs = [JobSpec("mood_weekly_review", cadence="weekly", weekday=6, hour=20),
             JobSpec("digest", cadence="daily", hour=8)]
    text = jobs.snapshot(specs, state={}, now=TUESDAY)
    assert "定时任务 2 个" in text
    assert "mood_weekly_review(每周周日 20 点后):从未跑过" in text
    assert "digest(每天 8 点后):从未跑过" in text
    assert "现在已到期" in text          # 每日 8 点任务在 09:00 已到期


def test_snapshot_reports_last_result_and_failure(ledger):
    """上次的结论随行摆出;handler 异常要能被问出来("跑失败了"≠"没跑")。"""
    specs = [JobSpec("mood_weekly_review", cadence="weekly", weekday=6, hour=20)]
    jobs.mark_run("mood_weekly_review", when=SUNDAY, result="已推")
    ok_text = jobs.snapshot(specs, now=TUESDAY)
    assert "上次 10-04 23:30 跑过" in ok_text and "结论:已推" in ok_text
    assert "本周周日那一班已跑过" in ok_text
    jobs.mark_run("mood_weekly_review", when=SUNDAY, result="异常 RuntimeError")
    bad_text = jobs.snapshot(specs, now=TUESDAY)
    assert "上次跑失败:异常 RuntimeError" in bad_text


def test_snapshot_weekly_not_yet_and_due(ledger):
    """周日 20 点前:还没到那一班;到了且没跑:说"下一次心跳就会跑"。"""
    specs = [JobSpec("mood_weekly_review", cadence="weekly", weekday=6, hour=20)]
    before = jobs.snapshot(specs, now=TUESDAY)
    assert "本周还没到周日那一班" in before
    at_deadline = jobs.snapshot(specs, now=dt("2026-10-04 20:05"))
    assert "现在已到期,下一次心跳(≤5 分钟)就会跑" in at_deadline


def test_every_registered_job_has_a_human_label():
    """看板那一行给人看的是「周复盘」,不是 mood_weekly_review(B1 的 §四.1:每行都得读得懂)。"""
    from minicode import proactive
    assert proactive.JOB_SPECS, "任务表空了?"
    for spec in proactive.JOB_SPECS:
        assert spec.label.strip(), f"{spec.id} 没有给人看的名"
        assert spec.label not in (spec.id,), "label 抄个 id 等于没起名字"
    ids = {s.id for s in proactive.JOB_SPECS}
    assert ids == set(proactive.JOB_HANDLERS), "登记了却没接线 / 接线了却没登记"
