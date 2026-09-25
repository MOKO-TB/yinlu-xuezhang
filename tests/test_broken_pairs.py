"""崩溃/强关留下的"assistant 喊了工具、结果却没落盘"必须在恢复时修合法。

留档是逐条追加的,`add_assistant`(带 tool_calls)与逐条 `add_tool_result` **不在一个
事务里**:这两步之间断电、被强关、被 Windows 更新杀掉,恢复出来的上下文就带一条悬空
call。OpenAI 兼容端点对这种序列回的是 400,而且**往后每一轮都 400**(坏对儿一直躺在
messages 里),同学唯一的自救是 `/clear` 把整个会话清空 —— 对托盘常驻、天天被强关的
应用,这不是小概率事件。压缩(`replace_messages` 保头+尾)也能从中间切断一对。

这份文件钉的是:两种坏形状都管、修完是真合法(不是把断言改松)、原始留档不被偷偷改写。
"""
import json

from minicode.agent import Agent
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.session import Session, repair_tool_pairs
from minicode.tools.registry import build_default_registry


def _assistant(*call_ids, text=""):
    return {"role": "assistant", "content": text or None,
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "grep", "arguments": "{}"}}
                           for cid in call_ids]}


def _tool(call_id, content="结果"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def assert_protocol_legal(msgs):
    """逐条走一遍协议规矩:每个 tool_call 有且只有一条结果、结果不无主、
    而且必须在下一条 user/assistant 之前闭合。断言写成这样是为了"修好了"这件事
    不由被修的那段代码自己说了算。
    """
    pending = set()
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            assert m.get("tool_call_id") in pending, "出现了无主的工具结果"
            pending.discard(m["tool_call_id"])
            continue
        assert not pending, f"上一批工具没闭合就开口说别的:{sorted(pending)}"
        if role == "assistant":
            pending = {c["id"] for c in (m.get("tool_calls") or [])}
    assert not pending, "会话结尾还挂着没闭合的工具调用"


# ---------------------------------------------------------------- 纯函数

def test_dangling_calls_get_one_factual_result_each():
    msgs = [{"role": "user", "content": "查两件事"},
            _assistant("c1", "c2"),
            _tool("c1", "只跑完了第一条")]
    out, fixed = repair_tool_pairs(msgs)
    assert fixed == 1
    assert_protocol_legal(out)
    added = [m for m in out if m.get("tool_call_id") == "c2"]
    assert len(added) == 1 and "程序中断" in added[0]["content"]
    assert added[0]["content"], "补的那条要有话可看,不能是空串"


def test_orphan_tool_result_is_dropped_and_counted():
    msgs = [{"role": "user", "content": "x"}, _tool("ghost"),
            {"role": "assistant", "content": "好"}]
    out, fixed = repair_tool_pairs(msgs)
    assert fixed == 1 and not any(m.get("role") == "tool" for m in out)
    assert_protocol_legal(out)


def test_a_clean_history_is_left_alone():
    msgs = [{"role": "user", "content": "x"}, _assistant("c1"), _tool("c1"),
            {"role": "assistant", "content": "答完了"}]
    out, fixed = repair_tool_pairs(msgs)
    assert fixed == 0 and out == msgs, "没坏就别动:修好的判据不能是「永远补一条」"
    assert out is msgs, "没修时要交回**同一个列表对象** —— 每步请求都白复制一份长列表," \
                        "`test_feedback_recall` 那针「关开关后发出去的就是 session.messages 本身」也靠它"


def test_crash_at_the_very_end_still_closes_the_pair():
    out, fixed = repair_tool_pairs([_assistant("c1", "c2")])
    assert fixed == 2 and len(out) == 3
    assert_protocol_legal(out)


# ---------------------------------------------------------------- 恢复路径

def test_load_from_repairs_a_truncated_log_and_leaves_a_mark(tmp_path):
    log = tmp_path / "session_20260925_1010.jsonl"
    rows = [{"type": "session_start", "time": "2026-09-25 10:10:00", "workspace": ""},
            {"role": "user", "content": "帮我改两处在代码"},
            dict(_assistant("c1", "c2")),
            _tool("c1", "第一处改完了")]
    # 第二步还没落盘就被杀了 —— 这就是断电那一刻留在磁盘上的样子
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    s = Session(system_prompt="sys")
    n = s.load_from(log)

    assert n == 4 and len(s.messages) == 5      # 返回值不含 system 那条
    assert_protocol_legal(s.messages)
    mark = [json.loads(x) for x in
            log.read_text(encoding="utf-8").splitlines() if '"repaired"' in x]
    assert mark and mark[0]["changed"] == 1, "修了几条要在留档里看得见,不能静默"
    assert s.log_path == log, "恢复完还得继续往同一份留档写"


def test_repairing_does_not_touch_the_untouched_archive(tmp_path):
    """留档本身**不回写**:补记只发生在内存与请求里。磁盘那份是"当时真发生了什么"
    的原始记录,把它改写就等于抹掉"这里断过"这件事。"""
    log = tmp_path / "session_20260925_1111.jsonl"
    before = "\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"type": "session_start", "time": "2026-09-25 11:11:00", "workspace": ""},
        {"role": "user", "content": "跑一下"},
        dict(_assistant("c9")),
    ]) + "\n"
    log.write_text(before, encoding="utf-8")
    s = Session(system_prompt="sys")
    s.load_from(log)
    body = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [r for r in body if r.get("role") == "tool"] == [], "不该往原始记录里塞结果"


# ---------------------------------------------------------------- 贴着出口那道闸

class _LLM:
    """只记录每次请求真正发出去的消息列表。"""

    def __init__(self):
        self.sent = []

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None, on_retry=None):
        self.sent.append([dict(m) for m in messages])
        return AssistantTurn(text="好")


class _UI:
    def __getattr__(self, name):        # 这场只关心发出去的消息,其它回调一律吞掉
        return lambda *a, **k: None


def _agent(session, llm, config, ctx):
    return Agent(llm=llm, registry=build_default_registry(), session=session,
                 gate=PermissionGate(config), ui=_UI(), ctx=ctx)


def test_request_is_never_sent_with_a_dangling_pair(config, ctx):
    """压缩把一对 call/result 从中间切断时,不需要等到崩溃:出口这遍兜底要拦住。"""
    s = Session(system_prompt="sys")
    s.messages = [{"role": "user", "content": "q"}, _assistant("c1", "c2")]
    agent = _agent(s, _LLM(), config, ctx)

    assert_protocol_legal(agent._messages_for_request())
    assert len(s.messages) == 2, "兜底只影响这一次请求,不该反过来污染会话上下文"


def test_handler_that_raises_still_records_a_result(config, ctx, monkeypatch):
    """`_execute` 只兜住了参数与未知工具两类错;handler 自己抛(OSError 之类)过去会
    把整轮打断并留下悬空 call —— 从这一轮起每句话都 400。"""
    s = Session(system_prompt="sys")
    agent = _agent(s, _LLM(), config, ctx)

    def boom(call):
        raise OSError("磁盘没了")

    agent._execute = boom
    steps = [AssistantTurn(tool_calls=[ToolCall(id="t1", name="grep", arguments="{}")]),
             AssistantTurn(text="收到,我换个法子")]
    monkeypatch.setattr(agent.llm, "stream_chat", lambda *a, **k: steps.pop(0))

    agent.run_turn("帮我搜一下")
    roles = [m.get("role") for m in s.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert "OSError" in s.messages[3]["content"], "错误原文要进结果,模型才知道该改道"
    assert_protocol_legal(s.messages)
