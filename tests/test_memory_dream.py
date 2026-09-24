"""长期记忆进阶测试:用户级(跨项目)作用域、去重归并、Dream 整理。"""
import json

import pytest

from minicode import config as config_mod
from minicode.memory import (
    MemoryEntry, dedupe_entries, dream, dream_all, global_memory_path,
    is_same_memory, load_entries, load_from, memory, memory_path,
    prompt_section, save_to, similarity,
)
from minicode.tools.base import ToolContext


@pytest.fixture
def cred(tmp_path, monkeypatch):
    """把用户级数据目录指到临时目录,避免写真实主目录。"""
    d = tmp_path / "home" / ".yinluxs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH", d / "credentials.json")
    return d


def _ctx(tmp_path, llm=None):
    ctx = ToolContext(workspace=tmp_path, config=None)
    ctx.llm = llm
    return ctx


class FakeDreamLLM:
    """把记忆压成一条的假模型。"""

    def __init__(self, out): self.out = out
    def complete(self, system, user, max_tokens=None): return self.out


class EchoDreamLLM:
    """把收到的条目原样返回(模拟"只做轻微整理"的模型),用于分作用域断言。"""

    def complete(self, system, user, max_tokens=None):
        payload = user.split("现有条目:", 1)[1].strip()
        return json.dumps({"entries": json.loads(payload)}, ensure_ascii=False)


def _entry_line(date, cat, text):
    return {"date": date, "category": cat, "content": text}


# ------------------------------------------------------------------ 作用域

def test_global_path_follows_credentials_dir(tmp_path, cred):
    assert global_memory_path() == cred / "memory" / "MEMORY.md"
    assert memory_path(tmp_path) == tmp_path / "memory" / "MEMORY.md"


def test_user_scope_writes_to_global_file(tmp_path, cred):
    ctx = _ctx(tmp_path)
    r = memory(ctx, "save", category="user", content="平时用中文交流", scope="user")
    assert "已记住[用户级]" in r
    assert load_from(global_memory_path())[0].content == "平时用中文交流"
    assert load_entries(tmp_path) == []          # 没有污染项目级

    r2 = memory(ctx, "save", category="project", content="本项目用 pytest")
    assert "用户级" not in r2
    assert load_entries(tmp_path)[0].content == "本项目用 pytest"


def test_scopes_are_isolated_in_list_search_forget(tmp_path, cred):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="user", content="喜欢先给结论", scope="user")
    memory(ctx, "save", category="project", content="部署走 Docker")

    assert "喜欢先给结论" in memory(ctx, "list", scope="user")
    assert "部署走 Docker" not in memory(ctx, "list", scope="user")
    assert "喜欢先给结论" not in memory(ctx, "list")

    assert "命中 1 条" in memory(ctx, "search", query="结论", scope="user")
    assert "没有与" in memory(ctx, "search", query="结论")

    assert "已忘记(用户级) 1 条" in memory(ctx, "forget", query="结论", scope="user")
    assert load_from(global_memory_path()) == []


def test_unknown_scope_rejected(tmp_path, cred):
    ctx = _ctx(tmp_path)
    assert "未知作用域" in memory(ctx, "save", category="user", content="x", scope="galaxy")
    assert "未知作用域" in memory(ctx, "list", scope="galaxy")


def test_prompt_section_includes_both_scopes(tmp_path, cred):
    save_to(global_memory_path(), [MemoryEntry("2026-01-01", "user", "跨项目偏好: 用中文")])
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-02", "project", "本项目约定")])
    text = prompt_section(tmp_path)
    assert "用户级(跨项目通用)" in text and "跨项目偏好" in text
    assert "本项目" in text and "本项目约定" in text


# ------------------------------------------------------------------ 去重归并

def test_is_same_memory_handles_subset_and_distinct():
    assert is_same_memory("用户喜欢简短结论", "用户喜欢简短结论,先给答案")   # 子集也算同一条
    assert is_same_memory("部署走 Docker", "部署走 Docker")                 # 完全相同
    assert not is_same_memory("部署走 Docker", "文档放 docs 目录")
    assert similarity("a", "") == 0.0


def test_save_updates_instead_of_piling_up(tmp_path, cred):
    ctx = _ctx(tmp_path)
    memory(ctx, "save", category="user", content="喜欢简短结论")
    r = memory(ctx, "save", category="user", content="喜欢简短结论,先给答案再给理由")
    assert "已更新已有记忆" in r
    entries = load_entries(tmp_path)
    assert len(entries) == 1
    assert "先给答案再给理由" in entries[0].content


def test_dedupe_merges_and_drops_duplicates():
    entries = [
        MemoryEntry("2026-01-01", "project", "部署走 Docker"),
        MemoryEntry("2026-02-01", "project", "部署走 Docker 加 compose"),
        MemoryEntry("2026-03-01", "project", "部署走 Docker"),
        MemoryEntry("2026-01-01", "user", "部署走 Docker"),          # 类别不同,不合并
        MemoryEntry("2026-01-05", "project", "文档放 docs 目录"),
    ]
    kept, notes = dedupe_entries(entries)
    contents = [(e.category, e.content) for e in kept]
    assert ("project", "部署走 Docker 加 compose") in contents
    assert ("user", "部署走 Docker") in contents
    assert ("project", "文档放 docs 目录") in contents
    assert len(kept) == 3
    assert any("合并同类" in n for n in notes)
    assert any("删除重复条目" in n for n in notes)


# ------------------------------------------------------------------ Dream

def test_dream_without_llm_only_dedupes(tmp_path, cred):
    save_to(memory_path(tmp_path), [
        MemoryEntry("2026-01-01", "project", "部署走 Docker"),
        MemoryEntry("2026-02-01", "project", "部署走 Docker"),
    ])
    out = dream(tmp_path, "project", None)
    assert "整理完成(确定性去重)" in out
    assert "2 条 → 1 条" in out
    assert len(load_entries(tmp_path)) == 1


def test_dream_with_llm_rewrites_and_backs_up(tmp_path, cred):
    p = memory_path(tmp_path)
    save_to(p, [
        MemoryEntry("2026-01-01", "project", "部署走 Docker"),
        MemoryEntry("2026-02-01", "user", "用户偏好简短回复"),
    ])
    payload = {"entries": [_entry_line("2026-02-01", "project", "部署: Docker + compose"),
                           _entry_line("2026-02-01", "user", "偏好简短回复")]}
    out = dream(tmp_path, "project", FakeDreamLLM(json.dumps(payload, ensure_ascii=False)))
    assert "模型归纳" in out
    entries = load_entries(tmp_path)
    assert [e.content for e in entries] == ["部署: Docker + compose", "偏好简短回复"]
    backup = p.parent / "history"
    assert backup.is_dir() and list(backup.glob("MEMORY-*.md"))     # 原文件已备份


def test_dream_survives_broken_llm_output(tmp_path, cred):
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-01", "project", "部署走 Docker")])
    out = dream(tmp_path, "project", FakeDreamLLM("模型今天不想输出 JSON"))
    assert "仅做了去重" in out or "确定性去重" in out
    assert load_entries(tmp_path)[0].content == "部署走 Docker"      # 内容没丢


def test_dream_ignores_empty_model_result(tmp_path, cred):
    """模型返回空结果时忽略它、保留原文——绝不能把记忆整理没了。"""
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-01", "project", "部署走 Docker")])
    out = dream(tmp_path, "project", FakeDreamLLM('{"entries": []}'))
    assert "模型返回空结果" in out
    assert len(load_entries(tmp_path)) == 1
    assert load_entries(tmp_path)[0].content == "部署走 Docker"


def test_dream_on_empty_is_friendly(tmp_path, cred):
    assert "还没有记忆" in dream(tmp_path, "project", None)


def test_dream_all_covers_both_scopes(tmp_path, cred):
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-01", "project", "项目约定 A")])
    save_to(global_memory_path(), [MemoryEntry("2026-01-01", "user", "跨项目偏好 B")])
    out = dream_all(tmp_path, None)
    assert "本项目" in out and "用户级" in out


def test_memory_tool_dream_action(tmp_path, cred):
    ctx = _ctx(tmp_path, EchoDreamLLM())
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-01", "project", "项目约定 A")])
    save_to(global_memory_path(), [MemoryEntry("2026-01-01", "user", "跨项目偏好 B")])

    out = memory(ctx, "dream", scope="all")
    assert "本项目" in out and "用户级" in out
    assert "整理完成" in out
    # 两级各自整理,互不串味
    assert load_entries(tmp_path)[0].content == "项目约定 A"
    assert load_from(global_memory_path())[0].content == "跨项目偏好 B"


def test_memory_tool_unknown_action_lists_dream(tmp_path, cred):
    assert "dream" in memory(_ctx(tmp_path), "nope")


# ------------------------------------------------------------------ LLMClient.complete

def test_llm_complete_returns_message_content(config):
    from minicode.llm import LLMClient

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Msg: content = "整理结果"
            class Choice: message = Msg()
            class Resp: choices = [Choice()]
            return Resp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        chat = FakeChat()

    client = LLMClient(config)
    client._client = FakeOpenAI()
    assert client.complete("sys", "user", max_tokens=128) == "整理结果"
    assert captured["messages"][0]["role"] == "system"
    assert captured["max_tokens"] == 128
    assert "tools" not in captured          # 内部补全不带工具


# ------------------------------------------------------------------ 桥接层

def test_bridge_consolidate_memory_and_counts(config, tmp_path, cred):
    """GUI 的「🧠 记忆」按钮走的就是这个接口:整理两级 + 回报条数。"""
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make

    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    save_to(memory_path(tmp_path), [MemoryEntry("2026-01-01", "project", "项目约定 A"),
                                    MemoryEntry("2026-01-02", "project", "项目约定 A")])
    save_to(global_memory_path(), [MemoryEntry("2026-01-01", "user", "跨项目偏好 B")])

    r = bridge.consolidate_memory("all")
    assert r["ok"] is True
    assert r["project"] == 1 and r["user"] == 1          # 项目级重复项被合并
    assert "整理完成" in r["msg"]

    info = bridge.get_info()
    assert info["memory_count"] == 1 and info["memory_user_count"] == 1


def test_bridge_consolidate_single_scope(config, tmp_path, cred):
    from minicode.llm import AssistantTurn
    from tests.test_gui_bridge import FakeLLM, make

    bridge, _, _ = make(config, tmp_path, FakeLLM([AssistantTurn(text="x")]))
    save_to(global_memory_path(), [MemoryEntry("2026-01-01", "user", "用户级 A"),
                                   MemoryEntry("2026-01-02", "user", "用户级 A")])
    r = bridge.consolidate_memory("user")
    assert r["ok"] and r["user"] == 1
    assert "用户级" in r["msg"] and "本项目" not in r["msg"]
