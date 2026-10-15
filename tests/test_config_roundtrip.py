"""config.json 的"读得懂"与"写得回"必须是同一批键(v0.9.176 立的对账闸)。

缘起是量出来的一件事:`save_config` 写的是**一份手写的白名单**,而 `load_config`
认另一批键 —— 两边的差集就是"**用户在 config.json 里写了,程序某次保存又悄悄抹掉**"
的清单。本轮加"跳过此版本"要落盘,正好撞在这上面,顺手把差集量清:

    update_check / subagent_nesting / shadow_git 三枚开关都在白名单外。

`update_check` 那一枚的承诺是"**关掉就完全不出网**",而 `hooks_apply`、模型管理
里勾一下、`--adopt-project-agents` …… 任何一次 `save_config` 都会把它写回默认开。
这不是"配置没人读"(那是 `check_docs` 的"档位与数字口径同步"管的事),
是**配置被人改掉了** —— 现有闸结构上抓不到,因为它只查读不查写。

所以这里钉两件事:
1. **对账**:`load_config` 解析的标量键 ⊆ `save_config` 写回的键,例外必须写明理由;
2. **行为**:三枚真开关设成非默认值 → 存 → 读回来还是那个值(对账闸的假想敌是
   "有人加了字段忘了写回",行为测试的假想敌是"写回去了但值被洗掉")。
"""
import json
import re
from pathlib import Path

import pytest

from minicode.config import Config, Provider, load_config, save_config

SRC = (Path(__file__).resolve().parent.parent / "minicode" / "config.py")

# 有意不写回 config.json 的键 → 为什么可以这样(**每一条都必须带理由**,
# 空理由会让这份清单变成"检查器红了就往里加一行"的垃圾桶)。
# 今天它是**空的** —— 曾经挂过一条 `active_provider_id`,查下来是错的:解析入口读的
# 是 `active_provider`(那个 id 由它派生),所以根本没有"读了但不写"的例外可主张。
NOT_SAVED: dict = {}


def _builder_body() -> str:
    """取"把 raw JSON 拼成 Config"的那一个顶层函数体。

    ⚠ 这一刀切错过一次:先按 `load_config` 切,结果它自己不写 `raw.get(`(它把活
    交给下面那个构建函数),于是 `read` 是**空集**、对账断言**恒真地绿** ——
    一份永远通过的"读到的键都在写回的键里"比没有更糟。所以下面那条 `len(read) >= 15`
    是**下限闸**:切错了就先红在这里,而不是让差集悄悄变成空。
    """
    src = SRC.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"^(?:def|class) ", src, re.M)]
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(src)
        blocks.append(src[s:e])
    hits = [b for b in blocks if 'raw.get("update_check"' in b]
    assert len(hits) == 1, f"找'把 raw 拼成 Config'的那个函数:命中 {len(hits)} 个,这份切法要修"
    return hits[0]


def _read_keys() -> set:
    read = set(re.findall(r'raw\.get\("([a-z_0-9]+)"', _builder_body()))
    assert len(read) >= 15, f"只认出 {len(read)} 个键,不像是真的解析入口 —— 先修测试自己"
    return read


def _saved_keys(tmp_path) -> set:
    """真存一次再读回键名 —— 条件写的那几枚(mcp_servers/sandbox/vision/…)
    只有**给了值**才会出现,所以这里刻意把它们都填上,不然对账闸会漏比。"""
    cfg = Config(
        providers=[Provider(id="p1", name="P", base_url="https://x/v1",
                            api_key="k", model="m")],
        active_provider_id="p1", mcp_servers=[{"name": "s", "command": "x"}],
        sandbox={"read_only": True}, vision=True,
        capabilities={"embeddings": {"provider_id": "p1", "model": "e"}},
        hooks={"pre_tool": []},
    )
    path = tmp_path / "config.json"
    save_config(path, cfg)
    got = set(json.loads(path.read_text(encoding="utf-8")).keys())
    assert len(got) >= 15, f"只写出 {len(got)} 个键,这份样本没喂满,对账会漏比"
    return got


def test_every_scalar_key_the_loader_reads_is_written_back(tmp_path):
    """读的那批标量键必须都在写回的那批里,漏一个就当场红指名是谁。"""
    read = _read_keys()
    saved = _saved_keys(tmp_path)
    missing = sorted(k for k in read - saved if k not in NOT_SAVED)
    assert not missing, (
        f"这几枚键 config 认、save_config 不写:用户在 config.json 里写了会被抹掉 "
        f"→ {missing};要有意不写就进 NOT_SAVED 并写清理由")


def test_every_exception_reason_is_real():
    """例外清单自己不许漂:每条都要有非空理由,且**今天仍然成立**(键确实在读)。"""
    read = _read_keys()
    for key, why in NOT_SAVED.items():
        assert why.strip(), f"{key} 的例外没写理由 —— 那这条闸就白设了"
        assert key in read, f"{key} 已经不在解析入口里了,例外条目该删"


@pytest.mark.parametrize("field,value", [
    ("update_check", False),          # "关掉就完全不出网"这句承诺
    ("update_popup", False),
    ("update_skip_version", "0.9.999"),
    ("subagent_nesting", True),       # v0.9.155 那枚默认关的开关
    ("shadow_git", False),            # v0.9.157 那枚
])
def test_a_switch_set_by_hand_survives_one_save(tmp_path, field, value):
    """写进 config.json 的开关,经过一次"程序存配置"之后**还得是它**。

    这三枚在 v0.9.176 之前都是漏的 —— 现象不是报错,是"我明明关过"。
    """
    path = tmp_path / "config.json"
    cfg = Config(providers=[Provider(id="p1", name="P", base_url="https://x/v1",
                                     api_key="k", model="m")],
                 active_provider_id="p1", mcp_servers=[], sandbox={})
    setattr(cfg, field, value)
    save_config(path, cfg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert field in raw, f"{field} 没被写进 config.json"
    assert raw[field] == value
    back = load_config(path)
    assert getattr(back, field) == value, (
        f"{field} 存了又读不回来(或被洗成别的值)—— 那就是第二处真相")
