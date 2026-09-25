"""前端调的每一个 `pywebview.api.X` 必须在门面上真存在(v0.9.42 补的闸)。

为什么要这条:门面对象 `PublicApi` 是 js_api 的全部接口,而前端是 JS —— 名字拼错、
方法只写在 `Bridge` 忘了在门面转发、或者**实现错落到门面上**(本轮 `share_image` 就
先写进了 `PublicApi`,那里没有 `self.workspace`),三种情况都不会有任何启动错误或
导入错误:界面上表现为点一下没反应,或 `pywebview.api.X is undefined`。测试全绿、
包也照出,只有真去点那颗按钮才发现。

扫描前端所有 `api.<name>(` 调用,逐个要求它在门面上可调用。唯一例外是"能力探测式"
写法(`if (pywebview.api.X)`),它本来就要在缺失时安静降级 —— 那类另列。
"""
import inspect
import re
from pathlib import Path

import pytest

from minicode.gui_bridge import PublicApi

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

CALL_RE = re.compile(r"\bapi\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# 只当作"有没有这个能力"来问的写法(缺失时前端自己降级),不算调用了不存在的方法
OPTIONAL_RE = re.compile(r"if\s*\(\s*!?pywebview\.api\.([A-Za-z_][A-Za-z0-9_]*)\)")


def called_names() -> set[str]:
    return set(CALL_RE.findall(INDEX)) - set(OPTIONAL_RE.findall(INDEX)) - {"then", "catch"}


def facade_names() -> set[str]:
    return {n for n, v in inspect.getmembers(PublicApi, predicate=inspect.isfunction)}


def test_the_scan_finds_a_real_number_of_calls():
    """扫描器自己坏掉(正则不匹配)会让这条闸空转 —— 先确认它真看到几十个字。"""
    assert len(called_names()) > 40, f"只扫到 {len(called_names())} 个,正则该修了"


@pytest.mark.parametrize("name", sorted(called_names()))
def test_every_called_api_method_exists_on_the_facade(name):
    fn = getattr(PublicApi, name, None)
    assert callable(fn), (
        f"前端在调 pywebview.api.{name}(),但门面 PublicApi 上没有它 —— "
        f"真机表现是点了没反应(要么忘了转发,要么实现写错了层)")


def test_facade_methods_only_forward_to_the_bridge():
    """门面只该是转发层:实现留在 `Bridge`,否则桥的字段(`self.workspace` 等)不在场。

    这条就是钉本轮自己犯的那个错:`share_image` 的实现先写进了门面 —— 那里没有
    `self.workspace`,一调用就是 `AttributeError`,而导入、启动、单测全都不会红。
    允许别名(`get_recent_projects` → `_b.get_workspaces()`),不允许自己写实现。
    """
    local = []
    for name in sorted(facade_names()):
        src = inspect.getsource(getattr(PublicApi, name))
        if "self._b" not in src:
            local.append(name)
    assert not local, f"这些门面方法没在转发、是自己写的实现: {local}"
