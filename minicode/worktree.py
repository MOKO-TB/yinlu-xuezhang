"""Git 工作副本(Worktree)隔离 · W1(v0.9.98,docs/roadmap-git-worktree隔离.md)。

解决的是"同一个仓库里同时做两件事,两边互不踩文件":现在一个学长窗口只绑一个
工作区,并行的子代理共享同一个物理目录 —— 是分工,不是隔离。W1 给出的是
「**再开一份工作副本**」:在仓库同级建 `<项目>__<分支>/`,把它当一个普通工作区打开。

三条设计规矩(方案 §三 W1 原文):
1. **只做"问 git 要事实"和"生成一条要用户确认的命令"** —— 本模块绝不自己
   建/删目录:建 worktree 是写操作,必须过 `bash` 那条确认框(复用现有权限链,
   不在这里开第二条路);
2. **展示而非托管**:只读地报有几个副本、各在哪个分支、有没有未提交改动;
   删除这一轮不做(删工作副本 = 删掉别人正在改的东西);
3. **副本就是普通工作区**:沙箱、权限白名单、凭据确认那套原样照用;
   项目级白名单默认不生效要重批 —— 这是特性不是缺陷(隔离出来的目录
   不该继承原目录的授权)。

已知口径(要写进《使用说明》,不能只留在代码里):两个副本**各自一份**长期记忆
(记忆按工作区路径存),但课表/待办/心情是**全局一份** —— 校园数据本来与
代码仓库无关。W3(让子代理跨工作区派活)按方案明确不做。
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path | None = None, timeout: int = 10) -> tuple[bool, str]:
    """跑一条只读的 git 命令;git 不在/出错都按 (False, 原因) 回,绝不抛。"""
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "").strip()[:300]
    return True, r.stdout


def is_git_repo(path: Path) -> bool:
    """path(或其上层)是不是一个 git 工作树。"""
    ok, out = _git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return ok and out.strip() == "true"


def parse_worktree_list(text: str) -> list[dict]:
    """解析 `git worktree list --porcelain` 的输出(纯函数,喂真样例可测)。

    每份 worktree 一个块:worktree <path> / HEAD <sha> / branch <ref>,
    bare 与 detached 两种特殊形状也照实标出来。主 worktree 与副本在
    输出里没有主次之分 —— git 不分,我们也不分。
    """
    out: list[dict] = []
    row: dict = {}
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line:
            if row:
                out.append(row)
                row = {}
            continue
        if line.startswith("worktree "):
            row = {"path": line[len("worktree "):].strip()}
        elif line == "bare":          # 整行属性标记,porcelain 里没有空格
            row["bare"] = True
        elif line == "detached":
            row["detached"] = True
        elif " " in line and row:
            key, _, value = line.partition(" ")
            if key == "HEAD":
                row["head"] = value.strip()
            elif key == "branch":
                row["branch"] = value.strip().removeprefix("refs/heads/")
    if row:
        out.append(row)
    return out


def suggest_copy_path(repo: Path, branch: str) -> Path:
    """副本落点:仓库**同级**的 `<项目>__<分支>/`(方案 §六 第 2 问的拍板)。

    同级而不是数据目录:可见、好找、同学能拿自己的 IDE 直接打开它;
    代价是"容易误删" —— 所以本模块只报不删,删除永远不在程序里做。
    分支名里的 `/`(feature/x)折叠成 `__`(路径里不能有那段目录歧义)。
    """
    safe = str(branch or "").strip().replace("/", "__").replace("\\", "__")
    return Path(repo).resolve().parent / f"{Path(repo).resolve().name}__{safe}"


def build_add_command(repo: Path, branch: str, path: Path,
                      new_branch: bool = True) -> str:
    """生成"要用户确认的那条命令" —— 本模块只生成,不执行。

    执行走 `bash` 工具的确认框(带预览,用户看得见每一段);命令用 `git -C`
    定死仓库,引号包路径(路径里有空格是常态)。
    """
    repo_s = f'"{Path(repo)}"'
    path_s = f'"{Path(path)}"'
    if new_branch:
        return f"git -C {repo_s} worktree add -b {branch} {path_s}"
    return f"git -C {repo_s} worktree add {path_s} {branch}"


def _dirty(repo: Path) -> str:
    """未提交改动的概况:干净给空串;有改动给 "N 个文件";问不出给 "未知"。"""
    ok, out = _git(["status", "--porcelain"], cwd=repo)
    if not ok:
        return "未知"
    lines = [l for l in out.splitlines() if l.strip()]
    return f"{len(lines)} 个文件" if lines else ""


def worktree_facts(path: Path | None = None) -> dict:
    """一件事的现状:这是不是 git 仓库、有哪几份工作副本、各自干净不干净。

    只读:两条 git 命令(worktree list / status)都是查事实,不写任何东西。
    返回的 `worktrees` 每项带 path/branch/head/dirty —— 供工具回话与
    (将来的)面板直接摆;git 不在或不是仓库时如实说明,不猜。
    """
    root = Path(path).resolve() if path else None
    if root is None or not root.is_dir():
        return {"ok": False, "msg": f"目录不存在:{root}"}
    if not is_git_repo(root):
        return {"ok": False, "msg": "当前工作区不是 git 仓库(没有可用的工作副本这回事)"}
    ok, out = _git(["worktree", "list", "--porcelain"], cwd=root)
    if not ok:
        return {"ok": False, "msg": f"git worktree list 读不出来:{out}"}
    rows = parse_worktree_list(out)
    for row in rows:
        row["dirty"] = _dirty(Path(row["path"]))
    return {"ok": True, "repo": str(root), "worktrees": rows}


def suggest(repo: Path, branch: str, exists: bool | None = None) -> dict:
    """给出开副本的**完整建议**:落点、命令、要不要重名处理。

    `exists=None` 表示"分支在不在没查"(调用方没给事实就不假装查过);
    建议里如实写上两种情形的命令差别(-b 建新分支 / 直接检出已有分支)。
    """
    repo = Path(repo).resolve()
    if not branch or not branch.strip():
        return {"ok": False, "msg": "要给一个分支名(比如 login-fix)"}
    branch = branch.strip()
    target = suggest_copy_path(repo, branch)
    if exists is True:
        cmd = build_add_command(repo, branch, target, new_branch=False)
        note = f"分支 {branch} 已存在:直接检出它。"
    else:
        cmd = build_add_command(repo, branch, target, new_branch=True)
        note = "会新建这个分支;分支其实已存在时,把 exists 告诉我,换成直接检出。"
    if target.exists():
        return {"ok": False,
                "msg": f"落点已存在:{target}(换个别名分支,或删掉旧副本后重试 —— 删的事我不替你做)"}
    return {"ok": True, "path": str(target), "command": cmd, "note": note,
            "next": ("这条命令要走 bash 执行(会弹确认框,不会偷偷建目录);"
                     "建好后说一声,我把它登记进工作区清单,你点 📁 工作区就能切过去。")}


def worktree_tool(ctx, action: str = "list", branch: str = "",
                  path: str = "", exists=None) -> str:
    """worktree 工具处理器:只报事实与建议,建副本的命令由 bash 带确认执行。"""
    root = Path(str(path).strip()) if str(path).strip() else \
        getattr(ctx, "workspace", None)
    action = (str(action or "list")).strip().lower()
    if action == "suggest":
        r = suggest(root, str(branch or ""))
        if not r.get("ok"):
            return f"错误: {r['msg']}"
        return (f"落点:{r['path']}\n要执行的命令:{r['command']}\n{r['note']}\n{r['next']}")
    if action != "list":
        return (f"错误: 不认识的 action「{action}」。可用:list(看现状)/"
                f"suggest(算落点与命令)。")
    r = worktree_facts(root)
    if not r.get("ok"):
        return f"错误: {r['msg']}"
    rows = r["worktrees"]
    lines = [f"git 仓库 {r['repo']} 有 {len(rows)} 份工作副本:"]
    for row in rows:
        branch = row.get("branch") or ("(detached)" if row.get("detached") else "(bare)")
        dirty = f" · 未提交:{row['dirty']}" if row.get("dirty") else " · 干净"
        mark = " ←当前工作区" if _same_dir(row["path"], root) else ""
        lines.append(f"  - {row['path']} · {branch}{dirty}{mark}")
    lines.append("要另开一份副本:action=suggest 给 branch 算落点与命令"
                 "(命令走 bash 确认执行);副本开了之后它就是一个普通工作区,"
                 "记忆各一份、课表/待办全局一份,项目白名单要重新批。")
    return "\n".join(lines)


def _same_dir(a, b) -> bool:
    import os
    try:
        return os.path.normcase(os.path.abspath(str(a))) == \
            os.path.normcase(os.path.abspath(str(b)))
    except OSError:
        return False
