"""信号回灌 B0/B1:已经记下来的东西,回头要真的改变行为(v0.9.39)。

方案在主线那份 `D:/game/mini-code/docs/roadmap-v0.9.39-信号回灌.md`(本树没带副本)。这份测试钉的是它的四条红线,而不是
"功能跑通了":

1. **判据只有一份**:"这次搜空了"认的是 `kb.NO_HIT_TEXT`(与 `format_results` 同源),
   计数只扫一遍(`feedback_recall.empty_hits`),工具结果末尾那句与监控栏那个「×N」
   必须说同一个数 —— 两处各算一次,迟早出现"聊天里说没搜到、监控栏写着搜到了"。
2. **只认跨轮反复**:一次任务里换着关键词补搜是正常检索,不该被催;第 3 次同一个词
   还空手才在结果里附一句。
3. **差评只当先验,绝不当众复述**:`avoidance_hint` 里不许出现"你点过不满意"这类话;
   相似度够不到 `memory.is_same_memory` 那一份口径就一个字都不提示(负例逐字钉)。
4. **关掉开关就与开之前逐字一致**:`feedback_recall: false` 时,发给模型的 messages
   就是留档那一份对象本身,工具结果也不多个字。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from minicode import activity, feedback_recall, growth, kb
from minicode.agent import Agent
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import PermissionGate
from minicode.session import Session
from minicode.tools.base import Tool, ToolContext


@pytest.fixture
def config():
    return Config(providers=[Provider(id="t", name="T",
                                      base_url="https://example.invalid/v1",
                                      api_key="k", model="m")],
                  active_provider_id="t")


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """把应用数据目录指到临时目录:`message-feedback.jsonl` 只在这次测试里存在。"""
    monkeypatch.setattr("minicode.paths.app_path",
                        lambda *parts: Path(tmp_path, "app", *parts))
    return Path(tmp_path, "app")


@pytest.fixture
def only_tmp_kb(tmp_path, monkeypatch):
    """把知识库指到临时目录。

    不这么做的话 `kb.kb_dirs()` 还会带上仓库自带的 `kb/`(实测 860 条真语料),
    于是"搜空"这个前提根本不成立 —— 测试会绿得很假,或者像这次一样红得莫名其妙。
    """
    d = _make_kb(tmp_path)
    monkeypatch.setattr(kb, "kb_dirs", lambda workspace=None: [d])
    return d


def _make_kb(tmp_path) -> Path:
    """一个**有数据但接不住**某些问法的知识库(空库会走"错误: 知识库为空",那不是搜空)。"""
    d = tmp_path / "kb"
    d.mkdir(exist_ok=True)
    (d / "notices.json").write_text(json.dumps([
        {"id": "N001", "title": "国家励志奖学金申请通知", "category": "奖助学金",
         "body": "奖励标准5000元/人,请于9月14日前提交材料。", "credibility": "A"},
    ], ensure_ascii=False), encoding="utf-8")
    return d


def _write_feedback(app_dir: Path, rows: list[dict]) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    with open(app_dir / growth.FEEDBACK_FILE, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _fb(rating: int, question: str, days_ago: int = 0, answer: str = "答") -> dict:
    ts = time.strftime("%Y-%m-%d %H:%M:%S",
                       time.localtime(time.time() - days_ago * 86400))
    return {"ts": ts, "rating": rating, "question": question, "answer": answer,
            "session": "s"}


# ------------------------------------------------------------------ 判据同源

def test_no_hit_wording_comes_from_one_place():
    """"没查到"这句话只有一处真相:改文案不会让 B0 静默失灵。"""
    assert kb.is_no_hit(kb.format_results([]))
    assert kb.is_no_hit(kb.format_results([], meta={"mode": "关键词"}))
    assert kb.NO_HIT_TEXT in kb.format_results([])


def test_hits_errors_and_non_strings_are_not_counted_as_empty():
    assert not kb.is_no_hit("共命中 1 条(按相关度排序)。…")
    assert not kb.is_no_hit("错误: 知识库为空(kb/ 目录下没有可用的 JSON 数据)。")
    assert not kb.is_no_hit(None)          # 还没回来(在跑)
    assert not kb.is_no_hit(42)            # 宁可少标,不乱标


# ------------------------------------------------------------------ B0 计数

def _calls(*pairs) -> list[dict]:
    """把 (query, result) 串成一份真的会话消息形状(assistant.tool_calls + tool)。"""
    msgs: list[dict] = [{"role": "system", "content": "sys"},
                        {"role": "user", "content": "问一句"}]
    for i, (q, res) in enumerate(pairs, start=1):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "search_knowledge",
                                                  "arguments": json.dumps(
                                                      {"query": q},
                                                      ensure_ascii=False)}}]})
        if res is not None:
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": res})
    return msgs


EMPTY = kb.format_results([])


def test_three_empty_searches_of_one_word_count_up():
    msgs = _calls(("奖学金", EMPTY), ("奖学金", EMPTY), ("奖学金", EMPTY))
    assert [n for _c, _q, n in feedback_recall.empty_hits(msgs)] == [1, 2, 3]
    assert feedback_recall.repeat_empty_queries(msgs) == {
        growth.normalize_query("奖学金"): 3}


def test_a_running_call_and_other_tools_are_not_counted():
    msgs = _calls(("奖学金", EMPTY), ("奖学金", None))       # 第二次还没回话
    msgs.append({"role": "assistant", "content": "",
                 "tool_calls": [{"id": "c9", "type": "function",
                                 "function": {"name": "read_file",
                                              "arguments": json.dumps(
                                                  {"path": "a.txt"})}}]})
    msgs.append({"role": "tool", "tool_call_id": "c9", "content": EMPTY})
    assert feedback_recall.repeat_empty_queries(msgs) == {
        growth.normalize_query("奖学金"): 1}


def test_rewording_within_one_task_does_not_add_up():
    """一次任务里换关键词补搜是**正常检索**,不是"反复搜空"。"""
    msgs = _calls(("奖学金", EMPTY), ("励志奖学金 材料", EMPTY), ("评奖评优", EMPTY))
    counts = feedback_recall.repeat_empty_queries(msgs)
    assert max(counts.values()) == 1, counts


def test_punctuation_and_spacing_variants_are_the_same_word():
    msgs = _calls(("奖学金 申请", EMPTY), ("奖学金,申请!", EMPTY))
    counts = feedback_recall.repeat_empty_queries(msgs)
    assert len(counts) == 1 and list(counts.values()) == [2]


def test_repeat_note_states_the_count_and_a_way_out():
    note = feedback_recall.repeat_note(3)
    assert "×3" in note and "3 次" in note
    assert "kb_suggest" in note            # 给得出下一步,不是只说"没有"
    assert "上次" not in note and "不满意" not in note


# ------------------------------------------------------------------ B0 端到端

class RecordingLLM:
    """记下每次请求**实际发出去的 messages**(B1 要验的就是这一份)。"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None):
        self.seen.append(messages)
        return self.turns.pop(0)


def _search_tool() -> Tool:
    return Tool(name="search_knowledge", description="检索本地知识库",
                parameters={"type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"]},
                handler=kb.search_knowledge, read_only=True)


def _agent(tmp_path, config, turns, registry=None):
    _make_kb(tmp_path)
    llm = RecordingLLM(turns)
    ctx = ToolContext(workspace=tmp_path, config=config)
    agent = Agent(llm=llm, registry=registry or [_search_tool()],
                  session=Session(system_prompt="你是学长"),
                  gate=PermissionGate(config), ui=_QuietUI(), ctx=ctx)
    return agent, llm


class _QuietUI:
    def confirm_tool(self, tool, args, reason="", note=""):
        return "yes"                    # 代用户点"允许",否则非只读工具走不到执行

    def __getattr__(self, name):
        return lambda *a, **k: None


def _search_turn(query: str, cid: str) -> AssistantTurn:
    return AssistantTurn(tool_calls=[ToolCall(
        id=cid, name="search_knowledge",
        arguments=json.dumps({"query": query}, ensure_ascii=False))])


def test_the_mark_appears_only_from_the_third_empty_search(tmp_path, config, only_tmp_kb):
    agent, _llm = _agent(tmp_path, config, [
        _search_turn("社团招新", "c1"),      # 库里没有这个词
        _search_turn("社团招新", "c2"),
        _search_turn("社团招新", "c3"),
        AssistantTurn(text="收工")])
    agent.run_turn("帮我查社团招新")
    results = [m["content"] for m in agent.session.messages if m["role"] == "tool"]
    assert len(results) == 3
    assert "×3 未命中" in results[2], results[2]
    # v0.9.92 起第 1 次未命中就带 kb 的联网/入库路径指引(FALLBACK_HINT,不随
    # 回灌开关),所以这里断的是 B0 催促句本身,不再断"未命中"三个字
    assert "别再换着说法" not in results[0] and "别再换着说法" not in results[1], \
        "前两次空手是正常检索,B0 不该被催"


def test_switch_off_leaves_results_byte_identical(tmp_path, config, only_tmp_kb):
    config.feedback_recall = False
    agent, _llm = _agent(tmp_path, config, [
        _search_turn("社团招新", "c1"), _search_turn("社团招新", "c2"),
        _search_turn("社团招新", "c3"), AssistantTurn(text="收工")])
    agent.run_turn("帮我查社团招新")
    results = [m["content"] for m in agent.session.messages if m["role"] == "tool"]
    # v0.9.92:kb 的未命中路径指引(FALLBACK_HINT)是 kb 模块固有出口,不随
    # 回灌开关变化;这条闸钉的是 B0 的催促句必须消失(开关只管回灌那一层)
    assert all("别再换着说法" not in r for r in results), results


# ------------------------------------------------------------------ B0 监控栏

def _timeline_rows(session) -> list[dict]:
    out = []
    for turn in activity.timeline(session.messages)["turns"]:
        out.extend(turn["items"])
    return out


def test_monitor_marks_from_the_second_empty_search(tmp_path, config, only_tmp_kb):
    agent, _llm = _agent(tmp_path, config, [
        _search_turn("社团招新", "c1"), _search_turn("社团招新", "c2"),
        _search_turn("社团招新", "c3"), AssistantTurn(text="收工")])
    agent.run_turn("帮我查社团招新")
    marks = [r.get("repeat_text", "") for r in _timeline_rows(agent.session)]
    assert marks == ["", "×2 未命中", "×3 未命中"], marks


def test_monitor_and_tool_result_tell_the_same_count(tmp_path, config, only_tmp_kb):
    """两处数字同源:监控栏写 ×3 时,工具结果末尾那句也必须写 ×3。"""
    agent, _llm = _agent(tmp_path, config, [
        _search_turn("社团招新", "c1"), _search_turn("社团招新", "c2"),
        _search_turn("社团招新", "c3"), AssistantTurn(text="收工")])
    agent.run_turn("帮我查社团招新")
    rows = [r for r in _timeline_rows(agent.session) if r.get("repeat_text")]
    last_tool = [m["content"] for m in agent.session.messages
                 if m["role"] == "tool"][-1]
    assert rows[-1]["repeat_text"] == "×3 未命中"
    assert "×3 未命中" in last_tool


# ------------------------------------------------------------------ B1 差评先验

def test_only_recent_thumbs_down_with_a_question_are_read(app_dir):
    _write_feedback(app_dir, [
        _fb(-1, "国家励志奖学金怎么申请"),
        _fb(1, "这条是好评,不该被当成先验"),
        _fb(-1, "两年前的差评", days_ago=feedback_recall.MATCH_DAYS + 10),
        _fb(-1, ""),                              # 没有问题原文,没法判同类
        {"ts": "坏行", "rating": -1},
    ])
    (app_dir / growth.FEEDBACK_FILE).open("a", encoding="utf-8").write("{半截 json\n")
    rows = feedback_recall.recent_low_rated()
    assert [r["question"] for r in rows] == ["国家励志奖学金怎么申请"]


def test_missing_feedback_file_is_not_an_error(app_dir):
    assert feedback_recall.recent_low_rated() == []
    assert feedback_recall.match_low_rated("随便问点什么") is None


def test_match_needs_real_similarity():
    rows = [{"question": "国家励志奖学金怎么申请", "q_norm": "国家励志奖学金怎么申请",
             "answer": "答", "ts": ""}]
    hit = feedback_recall.match_low_rated("国家励志奖学金要怎么申请?", rows)
    assert hit and hit["question"] == "国家励志奖学金怎么申请"
    # 负例:结构上完全不同的问题,一个字都不提示
    assert feedback_recall.match_low_rated("图书馆几点闭馆", rows) is None
    assert feedback_recall.match_low_rated("", rows) is None
    assert feedback_recall.match_low_rated("国家励志奖学金怎么申请", []) is None


def test_match_picks_the_most_similar_row():
    rows = [{"question": "图书馆几点闭馆", "q_norm": "图书馆几点闭馆", "answer": "", "ts": ""},
            {"question": "国家励志奖学金怎么申请", "q_norm": "国家励志奖学金怎么申请",
             "answer": "", "ts": ""}]
    hit = feedback_recall.match_low_rated("国家励志奖学金怎么申请", rows)
    assert hit["question"] == "国家励志奖学金怎么申请"
    assert hit["similarity"] >= 0.75


def test_the_hint_never_replays_the_thumbs_down():
    """把用户私下点的那一下当众复述出来,是这份方案明确不做的事(§2 第 1 条)。"""
    hint = feedback_recall.avoidance_hint({"question": "x", "similarity": 0.9})
    for banned in ("不满意", "👎", "差评", "上次那条回答", "你点过"):
        assert banned not in hint, f"先验里出现了「{banned}」"
    assert "不要念给用户" in hint          # 它确实是给模型看的内部提示


# ------------------------------------------------------------------ B1 端到端

def test_prior_goes_into_the_next_request_only(tmp_path, config, app_dir, only_tmp_kb):
    _write_feedback(app_dir, [_fb(-1, "国家励志奖学金怎么申请")])
    agent, llm = _agent(tmp_path, config, [
        _search_turn("国家励志奖学金要怎么申请", "c1"),
        _search_turn("图书馆几点闭馆", "c2"),       # 不相似:不该再带先验
        AssistantTurn(text="收工")])
    agent.run_turn("问两件事")

    assert len(llm.seen) == 3
    assert "先验" not in llm.seen[0][0]["content"], "还没搜就先给提示,顺序错了"
    assert "先验" in llm.seen[1][0]["content"], "命中差评后,下一次请求里该有那句先验"
    assert "先验" not in llm.seen[2][0]["content"], "先验只用一次,别每步都重复"
    # 留档没被改:系统提示还是原来那句(不进 sessions/*.jsonl)
    assert agent.session.messages[0]["content"] == "你是学长"
    assert all("先验" not in m.get("content", "") for m in agent.session.messages
               if m["role"] == "system")


def test_prior_is_not_injected_for_other_tools(tmp_path, config, app_dir, only_tmp_kb):
    _write_feedback(app_dir, [_fb(-1, "把 README 改一下")])

    def _noop(ctx, **kw):
        return "好"

    agent, llm = _agent(tmp_path, config,
                        [AssistantTurn(tool_calls=[ToolCall(
                            id="c1", name="write_file",
                            arguments=json.dumps({"path": "a.txt", "content": "x"},
                                                 ensure_ascii=False))]),
                         AssistantTurn(text="收工")],
                        registry=[Tool(name="write_file", description="d",
                                       parameters={"type": "object", "properties": {},
                                                   "required": []},
                                       handler=_noop)])
    agent.run_turn("改一下")
    assert all("先验" not in m[0]["content"] for m in llm.seen), \
        "保守版只在 search_knowledge 前做匹配(用户拍的边界)"


def test_switch_off_sends_the_archived_messages_themselves(tmp_path, config, app_dir, only_tmp_kb):
    _write_feedback(app_dir, [_fb(-1, "国家励志奖学金怎么申请")])
    config.feedback_recall = False
    agent, llm = _agent(tmp_path, config, [
        _search_turn("国家励志奖学金要怎么申请", "c1"), AssistantTurn(text="收工")])
    agent.run_turn("问一句")
    # v0.9.66 语义微调:出口必经 outbound 剥离拷贝(ts 是本地记账字段,严格端点
    # 会 400),所以"同一份对象零拷贝"不再可能 —— 真正要守的是**留档本体一个字
    # 没动**,且出口内容 = 留档减 ts(关掉开关时没有任何先验被并进去)。
    from minicode.session import outbound
    sent = llm.seen[1]
    assert sent is not agent.session.messages
    assert all("ts" not in m for m in sent), "出口净场:没有本地记账字段"
    # 请求发出时"收工"还没落档,所以按前缀比:出口 = 留档同时刻的前缀减 ts
    assert sent == outbound(agent.session.messages)[:len(sent)]
    assert all("ts" in m for m in agent.session.messages if m.get("role") != "system")


def test_a_broken_feedback_file_cannot_break_the_turn(tmp_path, config, app_dir, only_tmp_kb):
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / growth.FEEDBACK_FILE).write_text("这不是 json\n{半截\n",
                                                encoding="utf-8")
    agent, _llm = _agent(tmp_path, config, [
        _search_turn("国家励志奖学金要怎么申请", "c1"), AssistantTurn(text="收工")])
    agent.run_turn("问一句")            # 不抛就算过:回灌坏了不该带走一轮对话
    assert any(m["role"] == "tool" for m in agent.session.messages)
