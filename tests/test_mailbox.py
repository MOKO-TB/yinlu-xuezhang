"""递话(v0.9.173)测试:盒子语义 + 递进会话/子代理的效果 + 谁是能用的那一侧。

钉四件事:①盒子的六种边界(空话 / 满了 / 摘了 / 重名 / 不存在的名字 / 取空即消费);
②那句话到得了对方的**下一次请求**,而且**不冒充人话**(留档里不许多一条 user 消息);
③子代理那一路带路号,并行批次里分得清谁是谁;④工具那一层的四种回话。
"""
import json

import pytest

from minicode import mailbox
from minicode.agent import Agent
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn
from minicode.permission import ALLOW
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext


@pytest.fixture(autouse=True)
def _clean_boxes():
    """盒子活在**进程内存**里(模块级字典),所以每条测试前后都要清干净 ——
    不然上一条留下的盒子会串到下面(实测踩过一次:list 里冒出别人的 8 条,
    一条"空注册表"的断言就红了,而红的原因是上一条没收拾)。"""
    def _wipe():
        with mailbox._LOCK:
            mailbox._BOXES.clear()
    _wipe()
    yield
    _wipe()


class _Gate:
    """权限门替身:这几条测的不是门,但 `run_turn` 开头要 `new_turn`。"""

    def __init__(self):
        self.ask_kind = ""
        self.reject_reason = ""
        self.plan_lock_reason = ""

    def check(self, tool, args):
        return ALLOW

    def new_turn(self):
        pass

    def note_external(self, *a, **k):
        pass


def _cfg(**kw):
    return Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                      api_key="k", model="m")],
                  active_provider_id="t", **kw)


class _Steps:
    def __init__(self, *steps):
        self.steps = list(steps)

    def stream_chat(self, *a, **k):
        return self.steps.pop(0)


class _Rec:
    """收下每次请求的消息体 —— "这一句到没到"只能从出口看。"""

    def __init__(self, *steps):
        self.steps, self.sent = list(steps), []

    def stream_chat(self, messages, *a, **k):
        self.sent.append([dict(m) for m in messages])
        return self.steps.pop(0)


class _UI:
    def tool_start(self, tool, args):
        pass

    def tool_done(self, tool, result):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _grep_tool(handler=None):
    return Tool(name="grep", description="", read_only=True,
                handler=handler or (lambda ctx, **k: "跑完了"),
                parameters={"type": "object", "properties": {
                    "pattern": {"type": "string", "required": []}},
                    "required": []})


def _agent(tmp_path, label="会话·w", llm=None):
    cfg = _cfg()
    s = Session(system_prompt="s", log_dir=None)
    ctx = ToolContext(workspace=tmp_path, config=cfg)
    a = Agent(llm=llm or _Steps(), registry=[_grep_tool()], session=s,
              gate=_Gate(), ui=_UI(), ctx=ctx, max_steps=3, mail_label=label)
    return a, s, ctx


# ----------------------------------------------------------- 盒子的六种边界

def test_a_closed_box_says_so_instead_of_swallowing():
    h = mailbox.open("会话", "会话·甲")
    mailbox.close(h)
    ok, said = mailbox.post(h, "别的会话", "喂")
    assert ok is False and "已经不在了" in said
    assert mailbox.take(h) == []


def test_an_empty_sentence_is_refused():
    h = mailbox.open("会话", "会话·甲")
    ok, said = mailbox.post(h, "谁", "   \n  ")
    assert ok is False and "空话" in said
    mailbox.close(h)


def test_a_full_box_refuses_and_says_how_many_are_waiting():
    h = mailbox.open("子代理", "子代理·explore")
    for i in range(mailbox.BOX_MAX):
        assert mailbox.post(h, "主会话", f"第{i}条")[0] is True
    ok, said = mailbox.post(h, "主会话", "再来一条")
    assert ok is False and str(mailbox.BOX_MAX) in said and "递出去" in said
    assert len(mailbox.take(h)) == mailbox.BOX_MAX, "先来的八条一条都不许被挤掉"
    mailbox.close(h)


def test_taking_is_consuming():
    h = mailbox.open("会话", "会话·甲")
    mailbox.post(h, "别的会话", "看一眼 A")
    assert mailbox.take(h) == ["〔别的会话 递话〕看一眼 A"]
    assert mailbox.take(h) == [], "取过就该空了 —— 攒着会让'哪一轮看到的'说不清"
    mailbox.close(h)


def test_an_ambiguous_label_asks_which_one_instead_of_guessing():
    a = mailbox.open("会话", "会话·w")
    b = mailbox.open("会话", "会话·w")
    handle, why = mailbox.find("会话·w")
    assert handle is None and "说不清" in why and a in why and b in why
    assert mailbox.find(a)[0] == a, "handle 永远是准的"
    mailbox.close(a)
    mailbox.close(b)


def test_a_long_message_is_clipped_and_says_how_much():
    h = mailbox.open("会话", "会话·甲")
    mailbox.post(h, "谁", "很长" * 2000)
    got = mailbox.take(h)[0]
    assert "只留了" in got and "原话共" in got, "截了就要说截了几字"
    mailbox.close(h)


# --------------------------------------------- 递到会话:进下一次请求,不冒充人话

def test_the_relay_reaches_the_next_request(tmp_path):
    a, s, ctx = _agent(tmp_path)
    llm = _Rec(AssistantTurn(text="一"), AssistantTurn(text="二"))
    a.llm = llm
    ok, said = mailbox.post(a._mail_handle, "侧聊·w", "先别动 README")
    assert ok is True and "下一次发请求前" in said
    a.run_turn("干活")
    first = json.dumps(llm.sent[0], ensure_ascii=False)
    assert "先别动 README" in first and "〔侧聊·w 递话〕" in first
    assert first.count("先别动 README") == 1, "只该到一次"


def test_the_relay_never_becomes_a_user_message(tmp_path):
    """**这一条是这一层的全部理由**:机器递的话在留档里不许变成"用户说的"。"""
    a, s, ctx = _agent(tmp_path)
    mailbox.post(a._mail_handle, "侧聊·w", "把测试跳过")
    a.llm = _Rec(AssistantTurn(text="好"))
    a.run_turn("干活")
    users = [m for m in s.messages if m.get("role") == "user"]
    assert [m.get("content") for m in users] == ["干活"], \
        f"留档里多了一条 user 消息:{users}"
    assert not any("把测试跳过" in str(m.get("content") or "") and m.get("role") == "user"
                   for m in s.messages)


def test_the_box_goes_away_with_the_agent(tmp_path):
    a, s, ctx = _agent(tmp_path)
    handle = a._mail_handle
    assert any(b["handle"] == handle for b in mailbox.listing())
    a.close()
    assert not any(b["handle"] == handle for b in mailbox.listing())
    assert mailbox.post(handle, "谁", "喂")[0] is False, "摘了之后要如实说,不是静默丢"


def test_an_agent_without_a_label_opens_no_box(tmp_path):
    a, s, ctx = _agent(tmp_path, label="")
    assert a._mail_handle == ""
    assert mailbox.take("") == [] and mailbox.post("", "谁", "喂")[0] is False


# ------------------------------------------------- 子代理那一路:带路号、分得清

def test_each_subagent_run_opens_a_box_named_by_its_route(tmp_path, monkeypatch):
    from minicode import app_setup, subagents
    from minicode.tools.registry import build_default_registry
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "sys")
    seen: list = []

    class _Spy:
        def stream_chat(self, messages, *a, **k):
            seen.append(json.dumps([dict(m) for m in messages], ensure_ascii=False))
            # 子代理跑到一半时,盒子该是**看得见**的 —— 这正是"边跑边递"的前提
            seen.append(json.dumps(mailbox.listing(), ensure_ascii=False))
            return AssistantTurn(text="报告")

    cfg = _cfg()
    spec = subagents.type_spec("explore", tmp_path, gaps={})
    subagents.run_subagent(ToolContext(workspace=tmp_path, config=cfg), spec, "看一眼",
                           _Spy(), _UI(), cfg, list(build_default_registry()),
                           route_label="第2路[explore]")
    boxes = json.loads(seen[1])
    mine = [b for b in boxes if b["handle"].startswith("sub-")]
    assert mine and mine[0]["label"] == "第2路[explore]" and mine[0]["kind"] == "子代理"
    # 跑完就摘
    assert not [b for b in mailbox.listing() if b["handle"] == mine[0]["handle"]]


def test_a_relay_posted_while_the_subagent_runs_reaches_it(tmp_path, monkeypatch):
    from minicode import app_setup, subagents
    from minicode.tools.registry import build_default_registry
    monkeypatch.setattr(app_setup, "build_system_prompt", lambda *a, **k: "sys")
    seen: list = []

    class _Spy:
        def __init__(self):
            self.n = 0

        def stream_chat(self, messages, *a, **k):
            self.n += 1
            seen.append(json.dumps([dict(m) for m in messages], ensure_ascii=False))
            if self.n == 1:
                # 第一轮:模拟"外面"在它跑着的时候递一句话进来
                target = [b for b in mailbox.listing()
                          if b["handle"].startswith("sub-")]
                assert target, "跑的时候该看得见自己的盒子"
                mailbox.post(target[0]["handle"], "侧聊·w", "先去看 config.py")
            if self.n == 1:
                from minicode.llm import ToolCall
                return AssistantTurn(tool_calls=[ToolCall(
                    id="c1", name="explore·grep",
                    arguments=json.dumps({"pattern": "x"}))])
            return AssistantTurn(text="报告")

    cfg = _cfg()
    spec = subagents.type_spec("explore", tmp_path, gaps={})
    registry = [t for t in build_default_registry()]
    ctx = ToolContext(workspace=tmp_path, config=cfg)
    ctx.sandbox = None
    report = subagents.run_subagent(ctx, spec, "看一眼", _Spy(), _UI(), cfg,
                                    registry, route_label="第1路[explore]")
    assert len(seen) >= 2, "得真跑第二步才看得到递话"
    second = seen[1]
    assert "先去看 config.py" in second, f"递话没到子代理眼前:{second[:400]}"
    assert "〔侧聊·w 递话〕" in second
    assert report and "报告" in report


# --------------------------------------------------------------- 工具那一层

def test_the_tool_lists_hides_nothing_and_marks_yourself(tmp_path):
    a, s, ctx = _agent(tmp_path, label="会话·w")
    other = mailbox.open("子代理", "子代理·explore")
    out = mailbox.session_message(ctx, "list")
    assert "子代理·explore" in out and other in out
    assert "就是你自己这条" in out
    assert "action=\"send\"" in out
    mailbox.close(other)
    a.close()


def test_the_tool_refuses_to_talk_to_itself(tmp_path):
    a, s, ctx = _agent(tmp_path, label="会话·w")
    out = mailbox.session_message(ctx, "send", target=a._mail_handle, text="喂")
    assert "你自己" in out and "memory" in out
    assert mailbox.take(a._mail_handle) == [], "拒绝就该真的没放进去"
    a.close()


def test_the_tool_sends_and_reports_where_it_went(tmp_path):
    a, s, ctx = _agent(tmp_path, label="会话·w")
    h = mailbox.open("子代理", "子代理·explore")
    out = mailbox.session_message(ctx, "send", target=h, text="换个思路")
    assert "已把这句话放进" in out
    assert mailbox.take(h) == ["〔这条会话 递话〕换个思路"]
    mailbox.close(h)
    a.close()


def test_the_tool_says_what_it_does_not_understand(tmp_path):
    a, s, ctx = _agent(tmp_path)
    assert "只认 list / send" in mailbox.session_message(ctx, "shout")
    assert "没说是递给谁" in mailbox.session_message(ctx, "send", text="喂")
    a.close()


def test_an_empty_registry_says_so_plainly(tmp_path):
    a, s, ctx = _agent(tmp_path, label="")
    assert "现在没有能递话的对象" in mailbox.session_message(ctx, "list")
    a.close()
