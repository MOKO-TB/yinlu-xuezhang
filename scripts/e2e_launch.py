# -*- coding: utf-8 -*-
"""起真浏览器这一件事的一份真相(所有 `scripts/e2e_ui_*.py` 共用)。

为什么单拆:这台机器上 `playwright` 装的是**完整版 chromium**,没有随附的
`chromium_headless_shell`,所以直接 `p.chromium.launch()` 会报
「Executable doesn't exist at .../chromium_headless_shell-1234/...」——
这不是页面坏了,是浏览器根本没起来。v0.9.14 起每场 e2e 各自抄了一份 fallback,
到 p3 这一轮数了数是 **12 份副本**;而 v0.9.09~0.9.13 那五场**没抄**,于是它们
每次都"红",红在起浏览器那一步,**连页面都没打开** —— 也就是说那五场守的东西
(项目面板/权限四档/会话分组/技能渠道/模型管理)这段时间其实是零覆盖,而没人从
输出里看出来。教训:**一场测试"没跑"和"跑挂了"必须在输出里长得不一样**。

用法:
    from e2e_launch import launch
    browser = launch(p)
"""
from __future__ import annotations

import os
from pathlib import Path


def _candidates() -> list[str]:
    base = os.environ.get("LOCALAPPDATA", "")
    root = Path(base, "ms-playwright")
    return sorted(root.glob("chromium-*/chrome-win*/chrome.exe"),
                  key=str, reverse=True)


def launch(p):
    """起 chromium;headless shell 不在就退回本机装好的完整版。

    两条路都起不来时把异常原样抛出去(不吞、不假绿)。
    """
    try:
        return p.chromium.launch()
    except Exception:
        for d in _candidates():
            return p.chromium.launch(executable_path=str(d), headless=True)
        raise RuntimeError(
            "既没有 headless shell,也在 LOCALAPPDATA/ms-playwright 里找不到 "
            f"chrome.exe(找过 {str(Path(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright'))} "
            "的 chromium-*/chrome-win*/chrome.exe)—— 先 `playwright install chromium`"
        )
