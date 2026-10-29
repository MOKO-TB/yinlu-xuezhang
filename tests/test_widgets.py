# -*- coding: utf-8 -*-
"""交互式可视化的规格、工具推送与历史回放(v0.9.185)。

这一层最容易做假的地方是"看起来画出来了":数据缺一项、数值是 NaN、
饼图两个系列 —— 前端如果硬画,用户会看到一张正常的假图。所以下面
按四条摆针:坏形状拒绝、上限硬拒、工具没通道要说没展示、历史回放
读的是同一份规格。
"""
from __future__ import annotations

import json

import pytest

from minicode import widgets
from minicode.gui_bridge import Bridge
from minicode.llm import AssistantTurn, ToolCall
from minicode.tools import catalog_viz
from minicode.tools.base import ToolContext
from test_gui_bridge import FakeLLM, make, wait_until


# ---------------------------------------------------------------- 规格

def test_chart_accepts_a_good_spec_and_keeps_numbers():
    spec, err = widgets.normalize_chart({
        "title": "绩点趋势", "chart_type": "line",
        "labels": ["大一", "大二"], "series": [
            {"name": "绩点", "values": [3.2, 3.5]}],
        "note": "scores.json"})
    assert err == ""
    assert spec["kind"] == "chart"
    assert spec["series"][0]["values"] == [3.2, 3.5]
    assert spec["note"] == "scores.json"


def test_chart_rejects_mismatched_values_and_pie_with_two_series():
    _spec, err = widgets.normalize_chart({
        "title": "x", "labels": ["a", "b"],
        "series": [{"name": "s", "values": [1]}]})
    assert "数量" in err
    _spec, err = widgets.normalize_chart({
        "title": "x", "chart_type": "pie", "labels": ["a", "b"],
        "series": [{"name": "s1", "values": [1, 2]},
                   {"name": "s2", "values": [2, 1]}]})
    assert "饼图" in err


def test_chart_rejects_non_finite_and_negative_pie():
    _spec, err = widgets.normalize_chart({
        "title": "x", "labels": ["a", "b"],
        "series": [{"name": "s", "values": [1, float("nan")]}]})
    assert "有限" in err
    _spec, err = widgets.normalize_chart({
        "title": "x", "chart_type": "pie", "labels": ["a", "b"],
        "series": [{"name": "s", "values": [1, -1]}]})
    assert "负数" in err


def test_limits_are_rejected_not_silently_truncated():
    _spec, err = widgets.normalize_chart({
        "title": "x", "labels": [str(i) for i in range(widgets.MAX_LABELS + 1)],
        "series": [{"name": "s", "values": [1] * (widgets.MAX_LABELS + 1)}]})
    assert "最多" in err
    _spec, err = widgets.normalize_table({
        "title": "x", "columns": ["a"],
        "rows": [[i] for i in range(widgets.MAX_ROWS + 1)]})
    assert "最多" in err
    _spec, err = widgets.normalize_checklist({
        "title": "x", "items": [{"label": str(i)}
                                for i in range(widgets.MAX_ITEMS + 1)]})
    assert "最多" in err


def test_table_keeps_numbers_and_rejects_ragged_rows():
    spec, err = widgets.normalize_table({
        "title": "名单", "columns": ["姓名", "分数"],
        "rows": [["张三", 90], ["李四", 85]]})
    assert err == "" and spec["rows"][0][1] == 90
    _spec, err = widgets.normalize_table({
        "title": "x", "columns": ["a", "b"], "rows": [["only-one"]]})
    assert "列数" in err


def test_checklist_accepts_strings_and_rejects_duplicate_labels():
    spec, err = widgets.normalize_checklist({
        "title": "待办", "items": ["找张三", {"label": "交材料", "note": "周五前"}]})
    assert err == "" and spec["items"][0]["label"] == "找张三"
    _spec, err = widgets.normalize_checklist({
        "title": "x", "items": ["一样", "一样"]})
    assert "重复" in err


# ---------------------------------------------------------------- 工具推送

class _UI:
    def __init__(self):
        self.events = []

    def push(self, type_, **payload):
        self.events.append({"type": type_, **payload})


def test_show_chart_pushes_a_normalized_widget():
    ui = _UI()
    ctx = ToolContext(workspace=None, config=None, ui=ui)
    out = catalog_viz.show_chart(
        ctx, title="绩点趋势", chart_type="line",
        labels=["大一", "大二"],
        series=[{"name": "绩点", "values": [3.2, 3.5]}],
        note="scores.json")
    assert "已展示" in out
    assert ui.events and ui.events[0]["type"] == "ui_widget"
    got = ui.events[0]["widget"]
    assert got["kind"] == "chart" and got["series"][0]["values"] == [3.2, 3.5]


def test_widget_without_a_channel_says_it_did_not_show():
    out = catalog_viz.show_chart(
        ToolContext(workspace=None, config=None, ui=None),
        title="x", labels=["a", "b"],
        series=[{"name": "s", "values": [1, 2]}])
    assert "没展示" in out and "文字" in out


# ---------------------------------------------------------------- 历史回放

def test_history_carries_ui_calls_and_pairs_the_answer():
    messages = [
        {"role": "assistant", "content": "先问一句", "tool_calls": [
            {"id": "t1", "type": "function", "function": {
                "name": "ask_user",
                "arguments": json.dumps({"questions": [{
                    "q": "选哪个?", "options": [{"label": "甲"}, {"label": "乙"}]}]})}},
        ]},
        {"role": "tool", "tool_call_id": "t1",
         "content": "用户回答:\n第 1 题「选哪个?」→ 甲"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t2", "type": "function", "function": {
                "name": "show_chart",
                "arguments": json.dumps({
                    "title": "x", "labels": ["a", "b"],
                    "series": [{"name": "s", "values": [1, 2]}]})}},
        ]},
        {"role": "tool", "tool_call_id": "t2", "content": "已展示图表"},
    ]
    out = Bridge._messages_for_frontend(type("S", (), {"messages": messages})())
    assert out[0]["ui_calls"][0]["name"] == "ask_user"
    assert out[0]["ui_calls"][0]["questions"][0]["q"] == "选哪个?"
    assert out[1]["name"] == "ask_user" and "甲" in out[1]["text"]
    assert out[2]["ui_calls"][0]["widget"]["kind"] == "chart"


# ---------------------------------------------------------------- 真实 Agent 链路

@pytest.mark.slow
def test_agent_can_show_a_chart_in_the_real_session_ui(config, tmp_path):
    """工具不是只直接调一次:要走 Agent → 注册表 → handler → SessionGuiUI.push,
    事件还必须带会话键,前端才知道画在哪条会话里。"""
    call = ToolCall(id="c1", name="show_chart", arguments=json.dumps({
        "title": "近四周待办", "chart_type": "bar",
        "labels": ["W1", "W2"], "series": [{"name": "完成", "values": [8, 12]}]}))
    llm = FakeLLM([AssistantTurn(tool_calls=[call]),
                   AssistantTurn(text="图已经画好。")])
    bridge, window, _ = make(config, tmp_path, llm)
    assert bridge.send_task("把待办画成图")["ok"]
    assert wait_until(lambda: any(e["type"] == "ui_widget" for e in window.events),
                      timeout=10)
    got = [e for e in window.events if e["type"] == "ui_widget"][-1]
    assert got["widget"]["kind"] == "chart"
    assert got.get("session")
    assert wait_until(lambda: not bridge._busy, timeout=10)


# ---------------------------------------------------------------- 清单发回

@pytest.mark.slow
def test_checklist_submit_becomes_a_user_message(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="收到,按这三项来。")])
    bridge, _window, _ = make(config, tmp_path, llm)
    got = bridge.widget_action("w1", "submit_checklist", json.dumps({
        "title": "本周待办",
        "selected": ["找张三谈心", "交材料"]}))
    assert got["ok"] and "勾选了" in got["text"]
    assert wait_until(lambda: any(
        m.get("role") == "user" and "找张三谈心" in str(m.get("content") or "")
        for m in bridge.session.messages))
    assert wait_until(lambda: not bridge._busy)


def test_widget_action_rejects_empty_or_unknown():
    assert "不认识" in _action_error("nope", {"selected": ["x"]})
    assert "一项都没选" in _action_error("submit_checklist", {"selected": []})


def _action_error(action, payload):
    """只测参数闸:不建 Bridge,直接借 Bridge 的方法需要一个最轻的桩。"""
    class _Stub:
        def send_task(self, text):
            raise AssertionError("不该走到发送")
    return Bridge.widget_action(_Stub(), "w1", action, json.dumps(payload))["msg"]
