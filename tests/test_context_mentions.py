"""智能上下文实体提及 (@mentions) 与桥接口测试 (v0.9.168, 对标 HANDOFF §48(c))。"""
from unittest.mock import MagicMock

from minicode.gui_bridge import Bridge, PublicApi


def test_get_context_mentions_all(tmp_path):
    bridge = Bridge(config=MagicMock(), workspace=tmp_path, ui=MagicMock())
    res = bridge.get_context_mentions("")
    assert res["ok"] is True
    mentions = res["mentions"]
    ids = [m["id"] for m in mentions]
    # 验证核心实体
    assert "plan" in ids
    assert "todo" in ids
    assert "schedule" in ids
    assert "git" in ids
    assert "wiki" in ids
    assert "test" in ids
    assert "problems" in ids


def test_get_context_mentions_filtered(tmp_path):
    bridge = Bridge(config=MagicMock(), workspace=tmp_path, ui=MagicMock())
    # 按标签/前缀搜索
    res = bridge.get_context_mentions("plan")
    assert res["ok"] is True
    assert len(res["mentions"]) >= 1
    assert res["mentions"][0]["id"] == "plan"
    assert res["mentions"][0]["label"] == "@plan"

    # 按中文描述/名称搜索
    res2 = bridge.get_context_mentions("课表")
    assert res2["ok"] is True
    assert any(m["id"] == "schedule" for m in res2["mentions"])


def test_public_facade_forwards_mentions_and_hierarchy(tmp_path):
    (tmp_path / "sample.py").write_text("def target(): pass\ndef caller(): target()\n", encoding="utf-8")
    bridge = Bridge(config=MagicMock(), workspace=tmp_path, ui=MagicMock())
    api = PublicApi(bridge)
    # 验证门面转发
    m_res = api.get_context_mentions()
    assert m_res["ok"] is True
    assert len(m_res["mentions"]) >= 7

    h_res = api.call_hierarchy("target", direction="incoming", depth=1)
    assert h_res["ok"] is True
    assert "data" in h_res
    assert "tree" in h_res
