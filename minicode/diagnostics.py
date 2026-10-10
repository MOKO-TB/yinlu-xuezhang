"""编辑后诊断回灌(v0.9.147):把"改完知道对不对"从 Python 语法扩到多语言。

## 它补的是哪一格

`agent._py_syntax_feedback` 是一条**语法底线**:`.py` 落盘后 `ast.parse` 一遍,
解析不过就把行号回填进工具结果。它管得住的只有"这个文件还是不是合法的 Python",
**类型错、未定义名字、`.ts` 的类型不匹配、测试跑不过 —— 一条都不覆盖**。竞品里
Cursor / Cline / Roo 有 LSP 诊断回灌,Aider 的 `--auto-lint` 默认开,而这里
`coding-agent-capability-matrix.md` §10D 那一格一直是 ◐。

## 三条设计约束(它们决定了这个模块为什么长这样)

1. **argv 全部由扩展名查表得出,模型碰不到一个字符。** 不接受模型给的命令、参数、
   环境变量或 shell 字符串;没有 shell 层;cwd 固定(工作区,或该文件所在目录)。
   代价是必须把可执行面收死 —— 换来的是"每次编辑后自动跑"这件事**不必弹确认框**
   (`hooks.py` 的先例:用户配置过的、argv 固定的自动命令不经确认框,风险写进 preview)。
   走 `bash` 就意味着一轮改十个文件要点十次头,那等于把闭环做成没有。
2. **检查器不存在就跳过并说明,绝不假装跑过。** `ruff` 是 dev 依赖,**打包版里一定
   没有**;`tsc` 要工作区自己装;`go`/`cargo` 多数学生机器上没有。缺了就说"没做检查",
   **不许出现"通过 / 干净 / OK"这类字样** —— 那是本项目列在禁忌里的最贵坏法。
3. **超时不等于通过。** 没跑完就说没跑完,不拿超时当结论。

## 与 `_py_syntax_feedback` 的关系:并存但分权,不取代

三条理由:① 仓库已把口径写死(`matrix:351` 那句"不可关的语法反馈底线,刻意不被
H6 替换,避免两套真相");② 打包版里 ruff 一定不在,一旦取代,装好的包里 `.py` 检查
会掉进"检查器不存在"分支 = **底线没了**;③ 两套真相的具体危害是"同一文件两句不同
的话"。所以分权后:**语法结论只有一处**(`ast.parse`,仍在 `agent.py`),本模块的
`.py` 档**只看 ruff**。两个共用常量(`SYNTAX_CHECK_TOOLS` / `SYNTAX_SCAN_LIMIT`)
归到这里当公共口径,两处 import 同一份。

## 明确不做

不做 LSP(要每语言一个常驻服务 + 握手 + 增量同步,"一步一命令"的进程模型与它不同构);
**不做自动修复**(`--fix` 都是写文件,要单独过门与进撤销账,还要说清"是检查器改的");
**不联网装检查器**(`npx`/`cargo`/`go` 会拉包并改变用户磁盘);不做"每次编辑都跑全部
语言";不做跨语言全项目体检(那是 CI 的事)。
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from minicode.tools.base import (PathBlocked, PathOutsideWorkspace, ToolContext,
                                 resolve_path)

# 绝对路径(盘符式 `D:\a\b` 或 UNC 式 `\\srv\share\a`)。**不匹配**后面的 `:行:列`
# —— 那两段是"问题本身",剥掉就等于让"同一批错换了行"看起来一样。
_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s:]*")

# 与 `agent._py_syntax_feedback` 共用的两个边界(v0.9.147 归到一处,不再两处各写一份)。
# 注意 `apply_patch` **不在**里面 —— 这是 v0.9.134 留下的既有口径,本轮不动它,
# 改它要单独一轮并说清为什么。
SYNTAX_CHECK_TOOLS = ("write_file", "edit_file")
SYNTAX_SCAN_LIMIT = 512 * 1024          # 只自检 512 KB 以内:再大不是"手写代码"的场景

CHECK_TIMEOUT = 60                      # 单个检查器的秒数上限(超时**不算通过**)
CHECK_ITEMS_LIMIT = 40                  # 回灌最多列这么多条,超了报总数
CHECK_ITEM_CHARS = 240                  # 单条截断长度


@dataclass(frozen=True)
class Checker:
    """一个按扩展名选出来的检查器。`argv` 是**定死的**,模型碰不到。"""

    id: str
    label: str
    argv: tuple[str, ...]     # 固定参数;`path_arg` 为真时把文件路径追加到最后
    path_arg: bool
    cwd: str                  # "workspace" = 工作区根;"file" = 该文件所在目录
    probe: str                # 怎么找可执行文件,见 `_resolve_prefix`
    scope_note: str           # **必须如实说**的粒度与副作用


def _tsc() -> Checker:
    return Checker(
        id="tsc", label="tsc",
        argv=("--noEmit",), path_arg=False, cwd="workspace",
        probe="local:node_modules/.bin/tsc",
        scope_note="报的是**整个项目**的类型图,不是这一个文件;要工作区自己装了 tsc",
    )


CHECKERS: dict[str, Checker] = {
    ".py": Checker(
        id="ruff", label="ruff",
        argv=("check", "--output-format", "concise", "--no-cache"),
        path_arg=True, cwd="workspace", probe="module:ruff",
        scope_note="只报这一个文件;ruff 是开发依赖,**打包版里没有**",
    ),
    ".ts": _tsc(),
    ".tsx": _tsc(),
    ".js": Checker(
        id="node-check", label="node --check",
        argv=("--check",), path_arg=True, cwd="workspace", probe="which:node",
        scope_note="只做语法检查,不做类型与风格",
    ),
    ".go": Checker(
        id="go-vet", label="go vet",
        argv=("vet", "./"), path_arg=False, cwd="file", probe="which:go",
        scope_note="粒度是**这个文件所在的包**,不是这一个文件",
    ),
    ".rs": Checker(
        id="cargo-check", label="cargo check",
        argv=("check",), path_arg=False, cwd="file", probe="which:cargo",
        scope_note="粒度是整个 **crate**,而且**会执行工作区代码**(`build.rs`)",
    ),
}


def checker_for(path: str | Path) -> Checker | None:
    """按扩展名挑检查器;没有这个类型就回 None(**不猜**)。"""
    ext = Path(str(path)).suffix.lower()
    return CHECKERS.get(ext)


def _local_bin(workspace: Path, rel: str) -> Path | None:
    """在**工作区及其各级父目录**里找 `node_modules/.bin/<rel>`。

    为什么往上找:monorepo 里 `tsc` 常常装在仓库根,而被改的文件在子包里。
    只找一层会把"项目其实装了"误判成"没装"。
    """
    names = (rel, f"{rel}.cmd", f"{rel}.exe")
    for base in (workspace, *workspace.parents):
        for name in names:
            p = base / name
            if p.is_file():
                return p
    return None


def _resolve_prefix(checker: Checker, workspace: Path) -> tuple[list[str] | None, str]:
    """找可执行文件。返回 `(argv 前缀, 一句要如实说的话)`;找不到时前缀为 None。

    **不联网、不装包** —— `npx`/`go`/`cargo` 会拉依赖并改用户磁盘,那不是"顺手帮你
    装一个检查器"能解释的。
    """
    kind, _, value = checker.probe.partition(":")
    if kind == "module":
        if importlib.util.find_spec(value) is None:
            return None, f"本机没有 {checker.label}(它是开发依赖,装好的包里不会有)"
        return [sys.executable, "-m", value], ""
    if kind == "which":
        exe = shutil.which(value)
        if not exe:
            return None, f"本机没有 {checker.label}(没找到 {value})"
        return [exe], ""
    if kind == "local":
        found = _local_bin(workspace, value)
        if found is None:
            return None, (f"本机没有 {checker.label}(工作区里没有 "
                          f"{value},也不会替你联网装)")
        return [str(found)], ""
    return None, f"不认识这种探测方式:{checker.probe}"


def _norm_line(line: str, workspace: Path) -> str:
    """去掉绝对路径与空白 —— 指纹要只随**问题本身**变,不随环境变。

    这里剥的是**任意**绝对路径,不只是工作区那一段:同一批错在别的目录、别的盘符
    下报出来必须得到同一个指纹,否则"换台机器"会被误判成"你改了但没修好"。
    位置信息(行号:列号)与规则码保留 —— 那才是"问题本身"。
    """
    text = line.strip().replace(str(workspace), "<ws>")
    text = _ABS_PATH.sub("<p>", text)
    return " ".join(text.split())[:CHECK_ITEM_CHARS]


def run_checker(workspace: Path, rel: str, checker: Checker) -> dict:
    """跑一次检查器。返回 `{ok, ran, items, note, scope}`。

    `ran=False` 表示**没跑成**(检查器不存在 / 超时 / 起不动)—— 那时 `ok` 一定是
    False 且 `note` 必须说清为什么,调用方**不许**把它说成"通过"。
    """
    prefix, why = _resolve_prefix(checker, workspace)
    if prefix is None:
        return {"ok": False, "ran": False, "items": [], "note": why,
                "scope": checker.scope_note}
    target = workspace / rel
    cwd = workspace if checker.cwd == "workspace" else target.parent
    argv = [*prefix, *checker.argv, *( [str(target)] if checker.path_arg else [] )]
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              timeout=CHECK_TIMEOUT, shell=False)
    except subprocess.TimeoutExpired:
        # 超时**不是通过**:没跑完就说没跑完。
        return {"ok": False, "ran": False, "items": [],
                "note": f"{checker.label} 在 {CHECK_TIMEOUT} 秒内没跑完,"
                        "这一次不算检查过", "scope": checker.scope_note}
    except OSError as e:
        return {"ok": False, "ran": False, "items": [],
                "note": f"{checker.label} 起不动({type(e).__name__})",
                "scope": checker.scope_note}
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    text = raw.decode("utf-8", errors="replace")
    items = [_norm_line(l, workspace) for l in text.splitlines() if l.strip()]
    return {"ok": proc.returncode == 0, "ran": True, "items": items[:CHECK_ITEMS_LIMIT],
            "total": len(items), "note": "", "scope": checker.scope_note}


def fingerprint(items: list[str]) -> str:
    """一批诊断的指纹:同样的"没修好"必须能被认出来。"""
    blob = "\n".join(items).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


class DiagnosisCache:
    """去重与"没修好"的账。挂在 agent 实例上(与 `_hook_bad` 同一口径)。

    三条规矩:①**文件变了但指纹相同 → 不沉默**,回一行"仍是上次那 N 条,这次改动
    没有解决它"(沉默会把"你改了但没修好"变成没人知道);②同指纹连续 ≥2 次压成
    一行;③文件删除则清键(否则同一个路径下次重建会继承旧账)。
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], tuple[str, int]] = {}

    def note(self, rel: str, checker_id: str, fp: str, count: int) -> str:
        """记一次并回一句该怎么描述它:"" = 首次,原样全列。"""
        key = (rel, checker_id)
        prev = self._seen.get(key)
        self._seen[key] = (fp, count)
        if prev is None or prev[0] != fp:
            return ""
        times = 2 if prev[1] == count else prev[1] + 1
        self._seen[key] = (fp, times)
        if times == 2:
            return (f"仍是上次那 {count} 条,这次改动没有解决它"
                    "(再改一次还是同一批,我就只提这一行)")
        return f"仍是同一批 {count} 条(第 {times} 次)"

    def forget(self, rel: str) -> None:
        for key in [k for k in self._seen if k[0] == rel]:
            self._seen.pop(key, None)


def feedback(workspace: Path, rel: str, checker: Checker, result: dict,
             cache: DiagnosisCache | None = None) -> str:
    """把一次检查的结论拼成**回灌给模型的一段话**。没跑成时如实说没跑成。"""
    head = f"\n\n⚠ 诊断({checker.label} · {checker.scope_note}):"
    if not result["ran"]:
        return head + f"**这次没做检查** —— {result['note']}。"
    if result["ok"]:
        return head + "没有报出问题。"
    items = result["items"]
    total = int(result.get("total") or len(items))
    more = "" if total <= len(items) else f"(共 {total} 条,只列前 {len(items)} 条)"
    body = "\n".join("  " + x for x in items)
    tag = ""
    if cache is not None:
        tag = cache.note(rel, checker.id, fingerprint(items), total)
    if tag:
        return head + f"{tag}。"
    return head + f"{more}\n{body}"


# ------------------------------------------------------------------ 工具面

def _list_report(workspace: Path) -> str:
    """这台机器上按扩展名能跑哪些检查器。**不可用就说不可用**,不写成"跳过"。"""
    lines = ["按扩展名能跑哪些检查器(argv 全部定死,不联网装任何东西):"]
    for ext in sorted(CHECKERS):
        c = CHECKERS[ext]
        prefix, why = _resolve_prefix(c, workspace)
        mark = "可用" if prefix else f"不可用 —— {why}"
        lines.append(f"- {ext} → {c.label}({mark});{c.scope_note}")
    lines.append("另有一条不可关的**语法底线**(`ast.parse`,只管 `.py`,写在 agent 里):"
                 "它不在上面这张表里,也不会因为这里缺检查器而消失。")
    return "\n".join(lines)


def diagnostics(ctx: ToolContext, action: str = "check", path: str = "",
                reason: str = "") -> str:
    """`diagnostics` 工具:按文件类型跑一次检查器,把结论原样摆出来。

    两处刻意的口径:①**没有这个类型的检查器时必须回答"没做检查"**,不许写成
    "检查过了没问题" —— 那是本项目列在禁忌里的最贵坏法;②检查器不存在、超时、
    起不动,一律说"这次没做检查"并给原因,`ok` 不会因此变成 True。
    """
    workspace = Path(ctx.workspace)
    act = str(action or "check").strip().lower()
    if act == "list":
        return _list_report(workspace)
    if act != "check":
        return f"错误: action 只认 check / list,收到「{action}」。"
    raw = str(path or "").strip()
    if not raw:
        return "错误: action=check 需要 path(要检查哪个文件)。"
    try:
        p = resolve_path(ctx, raw, for_write=False)
    except (PathOutsideWorkspace, PathBlocked, OSError):
        return f"错误: 读不到 {raw}(越界、被拒或不存在)。"
    if not p.is_file():
        return f"错误: {raw} 不是一个文件(或不存在)。"
    checker = checker_for(p)
    if checker is None:
        known = "、".join(sorted(CHECKERS))
        return (f"没有这个文件类型的检查器:{p.suffix or '(无扩展名)'}。"
                f"现在认得的是:{known}。**没做检查** —— 这句话不等于"
                "「检查过了没问题」。")
    try:
        size = p.stat().st_size
    except OSError:
        return f"错误: 读不到 {raw} 的大小。"
    if size > SYNTAX_SCAN_LIMIT:
        return (f"错误: {raw} 有 {size} 字节,超过自检上限 {SYNTAX_SCAN_LIMIT} —— "
                "这么大的文件不是「手写代码」的场景,跳过(**没做检查**)。")
    try:
        rel = p.relative_to(workspace).as_posix()
    except ValueError:
        # 工作区外的文件没有"工作区相对路径"可给检查器当 cwd 锚点,如实拒绝。
        return f"错误: {raw} 在工作区外,诊断只对工作区内的文件跑。"
    result = run_checker(workspace, rel, checker)
    head = f"{checker.label} · {checker.scope_note}\n"
    if not result["ran"]:
        return head + f"**这次没做检查** —— {result['note']}。"
    if result["ok"]:
        return head + "没有报出问题。"
    items = result["items"]
    total = int(result.get("total") or len(items))
    more = "" if total <= len(items) else f"(共 {total} 条,只列前 {len(items)} 条)"
    return head + f"报出 {total} 条{more}\n" + "\n".join("  " + x for x in items)
