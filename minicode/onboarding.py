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
WIZARD_KEY = "first_run"          # 目前只有一个向导;键留着以后加分步提示
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
    return {"ok": True, "show": show, "steps": [dict(s) for s in STEPS],
            "key_configured": bool(key_configured), "paths": paths or {},
            "data_dir": str((paths or {}).get("app_dir") or state_path().parent)}
