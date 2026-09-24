"""静态检查这一关也要能在 pytest 里跑(#85)。

`ruff` 的价值不在"跑过一次很干净",而在**下次有人写坏时会自动红** —— 所以它必须
挂在测试里,不能只写在 CONTRIBUTING 的"记得跑"清单上(那份清单里的每一项,这个
仓库都已经吃过"忘了跑"的亏:文档基线漂 15 条、五支 e2e 从来没跑起来过)。

ruff 是 **dev 依赖**(`requirements-dev.txt`),不进运行时、不进安装包。没装时
这里**直接红并给安装命令**,不静默跳过 —— "没跑"和"跑挂"在输出里必须长得不一样。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _ruff(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-m", "ruff", *args],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if "No module named ruff" in (proc.stderr or "") + (proc.stdout or ""):
        raise AssertionError(
            "ruff 没装:pip install -r requirements-dev.txt"
            "(它是 dev 依赖,不进 requirements.txt、不进安装包)")
    return proc


def test_ruff_check_is_clean():
    proc = _ruff("check", ".", "--output-format", "concise")
    assert proc.returncode == 0, "ruff 报出问题:\n" + (proc.stdout or "")[-3000:]


def test_no_invalid_noqa_directives():
    """写坏的 `# noqa` 等于没写(ruff 只在 stderr 里嘟囔一句),这一轮就修掉两处。"""
    proc = _ruff("check", ".")
    assert "Invalid `# noqa`" not in (proc.stderr or ""), proc.stderr


def test_lint_rules_are_the_ones_we_agreed_on():
    """防"检查器被悄悄调松":规则集只能加不能偷偷减。

    要放宽请改这里 + 在 pyproject 的注释里写清为什么(那份注释就是这次的记录)。
    """
    block = PYPROJECT.split("[tool.ruff.lint]", 1)[1].split("[tool.ruff.lint.per-file-ignores]", 1)[0]
    selected = set(re.findall(r'"([A-Z]+[0-9]*)"', block))
    for rule in ("F", "E722", "B006", "B023", "B002"):
        assert rule in selected, f"{rule} 从 select 里掉了(裸 except / 可变默认参数 / 循环变量闭包是要真报的)"
    assert "E501" not in selected, "别开行宽检查:这个仓库的中文长描述会被折得没法读"


def test_per_file_ignores_stay_narrow():
    """放行清单只准是那两个有理由的文件;它一旦变长,说明大家在拿 ignore 当垃圾桶。"""
    block = PYPROJECT.split("[tool.ruff.lint.per-file-ignores]", 1)[1]
    files = re.findall(r'^"([^"]+)"\s*=', block, re.M)
    assert sorted(files) == ["minicode/__init__.py", "minicode/tools/registry.py", "tests/*"], \
        f"per-file-ignores 变了:{files}"
