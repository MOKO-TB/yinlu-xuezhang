"""《使用说明》条目编号连续性(v0.9.63):1..N 必须连续且按序排列。

为什么钉它:v0.9.60/61 的插入把文件序弄成 …42,45,46,44,43,47 —— 每条自标
编号所以没人立刻发现,但印出来的说明书是跳着读的。v0.9.63 用尾巴锚重排清偿,
这条测试保证以后的插入要么按序、要么当场被红指名。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")

# manual 字面量区域:第 1 条起始 → write_text 的 encoding 终止符
START = SOURCE.find('\n        "1. ')
TERM = SOURCE.find('",\n        encoding="utf-8"', START)


def test_manual_region_exists():
    assert START > 0 and TERM > START, "manual 字面量区域没找到 —— 结构变了,先改这里"


def test_manual_numbers_are_sequential():
    region = SOURCE[START:TERM]
    nums = [int(m.group(1)) for m in re.finditer(r'\n        "(\d+)\. ', region)]
    assert nums, "manual 里一条条目都没找到"
    assert nums == list(range(1, len(nums) + 1)), (
        f"条目编号断档或乱序:{nums} —— 新条目请插到序号正确的位置")


def test_manual_last_number_matches_documented_count():
    """《使用说明》自述的条数(HANDOFF 等处引用)与最后一条编号一致。"""
    region = SOURCE[START:TERM]
    nums = [int(m.group(1)) for m in re.finditer(r'\n        "(\d+)\. ', region)]
    assert nums[-1] == len(nums)


def test_write_user_files_actually_runs(tmp_path):
    """真去调 `write_user_files`,而不是只对源码做正则。

    为什么必须有这条:v0.9.116 给手册加 93~95 三条时,我把上一条末尾那枚逗号漏摘了
    —— 相邻字符串字面量之间出现逗号,整块就变成 `write_text` 的**第二个位置参数**,
    出包当场 `got multiple values for argument 'encoding'`。上面那些按正则读源码的测试
    全绿,因为它语法合法、编号也连续:只有真调用才炸。这正是这个项目反复说的那类坏
    ——「测试没跑过 = 没有过」,而这里连"跑过测试"都不算,得跑**被验的那条路**。
    """
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "build_exe_under_test", root / "scripts" / "build_exe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    out_dir = tmp_path / "引路学长"
    out_dir.mkdir()
    mod.write_user_files(out_dir)          # 炸在这里就是回归
    text = (out_dir / "使用说明.txt").read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"(?m)^(\d{1,3})\. ", text)]
    assert nums == list(range(1, len(nums) + 1)), "手册编号断了"
    assert len(nums) >= 92, f"条目数掉到 {len(nums)},像是哪段没拼进去"
