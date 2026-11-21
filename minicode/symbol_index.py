"""工作区符号索引的跨进程缓存:同一份源文件没变就不再解析。

只缓存符号名/位置/签名/文档摘要,不缓存源码全文；文件按 lstat 的
mtime/ctime/size 失效。索引落在数据目录 `code-index/`,绝不随安装包外发。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from minicode.paths import app_path

INDEX_DIR = "code-index"
VERSION = 1
MAX_FILES = 20_000
MAX_BYTES = 8 * 1024 * 1024

_LOCK = threading.RLock()
_CACHE: dict[str, dict] = {}


def _target(workspace: Path) -> Path:
    key = hashlib.sha1(
        os.path.normcase(str(Path(workspace).resolve())).encode("utf-8")
    ).hexdigest()[:16]
    return app_path(INDEX_DIR) / f"{key}.json"


def load(workspace: Path) -> dict:
    """本进程里按索引文件状态复用；文件坏了/被换掉一律当没有。"""
    target = _target(workspace)
    key = str(target)
    try:
        st = target.stat()
    except OSError:
        return {}
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached["mtime"] == st.st_mtime_ns and cached["size"] == st.st_size:
            return dict(cached["data"])
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (not isinstance(data, dict) or data.get("version") != VERSION
            or data.get("workspace") != str(Path(workspace).resolve())):
        return {}
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    with _LOCK:
        if len(_CACHE) > 16:
            _CACHE.clear()
        _CACHE[key] = {"mtime": st.st_mtime_ns, "size": st.st_size, "data": dict(files)}
    return dict(files)


def save(workspace: Path, files: dict) -> bool:
    """原子写；超出上限或写不进去就放弃缓存，绝不影响搜索本身。"""
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        return False
    payload = {"version": VERSION, "workspace": str(Path(workspace).resolve()),
               "files": files}
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return False
    if len(raw) > MAX_BYTES:
        return False
    target = _target(workspace)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".index-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        st = target.stat()
    except OSError:
        return False
    with _LOCK:
        _CACHE[str(target)] = {"mtime": st.st_mtime_ns, "size": st.st_size,
                               "data": dict(files)}
    return True