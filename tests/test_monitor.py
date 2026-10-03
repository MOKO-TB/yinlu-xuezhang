# -*- coding: utf-8 -*-
"""任务监控栏的偏好落盘(v0.9.37):展开/收起 + 四段各自的折叠状态。

这一档测试盯的是"记不记得住"这一类假:点了收起、重启又弹开,或折了「计划」再折
「产出」把「计划」弹回来,都是用户一眼能看见、代码里却很容易写错的地方。
所以每条都是**写一次、重新读一次**,而不是只断言函数返回值。
"""
from __future__ import annotations

import json

from minicode import monitor


def _pref(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "pref_path", lambda: tmp_path / monitor.PREF_FILE)
    return tmp_path / monitor.PREF_FILE


def test_first_run_defaults_to_collapsed(tmp_path, monkeypatch):
    """没写过偏好 = 收起:这一栏不该在第一次开机就占掉 300px 对话宽度。"""
    _pref(tmp_path, monkeypatch)
    assert monitor.read_pref() == {"open": False, "folded": {}}


def test_open_state_survives_a_reread(tmp_path, monkeypatch):
    path = _pref(tmp_path, monkeypatch)
    assert monitor.write_pref(open_=True)["open"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["open"] is True
    assert monitor.read_pref()["open"] is True
    monitor.write_pref(open_=False)
    assert monitor.read_pref()["open"] is False


def test_folding_one_section_keeps_the_others_folded(tmp_path, monkeypatch):
    """`folded` 是**增量**:折了「计划」再折「产出」,「计划」必须还折着。"""
    _pref(tmp_path, monkeypatch)
    monitor.write_pref(open_=True, folded={"plan": True})
    monitor.write_pref(folded={"groups": True})
    st = monitor.read_pref()
    assert st["folded"] == {"plan": True, "groups": True}
    assert st["open"] is True                    # 只改折叠不该动展开状态


def test_unknown_section_keys_are_dropped(tmp_path, monkeypatch):
    """老版本/手改出来的陌生段名不回给界面:前端照它建段就是一次 undefined。"""
    path = _pref(tmp_path, monkeypatch)
    path.write_text(json.dumps({"open": True,
                                "folded": {"plan": True, "nonsense": True}}),
                    encoding="utf-8")
    assert monitor.read_pref()["folded"] == {"plan": True}


def test_garbage_file_falls_back_to_defaults_instead_of_raising(tmp_path, monkeypatch):
    """文件坏了/是数组/是半截 JSON:一律回默认,面板照样打得开。"""
    path = _pref(tmp_path, monkeypatch)
    for bad in ("", "{not json", "[]", "null"):
        path.write_text(bad, encoding="utf-8")
        assert monitor.read_pref() == {"open": False, "folded": {}}


def test_unwritable_dir_does_not_break_the_toggle(tmp_path, monkeypatch):
    """装进 Program Files 时数据目录只读:记不住也得让这一栏当场能用。"""
    _pref(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, "pref_path",
                        lambda: tmp_path / "没有这个目录" / monitor.PREF_FILE)
    assert monitor.write_pref(open_=True)["open"] is True   # 内存那份仍然对


def _bridge(tmp_path):
    from minicode.config import Config, Provider
    from minicode.gui_bridge import create_app
    from tests.test_gui_bridge import FakeLLM, FakeWindow

    cfg = Config(providers=[Provider(id="t", name="T",
                                      base_url="https://example.invalid/v1",
                                      api_key="k", model="m")],
                 active_provider_id="t")
    bridge, ui, _agent, _session = create_app(
        cfg, tmp_path, llm=FakeLLM([]), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    ui.attach(FakeWindow())
    return bridge


def test_bridge_round_trip_between_get_and_set(tmp_path, monkeypatch):
    """桥上的 get/set 是前端唯一够得着的路:写进去的必须原样读回来。"""
    _pref(tmp_path, monkeypatch)
    b = _bridge(tmp_path)
    assert b.get_monitor_pref() == {"ok": True, "open": False, "folded": {}}
    r = b.set_monitor_pref(True, json.dumps({"timeline": True}))
    assert r["open"] is True and r["folded"] == {"timeline": True}
    assert b.get_monitor_pref()["folded"] == {"timeline": True}


def test_bridge_reads_the_strings_webview_actually_sends(tmp_path, monkeypatch):
    """webview 常把 JS 的 true 送成字符串 "true"、把没填的参数送成空串。

    只认 Python 布尔的话,表现为"点展开没反应"—— 而且只在真窗口里复现。
    """
    _pref(tmp_path, monkeypatch)
    b = _bridge(tmp_path)
    assert b.set_monitor_pref("true")["open"] is True
    assert b.set_monitor_pref("false")["open"] is False
    assert b.set_monitor_pref(None, "坏 JSON")["open"] is False   # 不炸、不改状态
    assert b.set_monitor_pref(None, json.dumps({"now": True}))["folded"] == {"now": True}


def test_public_api_forwards_the_monitor_names():
    """js_api 门面是逐条手写的:漏一条,前端点一次就是一次 undefined。"""
    import inspect

    from minicode.gui_bridge import PublicApi
    for name in ("get_monitor_pref", "set_monitor_pref", "get_activity"):
        assert hasattr(PublicApi, name), name
    assert list(inspect.signature(PublicApi.set_monitor_pref).parameters) == \
        ["self", "is_open", "folded_json"]


def test_section_names_match_the_frontends_own_sections():
    """`SECTIONS` 与前端 `actSect("…")` 的段名必须一一对应。

    名字漂了的后果不是报错,而是**"用户折了它、下次开机又弹开"** —— `_clean_folded`
    把陌生键丢掉(v0.9.111 的「环境信息」就是这么漏的:那一节早就在画,段名却没登记,
    折叠状态写进去、读出来时被当垃圾扔了)。这条闸把"改一处要改两处"变成机器的事。
    """
    import re
    from pathlib import Path
    fe = (Path(__file__).resolve().parent.parent / "frontend" / "index.html")         .read_text(encoding="utf-8")
    names = set(re.findall(r'actSect\(\s*"([a-z_]+)"', fe))
    assert names == set(monitor.SECTIONS), (
        f"前端在画的段与登记过的段对不上:{names ^ set(monitor.SECTIONS)}")
