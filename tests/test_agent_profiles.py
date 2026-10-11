"""本地档位定义(agents/*.md)的四条红线。

这一层的全部风险都在"扩权"那一侧,所以断言只认两种结果:**要么更窄,要么整份不算** ——
一条都不许出现"配了个更大的权限结果真生效"。
"""
from pathlib import Path

import pytest

from minicode import agent_profiles, router, subagents
from minicode.tools.base import Tool


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """只认这个临时工作区,别把开发机数据目录里真有的 agents/ 混进测试。"""
    workspace = tmp_path / "proj"
    (workspace / "agents").mkdir(parents=True)
    monkeypatch.setattr(agent_profiles, "profile_dirs",
                        lambda w: [Path(w) / "agents"])
    return workspace


def _write(ws, name, text):
    (ws / "agents" / name).write_text(text, encoding="utf-8")


def _load(ws):
    return agent_profiles.load(ws, bases=subagents._profile_bases(),
                               step_cap=subagents.MAX_SUB_STEPS)


def _read_only_tool(name):
    return Tool(name=name, description="只读工具", parameters={"type": "object",
                                                                "properties": {}},
                handler=lambda ctx, **kw: "ok", read_only=True)


def _writing_tool(name):
    return Tool(name=name, description="写工具", parameters={"type": "object",
                                                              "properties": {}},
                handler=lambda ctx, **kw: "ok", read_only=False)


# ------------------------------------------------------------------ 红线 1:只收窄

def test_tools_only_narrows_the_child_registry(ws):
    _write(ws, "scout.md", "---\nname: scout\nbase: explore\nlabel: 校内科站侦察\n"
                   "tools: read_file, list_files\n---\n只看这两只。\n")
    profiles, notes = _load(ws)
    assert notes == []
    spec = subagents.type_spec("scout", ws, gaps={}, profiles=profiles)
    assert spec is not None and spec.type == "scout" and spec.label == "校内科站侦察"
    registry = [_read_only_tool("read_file"), _read_only_tool("list_files"),
                _read_only_tool("search_files"), _writing_tool("write_file"),
                _read_only_tool("task")]
    got = {t.name for t in subagents.sub_tool_registry(registry, spec)}
    assert got == {"scout·read_file", "scout·list_files"}, "定义里没点的工具必须一个都不给"
    assert "scout·search_files" not in got and "scout·task" not in got


def test_a_widening_tools_list_adds_nothing_and_says_so(ws):
    """写一只本档拿不到的工具(写工具 / task):不许因此多拿到,而且要说出来。"""
    _write(ws, "greedy.md", "---\nname: greedy\nbase: explore\n"
                            "tools: read_file, write_file, task\n---\n想写文件。\n")
    profiles, notes = _load(ws)
    assert any("task" in n for n in notes), "task 本来就摘掉了,这条要有声音"
    spec = subagents.type_spec("greedy", ws, gaps={}, profiles=profiles)
    registry = [_read_only_tool("read_file"), _writing_tool("write_file"),
                _read_only_tool("task")]
    got = {t.name for t in subagents.sub_tool_registry(registry, spec)}
    assert got == {"greedy·read_file"}
    assert subagents.unknown_tools(spec, registry) == ["write_file"]


def test_read_only_can_only_get_tighter_never_looser(ws):
    _write(ws, "loose.md", "---\nname: loose\nbase: explore\nread_only: false\n---\n放开写。\n")
    profiles, notes = _load(ws)
    spec = subagents.type_spec("loose", ws, gaps={}, profiles=profiles)
    assert spec.read_only is True, "只读档被 markdown 一句话放开 = 扩权"
    assert any("只读" in n for n in notes)
    # 反过来(非只读档改成只读)是往严里走,必须生效
    _write(ws, "tight.md", "---\nname: tight\nbase: implement\nread_only: true\n---\n")
    profiles2, _n2 = _load(ws)
    spec2 = subagents.type_spec("tight", ws, gaps={}, profiles=profiles2)
    assert spec2.read_only is True


def test_max_steps_can_shrink_and_is_clamped_to_the_cap(ws):
    _write(ws, "short.md", "---\nname: short\nbase: explore\nmax_steps: 3\n---\n")
    profiles, notes = _load(ws)
    spec = subagents.type_spec("short", ws, gaps={}, profiles=profiles)
    assert spec.steps == 3
    _write(ws, "long.md", "---\nname: long\nbase: explore\nmax_steps: 999\n---\n")
    profiles2, notes2 = _load(ws)
    spec2 = subagents.type_spec("long", ws, gaps={}, profiles=profiles2)
    assert spec2.steps == subagents.MAX_SUB_STEPS, "步数只能往下收,不许超过内置上限"
    assert any("999" in n for n in notes2)


# ------------------------------------------------------------------ 红线 2:不许覆盖内置

def test_a_profile_named_like_a_builtin_is_dropped_whole(ws):
    _write(ws, "explore.md", "---\nname: explore\nbase: implement\n"
                             "read_only: false\n---\n把侦察换成能写。\n")
    profiles, notes = _load(ws)
    assert "explore" not in profiles
    assert any("内置档" in n and "装载顺序" in n for n in notes)
    # 而且 type_spec 走的仍是代码里那份内置规格
    assert subagents.type_spec("explore", ws, gaps={},
                              profiles=profiles).read_only is True


def test_profile_names_are_not_the_builtin_ones_and_do_not_join_auto(ws):
    _write(ws, "scout.md", "---\nname: scout\nbase: explore\n---\n")
    profiles, _notes = _load(ws)
    keys = subagents.type_keys(ws)
    assert "scout" in keys and "explore" in subagents.TYPE_KEYS
    assert "scout" not in subagents.DOMAIN_KEYS
    assert "scout" not in router.DOMAINS, "领域档才进 auto 路由;角色档与本地档要显式点名"
    # 没有工作区时退回静态表(不因盘上有没有文件而两套样子)
    assert subagents.type_keys(None) == subagents.TYPE_KEYS


# ------------------------------------------------------------------ 红线 3:提权字段剥掉

def test_privileged_fields_are_stripped_and_spoken(ws):
    _write(ws, "sneaky.md", "---\nname: sneaky\nbase: explore\npermission_mode: accept_all\n"
                            "model: gpt-any\nallow_tools: write_file\n---\n正文\n")
    profiles, notes = _load(ws)
    assert profiles, "剥掉提权字段不等于整份不算"
    assert any("permission_mode" in n and "不由" in n for n in notes)
    assert any("allow_tools" in n for n in notes)
    spec = subagents.type_spec("sneaky", ws, gaps={}, profiles=profiles)
    assert spec.read_only is True
    registry = [_read_only_tool("read_file"), _writing_tool("write_file")]
    assert {t.name for t in subagents.sub_tool_registry(registry, spec)} == \
        {"sneaky·read_file"}


# ------------------------------------------------------------------ 形状与坏输入

@pytest.mark.parametrize("text,needle", [
    ("---\nname: ok\n---\n正文\n", "base"),
    ("---\nname: ok\nbase: nosuchtier\n---\n", "不存在"),
    ("---\nname: auto\nbase: explore\n---\n", "不能用"),
])
def test_bad_definitions_are_dropped_with_a_speaking_reason(ws, text, needle):
    _write(ws, "bad.md", text)
    profiles, notes = _load(ws)
    assert profiles == {}
    assert any(needle in n for n in notes), "丢掉必须有理由,不能默默少一档"


def test_file_stem_is_the_name_when_frontmatter_omits_it(ws):
    """没写 name: 就用文件名 —— 与 skills 同一条口径,不算坏输入。"""
    _write(ws, "kuan.md", "---\nbase: explore\ntools: read_file\n---\n")
    profiles, notes = _load(ws)
    assert list(profiles) == ["kuan"] and notes == []


def test_two_files_same_name_first_wins_and_says_so(ws):
    _write(ws, "a.md", "---\nname: twin\nbase: explore\nmax_steps: 2\n---\n")
    _write(ws, "z.md", "---\nname: twin\nbase: explore\nmax_steps: 9\n---\n")
    profiles, notes = _load(ws)
    assert profiles["twin"].steps == 2
    assert any("先装载" in n for n in notes)


def test_unreadable_file_says_what_failed(ws):
    (ws / "agents" / "bom.md").write_bytes(b"\xff\xfe not utf8 at all")
    profiles, notes = _load(ws)
    assert profiles == {} and any("读不出来" in n for n in notes)


def test_profile_dirs_puts_the_app_dir_first(monkeypatch, tmp_path):
    """装载顺序是红线 2 的另一半:同名先装载的那份赢,所以顺序必须写死并钉住。

    v0.9.152 起工作区那一段是**两截**:本项目自己的 `agents/` 在前,别的工具的那三个
    目录(`COMPAT_PROFILE_DIRS`)在后 —— 后者优先级天然更低。这一支把整条链**逐项**
    钉住,而不是只钉首尾:中间多插一个目录、或者顺序被谁调换,都该在这里红。
    """
    from minicode import paths
    app_dir = tmp_path / "appdata" / "agents"
    monkeypatch.setattr(paths, "app_path", lambda *p: tmp_path.joinpath("appdata", *p))
    proj = tmp_path / "proj"
    assert agent_profiles.profile_dirs(proj) == [
        app_dir, proj / "agents",
        proj / ".cursor" / "agents", proj / ".claude" / "agents",
        proj / ".codex" / "agents",
    ]


def test_no_workspace_still_reads_the_app_dir(monkeypatch, tmp_path):
    from minicode import paths
    monkeypatch.setattr(paths, "app_path", lambda *p: tmp_path.joinpath("appdata", *p))
    (tmp_path / "appdata" / "agents").mkdir(parents=True)
    (tmp_path / "appdata" / "agents" / "scout.md").write_text(
        "---\nname: scout\nbase: explore\ntools: read_file\n---\n没有工作区也要认这份。\n",
        encoding="utf-8")
    profiles, notes = agent_profiles.load(
        None, bases=subagents._profile_bases(), step_cap=subagents.MAX_SUB_STEPS)
    assert list(profiles) == ["scout"] and notes == []


def test_colliding_with_a_domain_tier_is_dropped_too(monkeypatch, tmp_path):
    """红线 2 说的是"内置档与领域档":领域档的名字一样不许覆盖(campus 是 router 登记的档)。"""
    from minicode import paths
    monkeypatch.setattr(paths, "app_path", lambda *p: tmp_path.joinpath("appdata", *p))
    d = tmp_path / "appdata" / "agents"
    d.mkdir(parents=True)
    (d / "campus.md").write_text("---\nname: campus\nbase: explore\n---\n", encoding="utf-8")
    profiles, notes = agent_profiles.load(
        None, bases=subagents._profile_bases(), step_cap=subagents.MAX_SUB_STEPS)
    assert profiles == {} and any("内置档" in n for n in notes)


# ------------------------------------------------------- 红线 4:注记要真说出口

def test_dispatch_notes_name_the_tool_the_tier_cannot_get(ws):
    """装载时合法、派发时才发现拿不到的那一只工具,必须点名 —— 否则就是"配了没生效"。"""
    _write(ws, "greedy.md", "---\nname: greedy\nbase: explore\n"
                            "tools: read_file, write_file\n---\n")
    profiles, notes = _load(ws)
    spec = subagents.type_spec("greedy", ws, gaps={}, profiles=profiles)
    registry = [_read_only_tool("read_file"), _writing_tool("write_file")]
    out = subagents.dispatch_notes(notes, [(spec, "任务", "")], registry)
    assert any("write_file" in n and "greedy" in n for n in out)


def test_notes_prefix_is_there_only_when_there_is_something_to_say():
    assert subagents.notes_prefix([]) == ""
    assert subagents.notes_prefix(["剥掉了 model"]) == "[档位定义注记] 剥掉了 model\n"
    got = subagents.notes_prefix(["甲", "乙"])
    assert got.startswith("[档位定义注记] ") and "甲" in got and "乙" in got


# ------------- E:别的工具的子代理目录(v0.9.152,只读兼容)
# 这一层多开了一个"读 markdown"的口子,而那个口子里的文件是**别人写的**
# (仓库自带的 `.cursor/agents/` 会跟着 clone 走)。所以这一族最要紧的一支不是
# "认得到",而是"**从别处认来的那份一样绕不过红线**"。
@pytest.fixture
def compat_ws(tmp_path, monkeypatch):
    """**不**把 `profile_dirs` 换成单目录版 —— 这一族要验的就是多目录那条链。

    只把 app 目录钉到临时目录(否则会读到开发机数据目录里真有的 `agents/`)。
    """
    from minicode import paths
    monkeypatch.setattr(paths, "app_path",
                        lambda *p: tmp_path.joinpath("appdata", *p))
    workspace = tmp_path / "proj"
    workspace.mkdir()
    return workspace


def _put(ws, rel, name, extra="", body=""):
    d = ws / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\nbase: explore\n{extra}---\n{body or (name + ' 的正文')}\n",
        encoding="utf-8")


def test_the_three_foreign_agent_dirs_are_read(compat_ws):
    """`.cursor/agents/`、`.claude/agents/`、`.codex/agents/` 里那几份也认。"""
    for rel, name in ((".cursor/agents", "cu"), (".claude/agents", "cl"),
                      (".codex/agents", "cx")):
        _put(compat_ws, rel, name)
    profiles, notes = _load(compat_ws)
    assert sorted(profiles) == ["cl", "cu", "cx"]
    for name in ("cu", "cl", "cx"):
        assert any(name in n and "认来的" in n for n in notes), notes


def test_the_project_own_definition_wins_on_a_name_clash(compat_ws):
    """同名时**本项目自己那份赢** —— 用户在这个项目里写的压过从别处带过来的。"""
    _put(compat_ws, "agents", "scout", body="本项目自己那份")
    _put(compat_ws, ".cursor/agents", "scout", body="Cursor 那份")
    profiles, notes = _load(compat_ws)
    assert profiles["scout"].extra_prompt == "本项目自己那份"
    assert any("先装载的那份赢" in n for n in notes), notes


def test_a_foreign_definition_cannot_widen_permissions(compat_ws):
    """**最要紧的一支**:从别的工具目录认来的那份,一样绕不过红线。

    提权字段剥掉、只读不许放开、步数只能往下收、摘不掉的那只拿不到 ——
    与项目自己的 `agents/` 受**同一套**限制(因为它们走的是同一条装载路)。
    """
    _put(compat_ws, ".cursor/agents", "sneaky",
         extra="permission_mode: yolo\nmodel: gpt-9\nmax_steps: 9999\n"
               "read_only: false\ntools: read_file, write_file, task\n")
    profiles, notes = _load(compat_ws)
    joined = " ".join(notes)
    assert "剥掉了" in joined and "permission_mode" in joined
    assert "task" in joined, "摘不掉的那只也要说一声"
    spec = subagents.type_spec("sneaky", compat_ws, gaps={}, profiles=profiles)
    assert spec.read_only is True, "别人的文件也不许把只读档放开"
    assert spec.steps <= subagents.MAX_SUB_STEPS, "步数只能往下收"
    registry = [_read_only_tool("read_file"), _writing_tool("write_file"),
                _read_only_tool("task")]
    got = {t.name for t in subagents.sub_tool_registry(registry, spec)}
    assert got == {"sneaky·read_file"}, f"从别处认来的档也不许多拿工具:{got}"


def test_no_workspace_means_nothing_foreign_to_read(monkeypatch, tmp_path):
    """没有工作区时,那几个兼容目录**一个都不在列**。

    这一条就是"**不去翻你本机的 `~/.claude/agents`**"那个边界的形状:
    目录表整条是从工作区算出来的,所以工作区为空就自然没有它们 ——
    不是靠某处一句 if 挡着(那种挡法漏一处就静默开了)。
    """
    from minicode import paths
    monkeypatch.setattr(paths, "app_path",
                        lambda *p: tmp_path.joinpath("appdata", *p))
    assert agent_profiles.profile_dirs(None) == [tmp_path / "appdata" / "agents"]


def test_empty_or_missing_foreign_dirs_say_nothing(compat_ws):
    """目录不存在、或者存在但是空的 —— 一个字都不许多说。"""
    profiles, notes = _load(compat_ws)
    assert profiles == {} and notes == []
    for rel in agent_profiles.COMPAT_PROFILE_DIRS:
        (compat_ws / rel).mkdir(parents=True)
    profiles2, notes2 = _load(compat_ws)
    assert profiles2 == {} and notes2 == []
