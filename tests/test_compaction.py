
import json  # noqa: E402

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


# --------------------------------------------------------------- microcompact
# 这一档管的是"很久以前某次只读查询的整屏结果,还要不要每轮重发"。
# 三条硬规矩各有一支针:只读且成功的才动、留档一个字不改、量小就不破前缀。

from minicode.compaction import (  # noqa: E402
    MICRO_FLOOR_TOKENS, MICRO_KEEP_RECENT, MICRO_MIN_CHARS, micro_placeholder,
    microcompact, tool_names_by_call)

LONG = "命中行\n" * 4000          # 约 2 万字,稳过 floor
def _pair(i, name="grep", content=LONG, extra=None):
    cid = f"c{i}"
    return [{"role": "assistant", "content": None,
             "tool_calls": [{"id": cid, "type": "function",
                             "function": {"name": name, "arguments": "{}"}}]},
            dict([("role", "tool"), ("tool_call_id", cid), ("content", content)]
                 + (extra or []))]


def _msgs(n, **kw):
    out = [{"role": "user", "content": "问"}]
    for i in range(n):
        out += _pair(i, **kw)
    return out


def test_nothing_eligible_returns_the_same_object():
    """没到动手的条件时必须**原样返回**(同一个对象),不是"改了一份一样的"。

    这一步是 KV Cache 的前提:每轮新建 dict 会让上游任何按 id 比较的优化全废。
    """
    msgs = _msgs(2)
    out, cleared, saved = microcompact(msgs, {"grep"})
    assert out is msgs and cleared == 0 and saved == 0


def test_write_results_are_never_touched():
    msgs = _msgs(12, name="write_file")
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert cleared == 0
    assert all(len(m["content"]) == len(LONG) for m in out if m["role"] == "tool")


def test_failed_read_only_result_stays():
    """失败那条是模型下一步的依据 —— 清了它就会拿同一份参数再撞一次墙。"""
    msgs = _msgs(12, name="grep", content="错误: 路径不存在\n" + LONG)
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert cleared == 0, "is_failure 判出来的那条不该进可清名单"


def test_recent_results_stay_verbatim():
    msgs = _msgs(MICRO_KEEP_RECENT + 3, name="grep")
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert cleared == 3
    tail = [m for m in out if m["role"] == "tool"][-MICRO_KEEP_RECENT:]
    assert all(len(m["content"]) == len(LONG) for m in tail)


def test_short_results_are_left_alone():
    """省几十个字却打断一次前缀是亏本买卖,所以有 `MICRO_MIN_CHARS` 这道门槛。"""
    msgs = _msgs(12, name="grep", content="x" * (MICRO_MIN_CHARS - 1))
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert out is msgs and cleared == 0


def test_below_the_floor_does_not_break_the_prefix():
    """够不上 floor 的整批一起不动:宁可少省,不要每一步都换一批(前缀就永远不稳)。"""
    just_enough = "y" * (MICRO_FLOOR_TOKENS * 4 // 3)
    msgs = _msgs(MICRO_KEEP_RECENT + 1, name="grep", content=just_enough)
    out, cleared, saved = microcompact(msgs, {"grep"})
    assert saved // 4 < MICRO_FLOOR_TOKENS or cleared == 0


def test_subagent_prefix_maps_back_to_the_builtin_name():
    """子代理的工具叫 `explore·grep`,注册表里的只读名是 `grep` —— 两边都要去前缀。"""
    msgs = _msgs(MICRO_KEEP_RECENT + 2, name="explore·grep")
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert cleared == 2


def test_placeholder_says_size_and_how_to_get_it_back():
    note = micro_placeholder(5000)
    assert "5000" in note and "留档" in note and "重新调用" in note


def test_tool_names_come_from_the_call_because_results_carry_no_name():
    """`add_tool_result` 存的只有 role/tool_call_id/content —— 谁产的要回看 tool_calls。"""
    msgs = _msgs(1, name="glob")
    assert tool_names_by_call(msgs) == {"c0": "glob"}


def test_the_archive_messages_are_not_mutated():
    """**这条是整个改动的前提**:出口上做的副本,磁盘与 session.messages 必须完整。"""
    msgs = _msgs(MICRO_KEEP_RECENT + 4, name="grep")
    before = json.dumps(msgs, ensure_ascii=False)
    out, cleared, _ = microcompact(msgs, {"grep"})
    assert cleared == 4
    assert json.dumps(msgs, ensure_ascii=False) == before, "传进来的那份被改了"
    assert any("省略" in m["content"] for m in out if m["role"] == "tool")


def test_agent_sends_placeholders_but_keeps_the_full_archive(tmp_path):
    """端到端:真 Agent + 真注册表形状,验"发出去的是压过的、留下的是全文"。"""
    from minicode.agent import Agent
    from minicode.config import Config, Provider
    from minicode.llm import AssistantTurn, ToolCall
    from minicode.session import Session
    from minicode.tools.base import Tool, ToolContext

    class _UI:
        def __init__(self):
            self.notes = []

        def __getattr__(self, name):
            return lambda *a, **k: None

        def info(self, text): self.notes.append(text)
        def tool_start(self, tool, args): pass
        def tool_done(self, tool, result): pass

    class _Allow:
        ask_note = ""
        ask_kind = ""
        project_allow_reason = ""

        def new_turn(self): pass

        def check(self, tool, args): return "allow"

        def approve_for_turn(self, name): pass

        def approve_outside(self, tool, args, session=False): pass

    class _Steps:
        """每一步都调一次 grep(结果超长),跑满 keep+2 步后出口就该开始省略了。"""

        def __init__(self, n):
            self.n = n
            self.i = 0

        def stream_chat(self, messages, tools, **kw):
            self.i += 1
            if self.i > self.n:
                return AssistantTurn(text="收尾")
            cid = f"call{self.i}"
            return AssistantTurn(tool_calls=[ToolCall(
                id=cid, name="grep",
                arguments=json.dumps({"pattern": f"p{self.i}"}))])

    ui = _UI()
    s = Session(system_prompt="s", log_dir=None)
    seen = []

    def grep(ctx, pattern=""):
        seen.append(pattern)
        return LONG

    tool = Tool(name="grep", description="", read_only=True, handler=grep,
                parameters={"type": "object", "properties": {}})
    cfg = Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                     api_key="k", model="m")], active_provider_id="t",
                 context_window=400000)   # 撑大:自动压缩是另一支测的事
    agent = Agent(llm=_Steps(MICRO_KEEP_RECENT + 3), registry=[tool], session=s,
                  gate=_Allow(), ui=ui,
                  ctx=ToolContext(workspace=tmp_path, config=cfg),
                  max_steps=MICRO_KEEP_RECENT + 5)
    agent.run_turn("搜吧")

    # 留档:每一条工具结果都还是全文
    archived = [m for m in s.messages if m["role"] == "tool"]
    assert len(archived) >= MICRO_KEEP_RECENT + 3
    assert all(len(m["content"]) == len(LONG) for m in archived), "留档被压过了"
    # 界面:那句"省略了多少"一回合只说一次
    assert sum(1 for n in ui.notes if "省略" in n) == 1, ui.notes
    # 出口:压过的确实是副本而不是本体
    sent = agent._messages_for_request()
    assert sum(1 for m in sent if m["role"] == "tool" and "省略" in str(m["content"])) > 0
    assert all(len(m["content"]) == len(LONG) for m in s.messages if m["role"] == "tool")
