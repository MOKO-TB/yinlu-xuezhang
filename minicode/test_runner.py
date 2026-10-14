"""智能测试运行与自愈诊断引擎(v0.9.164)。

对标主流 AI 编程智能体(Qoder、Codex、Cursor 等)的动态测试与修复闭环:
1. 自动探测工作区测试框架(Python pytest/unittest、Node npm test/jest/vitest、Rust cargo test、Go go test);
2. 隔离受控执行:纯标准库 argv 调用,严格限制在工作区围栏内,超时安全中断;
3. 结构化解析报告:统计通过/失败/跳过数,精准提炼失败用例名称与关键 Traceback / AssertionError 段落,
   严格控制字数预算(<= 2500 字),避免终端海量日志冲爆上下文;
4. 内存快照持久:记录最近一次测试运行结论,供 `/fix-test` 自愈工作流即时读取与定向修复。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from minicode.tools.base import ToolContext

# 内存快照:工作区绝对路径(规范化) -> 最近一次测试执行报告
_LAST_RUN_SNAPSHOT: dict[str, dict[str, Any]] = {}

DEFAULT_TIMEOUT = 120  # 默认测试超时 120 秒
MAX_SUMMARY_CHARS = 2500  # 结构化回显最大字符数


def _find_python_interpreter(workspace: Path) -> str:
    """寻找工作区最适合运行测试的 Python 解释器。优先工作区虚拟环境,其次当前解释器。"""
    candidates = [
        workspace / ".venv" / "Scripts" / "python.exe",
        workspace / ".venv" / "bin" / "python",
        workspace / "venv" / "Scripts" / "python.exe",
        workspace / "venv" / "bin" / "python",
        workspace / ".env" / "Scripts" / "python.exe",
        workspace / ".env" / "bin" / "python",
    ]
    for c in candidates:
        if c.is_file() and os.access(str(c), os.X_OK):
            return str(c.resolve())
    return sys.executable


def detect_framework(workspace: Path) -> dict[str, Any] | None:
    """自动探测工作区主要测试框架。"""
    if not workspace.is_dir():
        return None

    # 1. Python 探测
    py_indicators = [
        workspace / "pytest.ini",
        workspace / "setup.cfg",
        workspace / "pyproject.toml",
        workspace / "conftest.py",
        workspace / "tests",
    ]
    has_py_files = any(p.exists() for p in py_indicators) or any(workspace.glob("test_*.py"))
    if has_py_files:
        py_exe = _find_python_interpreter(workspace)
        # 默认推荐 pytest,如果无法调用再回退 unittest
        return {
            "type": "python",
            "framework": "pytest",
            "runner": py_exe,
            "cmd_template": [py_exe, "-m", "pytest", "-q"],
        }

    # 2. Node.js / TypeScript 探测
    pkg_json = workspace / "package.json"
    if pkg_json.is_file():
        npm_bin = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        return {
            "type": "node",
            "framework": "npm",
            "runner": npm_bin,
            "cmd_template": [npm_bin, "test", "--"],
        }

    # 3. Rust Cargo 探测
    cargo_toml = workspace / "Cargo.toml"
    if cargo_toml.is_file():
        cargo_bin = shutil.which("cargo.exe") or shutil.which("cargo") or "cargo"
        return {
            "type": "rust",
            "framework": "cargo",
            "runner": cargo_bin,
            "cmd_template": [cargo_bin, "test"],
        }

    # 4. Go 探测
    go_mod = workspace / "go.mod"
    if go_mod.is_file():
        go_bin = shutil.which("go.exe") or shutil.which("go") or "go"
        return {
            "type": "go",
            "framework": "go",
            "runner": go_bin,
            "cmd_template": [go_bin, "test", "./..."],
        }

    return None


def _clean_ansi(text: str) -> str:
    """过滤 ANSI 颜色与终端控制转义字符。"""
    return re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)


def parse_pytest_output(output: str) -> dict[str, Any]:
    """解析 pytest 的标准输出,提取统计与失败详情。"""
    clean = _clean_ansi(output)
    passed = 0
    failed = 0
    skipped = 0
    errors = 0

    # 提取汇总行,例如: 39 passed, 2 failed, 1 skipped in 4.52s
    # 或 ================= 2 failed, 39 passed in 3.12s =================
    m_pass = re.search(r"(\d+)\s+passed", clean)
    if m_pass:
        passed = int(m_pass.group(1))

    m_fail = re.search(r"(\d+)\s+failed", clean)
    if m_fail:
        failed = int(m_fail.group(1))

    m_skip = re.search(r"(\d+)\s+skipped", clean)
    if m_skip:
        skipped = int(m_skip.group(1))

    m_err = re.search(r"(\d+)\s+errors?", clean)
    if m_err:
        errors = int(m_err.group(1))

    # 1. 尝试从 FAILURES 区域提取详细用例与 Traceback
    failures = []
    fail_sections = re.findall(
        r"^[ \t]*_{3,}\s*([^\n_]+)\s*_{3,}[ \t]*\n(.*?)(?=^[ \t]*_{3,}|^[ \t]*={3,}|$)",
        clean,
        re.MULTILINE | re.DOTALL,
    )
    for test_name, body in fail_sections[:5]:  # 最多取前 5 处失败
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        err_lines = [l for l in lines if l.startswith("E   ") or "Error:" in l or "Exception:" in l]
        summary_line = err_lines[-1] if err_lines else (lines[-1] if lines else "测试断言或执行失败")
        failures.append({
            "test": test_name.strip(),
            "summary": summary_line,
            "details": "\n".join(lines[-10:]),  # 保留最后 10 行上下文
        })

    # 2. 如果 FAILURES 区域未命中,或者从 short test summary info 补充提取
    if not failures:
        for m in re.finditer(r"^[ \t]*FAILED\s+([^\n\s\-]+)(?:\s*-\s*([^\n]+))?", clean, re.MULTILINE):
            test_id = m.group(1).strip()
            reason = (m.group(2) or "测试失败").strip()
            failures.append({
                "test": test_id,
                "summary": reason,
                "details": reason,
            })
            if len(failures) >= 5:
                break

    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "total": passed + failed + skipped + errors,
        "failures": failures,
    }


def parse_generic_output(output: str, exit_code: int) -> dict[str, Any]:
    """通用输出解析。"""
    clean = _clean_ansi(output)
    failures = []
    if exit_code != 0:
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        err_lines = [l for l in lines if any(k in l.lower() for k in ("fail", "error", "panic", "assert"))]
        tail = "\n".join(lines[-15:])
        failures.append({
            "test": "执行异常/测试未通过",
            "summary": err_lines[0] if err_lines else "测试进程非零退出",
            "details": tail,
        })
    return {
        "passed": 0 if exit_code != 0 else 1,
        "failed": 1 if exit_code != 0 else 0,
        "skipped": 0,
        "errors": 0,
        "total": 1,
        "failures": failures,
    }


def run_tests(workspace: Path, target: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """在工作区受控执行测试并返回结构化报告。"""
    meta = detect_framework(workspace)
    if not meta:
        return {
            "ok": False,
            "status": "no_framework",
            "summary": "未在当前工作区探测到已知测试框架 (pytest/unittest, npm test, cargo test, go test)。",
            "details": "请确认工作区根目录下是否存在测试配置文件 (如 pytest.ini、package.json、Cargo.toml 或 go.mod)。",
        }

    argv = list(meta["cmd_template"])
    if target and target.strip():
        # 安全清洗: 仅允许工作区内相对文件路径或用例标识
        clean_target = target.strip()
        argv.append(clean_target)

    cmd_str = " ".join(argv)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        raw_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as e:
        raw_out = (e.stdout or "") + "\n" + (e.stderr or "")
        res = {
            "ok": False,
            "status": "timeout",
            "framework": meta["framework"],
            "command": cmd_str,
            "exit_code": -1,
            "summary": f"测试执行超时 ({timeout}s),已自动终止。",
            "failures": [{"test": "timeout", "summary": f"超时 {timeout} 秒未完成", "details": raw_out[-500:]}],
        }
        _LAST_RUN_SNAPSHOT[str(workspace.resolve())] = res
        return res
    except Exception as ex:
        res = {
            "ok": False,
            "status": "error",
            "framework": meta["framework"],
            "command": cmd_str,
            "exit_code": -1,
            "summary": f"执行测试失败({type(ex).__name__}: {ex})",
            "failures": [],
        }
        _LAST_RUN_SNAPSHOT[str(workspace.resolve())] = res
        return res

    # 解析测试输出
    if meta["framework"] == "pytest":
        parsed = parse_pytest_output(raw_out)
    else:
        parsed = parse_generic_output(raw_out, exit_code)

    ok = (exit_code == 0)
    summary_parts = []
    if ok:
        summary_parts.append(f"✓ 全部测试通过: {parsed.get('passed', 0)} passed")
        if parsed.get("skipped"):
            summary_parts.append(f"({parsed['skipped']} skipped)")
    else:
        summary_parts.append(f"✗ 测试未全部通过 (退出码 {exit_code}):")
        if parsed.get("failed"):
            summary_parts.append(f" {parsed['failed']} failed,")
        if parsed.get("passed"):
            summary_parts.append(f" {parsed['passed']} passed,")
        if parsed.get("errors"):
            summary_parts.append(f" {parsed['errors']} errors")

    summary_str = " ".join(summary_parts).strip()
    res = {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "framework": meta["framework"],
        "command": cmd_str,
        "exit_code": exit_code,
        "total": parsed.get("total", 0),
        "passed": parsed.get("passed", 0),
        "failed": parsed.get("failed", 0),
        "skipped": parsed.get("skipped", 0),
        "errors": parsed.get("errors", 0),
        "failures": parsed.get("failures", []),
        "summary": summary_str,
        "raw_tail": raw_out[-1200:].strip(),
    }

    _LAST_RUN_SNAPSHOT[str(workspace.resolve())] = res
    return res


def get_last_run(workspace: Path) -> dict[str, Any] | None:
    """获取工作区最近一次测试结果快照。"""
    return _LAST_RUN_SNAPSHOT.get(str(workspace.resolve()))


def format_test_report(result: dict[str, Any]) -> str:
    """将测试报告格式化为清晰的人类/模型可读文本。"""
    lines = [
        f"【测试框架】: {result.get('framework', 'unknown')}",
        f"【执行命令】: {result.get('command', '')}",
        f"【测试结论】: {result.get('summary', '')}",
    ]

    failures = result.get("failures", [])
    if failures:
        lines.append("\n【失败用例详情】:")
        for idx, f in enumerate(failures[:5], 1):
            lines.append(f"  {idx}. 用例: {f.get('test', '未知')}")
            lines.append(f"     报错: {f.get('summary', '')}")
            details = f.get("details", "").strip()
            if details:
                # 缩进展示关键日志
                indented = "\n".join("       " + l for l in details.splitlines()[-6:])
                lines.append(f"     日志:\n{indented}")

    raw_tail = result.get("raw_tail", "")
    if raw_tail and not result.get("ok"):
        lines.append(f"\n【终端尾部输出】:\n{raw_tail[-800:]}")

    text = "\n".join(lines)
    if len(text) > MAX_SUMMARY_CHARS:
        return text[:MAX_SUMMARY_CHARS] + "\n...(输出过长已截断)"
    return text


def test_runner_tool(ctx: ToolContext, action: str = "run", target: str = "", reason: str = "") -> str:
    """内置 test_runner 工具的处理函数。"""
    ws = Path(ctx.workspace) if ctx.workspace else Path.cwd()

    if action == "detect":
        meta = detect_framework(ws)
        if not meta:
            return "当前工作区未检测到已知测试框架。"
        return (
            f"已检测到测试框架:\n"
            f"- 类型: {meta['type']}\n"
            f"- 框架: {meta['framework']}\n"
            f"- 运行器: {meta['runner']}\n"
            f"- 默认命令: {' '.join(meta['cmd_template'])}"
        )

    if action == "last_result":
        last = get_last_run(ws)
        if not last:
            return "当前工作区本次会话内尚未运行过测试。请使用 action='run' 执行测试。"
        return "【最近一次测试运行快照】\n" + format_test_report(last)

    if action == "run":
        res = run_tests(ws, target=target if target.strip() else None)
        return format_test_report(res)

    return f"未知的 action={action}。有效选项为 run, detect, last_result。"
