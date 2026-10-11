r"""侧边栏「文件」页的后端(v0.9.159):围栏内的读与写。

项目面板那个「查看文件」(list_project_files)只列目录;这一页要真能打开文件
看内容、改内容、存回去 —— 于是就有了这个项目里最需要克制的两个函数。

四条边界,每条都是这个项目已经立过的规矩,这里只是照办:

- **工作区围栏**:`classify_path` / `classify_for_write` 同一套判定 ——
  敏感目录(BLOCKED)一律拒,凭据类文件(SECRET)不进这页(只读也要确认的
  东西,就不该给一个"顺手就能编辑"的入口),工作区外(OUTSIDE)拒。
- **二进制不装懂**:内容里有 NUL 字节就不是文本,硬解出来是乱码,存回去
  就是毁文件。如实回 `binary=True`,前端显示"这一页预览不了"。
- **保存前对底账**:写回时带打开那一刻算的 sha1 —— 磁盘上已经是别的字节
  (别的编辑器、别的程序改过)就拒绝,提示重新载入,**绝不静默覆盖**。
  这是 DSH-better-sidebar「已知限制」里用血换来的那条:磁盘被其他来源
  改动后再保存会被拒绝,不会静默覆盖对方字节。
- **写入走原子替换**(tmp + os.replace):半截写入比拒绝写更糟。

大小上限:预览不是 IDE,2MB 的文本在浏览器里滚都滚不动;再大的文件
该用真正的编辑器(reveal_file 那条路还在)。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from minicode.sandbox import (BLOCKED, OUTSIDE, SECRET, classify_for_write,
                              classify_path)

READ_MAX_BYTES = 2 * 1024 * 1024
WRITE_MAX_BYTES = 2 * 1024 * 1024
_SNIFF = 8192


def _fence(workspace: Path, raw: str, *, write: bool) -> tuple[str | None, Path]:
    """过围栏,回 (挡下来的话, 绝对路径);放行时第一项是 None。"""
    ws = Path(workspace).resolve()
    kind, p = (classify_for_write if write else classify_path)(ws, raw)
    if kind == BLOCKED:
        return "敏感目录里的文件不进这一页", p
    if kind == SECRET:
        return "凭据类文件不进这一页(查看/编辑都请用专门的工具,并当面确认)", p
    if kind == OUTSIDE:
        return "只认工作区里的文件(要动外面的文件,请在对话里让学长走确认流程)", p
    if p.is_dir():
        return "这是一个目录,不是文件", p
    return None, p


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def read(workspace: Path, raw: str, home: Path | None = None) -> dict:
    """读一个工作区内的文本文件。二进制、超大、围栏外,各有各的实话。"""
    ws = Path(workspace)
    blocked, p = _fence(ws, raw, write=False)
    if blocked:
        return {"ok": False, "msg": blocked}
    if not p.exists():
        return {"ok": False, "msg": "文件不存在(可能刚被删掉)"}
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "msg": f"读不到文件信息:{type(e).__name__}: {e}"}
    if size > READ_MAX_BYTES:
        return {"ok": False, "msg": f"文件太大({size:,} 字节,上限 {READ_MAX_BYTES:,})"
                                     "—— 大文件请用系统编辑器打开(在对话里说一声就行)"}
    try:
        data = p.read_bytes()
    except OSError as e:
        return {"ok": False, "msg": f"读不出来:{type(e).__name__}: {e}"}
    if b"\x00" in data[:_SNIFF]:
        return {"ok": False, "binary": True,
                "msg": "二进制文件,这一页预览不了(图片/表格/可执行都算)"}
    text = data.decode("utf-8", errors="replace")
    return {"ok": True, "text": text, "sha1": _sha1(data), "size": size,
            "mtime": p.stat().st_mtime, "path": str(p)}


def write(workspace: Path, raw: str, content: str, base_sha: str = "",
          home: Path | None = None) -> dict:
    """写回一个工作区内的文本文件;磁盘上的字节和打开时对不上就拒。

    `base_sha` 是前端打开文件那一刻 `read()` 给的账。文件本来不存在
    (新建)时 `base_sha` 留空,落盘前再确认一次它**还是**不存在 ——
    两个窗口同时"新建同一个文件"时,后存的那次必须退回来。
    """
    ws = Path(workspace)
    blocked, p = _fence(ws, raw, write=True)
    if blocked:
        return {"ok": False, "msg": blocked}
    data = content.encode("utf-8", errors="surrogatepass")
    if len(data) > WRITE_MAX_BYTES:
        return {"ok": False, "msg": f"内容太大({len(data):,} 字节,上限 {WRITE_MAX_BYTES:,})"}
    existed = p.exists()
    try:
        current = _sha1(p.read_bytes()) if existed else ""
    except OSError as e:
        return {"ok": False, "msg": f"对不了底账(读不动旧内容):{type(e).__name__}: {e}"}
    if existed:
        if not base_sha:
            return {"ok": False, "conflict": True,
                    "msg": "磁盘上已经有这个文件,而这一次编辑不是从它打开的 —— 请先重新载入"}
        if base_sha != current:
            return {"ok": False, "conflict": True,
                    "msg": "磁盘上的内容已经被别的来源改过(别的编辑器/程序)。"
                           "这次保存被拒绝,不会静默覆盖 —— 请重新载入后再改"}
    else:
        if base_sha:
            return {"ok": False, "conflict": True,
                    "msg": "打开时它还在,现在没了 —— 请先重新载入确认,再决定要不要新建"}
        parent = p.parent
        try:
            parent.relative_to(ws.resolve())
        except ValueError:
            return {"ok": False, "msg": "新建文件的父目录不在工作区里"}
    tmp = None
    try:
        parent = p.parent
        if not existed:
            parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=str(parent),
                                         prefix=".fp-", suffix=".tmp",
                                         delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
        os.replace(tmp, p)
    except OSError as e:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": False, "msg": f"写不进去:{type(e).__name__}: {e}"}
    return {"ok": True, "sha1": _sha1(data), "size": len(data),
            "path": str(p), "created": not existed}
