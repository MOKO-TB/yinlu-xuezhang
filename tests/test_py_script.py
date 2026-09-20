"""py_script 工具与 CLI --py 入口:内置 Python 运行时执行脚本。"""
import subprocess
import sys

from minicode.tools.shell import py_script


def _write(tmp_path, name, code):
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return name


def test_py_script_runs_and_prints(ctx, tmp_path):
    _write(tmp_path, "hello.py", "print('你好,内置运行时')\nprint(1 + 1)\n")
    out = py_script(ctx, "hello.py")
    assert "退出码: 0" in out
    assert "你好,内置运行时" in out
    assert "2" in out


def test_py_script_can_use_bundled_doclibs(ctx, tmp_path):
    """转换类技能的底座:内置环境真能 import pptx/openpyxl/pypdf。"""
    _write(tmp_path, "libs.py",
           "import pptx, openpyxl, pypdf\n"
           "print('libs-ok', openpyxl.__version__)\n")
    out = py_script(ctx, "libs.py")
    assert "退出码: 0" in out and "libs-ok" in out


def test_py_script_reads_and_writes_workspace_files(ctx, tmp_path):
    _write(tmp_path, "io.py",
           "from pathlib import Path\n"
           "Path('产物.txt').write_text('生成内容', encoding='utf-8')\n"
           "print(Path('产物.txt').read_text(encoding='utf-8'))\n")
    out = py_script(ctx, "io.py")
    assert "生成内容" in out
    assert (tmp_path / "产物.txt").read_text(encoding="utf-8") == "生成内容"


def test_py_script_reports_traceback_and_exit_code(ctx, tmp_path):
    _write(tmp_path, "bad.py", "raise ValueError('故意失败')\n")
    out = py_script(ctx, "bad.py")
    assert "退出码: 1" in out and "ValueError" in out


def test_py_script_rejects_missing_and_bad_paths(ctx, tmp_path):
    assert py_script(ctx, "不存在.py").startswith("错误:")
    assert py_script(ctx, "").startswith("错误:")
    assert py_script(ctx, "../outside.py").startswith("错误:")


def test_cli_py_flag(tmp_path):
    """打包版的执行底座:main.py --py 独立于 agent 直接跑脚本。"""
    script = tmp_path / "cli.py"
    script.write_text("print('cli-py-ok')\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "main.py", "--py", str(script)],
        capture_output=True, timeout=60,
        cwd=".", creationflags=subprocess.CREATE_NO_WINDOW)
    out = proc.stdout.decode("utf-8", errors="replace")
    assert proc.returncode == 0 and "cli-py-ok" in out
