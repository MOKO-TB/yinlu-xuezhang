"""路径解析:兼容源码运行、绿色便携与安装版三种形态。

- RESOURCE_DIR:只读资源(system_prompt.md、config.example.json);
  打包后位于解包临时目录 _MEIPASS。
- APP_DIR:用户数据目录(config.json、sessions/、model_caps.json…);
  打包后**默认**为 exe 所在目录,便于"绿色便携"使用。

为什么要"默认"两个字(v0.9.22):安装版若被装进 `C:\\Program Files`(安装时
以管理员身份运行 setup.exe 就会这样),exe 目录是**只读**的 —— 保存的模型
Key、勾选的思考档位、探到的模型能力全都写不进去,表现为"配好 Key 退出去再
进就没了""拖文件进对话框报错"。所以这里做一次写探针:写不进去就整体搬到
`%LOCALAPPDATA%\\引路学长`(没有 LOCALAPPDATA 时退回 `~/.yinluxs/app`),
并把 exe 目录里那份旧 config.json 复制过去,别让用户白配一遍。

便携版行为完全不变:exe 旁能写就用 exe 旁。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

STATE_DIR_NAME = "引路学长"

# 数据到底落在哪:"exe" / "repo" / "local"。给界面与日志看,别再让用户猜。
STATE_BACKEND = "repo"


def _is_writable(directory: Path) -> bool:
    """能否在此建目录并写文件(探针文件写完就删)。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe.tmp"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _local_state_dir() -> Path:
    """用户级可写目录:优先 %LOCALAPPDATA%,退回 ~/.yinluxs。"""
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local) / STATE_DIR_NAME
    return Path.home() / ".yinluxs" / "app"


def _adopt(source: Path, target: Path) -> None:
    """只读目录里已有的 config.json 搬到可写目录(目标已存在就不覆盖)。"""
    try:
        target.mkdir(parents=True, exist_ok=True)
        src = source / "config.json"
        dst = target / "config.json"
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
    except OSError:
        pass        # 搬不动也别让程序起不来:界面会提示重新配置模型


def _frozen_app_dir(exe_dir: Path) -> tuple[Path, str]:
    """冻结形态下选数据目录:exe 旁能写就用它,写不了就搬到用户目录。

    单独抽出来是为了能被测试直接调用(整个模块 reload 会把 RESOURCE_DIR
    一起换掉,污染后面的用例)。
    """
    if _is_writable(exe_dir):
        return exe_dir, "exe"
    target = _local_state_dir()
    target.mkdir(parents=True, exist_ok=True)
    _adopt(exe_dir, target)
    return target, "local"


if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR, STATE_BACKEND = _frozen_app_dir(Path(sys.executable).resolve().parent)
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = RESOURCE_DIR
    STATE_BACKEND = "repo" if _is_writable(APP_DIR) else "local"
    if STATE_BACKEND == "local":
        APP_DIR = _local_state_dir()
        APP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_CONFIG_PATH = RESOURCE_DIR / "config.example.json"
SYSTEM_PROMPT_PATH = RESOURCE_DIR / "system_prompt.md"
SESSIONS_DIR = APP_DIR / "sessions"


def app_path(*parts: str) -> Path:
    """可写路径:配置、会话等跟随 exe(开发态=项目根)。"""
    return APP_DIR.joinpath(*parts) if parts else APP_DIR


def resource_path(*parts: str) -> Path:
    """只读资源:随应用打包的文件(开发态=项目根)。"""
    return RESOURCE_DIR.joinpath(*parts) if parts else RESOURCE_DIR


def diagnostics() -> dict:
    """数据目录实况:出问题时让用户截一张图就能定位(v0.9.22)。

    `writable=False` 是"配了不保存"类反馈的唯一真相来源,别靠猜。
    """
    return {
        "app_dir": str(APP_DIR),
        "config": str(CONFIG_PATH),
        "backend": STATE_BACKEND,
        "frozen": FROZEN,
        "writable": _is_writable(APP_DIR),
    }
