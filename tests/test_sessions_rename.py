"""会话改名与归档(v0.9.126)。单独成文件 —— 沿用那条规矩:并行会话正在改的测试文件
不往里加东西(`test_gui_bridge.py` / `test_sessions_title.py` 都是别人的手)。

两层都钉:
  · 后端 —— 真读真写留档文件(不是 grep 源码);
  · 接线 —— 侧栏那行的标题、CLI 的 `/rename` 走真 `handle_slash`。
归档的口径写在针里:**沉底 + 打标,不删不隐藏** —— 侧栏 /resume / 分支共用一份序号,
一隐藏就把别人的坐标弄破。
"""
import json

from minicode.llm import AssistantTurn
from minicode.session import (Session, current_title, display_title, list_sessions,
                              normalize_title, set_archived, set_title)
from minicode.tools.base import ToolContext
from test_gui_bridge import FakeLLM, make, wait_until


def _start(**fields):
    return {"type": "session_start", "time": "2026-10-06 10:00", **fields}


def _user(text="帮我写一份报名材料"):
    return {"role": "user", "content": text}


def _log(dir_path: "object", name: str, lines) -> "object":
    from pathlib import Path
    d = Path(dir_path) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in lines) + "\n",
                 encoding="utf-8")
    return p


# ------------------------------------------------------------------------ 改名

def test_manual_title_wins_over_the_derived_one(tmp_path):
    p = _log(tmp_path, "session_a.jsonl", [_start(), _user()])
    assert set_title(p, "蓝桥杯报名材料 v2").startswith("已改名为")
    s = list_sessions(tmp_path / "sessions")[0]
    assert s.title == "蓝桥杯报名材料 v2"
    assert display_title(s) == "蓝桥杯报名材料 v2"       # 不再退回第一句问话
    assert current_title(p) == "蓝桥杯报名材料 v2"       # CLI 与侧栏同一个名字


def test_title_is_flattened_and_capped(tmp_path):
    p = _log(tmp_path, "session_b.jsonl", [_start(), _user()])
    raw = "多" * 200 + "\n第二行不该出现"
    set_title(p, raw)
    got = list_sessions(tmp_path / "sessions")[0].title
    assert got == normalize_title(raw)
    assert "\n" not in got and "第二行" not in got and len(got) == 60


def test_empty_title_changes_nothing_and_says_so(tmp_path):
    p = _log(tmp_path, "session_c.jsonl", [_start(), _user()])
    before = p.read_text(encoding="utf-8")
    assert "空的" in set_title(p, "   \n  ")
    assert p.read_text(encoding="utf-8") == before       # 一个字节都不许多写


def test_old_archive_without_start_meta_gets_one_line_prepended(tmp_path):
    """老留档没有 `session_start` 行(v0.9.66 之前)—— 手改的名字不能因此丢掉。"""
    p = _log(tmp_path, "session_d.jsonl", [_user("老会话")])
    assert set_title(p, "老会话·归档").startswith("已改名为")
    lines = p.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    assert meta["type"] == "session_start" and meta["title"] == "老会话·归档"
    assert len(lines) == 2                              # 消息一条没动,只在开头补一行


def test_rewrite_is_atomic_and_keeps_other_meta(tmp_path):
    p = _log(tmp_path, "session_e.jsonl", [_start(workspace="D:/ somewhere"), _user()])
    set_title(p, "留着工作区那份")
    meta = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert meta["workspace"] == "D:/ somewhere"         # 是补字段,不是整行重写
    assert not list(p.parent.glob("*.tmp"))             # 原子替换,不留半截文件


# ------------------------------------------------------------------------ 归档

def test_archive_sinks_to_the_end_without_hiding_anything(tmp_path):
    """归档只做两件事:沉底 + 打标。**不删、不隐藏** —— 侧栏 /resume / 分支用的是
    同一份序号,一隐藏就把别人的坐标弄破。

    mtime 手动钉死:改写元数据会把文件的 mtime 推到最新,不钉住的话"列表原本怎么排"
    本身就飘,那这支针测的就不再是归档逻辑了。
    """
    import os
    d = tmp_path / "sessions"
    for i, name in enumerate(("session_a.jsonl", "session_b.jsonl", "session_c.jsonl")):
        p = _log(tmp_path, name, [_start(time=f"2026-10-0{i + 1} 10:00"), _user()])
        os.utime(p, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
    base = ["session_c.jsonl", "session_b.jsonl", "session_a.jsonl"]   # 新的在前
    assert [s.path.name for s in list_sessions(d)] == base
    b = d / "session_b.jsonl"
    assert set_archived(b, True).startswith("已归档")
    after = [s.path.name for s in list_sessions(d)]
    assert sorted(after) == sorted(base), "归档不该把任何一条从列表里变没"
    assert after[-1] == "session_b.jsonl"                  # 归档的那条沉底
    assert [n for n in after if n != "session_b.jsonl"] == ["session_c.jsonl",
                                                            "session_a.jsonl"]
    os.utime(b, (1_700_000_000 + 100, 1_700_000_000 + 100))   # 排除 mtime 干扰
    assert set_archived(b, False).startswith("已取消归档")
    os.utime(b, (1_700_000_000 + 100, 1_700_000_000 + 100))
    assert [s.path.name for s in list_sessions(d)] == base     # 回到原状
    lines = [json.loads(ln) for ln in b.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines[-1] == _user()                     # 消息本体一个字段都没动
    assert lines[0]["time"] == "2026-10-02 10:00"   # 元数据是补字段,不是整行重写


def test_archive_keeps_the_title_and_the_body(tmp_path):
    """两条操作互不覆盖:归档不能把手改的名字冲掉。"""
    p = _log(tmp_path, "session_f.jsonl", [_start(), _user("论文初稿")])
    set_title(p, "论文·第三章")
    set_archived(p, True)
    s = list_sessions(tmp_path / "sessions")[0]
    assert s.archived is True and s.title == "论文·第三章"
    assert s.n_messages == 1


# ------------------------------------------------------------------------ 接线

def test_cli_rename_and_archive_go_through_the_real_handler(tmp_path, config):
    """走真 `handle_slash`:命令表那条路必须真通过去,不是只测函数。"""
    import main as cli_main

    s = Session(system_prompt="sys", log_dir=tmp_path / "cli", workspace=tmp_path)
    s.add_user("帮我列个材料清单")                       # 触发留档落盘
    assert s.log_path is not None

    class UI:
        def __init__(self):
            self.lines = []

        def info(self, t="", **k):
            self.lines.append(str(t))

        def error(self, t="", **k):
            self.lines.append("ERR:" + str(t))

        def __getattr__(self, _n):
            return lambda *a, **k: None

    ui = UI()
    ctx = ToolContext(workspace=tmp_path, config=config)
    agent = type("A", (), {"ctx": ctx, "session": s})()

    cli_main.handle_slash("/rename 材料清单·第一轮", s, config, ui, agent)
    assert "已改名为「材料清单·第一轮」" in "".join(ui.lines), ui.lines
    ui.lines.clear()
    cli_main.handle_slash("/rename", s, config, ui, agent)
    joined = "".join(ui.lines)
    assert "当前标题:材料清单·第一轮" in joined and "/rename <新标题>" in joined
    ui.lines.clear()
    cli_main.handle_slash("/archive", s, config, ui, agent)
    assert "已归档" in "".join(ui.lines), ui.lines
    assert current_title(s.log_path) == "材料清单·第一轮"     # 归档不冲掉名字


def test_fork_from_the_cli_copies_without_touching_the_source(tmp_path, config):
    """/fork:整段复制成一份新留档接管当前上下文,**原留档一个字节都不许变**。"""
    import main as cli_main
    from minicode.session import fork_log_file  # noqa: F401  (确认这条路真在)

    d = tmp_path / "cli"
    d.mkdir(parents=True, exist_ok=True)
    src = d / "session_src.jsonl"
    src.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in [
        _start(), _user("第一轮的任务"), {"role": "assistant", "content": "答"}
    ]) + "\n", encoding="utf-8")
    original = src.read_bytes()

    s = Session(system_prompt="sys", log_dir=d, workspace=tmp_path)

    class UI:
        def __init__(self):
            self.lines = []

        def info(self, t="", **k):
            self.lines.append(str(t))

        def error(self, t="", **k):
            self.lines.append("ERR:" + str(t))

        def list_sessions(self, summaries):
            self.lines.append("LIST")

        def __getattr__(self, _n):
            return lambda *a, **k: None

    ui = UI()
    ctx = ToolContext(workspace=tmp_path, config=config)
    agent = type("A", (), {"ctx": ctx, "session": s})()
    cli_main.SESSIONS_DIR = d
    cli_main.handle_slash("/fork 1", s, config, ui, agent)
    assert "分叉出新会话" in "".join(ui.lines), ui.lines
    assert src.read_bytes() == original, "原留档被动过 —— 分叉不是截断"
    assert "第一轮的任务" in json.dumps(s.messages, ensure_ascii=False)
    assert s.log_path is not None and s.log_path != src
    assert [p.name for p in d.glob("*_fork.jsonl")], list(d.iterdir())


def test_sidebar_row_shows_the_manual_title(config, tmp_path):
    """端到端一根线:改完名,侧栏那行的 title 就是手改名(不再是第一句问话)。"""
    llm = FakeLLM([AssistantTurn(text="好了")])
    bridge, window, _session = make(config, tmp_path, llm)
    bridge.send_task("帮我写一份蓝桥杯报名材料")
    assert wait_until(lambda: any(
        e["type"] == "sessions" and e.get("items") for e in window.events))
    path = bridge.session.log_path
    assert path is not None
    assert set_title(path, "蓝桥杯·报名材料").startswith("已改名为")
    bridge._push_sessions()
    items = [e for e in window.events if e["type"] == "sessions" and e.get("items")][-1]["items"]
    row = next(r for r in items if r["file"] == path.name)
    assert row["title"] == "蓝桥杯·报名材料", row


# ---------------------------------------------------- 侧栏那个菜单要的那三件(v0.9.140)

def _row(bridge, window, name):
    bridge._push_sessions()
    items = [e for e in window.events if e["type"] == "sessions" and e.get("items")][-1]["items"]
    return next((r for r in items if r["file"] == name), None)


def test_bridge_rename_session_writes_the_log_and_republishes(config, tmp_path):
    """图形侧改名:真改留档那一行,并把新标题推回侧栏(不是只改前端自己显示的字)。

    原来这一路**根本不存在**:前端只有一颗 ✕,`rename_session` 在桥里没有,
    改名只有 CLI 的 /rename 能用 —— 用户 2026-10-09 指的"还是无法更改任务的名称"就是它。
    """
    from test_gui_bridge import FakeLLM, make
    bridge, window, _s = make(config, tmp_path, FakeLLM([]))
    p = _log(tmp_path, "session_menu.jsonl", [_start(title="旧名"), _user()])
    got = bridge.rename_session(p.name, "  蓝桥杯报名材料  ")
    assert got["ok"] is True and "已改名为" in got["msg"], got
    head = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert head["title"] == "蓝桥杯报名材料", head          # 首尾空白压平,与 CLI 同一只手
    assert _row(bridge, window, p.name)["title"] == "蓝桥杯报名材料"


def test_bridge_rename_refuses_blank_and_missing_without_touching_bytes(config, tmp_path):
    from test_gui_bridge import FakeLLM, make
    bridge, window, _s = make(config, tmp_path, FakeLLM([]))
    p = _log(tmp_path, "session_blank.jsonl", [_start(title="留着"), _user()])
    before = p.read_bytes()
    got = bridge.rename_session(p.name, "   ")
    assert got["ok"] is False and "空" in got["msg"], got
    assert p.read_bytes() == before, "空标题不该动一个字节"
    missing = bridge.rename_session("session_none.jsonl", "改成这个")
    assert missing["ok"] is False and "不存在" in missing["msg"], missing
    assert p.read_bytes() == before


def test_bridge_archive_marks_the_row_and_still_shows_it(config, tmp_path):
    """归档要能在菜单上被**取消** —— 所以那一行的状态必须推给前端,否则按钮写什么靠猜。"""
    from test_gui_bridge import FakeLLM, make
    bridge, window, _s = make(config, tmp_path, FakeLLM([]))
    p = _log(tmp_path, "session_arch.jsonl", [_start(), _user()])
    assert _row(bridge, window, p.name)["archived"] is False
    got = bridge.archive_session(p.name, True)
    assert got["ok"] is True and "已归档" in got["msg"], got
    row = _row(bridge, window, p.name)
    assert row["archived"] is True, row                     # 菜单下一颗要改成「取消归档」
    assert row is not None                                  # 沉底 ≠ 隐藏
    assert bridge.archive_session(p.name, False)["ok"] is True
    assert _row(bridge, window, p.name)["archived"] is False

