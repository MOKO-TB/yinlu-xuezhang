"""对话型 slash 命令(v0.9.74):命令表是唯一真相,桥只做映射。

钉住的口径:
- **表自身形状**:cmd 唯一、非空、长度守住菜单 token 上限(24,超了菜单就
  关掉)、label/desc/prompt 非空、has_arg 与 prompt 里的 $text 严格一致;
- **expand 认裸名也认带 / 的写法**,不认识的给 None(前端当普通文本);
- **桥方法只映射不加工**:has_arg 由 $text 现算,改文案不用动前端。
"""

import types

from minicode import slash_commands as sc
from minicode.gui_bridge import Bridge


def test_command_table_shape():
    cmds = [c["cmd"] for c in sc.PROMPT_COMMANDS]
    assert cmds and len(cmds) == len(set(cmds)), "命令名必须唯一"
    for c in sc.PROMPT_COMMANDS:
        assert c["cmd"] and not c["cmd"].startswith("/"), \
            "cmd 存裸名(带 / 是调用侧的写法)"
        assert len(c["cmd"]) <= 24, \
            f"「{c['cmd']}」超过菜单 token 上限 24 —— 打不出来"
        assert c["label"].strip() and c["desc"].strip() and c["prompt"].strip()
        assert sc.has_arg(c["cmd"]) == ("$text" in c["prompt"])


def test_expand_accepts_bare_and_slash_and_unknown():
    assert sc.expand("总结") == sc.PROMPT_COMMANDS[0]["prompt"]
    assert sc.expand("/总结") == sc.PROMPT_COMMANDS[0]["prompt"]
    assert sc.expand("没有这个") is None
    assert sc.expand("") is None
    assert sc.expand(None) is None


def test_no_arg_prompts_are_send_ready():
    """无参数命令返回的就是能直接发送的完整提示词(不许残留 $text)。"""
    for c in sc.PROMPT_COMMANDS:
        if "$text" not in c["prompt"]:
            assert "$text" not in sc.expand(c["cmd"])


def test_bridge_maps_table_without_touching_it():
    """桥方法不读 self,也不复制一份文案 —— 直接映射 PROMPT_COMMANDS。"""
    out = Bridge.get_slash_commands(types.SimpleNamespace())
    assert out["ok"] is True
    got = {c["cmd"]: c for c in out["commands"]}
    assert set(got) == {c["cmd"] for c in sc.PROMPT_COMMANDS}
    for c in sc.PROMPT_COMMANDS:
        row = got[c["cmd"]]
        assert row["label"] == c["label"] and row["desc"] == c["desc"]
        assert row["prompt"] == c["prompt"]           # 模板随表走,前端不用再展开
        assert row["has_arg"] is ("$text" in c["prompt"])
