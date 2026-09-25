
from minicode.compaction import (
    HISTORY_CHAR_CAP, HISTORY_ELLIPSIS, HISTORY_HEAD_CAP, HISTORY_TAIL_CAP,
    SUMMARY_PROMPT, clip_history, compact, estimate_tokens,
)
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


# ------------------------------------------------- v0.9.40 D1:超预算时保头也保尾

def _long_history(turns: int = 60) -> list[dict]:
    """够长的对话:每轮正文都超过 `_render` 的单条上限,总长远超 40000 字预算。"""
    pad = "节" * 900
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "任务目标:把这学期每门课的学分统计出来"}]
    for i in range(turns):
        msgs.append({"role": "assistant", "content": f"第{i}步 {pad}"})
        msgs.append({"role": "user", "content": f"追问{i} {pad}"})
    return msgs


def test_head_and_tail_caps_add_up_to_the_budget():
    """两个数由总预算推出:改了头,尾自己跟着变,预算不会悄悄变大。"""
    assert HISTORY_HEAD_CAP + HISTORY_TAIL_CAP + len(HISTORY_ELLIPSIS) == HISTORY_CHAR_CAP


def test_clip_history_leaves_short_input_untouched():
    text = "一" * (HISTORY_CHAR_CAP - 1)
    assert clip_history(text) == text
    assert HISTORY_ELLIPSIS not in clip_history(text)


def test_clip_history_keeps_both_ends_within_budget():
    text = "".join(f"{i % 10}" for i in range(HISTORY_CHAR_CAP * 2))
    out = clip_history(text)
    assert len(out) <= HISTORY_CHAR_CAP
    assert out.startswith(text[:HISTORY_HEAD_CAP])
    assert out.endswith(text[-HISTORY_TAIL_CAP:])
    assert HISTORY_ELLIPSIS in out
    # 中间真的少了东西:不是"塞得下",是"两头都留着"
    assert len(out) < len(text)


def test_compact_sends_the_task_goal_even_when_history_is_huge():
    """这条就是本轮改动的全部理由:任务目标在开头,只截尾会把它丢掉。"""
    fake = FakeSummarizer()
    compact(_long_history(), fake)
    assert len(fake.prompts) == 1
    prompt = fake.prompts[0]
    assert "任务目标:把这学期每门课的学分统计出来" in prompt
    assert HISTORY_ELLIPSIS in prompt


def test_compact_still_sends_the_latest_older_turn():
    """尾部也得在 —— 摘要模型要看得见"最近做到哪一步",否则接着做会重复劳动。"""
    msgs = _long_history(turns=60)
    fake = FakeSummarizer()
    compact(msgs, fake)
    prompt = fake.prompts[0]
    older_last = "追问57"        # 58、59 两轮属于 keep_recent,不进摘要
    recent_first = "追问58"
    assert older_last in prompt
    assert recent_first not in prompt


def test_compact_small_history_has_no_ellipsis():
    fake = FakeSummarizer()
    compact([{"role": "system", "content": "sys"},
             *_user_text_turn("一"), *_user_text_turn("二"),
             *_user_text_turn("三")], fake)
    assert HISTORY_ELLIPSIS not in fake.prompts[0]


def test_summary_prompt_keeps_what_a_campus_agent_must_not_lose():
    """保留清单钉住:日期/课名/地点/待办/来源网址是这位同学的命根子,漏一项就是丢东西。"""
    for needle in ("任务目标", "文件路径", "日期", "课名", "地点",
                   "待办", "来源网址", "关键决定", "未完成"):
        assert needle in SUMMARY_PROMPT
    assert "400 字" in SUMMARY_PROMPT          # 字数上限没被顺手改掉


def test_summary_prompt_does_not_claim_the_agent_is_only_a_coder():
    """旧文案自称「编码助手对话历史」,于是摘要里只剩文件路径、课表和网址全丢。"""
    assert "编码助手" not in SUMMARY_PROMPT


def test_summary_prompt_forbids_inventing_missing_items():
    assert "不要编" in SUMMARY_PROMPT
