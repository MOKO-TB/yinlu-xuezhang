"""编辑后诊断回灌(v0.9.147):按扩展名跑检查器,把结论原样回灌。

这一路最贵的坏法是**把「没做检查」说成「检查过了没问题」** —— 检查器不存在
(打包版里没有 ruff、工作区没装 tsc)、超时、起不动,三样都会让一个不看细节的
实现顺手回一句"通过"。所以下面每一支都在钉"它说的是实话",而不只是"它跑了"。
"""
import json
import subprocess

import pytest
from minicode import diagnostics
from minicode.config import Config, Provider
from minicode.tools.base import ToolContext

from test_agent import FakeLLM, FakeUI, make_agent
from minicode.llm import AssistantTurn, ToolCall


def _cfg():
    return Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                      api_key="k", model="m")],
                  active_provider_id="t")


@pytest.fixture
def ws(tmp_path):
    return tmp_path


def _ctx(workspace):
    return ToolContext(workspace=workspace, config=_cfg())


# ---------------------------------------------------------------- 检查器表

def test_checker_is_picked_by_extension_and_unknown_is_none():
    assert diagnostics.checker_for("a.py").id == "ruff"
    assert diagnostics.checker_for("a.TSX").id == "tsc"      # 大小写不敏感
    assert diagnostics.checker_for("a.go").id == "go-vet"
    assert diagnostics.checker_for("a.rs").id == "cargo-check"
    # 没有这个类型就回 None —— **不猜一个检查器出来**
    assert diagnostics.checker_for("a.md") is None
    assert diagnostics.checker_for("noext") is None


def test_every_checker_declares_its_scope():
    """粒度与副作用必须写在表里:回灌那句话直接取它,不另写一份文案。"""
    for ext, c in diagnostics.CHECKERS.items():
        assert c.scope_note.strip(), f"{ext} 没写 scope_note"
        assert c.argv, f"{ext} 的 argv 是空的"
        assert not any("{" in a for a in c.argv), \
            f"{ext} 的 argv 里出现了占位符 —— argv 必须全是定死的字面量"


# ---------------------------------------------------------------- 没做检查

def test_unknown_extension_says_no_check_was_done(ws):
    (ws / "notes.md").write_text("# hi\n", encoding="utf-8")
    out = diagnostics.diagnostics(_ctx(ws), action="check", path="notes.md")
    assert "没做检查" in out, out
    # 禁的是**真正的通过措辞**(成功那一路说的就是"没有报出问题")。
    # 那句话里会出现「检查过了没问题」,但它是被引用来**否定**的 —— 所以不列入禁词。
    for word in ("没有报出问题", "通过", "干净"):
        assert word not in out, f"没有检查器时不许出现「{word}」:{out}"
    assert "不等于" in out, f"必须把「没做检查」与「检查过了没问题」明确分开:{out}"


def test_missing_checker_says_no_check_was_done_not_passed(ws, monkeypatch):
    """打包版里没有 ruff —— 那时必须说"没做检查",不是"检查过了没问题"。"""
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_resolve_prefix",
                        lambda checker, workspace: (None, "本机没有 ruff(它是开发依赖)"))
    out = diagnostics.diagnostics(_ctx(ws), action="check", path="a.py")
    assert "没做检查" in out and "本机没有 ruff" in out, out
    for word in ("没有报出问题", "通过", "干净"):
        assert word not in out, f"检查器不存在时不许出现「{word}」:{out}"


def test_timeout_is_not_a_pass(ws, monkeypatch):
    """超时**不是通过**:没跑完就说没跑完。"""
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ruff", timeout=diagnostics.CHECK_TIMEOUT)
    monkeypatch.setattr(diagnostics, "_resolve_prefix",
                        lambda checker, workspace: (["ruff"], ""))
    monkeypatch.setattr(diagnostics.subprocess, "run", _boom)
    result = diagnostics.run_checker(ws, "a.py", diagnostics.CHECKERS[".py"])
    assert result["ran"] is False and result["ok"] is False
    assert "没跑完" in result["note"] and "不算检查过" in result["note"], result
    out = diagnostics.diagnostics(_ctx(ws), action="check", path="a.py")
    assert "没做检查" in out and "不算检查过" in out, out


def test_start_failure_is_not_a_pass(ws, monkeypatch):
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_resolve_prefix",
                        lambda checker, workspace: (["ruff"], ""))

    def _boom(*a, **k):
        raise OSError("起不动")
    monkeypatch.setattr(diagnostics.subprocess, "run", _boom)
    out = diagnostics.diagnostics(_ctx(ws), action="check", path="a.py")
    assert "没做检查" in out and "起不动" in out, out


# ---------------------------------------------------------------- 路径边界

def test_outside_or_missing_path_never_starts_a_subprocess(ws, monkeypatch):
    """工作区外 / 不存在:断言**根本没起子进程** —— 不是"起了但没报问题"。"""
    started: list = []
    monkeypatch.setattr(diagnostics.subprocess, "run",
                        lambda *a, **k: started.append(a) or None)
    ctx = _ctx(ws)
    assert "错误" in diagnostics.diagnostics(ctx, action="check", path="../outside.py")
    assert "错误" in diagnostics.diagnostics(ctx, action="check", path="没有这个文件.py")
    assert "错误" in diagnostics.diagnostics(ctx, action="check", path="")
    assert started == [], f"不该起子进程,实际起了 {len(started)} 次"


def test_oversize_file_is_skipped_not_checked(ws, monkeypatch):
    started: list = []
    monkeypatch.setattr(diagnostics.subprocess, "run",
                        lambda *a, **k: started.append(a) or None)
    (ws / "big.py").write_text("x = 1\n" * 100_000, encoding="utf-8")
    out = diagnostics.diagnostics(_ctx(ws), action="check", path="big.py")
    assert "超过自检上限" in out and "没做检查" in out, out
    assert started == []


# ---------------------------------------------------------------- 去重

def test_same_batch_twice_says_still_the_same_then_compresses():
    cache = diagnostics.DiagnosisCache()
    assert cache.note("a.py", "ruff", "fp1", 3) == ""            # 首次:原样全列
    second = cache.note("a.py", "ruff", "fp1", 3)
    assert "仍是上次那 3 条" in second and "没有解决它" in second, second
    third = cache.note("a.py", "ruff", "fp1", 3)
    assert "仍是同一批 3 条" in third, third
    # 换了指纹(真的改了)就重新全列
    assert cache.note("a.py", "ruff", "fp2", 1) == ""


def test_fingerprint_ignores_absolute_paths_but_tracks_the_problem(tmp_path):
    """指纹只随**问题本身**变:同一批错换个目录/换个盘符不该被当成"改了"。"""
    a = diagnostics._norm_line(r"D:\x\ws\a.py:3:1: F401 unused", tmp_path)
    b = diagnostics._norm_line(r"C:\y\other\a.py:3:1: F401 unused", tmp_path)
    assert a == b, (a, b)
    assert "3:1" in a, f"位置信息是问题本身,不许一起剥掉:{a}"
    c = diagnostics._norm_line(r"D:\x\ws\a.py:4:1: F401 unused", tmp_path)
    assert diagnostics.fingerprint([a]) != diagnostics.fingerprint([c])


def test_forget_clears_the_key_so_a_recreated_file_starts_clean():
    cache = diagnostics.DiagnosisCache()
    cache.note("a.py", "ruff", "fp1", 2)
    cache.forget("a.py")
    assert cache.note("a.py", "ruff", "fp1", 2) == ""


# ---------------------------------------------------------------- 工具形状

def test_tool_shape_is_check_or_list_and_is_not_read_only():
    from minicode.tools.registry import build_default_registry
    tool = next(t for t in build_default_registry() if t.name == "diagnostics")
    # 会执行工作区里的检查器(cargo check 会跑 build.rs)—— 必须过确认门
    assert tool.read_only is False
    assert set(tool.parameters["properties"]) == {"action", "path", "reason"}
    assert tool.parameters["properties"]["action"]["enum"] == ["check", "list"]


def test_list_says_what_is_missing_instead_of_pretending(ws, monkeypatch):
    monkeypatch.setattr(diagnostics, "_resolve_prefix",
                        lambda checker, workspace: (None, "本机没有它"))
    out = diagnostics.diagnostics(_ctx(ws), action="list")
    assert "不可用" in out and "本机没有它" in out, out
    assert "语法底线" in out, "必须说清那条不可关的底线不在这张表里、也不会因此消失"


# ---------------------------------------------------------------- 真跑一遍

def test_automatic_feedback_reaches_the_model_after_an_edit(config, ctx, monkeypatch):
    """走真 `run_turn`:改完 .py 之后,检查器报出的问题**真进了工具结果**。"""
    monkeypatch.setattr(diagnostics, "run_checker", lambda workspace, rel, checker: {
        "ok": False, "ran": True, "items": [f"{rel}:2:5: F821 Undefined name `nope`"],
        "total": 1, "note": "", "scope": checker.scope_note})
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "a.py", "content": "x = 1\nprint(nope)\n"}))]),
        AssistantTurn(text="已修"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("写个脚本")
    feedback = agent.session.messages[-2]["content"]
    assert "F821" in feedback and "诊断" in feedback, feedback


def test_automatic_feedback_is_silent_when_there_is_nothing_to_report(config, ctx, monkeypatch):
    """**成功就静默** —— 与「写好的不噪声」同一条口径,否则每次编辑都多一行废话。"""
    monkeypatch.setattr(diagnostics, "run_checker", lambda workspace, rel, checker: {
        "ok": True, "ran": True, "items": [], "total": 0, "note": "",
        "scope": checker.scope_note})
    llm = FakeLLM([
        AssistantTurn(tool_calls=[ToolCall(
            id="c1", name="write_file",
            arguments=json.dumps({"path": "a.py", "content": "x = 1\n"}))]),
        AssistantTurn(text="完成"),
    ])
    agent = make_agent(config, ctx, llm, FakeUI())
    agent.run_turn("写个脚本")
    feedback = agent.session.messages[-2]["content"]
    assert "诊断" not in feedback, feedback


def test_missing_checker_is_announced_once_per_agent_not_every_edit(config, ctx, monkeypatch):
    """检查器不在时每个 agent 只说一次:一次不说是静默没检查,每次都说是噪声。"""
    monkeypatch.setattr(diagnostics, "run_checker", lambda workspace, rel, checker: {
        "ok": False, "ran": False, "items": [], "note": "本机没有 ruff",
        "scope": checker.scope_note})
    (ctx.workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = type("T", (), {"name": "write_file"})()
    args = {"path": "a.py"}
    first = agent._diagnostics_feedback(tool, args, "已写入 a.py")
    second = agent._diagnostics_feedback(tool, args, "已写入 a.py")
    assert "本机没有 ruff" in first, first
    assert second == "已写入 a.py", f"第二次不该再说一遍:{second}"


def test_unknown_extension_is_silent_on_the_automatic_path(config, ctx):
    """自动路径对没有检查器的类型不吭声(否则每次编辑 .md 都附一句废话)。"""
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = type("T", (), {"name": "write_file"})()
    assert agent._diagnostics_feedback(tool, {"path": "a.md"}, "已写入") == "已写入"


def test_non_edit_tools_are_never_diagnosed(config, ctx, monkeypatch):
    """只有落盘 .py 的那两只走这条路 —— 与语法底线同一个工具集,不另开一套。"""
    called: list = []
    monkeypatch.setattr(diagnostics, "run_checker",
                        lambda *a: called.append(a) or {"ok": True, "ran": True,
                                                        "items": [], "total": 0,
                                                        "note": "", "scope": ""})
    agent = make_agent(config, ctx, FakeLLM([]), FakeUI())
    tool = type("T", (), {"name": "read_file"})()
    assert agent._diagnostics_feedback(tool, {"path": "a.py"}, "内容") == "内容"
    assert called == []
