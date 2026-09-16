"""工作区:Agent「在哪个文件夹里干活」的选择与记忆。

以前工作区是**写死**的:GUI = exe 目录(`paths.app_path()`),CLI = 启动时的
当前目录(`Path.cwd()`)。用户没法把 Agent 指到自己放资料的文件夹,想让它
在别处写文件就只能在那个目录里启动程序。

本模块把这件事做成可持久化的用户设置:

    workspaces.json(跟着 app_path —— 打包后是 exe 旁,开发态是仓库根)
    {"current": "D:\\\\我的资料", "recent": [{"path", "name", "opened_at"}]}

边界(与沙箱同一套口径,不要放宽):
- 敏感目录(`~/.yinluxs` 凭据、`~/.ssh`、系统凭据目录…)永远不能当工作区;
- 只接受**已存在**的文件夹:不做隐式创建,免得在用户盘上凭空长出目录;
- 一律 resolve 后存盘,比较用 `normcase`(Windows 下大小写不敏感)。

设计取舍:状态是**全局一份**(GUI/CLI/web 共用),所以「上次在哪干活」这件事
跨形态一致;`--workspace` 之类显式指定始终优先于记忆值。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from minicode import paths
from minicode.sandbox import sensitive_roots

STATE_FILE = "workspaces.json"
MAX_RECENT = 8
DEFAULT_LABEL = "默认工作区(程序目录)"


# --------------------------------------------------------------------- 基础

def _resolve(p: Path) -> Path:
    try:
        return Path(p).expanduser().resolve()
    except OSError:                     # 盘符不存在/权限异常时退化为绝对路径
        return Path(p).expanduser().absolute()


def state_path(app_dir=None, state_file=None) -> Path:
    if state_file is not None:
        return Path(state_file)
    return Path(app_dir if app_dir is not None else paths.APP_DIR) / STATE_FILE


def default_workspace(app_dir=None) -> Path:
    """默认工作区 = 程序目录,也就是改造前写死的那个。"""
    return _resolve(Path(app_dir if app_dir is not None else paths.APP_DIR))


def key(p) -> str:
    """比较用键:resolve + normcase(Windows 大小写不敏感)。"""
    return os.path.normcase(str(_resolve(Path(p))))


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_target(raw, app_dir=None, home=None) -> tuple[Path | None, str]:
    """把用户输入/文件夹对话框的返回值校验成可用工作区。

    返回 `(路径, 说明)`:路径为 None 时,说明就是给用户看的失败原因。
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return None, "没有选择文件夹"
    p = Path(text)
    if not p.is_absolute():
        return None, f"请给完整路径(这是相对路径):{text}"
    target = _resolve(p)
    for root in sensitive_roots(home):
        if target == root or _under(target, root):
            return None, f"「{target}」是存放凭据/密钥的敏感目录,不能当工作区"
    if not target.exists():
        return None, f"文件夹不存在:{target}"
    if not target.is_dir():
        return None, f"这不是文件夹:{target}"
    return target, ""


def is_default(p, app_dir=None) -> bool:
    return key(p) == key(default_workspace(app_dir))


def display_name(p, app_dir=None) -> str:
    """界面上显示的名字:默认工作区给固定叫法,其余用文件夹名。"""
    if is_default(p, app_dir):
        return DEFAULT_LABEL
    name = Path(p).name
    return name or str(p)


def entry(p, app_dir=None) -> dict:
    path = _resolve(Path(p))
    return {"path": str(path), "name": display_name(path, app_dir),
            "is_default": is_default(path, app_dir)}


# --------------------------------------------------------------------- 状态

def load_state(state_file=None, app_dir=None) -> dict:
    """读状态;文件缺失/损坏一律退回空状态,绝不抛异常拦启动。"""
    path = state_path(app_dir, state_file)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"current": "", "recent": []}
    if not isinstance(raw, dict):
        return {"current": "", "recent": []}

    recent: list[dict] = []
    for item in raw.get("recent") or []:
        if not isinstance(item, dict):
            continue
        p = str(item.get("path") or "").strip()
        if not p:
            continue
        try:
            opened = float(item.get("opened_at") or 0)
        except (TypeError, ValueError):
            opened = 0.0
        recent.append({"path": p, "name": str(item.get("name") or ""),
                       "opened_at": opened})
        if len(recent) >= MAX_RECENT:
            break
    return {"current": str(raw.get("current") or "").strip(),
            "current_name": str(raw.get("current_name") or ""),
            "recent": recent}


def save_state(state: dict, state_file=None, app_dir=None) -> Path:
    """原子落盘(临时文件 + replace);写不进去只当没有,不影响本次使用。"""
    path = state_path(app_dir, state_file)
    data = {"current": str((state or {}).get("current") or ""),
            "current_name": str((state or {}).get("current_name") or ""),
            "recent": list((state or {}).get("recent") or [])[:MAX_RECENT]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return path


def remember(p, state_file=None, app_dir=None) -> dict:
    """把某个工作区记为「当前」并置顶最近列表。"""
    path = _resolve(Path(p))
    state = load_state(state_file, app_dir)
    recent = [{"path": str(path), "name": display_name(path, app_dir),
               "opened_at": time.time()}]
    for item in state.get("recent") or []:
        if key(item["path"]) == key(path):
            continue
        recent.append(item)
    state = {"current": str(path), "recent": recent[:MAX_RECENT]}
    save_state(state, state_file, app_dir)
    return state


def forget(p, state_file=None, app_dir=None) -> dict:
    """把某个工作区移出最近列表(只动记录,不碰磁盘上的文件夹)。"""
    path = _resolve(Path(p))
    state = load_state(state_file, app_dir)
    recent = [i for i in (state.get("recent") or [])
              if key(i["path"]) != key(path)]
    state = {"current": str(state.get("current") or ""), "recent": recent}
    save_state(state, state_file, app_dir)
    return state


def rename(p, name, state_file=None, app_dir=None) -> str:
    """给项目改备注名(只写回 workspaces.json,磁盘上的文件夹不动)。

    返回最终生效的名字:空串 = 恢复默认显示名(文件夹名/默认工作区叫法)。
    """
    path = _resolve(Path(p))
    clean = str(name or "").strip()[:60]
    state = load_state(state_file, app_dir)
    if key(path) == key(state.get("current") or ""):
        state["current_name"] = clean          # 当前项的备注单独存
    else:
        for item in state.get("recent") or []:
            if key(item.get("path") or "") == key(path):
                item["name"] = clean
    save_state(state, state_file, app_dir)
    return clean


def display_name_of(p, state_file=None, app_dir=None) -> str:
    """面板显示名:备注名优先,没有才用文件夹名/默认工作区固定叫法。"""
    path = _resolve(Path(p))
    state = load_state(state_file, app_dir)
    if key(path) == key(state.get("current") or ""):
        custom = str(state.get("current_name") or "").strip()
        if custom:
            return custom
    for item in state.get("recent") or []:
        if key(item.get("path") or "") == key(path):
            custom = str(item.get("name") or "").strip()
            if custom:
                return custom
    return display_name(path, app_dir)


FILE_LIST_LIMIT = 400          # 单目录条目上限:面板是预览,不是资源管理器


def list_files(root, subdir="", limit: int = FILE_LIST_LIMIT, home=None) -> dict:
    """列出一个工作区(或其子目录)的直接子项,给项目面板「查看文件」用。

    只读、有上限、防穿越:`subdir` 里带 ``..`` 或绝对路径直接拒绝;
    敏感目录清单(sandbox 同一套)整个拒绝 —— 面板入口不该能翻到凭据。
    返回 {ok, path, parent, items:[{name,is_dir,size,mtime}]} 或 {ok:False,msg}。
    """
    base = _resolve(Path(root))
    for r in sensitive_roots(home):
        if base == r or _under(base, r):
            return {"ok": False, "msg": f"「{base}」是敏感目录,不能浏览"}
    if not base.is_dir():
        return {"ok": False, "msg": f"文件夹不存在:{base}"}
    rel = str(subdir or "").replace("\\", "/").strip("/")
    if rel in (".", ".."):                 # ".." 不作数:上一级由前端用 rel 算
        return {"ok": False, "msg": "非法路径"}
    if rel:
        if rel.startswith("..") or "/../" in f"/{rel}/" or Path(rel).is_absolute():
            return {"ok": False, "msg": "非法路径"}
    target = base / rel if rel else base
    try:
        target = target.resolve()
    except OSError:
        return {"ok": False, "msg": "路径无法解析"}
    if not _under(target, base):
        return {"ok": False, "msg": "非法路径"}
    try:
        children = sorted(target.iterdir(),
                          key=lambda c: (not c.is_dir(), c.name.lower()))[:limit]
    except OSError as e:
        return {"ok": False, "msg": f"读取失败: {e}"}
    items = []
    for c in children:
        try:
            st = c.stat()
            items.append({"name": c.name, "is_dir": c.is_dir(),
                          "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            items.append({"name": c.name, "is_dir": c.is_dir(),
                          "size": 0, "mtime": 0})
    # rel = 当前列出目录相对项目根的路径(""=根;"sub"、"sub/deep"=子目录),
    # 上一级和面包屑由前端拿它自己算
    return {"ok": True, "path": str(target), "rel": rel,
            "items": items}


def recent_entries(state=None, state_file=None, app_dir=None) -> list[dict]:
    """最近列表(只保留还存在的目录,并标出哪一个是默认工作区)。"""
    state = state if state is not None else load_state(state_file, app_dir)
    out: list[dict] = []
    for item in state.get("recent") or []:
        p = Path(item["path"])
        if not p.is_dir():
            continue
        e = entry(p, app_dir)
        e["opened_at"] = item.get("opened_at") or 0
        out.append(e)
    return out


def startup_workspace(fallback, app_dir=None, state_file=None, prefer=None) -> Path:
    """启动时用哪个工作区:显式指定 > 记忆里的当前 > fallback。

    记忆里的路径失效(文件夹被删/改名/没权限)时静默回退,绝不拦启动。
    """
    for cand in (prefer, load_state(state_file, app_dir).get("current")):
        if not cand:
            continue
        target, _err = resolve_target(str(cand), app_dir=app_dir)
        if target is not None:
            return target
    return _resolve(Path(fallback))
