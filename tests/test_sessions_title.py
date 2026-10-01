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
    evs = [e for e in window.events if e["type"] == "sessions"]
    assert evs, "会话清单事件没推过"
    items = evs[-1]["items"]
    assert items, "清单为空"
    row = items[0]
    assert row["title"].startswith("帮我写一份蓝桥杯"), row
    assert row["last_user_text"].startswith("帮我写一份蓝桥杯")
