"""v0.9.10:回复下方的实测数据(turn_stats)与侧栏会话按工作区分组。

turn_stats:Agent 在每个流式回合结束后推一条事件,带 elapsed_ms / tokens /
tps / reasoning;前端挂在最近一条有正文的助手消息下面。这里只测后端事实:
事件确实推了、字段口径正确(服务端 usage 优先、缺了不硬凑)。

分组:_push_sessions 现在每条会话都带 workspace_display + ws_current,
前端按它把不同工作区的会话分成组(当前工作区组置顶)。
"""
from __future__ import annotations

import json


from minicode.gui_bridge import create_app
from minicode.llm import AssistantTurn

from tests.test_gui_bridge import FakeLLM, FakeWindow, wait_until


def make(config, tmp_path, llm):
    bridge, ui, agent, session = create_app(
        config, tmp_path, llm=llm, sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json")
    window = FakeWindow()
    ui.attach(window)
    return bridge, window, agent


# ------------------------------------------------------------- turn_stats

def test_turn_stats_pushed_after_reply(config, tmp_path):
    turn = AssistantTurn(text="你好", elapsed_ms=1200,
                         usage={"prompt_tokens": 10, "completion_tokens": 30})
    bridge, window, _ = make(config, tmp_path, FakeLLM([turn]))

    bridge.send_task("打个招呼")
    assert wait_until(lambda: not bridge._busy)

    stats = window.of_type("turn_stats")
    assert len(stats) == 1
    ev = stats[0]
    assert ev["elapsed_ms"] == 1200
    assert ev["tokens"] == 30
    assert ev["tps"] == 25.0          # 30 token / 1.2 s
    assert ev["reasoning"] is False


def test_turn_stats_without_usage_still_pushes_elapsed(config, tmp_path):
    """服务端没回 usage:tokens 缺、tps 缺,但耗时照样显示。"""
    turn = AssistantTurn(text="纯文本", elapsed_ms=800, usage=None)
    bridge, window, _ = make(config, tmp_path, FakeLLM([turn]))

    bridge.send_task("在吗")
    assert wait_until(lambda: not bridge._busy)

    ev = window.of_type("turn_stats")[0]
    assert ev["elapsed_ms"] == 800
    assert ev["tokens"] is None
    assert ev["tps"] is None


def test_turn_stats_marks_reasoning(config, tmp_path):
    turn = AssistantTurn(text="结论", reasoning="想一想…", elapsed_ms=2000,
                         usage={"prompt_tokens": 5, "completion_tokens": 50})
    bridge, window, _ = make(config, tmp_path, FakeLLM([turn]))

    bridge.send_task("思考题")
    assert wait_until(lambda: not bridge._busy)

    ev = window.of_type("turn_stats")[0]
    assert ev["reasoning"] is True
    assert ev["tokens"] == 50
    assert ev["tps"] == 25.0


def test_turn_stats_skipped_without_elapsed(config, tmp_path):
    """老 LLM 适配器(不记耗时)推不出事件,回合一律照常。"""
    turn = AssistantTurn(text="旧版适配器", elapsed_ms=None)
    bridge, window, _ = make(config, tmp_path, FakeLLM([turn]))

    bridge.send_task("兼容")
    assert wait_until(lambda: not bridge._busy)
    assert window.of_type("turn_stats") == []
    assert any(e["type"] == "finish_stream" for e in window.events)


def test_llm_stream_once_records_elapsed(tmp_path, monkeypatch):
    """_stream_once 自己计时:elapsed_ms > 0;completion_tokens 优先服务端。"""
    from minicode.config import Config, Provider
    from minicode.llm import LLMClient

    cfg = Config(providers=[Provider(id="t", name="T",
                                     base_url="https://example.invalid/v1",
                                     api_key="k", model="m")],
                 active_provider_id="t")
    client = LLMClient(cfg)

    class _Delta:
        content = "你好"
        reasoning = None
        tool_calls = None

    class _Choice:
        delta = _Delta()

    class _Chunk:
        choices = [_Choice()]
        usage = None

    class _UsageChunk:
        choices = []
        usage = type("U", (), {"prompt_tokens": 7, "completion_tokens": 9})()

    def fake_create(**kwargs):
        return iter([_Chunk(), _UsageChunk()])

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    turn = client._stream_once([{"role": "user", "content": "hi"}], [],
                               None, None)
    assert turn.elapsed_ms is not None and turn.elapsed_ms >= 0
    assert turn.completion_tokens == 9          # 服务端 usage 优先,不走估算


# ------------------------------------------------- 侧栏会话按工作区分组

def _seed_sessions(tmp_path, entries):
    """手工造会话留档:entries = [(文件名, workspace, 用户消息), ...]"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    import os
    import time as _time
    for i, (name, workspace, text) in enumerate(entries):
        p = sessions_dir / name
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_start", "time": "2026-09-16 10:0%d:00" % i,
                                "workspace": workspace}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"role": "user", "content": text},
                               ensure_ascii=False) + "\n")
        # mtime 错开,保证 list_sessions 的排序稳定(文件名排序不可靠)
        stamp = _time.time() - len(entries) + i
        os.utime(p, (stamp, stamp))


def test_sessions_group_fields(config, tmp_path):
    """每条会话都带 ws_current/workspace:当前工作区组不标名,其他组带名字。"""
    here = str(tmp_path)
    other = "D:/别的项目"
    _seed_sessions(tmp_path, [
        ("session_20260916_100000.jsonl", here, "当前项目的问题"),
        ("session_20260916_100100.jsonl", other, "别的项目的问题"),
        ("session_20260916_100200.jsonl", "", "老版本没记录工作区"),
    ])
    llm = FakeLLM([AssistantTurn(text="好")])
    bridge, window, _ = make(config, tmp_path, llm)
    bridge.refresh_sessions()

    ev = window.of_type("sessions")[-1]
    items = ev["items"]
    assert len(items) == 3
    by_text = {i["last_user_text"]: i for i in items}
    cur = by_text["当前项目的问题"]
    assert cur["ws_current"] is True and cur["workspace"] == ""
    other_item = by_text["别的项目的问题"]
    assert other_item["ws_current"] is False
    assert other_item["workspace"]            # 显示名非空(目录不存在也兜底有名字)
    legacy = by_text["老版本没记录工作区"]
    assert legacy["ws_current"] is False
    assert legacy["workspace"] == "未记录"


def test_sessions_limit_raised_to_50(config, tmp_path):
    """侧栏不再截 12 条:50 条以内全量下发(分组后每个项目都能看到自己的会话)。"""
    _seed_sessions(tmp_path, [
        (f"session_20260916_10{i:02d}00.jsonl", str(tmp_path), f"任务 {i}")
        for i in range(20)
    ])
    bridge, window, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    bridge.refresh_sessions()
    assert len(window.of_type("sessions")[-1]["items"]) == 20
