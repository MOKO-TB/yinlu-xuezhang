"""marker 分层这件事本身也要有针(#87)。

分层的全部价值在于"日常那次快跑真的快" —— 而它会**静默失效**:有人新建一个耗时
文件忘了打标,或者觉得 marker 碍事删掉一行,`-m "not slow"` 就又变回六分钟,而且
没有任何东西会红。

这里量的是**时间维度的那条边界**,不是条数:2026-09-25 那次 `--durations=0` 实测,
全量 342 s 里有 240 s 花在五个文件上(它们慢在真线程 / 真 socket / 真超时等待,
不是算得慢),但那五个文件只有 165 项 —— 按条数看只占 1/10,按秒数看占 7/10。
所以断言写成"快车道里一项都不许来自这五个文件",而不是"快车道条数少一半"。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 实测耗时(2026-09-25,`pytest -q --durations=0`):64 / 51 / 51 / 37 / 37 秒
HEAVY_FILES = ("test_gui_bridge.py", "test_subagent.py", "test_web_server.py",
               "test_model_manage.py", "test_workspace.py")
# 这两个文件里各有一条真等 5 s 超时的用例(整文件不慢,只有这一条慢)
SLOW_ONES = ("test_shell.py::test_timeout",
             "test_sandbox.py::test_shell_timeout_clamped_by_sandbox")


def _collect_ids(*args: str) -> list[str]:
    proc = subprocess.run([PY, "-m", "pytest", "--collect-only", "-q", *args],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = proc.stdout or ""
    ids = [ln for ln in out.splitlines() if "::" in ln]
    if not ids and proc.returncode not in (0, 5):
        raise AssertionError(f"collect 失败(退出码 {proc.returncode}):\n{out[-800:]}")
    return ids


def test_markers_are_registered_so_a_typo_cannot_pass_silently():
    text = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert re.search(r"^markers\s*=", text, re.M), "pytest.ini 没注册 markers"
    assert "slow:" in text, "slow 这个 marker 没写说明(将来没人知道它凭什么算慢)"


def test_heavy_files_are_still_marked():
    """删掉一行 `pytestmark` 不会让任何测试变红 —— 所以这里盯着它。"""
    for name in HEAVY_FILES:
        text = (ROOT / "tests" / name).read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.slow" in text, f"{name} 的 slow 标记没了"
    for item in SLOW_ONES:
        fname, fn = item.split("::")
        lines = (ROOT / "tests" / fname).read_text(encoding="utf-8").splitlines()
        hits = [i for i, ln in enumerate(lines) if ln.startswith(f"def {fn}(")]
        assert len(hits) == 1, f"{item} 在文件里找到 {len(hits)} 处定义"
        above = lines[hits[0] - 1]
        assert above.startswith("@pytest.mark.slow"), \
            f"{item} 上一行不是 slow 标记,而是:{above}"


def test_the_fast_lane_excludes_every_heavy_file():
    """`-m "not slow"` 收到的节点里,一项都不许来自那五个耗时文件。"""
    fast = _collect_ids("-m", "not slow")
    assert len(fast) > 1000, f"快车道只收到 {len(fast)} 项,collect 本身就不对"
    offenders = [i for i in fast if i.split("::")[0].split("/")[-1] in HEAVY_FILES]
    assert not offenders, f"快车道里混进了耗时文件的用例:{offenders[:5]}"


def test_the_slow_lane_is_not_empty_and_holds_the_heavy_files():
    slow = _collect_ids("-m", "slow")
    assert slow, "`-m slow` 一项都没筛出来:标记形同虚设"
    files = {i.split("::")[0].split("/")[-1] for i in slow}
    assert set(HEAVY_FILES) <= files, f"慢车道缺了:{set(HEAVY_FILES) - files}"
    for item in SLOW_ONES:
        assert any(i.replace("\\", "/").endswith(item) for i in slow), \
            f"{item} 没进慢车道"
