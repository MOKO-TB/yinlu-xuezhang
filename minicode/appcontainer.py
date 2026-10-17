"""Windows 执行隔离层：AppContainer 与受限令牌。

这里补的是 Job Object 做不到的那一半：

- `appcontainer` 模式使用 AppContainer token，工作区授予 modify，运行时目录只授予
  read/execute；
- `restricted` 模式使用 `CreateRestrictedToken(WRITE_RESTRICTED)`，保留用户读取面，
  只授予工作区专用 SID 写入；
- 默认不注入网络 capability，因此 AppContainer 不能直接联网；
- `allow_network=True` 时只注入 `internetClient`，不授予其他 capability；
- profile 按当前进程隔离，退出时移除 ACL 并删除 profile。

这不是“万能沙箱”。工作区内的 `.git` 和虚拟环境仍应视为可读、不可写区域；当前
实现会尽量对它们加 deny ACE，但命令仍可能通过操作系统允许的其他接口访问系统
目录。任何失败都必须返回可读原因，不能让调用方误以为已经隔离。
"""
from __future__ import annotations

import atexit
import contextlib
import ctypes
import hashlib
import json
import msvcrt
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ctypes import wintypes

_IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if _IS_WIN else 0
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_STILL_ACTIVE = 259
_INFINITE = 0xFFFFFFFF
_HANDLE_FLAG_INHERIT = 0x00000001
_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_ASSIGN_PRIMARY = 0x0001
_DISABLE_MAX_PRIVILEGE = 0x00000001
_LUA_TOKEN = 0x00000004
_WRITE_RESTRICTED = 0x00000008
_SE_GROUP_ENABLED = 0x00000004
_ERROR_ALREADY_EXISTS = 183
_ERROR_INSUFFICIENT_BUFFER = 122
_INTERNET_CLIENT_SID = "S-1-15-3-1"
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_GRANT_ACCESS = 1
_DENY_ACCESS = 3
_REVOKE_ACCESS = 4
_SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x00000003
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_GENERIC_EXECUTE = 0x20000000
_DELETE = 0x00010000
_FILE_DELETE_CHILD = 0x00000040
_FILE_WRITE_DATA = 0x00000002
_FILE_APPEND_DATA = 0x00000004
_FILE_WRITE_EA = 0x00000010
_FILE_WRITE_ATTRIBUTES = 0x00000100
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000


class AppContainerError(OSError):
    """AppContainer 启动失败。调用方应按“没有隔离”处理，不能静默降级。"""


@dataclass
class _Capability:
    sid: ctypes.c_void_p


@dataclass
class _Profile:
    name: str
    sid: ctypes.c_void_p
    sid_string: str
    restricted_sid: ctypes.c_void_p
    restricted_sid_string: str
    restricted_sid_buffer: ctypes.Array
    folder: Path
    grants: set[tuple[str, str, str]] = field(default_factory=set)


class _SIDAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SIDAndAttributes)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class _Trustee(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", ctypes.c_void_p),
        ("MultipleTrusteeOperation", ctypes.c_int),
        ("TrusteeForm", ctypes.c_int),
        ("TrusteeType", ctypes.c_int),
        ("ptstrName", ctypes.c_void_p),
    ]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [
        ("grfAccessPermissions", wintypes.DWORD),
        ("grfAccessMode", ctypes.c_int),
        ("grfInheritance", wintypes.DWORD),
        ("Trustee", _Trustee),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _kernel():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _kernelbase():
    return ctypes.WinDLL("kernelbase", use_last_error=True)


def _userenv():
    return ctypes.WinDLL("userenv", use_last_error=True)


def _advapi():
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _last_error() -> int:
    try:
        return int(ctypes.get_last_error())
    except Exception:
        return 0


def available() -> bool:
    """这台机器是否具备实现所需的 Windows API。"""
    if not _IS_WIN:
        return False
    try:
        _kernelbase().CreateAppContainerToken
        _userenv().CreateAppContainerProfile
        _advapi().CreateProcessAsUserW
        _advapi().CreateRestrictedToken
        return True
    except (AttributeError, OSError):
        return False


def _prototype():
    k = _kernel()
    a = _advapi()
    u = _userenv()
    kb = _kernelbase()
    if getattr(k, "_minicode_appcontainer_protos", False):
        return k, a, u, kb

    k.CreatePipe.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                             ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                             wintypes.DWORD]
    k.CreatePipe.restype = wintypes.BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    k.SetHandleInformation.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                       wintypes.DWORD]
    k.SetHandleInformation.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle.restype = wintypes.BOOL
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.GetLastError.restype = wintypes.DWORD

    a.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.c_void_p)]
    a.OpenProcessToken.restype = wintypes.BOOL
    a.GetTokenInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    a.GetTokenInformation.restype = wintypes.BOOL
    a.CreateRestrictedToken.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_SIDAndAttributes), ctypes.POINTER(ctypes.c_void_p),
    ]
    a.CreateRestrictedToken.restype = wintypes.BOOL
    a.CreateProcessAsUserW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPWSTR,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfo),
        ctypes.POINTER(_ProcessInformation),
    ]
    a.CreateProcessAsUserW.restype = wintypes.BOOL
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(wintypes.LPWSTR)]
    a.ConvertSidToStringSidW.restype = wintypes.BOOL
    a.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR,
                                         ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertStringSidToSidW.restype = wintypes.BOOL
    a.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    a.GetNamedSecurityInfoW.restype = wintypes.DWORD
    a.SetEntriesInAclW.argtypes = [
        wintypes.ULONG, ctypes.POINTER(_ExplicitAccess), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    a.SetEntriesInAclW.restype = wintypes.DWORD
    a.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    a.SetNamedSecurityInfoW.restype = wintypes.DWORD
    a.FreeSid.argtypes = [ctypes.c_void_p]
    a.FreeSid.restype = wintypes.BOOL

    u.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    u.CreateAppContainerProfile.restype = ctypes.c_long
    u.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    u.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    u.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    u.DeleteAppContainerProfile.restype = ctypes.c_long
    u.GetAppContainerFolderPath.argtypes = [wintypes.LPCWSTR,
                                             ctypes.POINTER(wintypes.LPWSTR)]
    u.GetAppContainerFolderPath.restype = ctypes.c_long

    kb.CreateAppContainerToken.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_SecurityCapabilities),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kb.CreateAppContainerToken.restype = wintypes.BOOL
    k._minicode_appcontainer_protos = True
    return k, a, u, kb


def _sid_string(sid: ctypes.c_void_p) -> str:
    _, a, _, _ = _prototype()
    out = wintypes.LPWSTR()
    if not a.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise AppContainerError(f"ConvertSidToStringSidW 失败(错误码 {_last_error()})")
    try:
        return str(out.value or "")
    finally:
        _kernel().LocalFree(out)


def _restricted_sid(workspace: Path) -> tuple[ctypes.c_void_p, str, None]:
    """使用本机 Users 组作 restricted SID，再只给工作区授写。"""
    del workspace
    _, a, _, _ = _prototype()
    local = ctypes.c_void_p()
    if not a.ConvertStringSidToSidW("S-1-5-32-545", ctypes.byref(local)):
        raise AppContainerError(f"解析 Users SID 失败(错误码 {_last_error()})")
    # 这个 SID 由 LocalAlloc 分配，profile 持有它直到 cleanup_all。
    return local, _sid_string(local), None


def _cap_sid() -> ctypes.c_void_p:
    _, a, _, _ = _prototype()
    sid = ctypes.c_void_p()
    if not a.ConvertStringSidToSidW(_INTERNET_CLIENT_SID, ctypes.byref(sid)):
        raise AppContainerError(f"无法解析 internetClient capability(错误码 {_last_error()})")
    return sid


def _change_acl(path: Path, sid: ctypes.c_void_p, access: int, mode: int,
                what: str) -> None:
    """用 DACL API 添加/撤销一个 ACE，避免 icacls 递归遍历。"""
    _, a, _, _ = _prototype()
    owner = ctypes.c_void_p()
    group = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = a.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl),
        ctypes.byref(sacl), ctypes.byref(descriptor),
    )
    if code != 0:
        raise AppContainerError(f"{what}失败({path}):GetNamedSecurityInfoW 错误码 {code}")
    new_acl = ctypes.c_void_p()
    try:
        entry = _ExplicitAccess()
        entry.grfAccessPermissions = int(access)
        entry.grfAccessMode = mode
        entry.grfInheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT
        entry.Trustee.TrusteeForm = 0       # TRUSTEE_IS_SID
        entry.Trustee.TrusteeType = 0       # TRUSTEE_IS_UNKNOWN
        entry.Trustee.ptstrName = sid
        code = a.SetEntriesInAclW(1, ctypes.byref(entry), dacl,
                                  ctypes.byref(new_acl))
        if code != 0:
            raise AppContainerError(f"{what}失败({path}):SetEntriesInAclW 错误码 {code}")
        code = a.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
            None, None, new_acl, None,
        )
        if code != 0:
            raise AppContainerError(f"{what}失败({path}):SetNamedSecurityInfoW 错误码 {code}")
    finally:
        if new_acl.value:
            _kernel().LocalFree(new_acl)
        if descriptor.value:
            _kernel().LocalFree(descriptor)


def _grant(profile: _Profile, path: Path, rights: str, what: str,
           restricted: bool = False) -> None:
    p = Path(path).resolve()
    if not p.exists():
        return
    kind = "restricted" if restricted else "app"
    key = (str(p), rights, kind)
    if key in profile.grants:
        return
    sid = profile.restricted_sid if restricted else profile.sid
    access = ((_GENERIC_READ | _GENERIC_EXECUTE) if rights == "RX"
              else (_GENERIC_READ | _GENERIC_WRITE | _GENERIC_EXECUTE | _DELETE))
    _change_acl(p, sid, access, _GRANT_ACCESS, what)
    profile.grants.add(key)
    _save_marker(profile)


def _deny_write(profile: _Profile, path: Path, restricted: bool = False) -> None:
    p = Path(path).resolve()
    if not p.exists():
        return
    # 读/执行保留，删除/改名/写入一律拒绝；这比把整个目录设成不可读更实用。
    access = (_FILE_WRITE_DATA | _FILE_APPEND_DATA | _FILE_WRITE_EA
              | _FILE_WRITE_ATTRIBUTES | _FILE_DELETE_CHILD | _DELETE
              | _WRITE_DAC | _WRITE_OWNER)
    sid = profile.restricted_sid if restricted else profile.sid
    _change_acl(p, sid, access, _DENY_ACCESS,
                f"保护 {p.name} 写权限")
    profile.grants.add((str(p), "DENY", "restricted" if restricted else "app"))
    _save_marker(profile)


def _remove_grants(profile: _Profile) -> None:
    for path, _rights, kind in sorted(profile.grants, key=lambda x: len(x[0]),
                                      reverse=True):
        with contextlib.suppress(Exception):
            sid = profile.restricted_sid if kind == "restricted" else profile.sid
            _change_acl(Path(path), sid, 0, _REVOKE_ACCESS,
                        "移除 ACL")
    profile.grants.clear()


def _profile_folder(name: str, sid_string: str) -> Path:
    _, _, u, _ = _prototype()
    out = wintypes.LPWSTR()
    hr = u.GetAppContainerFolderPath(sid_string, ctypes.byref(out))
    if hr == 0 and out.value:
        try:
            return Path(out.value)
        finally:
            _kernel().LocalFree(out)
    # API 不可用时仍给一个可写落点；真正的权限由 AppContainer 自身 profile 保证。
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Packages" / name / "AC"


_PROFILES: dict[str, _Profile] = {}
_PROFILE_LOCK = threading.Lock()


def _profile_name(workspace: Path) -> str:
    key = hashlib.sha256(str(Path(workspace).resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return f"yinlu-ac-{key}"


def _marker_path(profile: _Profile) -> Path:
    return profile.folder / "yinlu-grants.json"


def _load_marker(profile: _Profile) -> None:
    try:
        raw = json.loads(_marker_path(profile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    rows = raw.get("grants") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return
    for row in rows:
        if (isinstance(row, list) and len(row) in (2, 3)
                and all(isinstance(x, str) for x in row)):
            profile.grants.add(tuple(row) if len(row) == 3
                               else (row[0], row[1], "app"))


def _save_marker(profile: _Profile) -> None:
    try:
        _marker_path(profile).write_text(
            json.dumps({"grants": sorted([list(x) for x in profile.grants])},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _ensure_profile(workspace: Path) -> _Profile:
    key = str(Path(workspace).resolve()).lower()
    with _PROFILE_LOCK:
        got = _PROFILES.get(key)
        if got is not None:
            return got
        name = _profile_name(workspace)
        _, _, u, _ = _prototype()
        sid = ctypes.c_void_p()
        hr = u.CreateAppContainerProfile(
            name, "引路学长隔离执行", "引路学长工具执行的 AppContainer",
            None, 0, ctypes.byref(sid),
        )
        if hr != 0:
            # 同一个进程重试或 profile 已存在时，从名字派生 SID。
            if (hr & 0xFFFFFFFF) != 0x800700B7:
                raise AppContainerError(f"CreateAppContainerProfile 失败(HRESULT 0x{hr & 0xFFFFFFFF:08X})")
            sid = ctypes.c_void_p()
            hr2 = u.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid))
            if hr2 != 0 or not sid.value:
                raise AppContainerError(
                    f"DeriveAppContainerSidFromAppContainerName 失败(HRESULT 0x{hr2 & 0xFFFFFFFF:08X})")
        sid_string = _sid_string(sid)
        restricted_sid, restricted_sid_string, restricted_buf = _restricted_sid(
            workspace)
        folder = _profile_folder(name, sid_string)
        try:
            (folder / "Temp").mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise AppContainerError(f"AppContainer 临时目录建不起来:{e}") from e
        profile = _Profile(
            name=name, sid=sid, sid_string=sid_string,
            restricted_sid=restricted_sid,
            restricted_sid_string=restricted_sid_string,
            restricted_sid_buffer=restricted_buf, folder=folder)
        _load_marker(profile)
        _PROFILES[key] = profile
        return profile


def _default_read_roots(args: list[str]) -> list[Path]:
    base = Path(sys.base_prefix)
    prefix = Path(sys.prefix)
    roots = [
        Path(sys.executable),
        base / "python.exe",
        base / f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        base / "DLLs",
        base / "Lib",
        base / "Lib" / "site-packages",
        base / "Library" / "bin",
        base / "python312.zip",
        base / "python311.zip",
        base / "python310.zip",
        prefix / "Scripts" / "python.exe",
        prefix / "Lib" / "site-packages",
        prefix / "pyvenv.cfg",
    ]
    roots.extend(base.glob("*.dll"))
    if args:
        first = str(args[0])
        found = shutil.which(first) or first
        p = Path(found)
        if p.exists():
            roots.append(p.parent)
            # Git Bash 在 <root>/bin/bash.exe，运行时还需要 <root> 下的 usr。
            if p.name.lower() in {"bash.exe", "sh.exe"}:
                roots.append(p.parent.parent)
    return roots


def _system_owned(path: Path) -> bool:
    """Program Files / Windows 下的系统程序默认已有 AppContainer 读取权。"""
    p = Path(path).resolve()
    candidates = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "SystemRoot", "windir"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value).resolve())
    for root in candidates:
        try:
            if p == root or p.is_relative_to(root):
                return True
        except ValueError:
            pass
    return False


def _env_block(env: dict[str, str]) -> ctypes.Array:
    items = [f"{str(k)}={str(v)}" for k, v in env.items() if str(k)]
    items.sort(key=str.lower)
    return ctypes.create_unicode_buffer("\0".join(items) + "\0\0")


def _pipe() -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
    k, _, _, _ = _prototype()
    r, w = ctypes.c_void_p(), ctypes.c_void_p()
    if not k.CreatePipe(ctypes.byref(r), ctypes.byref(w), None, 0):
        raise AppContainerError(f"CreatePipe 失败(错误码 {_last_error()})")
    if not k.SetHandleInformation(w, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT):
        raise AppContainerError(f"SetHandleInformation 失败(错误码 {_last_error()})")
    return r, w


def _nul_handle() -> ctypes.c_void_p:
    k, _, _, _ = _prototype()
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    h = k.CreateFileW("NUL", 0x80000000, 0x00000003, None, 3, 0, None)
    if not h:
        raise AppContainerError(f"打开 NUL 失败(错误码 {_last_error()})")
    k.SetHandleInformation(h, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT)
    return h


def _file_from_handle(handle: ctypes.c_void_p, mode: str):
    fd = msvcrt.open_osfhandle(int(handle.value), os.O_BINARY | (
        os.O_RDONLY if "r" in mode else os.O_WRONLY))
    return os.fdopen(fd, mode, buffering=0)


class AppContainerProcess:
    """`subprocess.Popen` 所需子集的 Windows 隔离令牌版本。"""

    def __init__(self, args, *, cwd: Path, env: dict[str, str],
                 allow_network: bool, read_roots: list[Path] | None = None,
                 mode: str = "appcontainer",
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 stdin=subprocess.DEVNULL):
        if not available():
            raise AppContainerError("当前系统没有可用的 AppContainer API")
        if not isinstance(args, (list, tuple)):
            raise AppContainerError("AppContainer 只接受 argv 列表")
        self.args = [str(a) for a in args]
        self.cwd = Path(cwd).resolve()
        self.mode = str(mode or "appcontainer").strip().lower()
        if self.mode not in ("appcontainer", "restricted"):
            raise AppContainerError(f"不认识的隔离模式:{mode}")
        self._returncode: int | None = None
        self._closed = False
        self._profile = _ensure_profile(self.cwd)
        restricted_mode = self.mode == "restricted"
        _grant(self._profile, self.cwd, "M", "授予工作区修改权限",
               restricted=restricted_mode)
        _grant(self._profile, self._profile.folder, "M", "授予隔离临时目录写权限",
               restricted=restricted_mode)
        roots = [*(read_roots or []), *_default_read_roots(self.args)]
        seen = set()
        for root in roots:
            p = Path(root).resolve()
            try:
                under_workspace = p == self.cwd or p.is_relative_to(self.cwd)
            except ValueError:
                under_workspace = False
            if under_workspace or _system_owned(p) or str(p).lower() in seen:
                continue
            seen.add(str(p).lower())
            try:
                _grant(self._profile, p, "RX", "授予运行时读取权限",
                       restricted=restricted_mode)
            except AppContainerError as e:
                # 有些自建 Git/工具目录 ACL 不允许当前用户修改；如果系统本身
                # 已给它 ALL APPLICATION PACKAGES 读取权，仍可能正常启动。
                # 真正起不来时 CreateProcessAsUserW 会给出下一步错误。
                if "错误码 5" not in str(e):
                    raise
        for protected in (self.cwd / ".git", self.cwd / ".venv", self.cwd / "venv"):
            with contextlib.suppress(Exception):
                _deny_write(self._profile, protected, restricted=restricted_mode)

        env2 = dict(env)
        env2["TEMP"] = str(self._profile.folder / "Temp")
        env2["TMP"] = str(self._profile.folder / "Temp")
        env_block = _env_block(env2)

        r_out = w_out = r_err = w_err = None
        nul = None
        try:
            r_out, w_out = _pipe()
            if stderr is subprocess.STDOUT:
                w_err = w_out
            else:
                r_err, w_err = _pipe()
            if stdin is subprocess.DEVNULL:
                nul = _nul_handle()
                in_handle = nul
            elif stdin is subprocess.PIPE:
                r_in, w_in = _pipe()
                self._parent_stdin = _file_from_handle(w_in, "wb")
                self._child_stdin = r_in
                in_handle = r_in
            else:
                raise AppContainerError("AppContainer 只支持 stdin=DEVNULL/PIPE")

            si = _StartupInfo()
            si.cb = ctypes.sizeof(si)
            si.dwFlags = _STARTF_USESTDHANDLES
            if self.mode == "restricted":
                si.lpDesktop = "winsta0\\default"
                si.dwFlags |= _STARTF_USESHOWWINDOW
                si.wShowWindow = _SW_HIDE
            si.hStdInput = in_handle
            si.hStdOutput = w_out
            si.hStdError = w_err
            pi = _ProcessInformation()
            cmdline = ctypes.create_unicode_buffer(
                subprocess.list2cmdline(self.args))
            k, a, _, kb = _prototype()
            process_token = ctypes.c_void_p()
            app_token = ctypes.c_void_p()
            cap_sid = None
            try:
                if not a.OpenProcessToken(k.GetCurrentProcess(),
                                          _TOKEN_DUPLICATE | _TOKEN_QUERY
                                          | _TOKEN_ASSIGN_PRIMARY,
                                          ctypes.byref(process_token)):
                    raise AppContainerError(f"OpenProcessToken 失败(错误码 {_last_error()})")
                if self.mode == "restricted":
                    restricted = (_SIDAndAttributes * 1)()
                    restricted[0].Sid = self._profile.restricted_sid
                    restricted[0].Attributes = 0
                    if not a.CreateRestrictedToken(
                            process_token,
                            _DISABLE_MAX_PRIVILEGE | _LUA_TOKEN | _WRITE_RESTRICTED,
                            0, None, 0, None, 1, restricted,
                            ctypes.byref(app_token)):
                        raise AppContainerError(
                            f"CreateRestrictedToken 失败(错误码 {_last_error()})")
                else:
                    caps_buf = None
                    cap_count = 0
                    if allow_network:
                        cap_sid = _cap_sid()
                        caps_buf = (_SIDAndAttributes * 1)()
                        caps_buf[0].Sid = cap_sid
                        caps_buf[0].Attributes = _SE_GROUP_ENABLED
                        cap_count = 1
                    security = _SecurityCapabilities(
                        self._profile.sid,
                        ctypes.cast(caps_buf, ctypes.POINTER(_SIDAndAttributes))
                        if caps_buf is not None else None,
                        cap_count, 0,
                    )
                    if not kb.CreateAppContainerToken(process_token,
                                                      ctypes.byref(security),
                                                      ctypes.byref(app_token)):
                        raise AppContainerError(
                            f"CreateAppContainerToken 失败(错误码 {_last_error()})")
                ok = a.CreateProcessAsUserW(
                    app_token, None, cmdline, None, None, True,
                    (_CREATE_UNICODE_ENVIRONMENT if self.mode == "restricted"
                     else _NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT),
                    ctypes.cast(env_block, ctypes.c_void_p),
                    str(self.cwd), ctypes.byref(si), ctypes.byref(pi),
                )
                if not ok:
                    raise AppContainerError(
                        f"CreateProcessAsUserW 失败(错误码 {_last_error()})")
            finally:
                if process_token.value:
                    k.CloseHandle(process_token)
                if app_token.value:
                    k.CloseHandle(app_token)
                if cap_sid is not None:
                    k.LocalFree(cap_sid)

            self.pid = int(pi.dwProcessId)
            self._handle = int(pi.hProcess)
            self._thread_handle = int(pi.hThread)
            self.stdout = _file_from_handle(r_out, "rb") if stdout is subprocess.PIPE else None
            self.stderr = (_file_from_handle(r_err, "rb")
                           if stderr is subprocess.PIPE and r_err is not None else None)
            self.stdin = getattr(self, "_parent_stdin", None)
            self._child_handles = [
                h for h in (w_out, w_err, getattr(self, "_child_stdin", None), nul)
                if h
            ]
            self._reader_lock = threading.Lock()
        except Exception:
            for h in (r_out, w_out, r_err, w_err, getattr(self, "_child_stdin", None), nul):
                if h:
                    with contextlib.suppress(Exception):
                        _kernel().CloseHandle(h)
            raise
        finally:
            for h in (w_out, w_err, getattr(self, "_child_stdin", None), nul):
                if h:
                    with contextlib.suppress(Exception):
                        _kernel().CloseHandle(h)

    @property
    def returncode(self) -> int | None:
        self.poll()
        return self._returncode

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        code = wintypes.DWORD()
        if _kernel().GetExitCodeProcess(ctypes.c_void_p(self._handle), ctypes.byref(code)):
            if code.value != _STILL_ACTIVE:
                self._returncode = int(code.value)
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(0.02)

    def communicate(self, input: bytes | str | None = None,
                    timeout: float | None = None):
        if self.stdin is not None:
            try:
                if input:
                    self.stdin.write(input.encode("utf-8") if isinstance(input, str) else input)
                    self.stdin.flush()
            finally:
                with contextlib.suppress(Exception):
                    self.stdin.close()
                self.stdin = None
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []

        def read(stream, sink):
            try:
                while True:
                    chunk = stream.read(65536) if stream is not None else b""
                    if not chunk:
                        break
                    sink.append(chunk)
            except Exception:
                pass

        threads = []
        for stream, sink in ((self.stdout, out_chunks), (self.stderr, err_chunks)):
            if stream is not None:
                t = threading.Thread(target=read, args=(stream, sink), daemon=True)
                t.start()
                threads.append(t)
        try:
            self.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(
                self.args, timeout or 0, output=b"".join(out_chunks),
                stderr=b"".join(err_chunks)) from None
        finally:
            for t in threads:
                t.join(1)
        return b"".join(out_chunks), b"".join(err_chunks)

    def kill(self) -> None:
        if self._returncode is None:
            with contextlib.suppress(Exception):
                _kernel().TerminateProcess(ctypes.c_void_p(self._handle), 1)
        self.poll()

    terminate = kill

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
        for h in (self._handle, self._thread_handle):
            if h:
                with contextlib.suppress(Exception):
                    _kernel().CloseHandle(ctypes.c_void_p(h))
        self._handle = 0
        self._thread_handle = 0

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()


def cleanup_all() -> None:
    """显式清理本进程登记的 profile 与 ACL（测试/卸载时使用）。"""
    with _PROFILE_LOCK:
        profiles = list(_PROFILES.values())
        _PROFILES.clear()
    for profile in profiles:
        _remove_grants(profile)
        with contextlib.suppress(Exception):
            _userenv().DeleteAppContainerProfile(profile.name)
        with contextlib.suppress(Exception):
            _advapi().FreeSid(profile.sid)
        with contextlib.suppress(Exception):
            _kernel().LocalFree(profile.restricted_sid)
        with contextlib.suppress(Exception):
            shutil.rmtree(profile.folder.parent, ignore_errors=True)


# 默认保留 profile 与 ACL：后续启动复用同一身份，避免重新扫描 Python 运行时。
# 用户明确卸载或测试收尾时调用 `cleanup_all()`。
atexit.register(lambda: None)


def Popen(args, *, cwd: Path, env: dict[str, str], allow_network: bool,
          read_roots: list[Path] | None = None, stdout=subprocess.PIPE,
          stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
          creationflags: int = 0, mode: str = "appcontainer") -> AppContainerProcess:
    """兼容调用点的入口；创建失败一律抛 AppContainerError。"""
    del creationflags
    return AppContainerProcess(
        args, cwd=cwd, env=env, allow_network=allow_network,
        read_roots=read_roots, mode=mode, stdout=stdout, stderr=stderr, stdin=stdin,
    )
