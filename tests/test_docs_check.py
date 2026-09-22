"""`scripts/check_docs.py` 的三条对账要能在 pytest 里跑,不能只靠人记得执行。

上一轮的教训:文档里的测试基线数(1188)和实测(1142)漂了一整轮没人发现,
个人数据文件靠人肉记进清理清单 —— 这一轮加 `watchdog.log` 时才发现
`weather.json`、`web-sessions/`、`web-usage.log` 三份从来没人加过。
把"记得跑一下"变成测试,才是真的会跑。

`--collect` 那一项(跑一次 pytest --collect-only)放在测试里会自引用地慢,
所以测试只覆盖三条静态对账;收集数那项留在提交前手工跑脚本。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def test_handoff_header_matches_source_version():
    assert check_docs.check_version() == [], "HANDOFF 抬头的版本号和源码不是一处真相"


def test_every_local_state_file_is_ignored_and_cleaned():
    """新增本机状态文件却忘了清理清单 = 把同学自己的数据随安装包发出去。"""
    assert check_docs.check_state_targets() == []


def test_state_scan_actually_finds_the_real_files():
    """防"检查器扫不到东西所以永远绿":扫出来的必须包含这几份已知本机状态。

    没有这一条,一个坏掉的正则会让整条数据边界检查变成空转。
    """
    found = check_docs.state_targets()
    for name in ("schedule.json", "reminders.json", "period-times.json",
                 "pushed-today.json", "watchdog.log", "weather.json",
                 "web-sessions", "web-usage.log", "migrate-backup"):
        assert name in found, f"{name} 在代码里写进数据目录,却没被扫出来"


def test_state_exemptions_are_all_still_needed():
    """例外清单里躺着一份其实没人写的状态 = 例外成了垃圾桶。"""
    found = check_docs.state_targets()
    stale = [n for n in check_docs.STATE_EXEMPT if n not in found]
    assert not stale, f"{stale} 已不在代码里被写入,例外条目该删"


def test_handoff_milestones_stay_archived():
    """§2 只留最近几轮;超长说明这一轮又忘了归档。"""
    assert check_docs.check_handoff_size() == []


def test_archived_milestones_are_not_silently_dropped():
    """归档是搬走不是删掉:主文档 + 归档文件里必须真有那几张表。"""
    archive = ROOT / "docs" / "handoff" / "milestones.md"
    assert archive.exists(), "§2 说归档在 docs/handoff/,文件却不在"
    text = archive.read_text(encoding="utf-8")
    assert "里程碑" in text
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("| v")]
    assert len(rows) >= 40, f"归档里只剩 {len(rows)} 条里程碑,不像搬全了"
    # 真搬走了:归档里最新的那条不该还在主文档 §2 的行首(正文里提到 v0.9.22 是另一回事),
    # 当前版本那条该在。
    section2 = (ROOT / "HANDOFF.md").read_text(encoding="utf-8") \
        .split("## 2.", 1)[1].split("\n## 3.", 1)[0]
    heads = {ln.split()[1] for ln in section2.splitlines() if ln.startswith("| v")}
    newest_archived = rows[0].split()[1]                    # 如 v0.9.22
    assert newest_archived not in heads, f"{newest_archived} 两处都有行首 = 没搬干净"
    assert "v" + check_docs.version_from_source() in heads, "最近一轮被归档掉了"
