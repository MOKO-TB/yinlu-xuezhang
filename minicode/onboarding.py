"""首次运行向导与数据目录可视化(v0.9.23)。

为什么单独一个模块,而不是把判断塞进前端:
- "该不该弹"要同时看两件事 —— 这台机器配好密钥没有、用户是不是已经关过这个向导。
  前端每次刷新都是新的,只有本机落一份偏好才谈得上"不再显示";
- 这份偏好必须**写得进去**:v0.9.22 那个"配了不保存"的坑就是数据目录只读时
  静默吞掉 OSError。所以这里读写都带回真实结论,由界面如实说给你听。

数据目录那几行(`paths` 进来的)是向导的核心信息,不是装饰:装到 Program Files
这类只读位置时,学长会把状态挪到 %LOCALAPPDATA%,不写出来没人知道去哪找配置。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from minicode.paths import app_path

PREF_FILE = "onboarding.json"
WIZARD_KEY = "first_run"          # 配密钥向导:只对"还没配好"的人弹
TOUR_KEY = "tour"                 # 功能导览(v0.9.67):独立 key,面向所有人一次
SCHEMA_VERSION = 1

# 三步的内容写在这里,界面上只负责排版:文案改了不用动前端,也不会两处对不上。
STEPS = (
    {"key": "key", "title": "① 接上模型",
     "body": "点顶部的模型名开「模型管理」,挑一家供应商、填 API Key。"
             "Key 只写进下面这个数据目录里的 config.json,不会随安装包外发。"},
    {"key": "workspace", "title": "② 选个工作区",
     "body": "学长在你选的文件夹里干活:生成的文件、记住的偏好都落在那儿。"
             "左侧「工作区」可以随时换,换工作区不会动配置。"},
    {"key": "try", "title": "③ 先试一句话",
     "body": "「今天有什么课」「帮我看看官网有没有新通知」「把这份表格理一下」——"
             "课表可以在课表面板里从教务系统一键导入(登录那步必须你自己来)。"},
)


# 功能导览的三步(v0.9.67):把攒了二十多个版本的入口在第一次打开时说清 ——
# 内置校园模型开箱即用,大多数同学永远不会因为"没配密钥"看到上面那个向导,
# 这条是他们的第一面。仍然只讲**真实存在**的东西,不讲没做的。
TOUR_STEPS = (
    {"key": "rail", "title": "① 左栏那排图标是全部入口",
     "body": "地图、课表(可一键从教务导入,登录只由你自己输)、心情、计划、任务监控、"
             "技能、记忆、用量、我的数据都在那里;顶栏只留工作区、模型、权限这几个状态。"},
    {"key": "composer", "title": "② 输入框的三个小抄",
     "body": "打 @ 引用工作区文件(路径不用手敲);打 / 弹动作面板(导出、压缩、换主题"
             "这些一步直达);Enter 发送,Shift+Enter 换行。桌面提醒开关在右下角托盘图标"
             "的右键菜单里 —— 简报、课前、待办、通知、周报都会弹到桌面。"},
    {"key": "trust", "title": "③ 数据都在本机,来源都可核对",
     "body": "对话、课表、成绩、心情全部存在下面这个数据目录里,换电脑用「💾 数据」"
             "导出迁移包;API 密钥不会随安装包外发。放假安排、成绩这些以官网为准 ——"
             "学长会给通知原文和链接,找不到就说找不到,绝不编。"},
)


def state_path() -> Path:
    return app_path(PREF_FILE)


def read() -> dict:
    """读向导偏好;读不到/读坏了都当"没关过"(宁可再弹一次,别把入口弄丢)。"""
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"dismissed": []}
    if not isinstance(raw, dict):
        return {"dismissed": []}
    d = raw.get("dismissed")
    return {"dismissed": [str(x) for x in d] if isinstance(d, list) else []}


def _write(data: dict) -> tuple[bool, str]:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)     # 首次运行时目录可能还不存在
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        return False, f"错误: 这个偏好写不进去({type(exc).__name__}):{p}"
    return True, ""


def dismiss(key: str = WIZARD_KEY) -> dict:
    """勾了「不再显示」:落盘,并如实告诉界面有没有落成功。"""
    key = str(key or WIZARD_KEY)
    st = read()
    if key in st["dismissed"]:
        return {"ok": True, "msg": ""}
    st["dismissed"].append(key)
    ok, msg = _write({"schema": SCHEMA_VERSION, "dismissed": st["dismissed"],
                      "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return {"ok": ok, "msg": msg}


def reset() -> dict:
    """设置里想再看一遍时用。"""
    try:
        state_path().unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "msg": f"错误: 清不掉({type(exc).__name__})"}
    return {"ok": True, "msg": "下次启动会再放一次首次运行向导。"}


def payload(*, key_configured: bool, paths: dict | None = None) -> dict:
    """界面一次性要的东西:弹不弹、三步内容、数据目录实况。

    `key_configured=True` 时不再弹 —— 向导的目的是把"还没配好"的人送到模型管理,
    已经配好的人不需要第三次提醒。
    """
    st = read()
    show = (not key_configured) and (WIZARD_KEY not in st["dismissed"])
    # 功能导览:独立 key、面向所有人一次。**首启向导正在弹时先让位** ——
    # 同一个弹层不排队,先解决"能不能用",导览下次启动或配好密钥后再露面。
    tour_show = (TOUR_KEY not in st["dismissed"]) and (not show)
    return {"ok": True, "show": show, "steps": [dict(s) for s in STEPS],
            "tour_show": tour_show, "tour_steps": [dict(s) for s in TOUR_STEPS],
            "key_configured": bool(key_configured), "paths": paths or {},
            "data_dir": str((paths or {}).get("app_dir") or state_path().parent)}
