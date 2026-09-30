# -*- coding: utf-8 -*-
"""`.github/workflows/ci.yml` 里那三处"改了就会让 CI 变成装饰品"的写法(v0.9.89)。

这份 yml 在本机跑不起来(GitHub 才会跑它),所以它能骗人的方式比测试文件多:
①把 `PYTHONIOENCODING` 设上 —— GBK 控制台那一类崩当场查不出来,CI 从此只在自己机器外绿;
②去掉 `fetch-depth: 0` —— `test_changelog` 拿 CHANGELOG 尾注里那个提交重算一遍,浅克隆里
   那个提交不存在,得到的是**假红**,于是有人会去关掉那条测试;
③偷偷少跑 CONTRIBUTING 那四条里的某一条 —— 那才是"每次推送必跑"的全部含义。
所以这里不验"CI 跑过了"(本机无从验),只验这三条写法没漂。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
YML = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
CONTRIB = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_ci_runs_the_same_commands_as_contributing():
    """CONTRIBUTING 那四条命令必须逐条出现在 workflow 的 run 里。"""
    runs = " ".join(re.findall(r"run: \|\n((?:          .*\n)+)", YML))
    runs += " ".join(re.findall(r"run: (.+)", YML))
    for cmd in ("python -m ruff check .", "python scripts/check_docs.py --collect",
                "python scripts/check_frontend_js.py", 'python -m pytest -m "not slow" -q'):
        assert cmd in runs, f"CI 没跑 CONTRIBUTING 里那条 `{cmd}`"


def test_ci_does_not_force_a_utf8_console():
    """不设 PYTHONIOENCODING:Windows runner 默认 GBK,那才是本地会崩、CI 也得跟着崩的那一类。

    只看非注释行 —— 这份 yml 的注释里正写着"为什么不能设它",那几个字当然会出现。
    """
    body = "\n".join(ln for ln in YML.splitlines() if not ln.lstrip().startswith("#"))
    assert "PYTHONIOENCODING" not in body, (
        "CI 的 env 里设了 PYTHONIOENCODING —— GBK 控制台那一类崩溃从此查不出来")


def test_changelog_gate_gets_the_history_it_needs():
    """浅克隆会让 test_changelog 红在"那个提交不存在",那是假红,不是文档漂。"""
    assert re.search(r"fetch-depth:\s*0", YML), "checkout 没有 fetch-depth: 0"


def test_ci_declares_the_platform_it_is_written_for():
    """这仓库的托盘/PowerShell/DWM 都按 Windows 写;换 ubuntu 会得到一批与用户无关的红。"""
    assert "runs-on: windows-latest" in YML
    assert "runs-on: ubuntu" not in YML


def test_ci_gives_the_builtin_provider_a_placeholder_key():
    """干净工作树里没有 `config.local.json`,内置档就没 Key。

    彩排实测:`test_model_manage::test_session_model_switch_to_global_clears_override` 与
    `test_workspace::test_set_provider_pushes_metrics_only_for_global_sessions` 在那种树上红
    —— 它们测的是"切换"这件事本身(一个请求都不发),却被 Key 在不在绑住。所以 CI 给一个
    明显是假的占位值,而不是把这两条用例关掉或标 skip(那等于把已验证的行为面从闸口放掉)。
    """
    assert YML.count("YINLUXS_BUILTIN_API_KEY") == 2, "快车道与慢车道都要给,少一条那边就红"
    assert "ci-placeholder-not-a-real-key" in YML


def test_ci_pins_the_python_version_and_a_timeout():
    """版本与超时都要钉住:没有 timeout 的 CI 会把配额烧在一个卡住的用例上,
    而 Windows 跑器按 2 倍分钟计费 —— 那是真花钱的。"""
    assert 'python-version: "3.12"' in YML
    assert "timeout-minutes" in YML
