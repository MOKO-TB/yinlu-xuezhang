"""「环境信息」这一屏的数据:工作区 / 分支 / 本轮改动 / 后台命令 / 本机账本落在哪。

对标 Qoder 侧栏那块可折叠的「环境信息」(+201 -0 / 本地 / main / 提交或推送 / 后台进程)。
**刻意不做的那半**:Qoder 那块上有一颗「提交或推送」的按钮 —— 本机没有这个动作的对应物
(学长提交代码要走的是对话里的 bash + 确认框,而且推送按约定要用户单独点头),
摆一颗点了没用的按钮就是装饰,所以这里只给**事实**不给按钮。

每一条都要能追到一次真调用:`worktree.changes` 跑两条只读 git 命令,`bg_facts` 读的是
后台任务那张内存账,`paths.diagnostics` 是本机状态目录的真相。git 给不出的数(未跟踪文件
的行数、二进制)原样标 `partial`,**不补 0** —— 一个看着完整的 +201 -0 如果是拿 0 填出来的,
比不报更坏。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _added_removed(rows: list[dict]) -> tuple[int, int, bool]:
    """把改动清单里的行数加起来;任何一条给不出(Nones/二进制)就标 partial。"""
    added = removed = 0
    partial = False
    for r in rows:
        a, d = r.get("added"), r.get("removed")
        if a is None or d is None or r.get("binary"):
            partial = True
            continue
        added += int(a)
        removed += int(d)
    return added, removed, partial


def collect(workspace: Path | None = None) -> dict:
    """组装这一屏;任何一项读不出来都如实缺着,不编。"""
    from minicode import __version__, APP_NAME, paths, worktree
    from minicode.tools import shell

    here = Path(workspace).expanduser().resolve() if workspace else None
    out: dict[str, Any] = {
        "app": APP_NAME, "version": __version__,
        "workspace": str(here) if here else "",
        "is_repo": False, "branch": "", "repo_root": "", "dirty": None,
        "files": [], "total": 0, "more": 0,
        "added": 0, "removed": 0, "partial": False,
        "app_dir": "", "backend": "", "writable": True,
        "frozen": bool(paths.FROZEN),
        "jobs": [], "msg": "", "git_note": "",
    }
    try:
        # diagnostics() 的真实字段:app_dir / config / backend / frozen / writable。
        # `writable=False` 是"配了不保存"那类反馈的唯一真相来源,这里照实带出去。
        diag = paths.diagnostics()
        out["app_dir"] = str(diag.get("app_dir") or "")
        out["backend"] = str(diag.get("backend") or "")
        out["writable"] = bool(diag.get("writable", True))
    except Exception:                     # 诊断读不出来:这一项空着,不挡其余
        pass
    if here is None or not here.is_dir():
        out["msg"] = "还没有选工作区"
        return out

    ch = worktree.changes(here)
    # git 起不来时(用户看图 p7:打开监控栏就弹一个 0xc0000142 的系统框)这一屏要说的
    # 不是"没有改动",而是"这台机器的 git 问不动" —— 熔断之后连问都不再问第二遍。
    out["git_note"] = worktree.git_unavailable()
    if not ch.get("ok"):
        out["msg"] = str(ch.get("msg") or "这份工作区读不出改动")
    else:
        out["is_repo"] = True
        out["repo_root"] = str(ch.get("repo") or here)
        out["files"] = [{"path": f.get("path"), "state": f.get("state"),
                         "added": f.get("added"), "removed": f.get("removed"),
                         "binary": bool(f.get("binary"))} for f in ch.get("files") or []]
        out["total"] = int(ch.get("total") or 0)
        out["more"] = int(ch.get("more") or 0)
        a, d, partial = _added_removed(out["files"])
        # 只列了前 30 个文件时,"合计"就只是这 30 个的合计 —— 不标出来是一句假话
        out["added"], out["removed"] = a, d
        out["partial"] = bool(partial or out["more"] > 0)

    # 分支:worktree_facts 跑的是 `git worktree list`,每行带 path/branch/head/dirty。
    # 取"路径就是这一份"的那一行 —— 拿第一条会在链接副本里报成别的分支。
    facts = worktree.worktree_facts(here)
    if isinstance(facts, dict) and facts.get("ok"):
        for row in facts.get("worktrees") or []:
            try:
                if Path(str(row.get("path") or "")).resolve() == here:
                    out["branch"] = str(row.get("branch") or "")
                    out["dirty"] = bool(row.get("dirty"))
                    break
                if not out["repo_root"]:
                    out["repo_root"] = str(facts.get("repo") or here)
            except OSError:               # 路径读不动(权限/已删):这一行跳过,不猜分支
                continue
    try:
        out["jobs"] = [{"id": j["id"], "command": str(j["command"])[:120],
                        "running": bool(j["running"]), "exit_code": j.get("exit_code"),
                        "chars": int(j.get("chars") or 0),
                        "elapsed": int(j.get("elapsed") or 0)}
                       for j in shell.bg_facts()]
    except Exception:
        out["jobs"] = []
    return out
