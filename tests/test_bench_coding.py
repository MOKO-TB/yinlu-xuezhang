"""评测骨架自检:假模型跑通「agent 真改文件 + 独立 pytest 验收」整条链。"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_coding_tasks.py"


def test_fake_benchmark_runs_all_tasks_end_to_end(tmp_path):
    out = tmp_path / "report.json"
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake", "--json-out", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=240, env=env)
    assert proc.returncode == 0, proc.stderr[-1500:]
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mode"] == "fake"
    assert report["passed"] == report["total"] == 5
    assert all(task["test_exit"] == 0 and not task["error"] for task in report["tasks"])
    assert all(task["tool_calls"] >= 1 for task in report["tasks"])


def test_unknown_task_is_rejected():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--fake", "--task", "not-a-task"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60)
    assert proc.returncode == 2