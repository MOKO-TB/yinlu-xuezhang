"""跨会话记忆:模型侧的 `search_history` 工具与那条受控出口。

`search_logs` 早就写好了,但它的消费者只有 GUI 侧栏搜索框一个 —— 模型侧零入口,
而留档在 `APP_DIR/sessions`(工作区围栏之外,`read_file`/`grep` 到不了,这是刻意的)。
于是同学问"上次那个报错我们怎么解决的",学长只能靠 MEMORY.md 里被显式记下的几行,
整段对话记忆为零。这份文件钉四件事:

1. 工具在注册表里、只读、`trust=local`,且**永远不进网页版白名单**;
2. 输出不下发绝对路径,只出 user/assistant 正文(工具往返不算命中);
3. 片段窗口可以为模型放宽,但侧栏那套默认口径一个字节都不能漂;
4. 没查到就照实说没查到,不给"编一段上次说过"留余地。
"""
import json
from pathlib import Path

import pytest

from minicode import session as session_mod
from minicode.tools.registry import build_default_registry
from minicode.web_profile import EXCLUDED_TOOLS, PUBLIC_TOOLS


def _write(logs: Path, name: str, rows) -> Path:
    logs.mkdir(exist_ok=True)
    (logs / name).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    return logs / name


LONG_ANSWER = ("根因是你那个 venv 没激活,连装三次 pip 都装到全局去了,"
               "后来我们改成 python -m pip 就好了。" * 6)


@pytest.fixture
def logs(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    _write(d, "session_20260917_2130.jsonl", [
        {"type": "session_start", "time": "2026-09-17 21:30:11",
         "workspace": str(tmp_path / "ws-demo")},
        {"role": "user", "content": "三体这本书在图书馆哪里"},
        {"role": "tool", "content": "三体 venv —— 工具原始结果,不该被搜到"},
        {"role": "assistant", "content": LONG_ANSWER},
    ])
    _write(d, "session_20260918_0900.jsonl", [
        {"type": "session_start", "time": "2026-09-18 09:00:00", "workspace": ""},
        {"role": "user", "content": "今天天气怎么样"},
    ])
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", d)
    return d


# ---------------------------------------------------------------- 注册与边界

def test_tool_is_registered_read_only_and_local():
    tool = next(t for t in build_default_registry() if t.name == "search_history")
    assert tool.read_only is True
    # 留档里只会有用户说过的话与助手的回话(工具往返不进结果),所以是本机内容不围栏
    assert tool.trust == "local"


def test_the_web_visitor_never_gets_it():
    assert "search_history" not in PUBLIC_TOOLS
    assert "search_history" in EXCLUDED_TOOLS, "访客能翻主人全部历史对话,必须写清排除理由"


def test_every_desktop_tool_takes_a_side_for_the_web():
    """新增工具必须**表态**:要么进白名单,要么在排除表里写清为什么。

    漏了不会有任何测试红(网页版是"不在白名单就不给"的默认拒绝),但那个工具就
    从没被谁想过一遍"访客能不能用"。这条把沉默变成一次必须做的选择。
    """
    names = {t.name for t in build_default_registry()} | {"task"}
    undecided = names - set(PUBLIC_TOOLS) - set(EXCLUDED_TOOLS)
    assert not undecided, f"这些工具没在 web_profile 里表态: {sorted(undecided)}"


# ---------------------------------------------------------------- 取数口径

def test_history_hits_label_the_hit_without_any_path(logs, tmp_path):
    rows = session_mod.history_hits("三体", top=3)
    assert len(rows) == 1
    row = rows[0]
    assert row["when"] == "2026-09-17 21:30"
    assert row["workspace"] == "ws-demo"          # 只给目录名,不给路径
    assert row["hits"] == 1                       # 工具结果那行含"三体"也不算
    assert "图书馆" in row["snippet"]              # 片段以第一处命中为中心
    assert "path" not in row, "结果里不该带绝对路径"
    assert str(tmp_path) not in json.dumps(row, ensure_ascii=False)


def test_tool_output_carries_the_snippet_and_the_ruler_line(logs, tmp_path):
    out = session_mod.search_history(None, "venv")
    assert "2026-09-17 21:30" in out
    assert "python -m pip" in out, "放宽的窗口要真把上下文带出来,不然学长还是答不上"
    assert "口径" in out and "/resume" in out
    assert str(logs) not in out and str(tmp_path) not in out


def test_empty_query_asks_for_a_keyword(logs):
    assert "关键词" in session_mod.search_history(None, "   ")
    assert session_mod.history_hits("") == []


def test_no_hit_says_so_and_shuts_down_invention(logs):
    out = session_mod.search_history(None, "根本没人提过的词")
    assert "没有提到" in out
    assert "别编" in out, "没查到的那句要顺手挡住「我们上次说过」式的编造"


def test_limit_is_capped(logs):
    assert len(session_mod.history_hits("三体", top=99)) <= session_mod.HISTORY_TOP_LIMIT


# ---------------------------------------------------------------- 默认窗口不许漂

def test_default_snippet_window_is_unchanged(logs):
    """侧栏搜索框吃的是默认窗口(v0.9.39 起就是 80 字)。模型侧放宽不能顺手改掉它。"""
    narrow = session_mod.search_logs(logs, "venv")[0]["snippet"]
    wide = session_mod.search_logs(logs, "venv", snippet_chars=420)[0]["snippet"]
    assert len(narrow) <= 80 + len("venv")
    assert len(wide) > len(narrow)
