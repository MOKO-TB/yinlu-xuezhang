
from minicode.compaction import compact, estimate_tokens
from minicode.llm import AssistantTurn


class FakeSummarizer:
    def __init__(self, summary="这是摘要"):
        self.summary = summary
        self.prompts = []

    def stream_chat(self, messages, tools, **kwargs):
        self.prompts.append(messages[0]["content"])
        return AssistantTurn(text=self.summary)


def _user_text_turn(text, call_id=None, tool_name=None):
    if call_id:
        return [
            {"role": "user", "content": text},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": call_id, "type": "function",
                             "function": {"name": tool_name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": call_id, "content": "结果"},
        ]
    return [{"role": "user", "content": text},
            {"role": "assistant", "content": "回复"}]


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens([{"role": "user", "content": "abcd"}]) > 0
    # 同样 4 个"字符",中文按 1 token/字,英文按 4 字符/token
    cjk = estimate_tokens([{"role": "user", "content": "中文中文"}])
    ascii_ = estimate_tokens([{"role": "user", "content": "abcd"}])
    assert cjk > ascii_


def test_compact_folds_older_turns():
    msgs = [{"role": "system", "content": "sys"}]
    msgs += _user_text_turn("任务一")
    msgs += _user_text_turn("任务二")
    msgs += _user_text_turn("任务三")
    fake = FakeSummarizer(summary="早期任务摘要")

    new_msgs, before = compact(msgs, fake)
    assert before == len(msgs)
    assert len(new_msgs) < len(msgs)
    assert new_msgs[0]["role"] == "system"
    # 摘要作为 user 消息插入,包含摘要模型输出
    assert new_msgs[1]["role"] == "user"
    assert "早期任务摘要" in new_msgs[1]["content"]
    # 最近两轮原文保留
    assert any(m.get("content") == "任务二" for m in new_msgs)
    assert any(m.get("content") == "任务三" for m in new_msgs)
    assert not any(m.get("content") == "任务一" for m in new_msgs)
    # 摘要请求里包含被折叠的历史
    assert "任务一" in fake.prompts[0]


def test_compact_keeps_tool_pairing_intact():
    msgs = [{"role": "system", "content": "sys"}]
    msgs += _user_text_turn("任务一")
    msgs += _user_text_turn("任务二", call_id="c1", tool_name="bash")
    msgs += _user_text_turn("任务三", call_id="c2", tool_name="bash")
    new_msgs, _ = compact(msgs, FakeSummarizer())

    # 保留部分中,每个带 tool_calls 的 assistant 后面必须紧跟对应 tool 结果
    for i, m in enumerate(new_msgs):
        if m["role"] == "assistant" and m.get("tool_calls"):
            nxt = new_msgs[i + 1]
            assert nxt["role"] == "tool"
            assert nxt["tool_call_id"] == m["tool_calls"][0]["id"]
    # 不能出现孤儿 tool 结果
    for i, m in enumerate(new_msgs):
        if m["role"] == "tool":
            assert new_msgs[i - 1]["role"] == "assistant"


def test_compact_noop_when_few_turns():
    msgs = [{"role": "system", "content": "sys"}, *_user_text_turn("只有一轮")]
    fake = FakeSummarizer()
    new_msgs, before = compact(msgs, fake)
    assert new_msgs == msgs
    assert before == len(msgs)
    assert not fake.prompts  # 没有调用摘要模型
