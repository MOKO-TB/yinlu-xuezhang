"""流式增量攒批:一次 JS 往返搬一批,而不是一个 token 一次。

钉的是 2026-10-09 量出来的那个数(`scripts/e2e_ui_lag.py`):600 条 text_delta
一条一次 `evaluate_js` 共 1767 ms,同样的内容放进一次调用里循环只要 6.6 ms
—— **往返本身占 100%**。而这一趟是同步的,它把读流那条线程一起拖住,
表现就是用户报的"使用中有明显的卡顿感"。

全部走真入口 `GuiUI.push`,替身只放在最外面的窗口上(数 evaluate_js 调了几次、
送出去的报文是什么)。
"""
from __future__ import annotations

import json
import time

from minicode.gui_ui import FLUSH_CHARS, FLUSH_MS, GuiUI

N = 600
PIECE = "这一项要注意的地方不少。"


class FakeWindow:
    def __init__(self):
        self.calls: list[str] = []

    def evaluate_js(self, script: str):
        self.calls.append(script)
        return None


def _payload(script: str):
    """从 `window.__gcodeEvent(s)(…)` 里把报文取回来(顺带钉住用的是哪一只)。"""
    head = "window.__gcodeEvents(" if script.startswith("window.__gcodeEvents(") \
        else "window.__gcodeEvent("
    assert script.startswith(head), script[:40]
    assert script.endswith(")"), script[-40:]
    return json.loads(script[len(head):-1])


def _ui():
    ui = GuiUI()
    w = FakeWindow()
    ui.attach(w)
    return ui, w


def _drain(w: FakeWindow) -> list[dict]:
    """把这一场送出去的所有事件按到达顺序摊平。"""
    out: list[dict] = []
    for call in w.calls:
        got = _payload(call)
        out.extend(got if isinstance(got, list) else [got])
    return out


def test_600_deltas_do_not_cost_600_round_trips():
    """往返次数是这一轮真正要钉的数:600 条必须远少于一发一次。"""
    ui, w = _ui()
    for _ in range(N):
        ui.push("text_delta", text=PIECE)
    ui.flush()
    # 只按字数触发(测试里不能让真时间走),所以是 600×13 字 / 400 字一批
    assert len(w.calls) < N / 5, f"往返 {len(w.calls)} 次,批处理等于没做"
    events = _drain(w)
    joined = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert joined == PIECE * N, "一个字都不许多,也不许少、不许换顺序"


def test_adjacent_same_type_merge_into_one_event():
    """相邻同类型的增量合成一条:屏幕上这一截正文本来就是一起到达的。"""
    ui, w = _ui()
    for _ in range(20):
        ui.push("text_delta", text="哈")
    ui.flush()
    events = _drain(w)
    assert len(events) == 1 and events[0]["type"] == "text_delta"
    assert events[0]["text"] == "哈" * 20


def test_a_non_delta_event_flushes_first_so_order_survives():
    """工具卡不许插在正文中间:攒着的增量必须先落地。"""
    ui, w = _ui()
    ui.push("text_delta", text="前半句")
    ui.push("text_delta", text="，")
    ui.push("tool_start", name="read_file", summary="a.py")
    ui.push("text_delta", text="后半句")
    ui.flush()
    kinds = [e["type"] for e in _drain(w)]
    assert kinds == ["text_delta", "tool_start", "text_delta"], kinds
    texts = [e.get("text") or e.get("name") for e in _drain(w)]
    assert texts == ["前半句，", "read_file", "后半句"], texts


def test_reasoning_and_text_keep_their_relative_order():
    """思考与正文交替时不许互相超车(它们是两种事件,不能合成一条)。"""
    ui, w = _ui()
    ui.push("reasoning_delta", text="想")
    ui.push("text_delta", text="答")
    ui.push("reasoning_delta", text="再想")
    ui.flush()
    assert [e["type"] for e in _drain(w)] == [
        "reasoning_delta", "text_delta", "reasoning_delta"]


def test_a_big_chunk_goes_out_without_waiting_for_the_clock():
    """一次吐得多的大段立刻送:让字等满 80ms 才上屏,就是拿省往返换回一次卡顿。"""
    ui, w = _ui()
    ui.push("text_delta", text="字" * (FLUSH_CHARS + 1))
    assert len(w.calls) == 1, "攒够字数就该当场送,不该等下一次 push 来冲"
    assert _payload(w.calls[0])[0]["type"] == "text_delta"


def test_a_lone_delta_lands_on_its_own_clock():
    """吐一句就停手的那一种:没有"下一次 push"来冲它,定时器必须自己到点送出去。

    这一条是攒批最容易写坏的地方 —— 只靠 push 驱动去冲缓冲,表现是"模型想三秒,
    屏上就静三秒",省下来的往返反而变成新的卡顿。
    """
    ui, w = _ui()
    ui.push("text_delta", text="一小截")
    assert w.calls == [], "一小截就送一发的话,批处理就白做了"
    time.sleep(FLUSH_MS / 1000.0 + 0.15)
    assert len(w.calls) == 1, "到点没送:缓冲区成了黑洞"
    assert _payload(w.calls[0])[0]["text"] == "一小截"
    assert ui._timer is None and ui._pending == []


def test_the_timer_does_not_fire_twice_for_one_batch():
    """冲完就销表:留下一只到点空跑的定时器,等于每个回合都漏一个计时器。"""
    ui, w = _ui()
    ui.push("text_delta", text="一句")
    ui.flush()
    n = len(w.calls)
    time.sleep(FLUSH_MS / 1000.0 + 0.15)
    assert len(w.calls) == n, "定时器没销掉, flushed 之后又送了一次"


def test_stop_flushes_the_last_half_sentence():
    """按停止时最后那半句不许留在缓冲区里 —— 中断之后再也没有下一次 push。"""
    ui, w = _ui()
    ui.push("text_delta", text="说到一半")
    ui.stop_requested = True
    try:
        ui._check_stop()
    except KeyboardInterrupt:
        pass
    assert "".join(e.get("text", "") for e in _drain(w)) == "说到一半"


def test_no_window_drops_instead_of_growing_forever():
    """窗口没就绪时旧口径是丢弃;攒批之后不许变成"越攒越大的内存洞"。"""
    ui = GuiUI()
    for _ in range(200):
        ui.push("text_delta", text=PIECE)
    assert ui._pending_chars <= FLUSH_CHARS, \
        f"缓冲区到 {ui._pending_chars} 字还没冲:没窗口时就该有个上限"
    ui.flush()
    assert ui._pending == [] and ui._pending_chars == 0


def test_single_non_delta_payload_is_unchanged():
    """对照:非增量那一路的报文形状与今天逐字一样(旧契约不许被顺手改掉)。"""
    ui, w = _ui()
    ui.push("busy", value=True)
    assert w.calls == ['window.__gcodeEvent({"value": true, "type": "busy"})'], w.calls


def test_chinese_is_not_escaped():
    """`ensure_ascii=False` 还在:转成 \\uXXXX 等于把中文塞进每一趟往返。"""
    ui, w = _ui()
    ui.push("info", text="引路学长")
    assert "引路学长" in w.calls[0]
