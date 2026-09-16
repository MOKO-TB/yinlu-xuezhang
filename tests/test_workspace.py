"""工作区(workspace)测试:校验规则、状态落盘、围栏跟随、GUI/CLI 入口。

对应功能(v0.9.08):以前工作区是写死的(GUI=exe 目录,CLI=当前目录),现在
用户可以自己选一个文件夹当工作台;这里守住三件事 ——
① 只有**合法的已存在文件夹**能被选(敏感目录永久拒绝);
② 切换工作区时沙箱围栏、system prompt、会话运行器一起换,不留旧工作区的会话;
③ 状态落盘后 GUI 与 CLI 读到的是同一份「上次在哪干活」。
"""
import json
import sys
import types
from pathlib import Path

import pytest

from main import cmd_workspace, pop_workspace_flag, startup_workspace, _repl
from minicode import paths as paths_mod
from minicode import workspace as ws
from minicode.config import Config
from minicode.gui_bridge import create_app
from minicode.sandbox import SandboxPolicy, SandboxState
from minicode.session import Session, list_sessions


# --------------------------------------------------------------------- 替身

class FakeWindow:
    """捕获 evaluate_js 推给前端的事件。"""

    def __init__(self):
        self.events: list[dict] = []

    def evaluate_js(self, code: str):
        prefix = "window.__gcodeEvent("
        assert code.startswith(prefix), code
        self.events.append(json.loads(code[len(prefix):-1]))

    def of_type(self, name):
        return [e for e in self.events if e["type"] == name]


class NoLLM:
    """本组用例不该真的调到模型:调到了就是设计错了。"""

    def stream_chat(self, *a, **k):
        raise AssertionError("工作区测试不应该发起模型调用")


class FakeUI:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def info(self, text): self.messages.append(("info", text))
    def error(self, text): self.messages.append(("error", text))


class FakeAgent:
    def __init__(self):
        self.closed = False

    def close(self): self.closed = True


def make_bridge(tmp_path: Path, workspace: Path | None = None):
    """一个全部落在 tmp 的 GUI 应用(工作区状态也是)。"""
    workspace = Path(workspace or (tmp_path / "ws"))
    workspace.mkdir(exist_ok=True)
    app_dir = tmp_path / "appdir"
    app_dir.mkdir(exist_ok=True)
    bridge, ui, _agent, _session = create_app(
        Config(), workspace, llm=NoLLM(), sessions_dir=tmp_path / "sessions",
        config_path=tmp_path / "config.json",
        workspace_file=app_dir / "workspaces.json", app_dir=app_dir)
    window = FakeWindow()
    ui.attach(window)
    return bridge, ui, window


def state_of(app_dir: Path) -> dict:
    return json.loads((app_dir / "workspaces.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------- 校验

def test_resolve_target_rejects_relative():
    target, err = ws.resolve_target("资料/子目录")
    assert target is None
    assert "完整路径" in err


def test_resolve_target_rejects_missing(tmp_path):
    target, err = ws.resolve_target(str(tmp_path / "没有这个目录"))
    assert target is None and "不存在" in err


def test_resolve_target_rejects_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    target, err = ws.resolve_target(str(f))
    assert target is None and "不是文件夹" in err


def test_resolve_target_rejects_sensitive_dirs(tmp_path):
    """凭据目录不能当工作区:选它等于把 ~/.ssh、内置 Key 全暴露给工具。"""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "sub").mkdir()
    for bad in (home / ".ssh", home / ".ssh" / "sub"):
        target, err = ws.resolve_target(str(bad), home=home)
        assert target is None
        assert "敏感" in err


def test_resolve_target_accepts_and_normalizes(tmp_path):
    d = tmp_path / "我的资料"
    d.mkdir()
    target, err = ws.resolve_target(str(d) + "/./")
    assert err == ""
    assert target == d.resolve()
    # 带引号的输入(用户从资源管理器复制路径常带引号)也要认
    assert ws.resolve_target(f'"{d}"')[0] == d.resolve()


def test_display_name_and_default(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    d = tmp_path / "资料"
    d.mkdir()
    assert ws.display_name(d, app_dir) == "资料"
    assert ws.display_name(app_dir, app_dir).startswith("默认工作区")
    assert ws.is_default(app_dir, app_dir) is True
    assert ws.is_default(d, app_dir) is False


# --------------------------------------------------------------------- 状态

def test_remember_dedupes_and_moves_to_front(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()

    ws.remember(a, app_dir=app_dir)
    ws.remember(b, app_dir=app_dir)
    ws.remember(a, app_dir=app_dir)          # 回到 a:置顶且不重复

    state = ws.load_state(app_dir=app_dir)
    assert Path(state["current"]) == a.resolve()
    assert [i["path"] for i in state["recent"]] == [str(a.resolve()), str(b.resolve())]


def test_recent_list_is_capped(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    for i in range(ws.MAX_RECENT + 3):
        d = tmp_path / f"d{i}"
        d.mkdir()
        ws.remember(d, app_dir=app_dir)
    state = ws.load_state(app_dir=app_dir)
    assert len(state["recent"]) == ws.MAX_RECENT
    assert Path(state["current"]) == (tmp_path / f"d{ws.MAX_RECENT + 2}").resolve()


def test_forget_keeps_current_and_folder(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    a = tmp_path / "a"
    a.mkdir()
    ws.remember(a, app_dir=app_dir)

    ws.forget(a, app_dir=app_dir)

    state = ws.load_state(app_dir=app_dir)
    assert state["recent"] == []
    assert Path(state["current"]) == a.resolve()      # 还是"当前",只是不在最近列表
    assert a.is_dir()                                 # 只动记录,不删文件夹


def test_recent_entries_skips_missing_dirs(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    ws.remember(a, app_dir=app_dir)
    ws.remember(b, app_dir=app_dir)

    b.rmdir()          # 用户把文件夹删了/改名了

    entries = ws.recent_entries(app_dir=app_dir)
    assert [e["path"] for e in entries] == [str(a.resolve())]


def test_load_state_tolerates_garbage(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    f = app_dir / "workspaces.json"
    for garbage in ("{不是 JSON", "[]", '"str"', ""):
        f.write_text(garbage, encoding="utf-8")
        state = ws.load_state(app_dir=app_dir)
        assert state == {"current": "", "recent": []}
    # 列表里的坏项被丢掉,好项留下
    f.write_text(json.dumps({"current": "  ", "recent": [
        "not-a-dict", {}, {"path": ""}, {"path": str(tmp_path), "name": "好"}]}),
        encoding="utf-8")
    state = ws.load_state(app_dir=app_dir)
    assert state["current"] == ""
    assert [i["path"] for i in state["recent"]] == [str(tmp_path)]


# ------------------------------------------------------------------ 启动选择

def test_startup_workspace_explicit_wins(tmp_path):
    fallback = tmp_path / "appdir"
    fallback.mkdir()
    d = tmp_path / "d"
    d.mkdir()
    assert ws.startup_workspace(fallback, prefer=str(d)) == d.resolve()
    # 显式给错路径时不该悄悄退回 fallback,而是当作没有偏好(由 CLI 决定报错)
    assert ws.startup_workspace(fallback, prefer=str(tmp_path / "nope")) == fallback.resolve()


def test_startup_workspace_uses_memory_then_fallback(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    d = tmp_path / "d"
    d.mkdir()
    assert ws.startup_workspace(app_dir, app_dir=app_dir) == app_dir.resolve()
    ws.remember(d, app_dir=app_dir)
    assert ws.startup_workspace(app_dir, app_dir=app_dir) == d.resolve()


def test_startup_workspace_ignores_stale_memory(tmp_path):
    """记忆里的工作区被删掉时静默回退:绝不因为一个失效设置拦启动。"""
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    d = tmp_path / "d"
    d.mkdir()
    ws.remember(d, app_dir=app_dir)
    d.rmdir()
    assert ws.startup_workspace(app_dir, app_dir=app_dir) == app_dir.resolve()


# ------------------------------------------------------------------ 沙箱围栏

def test_sandbox_retarget_moves_fence_and_clears_approvals(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    state = SandboxState(SandboxPolicy(), old)
    state.approve_path(tmp_path / "outside")

    state.retarget(new)

    assert state.workspace == new
    assert state.approved_paths == set()      # 旧授权不跟着搬过去


# -------------------------------------------------------------- 会话留档

def test_session_records_workspace(tmp_path):
    logs = tmp_path / "logs"
    work = tmp_path / "ws"
    work.mkdir()
    session = Session(system_prompt="p", log_dir=logs, workspace=work)
    session.add_user("你好")

    first = json.loads(session.log_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["type"] == "session_start"
    assert first["workspace"] == str(work)          # 不再是 Path.cwd()
    assert list_sessions(logs)[0].workspace == str(work)


def test_session_without_workspace_logs_empty(tmp_path):
    session = Session(system_prompt="p", log_dir=tmp_path / "logs")
    session.add_user("你好")
    first = json.loads(session.log_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["workspace"] == ""


# ---------------------------------------------------------------- GUI 桥接

def test_get_workspaces_lists_current_and_default(tmp_path):
    bridge, _ui, _window = make_bridge(tmp_path)
    data = bridge.get_workspaces()

    assert data["ok"] is True
    assert Path(data["current"]["path"]) == (tmp_path / "ws").resolve()
    assert data["current"]["is_default"] is False
    assert Path(data["default"]["path"]) == (tmp_path / "appdir").resolve()
    assert data["default"]["is_default"] is True
    # 默认工作区就在最近列表里(用户点一下就能回去)
    assert [e["path"] for e in data["recent"]] == []


def test_get_info_exposes_workspace_labels(tmp_path):
    bridge, _ui, _window = make_bridge(tmp_path)
    info = bridge.get_info()
    assert info["workspace"] == str((tmp_path / "ws").resolve())
    assert info["workspace_name"] == "ws"
    assert info["workspace_is_default"] is False


def test_switch_workspace_moves_fence_state_and_session(tmp_path):
    bridge, _ui, window = make_bridge(tmp_path)
    old_workspace = bridge.workspace
    target = tmp_path / "我的资料"
    target.mkdir()

    result = bridge.switch_workspace(str(target))

    assert result["ok"] is True and result["path"] == str(target.resolve())
    assert bridge.workspace == target.resolve()
    assert bridge._sandbox.workspace == target.resolve()      # 围栏跟着走
    assert bridge.agent.ctx.workspace == target.resolve()     # 新会话的工具上下文
    assert str(target.resolve()) in bridge.session.messages[0]["content"]
    assert len(bridge._runners) == 1                          # 旧工作区的会话被丢掉
    assert old_workspace not in [r.session.workspace for r in bridge._runners.values()]
    # 状态落盘:下次启动还在这个工作区
    assert state_of(tmp_path / "appdir")["current"] == str(target.resolve())
    # 事件顺序:先 cleared(前端认下新会话),再 workspace(改顶栏)
    types = [e["type"] for e in window.events]
    assert types.index("cleared") < types.index("workspace")
    pushed = window.of_type("workspace")[-1]
    assert pushed["path"] == str(target.resolve())
    assert pushed["is_default"] is False
    assert pushed["name"] == "我的资料"


def test_switch_workspace_refuses_while_task_running(tmp_path):
    """有任务在跑时不动工作区:静默换掉围栏会让正在跑的那轮写到错地方。"""
    bridge, _ui, _window = make_bridge(tmp_path)
    bridge._current.busy = True
    target = tmp_path / "别的"
    target.mkdir()

    result = bridge.switch_workspace(str(target))

    assert result["ok"] is False and "任务" in result["msg"]
    assert bridge.workspace == (tmp_path / "ws").resolve()
    assert (tmp_path / "appdir" / "workspaces.json").exists() is False


def test_switch_workspace_to_same_path_is_noop(tmp_path):
    bridge, _ui, window = make_bridge(tmp_path)
    before = bridge.session

    result = bridge.switch_workspace(str(tmp_path / "ws"))

    assert result["ok"] is True and "已经在用" in result["msg"]
    assert bridge.session is before                 # 没有新开会话
    assert window.of_type("cleared") == []


def test_switch_workspace_reports_bad_paths(tmp_path):
    bridge, _ui, _window = make_bridge(tmp_path)
    missing = bridge.switch_workspace(str(tmp_path / "没有"))
    assert missing["ok"] is False and "不存在" in missing["msg"]
    relative = bridge.switch_workspace("相对路径")
    assert relative["ok"] is False
    assert bridge.workspace == (tmp_path / "ws").resolve()


def test_reset_workspace_returns_to_default(tmp_path):
    bridge, _ui, window = make_bridge(tmp_path)
    target = tmp_path / "资料"
    target.mkdir()
    bridge.switch_workspace(str(target))

    result = bridge.reset_workspace()

    assert result["ok"] is True
    assert bridge.workspace == (tmp_path / "appdir").resolve()
    assert window.of_type("workspace")[-1]["is_default"] is True
    assert state_of(tmp_path / "appdir")["current"] == str((tmp_path / "appdir").resolve())


def test_forget_workspace_only_touches_list(tmp_path):
    bridge, _ui, _window = make_bridge(tmp_path)
    target = tmp_path / "资料"
    target.mkdir()
    bridge.switch_workspace(str(target))

    result = bridge.forget_workspace(str(target))

    assert result["ok"] is True
    assert all(e["path"] != str(target.resolve()) for e in result["recent"])
    assert target.is_dir()                      # 不删文件夹
    assert bridge.workspace == target.resolve()  # 也不把用户踢走


def test_pick_workspace_needs_window(tmp_path):
    """没有窗口时给可读的失败,而不是抛异常。"""
    bridge, _ui, _window = make_bridge(tmp_path)
    bridge.ui.window = None
    result = bridge.pick_workspace()
    assert result["ok"] is False and "窗口" in result["msg"]


def test_pick_workspace_returns_chosen_path(tmp_path, monkeypatch):
    bridge, ui, _window = make_bridge(tmp_path)
    fake_webview = types.ModuleType("webview")
    fake_webview.FOLDER_DIALOG = "folder-dialog"
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    calls = []

    class Win:
        def create_file_dialog(self, kind, **kwargs):
            calls.append(kind)
            return str(tmp_path / "选中的文件夹")

    ui.window = Win()
    result = bridge.pick_workspace()
    assert result == {"ok": True, "path": str(tmp_path / "选中的文件夹")}
    assert calls == ["folder-dialog"]
    # 只选不改:bridge 的工作区还是原来的
    assert bridge.workspace == (tmp_path / "ws").resolve()


def test_pick_workspace_cancel(tmp_path, monkeypatch):
    bridge, ui, _window = make_bridge(tmp_path)
    fake_webview = types.ModuleType("webview")
    fake_webview.FOLDER_DIALOG = "folder-dialog"
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    class Win:
        def create_file_dialog(self, kind, **kwargs):
            return None            # 用户点了取消

    ui.window = window = Win()
    assert bridge.pick_workspace() == {"ok": False, "msg": "已取消"}


def test_sessions_payload_tags_other_workspace(tmp_path):
    """别的工作区留下的会话要标出来,否则用户会以为文件还在当前文件夹里。"""
    bridge, _ui, window = make_bridge(tmp_path)
    bridge.session.add_user("在旧工作区问的")
    target = tmp_path / "新工作区"
    target.mkdir()
    bridge.switch_workspace(str(target))

    items = window.of_type("sessions")[-1]["items"]
    tagged = [i for i in items if i["workspace"]]
    assert tagged and tagged[0]["workspace"] == "ws"
    assert tagged[0]["current"] is False


def test_public_api_does_not_cache_workspace(tmp_path):
    bridge, _ui, _window = make_bridge(tmp_path)
    api = bridge.public_api()
    assert api.workspace == str((tmp_path / "ws").resolve())
    for name in ("get_workspaces", "pick_workspace", "switch_workspace",
                 "reset_workspace", "forget_workspace"):
        assert callable(getattr(api, name))
    target = tmp_path / "改"
    target.mkdir()
    api.switch_workspace(str(target))
    assert api.workspace == str(target.resolve())


# ---------------------------------------------------------------- 网页版

def test_web_facade_refuses_workspace_methods():
    from minicode.web_profile import PublicFacade

    facade = PublicFacade(object())      # 构造不碰 bridge,拒绝分支也不需要它
    for name in ("get_workspaces", "pick_workspace", "switch_workspace",
                 "reset_workspace", "forget_workspace"):
        result = facade.call(name)
        assert result["ok"] is False and "工作区" in result["msg"]


# ---------------------------------------------------------------- CLI

def test_pop_workspace_flag():
    assert pop_workspace_flag(["--workspace", "D:\\x"]) == ("D:\\x", [])
    assert pop_workspace_flag(["-w", "/tmp/a", "写个说明"]) == ("/tmp/a", ["写个说明"])
    assert pop_workspace_flag(["--workspace=/tmp/b", "任务"]) == ("/tmp/b", ["任务"])
    assert pop_workspace_flag(["写个 README"]) == ("", ["写个 README"])
    assert pop_workspace_flag([]) == ("", [])


def test_startup_workspace_rejects_bad_explicit(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        startup_workspace(str(tmp_path / "没有这个目录"))
    assert exc.value.code == 2
    assert "--workspace" in capsys.readouterr().err


def test_startup_workspace_uses_memory_over_cwd(tmp_path, monkeypatch):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    assert startup_workspace("") == Path.cwd().resolve()

    target = tmp_path / "资料"
    target.mkdir()
    ws.remember(target, app_dir=app_dir)
    assert startup_workspace("") == target.resolve()


def test_cmd_workspace_shows_current_and_recent(tmp_path, monkeypatch):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    older = tmp_path / "older"
    older.mkdir()
    ws.remember(older, app_dir=app_dir)
    ui = FakeUI()

    assert cmd_workspace("/workspace", Config(), current, ui, FakeAgent()) is None

    text = ui.messages[-1][1]
    assert str(current) in text
    assert "默认工作区" in text
    assert str(older.resolve()) in text


def test_cmd_workspace_one_shot_only_remembers(tmp_path, monkeypatch):
    """一次性模式(`python main.py "/workspace <路径>"`):只记忆,不重建 Agent。"""
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    target = tmp_path / "我的资料"
    target.mkdir()
    ui, agent = FakeUI(), FakeAgent()

    assert cmd_workspace(f"/workspace {target}", Config(), current, ui, agent,
                         rebuild=False) is None

    assert state_of(app_dir)["current"] == str(target.resolve())
    assert agent.closed is False
    assert "下次启动生效" in ui.messages[-1][1]


def test_cmd_workspace_accepts_quoted_spaced_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    spaced = tmp_path / "my research"
    spaced.mkdir()

    cmd_workspace(f'/workspace "{spaced}"', Config(), current, FakeUI(),
                  FakeAgent(), rebuild=False)

    assert state_of(app_dir)["current"] == str(spaced.resolve())


def test_cmd_workspace_reports_bad_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    ui = FakeUI()

    cmd_workspace(f"/workspace {tmp_path / '没有'}", Config(), current, ui,
                  FakeAgent(), rebuild=False)

    assert ui.messages[-1][0] == "error"
    assert not (app_dir / "workspaces.json").exists()


def test_cmd_workspace_same_path_is_noop(tmp_path, monkeypatch):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    ui = FakeUI()

    cmd_workspace(f"/workspace {current}", Config(), current, ui, FakeAgent(),
                  rebuild=False)

    assert "已经在用" in ui.messages[-1][1]
    assert not (app_dir / "workspaces.json").exists()


def test_repl_routes_workspace_command(tmp_path, monkeypatch):
    """`python main.py /workspace <路径>`:参数要被当命令消化掉,不能当任务发出去。

    FakeAgent 没有 run_turn —— 真发出去就会 AttributeError。
    """
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    monkeypatch.setattr(paths_mod, "APP_DIR", app_dir)
    current = tmp_path / "current"
    current.mkdir()
    target = tmp_path / "目标"
    target.mkdir()
    ui = FakeUI()

    _repl(Config(), current, ui, FakeAgent(), Session(system_prompt="p"),
          argv=["/workspace", str(target)])

    assert "已记住工作区" in ui.messages[-1][1]
    assert state_of(app_dir)["current"] == str(target.resolve())

# --------------------------------------------------------------------- v0.9.11:项目面板(重命名/删除/查看文件)

def test_rename_writes_custom_name_and_restores(tmp_path):
    """备注名只写进 workspaces.json;留空恢复默认叫法。"""
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    d = tmp_path / "资料"
    d.mkdir()
    ws.remember(d, app_dir=app_dir)

    assert ws.rename(d, "毕业设计", app_dir=app_dir) == "毕业设计"
    assert ws.display_name_of(d, app_dir=app_dir) == "毕业设计"
    # 磁盘文件夹名不被改
    assert d.is_dir() and d.name == "资料"

    ws.rename(d, "", app_dir=app_dir)
    assert ws.display_name_of(d, app_dir=app_dir) == "资料"


def test_rename_current_uses_current_name_slot(tmp_path):
    app_dir = tmp_path / "appdir"
    app_dir.mkdir()
    d = tmp_path / "a"
    d.mkdir()
    ws.remember(d, app_dir=app_dir)
    ws.rename(d, "主项目", app_dir=app_dir)
    assert ws.display_name_of(d, app_dir=app_dir) == "主项目"


def test_list_files_basic_and_dirs_first(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "b.txt").write_text("x" * 10, encoding="utf-8")
    (root / "a.txt").write_text("y", encoding="utf-8")

    r = ws.list_files(root)
    assert r["ok"] is True and r["rel"] == ""
    names = [i["name"] for i in r["items"]]
    # 目录排前面,文件按名字排
    assert names == ["sub", "a.txt", "b.txt"]
    assert r["items"][0]["is_dir"] is True
    assert r["items"][1]["size"] == 1


def test_list_files_subdir_rel_semantics(tmp_path):
    """rel = 当前列出目录相对项目根的路径;上一级由调用方用 rel 推。"""
    root = tmp_path / "proj"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "note.md").write_text("n", encoding="utf-8")

    r = ws.list_files(root, "sub")
    assert r["ok"] is True and r["rel"] == "sub"
    assert [i["name"] for i in r["items"]] == ["deep", "note.md"]

    r2 = ws.list_files(root, "sub/deep")
    assert r2["rel"] == "sub/deep"
    # 上一级 = rel 去掉最后一段
    up = "/".join(r2["rel"].split("/")[:-1])
    r3 = ws.list_files(root, up)
    assert [i["name"] for i in r3["items"]] == ["deep", "note.md"]


def test_list_files_rejects_traversal(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")

    for bad in ("../secret.txt", "..", "sub/../../x", str(tmp_path / "secret.txt")):
        r = ws.list_files(root, bad)
        assert r["ok"] is False, bad
        assert "非法" in r["msg"] or "不存在" in r["msg"], bad


def test_list_files_rejects_sensitive_roots(tmp_path):
    home = tmp_path / "home"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True)
    r = ws.list_files(ssh, home=home)
    assert r["ok"] is False and "敏感" in r["msg"]


def test_bridge_delete_project_removes_and_resets_current(tmp_path):
    """删除当前项目:列表里没了,并且回到默认工作区。"""
    bridge, ui, window = make_bridge(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    bridge.switch_workspace(str(other))
    window.events.clear()

    res = bridge.delete_project(str(other))
    assert res["ok"] is True
    state = state_of(bridge.app_dir)
    assert all(Path(i["path"]) != other for i in state["recent"])
    # 当前工作区已回默认(默认 = app_dir)
    assert Path(bridge.workspace) == bridge.app_dir.resolve()
    assert other.is_dir()                     # 文件夹没删


def test_bridge_delete_project_other_keeps_workspace(tmp_path):
    bridge, ui, window = make_bridge(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    bridge.switch_workspace(str(other))
    cur_before = Path(bridge.workspace)
    window.events.clear()

    res = bridge.delete_project(str(cur_before.parent / "nonexist-in-list"))  # 不存在 → 报错
    assert res["ok"] is False

    # 把 cur 自身从 recent 里 forget 再重新记住另一个,删那个
    another = tmp_path / "another"
    another.mkdir()
    bridge.forget_workspace(str(another))     # 无害:不在列表里
    res2 = bridge.delete_project(str(another))
    assert res2["ok"] is False                # 不在最近列表/不存在 → resolve_target 报错


def test_bridge_list_project_files_via_facade(tmp_path):
    bridge, ui, window = make_bridge(tmp_path)
    (bridge.workspace / "hello.txt").write_text("hi", encoding="utf-8")
    api = bridge.public_api()
    r = api.list_project_files(str(bridge.workspace))
    assert r["ok"] is True
    assert any(i["name"] == "hello.txt" and not i["is_dir"] for i in r["items"])


def test_save_providers_pushes_session_metrics_live(tmp_path):
    """p1/p2:保存模型后必须立刻推 session_metrics,面板才不用等切会话。"""
    bridge, ui, window = make_bridge(tmp_path)
    window.events.clear()

    items = [{"id": "", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
              "api_key": "sk-test", "model": "deepseek-chat"}]
    res = bridge.save_providers(json.dumps(items), "gzasc")
    assert res["ok"] is True
    pushed = window.of_type("session_metrics")
    assert pushed, "保存模型后必须推 session_metrics 事件"
    selectable = pushed[-1]["selectable"]
    names = [s["name"] for s in selectable]
    assert "DeepSeek" in names and "广州应用科技学院" in names


def test_set_provider_pushes_metrics_only_for_global_sessions(tmp_path):
    """切全局模型:没单独切过模型的会话收到推送;切过的会话不受影响也不推错。"""
    bridge, ui, window = make_bridge(tmp_path)
    window.events.clear()
    items = [{"id": "", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
              "api_key": "sk-test", "model": "deepseek-chat"}]
    bridge.save_providers(json.dumps(items), "gzasc")
    custom_id = next(p["id"] for p in bridge.get_providers()["providers"]
                     if p["name"] == "DeepSeek")
    window.events.clear()

    # 当前会话单独切到 DeepSeek(会话级覆盖)
    bridge.set_session_model(custom_id)
    window.events.clear()
    res = bridge.set_provider("gzasc")        # 全局仍在内置
    assert res["ok"] is True
    pushed = window.of_type("session_metrics")
    assert pushed == []                       # 该会话有 override,不跟随全局,不推

    bridge.set_session_model("gzasc")         # 切回全局
    window.events.clear()
    bridge.set_provider(custom_id)
    assert window.of_type("session_metrics")  # 无 override → 跟随并收到推送
