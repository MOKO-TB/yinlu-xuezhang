"""用户自己登记的定时任务(v0.9.114,图 p5/p6)。

自动化那一屏以前只能"看 + 关":要加一条得改 Python。这一轮把"加"开放给同学,
所以这里钉的是**加进来的东西会不会被当真**的四件事:
① 校验不过一个字都不写(空任务、没名字、超长、超条数、weekly 没给星期);
② 到点跑的确实是同学写的那句话,而且走 `send_task` 那一条 —— 权限档位照常生效,
   **自动化不是绕过确认的第二条路**;
③ 会话正忙时不排队、不插队:记一句「跳过」并照常记账(免得每 5 分钟重试刷屏);
④ 开关与记账沿用同一份机制(`jobs-pref.json` 的 disabled + `jobs-state.json`),
   不新起第二套状态。
"""
import json
from datetime import datetime, timedelta

import pytest

from minicode import jobs, paths, proactive


@pytest.fixture(autouse=True)
def _app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "APP_DIR", tmp_path / "app")


NOW = datetime(2026, 10, 4, 20, 30)          # 周日晚


class FakeBridge:
    """只有一件事的假桥:接不接这条任务、当前忙不忙。"""

    def __init__(self, busy=False):
        self.sent = []
        self.busy = busy

    def send_task(self, text, attachments="[]"):
        if self.busy:
            return {"ok": False, "msg": "当前会话有任务正在进行中"}
        self.sent.append(text)
        return {"ok": True, "session": "s1"}


class NoSendBridge:
    """网页版那一侧:没有 send_task。"""


# ---------------------------------------------------------------- ① 校验

def test_an_empty_task_is_refused_before_anything_is_written():
    got = jobs.add_user("每晚看待办", "   ")
    assert got["ok"] is False and "空任务" in got["msg"]
    assert jobs.load_user() == [], "校验不过却落了盘 = 列表里多一条跑不了的东西"


def test_a_task_needs_a_name_because_that_is_what_the_panel_clicks():
    got = jobs.add_user("  ", "整理今天的待办")
    assert got["ok"] is False and "起个名" in got["msg"]


def test_weekly_without_a_weekday_is_refused_not_silently_never_run():
    """JobSpec 自己会拒(weekly 必须给 weekday)—— 拒要拒在写盘之前。"""
    got = jobs.add_user("周报", "汇总一周", "weekly", None, 18, 0)
    assert got["ok"] is False and "weekday" in got["msg"]
    assert jobs.load_user() == []


def test_a_too_long_prompt_is_refused_with_the_real_number():
    got = jobs.add_user("长", "字" * (jobs.MAX_PROMPT + 1))
    assert got["ok"] is False and str(jobs.MAX_PROMPT) in got["msg"]


def test_the_cap_is_counted_not_assumed():
    for i in range(jobs.MAX_USER_JOBS):
        assert jobs.add_user(f"任务{i}", "做点什么")["ok"] is True
    got = jobs.add_user("超出的那条", "做点什么")
    assert got["ok"] is False and str(jobs.MAX_USER_JOBS) in got["msg"]
    assert len(jobs.load_user()) == jobs.MAX_USER_JOBS


def test_ids_never_collide_even_after_a_delete():
    a = jobs.add_user("甲", "一")["id"]
    b = jobs.add_user("乙", "二")["id"]
    assert a != b
    jobs.remove_user(a)
    c = jobs.add_user("丙", "三")["id"]
    assert c not in (a, b) or c == a          # 复用空位可以,撞车不行
    ids = [r["id"] for r in jobs.load_user()]
    assert len(ids) == len(set(ids)), f"id 撞了:{ids}"


# ---------------------------------------------------------------- ② 到点跑那句话

def test_a_saved_task_comes_back_as_a_spec_with_its_prompt():
    jobs.add_user("每晚看待办", "把今天要交的列三行", "daily", None, 20, 0)
    specs = jobs.user_specs()
    assert len(specs) == 1 and specs[0].prompt == "把今天要交的列三行"
    assert specs[0].hour == 20 and specs[0].cadence == "daily"


def test_all_specs_is_registered_plus_user_in_that_order():
    jobs.add_user("我的", "跑一下")
    specs = proactive.all_specs()
    assert [s.id for s in specs[:len(proactive.JOB_SPECS)]] == \
        [s.id for s in proactive.JOB_SPECS], "内置那几条的顺序不能因为加了人就漂"
    assert specs[-1].id.startswith("u") and specs[-1].prompt == "跑一下"


def test_a_broken_row_is_dropped_rather_than_guessed_into_a_schedule():
    """手改坏了 JSON(比如 cadence 写错):这一条不跑,但别把整屏带崩。"""
    jobs.add_user("好的", "跑")
    rows = jobs.load_user()
    rows.append({"id": "u9", "label": "坏的", "cadence": "hourly", "prompt": "x"})
    jobs.save_user(rows)
    specs = jobs.user_specs()
    assert [s.id for s in specs] == [r["id"] for r in rows if r["id"] != "u9"]


def test_the_due_user_job_sends_exactly_what_the_classmate_wrote():
    jobs.add_user("每晚看待办", "把今天要交的东西列三行", "daily", None, 20, 0)
    brs = FakeBridge()
    ran = proactive._run_due_jobs(brs, now=NOW)
    mine = [s.id for s in proactive.all_specs() if s.prompt]
    assert set(mine) <= set(ran), f"到点没跑:{ran}"
    assert brs.sent == ["把今天要交的东西列三行"], brs.sent


def test_the_result_landed_in_the_ledger_is_a_conclusion_not_the_prompt():
    """`jobs-state.json` 里那句 result 会显示在面板与 watchdog.log:
    记「已交给学长跑」就够,把同学原文再抄一遍进日志是多余的泄露面。"""
    jobs.add_user("每晚看待办", "把今天要交的列三行,里面有我的名字", "daily", None, 20, 0)
    proactive._run_due_jobs(FakeBridge(), now=NOW)
    book = json.loads(jobs.state_path().read_text(encoding="utf-8"))
    row = [v for k, v in book.items() if str(v.get("result") or "").startswith("已交给")]
    assert row, book
    assert "里面有我的名字" not in row[0]["result"]


def test_a_busy_session_skips_this_slot_instead_of_queuing_or_retrying_forever():
    jobs.add_user("每晚看待办", "列三行", "daily", None, 20, 0)
    proactive._run_due_jobs(FakeBridge(busy=True), now=NOW)
    book = json.loads(jobs.state_path().read_text(encoding="utf-8"))
    assert any("跳过" in str(v.get("result") or "") for v in book.values()), book
    # 记了账 = 这一班不再重试(下一班是明天),不会每 5 分钟撞一次"会话正忙"
    again = proactive._run_due_jobs(FakeBridge(), now=NOW + timedelta(minutes=5))
    mine = [s.id for s in proactive.all_specs() if s.prompt]
    assert not (set(mine) & set(again))


def test_without_a_send_channel_it_says_so_instead_of_pretending():
    jobs.add_user("某任务", "跑一下", "daily", None, 20, 0)
    proactive._run_due_jobs(NoSendBridge(), now=NOW)
    book = json.loads(jobs.state_path().read_text(encoding="utf-8"))
    assert any("没有可发任务的会话" in str(v.get("result") or "")
               for v in book.values()), book


# ---------------------------------------------------------------- ③ 一套机制

def test_the_shared_disabled_list_applies_to_user_jobs_too():
    jid = jobs.add_user("我的", "跑", "daily", None, 20, 0)["id"]
    jobs.set_enabled(jid, False)
    specs = proactive.all_specs()
    due = [s.id for s in jobs.due_jobs(specs, now=NOW, disabled=jobs.read_disabled())
           if s.prompt]
    assert due == [], "关掉却还到期 = 那颗开关是假的"
    jobs.set_enabled(jid, True)
    assert [s.id for s in jobs.due_jobs(specs, now=NOW,
                                        disabled=jobs.read_disabled()) if s.prompt]


def test_facts_marks_which_rows_are_the_classmates_own():
    """界面据此才敢给「删」那颗:内置的删不掉,给了就是引导人点一次失败。"""
    jobs.add_user("我的", "跑")
    rows = jobs.facts(proactive.all_specs(), now=NOW)
    mine = [r for r in rows if r["user"]]
    assert len(mine) == 1 and mine[0]["label"] == "我的"
    assert all(not r["user"] for r in rows if not r["id"].startswith("u"))


def test_remove_only_deletes_what_is_actually_there():
    jid = jobs.add_user("要删的", "跑")["id"]
    assert jobs.remove_user("u404")["ok"] is False
    assert jobs.remove_user(jid)["ok"] is True
    assert jobs.load_user() == []


def test_the_file_is_written_atomically_and_carries_a_self_explanation():
    jobs.add_user("一条", "跑")
    raw = json.loads(jobs.user_path().read_text(encoding="utf-8"))
    assert "_说明" in raw and raw["items"][0]["label"] == "一条"
    assert not list(jobs.user_path().parent.glob("*.tmp")), "临时文件没被 replace 掉"
