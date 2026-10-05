"""冻结针清单自身的体检(scripts/check_frozen_version.py 的 TARGETS)。

`check_frozen_version.py` 要在**打完包之后**才跑得动,而它自己会坏在两种
不需要打包就能发现的方式上(文件里的注释已经警告过后一种,但没人在提交前看):
1. dict 里同一个模块写两遍 → 后写的赢,前一批 needle **静默不再检查**;
2. needle 写的名字在源码里根本不存在(函数改名/被删) → 打包后必然红,
   但那时已经隔了一次十几分钟的构建。

这两条都在提交前当场红,比"下次打包时再说"便宜得多。
"""
import ast
import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_frozen_version.py"

sys.path.insert(0, str(ROOT / "scripts"))
from check_frozen_version import TARGETS, _code_names      # noqa: E402


def test_targets_has_no_duplicate_module_keys():
    """重复键 = 那一批 needle 悄悄失效,所以直接按源码字面量数一遍。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    keys: list[str] = []
    for node in ast.walk(tree):
        # 脚本里写的是 `TARGETS: dict[str, tuple[str, ...]] = {…}`,那是 AnnAssign
        # 而不是 Assign —— 只认 Assign 会一条都找不到,然后这条检查永远"过"。
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if (isinstance(value, ast.Dict) and any(
                isinstance(t, ast.Name) and t.id == "TARGETS" for t in targets)):
            keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
    assert keys, "没在脚本里找到 TARGETS 字典字面量(结构变了,这条检查该跟着改)"
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"TARGETS 里这些模块被写了两遍,前一批 needle 已失效:{dupes}"


def test_every_needle_exists_in_its_module_source():
    """每根针都要在对应模块的字节码里真找得到 —— 不打包也能先红。"""
    stale: list[str] = []
    for mod, needles in TARGETS.items():
        try:
            source = inspect.getsource(importlib.import_module(mod))
        except (OSError, TypeError) as e:
            stale.append(f"{mod}: 读不到源码({e})")
            continue
        haystack = _code_names(compile(source, mod, "exec"))
        stale += [f"{mod}:{n}" for n in needles if n not in haystack]
    assert not stale, f"冻结针已过期(打包后必然红,现在先修):{stale}"


def test_every_frontend_needle_exists_in_index_html():
    """前端针同样要在提交前红:check_frozen 只在打完包之后跑得动,而前端每次
    改版都可能让旧针过期(v0.9.117 就撞过一次 ——「缓存命中(端点不回报)」
    随用量弹窗改版从界面上消失,没人红过,直到打包那天才会炸)。"""
    from check_frozen_version import FRONTEND_TARGETS

    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    stale = [n for n in FRONTEND_TARGETS if n not in html]
    assert not stale, f"前端冻结针已过期(打包后必然红,现在先修):{stale}"
