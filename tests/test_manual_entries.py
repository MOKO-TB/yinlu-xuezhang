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
