"""`session_keep_last` 真接上了启动路径。

这条测试存在的理由很具体:v0.9.66 那一笔同时写了 `prune_sessions()`(正确)、配置字段
(登记齐)、README 与 `config.example.json` 里那句「启动时按时间删旧的」(**承诺**)、
以及 5 条测试(全绿)—— 唯独**一个调用者都没写**。函数没问题所以测试没问题,
于是这个开关在真机上从来没生效过,`sessions/` 只会一直变多。

所以这里钉两件事:①策略本身按承诺工作(含"当前那份永不删");
②**两个入口的源码里真有调用点** —— 那才是上一轮唯一缺的东西。
"""
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode import app_setup

ROOT = Path(__file__).resolve().parent.parent


class _Ui:
    """只收 ui.info 那一句:清理这种事说了没有,是这条承诺的一半。"""

    def __init__(self):
        self.lines: list[str] = []

    def info(self, text: str):
        self.lines.append(text)


def _sessions(dir_: Path, names: list[str], gap_days: int = 1) -> dict[str, Path]:
    """按 names 顺序造留档,越靠前越新(mtime 递减)。"""
    dir_.mkdir(parents=True, exist_ok=True)
    made = {}
    now = time.time()
    for i, name in enumerate(names):
        p = dir_ / name
        p.write_text('{"type": "user"}\n', encoding="utf-8")
        when = now - (i + 1) * gap_days * 86400
        os.utime(p, (when, when))
        made[name] = p
    return made


@pytest.fixture
def sessions_dir(tmp_path):
    d = tmp_path / "sessions"
    return d


def test_retention_deletes_the_oldest_and_says_the_count(sessions_dir):
    made = _sessions(sessions_dir, ["session_new.jsonl", "session_mid.jsonl",
                                    "session_a.jsonl", "session_b.jsonl",
                                    "session_c.jsonl"])
    ui = _Ui()
    removed = app_setup.apply_retention(SimpleNamespace(session_keep_last=2), ui,
                                        sessions_dir=sessions_dir)
    assert sorted(removed) == ["session_a.jsonl", "session_b.jsonl", "session_c.jsonl"]
    assert made["session_new.jsonl"].exists() and made["session_mid.jsonl"].exists()
    assert len(ui.lines) == 1, ui.lines
    assert "删掉 3 份" in ui.lines[0] and "session_keep_last=2" in ui.lines[0]
    assert str(sessions_dir) in ui.lines[0]     # 说清动的是哪个目录,不含糊过去


def test_default_zero_keeps_everything_and_says_nothing(sessions_dir):
    """默认 0 = 全部保留:一个文件都不动,也不该在启动时啰嗦一句。"""
    made = _sessions(sessions_dir, ["session_a.jsonl", "session_b.jsonl"])
    ui = _Ui()
    assert app_setup.apply_retention(SimpleNamespace(session_keep_last=0), ui,
                                     sessions_dir=sessions_dir) == []
    assert all(p.exists() for p in made.values())
    assert ui.lines == []


def test_the_resumed_log_is_never_deleted(sessions_dir):
    """接着用的那一份进不了删除名单 —— 时钟被手改时,"最新"未必真最新。"""
    made = _sessions(sessions_dir, ["session_new.jsonl", "session_mid.jsonl",
                                    "session_old.jsonl"])
    removed = app_setup.apply_retention(
        SimpleNamespace(session_keep_last=1), _Ui(),
        current=made["session_old.jsonl"], sessions_dir=sessions_dir)
    assert removed == ["session_mid.jsonl"], removed
    assert made["session_old.jsonl"].exists()
    assert made["session_new.jsonl"].exists()


def test_missing_sessions_dir_is_not_an_error(tmp_path):
    """还没落过盘的新机器:目录不存在 = 没东西可删,不该在启动路径上报错。"""
    ui = _Ui()
    assert app_setup.apply_retention(SimpleNamespace(session_keep_last=50), ui,
                                     sessions_dir=tmp_path / "nope") == []
    assert ui.lines == []


# ---------------------------------------------------- 调用点(这才是上一轮缺的那一半)

@pytest.mark.parametrize("entry", ["main.py", "gui_main.py"])
def test_every_entry_actually_calls_retention(entry):
    """`prune_sessions` 有过"函数、测试、文档全齐,调用者为 0"的前科。

    这条扫的是**调用点存在**:两个入口都得调 `apply_retention`,否则配置里那个数是装饰。
    """
    src = (ROOT / entry).read_text(encoding="utf-8")
    assert "apply_retention(" in src, f"{entry} 没接留档清理,session_keep_last 就是假设置"


def test_the_cli_wires_it_after_the_agent_is_built():
    """顺序有意义:要先建好 session(才知道"当前这份"是哪个),再删。

    写反了不会报错,只会把这次接着用的那份留档当旧的删掉。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert src.index("agent, session = create_agent(") < src.index("apply_retention(")
