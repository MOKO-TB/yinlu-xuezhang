# -*- coding: utf-8 -*-
"""没读过的文件不许直接改（v0.9.133，P0-1）。

钉的是**正确性**不是安全：模型没读过一份既有文件就调 `edit_file`，那份
`old_string` 是按印象写的，而 `fs.py` 只做逐字匹配 —— 结局要么「没找到」白烧一轮，
要么改错地方并把错的那份永久写进同学的文件。Claude Code 把这当成防幻觉第一道。

四条形状每件都要有针（口径写在 `permission.py` 那段注释里）：
① **不硬拒**，问一次，人点「允许」照改；
② **只碰还会问人的两个档位**：`confirm` 给本来就有的那张框补一句原因（`ask_kind`
   仍是 `mode` —— 那个字段说的是哪条门开的口，不是文案），`accept_edits` 才是这枚钉
   真正咬人的地方（它以前「工作区内写文件一律不问」）；`accept_all` / `full` 一个字
   不动，那是档位对用户的承诺；
③ **写成功也算读过**，否则「学长写完再改」这条最常见的链会被误伤；
④ 红线（凭据 / `.git` / 外部内容升级门 / 项目 ask 规则 / 规划锁）**排在它之前**，
   一枚启发式不许把它们的文案顶掉。
"""
import json

import pytest

from minicode.agent import Agent
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import ALLOW, ASK, PermissionGate
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.session import Session
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry

from test_agent import FakeLLM, FakeUI


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def registry():
    return {t.name: t for t in build_default_registry()}


def _config(mode="accept_edits", read_before=True):
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m")],
        active_provider_id="t",
        permission_mode=mode,
        read_before_edit=read_before,
    )


def _gate(ws, mode="accept_edits", read_before=True):
    sb = SandboxState(SandboxPolicy(), ws)
    return PermissionGate(_config(mode, read_before), sandbox=sb, workspace=ws)


def _edit(path="notes.txt"):
    return {"path": path, "old_string": "alpha", "new_string": "beta"}


# ------------------------------------------------------------ ② 咬人与不咬人

def test_unread_existing_file_now_asks_in_auto_edit_mode(ws, registry):
    """这枚钉的全部价值在这一条:以前这一档「工作区内写文件一律不问」。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    assert gate.check(registry["edit_file"], _edit()) == ASK
    assert gate.ask_kind == "unread"
    assert "没读过" in gate.ask_note and "notes.txt" in gate.ask_note


def test_reading_it_first_lights_it_up(ws, registry):
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    gate.note_read("notes.txt")
    assert gate.check(registry["edit_file"], _edit()) == ALLOW


def test_the_key_is_the_same_ruler_as_the_gate(ws, registry):
    """「./notes.txt」与「notes.txt」必须是同一条路径 —— 不然读过也算没读过。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    gate.note_read("./notes.txt")
    assert gate.check(registry["edit_file"], _edit()) == ALLOW
    # 反方向也一样:子目录的两种写法同一个键
    sub = ws / "src"
    sub.mkdir()
    (sub / "a.py").write_text("alpha\n", encoding="utf-8")
    gate2 = _gate(ws)
    gate2.note_read(str((sub / "a.py").resolve()))
    assert gate2.check(registry["edit_file"],
                       _edit("src/a.py")) == ALLOW


def test_new_file_stays_silent(ws, registry):
    """新建没有任何可先读的东西 —— 这条钉最不该出声的一例。"""
    gate = _gate(ws)
    assert gate.check(registry["write_file"],
                      {"path": "brand/new.txt", "content": "hi"}) == ALLOW


def test_failed_read_does_not_light_it_up(ws, registry):
    """读失败不该点亮:门要的是「内容真进过它的手」,不是「调过一次 read_file」。"""
    blob = ws / "data.bin"
    blob.write_bytes(b"\x00\x01alpha")
    gate = _gate(ws)
    ctx = ToolContext(workspace=ws, config=_config(), sandbox=gate.sandbox)
    from minicode.tools.fs import read_file
    assert read_file(ctx, "data.bin").startswith("错误:")     # 二进制读不出来
    assert gate.check(registry["edit_file"], _edit("data.bin")) == ASK


# ------------------------------------------------------------ ④ 红线在它之前

@pytest.mark.parametrize("path,want", [
    (".env", "credential"),
    (".git/hooks/pre-commit", "git"),
])
def test_red_lines_keep_their_own_names(ws, registry, path, want):
    """没读过的红线路径仍然报**原来那道门**的名字,不许被这枚启发式顶掉。

    顶掉了会同时坏两件事:用户看到的是弱化版的理由,而 `permission_request` 钩子
    按 `ask_kind` 判「这三类不许代答」—— 类别一改成 `unread`,红线就能被钩子放行。
    """
    p = ws / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    assert gate.check(registry["edit_file"], _edit(path)) == ASK
    assert gate.ask_kind == want


def test_external_upgrade_gate_outranks_it(ws, registry):
    (ws / "kb").mkdir()
    (ws / "kb" / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    gate.note_external("web_crawl")
    assert gate.check(registry["edit_file"], _edit("kb/notes.txt")) == ASK
    assert gate.ask_kind == "external"        # 写进信任源目录那一问更该先说话


# ------------------------------------------------------------ ② 的另一半:confirm

def test_confirm_mode_keeps_kind_and_adds_the_reason(ws, registry):
    """这一问本来就有,所以 `ask_kind` 仍是 `mode`,只有文案多一行。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws, mode="confirm")
    assert gate.check(registry["edit_file"], _edit()) == ASK
    assert gate.ask_kind == "mode"
    assert "没读过" in gate.ask_note
    gate.note_read("notes.txt")
    gate._ask_note = ""
    assert gate.check(registry["edit_file"], _edit()) == ASK
    assert gate.ask_note == ""               # 读过之后与今天逐字一样


@pytest.mark.parametrize("mode", ["accept_edits", "confirm"])
def test_switch_off_is_byte_identical_to_before(ws, registry, mode):
    """`read_before_edit=false` 必须逐字回到开着这枚钉之前:不问的照旧不问,
    要问的一句原因都不多。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws, mode=mode, read_before=False)
    decision = gate.check(registry["edit_file"], _edit())
    assert decision == (ALLOW if mode == "accept_edits" else ASK)
    assert gate.ask_note == ""


def test_auto_allow_modes_are_not_touched(ws, registry):
    """`accept_all` / `full` 对用户的承诺就是不再问,拿启发式去拆承诺是另一种坏法。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    for mode in ("accept_all", "full"):
        gate = _gate(ws, mode=mode)
        assert gate.check(registry["edit_file"], _edit()) == ALLOW


# ------------------------------------------------------------ ③ 隔离与记账

def test_reads_are_per_agent_not_shared_with_subagents(ws, registry):
    """两个 gate 共用同一份 sandbox(规划锁要靠它传下去),而这本账必须各自独立。

    侦察那一档读过的东西不该让写代码的这一档以为自己也读过 —— 它拿到的是摘要。
    """
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    shared = SandboxState(SandboxPolicy(), ws)
    main = PermissionGate(_config(), sandbox=shared, workspace=ws)
    sub = PermissionGate(_config(), sandbox=shared, workspace=ws)
    sub.note_read("notes.txt")
    assert main.check(registry["edit_file"], _edit()) == ASK
    main.note_read("notes.txt")
    assert main.check(registry["edit_file"], _edit()) == ALLOW


def test_new_turn_does_not_forget_what_was_read(ws, registry):
    """回合尺度的账会自己刷屏:同一份文件每问一次都要重新读一遍是错的。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    gate.note_read("notes.txt")
    gate.new_turn()
    assert gate.check(registry["edit_file"], _edit()) == ALLOW


class NoteUI(FakeUI):
    """带 note 那一栏的界面替身:真实四参数形状,不然测的是不存在的调用。"""

    def __init__(self, confirm_choice="yes"):
        super().__init__(confirm_choice)
        self.notes = []

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False):
        self.confirmed.append((tool.name, dict(args)))
        self.reasons.append(reason)
        self.notes.append(note)
        return self.confirm_choice


def _agent(ws, ui, llm, mode="accept_edits", read_before=True):
    cfg = _config(mode, read_before)
    sb = SandboxState(SandboxPolicy(), ws)
    gate = PermissionGate(cfg, sandbox=sb, workspace=ws)
    ctx = ToolContext(workspace=ws, config=cfg, sandbox=sb)
    return Agent(llm=llm, registry=build_default_registry(),
                 session=Session(system_prompt="sys"), gate=gate, ui=ui, ctx=ctx)


def _turn(tool, args, text="做完"):
    return AssistantTurn(tool_calls=[ToolCall(id="c1", name=tool,
                                              arguments=json.dumps(args))])


def test_written_file_is_known_without_reading_it(ws):
    """端到端钉的是调用点:这支笔由 `Agent._finish` 落下,不是谁手写进 gate 的。"""
    llm = FakeLLM([
        _turn("write_file", {"path": "a.txt", "content": "alpha\n"}),
        _turn("edit_file", _edit("a.txt")),
        AssistantTurn(text="完成"),
    ])
    ui = NoteUI()
    agent = _agent(ws, ui, llm)
    agent.run_turn("先写再改")
    assert (ws / "a.txt").read_text(encoding="utf-8") == "beta\n"
    # 自动编辑档里 ALLOW 根本不弹框,所以"没问"的证据是 confirmed 空,不是它记了两条
    assert ui.confirmed == [] and ui.notes == []


def test_approved_edit_is_not_asked_twice_for_the_same_file(ws):
    """点「仍然改」落盘之后,同一份文件下一问不再问(③ 的另一面)。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    llm = FakeLLM([
        _turn("edit_file", _edit("notes.txt")),
        _turn("edit_file", {"path": "notes.txt", "old_string": "beta",
                            "new_string": "gamma"}),
        AssistantTurn(text="完成"),
    ])
    ui = NoteUI()
    agent = _agent(ws, ui, llm)
    agent.run_turn("改两次")
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "gamma\n"
    assert [n for n, _ in ui.confirmed] == ["edit_file"]   # 只问第一次
    assert "没读过" in ui.notes[0]


def test_refused_edit_lands_nothing(ws):
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    llm = FakeLLM([
        _turn("edit_file", _edit("notes.txt")),
        AssistantTurn(text="好,那我先读一遍"),
    ])
    ui = NoteUI(confirm_choice="no")
    agent = _agent(ws, ui, llm)
    agent.run_turn("改一下")
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "alpha\n"
    assert len(ui.confirmed) == 1


def test_read_then_edit_never_asks_end_to_end(ws):
    (ws / "notes.txt").write_text("alpha\nsecond\n", encoding="utf-8")
    llm = FakeLLM([
        _turn("read_file", {"path": "notes.txt"}),
        _turn("edit_file", {"path": "notes.txt", "old_string": "second",
                            "new_string": "beta"}),
        AssistantTurn(text="完成"),
    ])
    ui = NoteUI()
    agent = _agent(ws, ui, llm)
    agent.run_turn("读了再改")
    assert "beta" in (ws / "notes.txt").read_text(encoding="utf-8")
    assert ui.confirmed == []                # 一个框都没弹


def test_switch_off_end_to_end_asks_nothing(ws):
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    llm = FakeLLM([
        _turn("edit_file", _edit("notes.txt")),
        AssistantTurn(text="完成"),
    ])
    ui = NoteUI()
    agent = _agent(ws, ui, llm, read_before=False)
    agent.run_turn("直接改")
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "beta\n"
    assert ui.confirmed == []


def test_concurrency_precheck_would_ask_sees_this_gate(ws, registry):
    """预演必须算上这一问,否则并发批次会把一个「其实要问人」的步骤一起发出去。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws)
    assert gate.would_ask(registry["edit_file"], _edit()) is True
    gate.note_read("notes.txt")
    assert gate.would_ask(registry["edit_file"], _edit()) is False
    # 预演不许把账记上:peeking 里读过一次不该真点亮
    (ws / "other.txt").write_text("alpha\n", encoding="utf-8")
    gate.would_ask(registry["edit_file"], _edit("other.txt"))
    assert gate.check(registry["edit_file"], _edit("other.txt")) == ASK
