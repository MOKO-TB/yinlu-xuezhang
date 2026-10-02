"""同一个测试文件里不许有两个同名 `test_*`(后一份会**静默**盖掉前一份)。

为什么要有这条闸:Python 允许重复定义函数,pytest 只收最后那一份 —— 前面那条测试
凭空消失,而 collect 的基线数照常动,于是"测试变多"和"测试变少"长得一模一样。
在这仓库里这不是假想:同一个工作树被另一会话同时读写,两边都往测试文件尾部追加,
函数名撞车的概率远高于单人作业。2026-10-02 补 `/undo` 那一族时,我自己就把
"命令表"那一段写了两遍 —— 没有这条闸的话,那六支测试会安静地少掉三笔。

只查**模块顶层**的 `def test_*`:类方法(如 `class TestX`)与嵌套函数不在这条的射程里,
它们的遮蔽方式不同,真要查得另立一条,别把这条写宽了。
"""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _top_level_test_names(path: Path) -> list[str]:
    # utf-8-sig:有个测试文件带 BOM(test_school_topics.py),按 utf-8 读会把
    # U+FEFF 留在源码串开头,ast 当场回"invalid non-printable character"。
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def test_no_duplicate_test_names_in_one_file():
    bad = {}
    for path in sorted(TESTS.glob("test_*.py")):
        names = _top_level_test_names(path)
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            bad[path.name] = sorted(dup)
    assert not bad, (f"这些文件里有同名测试,前面那份根本没在跑:{bad}"
                     " —— 改名字或删掉一份,别留两份互相顶掉")
