"""任务监控栏的偏好(v0.9.37):这一栏开没开、哪几段被折叠过。

为什么要落盘而不是放 localStorage:免安装版被换台机器、清一次 WebView2 缓存,
界面状态就全没了;而这一栏的"收起/展开"是用户拿鼠标点出来的习惯,丢了就等于
每次开机都要重新点开一次。写在本机一个 json 里,和 `close-pref.json`(关窗时
怎么办)、`notify-pref.json`(要不要弹桌面)是同一类东西 —— 纯界面偏好,
不含任何内容数据,所以**它自己**在「我的数据」面板里归在"设置与偏好"。

读不出来一律回默认值(收起),绝不抛:这一栏打不开只是少一栏,不该把启动带崩。
"""
from __future__ import annotations

import json

from minicode.paths import app_path

PREF_FILE = "monitor-pref.json"
# 四段各自的折叠状态。名字与前端那四个 section 一一对应,改一处要改两处。
SECTIONS = ("now", "plan", "timeline", "groups")
DEFAULTS = {"open": False, "folded": {}}


def pref_path():
    return app_path(PREF_FILE)


def _clean_folded(raw) -> dict:
    """只认已知段名,值收成真/假。未知键丢掉:老版本留下的键不该被原样喂回去。"""
    out = {}
    if isinstance(raw, dict):
        for k in SECTIONS:
            if k in raw:
                out[k] = bool(raw[k])
    return out


def read_pref() -> dict:
    """{"open": bool, "folded": {段名: 是否折叠}};读不出来就按默认(收起)。"""
    try:
        raw = json.loads(pref_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"open": DEFAULTS["open"], "folded": {}}
    if not isinstance(raw, dict):
        return {"open": DEFAULTS["open"], "folded": {}}
    return {"open": raw.get("open") is True, "folded": _clean_folded(raw.get("folded"))}


def write_pref(open_=None, folded=None) -> dict:
    """写一次偏好并回读校验(写不进去时返回的仍是内存里那份,界面不会自相矛盾)。

    `folded` 给增量:只改点过的那一段,别把用户另外三段折叠状态冲掉。
    """
    cur = read_pref()
    if open_ is not None:
        cur["open"] = bool(open_)
    if isinstance(folded, dict):
        cur["folded"].update(_clean_folded(folded))
    try:
        pref_path().write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    except OSError:
        pass        # 装进只读目录(Program Files):记不住但不影响用
    return cur
