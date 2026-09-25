"""模型连不上之后的那条活路:轻探活 + 诚实的恢复信号(本轮审计的发现三)。

现状是死胡同:`llm.py` 重试 2 次就抛,界面上只剩一句「模型调用失败: …」,之后
**没有任何恢复信号** —— 校园网/内网网关抖动几十秒就回来的日常,用户只能反复点发送试。
这份文件钉三件事:

1. `watch_liveness` 只问 `GET /models`,不发对话请求(不动同学的 Key 额度),
   问通就收工、问不通最多盯 N 次,探测自己出错不能把看门狗弄死;
2. `GuiUI.llm_error` 推的事件要带 `llm` 标记 —— 前端靠它区分"整句没送到模型"与
   "工具跑到一半才错",只有前者才把话放回输入框;
3. 看门狗回调出错不能连带把那条错误提示吞掉(用户最需要的正是那句)。
"""
import json

from minicode import model_probe
from minicode.gui_ui import GuiUI


class FakeWindow:
    def __init__(self):
        self.events = []

    def evaluate_js(self, code):
        prefix = "window.__gcodeEvent("
        self.events.append(json.loads(code[len(prefix):-1]))


def script_probe(results):
    """按脚本回答活/不活,顺手数被问了几次。"""
    calls = []

    def probe():
        calls.append(1)
        outcome = results[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    probe.calls = calls
    return probe


# ---------------------------------------------------------------- 探活本体

def test_watcher_stops_at_the_first_answer():
    slept = []
    probe = script_probe([False, False, True])
    got = model_probe.watch_liveness(probe, interval=60, sleep=slept.append)
    assert got == {"recovered": True, "tried": 3}
    assert slept == [60, 60], "问通就不该再睡最后一轮"
    assert len(probe.calls) == 3, "问通之后不能继续敲服务"


def test_watcher_gives_up_after_the_budget_and_says_so():
    slept = []
    probe = script_probe([False] * 4)
    got = model_probe.watch_liveness(probe, interval=5, attempts=4, sleep=slept.append)
    assert got == {"recovered": False, "tried": 4}
    assert len(slept) == 3, "最后一次问完不必再等"


def test_a_probe_that_throws_counts_as_still_down():
    """探测本身报错(连不上正是它该处理的常态)不能把看门狗弄死。"""
    probe = script_probe([ConnectionError("拒绝连接"), True])
    got = model_probe.watch_liveness(probe, interval=1, sleep=lambda _s: None)
    assert got["recovered"] is True and got["tried"] == 2


# ---------------------------------------------------------------- 事件形状

def test_llm_error_marks_the_event_and_notifies_the_bridge():
    ui = GuiUI()
    win = FakeWindow()
    ui.attach(win)
    seen = []
    ui.on_llm_failure = seen.append
    ui.llm_error(RuntimeError("401 unauthorized"))

    err = [e for e in win.events if e["type"] == "error"]
    assert err and err[-1].get("llm") is True, "前端靠这个标记决定要不要把话放回输入框"
    assert "401" in err[-1]["text"]
    assert len(seen) == 1, "一次失败叫一次看门狗(多条只起一条是桥那边钉的)"


def test_a_broken_watchdog_does_not_eat_the_error_message():
    ui = GuiUI()
    win = FakeWindow()
    ui.attach(win)

    def boom(_msg):
        raise RuntimeError("看门狗自己起不来")

    ui.on_llm_failure = boom
    ui.llm_error(RuntimeError("连不上"))
    assert any(e["type"] == "error" for e in win.events), "回调炸了也要把那句失败留在界面上"
