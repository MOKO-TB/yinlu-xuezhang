"""路径解析:兼容源码运行与 PyInstaller 打包两种形态。

- RESOURCE_DIR:只读资源(system_prompt.md、config.example.json);
  打包后位于解包临时目录 _MEIPASS。
- APP_DIR:用户数据目录(config.json、sessions/);
  打包后为 exe 所在目录,便于"绿色便携"使用。
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = RESOURCE_DIR

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
