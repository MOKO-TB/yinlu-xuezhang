# -*- coding: utf-8 -*-
"""估算费用(W2):价目缺行是「未知」不是 $0,改价只存与默认不同的行。

这档测试盯的是两件最容易写歪的事:
- **把未知当零**:查不到价目的模型算出 $0.00,总数就成了一笔安静撒谎的账
  —— 所以每一条未知路径都钉"usd 是 None、priced 是 False";
- **改价污染默认表**:覆盖必须写在命中行的副本上(或者干脆动了 RATES 本体),
  第二次估算就会用被污染的默认价 —— 而且**按别名改的价必须连主名一起变**,
  不然同一行按两个名字查出来是两个价。
"""
from __future__ import annotations

import json

import pytest

from minicode import pricing


# ------------------------------------------------------------------ 匹配

def test_rate_lookup_hits_exact_alias_and_prefix():
    assert pricing.rate_for("deepseek-chat")["input"] == 0.15           # 别名行
    assert pricing.rate_for("GLM-5.3")["output"] == \
        pricing.RATES["glm-5.3"]["output"]                              # 大小写放平
    assert pricing.rate_for("glm-5.3-20260901") is \
        pricing.RATES["glm-5.3"]                                        # 前缀落主行
    assert pricing.rate_for("no-such-model") is None                    # 未知就是未知
    assert pricing.rate_for("") is None
    assert pricing.rate_for(None) is None


# ------------------------------------------------------------------ 估算

def test_estimate_never_prices_the_unknown_as_zero():
    r = pricing.estimate("no-such-model", 1000, 2000)
    assert r == {"usd": None, "priced": False}      # 没有 $0,只有「未知」
    assert pricing.estimate("deepseek-chat", None, 10)["priced"] is False
    assert pricing.estimate("deepseek-chat", -1, 10)["priced"] is False


def test_estimate_math_on_a_known_row():
    # deepseek-chat:输入 0.15 / 输出 0.6(USD 每 1M)→ 1M+1M = $0.75
    assert pricing.estimate("deepseek-chat", 1_000_000, 1_000_000)["usd"] == 0.75


def test_cache_price_applies_only_with_real_cache_details():
    # 端点真的给了缓存明细才按缓存价折:0.8M 走缓存,剩 0.2M 按输入价
    r = pricing.estimate("deepseek-chat", 1_000_000, 1_000_000,
                         cached_tokens=800_000)
    assert abs(r["usd"] - (0.2 * 0.15 + 0.8 * 0.003 + 1.0 * 0.6)) < 1e-9
    # 没带明细(走库的常态):整段按输入价,宁可估多,不假装享受了缓存折扣
    r2 = pricing.estimate("deepseek-chat", 1_000_000, 1_000_000)
    assert abs(r2["usd"] - 0.75) < 1e-9
    # 缓存数超出 prompt = 脏数据,不按缓存折
    r3 = pricing.estimate("deepseek-chat", 100, 10, cached_tokens=999)
    assert abs(r3["usd"] - (100 * 0.15 + 10 * 0.6) / 1e6) < 1e-9


def test_summarize_prices_only_priced_rows_and_lists_the_rest():
    rows = [
        {"model": "deepseek-chat", "prompt": 1_000_000, "completion": 1_000_000},
        {"model": "mystery-x", "prompt": 10, "completion": 20},
        {"model": "mystery-y", "prompt": 0, "completion": 0},   # 零用量不进脚注
    ]
    out = pricing.summarize(rows)
    assert out["cost_usd"] == 0.75                  # 主数字只有有价可依的部分
    assert out["unpriced_models"] == ["mystery-x"]
    assert rows[1]["cost_usd"] is None and rows[1]["priced"] is False
    assert rows[0]["priced"] is True and "0.15" in rows[0]["rate_note"]


def test_summarize_allows_zero_only_when_tokens_are_zero():
    rows = [{"model": "mystery-x", "prompt": 0, "completion": 0}]
    out = pricing.summarize(rows)
    assert out["cost_usd"] is None and out["unpriced_models"] == []
    assert rows[0]["cost_usd"] is None              # 依然不当 $0


# ------------------------------------------------------------------ 改价

@pytest.fixture
def override_home(tmp_path, monkeypatch):
    target = tmp_path / "pricing-override.json"
    monkeypatch.setattr(pricing, "override_path", lambda: target)
    monkeypatch.setattr(pricing, "_OVERRIDE", None)
    return target


def test_save_override_keeps_only_rows_that_differ(override_home):
    pricing.save_override({"deepseek-chat": {"input": 0.15, "output": 0.9},
                           "brand-new-model": {"input": 1.0, "output": 2.0}})
    saved = json.loads(override_home.read_text(encoding="utf-8"))["models"]
    # 与默认同价的行不落盘(diff-only);默认表没有的行整行存
    assert saved == {"deepseek-chat": {"output": 0.9},
                     "brand-new-model": {"input": 1.0, "output": 2.0}}


def test_override_via_alias_changes_the_whole_row(override_home):
    pricing.save_override({"deepseek-chat": {"output": 0.9}})
    # 别名与主名查出来必须是**同一个价** —— 同一行两个价是最会骗人的账
    assert pricing.rate_for("deepseek-chat")["output"] == 0.9
    assert pricing.rate_for("deepseek-flash")["output"] == 0.9
    assert pricing.RATES["deepseek-flash"]["output"] == 0.6     # 默认表本体没动


def test_override_takes_effect_without_restart(override_home):
    assert pricing.estimate("deepseek-chat", 1_000_000, 0)["usd"] == 0.15
    pricing.save_override({"deepseek-chat": {"input": 1.0}})
    assert pricing.estimate("deepseek-chat", 1_000_000, 0)["usd"] == 1.0
    # 删掉文件 = 回默认(下一次统计就生效,不用重启)
    override_home.unlink()
    pricing._OVERRIDE = None
    assert pricing.estimate("deepseek-chat", 1_000_000, 0)["usd"] == 0.15


def test_garbage_override_file_falls_back_to_defaults(override_home):
    override_home.write_text("{not json", encoding="utf-8")
    assert pricing.load_override() == {}
    assert pricing.rate_for("deepseek-chat")["input"] == 0.15


def test_unwritable_override_dir_falls_back_to_defaults(tmp_path, monkeypatch):
    """装进只读目录:写不进去就如实回「没改成」,估算照默认价走,不抛。

    价目不是界面偏好 —— 估算每次从文件读,没有「内存里那份」可依;
    与 monitor-pref 的差别要写清楚,不能抄一条不成立的口径。
    """
    monkeypatch.setattr(pricing, "override_path",
                        lambda: tmp_path / "没这个目录" / "pricing-override.json")
    monkeypatch.setattr(pricing, "_OVERRIDE", None)
    out = pricing.save_override({"deepseek-chat": {"input": 1.0}})
    assert out == {}                                     # 如实回"没改成"
    assert pricing.estimate("deepseek-chat", 1_000_000, 0)["usd"] == 0.15
