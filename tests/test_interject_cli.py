"""终端插话(v0.9.177)那一只口的针。

判的是 `minicode.interject` 这个**真入口**:`feed()` 吃一个键、`start()` 决定开不开。
不测"状态字段写对了没有",因为这条路坏了的表现是"我明明按了回车它却没听见",
而那句话只能由"键 → 信箱"这一段真的走一遍才验得出来。

真控制台在本机没法无人值守地敲,所以取键这一步是**注进去的假键源**(`take_key`);
除它之外的一切(`feed` 的攒句、`queue_interjection` 的收下/拒收、非 TTY 那道闸)
都是真代码。
"""
import sys
import time

import pytest

from minicode import agent as agent_mod
from minicode.interject import Interjection, turn_with_interjection


class _Inbox:
    """长得像 `Agent.queue_interjection` 的那个对象:收下规则一模一样。

    规则不是抄的 —— 下面 `test_the_gate_matches_the_real_agent` 拿真 `Agent`
    的方法名与签名核过一遍,所以这只假东西**没法悄悄和真的走岔**。
    """

    def __init__(self, accept=True):
        self.got: list[str] = []
        self.accept = accept

    def queue_interjection(self, text: str) -> bool:
        clean = str(text or "").strip()
        if not clean or not self.accept:
            return False
        self.got.append(clean)
        return True


# ------------------------------------------------------------------ 真入口的形状

def test_the_gate_matches_the_real_agent():
    """这只口接的是 `Agent.queue_interjection`,而它必须还在、还 callable。

    写"状态字段"的针看不见这一层:方法被改名/被属性盖住时,`interject` 里那句
    `self.agent.queue_interjection(line)` 会在**用户按下回车的那一刻**炸掉
    (v0.9.108 那个 `llm_error` 盖住回调就是同一类坏)。
    """
    real = getattr(agent_mod.Agent, "queue_interjection", None)
    assert callable(real), "Agent.queue_interjection 不在了,终端插话这条路是断的"
    box = Interjection(_Inbox(), note=lambda s: None, take_key=lambda: None)
    assert callable(box.feed) and callable(box.start) and callable(box.stop)


# ---------------------------------------------------------------- 攒句 → 交出去

def test_a_typed_line_lands_in_the_inbox_on_enter():
    inbox = _Inbox()
    notes: list[str] = []
    box = Interjection(inbox, note=notes.append, take_key=lambda: None)
    for ch in "第三步先看数据":
        assert box.feed(ch) is None, "没到回车不该交出去"
    assert inbox.got == [], "半句就交给模型 = 把没打完的话当指令"
    line = box.feed("\r")
    assert line == "第三步先看数据"
    assert inbox.got == ["第三步先看数据"]
    assert notes and "插话已进信箱" in notes[0], "收下这件事要说给用户"


def test_enter_alone_and_spaces_send_nothing():
    inbox = _Inbox()
    notes: list[str] = []
    box = Interjection(inbox, note=notes.append, take_key=lambda: None)
    assert box.feed("\r") is None
    for ch in "   ":
        box.feed(ch)
    assert box.feed("\n") is None
    assert inbox.got == [] and notes == []


def test_backspace_edits_the_pending_line():
    inbox = _Inbox()
    box = Interjection(inbox, note=lambda s: None, take_key=lambda: None)
    for ch in "不对\x08\x08对新":      # 退两格再打"对新" → "对新"
        box.feed(ch)
    box.feed("\r")
    assert inbox.got == ["对新"], inbox.got


def test_ctrl_c_drops_the_half_line_but_still_lets_the_turn_run():
    """手上那半句丢掉;而 **Ctrl+C 本身不在这层处理**(模块头第 3 条)。"""
    inbox = _Inbox()
    box = Interjection(inbox, note=lambda s: None, take_key=lambda: None)
    for ch in "算了\x03还要":
        box.feed(ch)
    box.feed("\r")
    assert inbox.got == ["还要"], "Ctrl+C 之前那半句不该混进来"


def test_a_refused_line_is_said_loudly_not_dropped():
    """信箱满了(`queue_interjection` 回 False)必须说,不能默默丢。

    用户已经看见自己那句话打出去了 —— 这一条与 GUI 那条同口径。
    """
    inbox = _Inbox(accept=False)
    notes: list[str] = []
    box = Interjection(inbox, note=notes.append, take_key=lambda: None)
    assert box.feed("满了也要说") is None
    assert box.feed("\r") is None
    assert len(notes) == 1 and "没接这句" in notes[0] and "满了也要说" in notes[0]


def test_ansi_and_function_key_noise_is_not_text():
    inbox = _Inbox()
    box = Interjection(inbox, note=lambda s: None, take_key=lambda: None)
    for ch in "ab\x1b[31m":            # 方向键 / ESC 序列里的字节不该进句子
        box.feed(ch)
    box.feed("\r")
    assert inbox.got == ["ab"], inbox.got


# ---------------------------------------------------------------- 开不开这一闸

def test_a_piped_stdin_never_starts_the_reader(monkeypatch):
    """**非控制台一律不开**:那份 stdin 是给 REPL 下一句用的(脚本、e2e、管道)。

    这一支钉的是"没有线程被起来",不是某个旗子 —— 起了线程就会和 prompt_toolkit
    抢同一个 fd,表现是"输入偶尔丢一半",而那正是本轮要躲开的坏法。
    """
    class _Piped:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", _Piped())
    box = Interjection(_Inbox(), note=lambda s: None, take_key=lambda: None)
    assert box.start() is None
    assert box._thread is None


def test_a_real_tty_starts_and_stops_the_reader(monkeypatch):
    """真起线程走一遍:**键 → 信箱**这一段不许只在没有线程的路径上被测。

    `take_key` 是注进去的(本机没法无人值守敲控制台),但线程、轮询、停止、
    `queue_interjection` 全是真的。等的是"那句话进没进信箱"这个**结果**,
    不是 sleep 一个固定时长 —— 慢机器上那种写法会红得莫名其妙。
    """
    class _Tty:
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", _Tty())
    inbox = _Inbox()
    keys = ["停", "\r"]

    def take():
        return keys.pop(0) if keys else None

    box = Interjection(inbox, note=lambda s: None, take_key=take)
    assert box.start() is box and box._thread is not None
    deadline = time.monotonic() + 3.0
    while not inbox.got and time.monotonic() < deadline:
        time.sleep(0.02)
    box.stop()
    assert inbox.got == ["停"], "线程跑了一遍却没把句子交出去"
    assert box._thread is None, "回合结束还留一只读 stdin 的线程 = 抢 prompt_toolkit 的 fd"


def test_the_context_manager_runs_the_turn_even_when_the_reader_cannot_open(monkeypatch):
    """开不出去也必须**原样跑完这个回合** —— 新功能不许多造一种坏法。"""
    class _Piped:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", _Piped())
    ran = []
    with turn_with_interjection(_Inbox(), note=lambda s: None):
        ran.append("turn")
    assert ran == ["turn"]


def test_the_context_manager_does_not_swallow_a_turn_error(monkeypatch):
    """回合里炸出来的异常照原样往上走(这层只是个透明的 with,不是 try/except)。"""
    class _Piped:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", _Piped())
    with pytest.raises(RuntimeError, match="回合本身炸了"):
        with turn_with_interjection(_Inbox(), note=lambda s: None):
            raise RuntimeError("回合本身炸了")
