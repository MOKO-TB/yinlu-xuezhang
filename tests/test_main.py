"""CLI 斜杠命令的回归测试:每个命令都必须能跑通(不能有 NameError 之类)。"""
import re
import types
from pathlib import Path

import pytest

from main import handle_slash, unmanagle_slash_command
from minicode import config as config_mod
from minicode.config import Config
from minicode.session import Session
from minicode.tools.base import ToolContext


def test_unmangles_msys_paths():
    assert unmanagle_slash_command(["D:/git/Git/history"]) == "/history"
    assert unmanagle_slash_command(["C:/Program Files/Git/undo"]) == "/undo"
    assert unmanagle_slash_command(["C:\\Program Files\\Git\\resume"]) == "/resume"


def test_leaves_normal_tasks_alone():
    assert unmanagle_slash_command(["/model"]) == "/model"
    assert unmanagle_slash_command(["修复", "pytest", "报错"]) == "修复 pytest 报错"
    assert unmanagle_slash_command(["运行", "model"]) == "运行 model"
    assert unmanagle_slash_command(["优化", "agent.py", "的性能"]) == "优化 agent.py 的性能"


class FakeUI:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.answer = True              # confirm_destructive 的答复(默认点头)
        self.asks: list[str] = []       # 每次都问了什么(动手面有没有问过)
        self.previews: list[str] = []   # 问之前摆出来的那一屏内容

    def info(self, text): self.messages.append(("info", text))
    def error(self, text): self.messages.append(("error", text))
    def help_text(self): self.messages.append(("help", ""))

    def confirm_destructive(self, detail, question="确定?"):
        self.asks.append(question)
        self.previews.append(detail)      # 点头之前那一屏:断言它才断言到"看得见什么"
        return self.answer


class FakeAgent:
    """只用到 ctx.workspace / undo / 上下文估算的最小替身(空栈那一侧)。"""

    def __init__(self, workspace):
        self.ctx = ToolContext(workspace=workspace, config=Config())
        # 真 Agent 一定带 gate,gate 一定带 project(PermissionGate.__init__ 无条件装载);
        # 替身缺这面,/permission 会在测试里走到真代码走不到的分支。
        self.gate = types.SimpleNamespace(project=None)
        self.tools_tokens = 1234       # /history 要能把工具定义那份占用单独报出来

    def undo(self, seq=None): return f"已撤销 {seq or '最近一步'}"

    # /undo 那一族读的就是这三份;替身照真空栈的形状回(真语义在 tests/test_agent.py 钉)
    def undo_records(self):
        return {"ok": True, "items": [], "total": 0, "more": 0,
                "note": "只列我用 write_file / edit_file 改的文件",
                "hint": "没有可退的改动 —— 这份栈在 /clear 与换会话时会清空;"}

    def undo_diff(self, seq):
        return {"ok": False, "msg": f"清单可能已过期:这一处({seq})不在栈里了。"}

    def undo_all(self): return "没有可撤销的文件改动(bash 产生的改动不在跟踪范围)。"

    def compact_now(self): return None

    def context_tokens(self): return 5000 + self.tools_tokens


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CREDENTIALS_PATH",
                        tmp_path / "home" / ".yinluxs" / "credentials.json")
    return tmp_path, FakeUI(), FakeAgent(tmp_path)


SILENT_COMMANDS = ("/clear", "/compact")   # 只改状态,不往 UI 打消息


@pytest.mark.parametrize("cmd", [
    "/help", "/clear", "/model", "/history", "/undo", "/compact",    "/sandbox", "/sandbox off", "/memory", "/memory user",
    "/remember 喜欢先给结论", "/dream", "/dream all",
    "/skills", "/kb", "/plan", "/nothing-like-this",
    "/find", "/find 三体", "/permission", "/permission full",
    # v0.9.96 之前这三条根本不在清单里:/library 与 /usage 改坏了也没人红
    "/library", "/library 活着", "/usage",
    # v0.9.103 ①R4:/undo 长成了一族,每一条都要能跑(空栈时不许伸手要点头)
    "/undo list", "/undo last", "/undo 1", "/undo diff 1", "/undo diff",
    "/undo all", "/undo 乱写", "/instructions",
    "/rewind 1", "/rewind 1 talk", "/rewind 9", "/rewind 1 全部",
])
def test_slash_commands_do_not_crash(env, cmd):
    """重构 handle_slash 时漏传 workspace,曾让 /memory /plan /skills /kb 直接 NameError。"""
    _workspace, ui, agent = env
    session = Session(system_prompt="测试用系统提示")
    assert handle_slash(cmd, session, Config(), ui, agent) is False
    if cmd not in SILENT_COMMANDS:
        assert ui.messages, f"{cmd} 没有任何输出"


def test_slash_exit_returns_true(env):
    _workspace, ui, agent = env
    assert handle_slash("/exit", Session(system_prompt="x"), Config(), ui, agent) is True


def test_history_reports_the_same_ruler_the_compact_trigger_uses(env):
    """/history 说的"约多少 token"必须含工具定义 —— 否则阈值看着很远,却会突然压。"""
    _workspace, ui, agent = env
    session = Session(system_prompt="测试用系统提示")
    session.add_user("在吗")
    handle_slash("/history", session, Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert f"{agent.context_tokens()} tokens" in text
    assert "工具定义 1234 tokens" in text


def test_find_prints_hits_without_leaking_the_sessions_path(env, monkeypatch):
    """/find 与模型侧 search_history 共用 history_hits:终端这条要真能搜到自己说过的话,
    但留档绝对路径不该打到屏幕上(那是家目录)。"""
    import json

    from minicode import session as session_mod
    _workspace, ui, agent = env
    logs = _workspace / "sessions"
    logs.mkdir()
    (logs / "session_20260917_2130.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"type": "session_start", "time": "2026-09-17 21:30", "workspace": str(_workspace)},
        {"role": "user", "content": "三体这本书在图书馆哪里"},
        {"role": "assistant", "content": "三楼社科阅览室"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", logs)

    handle_slash("/find 三体", Session(system_prompt="x"), Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert "2026-09-17 21:30" in text and "图书馆" in text
    assert str(logs) not in text, "留档绝对路径不该出现在终端"


def test_dream_command_reports_result(env):
    _workspace, ui, agent = env
    workspace = agent.ctx.workspace
    from minicode.memory import MemoryEntry, memory_path, save_to
    save_to(memory_path(workspace), [MemoryEntry("2026-01-01", "project", "同一条"),
                                     MemoryEntry("2026-01-02", "project", "同一条")])
    handle_slash("/dream", Session(system_prompt="x"), Config(), ui, agent)
    text = "\n".join(t for _kind, t in ui.messages)
    assert "整理完成" in text and "2 条 → 1 条" in text


def test_permission_view_reports_the_project_whitelist_state(env):
    """/permission 要看得见项目白名单的现状(生效/未批准/没有),且命令行不给批准入口。"""
    from minicode import permspec
    _workspace, ui, agent = env
    rules, _problems = permspec.parse_rules(
        [{"tool": "write_file", "path_prefix": "notes/"}], {"write_file"})
    spec = permspec.Spec(workspace=_workspace, sha="ab" * 32, rules=rules)
    agent.gate.project = spec
    handle_slash("/permission", Session(system_prompt="x"), Config(), ui, agent)
    text = chr(10).join(t for _k, t in ui.messages)
    assert "1 条**未生效**" in text and "在对话里说" in text
    spec.trusted = True
    handle_slash("/permission", Session(system_prompt="x"), Config(), ui, agent)
    assert "1 条**已生效**" in chr(10).join(t for _k, t in ui.messages)
    # 命令行不留 trust/revoke:那条路必须走确认框,绕过它等于给自己发通行证
    ui.messages.clear()
    handle_slash("/permission trust", Session(system_prompt="x"), Config(), ui, agent)
    assert any(k == "error" for k, _t in ui.messages), "trust 不该被当成档位接受"


def test_permission_view_survives_a_workspace_without_a_file(env):
    """没有 permissions.json 也要说一句"无",而不是静默少一行让用户以为没实现。"""
    from minicode import permspec
    _workspace, ui, agent = env
    agent.gate.project = permspec.load(_workspace, set())
    handle_slash("/permission", Session(system_prompt="x"), Config(), ui, agent)
    assert "项目白名单:无" in chr(10).join(t for _k, t in ui.messages)


# -------------------------------- /library 带书名 = 直接搜书(v0.9.97)


def test_library_without_a_query_still_reports_health(env, tmp_path):
    tmp_path, ui, agent = env
    handle_slash("/library", Session(system_prompt="x"), Config(), ui, agent)
    out = " ".join(m[1] for m in ui.messages)
    assert "馆藏库状态" in out


def test_library_with_a_query_searches_through_the_tool_handler(env, tmp_path, monkeypatch):
    """搜书必须走工具那个 handler:同一份检索、同一句"没查到"、同一个坏库口径。"""
    from minicode import library as lib

    calls = []
    real = lib.library_search

    def spy(ctx, query, **kw):
        calls.append((ctx.workspace, query))
        return real(ctx, query, **kw)

    monkeypatch.setattr(lib, "library_search", spy)
    tmp_path, ui, agent = env
    handle_slash("/library 活着", Session(system_prompt="x"), Config(), ui, agent)
    assert calls == [(tmp_path, "活着")], calls
    out = " ".join(m[1] for m in ui.messages)
    assert "馆藏" in out or "本" in out        # 没库时也是工具自己那句实话,不是崩栈


def test_history_says_which_ruler_it_used(env, tmp_path):
    """`/history` 报的数要说得出来源:实测顶上来的 vs 本地估的,是两种可信度。"""
    tmp_path, ui, agent = env
    agent.context_source = lambda: "real"
    handle_slash("/history", Session(system_prompt="x"), Config(), ui, agent)
    assert "服务端真返回" in " ".join(m[1] for m in ui.messages)

    ui2 = FakeUI()
    agent2 = FakeAgent(tmp_path)
    agent2.context_source = lambda: "estimated"
    handle_slash("/history", Session(system_prompt="x"), Config(), ui2, agent2)
    assert "本地估算" in " ".join(m[1] for m in ui2.messages)


def test_history_survives_an_agent_without_a_source_label(env, tmp_path):
    """替身没有 context_source 也不能崩(这条路径在测试桩与旧装配里都会走到)。"""
    tmp_path, ui, agent = env
    assert not hasattr(agent, "context_source")
    handle_slash("/history", Session(system_prompt="x"), Config(), ui, agent)
    assert any(m[0] == "info" for m in ui.messages)


# ------------------------------------------- 自定义斜杠命令(F2,v0.9.100)

def test_workspace_commands_scanned_and_override_builtin(tmp_path):
    from minicode import slash_commands as sc
    d = tmp_path / ".commands"
    d.mkdir()
    (d / "周报.md").write_text(
        "---\ndescription: 帮我写周报\n---\n请按下面的要点写周报:\n$ARGS", encoding="utf-8")
    rows = sc.workspace_commands(tmp_path)
    assert rows[0]["cmd"] == "周报" and "$ARGS" in rows[0]["prompt"]
    # 工作区同名覆盖内置(翻译)
    (d / "翻译.md").write_text("---\ndescription: 方言翻译\n---\n$ARGS", encoding="utf-8")
    # expand 只认命令词本身(带内容的展开归 CLI 的 expand_with_args)
    out = sc.expand("/翻译", tmp_path)
    assert out == "$ARGS" and out != sc.expand("/翻译")   # 工作区覆盖内置
    # 无参命令直接展开
    assert "写周报" in sc.expand("/周报", tmp_path)
    assert sc.expand("/不存在", tmp_path) is None


def test_expand_with_args_cli_side(tmp_path):
    from types import SimpleNamespace
    msgs = []
    ui = SimpleNamespace(info=lambda t: msgs.append(t))
    from main import expand_with_args
    d = tmp_path / ".commands"
    d.mkdir()
    (d / "周报.md").write_text("---\ndescription: x\n---\n要点:\n$ARGS", encoding="utf-8")
    # 带内容:替换进 $ARGS
    out = expand_with_args("/周报 本周做了登录页", tmp_path, ui)
    assert out is not None and "本周做了登录页" in out and "$ARGS" not in out
    # 不带内容:提示后不展开
    msgs.clear()
    assert expand_with_args("/周报", tmp_path, ui) is None
    assert msgs and "要带内容" in msgs[0]
    # 无参命令直接发
    assert expand_with_args("/总结", tmp_path, ui) is not None


def test_slash_commands_payload_carries_custom(tmp_path):
    from test_gui_bridge import FakeLLM
    from minicode.llm import AssistantTurn
    from test_gui_bridge import make
    d = tmp_path / ".commands"
    d.mkdir()
    (d / "周报.md").write_text("---\ndescription: 帮我写周报\n---\n$ARGS", encoding="utf-8")
    llm = FakeLLM([AssistantTurn(text="ok")])
    bridge, _w, _s = make(_cfg(), tmp_path, llm)
    r = bridge.get_slash_commands()
    cmds = {c["cmd"]: c for c in r["commands"]}
    # 载荷里没有 custom 这个键(前端不读它,所以桥没带),这条只能断到"并进了同一张表"
    assert "周报" in cmds, "工作区自定义命令要并进内置那一张,不是另开一份"
    assert cmds["周报"]["desc"] == "帮我写周报", "frontmatter 那句说明要真带出来"
    assert cmds["周报"]["has_arg"] is True          # $ARGS 占位算出来的,不是写死的


def _cfg():
    from minicode.config import Config, Provider
    return Config(providers=[Provider(id="t", name="T", base_url="https://x/v1",
                                      api_key="k", model="m")],
                  active_provider_id="t")


# ---------------- ①R4 之后补的闸:命令表只有一份,不许再有人手抄
#
# 抄过的三处各漏了几条:补全里少 /find /permission /think /workspace(打出来补不出
# 建议),「未知命令」那句里少 /workspace(能跑却不说),README 的命令行少 /usage
# 之外的两条。补全与提示都从 ui.SLASH_COMMANDS 派生,这几条针把"派生"钉成事实。

ROOT = Path(__file__).resolve().parent.parent


def _dispatched_commands() -> set:
    """main.py 真分发的命令名(只认比较式,不认注释与帮助文字里的)。"""
    import main as main_mod
    src = Path(main_mod.__file__).read_text(encoding="utf-8")
    found = set(re.findall(r'name == "(/[a-z]+)"', src))
    for group in re.findall(r'name in \(([^)]*)\)', src):
        found |= set(re.findall(r'"(/[a-z]+)"', group))
    found |= set(re.findall(r'startswith\("(/[a-z]+)"', src))
    return found


def test_command_table_covers_everything_main_dispatches():
    from minicode.ui import SLASH_COMMANDS
    dispatched = _dispatched_commands()
    assert dispatched, "解析没抓到命令词 —— 这条闸自己坏了,不算绿"
    assert dispatched - set(SLASH_COMMANDS) == set(), "分发了一条表外的命令"
    assert set(SLASH_COMMANDS) - dispatched == set(), "表里有条命令没人分发"


def test_slash_names_are_derived_not_recopied():
    import main as main_mod
    from minicode.ui import SLASH_COMMANDS
    assert main_mod.SLASH_COMMANDS is SLASH_COMMANDS, "main 该用那份表,不是再造一张"
    assert main_mod.SLASH_NAMES == frozenset(SLASH_COMMANDS)


def test_completer_offers_the_whole_table(tmp_path):
    """补全必须给整张表:能跑却补不出来,人就永远不知道有这条命令。"""
    from minicode.config import Config
    from minicode.ui import SLASH_COMMANDS, TerminalUI
    ui = TerminalUI(Config(), tmp_path)
    assert set(ui._completer.words) == set(SLASH_COMMANDS)


def test_unknown_command_hint_shows_the_whole_table(env):
    """那句「可用:」是表本身拼出来的,不是第四份手抄。"""
    from minicode.ui import SLASH_COMMANDS
    _workspace, ui, agent = env
    handle_slash("/nothing-like-this", Session(system_prompt="x"), Config(), ui, agent)
    err = " ".join(t for k, t in ui.messages if k == "error")
    assert all(cmd in err for cmd in SLASH_COMMANDS), \
        sorted(set(SLASH_COMMANDS) - {c for c in SLASH_COMMANDS if c in err})


def test_every_command_has_a_help_line():
    from minicode import ui as ui_mod
    from minicode.ui import SLASH_COMMANDS
    body = Path(ui_mod.__file__).read_text(encoding="utf-8").split("def help_text")[1]
    documented = set(re.findall(r'"  (/[a-z]+)', body.split("def compacted")[0]))
    # /quit 是 /exit 的同义别名,不单独占一行说明
    assert set(SLASH_COMMANDS) - documented == {"/quit"}


def test_readme_cli_line_lists_every_command():
    """包外同学先读 README:命令行漏一条,那条命令就等于没做过。"""
    from minicode.ui import SLASH_COMMANDS
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("CLI 命令")
    block = readme[start:start + 1200].split("\n\n")[0]      # 那一段跨三行
    missing = {cmd for cmd in SLASH_COMMANDS if cmd not in block}
    assert missing == {"/quit"}, f"README 命令行缺:{missing}"
#
# 这一族全部用**真 Agent + 真 write_file**:栈、diff、退下去改的是磁盘上哪个字节,
# 都得是真的;替身只能证明"格式化没崩",证明不了"退完文件真的回去了"。

@pytest.fixture
def written(config, ctx):
    """同一份文件被改两次:栈里两条(早的那条是「新建」,晚的是「改」)。"""
    from test_agent import FakeLLM, FakeUI as AgentUI, _write_turn, make_agent
    from minicode.llm import AssistantTurn
    llm = FakeLLM([
        _write_turn("doc.txt", "v1", "c1"), AssistantTurn(text="d1"),
        _write_turn("doc.txt", "v2", "c2"), AssistantTurn(text="d2"),
    ])
    agent = make_agent(config, ctx, llm, AgentUI())
    agent.run_turn("第一版")
    agent.run_turn("第二版")
    return agent, ctx.workspace / "doc.txt"


def _slash(cmd, agent, ui=None):
    ui = ui or FakeUI()
    handle_slash(cmd, agent.session, agent.ctx.config, ui, agent)
    return ui, "\n".join(t for _k, t in ui.messages)


def test_cli_undo_lists_every_change_and_touches_nothing(written):
    agent, doc = written
    assert doc.read_text(encoding="utf-8") == "v2"
    ui, out = _slash("/undo", agent)
    assert "本轮改动 2 处" in out and "doc.txt" in out
    assert "还有 1 处没退" in out                 # 早那条带着"上面还有几处"的旗
    assert "命令(bash / py_script)" in out         # 这份账的边界必须说出口
    assert ui.asks == [], "只是列清单,不该伸手要点头"
    assert doc.read_text(encoding="utf-8") == "v2"


def test_cli_undo_asks_first_and_a_no_means_disk_untouched(written):
    agent, doc = written
    ui = FakeUI()
    ui.answer = False
    _slash("/undo last", agent, ui)
    assert ui.asks == ["退回这一步?"]
    assert "已取消" in " ".join(t for _k, t in ui.messages)
    assert doc.read_text(encoding="utf-8") == "v2"
    assert agent.undo_records()["total"] == 2, "没退成的那条不许凭空消失"


def test_cli_undo_last_reverts_after_the_nod(written):
    agent, doc = written
    ui, out = _slash("/undo last", agent)
    assert ui.asks == ["退回这一步?"]
    assert "已撤销" in out
    assert doc.read_text(encoding="utf-8") == "v1"


def test_cli_undo_by_seq_only_touches_that_one(written):
    agent, doc = written
    seq = agent.undo_records()["items"][0]["seq"]        # 最新那条(改 → 恢复)
    _slash(f"/undo {seq}", agent)
    assert doc.read_text(encoding="utf-8") == "v1"
    assert agent.undo_records()["total"] == 1


def test_cli_undo_oldest_seq_deletes_the_file_it_created(written):
    """早那条的「退下去=删除」必须在清单里写明白 —— 它不是恢复,是删文件。"""
    agent, doc = written
    older = agent.undo_records()["items"][1]
    assert older["kind"] == "新建" and older["verb"] == "删除"
    _slash(f"/undo {older['seq']}", agent)
    assert not doc.exists()


def test_cli_undo_diff_shows_both_sides_without_writing(written):
    agent, doc = written
    seq = agent.undo_records()["items"][0]["seq"]
    ui, out = _slash(f"/undo diff {seq}", agent)
    assert "@@" in out and "-v1" in out and "+v2" in out
    assert "磁盘上的现在" in out                   # 两侧各是什么,话说在前面
    assert ui.asks == [] and doc.read_text(encoding="utf-8") == "v2"


def test_cli_undo_all_rolls_the_whole_stack(written):
    agent, doc = written
    ui, out = _slash("/undo all", agent)
    assert ui.asks == ["全部回退?"]
    assert "已全部回退 2 处" in out
    assert not doc.exists()                        # 退到最早:那之前这文件还不存在


def test_cli_undo_stale_seq_asks_for_no_nod(written):
    """编号算不出 diff 就当没这回事退出:让人在一个空确认框上点头,比拒绝更坏。"""
    agent, doc = written
    ui, out = _slash("/undo 999", agent)
    assert ui.asks == []
    assert "什么都没退" in out
    assert doc.read_text(encoding="utf-8") == "v2"


# ---------------------------------------- /rewind:退到第 N 问之前(v0.9.105)
#
# 与 GUI 的「编辑重发 + 退回文件」共用同一套坐标(R3)与同一份筛选
# (`Agent._rollback_scope`),所以这里断的是**命令行这一侧的编排**:
# 先退文件再截对话、退不成就不截、不点头什么都不动。


def _two_questions(config, ctx):
    """两问、两版:第 2 问把 doc.txt 从 v1 改成 v2。"""
    from test_agent import FakeLLM, FakeUI as AgentUI, _write_turn, make_agent
    from minicode.llm import AssistantTurn
    llm = FakeLLM([
        _write_turn("doc.txt", "v1", "c1"), AssistantTurn(text="d1"),
        _write_turn("doc.txt", "v2", "c2"), AssistantTurn(text="d2"),
    ])
    agent = make_agent(config, ctx, llm, AgentUI())
    agent.run_turn("第一问:写个初稿")
    agent.run_turn("第二问:改成 v2")
    return agent


def test_rewind_rolls_files_and_transcript_after_the_nod(config, ctx):
    agent = _two_questions(config, ctx)
    doc = ctx.workspace / "doc.txt"
    before = len(agent.session.messages)
    assert doc.read_text(encoding="utf-8") == "v2"
    ui = FakeUI()
    handle_slash("/rewind 2", agent.session, config, ui, agent)
    assert ui.asks == ["退到第 2 问之前?"], ui.asks
    asked = "\n".join(ui.previews)
    assert "第二问:改成 v2" in asked, "点头之前必须看见那一问是什么"
    assert "退 1 处" in asked and "doc.txt" in asked
    assert "命令与外部程序改的文件从来不在账里" in asked, "边界要在点头前说,不是退完才说"
    assert doc.read_text(encoding="utf-8") == "v1"
    assert len(agent.session.messages) < before      # 对话也截了


def test_rewind_without_the_nod_leaves_both_alone(config, ctx):
    agent = _two_questions(config, ctx)
    doc = ctx.workspace / "doc.txt"
    before = len(agent.session.messages)
    ui = FakeUI()
    ui.answer = False
    handle_slash("/rewind 2", agent.session, config, ui, agent)
    assert ui.asks == ["退到第 2 问之前?"]
    assert doc.read_text(encoding="utf-8") == "v2"
    assert len(agent.session.messages) == before
    assert "都没动" in " ".join(t for _k, t in ui.messages)


def test_rewind_files_only_keeps_the_transcript(config, ctx):
    agent = _two_questions(config, ctx)
    doc = ctx.workspace / "doc.txt"
    before = len(agent.session.messages)
    handle_slash("/rewind 2 files", agent.session, config, FakeUI(), agent)
    assert doc.read_text(encoding="utf-8") == "v1"
    assert len(agent.session.messages) == before, "只退文件就不该顺手截对话"


def test_rewind_talk_only_leaves_the_files_on_disk(config, ctx):
    """只截对话时文件**照旧留着** —— 这格要能单独选,不然"我想留着代码重聊"做不到。"""
    agent = _two_questions(config, ctx)
    doc = ctx.workspace / "doc.txt"
    before = len(agent.session.messages)
    handle_slash("/rewind 2 talk", agent.session, config, FakeUI(), agent)
    assert doc.read_text(encoding="utf-8") == "v2"
    assert len(agent.session.messages) < before


def test_rewind_after_a_compaction_refuses_and_truncates_nothing(config, ctx):
    """纪元对不上时**两半都不做**:截了对话而文件退不了,是最坏的一半。"""
    agent = _two_questions(config, ctx)
    agent._epoch += 1                      # 模拟"中间又压过一次历史"
    doc = ctx.workspace / "doc.txt"
    before = len(agent.session.messages)
    ui = FakeUI()
    handle_slash("/rewind 2", agent.session, config, ui, agent)
    assert ui.asks == [], "对不上号时连确认都不该问 —— 问了一年要人点头的假事"
    assert doc.read_text(encoding="utf-8") == "v2"
    assert len(agent.session.messages) == before
    assert "我不敢按号退" in " ".join(t for _k, t in ui.messages)


def test_rewind_names_a_number_that_is_not_there(config, ctx):
    agent = _two_questions(config, ctx)
    doc = ctx.workspace / "doc.txt"
    ui = FakeUI()
    handle_slash("/rewind 9", agent.session, config, ui, agent)
    assert ui.asks == []
    assert "不在当前上下文里" in " ".join(t for _k, t in ui.messages)
    assert doc.read_text(encoding="utf-8") == "v2"


def test_rewind_rejects_an_unknown_scope_with_the_menu(config, ctx):
    agent = _two_questions(config, ctx)
    ui = FakeUI()
    handle_slash("/rewind 2 全部", agent.session, config, ui, agent)
    out = " ".join(t for _k, t in ui.messages)
    assert "没这个范围" in out and "files" in out and "talk" in out
    assert ui.asks == []


def test_rollback_preview_and_the_real_rollback_never_disagree(config, ctx):
    """预览说几处,动手就退几处 —— 两处各筛一遍总有一天一处骗人。"""
    agent = _two_questions(config, ctx)
    prev = agent.rollback_preview(2)
    assert prev["ok"] and prev["count"] == 1 and prev["paths"] == ["doc.txt"]
    assert agent.rollback_to_ordinal(2)["msg"].count("doc.txt") == 1
    # 编号合法性也是同一把尺:preview 不许比动手宽松
    assert agent.rollback_preview(0)["ok"] is False
    assert agent.rollback_preview("x")["ok"] is False
    assert agent.rollback_preview(True)["ok"] is False


# ---------------- ④L3 的终端形态:这一轮到底读到了哪几层指令(v0.9.103)


def test_cli_instructions_reports_both_layers_without_leaking_content(tmp_path,
                                                                      monkeypatch):
    from minicode import app_setup
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(app_setup, "app_path", lambda *p: home.joinpath(*p))
    (home / "AGENTS.md").write_text("先给结论\n再给理由\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("测试要跑 pytest\n", encoding="utf-8")

    ui = FakeUI()
    handle_slash("/instructions", Session(system_prompt="x"), Config(), ui,
                 FakeAgent(ws))
    out = "\n".join(t for _k, t in ui.messages)
    assert "用户级 AGENTS.md:2 行" in out
    assert "项目级 AGENTS.md:1 行" in out
    assert app_setup.INSTRUCTION_GUARD in out      # 红线改不动那句必须跟着报
    assert "先给结论" not in out, "这一屏只报读到几行,不重贴内容(两处真相)"


def test_cli_instructions_says_so_when_nothing_was_written(tmp_path):
    ui = FakeUI()
    handle_slash("/instructions", Session(system_prompt="x"), Config(), ui,
                 FakeAgent(tmp_path))
    out = "\n".join(t for _k, t in ui.messages)
    assert "用户级、项目级都没读到" in out and "/init" in out


def test_cli_instructions_reports_a_truncated_layer(tmp_path, monkeypatch):
    """被截了要说"截了多少":静默吃掉后半句规矩,比不注入更坏。"""
    from minicode import app_setup
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(app_setup, "app_path", lambda *p: home.joinpath(*p))
    (home / "AGENTS.md").write_text("字" * (app_setup.AGENTS_CHAR_LIMIT + 40),
                                    encoding="utf-8")
    ui = FakeUI()
    handle_slash("/instructions", Session(system_prompt="x"), Config(), ui,
                 FakeAgent(tmp_path))
    assert "只注入了前面那一截" in " ".join(t for _k, t in ui.messages)


# ------------------------------------------- 讲解模式(Z1,v0.9.104)

def test_style_toggles_and_persists(env, tmp_path):
    """/style guide 切换并落盘;非法值报当前风格不乱切。"""
    tmp_path, ui, agent = env
    handle_slash("/style guide", Session(system_prompt="s", log_dir=tmp_path / "s",
                                         workspace=tmp_path),
                 agent.ctx.config, ui, agent)
    assert agent.ctx.config.reply_style == "guide"
    ui.messages.clear()
    handle_slash("/style 不知道什么", Session(system_prompt="s", log_dir=tmp_path / "s",
                                             workspace=tmp_path),
                 agent.ctx.config, ui, agent)
    assert any("guide" in t for _k, t in ui.messages), "非法值要报当前风格与用法"
    assert agent.ctx.config.reply_style == "guide", "非法值不许改变现状"
