r"""侧边栏「Git」页的后端(v0.9.159):状态 / 差异 / 历史 / 暂存 / 提交 / 影子快照。

侧边栏底座的第一个真页面。界面上看得见的 Git 事实此前只有「任务监控」环境段里
那一行(envinfo.py:分支与 ±行数)—— 那是「现在仓库什么状态」的一句话,不是
「我想看看改了什么、把哪几个留下」的工作台。这一模块把工作台补上,但它**刻意
只是 git CLI 的一层薄皮**,四条铁律一条都不让:

- **git 只调 CLI,绝不设置身份**。不写 user.name/user.email,也不带
  `-c user.name=…` 这类一次性注入 —— 没配身份的仓库让它报错,把 git 的原话
  摆给用户,而不是替用户编一个身份提交(那份提交永远洗不掉)。
- **绝不 push / pull / fetch**。网络动作不归这一页管:推错一次远端,本页
  界面上任何一颗按钮都救不回来。DSH-better-sidebar 的「已知限制」同样明写
  「Git 无 push/pull/fetch」—— 两个项目在这条上意见一致。
- **路径全部相对工作区,逐个过围栏**。前端传来的 path 要过 `_safe_rel`:
  绝对路径、盘符、`..`、`.git` 内部一律拒。git 命令本身也吃 `--` 分隔,
  选项与路径永不混排。
- **写档的三颗按钮(暂存/取消暂存/提交)由前端先确认**;只读的三个
  (状态/差异/历史)不问 —— 问的次数多了,确认框就成了橡皮图章。

超时一律 20 秒:状态/差异/历史是本地命令,大仓库也就几秒;卡 20 秒的
只有一种情况 —— 某个 hook 在等人输入,那本就该报错而不是挂死界面。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from minicode.shadow_git import git_bin, restore_only, snapshots

TIMEOUT = 20.0
DIFF_MAX_CHARS = 200_000        # 单文件差异的上限:再大就不是"看差异",是灌屏
LOG_LIMIT = 50

_creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run(workspace: Path, args: list[str], timeout: float = TIMEOUT) -> tuple[int, str]:
    """跑一条 git 命令,回 (退出码, 合并后的输出)。git 不在就如实说。"""
    git = git_bin()
    if not git:
        return -1, "没找到 git(装一个,或把 git 加进 PATH)"
    try:
        p = subprocess.run(
            [git, *args], cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=_creationflags)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        return p.returncode, (out + ("\n" + err if err and out else err))
    except subprocess.TimeoutExpired:
        return -1, f"git 命令超时(>{timeout:.0f}s):可能有 hook 在等人输入"
    except OSError as e:
        return -1, f"git 启动失败:{type(e).__name__}: {e}"


def _safe_rel(workspace: Path, raw: str) -> str | None:
    """把前端传来的路径验成「工作区内、相对、不指 .git」的一条;不行回 None。

    前端给的一律是 status 面板里列过的相对路径,但这一层**不信任来源**:
    面板是展示入口,不该因为路径没验干净变成任意文件的操作器。
    """
    text = str(raw or "").strip().replace("\\", "/").strip("/")
    if not text or text.startswith(("../", "~/")) or ":/" in text or ":" in text.split("/")[0]:
        return None
    parts = text.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if parts[0] == ".git":
        return None
    try:
        resolved = (Path(workspace) / text).resolve()
        resolved.relative_to(Path(workspace).resolve())
    except (ValueError, OSError):
        return None
    return text


def available() -> bool:
    """这台机器能不能跑 git(不能的话整页显示一句怎么装,而不是一堆报错)。"""
    return bool(git_bin())


def status(workspace: Path) -> dict:
    """分支 + 变更行清单。porcelain v1 的人话翻译在 `_status_word`。"""
    code, out = _run(workspace, ["status", "--porcelain=v1", "-b", "--no-renames"])
    if code != 0:
        return {"ok": False, "msg": out or "git status 失败"}
    branch, upstream, ahead, behind = "", "", 0, 0
    rows: list[dict] = []
    for line in out.splitlines():
        if line.startswith("## "):
            head = line[3:]
            if "..." in head:
                branch, rest = head.split("...", 1)
                tail = rest.split(" ", 1)
                upstream = tail[0].split(" ")[0]
                info = tail[1] if len(tail) > 1 else ""
                m = info
                ahead = _int_after(m, "ahead ")
                behind = _int_after(m, "behind ")
            else:
                branch = head.split(" ")[0]
            continue
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:].strip()
        rows.append({"path": path, "x": x, "y": y,
                     "staged": x not in (" ", "?"),
                     "word": _status_word(x, y)})
    return {"ok": True, "branch": branch, "upstream": upstream,
            "ahead": ahead, "behind": behind, "rows": rows}


def _int_after(text: str, key: str) -> int:
    i = text.find(key)
    if i < 0:
        return 0
    digits = ""
    for ch in text[i + len(key):]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


_STATUS = {
    "A": "新文件", "M": "修改", "D": "删除", "R": "重命名",
    "C": "复制", "U": "冲突", "T": "类型变更",
}


def _status_word(x: str, y: str) -> str:
    if x == "?" and y == "?":
        return "未跟踪"
    if x == "!":
        return "已忽略"
    a = _STATUS.get(x, "")
    b = _STATUS.get(y, "")
    if a and b:
        return f"{a}·未暂存的{b.lower()}" if x == " " else f"{a}·{b}"
    return a or b or "变更"


def diff(workspace: Path, path: str, staged: bool = False) -> dict:
    """一个文件的差异文本。未跟踪的新文件没有"底版",明说,不假装。"""
    rel = _safe_rel(workspace, path)
    if rel is None:
        return {"ok": False, "msg": "路径不合法(要相对工作区,且不指 .git)"}
    args = ["diff", "--no-color", "--no-renames", "--text"]
    args += ["--cached"] if staged else []
    args += ["--", rel]
    code, out = _run(workspace, args)
    if code != 0:
        return {"ok": False, "msg": out or "git diff 失败"}
    if not out.strip():
        # 未跟踪的新文件:git diff 永远是空的。如实说,别让用户以为"没差异=没改"。
        st = status(workspace)
        if st.get("ok"):
            for r in st.get("rows", []):
                if r["path"] == rel and r["x"] == "?":
                    return {"ok": True, "text": "",
                            "note": "未跟踪的新文件:还没有可对比的底版,暂存后即可看完整内容。"}
        return {"ok": True, "text": "", "note": "没有差异(这一侧没有改动)。"}
    if len(out) > DIFF_MAX_CHARS:
        out = out[:DIFF_MAX_CHARS] + f"\n…(差异太长,只显示前 {DIFF_MAX_CHARS} 字符)"
    return {"ok": True, "text": out, "note": ""}


def log(workspace: Path, limit: int = 30) -> dict:
    """最近 N 条提交。标签(描述/HEAD)也在,首列是短 sha。"""
    n = max(1, min(int(limit or 30), LOG_LIMIT))
    code, out = _run(workspace, [
        "log", f"-n{n}", "--no-color", "--date=short",
        "--pretty=format:%h%x09%ad%x09%s%x09%D"])
    if code != 0:
        msg = out or ""
        if "does not have any commits yet" in msg or not msg:
            return {"ok": True, "rows": [], "msg": "这个仓库还没有任何提交"}
        return {"ok": False, "msg": msg}
    rows = []
    for line in out.splitlines():
        parts = (line.split("\t") + ["", "", ""])[:4]
        sha, date, subject, refs = parts
        rows.append({"sha": sha, "date": date, "subject": subject,
                     "head": bool(refs.strip())})
    return {"ok": True, "rows": rows, "msg": ""}


def stage(workspace: Path, paths: list[str]) -> dict:
    """把文件放进暂存区。`git add --` 逐个来:一个路径不合法,整批都值得停。"""
    rels = []
    for p in paths or []:
        rel = _safe_rel(workspace, p)
        if rel is None:
            return {"ok": False, "msg": f"路径不合法,一个都没动:{p!r}"}
        rels.append(rel)
    if not rels:
        return {"ok": False, "msg": "没有要暂存的文件"}
    code, out = _run(workspace, ["add", "--", *rels])
    return {"ok": code == 0, "msg": "" if code == 0 else (out or "git add 失败")}


def unstage(workspace: Path, paths: list[str]) -> dict:
    """把文件退回未暂存(新加的文件也会退回"未跟踪",不碰工作区内容)。"""
    rels = []
    for p in paths or []:
        rel = _safe_rel(workspace, p)
        if rel is None:
            return {"ok": False, "msg": f"路径不合法,一个都没动:{p!r}"}
        rels.append(rel)
    if not rels:
        return {"ok": False, "msg": "没有要退回的文件"}
    code, out = _run(workspace, ["reset", "-q", "HEAD", "--", *rels])
    if code != 0 and "ambiguous argument 'HEAD'" in out:
        # 还没有任何提交的仓库:HEAD 不存在,但索引照样能清(git reset 不带树也行)
        code, out = _run(workspace, ["reset", "-q", "--", *rels])
    return {"ok": code == 0, "msg": "" if code == 0 else (out or "git reset 失败")}


def commit(workspace: Path, message: str) -> dict:
    """提交暂存区里的东西。**只提交已暂存的**,不带 -A —— 半截工作不该被顺手全收。"""
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "msg": "提交说明不能为空"}
    if len(text) > 2000:
        return {"ok": False, "msg": "提交说明太长(>2000 字符),先收一收"}
    code, out = _run(workspace, ["commit", "-m", text], timeout=60.0)
    if code != 0:
        # 身份没配是这里最常见的失败:把 git 的原话给用户,不替他编身份
        return {"ok": False, "msg": out or "git commit 失败"}
    return {"ok": True, "msg": out}


def restore(workspace: Path, path: str) -> dict:
    """把一个**已跟踪**文件的工作区改动退回(未跟踪的没有底版,拒绝)。"""
    rel = _safe_rel(workspace, path)
    if rel is None:
        return {"ok": False, "msg": "路径不合法"}
    st = status(workspace)
    if st.get("ok"):
        for r in st.get("rows", []):
            if r["path"] == rel and r["x"] == "?" and r["y"] == "?":
                return {"ok": False,
                        "msg": "未跟踪的新文件没有可退回的底版(删掉它请走文件管理,这一页不代删)"}
    code, out = _run(workspace, ["checkout", "--", rel])
    return {"ok": code == 0, "msg": "" if code == 0 else (out or "git checkout 失败")}


def snapshot_list(workspace: Path, limit: int = 12) -> list[dict]:
    """影子快照(v0.9.157 的账)在这一页的入口:列表是只读的。"""
    try:
        return snapshots(Path(workspace), limit=limit)
    except Exception:
        return []


def snapshot_restore(workspace: Path, sha: str) -> dict:
    """还原一份影子快照。语义沿用 v0.9.157:只还原、绝不删 —— 由库自己把守。"""
    text = str(sha or "").strip()
    if not text or len(text) > 64 or not all(c in "0123456789abcdef" for c in text.lower()):
        return {"ok": False, "msg": "快照 sha 不合法"}
    n, out = restore_only(Path(workspace), text.lower())
    if n <= 0:
        return {"ok": False, "msg": out or "还原失败"}
    return {"ok": True, "msg": f"已还原 {n} 个文件" + (f":{out}" if out else "")}
