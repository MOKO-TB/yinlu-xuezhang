"""子代理执行留档与只读状态核对，不自动重放。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from minicode.paths import app_path
from minicode.session import Session, _start_meta

_LOCK = threading.RLock()
_ACTIVE: set[tuple[str, str]] = set()
_PROCESS_TOKEN = str(uuid.uuid4())
_GENERATION = 0
TERMINAL = {"completed", "failed", "interrupted"}


def generation() -> int:
    return _GENERATION


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value))


def path_key(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _probe_process(pid: int) -> tuple[str, str]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "unknown", ""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.GetProcessTimes.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        handle = k.OpenProcess(0x1000, False, pid)
        if not handle:
            return ("exited" if ctypes.get_last_error() == 87 else "unknown"), ""
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
                return "unknown", ""
            if not k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return "unknown", ""
            birth = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            return ("alive" if code.value == 259 else "exited"), birth
        finally:
            k.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
            parts = stat.rsplit(")", 1)[1].split()
            return ("exited" if parts[0] == "Z" else "alive"), f"{boot}:{parts[19]}"
        except FileNotFoundError:
            if Path("/proc/self/stat").exists() and not Path(f"/proc/{pid}").exists():
                return "exited", ""
        except (OSError, IndexError):
            pass
    return "unknown", ""


class TaskRecords:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir).resolve()
        self.root = self.sessions_dir / "subagents"

    def run_dir(self, run_id: str) -> Path:
        if not valid_id(run_id):
            raise ValueError("无效任务记录 ID")
        if self.root.resolve().parent != self.sessions_dir:
            raise ValueError("任务目录越界")
        target = self.root / run_id
        if target.resolve().parent != self.root.resolve():
            raise ValueError("任务目录越界")
        return target

    def _path(self, run_id: str) -> Path:
        directory = self.run_dir(run_id)
        target = directory / "record.json"
        if target.resolve().parent != directory.resolve():
            raise ValueError("任务记录越界")
        return target

    def write(self, record: dict) -> None:
        global _GENERATION
        with _LOCK:
            _GENERATION += 1
            target = self._path(record["id"])
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(record, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)

    def read(self, run_id: str) -> dict:
        with _LOCK:
            data = json.loads(self._path(run_id).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("id") != run_id:
                raise ValueError("任务记录损坏")
            state = str(data.get("status") or "unknown")
            active = (str(self.root), run_id) in _ACTIVE
            if active:
                state = "running"
            elif state == "running":
                if data.get("pid") == os.getpid() and data.get("process_token") == _PROCESS_TOKEN:
                    state = "unknown"
                else:
                    try:
                        live, birth = _probe_process(data.get("pid"))
                    except Exception:
                        live, birth = "unknown", ""
                    saved_birth = data.get("process_created")
                    if birth and saved_birth and birth != saved_birth:
                        state = "unknown"
                    elif live == "exited":
                        state = "interrupted"
                    elif live == "alive" and birth and birth == saved_birth:
                        state = "running"
                    else:
                        state = "unknown"
            return {**data, "status": state, "can_continue": state in TERMINAL
                    and not active and not data.get("archive_error")}

    def parent_path(self, record: dict, workspace: Path) -> Path:
        name = record.get("root_parent_log", "")
        if not isinstance(name, str) or not re.fullmatch(r"session_[\w.-]+\.jsonl", name):
            raise ValueError("原父会话没有有效留档")
        target = self.sessions_dir / name
        if target.resolve().parent != self.sessions_dir or not target.is_file():
            raise ValueError("原父会话留档不存在或越界")
        meta = _start_meta(target)
        recorded = record.get("workspace")
        if not recorded or not meta.get("workspace") or path_key(recorded) != path_key(workspace) \
                or path_key(meta["workspace"]) != path_key(workspace):
            raise ValueError("任务或父会话不属于当前工作区")
        return target

    def list_for(self, workspace: Path) -> list[dict]:
        out = []
        if self.root.resolve().parent != self.sessions_dir:
            return out
        for directory in self.root.glob("*"):
            if not valid_id(directory.name):
                continue
            try:
                record = self.read(directory.name)
                self.parent_path(record, workspace)
                out.append(record)
            except (OSError, ValueError, TypeError):
                continue
        return sorted(out, key=lambda r: str(r.get("started_at", "")), reverse=True)


class TaskRun:
    def __init__(self, ctx, role: str, goal: str):
        parent = getattr(ctx, "session", None)
        self.store = getattr(parent, "_task_store", None) or TaskRecords(
            getattr(parent, "_log_dir", None) or app_path("sessions"))
        parent_log = getattr(parent, "log_path", None)
        self.error = ""
        self.data = {
            "id": str(uuid.uuid4()), "pid": os.getpid(), "process_token": _PROCESS_TOKEN,
            "process_created": "", "parent_log": str(parent_log or ""),
            "root_parent_log": getattr(parent, "_task_root_parent_log", "")
            or (parent_log.name if parent_log else ""),
            "parent_task_id": getattr(parent, "_task_run_id", ""),
            "workspace": str(getattr(parent, "_task_original_workspace", ctx.workspace)),
            "requested_workspace": str(ctx.workspace), "execution_workspace": str(ctx.workspace),
            "worktree_path": "", "worktree_base": "", "role": role, "goal": goal,
            "started_at": time.time(), "ended_at": None, "status": "running",
            "report": "", "has_report": False, "archive_error": "", "session_log": "",
        }
        try:
            self.data["process_created"] = _probe_process(os.getpid())[1]
        except Exception:
            pass
        with _LOCK:
            _ACTIVE.add((str(self.store.root), self.data["id"]))
        self.save()

    def save(self, **fields) -> None:
        self.data.update(fields)
        self.data["archive_error"] = self.error
        try:
            self.store.write(self.data)
        except Exception as exc:
            self.log_error(exc)

    def log_error(self, exc) -> None:
        self.error = f"留档写入失败，不能保证可恢复：{type(exc).__name__}: {exc}"
        self.data["archive_error"] = self.error

    def make_session(self, system_prompt: str, workspace: Path) -> Session:
        run = self

        class RecordedSession(Session):
            def _ensure_log(self):
                try:
                    super()._ensure_log()
                except Exception as exc:
                    run.log_error(exc)

            def _log_only(self, obj):
                try:
                    super()._log_only(obj)
                except Exception as exc:
                    run.log_error(exc)

        try:
            directory = self.store.run_dir(self.data["id"])
        except Exception as exc:
            self.log_error(exc)
            directory = None
        session = RecordedSession(system_prompt, log_dir=directory,
                                  workspace=workspace, origin="subagent")
        session._task_store = self.store
        session._task_run_id = self.data["id"]
        session._task_root_parent_log = self.data["root_parent_log"]
        session._task_original_workspace = self.data["workspace"]
        session._task_parent_log = self.data["parent_log"]
        session._ensure_log()
        self.save(session_log=str(session.log_path or ""))
        return session

    def finish(self, report: str, has_report: bool) -> str:
        with _LOCK:
            self.save(status="completed" if has_report else "failed", report=report,
                      has_report=has_report, ended_at=time.time())
            _ACTIVE.discard((str(self.store.root), self.data["id"]))
        note = f"\n[任务留档 {self.data['id']}] "
        if self.error:
            return report + note + self.error
        if not self.data["root_parent_log"]:
            return report + note + "缺少父会话留档，无法从界面继续处理。"
        return report + note + "已留档；执行结束不代表验收通过。"
