# -*- coding: utf-8 -*-
"""「以后都允许」落一条本机规则（v0.9.133，P0-2）。

以前用户点完「这次允许」之后想让它下次别再问,得**自己手写一份 `permissions.json`**
—— 矩阵 §10D 把那格标成「只有半格」,而 `permspec.py` 全文当时没有一个写规则的函数。

这一族针钉的是**三条红线**,少钉任何一条,这个功能就从"省事"变成"权限自动增殖":
① **只记本机、按工作区隔离**,绝不写工作区里的任何文件 —— `permissions.json` 是
   "仓库作者写的、要核对内容指纹才生效"那一本,点一次头不能往里加东西,否则 clone
   一份仓库就等于替这台机器决定什么不用问(与 §6 49「hooks 只从安装目录 config.json 读」
   同一条根)。
② **只收窄**:记的是"这一个文件 / 这一类首词",不是"这个目录"。
③ **可撤销**,且四类问句永远不给记住(凭据 / `.git` / 外部内容升格 / 项目 ask 规则,
   另加沙箱边界与危险命令、以及「没读过」那一句)。
"""
import inspect
import json

import pytest

from minicode import permspec
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import ASK, PermissionGate
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.session import Session
from minicode.tools.base import ToolContext
from minicode.tools.registry import build_default_registry

from test_agent import FakeLLM, FakeUI


@pytest.fixture
def trust(tmp_path, monkeypatch):
    """把信任表指到临时目录:这台机器上的真实授权记录一个字节都不该被测试动过。"""
    path = tmp_path / "perms-trust.json"
    monkeypatch.setattr(permspec, "trust_path", lambda: path)
    return path


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def registry():
    return {t.name: t for t in build_default_registry()}


def _names(registry):
    return set(registry)


def _gate(ws, registry, mode="confirm"):
    cfg = Config(permission_mode=mode)
    sb = SandboxState(SandboxPolicy(), ws)
    return PermissionGate(cfg, sandbox=sb, workspace=ws, tool_names=_names(registry))


def _edit_args(path="notes.txt"):
    return {"path": path, "old_string": "a", "new_string": "b"}


# ------------------------------------------------------------ ① 记下来就不再问

def test_one_click_silences_the_same_action(ws, registry, trust):
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws, registry)
    tool = registry["edit_file"]
    assert gate.check(tool, _edit_args()) == ASK
    msg = gate.remember(tool, _edit_args(), "mode")
    assert "已记住" in msg
    assert gate.check(tool, _edit_args()) == "allow"
    # 当场生效靠的是重载,不是缓存 —— 换一个 gate 实例(等于重开程序)也照样认
    assert _gate(ws, registry).check(tool, _edit_args()) == "allow"


def test_permissions_json_is_never_touched(ws, registry, trust):
    """这条是整个功能的安全边界:点一次头**不许**往工作区里写一个字。

    记进 `permissions.json` 等于"仓库里能长出自己的权限",而那份文件是要按内容指纹
    核对才生效的 —— 让 UI 的一个按钮去改它,clone 来的仓库就此带上你机器上的授权。
    """
    f = ws / permspec.PERMISSIONS_FILE
    f.write_text('{"allow": []}', encoding="utf-8")
    before = f.read_bytes()
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    _gate(ws, registry).remember(registry["edit_file"], _edit_args(), "mode")
    assert f.read_bytes() == before
    assert trust.exists() and "granted" in trust.read_text(encoding="utf-8")


def test_remembered_rule_is_scoped_to_one_file_not_the_directory(ws, registry, trust):
    """只收窄:「允许改 notes.txt」永远不等于「允许改这个目录下的任何东西」。"""
    (ws / "notes.txt").write_text("alpha\n", encoding="utf-8")
    (ws / "other.txt").write_text("alpha\n", encoding="utf-8")
    gate = _gate(ws, registry)
    gate.remember(registry["edit_file"], _edit_args("notes.txt"), "mode")
    assert gate.check(registry["edit_file"], _edit_args("notes.txt")) == "allow"
    assert gate.check(registry["edit_file"], _edit_args("other.txt")) == ASK


def test_two_workspaces_do_not_share_their_memory(ws, registry, tmp_path, trust):
    other = tmp_path / "other-proj"
    other.mkdir()
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    (other / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    gate.remember(registry["edit_file"], _edit_args(), "mode")
    assert _gate(other, registry).check(registry["edit_file"], _edit_args()) == ASK


# ------------------------------------------------------------ ③ 撤销与装载

def test_revoke_clears_both_halves_of_the_whitelist_family(ws, registry, trust):
    """撤销一句「这个项目的免确认动作都收回去」必须把记住的也带走。

    只撤文件那一半的话,用户以为已经全部失效,而某个文件从此不再问他 ——
    那正是"撤销没撤销干净"里最坏的一种。AGENTS.md 的采纳仍不动(两次独立点头)。
    """
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    gate.remember(registry["edit_file"], _edit_args(), "mode")
    assert permspec.revoke(ws) is True
    assert _gate(ws, registry).check(registry["edit_file"], _edit_args()) == ASK
    assert "granted" not in trust.read_text(encoding="utf-8")


def test_remembered_rules_load_even_without_a_project_file(ws, registry, trust):
    """本机那本账与项目文件在不在无关 —— 它就是这台机器自己的决定。"""
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    _gate(ws, registry).remember(registry["edit_file"], _edit_args(), "mode")
    spec = permspec.load(ws, _names(registry))
    assert spec.rules == [] and len(spec.granted) == 1
    assert "本机记住的第 1 条" in spec.hit("edit_file", _edit_args(), "allow")


def test_hand_edited_granted_entry_is_rechecked_on_load(ws, registry, trust):
    """装载即重核:自己写回去的数据也要再过一遍同一套校验。

    手改一条指向 `.ssh/` 的记住条目,如果装载时不再校验,"点一次头"那道门槛就变成
    了一句空话 —— 文件一改,权限跟着改,而没有任何一处会红。
    """
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    gate.remember(registry["edit_file"], _edit_args(), "mode")
    row = json.loads(trust.read_text(encoding="utf-8"))
    key = str(ws.resolve())
    row[key]["granted"].append({"tool": "edit_file", "path": "../secrets.txt"})
    trust.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    rules, problems = permspec.granted_rules(ws, _names(registry))
    assert len(rules) == 1, "越界那条必须被丢掉"
    assert problems and "含 .. 上跳" in problems[0]
    # 而且不能牵连整本账:合法那一条照样生效
    assert gate.check(registry["edit_file"], _edit_args()) == "allow"


# ------------------------------------------------------------ 四类永不记住

@pytest.mark.parametrize("kind", ["credential", "git", "external", "project_rule",
                                  "outside_path", "sandbox_command", "unread"])
def test_these_asks_are_never_rememberable(ws, registry, trust, kind):
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    assert gate.can_remember("edit_file", _edit_args(), kind) is False
    assert "没记住" in gate.remember(registry["edit_file"], _edit_args(), kind)
    assert not trust.exists() or "granted" not in trust.read_text(encoding="utf-8")


def test_mode_and_unread_are_the_two_kinds_that_do_differ(ws, registry, trust):
    """`mode` 给记(那正是这个功能要办的),`unread` 不给记(它问的是读过没有)。"""
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    assert gate.can_remember("edit_file", _edit_args(), "mode") is True
    assert gate.can_remember("edit_file", _edit_args(), "unread") is False


def test_command_rules_only_take_the_safe_heads(ws, registry, trust):
    """命令这一类只有可预授权的首词能记 —— `rm` / `del` 那种点一次头就永久不问。"""
    gate = _gate(ws, registry)
    assert gate.can_remember("bash", {"command": "pytest -q"}, "mode") is True
    assert gate.can_remember("bash", {"command": "rm -rf build"}, "mode") is False
    assert gate.can_remember("bash", {"command": "git push"}, "mode") is False
    gate.remember(registry["bash"], {"command": "rm -rf build"}, "mode")
    assert not trust.exists()


def test_a_tool_that_is_neither_path_nor_command_says_so(ws, registry, trust):
    """给不出一条可复用规则的动作,那颗按钮就不该出现(而不是出现了再报错)。"""
    gate = _gate(ws, registry)
    assert gate.can_remember("browser_open", {"url": "https://x"}, "mode") is False
    why = gate.remember(registry["memory"], {"action": "list"}, "mode")
    assert "收窄不成一条" in why


def test_duplicate_clicks_do_not_grow_the_ledger(ws, registry, trust):
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    for _ in range(3):
        msg = gate.remember(registry["edit_file"], _edit_args(), "mode")
    assert "本来就记住了" in msg
    assert len(permspec.granted_raw(ws)) == 1


# ------------------------------------------------------------ 界面协议与调用点

def test_every_ui_accepts_the_new_choice(registry):
    """六个实现 + 那颗按钮的传递必须一起改到位。

    这条是**签名对账**,不是文字对账:上一轮就红过一次 —— 界面替身少一个参数时,
    调用点抛 TypeError 被当成"这一步失败了",确认框根本没弹出来而测试差点没看见。
    """
    from minicode.gui_bridge import Bridge
    from minicode.gui_ui import GuiUI
    from minicode.headless import HeadlessUI
    from minicode.ui import TerminalUI
    from minicode.web_ui import WebUI
    from minicode.subagents import _TaggedUI
    for cls in (TerminalUI, GuiUI, Bridge, WebUI, HeadlessUI, _TaggedUI):
        params = list(inspect.signature(cls.confirm_tool).parameters)
        assert params[:5] == ["self", "tool", "args", "reason", "note"], params
        assert "can_remember" in params, f"{cls.__name__} 没接 can_remember"


def test_gui_end_to_end_pushes_the_flag_and_remembering_works(tmp_path, monkeypatch, trust):
    """真跑图形链路(桥 + 假窗口事件流),不是读源码:那颗按钮的出现条件、回话值、
    以及"点下去之后这一轮真的执行了、本机真的记住了",三件事一次验完。

    前端 DOM 那半（按钮 display 与 `confirm_reply("remember")`）在
    `scripts/e2e_ui_confirm_choices.py` 里用真 Chromium 量,Python 这边量不到。
    """
    from test_gui_bridge import make, wait_until
    from test_agent import FakeLLM as _FakeLLM
    from minicode.llm import AssistantTurn as _AT, ToolCall as _TC

    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    cfg = Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m")],
        active_provider_id="t", permission_mode="confirm")
    llm = _FakeLLM([
        _AT(tool_calls=[_TC(id="c1", name="edit_file", arguments=json.dumps(
            {"path": "notes.txt", "old_string": "alpha", "new_string": "one"}))]),
        _AT(text="好了"),
    ])
    bridge, window, _session = make(cfg, tmp_path, llm)
    assert bridge.send_task("改一下 notes.txt")["ok"]
    assert wait_until(lambda: any(e["type"] == "confirm_request" for e in window.events))
    ev = next(e for e in window.events if e["type"] == "confirm_request")
    assert ev["can_remember"] is True, "改一个具体文件这一步该给那颗按钮"
    bridge.confirm_reply("remember")
    assert wait_until(lambda: not bridge._busy)
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\n"
    assert "granted" in trust.read_text(encoding="utf-8")
    assert not (tmp_path / permspec.PERMISSIONS_FILE).exists()
    # 桥那侧的词表收不收这个回话值,表现是"这一轮到底执行了没有" —— 上面那句断言
    # 就是它的证据:没收的话 _confirm_choice 还是 "no",文件内容不会变。


class RememberUI(FakeUI):
    """第一次点「以后都允许」,以后照常走。"""

    def __init__(self):
        super().__init__(confirm_choice="remember")
        self.infos = []
        self.calls = []

    def info(self, text):
        self.infos.append(text)

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False):
        self.confirmed.append((tool.name, dict(args)))
        self.calls.append(can_remember)
        self.reasons.append(reason)
        return self.confirm_choice


def _agent(ws, ui, llm, registry):
    cfg = Config(permission_mode="confirm")
    sb = SandboxState(SandboxPolicy(), ws)
    gate = PermissionGate(cfg, sandbox=sb, workspace=ws, tool_names=set(registry))
    ctx = ToolContext(workspace=ws, config=cfg, sandbox=sb)
    from minicode.agent import Agent
    return Agent(llm=llm, registry=list(registry.values()),
                 session=Session(system_prompt="sys"), gate=gate, ui=ui, ctx=ctx)


def test_clicking_the_button_end_to_end_silences_the_next_ask(ws, registry, trust):
    """真跑一遍:点「以后都允许」→ 这一问执行 → 下一问同文件不再弹,且界面说出了记了什么。"""
    (ws / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="edit_file", arguments=json.dumps(
                {"path": "notes.txt", "old_string": "alpha", "new_string": "one"}))]),
        AssistantTurn(text="第一处好了"),
        AssistantTurn(tool_calls=[ToolCall(
            id="c2", name="edit_file", arguments=json.dumps(
                {"path": "notes.txt", "old_string": "beta", "new_string": "two"}))]),
        AssistantTurn(text="两处都好"),
    ])
    ui = RememberUI()
    agent = _agent(ws, ui, llm, registry)
    agent.run_turn("改两处")
    assert "one" in (ws / "notes.txt").read_text(encoding="utf-8")
    assert len(ui.confirmed) == 1, "第二次不该再问"
    assert ui.calls == [True], "那颗按钮该在这一步出现"
    assert any("已记住" in t for t in ui.infos)
    assert not (ws / permspec.PERMISSIONS_FILE).exists()


def test_the_info_line_is_the_only_notice_and_it_is_not_silent(ws, registry, trust):
    """记成什么必须说出口:静默记住等于权限在没人看见的地方增殖。"""
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    gate = _gate(ws, registry)
    msg = gate.remember(registry["edit_file"], _edit_args(), "mode")
    assert "只在这台机器" in msg and "这个项目" in msg


def test_headless_still_refuses_and_never_remembers(ws, registry, trust):
    """「换入口不换权限」的另一半:无头模式没有人点头,也就没有一条该被记住。"""
    from minicode.headless import HeadlessUI
    hui = HeadlessUI(object())
    assert hui.confirm_tool(_FakeTool("edit_file"), _edit_args(),
                           reason="r", note="", can_remember=True) == "no"
    assert not trust.exists()


class _FakeTool:
    def __init__(self, name):
        self.name = name


def test_cli_offers_the_choice_only_when_the_gate_allows_it(ws, registry, trust):
    """命令行那颗 (r) 出现与否跟着门的判断走,不在界面里另判一次。

    假 console 的答案**必须是有限序列**:上一版这里写成"永远回 r",于是
    `can_remember=False` 那一路在"没听懂→再问一次"的循环里转到把内存吃光,
    整台机器的 fork 都开始失败。测试把被测代码的死循环放大成自己的死循环,
    是这一族最贵的一种坏法(它不会红,只会拖死旁边的人)。
    """
    from minicode import ui as ui_mod
    real = ui_mod.TerminalUI

    class C:
        def __init__(self, answers):
            self.answers = list(answers)
            self.shown: list[str] = []

        def print(self, *a, **k):
            for x in a:
                self.shown.append(str(x))

        def input(self, *a, **k):
            self.shown.append(str(a[0] if a else ""))
            if not self.answers:
                raise EOFError        # 问完就该收口,不许在这里空转
            return self.answers.pop(0)

    u = real.__new__(real)
    u._permission_detail = lambda tool, args, why: "detail"
    ui_mod.Panel = lambda *a, **k: a
    tool = registry["edit_file"]

    c = C(["r"])
    u.console = c
    assert u.confirm_tool(tool, _edit_args(), can_remember=True) == "remember"
    assert "以后都允许" in "\n".join(c.shown)

    # 不给记的时候那句提示一个字都不出现;此时答 r 算没听懂,问第二次后按拒绝收口
    c2 = C(["r", "n"])
    u.console = c2
    assert u.confirm_tool(tool, _edit_args(), can_remember=False) == "no"
    assert "以后都允许" not in "\n".join(c2.shown)
    assert not trust.exists()
