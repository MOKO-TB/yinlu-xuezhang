r"""侧边栏三方扩展的发现(v0.9.159):底座是开放的,分发只认一条路。

**信任模型先说清**(SECURITY.md 同款口径):侧边栏页面的注册 API
(`window.MiniCodePages.register`)对**内置页与扩展页是同一个口**,而扩展
只从**数据目录的 `extensions/<名字>/`** 读 —— 放进去 = 用户(或安装器)
亲手动作,与"装了一个软件"同一级信任。工作区里的 `extensions/`
**一个字都不读**:hooks 那条先例(v0.9.x)已经立过 —— 仓库带来的可执行
代码是注入的直达路,clone 一个坏仓库不该等于交出界面。

manifest.json 的形状(缺了哪样就整个目录不出现,绝不猜):
    {"id": "dbuddy", "title": "数据库", "entry": "page.js",
     "version": "0.1.0", "description": "一眼能看懂的一句话"}

- `id`:小写字母数字与 -_,2~32 位 —— 它同时是页面 id 的前缀空间
  (扩展页自己注册时请用 `dbuddy:tables` 这类带前缀的 id,撞上内置页
  会在 register 时被拒)。
- `entry`:相对本目录的 .js 文件;前端把它作为 <script src> 装进来,
  装进来之前后端验过:不越目录、不指别处、真存在。
- 本模块**只发现、不执行**:执行在前端(装进页面上下文的那一步)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
MAX_TITLE = 40
MAX_DESC = 200


def extension_root() -> Path:
    """扩展的家:数据目录下的 extensions/。个人数据,进 mydata 的边界账。"""
    from minicode.paths import app_path
    return app_path("extensions")


def discover(root: Path | None = None) -> dict:
    """扫出所有合法扩展。返回 {"ok", "items", "skipped"} —— skipped 里
    每一条都是"哪个目录因为什么没算数",装了没生效的人得有地方查原因。"""
    base = Path(root) if root is not None else extension_root()
    items: list[dict] = []
    skipped: list[dict] = []
    if not base.is_dir():
        return {"ok": True, "items": [], "skipped": [], "root": str(base)}
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        why = _validate(d)
        if isinstance(why, str):
            skipped.append({"dir": d.name, "msg": why})
            continue
        items.append(why)
    return {"ok": True, "items": items, "skipped": skipped, "root": str(base)}


def _validate(d: Path) -> str | dict:
    """合法回条目 dict,不合法回一句人话。"""
    if not ID_RE.match(d.name):
        return "目录名不合规(要 2~32 位小写字母/数字/-_)"
    mf = d / "manifest.json"
    if not mf.is_file():
        return "缺 manifest.json"
    try:
        raw = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"manifest.json 读不出来:{type(e).__name__}"
    if not isinstance(raw, dict):
        return "manifest.json 顶层不是对象"
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > MAX_TITLE:
        return f"title 缺失或超过 {MAX_TITLE} 字"
    entry = str(raw.get("entry") or "page.js").strip().replace("\\", "/")
    if entry.startswith("/") or ".." in entry.split("/"):
        return "entry 不能越出扩展自己的目录"
    if not entry.endswith(".js"):
        return "entry 必须是 .js 文件"
    target = (d / entry).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return "entry 越出了扩展自己的目录"
    if not target.is_file():
        return f"entry 指的文件不存在:{entry}"
    desc = str(raw.get("description") or "").strip()
    return {"id": d.name, "title": title,
            "version": str(raw.get("version") or "").strip()[:20],
            "description": desc[:MAX_DESC],
            "entry_path": str(target)}
