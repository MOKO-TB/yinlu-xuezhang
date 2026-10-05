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
import sys
from pathlib import Path

# Windows 下起子进程要挂 CREATE_NO_WINDOW:桌面版是 **GUI 子系统**进程(没有控制台),
# 而 git.exe 是控制台程序 —— 不挂这个标志就等于请系统替它新建一个控制台。
# 用户看图 p7 那条「打开监控栏就弹 git.exe 应用程序无法正常启动(0xc0000142)」正是
# 这一类:弹窗是**模态**的,点掉之前整个窗口卡住,而它每次开监控都会再来一遍。
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# 0xc0000142 = STATUS_DLL_INIT_FAILED。子进程压根没起来时,有人把它当退出码回,
# 有人直接抛 OSError —— 两种都要认,见 _git 里那两处判断。
_BROKEN_CODES = {-1073741502, 3221225794}

# 熔断:本机 git 一旦「起不来」(不是「这条命令失败」),这一程就不再试第二遍。
# 表现不能是"每开一次监控弹一次系统错误框",也不能是"每开一次都白等几百毫秒"。
_git_down = ""


def git_unavailable() -> str:
    """熔断之后给界面看的那句话;空串 = 没熔断过,git 还能问。"""
    return _git_down


def _reset_git_breaker() -> None:
    """测试用:把熔断复位(一程之内只熔断一次是设计,但测试要能重来)。"""
    global _git_down
    _git_down = ""


def _git(args: list[str], cwd: Path | None = None, timeout: int = 10) -> tuple[bool, str]:
    """跑一条只读的 git 命令;git 不在/出错都按 (False, 原因) 回,绝不抛。"""
    global _git_down
    if _git_down:
        return False, f"本机 git 起不来({_git_down}),已停止再试"
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        # 超时不是"git 坏了"(大仓库上 git 就是慢),那条不熔断;
        # OSError 才是压根起不来 —— 熔断,免得下一次又弹一个模态框。
        if isinstance(e, OSError):
            _git_down = f"{type(e).__name__}: {e}"
        return False, f"{type(e).__name__}: {e}"
    if r.returncode in _BROKEN_CODES:
        _git_down = f"git.exe 启动失败(0x{(r.returncode) & 0xFFFFFFFF:08X})"
        return False, _git_down
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "").strip()[:300]
    return True, r.stdout


def find_repo_root(path: Path) -> Path | None:
    """**不起进程**地往上看有没有 `.git`(目录或 worktree 的指针文件)。

    存在的意义就是那条熔断之外的第二层省:大多数工作区压根不是仓库,
    为它们起一次 git 子进程只是白等 + 白给系统一次弹窗的机会。
    """
    here = Path(path).expanduser()
    try:
        here = here.resolve()
    except OSError:
        return None
    for cand in (here, *here.parents):
        if (cand / ".git").exists():
            return cand
    return None


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
    if find_repo_root(root) is None:
        return {"ok": False, "msg": "当前工作区不是 git 仓库(往上没找到 .git)"}
    if not is_git_repo(root):
        return {"ok": False, "msg": "当前工作区不是 git 仓库(没有可用的工作副本这回事)"}
    ok, out = _git(["worktree", "list", "--porcelain"], cwd=root)
    if not ok:
        return {"ok": False, "msg": f"git worktree list 读不出来:{out}"}
    rows = parse_worktree_list(out)
    for row in rows:
        row["dirty"] = _dirty(Path(row["path"]))
    return {"ok": True, "repo": str(root), "worktrees": rows}


# ---------------------------------------------------- 相对 HEAD 的改动清单
#
# 为什么单列这一档:「本轮改动」那份账只认 write_file / edit_file 的快照,bash、
# py_script、编辑器自己保存出来的东西从来不在里面 —— 而那句「退不掉」原先只能靠承认。
# git 看得见这些文件,所以这里把**看得见的部分**补上,同时把**仍然退不掉**写在回话里:
# 它不是审查视图的第二份真相,是那份账外面的一整片黑。
CHANGE_ROW_LIMIT = 30          # 一屏列几个文件(与看板 ROW_LIMIT 同一类节制)
BRANCH_COMMIT_LIMIT = 10       # 分支审查的提交清单列几条(另有 N 条没列)
BRANCH_DIFF_LIMIT = 400        # 分支审查单文件 diff 最多回几行(超了明说截断)
# action → 一句人话。错误提示与工具描述都从这份表来(加一类时只改这里与参数 enum)。
ACTIONS = {"list": "看有哪几份副本、各自干净不干净", "suggest": "算新开副本的落点与命令",
           "changes": "列相对 HEAD 改了哪些文件",
           "branch": "审查一个分支相对分叉点的改动"}


_STATUS_LABELS = {"?": "未跟踪", "A": "新增", "M": "改", "D": "删除",
                  "R": "改名", "C": "复制", "U": "冲突未解决", "T": "类型变了"}


def parse_status_porcelain(text: str) -> list[dict]:
    """解析 `git status --porcelain`(每行 `{XY} 路径`)→ [{path, state, staged}]。

    X 是索引侧(已暂存)、Y 是工作区侧;两边都动过的(`MM`)只算一条并标出已暂存 ——
    这里要的是"哪些文件与 HEAD 不一样",不是重演 git 的状态机。
    改名行形如 `R  旧 -> 新`,取新名:人要看的是现在的名字。
    """
    out: list[dict] = []
    for line in (text or "").splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        body = line[3:]
        path = body.split(" -> ")[-1] if "->" in body else body
        path = path.strip().strip('"')
        # 未跟踪是 XY 两位都是 `?`,所以单独判一次 —— 按位查表会把它认成"改"
        state = ("未跟踪" if (x == "?" and y == "?")
                 else _STATUS_LABELS.get(y) or _STATUS_LABELS.get(x)
                 or ("改" if (x != " " or y != " ") else "不明"))
        out.append({"path": path, "state": state, "staged": x not in " ?"})
    return out


def parse_numstat(text: str) -> dict:
    """`git diff HEAD --numstat` → {路径: {added, removed, binary}}。

    二进制文件 git 给的是 `-`:这里如实留 None 并打 binary 标 ——
    补一个 0 等于宣称"它没改几行",那是当着面编数。
    """
    rows: dict[str, dict] = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[-1].strip().strip('"')
        rows[path] = {"added": int(added) if added.isdigit() else None,
                      "removed": int(removed) if removed.isdigit() else None,
                      "binary": added == "-" or removed == "-"}
    return rows


def changes(root: Path | None = None) -> dict:
    """这份工作副本相对 HEAD 改了哪些文件 —— 只读:两条 git 命令,不写任何东西。

    git 给不出的(未跟踪文件的行数、二进制)明说给不出,不补 0。
    不是仓库 / git 不在 / 问不出 → ok=False 带原因,绝不回一份空清单装成功。
    """
    here = Path(root).resolve() if root else None
    if here is None or not here.is_dir():
        return {"ok": False, "msg": f"目录不存在:{here}"}
    # 先**不起进程**判一次"往上有没有 .git":大多数工作区压根不是仓库,
    # 为它们起一次 git 只是白等,还白给系统一次弹模态错误框的机会(见 _git 上面那段)。
    if find_repo_root(here) is None:
        return {"ok": False, "msg": "当前工作区不是 git 仓库(往上没找到 .git,没有 HEAD 可比)"}
    if not is_git_repo(here):
        return {"ok": False, "msg": "当前工作区不是 git 仓库(没有 HEAD 可比)"}
    ok, status = _git(["status", "--porcelain"], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"git status 读不出来:{status}"}
    rows = parse_status_porcelain(status)
    # 第二次问的是"改了几行",仓库一次提交都没有时 diff HEAD 会失败 ——
    # 那时文件清单照样给,只是行数一律没得算(下面按 None 走同一句实话)。
    ok2, num = _git(["diff", "HEAD", "--numstat"], cwd=here)
    counts = parse_numstat(num) if ok2 else {}
    for r in rows:
        got = counts.get(r["path"]) or {}
        r["added"] = got.get("added")
        r["removed"] = got.get("removed")
        r["binary"] = bool(got.get("binary"))
    return {"ok": True, "repo": str(here), "files": rows[:CHANGE_ROW_LIMIT],
            "total": len(rows), "more": max(0, len(rows) - CHANGE_ROW_LIMIT),
            "added": sum(r["added"] or 0 for r in rows),
            "removed": sum(r["removed"] or 0 for r in rows),
            "uncounted": sum(1 for r in rows if r["added"] is None),
            "no_line_counts": not ok2}


# ---------------------------------------------------- 分支审查(N1,v0.9.119)
#
# 「相对 HEAD 的改动」那档看的是**这份工作副本自己**;分支审查看的是**另一条
# 线**:某个分支相对分叉点(merge-base)改了哪些文件、提交清单、行数合计 ——
# 审查一个 PR/feature 分支时,模型需要的就是这份概览 + 逐文件 diff。
# 同一档纪律:全部只读;git 给不出的(二进制行数)明说给不出,不补 0。

def _safe_rev(rev: str) -> str | None:
    """分支名安全闸:空、以 - 开头、带空白的一律不收。

    所有 git 调用都走参数列表(不经 shell),这里挡的是**选项注入**:
    `--upload-pack=x` 这类以 - 开头的"分支名"会被 git 当成它自己的选项。
    带空白的 git 本来也不认。
    """
    rev = (rev or "").strip()
    if not rev or rev.startswith("-") or any(c.isspace() for c in rev):
        return None
    return rev


def parse_name_status(text: str) -> dict:
    """`git diff --name-status` → {路径: 状态}。改名行 `R100 旧 -> 新` 取新名
    (人要看的是现在的名字);状态字母照 git 原样(A/M/D/R…),不翻译 ——
    这里是喂给审查者的原始事实,翻译是回话层的事。"""
    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        out[parts[-1].strip()] = parts[0][:1]
    return out


def _branch_base(root: Path | None, branch: str) -> dict:
    """分支审查共用的前半段:仓库核验 → 名字安全闸 → 解析 → 分叉点。

    返回 {"ok": True, root, rev, tip, base} 或 {"ok": False, msg}。
    tip/base 都 strip 过(实测:rev-parse 的输出带换行,不 strip 永远比不中)。
    """
    here = Path(root).resolve() if root else None
    if here is None or not here.is_dir():
        return {"ok": False, "msg": f"目录不存在:{here}"}
    if find_repo_root(here) is None:
        return {"ok": False,
                "msg": "当前工作区不是 git 仓库(往上没找到 .git,没有分支可比)"}
    if not is_git_repo(here):
        return {"ok": False, "msg": "当前工作区不是 git 仓库(没有分支可比)"}
    rev = _safe_rev(branch)
    if rev is None:
        return {"ok": False,
                "msg": "分支名不合法(不以 - 开头、不含空白,比如 feat/login-fix)"}
    ok, tip = _git(["rev-parse", "--verify", f"{rev}^{{commit}}"], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"分支不存在或写错了:{rev}(git 原话:{tip})"}
    ok, base = _git(["merge-base", "HEAD", rev], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"算不出分叉点(merge-base):{base}"}
    return {"ok": True, "root": here, "rev": rev,
            "tip": tip.strip(), "base": base.strip()}


def branch_overview(root: Path | None, branch: str) -> dict:
    """一个分支相对分叉点(merge-base)改了什么 —— 只读,五条 git 命令。

    为什么以 merge-base 为基准而不是 HEAD:审查的是"这条分支自己带来的改动",
    主线在分叉之后自己新走的提交不该算进它的账。git 给不出的(二进制行数)
    照 parse_numstat 的口径留 None;不是仓库/分支不存在都如实说,不猜。
    """
    r = _branch_base(root, branch)
    if not r.get("ok"):
        return r
    here, rev = r["root"], r["rev"]
    tip, base = r["tip"], r["base"]
    # 退化情形:人当前就在这条分支上(或它已完全并进当前线),merge-base 就是
    # 分支顶点 —— 区间为空,"审自己"审不出东西。这时自动改用默认线(main/master)
    # 当参考线,并在结果里带一句说明;两条默认线都不存在就如实说,不猜。
    note = ""
    if base == tip:
        for cand in ("main", "master"):
            ok2, _ = _git(["rev-parse", "--verify", f"{cand}^{{commit}}"], cwd=here)
            if not ok2:
                continue
            ok3, b2 = _git(["merge-base", cand, rev], cwd=here)
            if ok3 and b2.strip() != tip:
                base = b2.strip()
                note = f"当前就在 {rev} 上(或它已并入当前线),参考线自动改用 {cand}"
                break
        if base == tip:
            return {"ok": False,
                    "msg": f"分支 {rev} 已完全包含在当前线里(merge-base 就是分支顶点):"
                           "从当前线算不出它的改动 —— 切到别的分支再审。"}
    ok, ahead = _git(["rev-list", "--count", f"{base}..{rev}"], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"数不出提交个数:{ahead}"}
    ok, log = _git(["log", "--oneline", "--no-decorate", f"{base}..{rev}"], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"提交清单读不出来:{log}"}
    commits = []
    for line in log.splitlines():
        if line.strip():
            sha, _, subject = line.partition(" ")
            commits.append({"sha": sha, "subject": subject})
    ok, ns = _git(["diff", "--name-status", f"{base}..{rev}"], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"文件清单读不出来:{ns}"}
    states = parse_name_status(ns)
    ok, num = _git(["diff", "--numstat", f"{base}..{rev}"], cwd=here)
    counts = parse_numstat(num) if ok else {}
    rows = []
    for path, state in states.items():
        got = counts.get(path) or {}
        rows.append({"path": path, "state": state,
                     "added": got.get("added"), "removed": got.get("removed"),
                     "binary": bool(got.get("binary"))})
    rows.sort(key=lambda r: (-((r["added"] or 0) + (r["removed"] or 0)), r["path"]))
    return {"ok": True, "repo": str(here), "branch": rev, "base": base,
            "note": note,
            "commits_total": int(ahead.strip() or 0),
            "commits": commits[:BRANCH_COMMIT_LIMIT],
            "commits_more": max(0, len(commits) - BRANCH_COMMIT_LIMIT),
            "files": rows[:CHANGE_ROW_LIMIT], "total": len(rows),
            "more": max(0, len(rows) - CHANGE_ROW_LIMIT),
            "added": sum(r["added"] or 0 for r in rows),
            "removed": sum(r["removed"] or 0 for r in rows),
            "uncounted": sum(1 for r in rows if r["added"] is None),
            "no_line_counts": not ok}


def branch_file_diff(root: Path | None, branch: str, path: str) -> dict:
    """一个文件在该分支上的完整 diff(相对分叉点)—— 只读,三条 git 命令。

    行数封顶 BRANCH_DIFF_LIMIT:超了截断并明说"另有 N 行没列",不静默;
    二进制文件 git 会说"Binary files ... differ",照原话转述,不假装能看。
    """
    r = _branch_base(root, branch)
    if not r.get("ok"):
        return r
    here, rev, base = r["root"], r["rev"], r["base"]
    p = (path or "").strip().strip('"')
    if not p:
        return {"ok": False,
                "msg": "要给 file(仓库内相对路径),才看得到单文件 diff"}
    ok, out = _git(["diff", f"{base}..{rev}", "--", p], cwd=here)
    if not ok:
        return {"ok": False, "msg": f"diff 读不出来:{out}"}
    lines = out.splitlines()
    return {"ok": True, "path": p, "branch": rev, "base": base,
            "diff": "\n".join(lines[:BRANCH_DIFF_LIMIT]),
            "lines": min(len(lines), BRANCH_DIFF_LIMIT),
            "lines_total": len(lines),
            "truncated": len(lines) > BRANCH_DIFF_LIMIT,
            "binary": "binary files" in out.lower()}


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
                  path: str = "", exists=None, file: str = "") -> str:
    """worktree 工具处理器:只报事实与建议,建副本的命令由 bash 带确认执行。"""
    root = Path(str(path).strip()) if str(path).strip() else \
        getattr(ctx, "workspace", None)
    action = (str(action or "list")).strip().lower()
    if action == "suggest":
        r = suggest(root, str(branch or ""))
        if not r.get("ok"):
            return f"错误: {r['msg']}"
        return (f"落点:{r['path']}\n要执行的命令:{r['command']}\n{r['note']}\n{r['next']}")
    if action == "changes":
        r = changes(root)
        if not r.get("ok"):
            return f"错误: {r['msg']}"
        if not r["files"]:
            return (f"{r['repo']} 相对 HEAD 是干净的:没有一个文件与上次提交不一样。"
                    "\n(未跟踪的新文件也算\"有改动\",被 .gitignore 挡着的不算。)")
        head = (f"{r['repo']} 相对 HEAD 改了 {r['total']} 个文件"
                + (f"(这里列前 {len(r['files'])} 个,另有 {r['more']} 个没列)" if r["more"] else "")
                + (f";git 算出来的合计 +{r['added']} / −{r['removed']} 行"
                   if not r["no_line_counts"] else ";行数没算出来(这仓库一次提交都还没有)"))
        lines = [head]
        for f in r["files"]:
            if f.get("binary"):
                n = "二进制文件,git 没给行数"
            elif f["added"] is None:
                n = "git 没给行数(未跟踪的东西它还没存过)"
            else:
                n = f"+{f['added']} / −{f['removed']} 行"
            lines.append(f"  - {f['state']} {f['path']} · {n}"
                         + (" · 已暂存" if f["staged"] else ""))
        if r["uncounted"]:
            lines.append(f"  (其中 {r['uncounted']} 个文件没有行数,不是 0 行 —— 没算过的不写数。)")
        lines.append("一句边界:这一档**看得见命令(bash / py_script)与编辑器自己改的文件**,"
                     "而「本轮改动」那份账只认我用 write_file / edit_file 改的 —— "
                     "所以看得见不等于退得掉:能逐处退的只有那份账里的。要退这里多出来的那些,"
                     "得你自己下 `git restore -- <文件>`(未跟踪的只能自己删),那是丢东西的动作,"
                     "我不替你做,也不替你决定哪些该丢。")
        return "\n".join(lines)
    if action == "branch":
        rev = _safe_rev(str(branch or ""))
        if rev is None:
            return ("错误: 分支名不合法(不以 - 开头、不含空白),比如 feat/login-fix;"
                    "不带 branch 回概览,带 branch=<名> 加 file=<仓库内相对路径>"
                    "看单文件完整 diff。")
        if str(file or "").strip():
            r = branch_file_diff(root, rev, str(file).strip())
            if not r.get("ok"):
                return f"错误: {r['msg']}"
            head = (f"分支 {rev} 相对分叉点 {r['base'][:12]} 的"
                    f"{r['path']} diff(共 {r['lines_total']} 行"
                    + (f",这里列前 {r['lines']} 行,另有 "
                       f"{r['lines_total'] - r['lines']} 行没列)"
                       if r["truncated"] else ")"))
            return head + "\n" + r["diff"]
        r = branch_overview(root, rev)
        if not r.get("ok"):
            return f"错误: {r['msg']}"
        lines = ([f"({r['note']})"] if r.get("note") else []) + \
                [f"{r['repo']} 分支 {rev} 相对分叉点 {r['base'][:12]}:"
                 f"{r['commits_total']} 个提交、{r['total']} 个文件,"
                 f"合计 +{r['added']} / −{r['removed']} 行"
                 + (f"(其中 {r['uncounted']} 个没有行数 —— 二进制 git 不数,不当 0)"
                    if r["uncounted"] else "")]
        if r["commits"]:
            lines.append(f"提交(列前 {len(r['commits'])} 条"
                         + (f",另有 {r['commits_more']} 条没列)" if r["commits_more"]
                            else "):"))
            for c in r["commits"]:
                lines.append(f"  - {c['sha']} {c['subject']}")
        if r["files"]:
            lines.append(f"文件(列前 {len(r['files'])} 个"
                         + (f",另有 {r['more']} 个没列)" if r["more"] else "):"))
            for f in r["files"]:
                if f.get("binary"):
                    n = "二进制,git 没给行数"
                elif f["added"] is None:
                    n = "没行数(git 没说,不编 0)"
                else:
                    n = f"+{f['added']} / −{f['removed']} 行"
                lines.append(f"  - {f['state']} {f['path']} · {n}")
        lines.append("要细看哪个文件:同一个 action 带 branch=<分支名> 与 "
                     "file=<仓库内相对路径>,回给完整 diff(行数封顶,超了明说截断)。"
                     "审查意见你自己给 —— 这一步只把事实摆齐,不改任何文件。")
        return "\n".join(lines)
    if action != "list":
        return (f"错误: 不认识的 action「{action}」。可用:"
                + "/".join(f"{a}({ACTIONS[a]})" for a in ACTIONS) + "。")
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
