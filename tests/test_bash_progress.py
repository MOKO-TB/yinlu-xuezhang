# -*- coding: utf-8 -*-
"""长命令跑到一半要看得见(v0.9.90,§6 第 9 条的后半句)。

以前前台 `bash` 走 `subprocess.run(capture_output=True)`:命令跑完才一次给全,
一条几分钟的命令中间界面完全是黑的 —— 用户分不清"在算"与"卡死"。现在自己起进程、
两条管道各一个读线程、每 `PROGRESS_EVERY` 秒向界面报一行。

这一层最怕的三件事,下面逐条钉:
1. **模型看到的东西不许变**:进度只走 UI 通道,不进会话消息、不进工具回话
   (与 v0.9.85 后台状态"搭在请求末尾、不进留档"同一取舍);
2. **两路输出必须还在各自那一段**:换成自己读管道之后如果合流,`--- stderr ---`
   那一段就没了,而失败判定与排查都靠它分开;
3. **进度报不出去绝不能影响命令本身**,UI 挂了/没有 UI(单测、脚本直接调 handler)
   都得照跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

from minicode import activity, gui_ui
from minicode.config import Config, Provider
from minicode.subagents import _QuietUI
from minicode.tools import shell
from minicode.tools.base import Tool, ToolContext

ROOT = Path(__file__).resolve().parent.parent


def _cfg():
    return Config(providers=[Provider(id="t", name="T", base_url="https://x",
                                      api_key="k", model="m")],
                  active_provider_id="t", shell="auto")


class RecordingUI:
    """只收 tool_progress 的 UI 替身(raise_=True 用来演"界面自己挂了")。"""

    def __init__(self, raise_=False):
        self.lines: list[str] = []
        self.raise_ = raise_

    def tool_progress(self, text: str) -> None:
        if self.raise_:
            raise RuntimeError("evaluate_js 挂了")
        self.lines.append(text)


def _ui_ctx(tmp_path, ui=None) -> ToolContext:
    return ToolContext(workspace=tmp_path, config=_cfg(), ui=ui)


def _script(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _cmd(script) -> str:
    """把脚本路径交给 shell:引号包起来,别让反斜杠被 shell 吃掉。"""
    return f'python "{str(script).replace(chr(92), "/")}"'


# ---------------------------------------------------------------- shell 那一侧


def test_progress_is_emitted_while_a_long_command_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(shell, "PROGRESS_EVERY", 0.3)
    ui = RecordingUI()
    # flush=True 是必须的:子进程的 stdout 接的是管道,它会**攒着**到退出才吐 ——
    # 那种时候中间一行都读不到(进度是尽力而为,不是逐字符转播;`python -u` 同理)。
    script = _script(tmp_path, "slow.py",
                     "import time\nprint('one', flush=True)\ntime.sleep(0.6)\n"
                     "print('two', flush=True)\n")
    out = shell.bash(_ui_ctx(tmp_path, ui), _cmd(script))
    assert "退出码: 0" in out and "one" in out and "two" in out
    assert ui.lines, "跑了一半没报过一行进度 —— 中间那段仍然是黑的"
    assert any("已输出" in l for l in ui.lines)
    assert any("最后一行: one" in l for l in ui.lines), ui.lines


def test_the_models_view_is_untouched_by_progress(tmp_path, monkeypatch):
    """进度是给**人**看的:模型那份回话一个字都不许多(否则每步都在往留档里塞旧闻)。"""
    monkeypatch.setattr(shell, "PROGRESS_EVERY", 0.15)
    ui = RecordingUI()
    script = _script(tmp_path, "slow2.py",
                     "import time\nprint('a', flush=True)\ntime.sleep(0.4)\n"
                     "print('b', flush=True)\n")
    out = shell.bash(_ui_ctx(tmp_path, ui), _cmd(script))
    assert ui.lines, "前提:这一条真的报过进度"
    assert "已跑" not in out and "↳" not in out


def test_stdout_and_stderr_stay_in_their_own_sections(tmp_path):
    """两路各一个读线程,但回话里仍是两段 —— 失败排查靠"这句是 stderr 说的"。"""
    script = _script(tmp_path, "both.py",
                     "import sys\nprint('to-out')\nsys.stderr.write('to-err\\n')\n")
    out = shell.bash(_ui_ctx(tmp_path), _cmd(script))
    assert "--- stdout ---\nto-out\n--- stderr ---\nto-err" in out


def test_both_pipes_filling_up_does_not_deadlock(tmp_path):
    """只读一条、或先 wait 再读,都会卡在写满的那条管道上 —— 这是这类改写最经典的坑。

    两路各写 300 KB(远超 4~8 KB 的管道缓冲),命令必须正常结束、两路都要有内容。
    """
    script = _script(tmp_path, "big.py",
                     "import sys\n"
                     "sys.stdout.write('o' * 300000 + '\\n')\n"
                     "sys.stderr.write('e' * 300000 + '\\n')\n")
    out = shell.bash(_ui_ctx(tmp_path), _cmd(script), timeout=60)
    assert "退出码: 0" in out
    assert "只保留了前" in out, "超量输出要照实说截断了,不能静默少给"


def test_a_fast_command_gets_no_progress_line(tmp_path):
    """默认 15 秒一格:秒完的命令不该有任何进度行(每条都贴一句等于没有)。"""
    ui = RecordingUI()
    assert shell.bash(_ui_ctx(tmp_path, ui), "echo quick")
    assert ui.lines == []


def test_no_ui_at_all_still_runs_the_command(tmp_path):
    """ToolContext 默认 ui=None:单测与脚本直接调 handler 不能因为这条链断掉。"""
    out = shell.bash(ToolContext(workspace=tmp_path, config=_cfg()), "echo no-ui")
    assert "no-ui" in out and "退出码: 0" in out


def test_a_broken_ui_cannot_break_the_command(tmp_path, monkeypatch):
    monkeypatch.setattr(shell, "PROGRESS_EVERY", 0.15)
    script = _script(tmp_path, "slow3.py",
                     "import time\nprint('x')\ntime.sleep(0.4)\nprint('y')\n")
    out = shell.bash(_ui_ctx(tmp_path, RecordingUI(raise_=True)), _cmd(script))
    assert "退出码: 0" in out and "x" in out and "y" in out


def test_timeout_reports_what_was_already_produced(tmp_path):
    script = _script(tmp_path, "hang.py",
                     "import sys, time\n"
                     "sys.stdout.write('partial-line\\n'); sys.stdout.flush()\n"
                     "time.sleep(30)\n")
    out = shell.bash(_ui_ctx(tmp_path), _cmd(script), timeout=1)
    assert out.startswith("错误: 命令超时"), out[:80]
    assert "partial-line" in out, "超时也要把已经产出的那截带回来"


def test_progress_uses_the_ui_channel_only_when_it_exists():
    """`_report_progress` 认的是"有没有 tool_progress 这个方法",不是某个具体 UI 类型。

    这条钉的是接口形状:TerminalUI / GuiUI / 会话适配器都要有它,少一个就是静默失灵。
    """
    for cls in (gui_ui.GuiUI,):
        assert hasattr(cls, "tool_progress"), f"{cls.__name__} 少了 tool_progress"
    from minicode.ui import TerminalUI
    assert hasattr(TerminalUI, "tool_progress")


# ---------------------------------------------------------------- 界面那一侧


def test_guiui_progress_lands_on_the_running_row_and_leaves_with_it():
    ui = gui_ui.GuiUI()
    tool = Tool(name="bash", description="", parameters={}, handler=lambda **k: "")
    ui.tool_start(tool, {"command": "pytest -q"})
    ui.tool_progress("已跑 20 秒 · 已输出 512 字 · 最后一行: tests/test_x.py ..")
    now = ui.live_now()
    assert now and "已输出 512 字" in now["progress"]
    ui.tool_done(tool, "退出码: 0")
    assert ui.live_now() is None, "步一结束,进度跟着那行一起收掉(不留'还在跑'的假象)"
    # 时间线里那一份快照也带着进度(面板与时间线同源)
    assert ui.live_rows()[-1]["progress"].startswith("已跑")


def test_quiet_ui_swallows_progress_from_subagents():
    """子代理的进度不进主会话那一栏:几路并发一起报会在同一行互相盖。"""
    seen = []

    class Inner:
        def tool_progress(self, text):
            seen.append(text)

    quiet = _QuietUI(Inner())
    quiet.tool_progress("已跑 30 秒")
    assert seen == [], "_QuietUI._SILENT 没登记 tool_progress 就会把子代理的进度串进来"


def test_subagent_gets_its_own_route_context(monkeypatch, tmp_path):
    """光有 `_QuietUI` 还不够:工具的进度是走 **ctx.ui** 的,不是走 agent.ui。

    并行批次里若 `run_subagent` 继续沿用父 ctx,几路子代理的 bash 进度就会盖到
    父会话「此刻」那一行上(那一行写的是父会话最深一层在跑的东西)—— 串味的进度
    等于假进度。所以每一路都得拿到自己的 ui。
    """
    from minicode import subagents
    captured = {}

    class StubAgent:
        def __init__(self, **kw):
            captured.update(kw)
            self._undo_stack = None

        def run_turn(self, text):
            captured["ran"] = text

    monkeypatch.setattr(subagents, "Agent", StubAgent)
    parent = RecordingUI()
    ctx = ToolContext(workspace=tmp_path, config=_cfg(), ui=parent)
    quiet = _QuietUI(parent)
    subagents.run_subagent(ctx, subagents.SUBAGENT_TYPES["explore"], "去看代码",
                           llm=object(), ui=quiet, config=_cfg(), registry=[])
    assert captured["ctx"].ui is quiet, "子代理还在用父会话的 ui 递进度"
    assert captured["ctx"].workspace == tmp_path and captured["ctx"].config is ctx.config
    assert captured["ui"] is quiet


def test_now_line_carries_progress_and_defaults_to_empty():
    got = activity.now_line({"name": "bash", "summary": "pytest", "start": 1,
                             "elapsed": 4, "progress": "已输出 12 字 · 最后一行: collecting"})
    assert got["progress"] == "已输出 12 字 · 最后一行: collecting"
    assert activity.now_line({"name": "bash", "summary": "x", "start": 1,
                              "elapsed": 1})["progress"] == ""


def test_the_frontend_reads_the_backends_progress_and_refreshes_on_the_event():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "now.progress" in html, "前端没消费 progress 这个字段(后端白算)"
    assert 'id = "actNowProgress"' in html
    assert 'e.type === "tool_progress"' in html, "进度事件没接刷新:那一行永远停在旧值"
    assert "actNowProgress" in html
    # 没有进度时那一行整条不出现(不写"刚开始"凑数)
    assert 'if (now.progress) {' in html


def test_frozen_needles_cover_the_new_streaming_symbols():
    """漏收 = 装好的包里 bash 照跑、进度静默没有(而这一轮的卖点就是"看得见")。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_frozen_version import TARGETS
    assert "tool_progress" in TARGETS["minicode.gui_ui"]
    needles = TARGETS["minicode.tools.shell"]
    for name in ("_Progress", "_drain", "_report_progress", "PROGRESS_EVERY"):
        assert name in needles, f"shell 的 {name} 没进冻结针清单"


def test_progress_line_texts_are_backed_by_real_counters():
    """进度那一行不许出现"大概/预计"这类字:它只有已跑秒数、已输出字数与最后一行。"""
    prog = shell._Progress()
    prog.feed("first\n")
    prog.feed("second line\n")
    line = prog.line(12.9, 300)
    assert "已跑 12 秒(上限 300s)" in line
    assert "已输出 18 字" in line and "最后一行: second line" in line
    assert not shell._Progress().line(0, 5).startswith("最后一行")
    assert "还没有输出" in shell._Progress().line(0, 5)
