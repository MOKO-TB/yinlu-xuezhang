"""增量更新·路 2:文件级清单 + 暂存 + 独立应用器(v0.9.183 起,默认关)。

路 1 只回答"馆藏库与内核这两块大数据要不要重下";**程序自己的字节**变化时仍然整件
135 MB 起步。路 2 把安装树里"随包发的那些文件"逐件登记(相对路径 + 大小 + sha256),
更新时只下**哈希对不上的那些**,再交给一个独立脚本在 App 退出之后应用。

为什么应用这一步不在 App 进程里做:onedir 包运行时,exe 与它加载的那几个 dll 是
**被锁的**,自己覆盖自己必然失败一半 —— 半新半旧比更新失败更坏。所以这里生成一份
PowerShell 脚本,由界面在退出前 detached 起它;脚本逐件校验暂存副本、原件搬进回滚
目录、任何一件对不上就**整体搬回去**,并把结局写进 state.json 给下一次启动读。

信任边界没有变:清单与安装包同源(同一份发布资产),暂存每件都按清单里的 sha256 验,
应用脚本只碰安装树以内的路径 —— 清单里出现绝对路径、`..`、或数据目录,整份拒收。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 构建端不算进清单、客户端也绝不许碰的目录/文件:它们是**用户数据或大数据**,
# 归路 1 的身份与「我的数据」管,不归文件级更新管。
EXCLUDE_DIRS = ("crawled", "ms-playwright", "updates", "sessions", "checkpoints",
                "shadow-git", "web-sessions", "study", "__pycache__")
EXCLUDE_FILES = ("config.json", "token-usage.db", "tray.log",
                 "library.db.stamp.json", "browser-kernel.stamp.json")

_BAD_SEG = re.compile(r"(^|/)(\.\.?)(/|$)")


def validate_manifest(man: "dict | None") -> "tuple[list, list, list]":
    """把发布清单拆成 (要逐件对的 files, 要撤的 drop, 实话 problems)。

    拒收的形状一个都不放过:绝对路径、盘符、`..`、反斜杠混进来、重复登记、
    以及任何落在 EXCLUDE_DIRS/EXCLUDE_FILES 里的条目 —— 清单能指挥删除,
    所以它的边界就是这次更新的爆炸半径。
    """
    files: list = []
    drop: list = []
    problems: list = []
    seen: set = set()
    for raw in (man or {}).get("files") or []:
        p = str(raw.get("p") or "")
        norm = p.replace("\\", "/").strip("/")
        if not norm or _BAD_SEG.search(norm) or re.match(r"^[A-Za-z]:", p) \
                or p.startswith("/"):
            problems.append("非法路径:%r" % p)
            continue
        head = norm.split("/")[0]
        if head in EXCLUDE_DIRS or norm in EXCLUDE_FILES:
            problems.append("清单碰了数据目录:%r" % p)
            continue
        if norm in seen:
            problems.append("重复登记:%r" % p)
            continue
        seen.add(norm)
        h = str(raw.get("h") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", h):
            problems.append("哈希不像 sha256:%r" % p)
            continue
        files.append({"p": norm, "s": int(raw.get("s") or 0), "h": h})
    for raw in (man or {}).get("drop") or []:
        p = str(raw).replace("\\", "/").strip("/")
        if not p or _BAD_SEG.search(p) or re.match(r"^[A-Za-z]:", raw) or raw.startswith("/"):
            problems.append("非法删除路径:%r" % raw)
            continue
        if p.split("/")[0] in EXCLUDE_DIRS or p in EXCLUDE_FILES:
            problems.append("删除清单碰了数据目录:%r" % raw)
            continue
        drop.append(p)
    return files, drop, problems


def hash_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def diff_install(files: list, root: Path) -> dict:
    """本机安装树与清单比一遍:回 fetch(要下的)/ same(不用动的)/ extra_local(清单没有的)。

    先比大小再算哈希:1.3 GB 的库不在清单里,而安装树上千个文件里绝大多数哈希相同,
    大小这一道能把算哈希的次数压到"真变了的 + 同大小不同内容的"。
    extra_local **只报告不处理** —— 本机多出来的文件可能是用户自己放的,删除权只在
    清单的 drop 里,而 drop 的边界上面已经验过。
    """
    root = Path(root)
    want = {f["p"]: f for f in files}
    fetch, same, extra = [], [], []
    for rel, f in want.items():
        loc = root / rel
        if not loc.is_file():
            fetch.append(f)
            continue
        if loc.stat().st_size != f["s"]:
            fetch.append(f)
            continue
        if hash_file(loc) == f["h"]:
            same.append(rel)
        else:
            fetch.append(f)
    for loc in sorted(root.rglob("*")):
        if not loc.is_file():
            continue
        rel = loc.relative_to(root).as_posix()
        if rel in want or rel.split("/")[0] in EXCLUDE_DIRS or rel in EXCLUDE_FILES:
            continue
        extra.append(rel)
    return {"fetch": fetch, "same": same, "extra_local": extra}


def staged_root(updates_dir: Path, version: str) -> Path:
    return Path(updates_dir) / "staged" / version


def apply_plan_path(updates_dir: Path, version: str) -> Path:
    return staged_root(updates_dir, version) / "_apply" / "plan.json"


def state_path(updates_dir: Path, version: str) -> Path:
    return staged_root(updates_dir, version) / "_apply" / "state.json"


def read_state(updates_dir: Path, version: str) -> dict:
    p = state_path(updates_dir, version)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_apply_script(updates_dir: Path, version: str, install_root: Path,
                       files: list, drop: list) -> Path:
    """生成"App 退出之后跑"的应用脚本,回 (脚本路径)。

    脚本自己只做三件事:验暂存副本的哈希、原件搬进回滚目录、搬完写 state。
    任何一件验不过就**把回滚目录整体搬回去** —— 更新失败可以是没更新,
    不能是半新半旧。路径全部由这里拼好写进 plan.json,脚本里不拼用户输入。
    """
    # 最后一道边界:应用脚本只信 plan.json,而 plan.json 是本地拼出来的 —— 所以这里
    # 再验一遍边界(validate_manifest 是第一道,这一道防的是"调用方没验就传进来")。
    def _safe(rel: str) -> bool:
        rel = str(rel).replace("\\", "/").strip("/")
        return bool(rel) and not _BAD_SEG.search(rel)             and not re.match(r"^[A-Za-z]:", str(rel)) and not str(rel).startswith("/")

    files = [f for f in files if _safe(f.get("p"))]
    drop = [d for d in drop if _safe(d)]
    st = staged_root(updates_dir, version)
    adir = st / "_apply"
    adir.mkdir(parents=True, exist_ok=True)
    # **一律 resolve 成绝对路径**再写进 plan:PowerShell 的 `FullName` 永远是绝对的,
    # 而回滚是按 `$_.FullName.Substring($plan.rollback.Length + 1)` 求相对名的 ——
    # 给它一个相对前缀就会从错误的位置切,切出来的"相对名"再拼回安装树,
    # 结果是回滚文件被搬进一个凭空造出来的子目录、原件根本没回来
    # (2026-10-18 包内探针抓到;开发态测试传的是绝对 tmp_path,所以全绿)。
    plan = {"install": str(Path(install_root).resolve()), "staged": str(st.resolve()),
            "rollback": str((adir / "rollback").resolve()),
            "state": str(state_path(updates_dir, version).resolve()),
            "files": [{"p": f["p"], "h": f["h"]} for f in files], "drop": list(drop)}
    (adir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    ps1 = adir / "apply.ps1"
    ps1.write_text(_PS1, encoding="utf-8-sig")
    return ps1


_PS1 = r"""
param([int]$WaitPid = 0)
$ErrorActionPreference = 'Stop'
# 等 App 自己走开再动手:onedir 的 exe 与它加载的 dll 在被锁着的时候搬不动,
# 硬搬的结果是"半新半旧"—— 那比不更新更坏。界面把**自己的 pid** 传进来。
if ($WaitPid -gt 0) {
    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 250
    }
}
$plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plan.json') -Raw | ConvertFrom-Json
function Roll-Back {
    if (Test-Path -LiteralPath $plan.rollback) {
        Get-ChildItem -LiteralPath $plan.rollback -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($plan.rollback.Length + 1)
            $dst = Join-Path $plan.install $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $dst) | Out-Null
            Move-Item -LiteralPath $_.FullName -Destination $dst -Force
        }
    }
}
[System.IO.File]::WriteAllText($plan.state, '{"phase":"running"}', (New-Object System.Text.UTF8Encoding($false)))
try {
    foreach ($f in $plan.files) {
        $src = Join-Path $plan.staged $f.p
        $got = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $f.h) { throw ('staged hash mismatch: ' + $f.p) }
        $dst = Join-Path $plan.install $f.p
        if (Test-Path -LiteralPath $dst) {
            $bk = Join-Path $plan.rollback $f.p
            New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $bk) | Out-Null
            Move-Item -LiteralPath $dst -Destination $bk -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $dst) | Out-Null
        Move-Item -LiteralPath $src -Destination $dst -Force
    }
    foreach ($d in $plan.drop) {
        $dst = Join-Path $plan.install $d
        if (Test-Path -LiteralPath $dst) {
            $bk = Join-Path $plan.rollback $d
            New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $bk) | Out-Null
            Move-Item -LiteralPath $dst -Destination $bk -Force
        }
    }
    [System.IO.File]::WriteAllText($plan.state, '{"phase":"done"}', (New-Object System.Text.UTF8Encoding($false)))
    exit 0
} catch {
    Roll-Back
    $why = ($_.Exception.Message -replace '"', "'")
    [System.IO.File]::WriteAllText($plan.state, ('{"phase":"rolledback","why":"' + $why + '"}'), (New-Object System.Text.UTF8Encoding($false)))
    exit 3
}
"""


def apply_command(ps1: Path, wait_pid: int = 0) -> list:
    """界面 detached 起它的那条命令(单独一个函数是为了能被针钉住)。

    `wait_pid` 传的是**学长自己的进程号**:脚本会等它消失再搬文件,所以点完"应用"
    不必替用户关窗口(那条边界从 v0.9.176 起就在),只要他随后自己退出一次。
    """
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(ps1)]
    if int(wait_pid or 0) > 0:
        cmd += ["-WaitPid", str(int(wait_pid))]
    return cmd


def pick_patch(man: "dict | None", current: str) -> "dict | None":
    """按"我现在是哪一版"挑对应那件补丁;挑不到回 None(调用方退回 appupdate/整包)。

    补丁是**按来源版本**在构建期算出来的(变了哪些文件就装哪些),所以清单里是一张
    `patches` 表:`{"0.9.181": {"asset": …, "sha256": …, "size": …}}`。本机版本不在表里
    (太老、或跳过好几版)就没有对应补丁 —— 这时退回整件是**正确**的,不是降级失败。
    """
    for key, val in ((man or {}).get("patches") or {}).items():
        if str(key) == str(current).lstrip("vV") and isinstance(val, dict):
            if val.get("asset") and val.get("sha256"):
                return val
    return None


def verify_staged(files: list, staged: Path) -> "tuple[list, list]":
    """暂存解包之后逐件对哈希:回 (对得上的, 对不上的)。

    对不上的那几件**不进应用清单** —— 应用脚本那一道还会再验一次,但早一点知道
    就能早一点把"这次更新不完整"说给人听,而不是等应用时回滚才明白。
    """
    good, bad = [], []
    for f in files:
        loc = Path(staged) / f["p"]
        if not loc.is_file() or loc.stat().st_size != f["s"] or hash_file(loc) != f["h"]:
            bad.append(f["p"])
        else:
            good.append(f)
    return good, bad


def unpack_patch(zip_path: Path, dest_staged: Path) -> list:
    """把补丁 zip 解到暂存目录,回解出来的相对路径列表。

    解压时也按成员名做一遍边界检查(zip slip):成员名带 `..` 或绝对路径就整份拒收 ——
    补丁 zip 的哈希虽然验过,但"验过哈希"和"名字安全"是两件事。
    """
    import zipfile
    out: list = []
    dest = Path(dest_staged)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            rel = n.replace("\\", "/").strip("/")
            if not rel or rel.endswith("/") or _BAD_SEG.search(rel) \
                    or re.match(r"^[A-Za-z]:", n) or n.startswith("/"):
                raise ValueError("补丁里有越界成员:%r" % n)
        for n in z.namelist():
            rel = n.replace("\\", "/").strip("/")
            if rel.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, target.open("wb") as dst:
                dst.write(src.read())
            out.append(rel)
    return out
def install_root() -> Path:
    """正在跑的这份安装树:打包态是 exe 旁,源码态是仓库根(与内核凭证同一套根)。"""
    import sys
    from minicode.paths import FROZEN
    if FROZEN:
        return Path(sys.executable).resolve().parent
    from minicode.paths import APP_DIR
    return Path(APP_DIR)


def updates_dir() -> Path:
    from minicode.paths import app_path
    return Path(app_path("updates"))


def stage_update(version: str, on_progress=None) -> dict:
    """路 2 的取件:按"我现在这一版"挑补丁 zip,下下来、解包、逐件验、写好应用脚本。

    任何一步不成都回 `ok=False` 并带一句实话;调用方(界面或命令行)据此退回
    appupdate/整包 —— **没有对应来源版本的补丁不是失败**,是这条路本来就不覆盖。
    """
    from minicode import proactive
    ver = str(version).lstrip("vV")
    resp, why = proactive._http_get(
        proactive.RELEASES_API + "/tags/v" + ver,
        headers={"Accept": "application/vnd.github+json"})
    if resp is None:
        return {"ok": False, "why": "取发布页失败:" + why}
    rel = json.loads(resp.text)
    fj = next((a for a in rel.get("assets") or []
               if str(a.get("name") or "").endswith("-files.json")), None)
    if fj is None:
        return {"ok": False, "why": "这一版没有文件级清单,路 2 用不上"}
    man = json.loads(proactive._http_get(fj["browser_download_url"])[0].text)
    files, drop, problems = validate_manifest(man)
    if problems:
        return {"ok": False, "why": "清单不合规:" + ";".join(problems[:3])}
    cur = proactive._cur_version()
    patch = pick_patch(man, cur)
    if patch is None:
        return {"ok": False, "why": f"没有 v{cur} → v{ver} 的补丁,退回整件"}
    sha_asset = next((a for a in rel.get("assets") or []
                      if str(a.get("name") or "").endswith("-sha256.txt")), None)
    asset = {"name": patch["asset"],
             "url": "", "browser_url": _browser_url(ver, patch["asset"]),
             "sha_url": proactive._asset_api_url(sha_asset) if sha_asset else "",
             "size": int(patch["size"])}
    got = proactive.download_asset(asset, updates_dir(), on_progress=on_progress)
    if not got.get("ok"):
        return {"ok": False, "why": got.get("why") or "补丁下载没成"}
    staged = staged_root(updates_dir(), ver)
    try:
        names = unpack_patch(Path(got["path"]), staged)
    except (OSError, ValueError) as e:
        return {"ok": False, "why": "补丁解包失败:%s" % e}
    want = {f["p"]: f for f in files if f["p"] in set(names)}
    good, bad = verify_staged(list(want.values()), staged)
    if bad:
        return {"ok": False, "why": "暂存里 %d 件与清单不符:%s" % (len(bad), bad[0])}
    diff = diff_install(files, install_root())
    apply_list = [f for f in good if f["p"] in {d["p"] for d in diff["fetch"]}]
    drop_here = [d for d in drop if (install_root() / d).exists()]
    ps1 = write_apply_script(updates_dir(), ver, install_root(), apply_list, drop_here)
    return {"ok": True, "why": "", "staged": len(good), "apply": len(apply_list),
            "same": len(diff["same"]), "drop": len(drop_here),
            "bytes": sum(f["s"] for f in apply_list), "script": str(ps1)}


def _browser_url(ver: str, asset_name: str) -> str:
    return ("https://github.com/MOKO-TB/yinlu-xuezhang/releases/download/"
            "v%s/%s" % (ver, asset_name))


def apply_update(version: str) -> dict:
    """起应用脚本然后**由调用方退出自己** —— 锁着的文件只有"没人在跑"才换得动。

    这里只负责 detached 起进程;脚本自己验哈希、自己回滚、自己写 state。
    """
    import os
    import subprocess
    import sys
    ps1 = staged_root(updates_dir(), str(version).lstrip("vV")) / "_apply" / "apply.ps1"
    if not ps1.is_file():
        return {"ok": False, "why": "没有暂存好的应用脚本,先 --stage-update"}
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(apply_command(ps1, getattr(os, "getpid", lambda: 0)()), creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "why": "", "script": str(ps1)}
