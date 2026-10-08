# -*- coding: utf-8 -*-
"""`apply_patch`:一次落一整包统一 diff（v0.9.134，P0-3）。

这一只工具的风险不在"能不能改对",而在**改坏一半**与**绕过门**,所以针分两族:

**A. 整包原子**（`patch.py`）：
① 多文件（改 / 新建 / 删除）一次落盘;② 任一文件对不上 → **一个字节都不写**并点名 hunk;
③ 行尾与编码跟着原文件（CRLF 还是 CRLF、GBK 还是 GBK）;④ 偏移要说出口（不悄悄改）;
⑤ 只收统一 diff —— 读不出来的形状(裸 hunk、行首不是空格/-/+、rename、二进制)一律拒并说为什么。

**B. 逐份文件过同一套门**（`permission.py`）：
⑥ 补丁里藏 `.env` / `.git/hooks/` / 工作区外路径,该问的照样问、该拒的照样拒,
   而且**报的还是那道门自己的名字**（不另写一套判定;`ask_kind` 被 `NON_DELEGABLE_ASKS`
   与 `NEVER_REMEMBER_ASKS` 两处按名字用,类别一糊就等于给凭据文件开了代答的口子）;
⑦ 外部内容升格门也算它（一包补丁写进 `kb/`、`skills/` 与 write_file 同权重）;
⑧ 改动前像**每份都存**,`/undo` 能整包退回。
"""
import json

import pytest

from minicode import patch
from minicode.config import Config, Provider
from minicode.llm import AssistantTurn, ToolCall
from minicode.permission import ASK, REJECT, PermissionGate
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


@pytest.fixture
def ctx(ws):
    cfg = Config(permission_mode="confirm")
    sb = SandboxState(SandboxPolicy(), ws)
    return ToolContext(workspace=ws, config=cfg, sandbox=sb)


def _cfg(mode="confirm"):
    return Config(
        providers=[Provider(id="t", name="T", base_url="https://x.invalid/v1",
                            api_key="k", model="m")],
        active_provider_id="t", permission_mode=mode)


def _gate(ws, registry, mode="confirm"):
    sb = SandboxState(SandboxPolicy(), ws)
    return PermissionGate(_cfg(mode), sandbox=sb, workspace=ws,
                          tool_names=set(registry))


def _ctx(ws, mode="confirm"):
    cfg = _cfg(mode)
    sb = SandboxState(SandboxPolicy(), ws)
    return ToolContext(workspace=ws, config=cfg, sandbox=sb)


# 一份三段补丁:改 a、删 b、新建 c/new.md
TRIPLE = """--- a/a.py
+++ b/a.py
@@ -2,2 +2,3 @@
 l2
-l3
+l3 changed
+added
--- a/b.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
--- /dev/null
+++ b/c/new.md
@@ -0,0 +1,2 @@
+hello
+world
"""


# ---------------------------------------------------------------- A. 解析与原子

def test_three_kinds_land_in_one_go(ws, ctx):
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\nl4\n")
    (ws / "b.txt").write_bytes(b"x\ny\n")
    outs, fails = patch.apply(ctx, patch.parse(TRIPLE)[0])
    assert fails == []
    assert (ws / "a.py").read_bytes() == b"l1\nl2\nl3 changed\nadded\nl4\n"
    assert not (ws / "b.txt").exists()
    assert (ws / "c" / "new.md").read_bytes() == b"hello\nworld\n"
    assert [o["action"] for o in outs] == ["update", "delete", "add"]


def test_one_mismatch_writes_nothing_at_all(ws, ctx):
    """半包落盘是这里最坏的坏法:代码改了一半直接编译不过,而模型以为改完了。"""
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\n")
    (ws / "b.txt").write_bytes(b"x\ny\n")
    (ws / "keep.py").write_bytes(b"keep\n")
    bad = TRIPLE.replace("-l3\n", "-nope\n")
    result = patch.apply_patch(ctx, patch=bad)
    assert result.startswith("错误: 整包没落盘")
    assert (ws / "a.py").read_bytes() == b"l1\nl2\nl3\n"
    assert not (ws / "c" / "new.md").exists()      # 连"新建"那半也不许做
    assert (ws / "b.txt").read_bytes() == b"x\ny\n"   # 要删的那份也必须原样还在
    assert "0 个文件被改动" in result
    assert "hunk" in result and "@@ -2,2 +2,3 @@" in result


def test_failure_names_the_file_and_hunk(ws, ctx):
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\n")
    (ws / "d.py").write_bytes(b"keep\n")
    two = ("--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-l1\n+L1\n"
           "--- a/d.py\n+++ b/d.py\n@@ -1,1 +1,1 @@\n-nope\n+other\n")
    result = patch.apply_patch(ctx, patch=two)
    assert "整包没落盘" in result
    assert (ws / "a.py").read_bytes() == b"l1\nl2\nl3\n"   # 能对上的那份也没写
    assert "d.py" in result and "nope" in result     # 点名是哪份、哪一行对不上


def test_crlf_and_gbk_survive_the_patch(ws, ctx):
    """#49a 那条老规矩在补丁这一路同样成立:未触碰的行字节完全不变。"""
    (ws / "crlf.py").write_bytes(b"l1\r\nl2\r\nl3\r\n")
    (ws / "gbk.txt").write_bytes("中文一\n中文二\n".encode("gbk"))
    p = ("--- a/crlf.py\n+++ b/crlf.py\n@@ -1,3 +1,3 @@\n l1\n-l2\n+L2\n"
         "--- a/gbk.txt\n+++ b/gbk.txt\n@@ -1,2 +1,2 @@\n-中文一\n+中文一改\n")
    assert patch.apply_patch(ctx, patch=p).startswith("已应用补丁")
    assert (ws / "crlf.py").read_bytes() == b"l1\r\nL2\r\nl3\r\n"
    raw = (ws / "gbk.txt").read_bytes()
    assert raw.decode("gbk") == "中文一改\n中文二\n"
    assert b"\xe4\xb8\xad" not in raw            # 没被悄悄转成 UTF-8


def test_missing_trailing_newline_is_preserved(ws, ctx):
    (ws / "n.txt").write_bytes(b"a\nb")          # 无尾换行
    p = "--- a/n.txt\n+++ b/n.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    patch.apply_patch(ctx, patch=p)
    assert (ws / "n.txt").read_bytes() == b"a\nB"     # 还是无尾换行


def test_no_newline_marker_at_the_end_is_honored(ws, ctx):
    (ws / "e.txt").write_bytes(b"a\nb\n")
    p = ("--- a/e.txt\n+++ b/e.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
         "\\ No newline at end of file\n")
    patch.apply_patch(ctx, patch=p)
    assert (ws / "e.txt").read_bytes() == b"a\nB"


def test_offset_is_reported_not_swallowed(ws, ctx):
    """行号与磁盘不符时按上下文找 —— 找到了必须把偏移说出来,悄悄改完等于骗人。"""
    (ws / "f.py").write_bytes(("\n".join(["fill"] * 5) + "\nl1\nl2\nl3\n").encode("utf-8"))
    p = "--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n-l1\n+L1\n l2\n"
    result = patch.apply_patch(ctx, patch=p)
    assert "偏移 +5 行" in result, result
    assert "l1\nl2" not in (ws / "f.py").read_text(encoding="utf-8").replace("L1", "")


def test_rejects_codex_dialect_and_weird_lines(ws, ctx):
    (ws / "g.py").write_bytes(b"keep\n")
    r = patch.apply_patch(ctx, patch="*** Begin Patch\n*** Update File: g.py\n*** End Patch")
    assert "读不出来" in r
    assert (ws / "g.py").read_bytes() == b"keep\n"
    r2 = patch.apply_patch(ctx, patch="--- a/g.py\n+++ b/g.py\n@@ -1,1 +1,1 @@\n!keep\n+x\n")
    assert "行首标记" in r2
    r3 = patch.apply_patch(ctx, patch="@@ -1,1 +1,1 @@\n-keep\n+x\n")
    assert "不属于任何文件" in r3


def test_rename_and_binary_are_refused_with_a_reason(ws, ctx):
    (ws / "h.py").write_bytes(b"keep\n")
    r = patch.apply_patch(ctx, patch="diff --git a/h.py b/i.py\nrename from h.py\n"
                                     "rename to i.py\n")
    assert "rename" in r and "不收" in r
    assert (ws / "h.py").read_bytes() == b"keep\n"
    r2 = patch.apply_patch(ctx, patch="Binary files a/h.py and b/h.py differ\n")
    assert "二进制" in r2


def test_new_file_that_already_exists_is_refused(ws, ctx):
    """补丁说"新建"而磁盘上已经有 —— 那就不是新建,不能覆盖一份没读过的东西。"""
    (ws / "i.py").write_bytes(b"existing\n")
    p = "--- /dev/null\n+++ b/i.py\n@@ -0,0 +1,1 @@\n+brand new\n"
    assert "已经在磁盘上" in patch.apply_patch(ctx, patch=p)
    assert (ws / "i.py").read_bytes() == b"existing\n"


def test_dry_run_computes_everything_and_writes_nothing(ws, ctx):
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\nl4\n")
    (ws / "b.txt").write_bytes(b"x\ny\n")
    r = patch.apply_patch(ctx, patch=TRIPLE, dry_run=True)
    assert "预演" in r and "什么都没改" in r
    assert (ws / "a.py").read_bytes() == b"l1\nl2\nl3\nl4\n"
    assert (ws / "b.txt").exists()
    assert not (ws / "c").exists()


def test_absolute_and_traversal_paths_are_refused_by_the_same_fence(ws, ctx):
    """路径判定不在补丁里重写一套:走的还是 `resolve_path(..., for_write=True)`。"""
    outside = ws.parent / "outside.txt"
    p = f"--- /dev/null\n+++ b/{outside}\n@@ -0,0 +1,1 @@\n+sneak\n"
    r = patch.apply_patch(ctx, patch=p)
    assert "整包没落盘" in r and "超出工作区" in r
    assert not outside.exists()
    p2 = "--- /dev/null\n+++ b/../escape.txt\n@@ -0,0 +1,1 @@\n+sneak\n"
    assert "整包没落盘" in patch.apply_patch(ctx, patch=p2)
    assert not (ws.parent / "escape.txt").exists()


def test_too_many_files_says_so_instead_of_truncating_silently(ws, ctx):
    body = "".join(f"--- /dev/null\n+++ b/f{n}.txt\n@@ -0,0 +1,1 @@\n+x\n"
                   for n in range(45))
    _patches, problems = patch.parse(body)
    assert problems and "最多改 40 个文件" in problems[0]


# ---------------------------------------------------------------- B. 门

@pytest.mark.parametrize("raw,want", [
    (".env", "credential"),
    (".git/hooks/pre-commit", "git"),
])
def test_a_patch_hiding_a_red_line_still_reports_that_gate(ws, registry, raw, want):
    """补丁里藏一份凭据/`.git`,问的还是那道门自己的名字。

    名字被糊成 `mode` 或 `unread` 的后果不是文案难看:`NON_DELEGABLE_ASKS` 与
    `NEVER_REMEMBER_ASKS` 都按 kind 判,于是凭据文件可以被一条钩子代答、
    也可以被一次「以后都允许」永久放行 —— 那才是真出事。
    """
    p = (f"--- /dev/null\n+++ b/{raw}\n@@ -0,0 +1,1 @@\n+x\n")
    gate = _gate(ws, registry)
    assert gate.check(registry["apply_patch"], {"patch": p}) == ASK
    assert gate.ask_kind == want


def test_a_patch_touching_outside_paths_asks_once(ws, registry):
    p = "--- /dev/null\n+++ b/../outside.txt\n@@ -0,0 +1,1 @@\n+x\n"
    gate = _gate(ws, registry)
    assert gate.check(registry["apply_patch"], {"patch": p}) in (ASK, REJECT)


def test_one_bad_file_among_good_ones_is_the_reason_we_ask_per_file(ws, registry):
    """三份里只有一份碰 `.env`:那一问的理由必须是凭据,而不是笼统"这一步要确认"。"""
    (ws / "ok.py").write_bytes(b"a\n")
    p = ("--- a/ok.py\n+++ b/ok.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
         "--- /dev/null\n+++ b/.env\n@@ -0,0 +1,1 @@\n+SECRET=1\n"
         "--- a/ok.py\n+++ b/ok.py\n@@ -1,1 +1,1 @@\n-a\n+b\n")
    gate = _gate(ws, registry)
    assert gate.check(registry["apply_patch"], {"patch": p}) == ASK
    assert gate.ask_kind == "credential"


def test_external_upgrade_gate_covers_patches(ws, registry):
    """接触过外部内容之后,一包补丁想写进 `kb/` 必须被升格门拦下来问。"""
    (ws / "kb").mkdir()
    p = "--- /dev/null\n+++ b/kb/inject.md\n@@ -0,0 +1,1 @@\n+x\n"
    gate = _gate(ws, registry)
    gate.note_external("web_crawl")
    assert gate.check(registry["apply_patch"], {"patch": p}) == ASK
    assert gate.ask_kind == "external"
    # 没接触过外部内容时同一包补丁不该由这条开口(否则就是无端加严)
    gate2 = _gate(ws, registry)
    gate2.check(registry["apply_patch"], {"patch": p})
    assert gate2.ask_kind != "external"


def test_project_deny_wins_over_the_patch_gate(ws, registry, monkeypatch, tmp_path):
    """用户自己写的 deny 比"补丁里有一份 .env 要不要问"更先说话(两个都拒时理由不同)。"""
    from minicode import permspec
    monkeypatch.setattr(permspec, "trust_path", lambda: tmp_path / "trust.json")
    (ws / permspec.PERMISSIONS_FILE).write_text(json.dumps(
        {"allow": [{"tool": "apply_patch", "path_prefix": "kb/", "decision": "deny"}]}),
        encoding="utf-8")
    sb = SandboxState(SandboxPolicy(), ws)
    permspec.trust(sb.workspace, permspec.load(ws, set(registry)).sha, 1)
    gate = PermissionGate(_cfg("full"), sandbox=sb, workspace=ws,
                          tool_names=set(registry))
    p = "--- /dev/null\n+++ b/kb/x.md\n@@ -0,0 +1,1 @@\n+x\n"
    assert gate.check(registry["apply_patch"], {"patch": p}) == REJECT
    assert "第 1 条" in gate.reject_reason


def test_full_mode_still_asks_for_a_patch_touching_credentials(ws, registry):
    """`full` 档的承诺是"沙箱内一律不问",但凭据那一层比档位高(与 write_file 同口径)。"""
    p = "--- /dev/null\n+++ b/.env\n@@ -0,0 +1,1 @@\n+x\n"
    gate = _gate(ws, registry, mode="full")
    assert gate.check(registry["apply_patch"], {"patch": p}) == ASK
    assert gate.ask_kind == "credential"


def test_unread_gate_does_not_silence_a_patch_and_concurrency_sees_the_ask(ws, registry):
    """read-before-edit 那枚钉不看 apply_patch(补丁自带上下文),但红线的问照样问。

    自动编辑档里补丁与 write_file/edit_file 同权重(它就是"一次写好几份"的编辑),
    所以普通工作区文件自动放行;而 `_patch_gate` 那层排在档位之前,`.env` 混在包里
    仍然要问 —— 这两条一起成立才算"自动放行只放普通文件"。
    """
    (ws / "note.txt").write_bytes(b"a\n")
    gate = _gate(ws, registry, mode="accept_edits")
    plain = {"path": "note.txt", "old_string": "a", "new_string": "b"}
    assert gate.check(registry["edit_file"], plain) == ASK      # 没读过 → accept_edits 也问
    assert gate.would_ask(registry["edit_file"], plain) is True
    p = "--- a/note.txt\n+++ b/note.txt\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    assert gate.check(registry["apply_patch"], {"patch": p}) == "allow"
    assert gate.would_ask(registry["apply_patch"], {"patch": p}) is False
    # 同一包里混一份凭据:自动放行档也要问,而且问的理由是凭据那一层
    mixed = p + "--- /dev/null\n+++ b/.env\n@@ -0,0 +1,1 @@\n+S=1\n"
    gate2 = _gate(ws, registry, mode="accept_edits")
    assert gate2.check(registry["apply_patch"], {"patch": mixed}) == ASK
    assert gate2.ask_kind == "credential"


def test_plan_lock_denies_a_patch_too(ws, registry):
    (ws / "a.py").write_bytes(b"a\n")
    gate = _gate(ws, registry)
    gate.lock_plan("看看就行")
    p = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    assert gate.check(registry["apply_patch"], {"patch": p}) == REJECT


def test_plan_lock_is_not_weakened_by_a_credential_in_the_pack(ws, registry):
    """锁着规划时,一包碰 `.env` 的补丁必须是**拒**,不是"问一句让人点头"。

    `_patch_gate` 若排在规划锁之前,这把锁就在这一条路径上被降级成一次确认 ——
    而"未批不许写"要防的恰恰就是"人在一张他看不懂的框上点了允许"。
    """
    gate = _gate(ws, registry)
    gate.lock_plan("先看清楚再动")
    p = "--- /dev/null\n+++ b/.env\n@@ -0,0 +1,1 @@\n+S=1\n"
    assert gate.check(registry["apply_patch"], {"patch": p}) == REJECT
    assert gate.ask_kind != "credential"


def test_apply_patch_is_not_a_read_only_or_concurrent_tool(registry):
    """写类工具不进并发名单:handler 之前门要问人,而问人不能发生在工作线程里。"""
    tool = registry["apply_patch"]
    assert tool.read_only is False
    assert tool.concurrent_safe is False


class NoteUI(FakeUI):
    """把确认框收到的那句"为什么要问"也记下来 —— 这条测试要的正是那句话。"""

    def __init__(self):
        super().__init__(confirm_choice="yes")
        self.notes = []

    def confirm_tool(self, tool, args, reason="", note="", can_remember=False):
        self.confirmed.append((tool.name, dict(args)))
        self.notes.append(note)
        self.reasons.append(reason)
        return self.confirm_choice


def test_the_packs_after_a_patch_count_as_read(ws, registry):
    """打完一包补丁,那几份文件的新内容就是它刚写进去的 —— 下一步改它们不该再多问一句
    「没读过」;而包里没动过的另一份文件,那句问话必须还在(否则这枚钉就漏了)。"""
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\nl4\n")
    (ws / "b.txt").write_bytes(b"x\ny\n")
    (ws / "other.txt").write_bytes(b"z\n")
    ui = NoteUI()
    agent = _agent(ws, ui, registry, [
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="apply_patch",
                                           arguments=json.dumps({"patch": TRIPLE}))]),
        AssistantTurn(tool_calls=[ToolCall(
            id="c2", name="edit_file", arguments=json.dumps(
                {"path": "a.py", "old_string": "added", "new_string": "more"}))]),
        AssistantTurn(tool_calls=[ToolCall(
            id="c3", name="edit_file", arguments=json.dumps(
                {"path": "other.txt", "old_string": "z", "new_string": "Z"}))]),
        AssistantTurn(text="都好"),
    ])
    agent.run_turn("打一包补丁再改两处")
    assert "more" in (ws / "a.py").read_text(encoding="utf-8")
    names = [n for n, _ in ui.confirmed]
    assert names == ["apply_patch", "edit_file", "edit_file"]   # confirm 档每写必问
    assert ui.notes[0] == ""                      # 补丁本身不问"读过没有"
    assert ui.notes[1] == ""                      # a.py 刚被这包补丁写过 → 算读过
    assert "没读过" in ui.notes[2]                # other.txt 没在包里 → 照旧提醒


def test_undo_walks_the_whole_pack_back(ws, registry):
    """整包原子要连回滚一起才算:一包改三份,`/undo` 一次把三份都退回去。"""
    (ws / "a.py").write_bytes(b"l1\nl2\nl3\nl4\n")
    (ws / "b.txt").write_bytes(b"x\ny\n")
    before_a = (ws / "a.py").read_bytes()
    ui = FakeUI()
    agent = _agent(ws, ui, registry, [
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="apply_patch",
                                           arguments=json.dumps({"patch": TRIPLE}))]),
        AssistantTurn(text="好了"),
    ])
    agent.run_turn("打一包补丁")
    assert (ws / "c" / "new.md").exists() and not (ws / "b.txt").exists()
    msg = agent.undo_all()
    assert "回退" in msg or "退" in msg
    assert (ws / "a.py").read_bytes() == before_a      # 改的那份原文回来
    assert (ws / "b.txt").read_bytes() == b"x\ny\n"    # 删掉的那份回来
    assert not (ws / "c" / "new.md").exists()          # 新建的那份被带走


def _agent(ws, ui, registry, turns):
    from minicode.agent import Agent
    cfg = _cfg("confirm")
    sb = SandboxState(SandboxPolicy(), ws)
    gate = PermissionGate(cfg, sandbox=sb, workspace=ws, tool_names=set(registry))
    ctx = ToolContext(workspace=ws, config=cfg, sandbox=sb)
    return Agent(llm=FakeLLM(turns), registry=list(registry.values()),
                 session=Session(system_prompt="sys"), gate=gate, ui=ui, ctx=ctx)


def test_headless_refuses_the_pack_and_says_which_tool(ws, registry):
    """无头模式不替人点头 —— 一包补丁在 --json 里就是"该问却没人可问"。"""
    from minicode.headless import HeadlessUI
    hui = HeadlessUI(FakeUI())
    tool = registry["apply_patch"]
    assert hui.confirm_tool(tool, {"patch": TRIPLE}, reason="") == "no"
    assert hui.asks_refused[-1]["name"] == "apply_patch"


def test_the_web_edition_drops_the_tool_from_the_catalog():
    """对外只读版没有写盘工具:这一只与 write_file 同权重,必须被摘掉。"""
    from minicode import web_profile
    assert "apply_patch" in web_profile.EXCLUDED_TOOLS
