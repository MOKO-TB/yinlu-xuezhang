# -*- coding: utf-8 -*-
"""真实编程任务评测骨架:固定任务 + 独立验收 + 可重复运行的 JSON 报告。

用法(项目根目录):
    .venv/Scripts/python.exe scripts/bench_coding_tasks.py --list
    .venv/Scripts/python.exe scripts/bench_coding_tasks.py --fake          # 自检评测流程
    .venv/Scripts/python.exe scripts/bench_coding_tasks.py --json-out out.json

真实模式使用当前配置的模型,在临时工作区里以自动放行运行;验收由本脚本在
沙箱外用 pytest 独立执行。每个任务都从模板重建,绝不写用户工作区。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minicode import headless                       # noqa: E402
from minicode.app_setup import create_agent         # noqa: E402
from minicode.config import Config, load_config     # noqa: E402
from minicode.llm import AssistantTurn, ToolCall    # noqa: E402

TASK_TIMEOUT = 60


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    files: dict[str, str]
    test_file: str
    fix: dict[str, str] = field(default_factory=dict)


TASKS: tuple[Task, ...] = (
    Task(
        id="fix-last-n",
        prompt=("修复 stats.py 里的 last_n:传 0 必须返回空列表,传负数必须抛 ValueError。"
                "改完后运行 python -m pytest tests/test_stats.py -q 直到通过。"),
        files={
            "stats.py": "def last_n(items, n):\n    return items[-n:]\n",
            "tests/test_stats.py": (
                "import pytest\nfrom stats import last_n\n\n"
                "def test_two():\n    assert last_n([1, 2, 3], 2) == [2, 3]\n\n"
                "def test_zero():\n    assert last_n([1, 2, 3], 0) == []\n\n"
                "def test_negative():\n    with pytest.raises(ValueError):\n"
                "        last_n([1], -1)\n"),
        },
        test_file="tests/test_stats.py",
        fix={"stats.py": (
            "def last_n(items, n):\n    if n < 0:\n        raise ValueError('n must be >= 0')\n"
            "    return items[-n:] if n else []\n")},
    ),
    Task(
        id="validate-port",
        prompt=("修复 net.py 的 parse_port:只接受 1-65535 的整数或数字字符串,"
                "其余输入一律 ValueError。运行 pytest tests/test_net.py -q 验证。"),
        files={
            "net.py": "def parse_port(raw):\n    return int(raw)\n",
            "tests/test_net.py": (
                "import pytest\nfrom net import parse_port\n\n"
                "def test_ok():\n    assert parse_port('8080') == 8080\n\n"
                "def test_bad():\n    for raw in (0, 70000, 'abc', None):\n"
                "        with pytest.raises(ValueError):\n            parse_port(raw)\n"),
        },
        test_file="tests/test_net.py",
        fix={"net.py": (
            "def parse_port(raw):\n    try:\n        port = int(raw)\n"
            "    except (TypeError, ValueError):\n        raise ValueError('invalid port')\n"
            "    if not 1 <= port <= 65535:\n        raise ValueError('out of range')\n"
            "    return port\n")},
    ),
    Task(
        id="fix-import",
        prompt=("report.py 导入的函数名不存在。修正导入并让 build 正常工作,"
                "运行 pytest tests/test_report.py -q 验证。"),
        files={
            "names.py": "def format_full_name(first, last):\n    return f'{first} {last}'.strip()\n",
            "report.py": ("from names import format_name\n\n"
                          "def build(first, last):\n    return 'Hello, ' + format_name(first, last)\n"),
            "tests/test_report.py": (
                "from report import build\n\n"
                "def test_build():\n    assert build('Ada', 'Lovelace') == 'Hello, Ada Lovelace'\n"),
        },
        test_file="tests/test_report.py",
        fix={"report.py": ("from names import format_full_name\n\n"
                           "def build(first, last):\n"
                           "    return 'Hello, ' + format_full_name(first, last)\n")},
    ),
    Task(
        id="fix-pagination",
        prompt=("paging.py 的分页按 1 开始:第 1 页返回前 size 条;page<1 或 size<1 "
                "抛 ValueError;越界页返回空列表。运行 pytest tests/test_paging.py -q 验证。"),
        files={
            "paging.py": ("def page_slice(items, page, size):\n"
                          "    start = page * size\n    return items[start:start + size]\n"),
            "tests/test_paging.py": (
                "import pytest\nfrom paging import page_slice\n\n"
                "def test_first_page():\n    assert page_slice(list(range(10)), 1, 3) == [0, 1, 2]\n\n"
                "def test_second_page():\n    assert page_slice(list(range(10)), 2, 3) == [3, 4, 5]\n\n"
                "def test_bad_args():\n    with pytest.raises(ValueError):\n"
                "        page_slice([], 0, 3)\n    with pytest.raises(ValueError):\n"
                "        page_slice([], 1, 0)\n\n"
                "def test_beyond_end():\n    assert page_slice([1, 2], 9, 3) == []\n"),
        },
        test_file="tests/test_paging.py",
        fix={"paging.py": ("def page_slice(items, page, size):\n"
                           "    if page < 1 or size < 1:\n        raise ValueError('invalid page')\n"
                           "    start = (page - 1) * size\n    return items[start:start + size]\n")},
    ),
    Task(
        id="add-dry-run",
        prompt=("给 tool.py 增加 --dry-run:带上时打印 'dry run: hello <name>' 或 "
                "'dry run: (no name)' 后退出;不带时仍需 --name。"
                "运行 pytest tests/test_tool.py -q 验证。"),
        files={
            "tool.py": ("import argparse\n\n\ndef main():\n    p = argparse.ArgumentParser()\n"
                        "    p.add_argument('--name', required=True)\n    args = p.parse_args()\n"
                        "    print('hello ' + args.name)\n\n\nif __name__ == '__main__':\n    main()\n"),
            "tests/test_tool.py": (
                "import subprocess, sys\n\n"
                "def run(*args):\n    return subprocess.run([sys.executable, 'tool.py', *args],"
                " text=True, capture_output=True, timeout=20)\n\n"
                "def test_normal():\n    r = run('--name', 'Ada')\n"
                "    assert r.returncode == 0 and r.stdout.strip() == 'hello Ada'\n\n"
                "def test_dry_with_name():\n    r = run('--dry-run', '--name', 'Ada')\n"
                "    assert r.returncode == 0 and r.stdout.strip() == 'dry run: hello Ada'\n\n"
                "def test_dry_without_name():\n    r = run('--dry-run')\n"
                "    assert r.returncode == 0 and r.stdout.strip() == 'dry run: (no name)'\n"),
        },
        test_file="tests/test_tool.py",
        fix={"tool.py": (
            "import argparse\n\n\ndef main():\n    p = argparse.ArgumentParser()\n"
            "    p.add_argument('--name')\n    p.add_argument('--dry-run', action='store_true')\n"
            "    args = p.parse_args()\n"
            "    if args.dry_run:\n"
            "        print('dry run: hello ' + args.name if args.name else 'dry run: (no name)')\n"
            "        return\n"
            "    if not args.name:\n        p.error('--name is required')\n"
            "    print('hello ' + args.name)\n\n\nif __name__ == '__main__':\n    main()\n")},
    ),
)


class _SilentInner:
    """给 HeadlessUI 当内层:所有没接的回调都不出声。"""

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeLLM:
    def __init__(self, task: Task):
        self.turns = []
        for i, (path, content) in enumerate(task.fix.items(), start=1):
            self.turns.append(AssistantTurn(tool_calls=[ToolCall(
                id=f"fix{i}", name="write_file",
                arguments=json.dumps({"path": path, "content": content}))]))
        self.turns.append(AssistantTurn(text="任务完成,请运行测试验收。"))

    def stream_chat(self, messages, tools, on_text=None, on_reasoning=None,
                    on_retry=None, **kwargs):
        if not self.turns:
            return AssistantTurn(text="(评测脚本已无预设步骤)")
        return self.turns.pop(0)


def _write_files(workspace: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


def _run_acceptance(workspace: Path, test_file: str) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1", PYTHONUTF8="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", test_file],
            cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TASK_TIMEOUT, env=env)
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                "tail": output[-1200:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": -1,
                "tail": f"验收超时(>{TASK_TIMEOUT}s)"}


def run_task(task: Task, config: Config, *, fake: bool, keep: bool) -> dict:
    base = Path(tempfile.mkdtemp(prefix=f"bench-{task.id}-"))
    workspace = base / "workspace"
    workspace.mkdir()
    _write_files(workspace, task.files)
    ui = headless.HeadlessUI(_SilentInner())
    started = time.perf_counter()
    agent, session = create_agent(
        config, workspace, ui, llm=_FakeLLM(task) if fake else None,
        sessions_dir=base / "sessions")
    error = ""
    try:
        agent.run_turn(task.prompt)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            agent.close()
        except Exception:
            pass
    elapsed = time.perf_counter() - started
    acceptance = _run_acceptance(workspace, task.test_file)
    result = {
        "id": task.id, "ok": bool(acceptance["ok"]) and not error,
        "elapsed_s": round(elapsed, 2),
        "tool_calls": len(ui.tool_calls),
        "messages": len(getattr(session, "messages", []) or []),
        "tokens": int(getattr(agent, "turn_tokens", 0) or 0),
        "verification": getattr(agent, "verification_report", None),
        "test_exit": acceptance["exit_code"], "test_tail": acceptance["tail"],
        "error": error,
    }
    if keep:
        result["workspace"] = str(workspace)
        result["report"] = getattr(session, "log_path", None) and str(session.log_path)
    else:
        shutil.rmtree(base, ignore_errors=True)
    return result


def _benchmark_config(fake: bool) -> Config:
    sandbox = {"enabled": True, "allow_outside_workspace": False, "allow_network": False}
    if fake:
        return Config(providers=[], permission_mode="accept_all",
                      sandbox=sandbox, mcp_servers=[])
    config = load_config()
    if config.provider is None:
        raise SystemExit("没有可用的模型供应商,先配置模型再跑真实评测")
    return replace(config, permission_mode="accept_all", sandbox=sandbox,
                   mcp_servers=[], max_steps=20)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实编程任务评测")
    parser.add_argument("--list", action="store_true", help="列出任务")
    parser.add_argument("--task", action="append", default=[], help="只跑指定任务(可重复)")
    parser.add_argument("--fake", action="store_true", help="假模型自检评测流程")
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--json-out", default="", help="把报告写到文件")
    args = parser.parse_args(argv)
    selected = [t for t in TASKS if not args.task or t.id in args.task]
    if args.list:
        for task in TASKS:
            print(f"{task.id}\t{task.prompt.splitlines()[0]}")
        return 0
    if not selected:
        print("没有匹配的任务", file=sys.stderr)
        return 2
    if set(args.task) - {t.id for t in TASKS}:
        print("包含未知任务 id", file=sys.stderr)
        return 2
    config = _benchmark_config(args.fake)
    print(f"模式:{'假模型自检' if args.fake else '真实模型'} · 任务 {len(selected)} 个 · "
          "每个任务在独立临时工作区以自动放行运行", file=sys.stderr)
    report = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "mode": "fake" if args.fake else "model",
              "model": getattr(config, "model", "") or "",
              "tasks": []}
    for task in selected:
        print(f"… {task.id}", file=sys.stderr, flush=True)
        item = run_task(task, config, fake=args.fake, keep=args.keep)
        print(f"  {'通过' if item['ok'] else '未通过'} · {item['elapsed_s']}s · "
              f"{item['tool_calls']} 次工具调用", file=sys.stderr)
        report["tasks"].append(item)
    report["passed"] = sum(1 for t in report["tasks"] if t["ok"])
    report["total"] = len(report["tasks"])
    payload = json.dumps(report, ensure_ascii=False, indent=1)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())