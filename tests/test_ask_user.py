# -*- coding: utf-8 -*-
"""动手前问一句(v0.9.96):`ask_user` 的问题形状、三种出口、谁有权问。

这一层最容易做假的地方不是"弹不出来",而是**把没答说成答了**、把用户没选的
项写成他选的。所以下面的针按四条摆:
1. 坏参数一个框都不弹(为了一条坏调用打断用户是最坏的交易);
2. 答案逐字回传,界面回了没列过的 label 也要如实说出来,不归到某个选项上;
3. `unanswered` 与 `recommend` 是两种不同的"没逐题确认",都不许写成同意;
4. 停止键/关窗必须叫醒正在等的那条线程 —— 这条不只是体验问题:确认框与提问卡
   都持着一把锁在等前端,叫不醒就是"学长再也不问东西了"。
"""
from __future__ import annotations

import json
import threading

import pytest

from minicode import asking
from minicode.subagents import SUBAGENT_TYPES, sub_tool_registry, type_spec
from minicode.tools.base import Tool, ToolContext
from minicode.tools.registry import build_default_registry
from minicode.web_profile import EXCLUDED_TOOLS
from test_gui_bridge import FakeLLM, make, wait_until

pytestmark = pytest.mark.slow      # 有真线程与真桥端到端(等待是按秒轮询的)


def _qs(n=1, opts=2):
    return [{"q": f"第{i}题怎么办?", "header": f"分叉{i}", "why": "推荐第一项,因为快",
             "options": [{"label": f"选{j}", "note": f"代价{j}"} for j in range(opts)]}
            for i in range(n)]


# ---------------------------------------------------------------- 问题形状


def test_one_option_is_not_a_choice():
    _out, err = asking.normalize([{"q": "要不要?", "options": [{"label": "只此一家"}]}])
    assert "一个选项不构成选择" in err


def test_too_many_options_and_questions_are_rejected():
    assert "上限" in asking.normalize([{"q": "x", "options":
                                        [{"label": str(i)} for i in range(5)]}])[1]
    assert "一次最多问 3 题" in asking.normalize(_qs(4))[1]


def test_duplicate_labels_rejected_because_the_bridge_echoes_them_back():
    """界面按 label 回传:重名就分不出用户点的到底是哪个,所以直接拒。"""
    _out, err = asking.normalize([{"q": "x", "options":
                                   [{"label": "一样"}, {"label": "一样"}]}])
    assert "重复" in err


def test_scalar_options_and_single_dict_are_accepted():
    qs, err = asking.normalize({"q": "x", "options": ["甲", "乙"]})
    assert err == "" and len(qs) == 1 and qs[0]["options"][1]["label"] == "乙"
    assert qs[0]["id"] == 0                          # 归一化时补 id,前端按它回传


def test_long_fields_are_clipped_not_dropped():
    qs, err = asking.normalize([{"q": "问" * 300, "header": "标" * 30,
                                 "options": [{"label": "甲", "note": "n" * 300},
                                             {"label": "乙"}]}])
    assert err == ""
    assert qs[0]["q"].endswith("…") and len(qs[0]["q"]) == 201      # 截断带省略号
    assert qs[0]["header"].endswith("…") and len(qs[0]["header"]) == asking.HEADER_LIMIT + 1
    assert qs[0]["options"][0]["note"].endswith("…")
    assert len(qs[0]["options"][0]["note"]) == asking.NOTE_LIMIT + 1


def test_broken_questions_never_reach_the_ui():
    """坏参数 = 一句错误回给模型,一个框都不弹。"""
    seen = []

    class _UI:
        def ask_user(self, questions, note=""):
            seen.append(questions)
            return {"mode": "answer", "answers": []}

    ctx = ToolContext(workspace=None, config=None, ui=_UI())
    out = asking.ask_user(ctx, [{"q": "x", "options": ["只一个"]}])
    assert out.startswith("错误:") and seen == []
    asking.ask_user(ctx, _qs(1))
    assert len(seen) == 1                            # 合规则真的去问


# ---------------------------------------------------------------- 三种出口


def _norm():
    qs, err = asking.normalize(_qs(2))
    assert err == ""
    return qs


def test_answer_is_echoed_verbatim_including_free_text():
    qs = _norm()
    text = asking.format_answers(qs, {"mode": "answer", "answers": [
        {"id": 0, "choice": ["选1"], "free": ""},
        {"id": 1, "choice": [], "free": "我其实想先问清楚预算"}]})
    assert "第 1 题「第0题怎么办?」→ 选1" in text
    assert "他自己补了一句:我其实想先问清楚预算" in text
    assert "(没选)" in text                          # 没选就说没选,不猜


def test_unanswered_is_never_worded_as_agreement():
    qs = _norm()
    text = asking.format_answers(qs, {"mode": "unanswered", "reason": "你按了停止"})
    assert "没等到" in text and "不算同意" in text
    for banned in ("用户选了", "同意了", "按推荐"):
        assert banned not in text


def test_recommend_says_he_did_not_confirm_each_question():
    qs = _norm()
    text = asking.format_answers(qs, {"mode": "recommend"})
    assert "按推荐项「选0」" in text
    assert "没逐题确认" in text or "没有逐题点选" in text


def test_labels_the_model_never_offered_are_reported_not_absorbed():
    """界面(或被改过的夹具)回了不存在的 label:如实说出来,不许当成某个选项的别名。"""
    qs = _norm()
    text = asking.format_answers(qs, {"mode": "answer", "answers": [
        {"id": 0, "choice": ["我瞎写的项"], "free": ""},
        {"id": 1, "choice": ["选0"], "free": ""}]})
    assert "(没选)" in text and "我瞎写的项" in text and "没列过" in text


def test_multi_keeps_every_pick_in_order():
    qs, err = asking.normalize([{"q": "选几门", "multi": True,
                                 "options": [{"label": "甲"}, {"label": "乙"},
                                             {"label": "丙"}]}])
    assert err == ""
    text = asking.format_answers(qs, {"mode": "answer",
                                      "answers": [{"id": 0, "choice": ["丙", "甲"]}]})
    assert "选了 丙、甲" in text


def test_ui_without_a_channel_says_it_did_not_ask():
    class _NoAsk:
        pass

    ctx = ToolContext(workspace=None, config=None, ui=_NoAsk())
    out = asking.ask_user(ctx, _qs(1))
    assert "没问出去" in out and "别把没问当成同意" in out


# ---------------------------------------------------------------- 桥的端到端


def _tool(name="write_file", read_only=False):
    return Tool(name=name, description="", parameters={}, handler=lambda **k: "",
                read_only=read_only)


def test_bridge_round_trip_pushes_the_real_questions_and_returns_the_answer(config,
                                                                            tmp_path):
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    qs, _err = asking.normalize(_qs(2))
    got: dict = {}
    th = threading.Thread(target=lambda: got.update(
        bridge.ask_user(qs, note="定了再排步骤")))
    th.start()
    assert wait_until(lambda: any(e["type"] == "ask_request" for e in window.events),
                      timeout=15)
    req = [e for e in window.events if e["type"] == "ask_request"][-1]
    assert [q["q"] for q in req["questions"]] == [q["q"] for q in qs]   # 原文,没被改写
    assert req["note"] == "定了再排步骤"

    assert bridge.ask_reply(json.dumps({"mode": "answer", "answers": [
        {"id": 0, "choice": ["选1"], "free": ""},
        {"id": 1, "choice": ["选0"], "free": ""}]}))["ok"] is True
    th.join(timeout=15)
    assert got["mode"] == "answer"
    assert [a["choice"] for a in got["answers"]] == [["选1"], ["选0"]]
    trace = [e for e in window.events if e["type"] == "ask_trace"]
    assert trace and "选1" in trace[-1]["text"]      # 聊天区那一行痕出自同一次回答


def test_garbage_reply_does_not_release_the_wait_with_a_fake_answer(config, tmp_path):
    """坏 payload 只能被拒收,**绝不能**把正在等的那条线程放出去(那等于凭空造答案)。"""
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    qs, _err = asking.normalize(_qs(1))
    got: dict = {}
    th = threading.Thread(target=lambda: got.update(bridge.ask_user(qs)))
    th.start()
    assert wait_until(lambda: any(e["type"] == "ask_request" for e in window.events),
                      timeout=15)
    for bad in ("{不是 JSON", json.dumps({"mode": "随便"}),
                json.dumps({"mode": "answer"})):
        assert bridge.ask_reply(bad)["ok"] is False
    assert got == {}                                 # 还没被放行
    bridge.ask_reply(json.dumps({"mode": "recommend"}))
    th.join(timeout=15)
    assert got["mode"] == "recommend"


def test_stop_button_wakes_a_pending_question(config, tmp_path):
    """D3 的硬证明:用户按停止,那次等待必须回来,而且回的是"没等到"。"""
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    qs, _err = asking.normalize(_qs(1))
    got: dict = {}
    th = threading.Thread(target=lambda: got.update(bridge.ask_user(qs)))
    th.start()
    assert wait_until(lambda: any(e["type"] == "ask_request" for e in window.events),
                      timeout=15)
    bridge.ui.request_stop()
    th.join(timeout=15)
    assert not th.is_alive(), "停止键没叫醒提问:那条线程带着锁挂死了"
    assert got["mode"] == "unanswered" and "停止" in got["reason"]


def test_stop_button_also_wakes_a_pending_confirm(config, tmp_path):
    """v0.9.96 补的既有隐患:`_confirm_event.wait()` 原先没人叫醒,按了停止之后
    所有确认框都弹不出来(锁还挂在死等的那条线程手里)。"""
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    choice: dict = {}
    th = threading.Thread(target=lambda: choice.update(v=bridge.confirm_tool(
        _tool(), {"path": "a.txt"}, "要写文件")))
    th.start()
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events),
                      timeout=15)
    bridge.ui.request_stop()
    th.join(timeout=15)
    assert not th.is_alive(), "确认框的等待没人叫醒 —— 锁会一直挂在这条线程上"
    assert choice["v"] == "no"                       # 没答就是不同意,方向安全


def test_close_wakes_a_pending_question(config, tmp_path):
    """关窗(而不是按停止)也算"不会再有人答了":等着的线程必须回来。"""
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    qs, _err = asking.normalize(_qs(1))
    a: dict = {}
    t1 = threading.Thread(target=lambda: a.update(bridge.ask_user(qs)))
    t1.start()
    assert wait_until(lambda: any(e["type"] == "ask_request" for e in window.events),
                      timeout=15)
    bridge.close()
    t1.join(timeout=15)
    assert not t1.is_alive(), "关窗没叫醒提问:那条线程带着提问锁挂死了"
    assert a.get("mode") == "unanswered"
    assert "关闭" in a.get("reason", "")


def test_confirm_queued_behind_a_question_still_gets_its_turn(config, tmp_path):
    """提问卡与确认框各一条锁:确认框不该因为"有人在回答问题"就永远排不到。

    这条测的是排队而不是并发:提问持锁期间起一个确认线程,回答提问之后确认框
    必须能推出来。两条通道要是共用一把锁,这里就会卡死。
    """
    bridge, window, _session = make(config, tmp_path, FakeLLM([]))
    qs, _err = asking.normalize(_qs(1))
    a: dict = {}
    t1 = threading.Thread(target=lambda: a.update(bridge.ask_user(qs)))
    t1.start()
    assert wait_until(lambda: any(e["type"] == "ask_request" for e in window.events),
                      timeout=15)
    c: dict = {}
    t2 = threading.Thread(target=lambda: c.update(v=bridge.confirm_tool(
        _tool(), {"path": "a.txt"}, "要写文件")))
    t2.start()
    bridge.ask_reply(json.dumps({"mode": "recommend"}))
    t1.join(timeout=15)
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events),
                      timeout=15)
    bridge.confirm_reply("yes")
    t2.join(timeout=15)
    assert c["v"] == "yes"


def test_two_questions_do_not_share_the_confirm_channel(config, tmp_path):
    """提问与确认各一条锁:共用会出现"用户点了提问的答案,被当成上一个确认的同意"。"""
    bridge, _window, _session = make(config, tmp_path, FakeLLM([]))
    assert bridge._ask_lock is not bridge._confirm_lock
    assert bridge._ask_event is not bridge._confirm_event


# ---------------------------------------------------------------- 谁有权问


def test_no_subagent_bucket_can_ask_the_user():
    """子代理跑在静默通道里,没人可问:要问由主代理问(用户拍的 D1)。"""
    registry = build_default_registry()
    assert any(t.name == "ask_user" for t in registry)
    for key in SUBAGENT_TYPES:
        spec = type_spec(key, None)
        names = {t.name for t in sub_tool_registry(registry, spec)}
        assert not any(n.endswith("ask_user") for n in names), f"{key} 档拿到了提问工具"


def test_web_edition_excludes_it_with_a_reason():
    reason = EXCLUDED_TOOLS.get("ask_user")
    assert reason, "网页版没给这只工具写明排除理由(它有条闸要求每个工具要么公开要么点名排除)"
    assert "访客" in reason


def test_the_tool_is_registered_read_only_and_after_plan():
    names = [t.name for t in build_default_registry()]
    tool = next(t for t in build_default_registry() if t.name == "ask_user")
    assert tool.read_only is True          # 它什么都不改,给它弹确认框是自指
    assert names.index("ask_user") == names.index("plan") + 1


# ---------------------------------------------------------------- 终端形态


class _Console:
    """假 console:喂好答案序列,记下面板与提示都印了什么。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.shown: list[str] = []

    def print(self, *a, **k):
        for x in a:
            self.shown.append(getattr(x, "plain", None) or str(x))

    def input(self, *a, **k):
        if not self.answers:
            raise EOFError
        got = self.answers.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got


class _Panel:
    def __init__(self, renderable=None, **k):
        self.renderable = renderable


def _terminal():
    from minicode.ui import TerminalUI
    ui = TerminalUI.__new__(TerminalUI)          # 不起 rich 控制台,只测这条方法
    return ui


def test_terminal_pick_returns_the_label_not_the_number(config):
    from minicode import asking as A
    ui = _terminal()
    ui.console = _Console(["2"])
    import minicode.ui as ui_mod
    ui_mod.Panel = _Panel
    qs, _err = A.normalize(_qs(1))
    out = ui.ask_user(qs)
    assert out["mode"] == A.MODE_ANSWER
    assert out["answers"][0]["choice"] == ["选1"]          # 编号 2 → 第二个 label


def test_terminal_free_text_is_not_absorbed_into_an_option(config):
    from minicode import asking as A
    ui = _terminal()
    ui.console = _Console(["我只有 800 块预算"])
    import minicode.ui as ui_mod
    ui_mod.Panel = _Panel
    qs, _err = A.normalize(_qs(1))
    out = ui.ask_user(qs)
    assert out["answers"][0]["choice"] == []
    assert out["answers"][0]["free"] == "我只有 800 块预算"


def test_terminal_recommend_and_no_input_are_two_different_things(config):
    from minicode import asking as A
    import minicode.ui as ui_mod
    ui_mod.Panel = _Panel
    qs, _err = A.normalize(_qs(1))
    ui = _terminal(); ui.console = _Console(["r"])
    assert ui.ask_user(qs)["mode"] == A.MODE_RECOMMEND
    ui2 = _terminal(); ui2.console = _Console([EOFError()])
    out = ui2.ask_user(qs)
    assert out["mode"] == A.MODE_UNANSWERED        # 管道/Ctrl+C:按没答处理,不猜
    ui3 = _terminal(); ui3.console = _Console([""])
    out3 = ui3.ask_user(qs)
    assert out3["answers"][0]["choice"] == [] and out3["answers"][0]["free"] == ""


def test_terminal_multi_accepts_comma_separated_picks(config):
    from minicode import asking as A
    import minicode.ui as ui_mod
    ui_mod.Panel = _Panel
    qs, _err = A.normalize([{"q": "选几门", "multi": True,
                             "options": [{"label": "甲"}, {"label": "乙"},
                                         {"label": "丙"}]}])
    ui = _terminal(); ui.console = _Console(["3,1"])
    out = ui.ask_user(qs)
    assert out["answers"][0]["choice"] == ["丙", "甲"]
