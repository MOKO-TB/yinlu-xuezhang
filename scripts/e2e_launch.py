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

⚠ 上面是**当时**的账。2026-10-08 实测核过,两处已经不是那样了,别照旧文档行事:
- 各抄一份退路的脚本现在是 **29 支**(不是 12)—— 抽完这支模块之后每轮又长;
  新增由 `tests/test_suite_shape.py::test_no_new_hand_rolled_browser_fallback` 钉住
  「只准减少,不准增加」。批量收编是安全的(下面那只函数就是严格超集:先试裸 launch,
  失败才找 chrome.exe,两条都不行抛得明明白白),但一次一支跑通再挪,别在风格改动里
  一起动 29 支没验过的浏览器场。
- 这台机器上裸 `p.chromium.launch()` **现在起得来**(chromium 151.0.7922.34,页面真
  渲染),所以「没抄退路的十一支每次都红、连页面都没打开」已经不成立 —— 那 11 支没病。
  这支模块仍然要留着:换一台没装 headless shell 的机器,它仍是唯一的出路。

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
