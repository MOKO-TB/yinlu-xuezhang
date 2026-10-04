"""技能分享包(v0.9.114,用户点名的新功能③):一个技能连它的资料一起打包给同学。

为什么要这一层:技能是同学之间最愿意互相给的东西(「你那个搜书的技能发我」),
而 `mydata` 那份迁移包按设计只带 `skills/*.md` 那种平铺文件 —— 一个带
`assets/`、`data/` 子目录的技能整包**带不走**(见 mydata 里那条 `kind="family",
pattern="*.md"`)。所以这里做一件窄事:**只装一个技能的那一个目录**,
连它的 `SKILL.md` 与子目录资料,给一份清单,让对方先看再落盘。

三条硬规矩(都是这个仓库一贯的口径,不是新发明):
1. **先看后写**:`preview` 校验并列出会写哪些、哪一条会覆盖本机已有的,一个字都不写;
   `apply` 再校验一遍(nonce 有有效期,防"看一眼之后文件被换掉")。
2. **覆盖之前先备份**:同名技能目录整个挪进 `skill-backup/<时刻>/`,和迁移包同一家族。
3. **路径校验只有一份**:zip 条目的安全判断直接吃 `mydata._key_is_safe` ——
   两处各写一份"什么叫越界",迟早一处漏。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

from minicode.mydata import _key_is_safe
from minicode.paths import app_path
from minicode.skills import app_skills_dir, find_skill, load_skills, skill_dirs

MANIFEST = "skill-share.json"
MAX_FILES = 60
MAX_FILE_BYTES = 2_000_000          # 单个文件上限:技能是文字,不是数据集
MAX_TOTAL_BYTES = 20_000_000        # 整包上限
BACKUP_DIR = "skill-backup"
BACKUP_KEEP = 10
_PREVIEW_TTL = 900                  # 秒:预览过太久之后那份 zip 可能已经不是原来那个

_previews: dict[str, dict] = {}


def _slug(name: str) -> str:
    """目录名:留中文与字母数字,其余变连字符(与 skills.create_skill 同一条口径)。"""
    keep = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", str(name or "").strip())
    return keep.strip("-.")[:60]


def skill_dir_of(workspace, name: str) -> Path | None:
    """这个技能在磁盘上**到底是哪一个目录**。

    找的是目录本体而不是 `find_skill` 的那份解析结果:带子目录资料技能的
    `path` 是 `…/<dir>/SKILL.md`,打包要的是它上一层。平铺的 `<name>.md`
    没有目录可打 —— 那种就按它自己那一个文件打(清单里只有一条)。
    """
    hit = find_skill(load_skills(skill_dirs(workspace)), str(name or "").strip())
    if hit is None:
        return None
    p = Path(hit.path)
    return p.parent if p.name.upper() == "SKILL.MD" else p


def _files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == MANIFEST:
            continue
        rel = p.relative_to(root).as_posix()
        if not _key_is_safe(rel) or any(seg.startswith(".") for seg in rel.split("/")):
            continue                       # 隐藏文件与越界路径都不进包
        out.append(p)
    return out


def pack(workspace, name: str, dest: Path | None = None) -> dict:
    """把一个技能打成 zip 交给同学:返回 {ok, path, files, bytes, msg}。"""
    root = skill_dir_of(workspace, name)
    if root is None:
        return {"ok": False, "msg": f"没有这个技能:{name or '(空)'} —— "
                                    "先点技能面板里那一行,别手输名字。"}
    files = _files(root)
    if not files:
        return {"ok": False, "msg": f"「{name}」这个技能目录里没有一个可打包的文件。"}
    if len(files) > MAX_FILES:
        return {"ok": False, "msg": f"这个技能有 {len(files)} 个文件,超过 {MAX_FILES} 个"
                                    "的不打包(技能是文字与资料,不是数据集)。"}
    total = sum(f.stat().st_size for f in files)
    if total > MAX_TOTAL_BYTES:
        return {"ok": False, "msg": f"这个技能共 {total / 1e6:.1f} MB,超过 "
                                    f"{MAX_TOTAL_BYTES // 1000000} MB 上限,不打包。"}
    if any(f.stat().st_size > MAX_FILE_BYTES for f in files):
        big = [f.name for f in files if f.stat().st_size > MAX_FILE_BYTES][:3]
        return {"ok": False, "msg": f"这些文件单个超过 {MAX_FILE_BYTES // 1000} KB:{big};"
                                    "先把它挪出技能目录再打包。"}
    slug = _slug(name)
    dest = Path(dest) if dest else app_path(f"技能分享包-{slug}.zip")
    manifest = {
        "kind": "yinluxs-skill-pack", "version": 1,
        "name": str(name), "slug": slug,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": [{"path": f.relative_to(root).as_posix(),
                   "bytes": f.stat().st_size,
                   "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                  for f in files],
        "_说明": "这是引路学长的一个技能(含它的资料)。导入前会先给你看会写哪些文件。",
    }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{slug}/{MANIFEST}",
                        json.dumps(manifest, ensure_ascii=False, indent=1))
            for f in files:
                zf.write(f, f"{slug}/{f.relative_to(root).as_posix()}")
        tmp.replace(dest)
    except OSError as exc:
        return {"ok": False, "msg": f"写不出来({type(exc).__name__}),本机技能没动。"}
    return {"ok": True, "path": str(dest), "files": len(files),
            "bytes": dest.stat().st_size,
            "msg": f"已把「{name}」连它的 {len(files)} 个文件打成一份分享包:"
                   f"{dest.name}(发给同学即可,里面没有密钥也没有你的数据)"}


def _entries(src: Path) -> tuple[dict | None, list[tuple[str, Path]], str]:
    """读包:回 (清单, [(包内路径, 落地路径)], 拒绝原因)。什么都不写。"""
    try:
        zf = zipfile.ZipFile(src)
    except (OSError, zipfile.BadZipFile) as exc:
        return None, [], f"这份文件读不出 zip({type(exc).__name__})。"
    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) > MAX_FILES:
            return None, [], f"包里有 {len(names)} 个文件,超过 {MAX_FILES} 个的不收。"
        bad = [n for n in names if not _key_is_safe(n)]
        if bad:
            return None, [], ("包里有越界路径,整份拒收(这是防 zip 路径穿越):"
                              + "、".join(bad[:3]))
        man_name = next((n for n in names
                         if Path(n).name.upper() == MANIFEST.upper()), "")
        if not man_name:
            return None, [], f"包里少了 {MANIFEST},不像引路学长的技能分享包。"
        try:
            man = json.loads(zf.read(man_name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, [], f"{MANIFEST} 读不懂,整份拒收。"
        if not isinstance(man, dict) or man.get("kind") != "yinluxs-skill-pack":
            return None, [], "这份清单不是引路学长的技能分享包(kind 对不上)。"
        top = Path(man_name).parent.as_posix()
        top = "" if top == "." else top
        rows = []
        for n in names:
            if n == man_name:
                continue
            rel = n[len(top) + 1:] if top and n.startswith(top + "/") else Path(n).name
            if not rel or not _key_is_safe(rel):
                return None, [], f"包内路径 {n} 落不到技能目录里,整份拒收。"
            rows.append((rel, Path(n)))
        if not rows:
            return None, [], "除了清单一个文件都没有,没什么可导入。"
        return {"manifest": man, "top": top or _slug(man.get("name", "")),
                "rows": rows, "zf_names": names}, [], ""


def preview(src: str | Path) -> dict:
    """**只校验与列清单,一个字都不写**:会写到哪、会不会覆盖本机已有的。"""
    p = Path(str(src or ""))
    if not p.is_file():
        return {"ok": False, "msg": f"找不到这份文件:{p.name or '(空路径)'}"}
    info, _, why = _entries(p)
    if why:
        return {"ok": False, "msg": why}
    man = info["manifest"]
    target = Path(app_skills_dir()) / info["top"]
    exists = target.exists()
    files = [r[0] for r in info["rows"]]
    nonce = hashlib.sha256(f"{p}|{p.stat().st_size}|{time.time()}".encode()).hexdigest()[:16]
    _previews[nonce] = {"src": str(p), "top": info["top"], "files": files,
                        "rows": [(a, str(b)) for a, b in info["rows"]],
                        "size": p.stat().st_size, "at": time.time()}
    return {"ok": True, "nonce": nonce, "name": str(man.get("name") or info["top"]),
            "files": files, "count": len(files),
            "created": str(man.get("created") or ""),
            "tools": [str(x) for x in (man.get("tools") or [])],
            "libs": [str(x) for x in (man.get("libs") or [])],
            "target": str(target), "conflict": exists,
            "msg": (f"这一包会写成技能「{man.get('name') or info['top']}」"
                    f"(共 {len(files)} 个文件)。"
                    + (f"本机已有同名技能目录,导入前会先备份进 {BACKUP_DIR}/。" if exists
                       else "本机没有同名技能,直接新增。"))}


def _prune_backups(keep: int = BACKUP_KEEP) -> None:
    root = app_path(BACKUP_DIR)
    try:
        kids = sorted((d for d in root.iterdir() if d.is_dir()))
    except OSError:
        return
    for old in kids[:-keep] if keep else []:
        import shutil
        shutil.rmtree(old, ignore_errors=True)


def apply(nonce: str) -> dict:
    """按预览过的那一份落盘:再校验一次、同名先备份、按清单核 sha256。"""
    held = _previews.get(str(nonce or ""))
    if not held:
        return {"ok": False, "msg": "没有这份预览(或已过期)。请先选包、看一眼再导入。"}
    if time.time() - float(held.get("at") or 0) > _PREVIEW_TTL:
        _previews.pop(str(nonce), None)
        return {"ok": False, "msg": "预览过期了(超过 15 分钟),请重新选一次这份包。"}
    src = Path(held["src"])
    if not src.is_file() or src.stat().st_size != held.get("size"):
        _previews.pop(str(nonce), None)
        return {"ok": False, "msg": "这份包在预览之后变了或不见了,没导入任何东西。"}
    info, _, why = _entries(src)
    if why or info["top"] != held["top"]:
        return {"ok": False, "msg": f"再校验没过:{why or '包的内容与预览时对不上'},没写入。"}
    man = info["manifest"]
    want = {row["path"]: row.get("sha256") for row in (man.get("files") or [])
            if isinstance(row, dict)}
    target = Path(app_skills_dir()) / info["top"]
    # 覆盖之前先整目录挪进备份 —— 与迁移包同一家族的做法。同学手改过的技能被一份
    # 别人的包盖掉又找不回来,是那种"再也没法信任导入按钮"的事故。
    moved = stash_existing(str(man.get("name") or info["top"]))
    written, replaced = [], []
    try:
        with zipfile.ZipFile(src) as zf:
            for rel, inner in info["rows"]:
                data = zf.read(inner.as_posix())
                digest = hashlib.sha256(data).hexdigest()
                if want.get(rel) and want[rel] != digest:
                    return {"ok": False,
                            "msg": f"{rel} 与清单上的 sha256 对不上,停在这里没继续写。"}
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.exists():
                    replaced.append(rel)
                out.write_bytes(data)
                written.append(rel)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return {"ok": False, "msg": f"写入失败({type(exc).__name__}),"
                                    "已写的那几个文件留在原地,可再导入一次覆盖。"}
    _previews.pop(str(nonce), None)
    return {"ok": True, "name": str(man.get("name") or info["top"]),
            "written": len(written), "replaced": len(replaced),
            "backup": str(moved) if moved else "",
            "path": str(target),
            "msg": f"已导入技能「{man.get('name') or info['top']}」"
                   f"(写了 {len(written)} 个文件,其中覆盖 {len(replaced)} 个)。"
                   + (f"原来那一份已挪进 {BACKUP_DIR}/ 退得回去。" if moved else "")
                   + "技能列表里立刻能用。"}


def stash_existing(name: str) -> Path | None:
    """覆盖前把本机那一份整目录挪进备份。没有同名就回 None(不建空目录)。"""
    import shutil
    src = Path(app_skills_dir()) / _slug(name)
    if not src.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = app_path(BACKUP_DIR) / f"{_slug(name)}-{stamp}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    _prune_backups()
    return dest
