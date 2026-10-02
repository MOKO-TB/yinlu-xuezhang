"""会话自动起名的**接线**测试(v0.9.98):_push_sessions 的 payload 必须带
title 字段。单独成文件 —— test_gui_bridge.py 此刻有并行会话的未提交改动,
不往那一 file 里加内容(规矩:不碰别人正在做的东西)。"""

from minicode.llm import AssistantTurn
from test_gui_bridge import FakeLLM, make, wait_until


def test_sessions_payload_carries_title(config, tmp_path):
    llm = FakeLLM([AssistantTurn(text="收到,材料我列个清单")])
    bridge, window, _session = make(config, tmp_path, llm)
    bridge.send_task("帮我写一份蓝桥杯报名材料,越快越好")
    assert wait_until(lambda: not bridge._busy)
    # v0.9.104:完成的 finally 里先 busy=False 再推 sessions —— 两者之间有竞态,
    # 等到「带载荷的那次推送」出现,别假设 busy 清零时它已经在
    assert wait_until(lambda: any(
        e["type"] == "sessions" and e.get("items") for e in window.events)),         "带载荷的会话清单事件没推过"
    evs = [e for e in window.events if e["type"] == "sessions"]
    items = [e for e in evs if e.get("items")][-1]["items"]
    assert items
    row = items[0]
    assert row["title"].startswith("帮我写一份蓝桥杯"), row
    assert row["last_user_text"].startswith("帮我写一份蓝桥杯")
