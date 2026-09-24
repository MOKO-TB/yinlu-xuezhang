"""文档与数据边界的对账(v0.9.28 A 批)。五件事只要漂了就当场红,不等用户发现。

1. **版本号只有一处真相**:HANDOFF 抬头写的 `v0.9.xx` 必须等于 `minicode.__version__`。
2. **个人数据文件三处同步**:代码里凡是往数据目录写的每一份本机文件(`app_path("x")`
   与 `APP_DIR / "x"`),必须**同时**出现在 `.gitignore` 和
   `minicode/state_files.py::SHIP_NEVER`(那份清单就是打包时删除的那一份)里,
   或者写进下面的 `SHIP_INTENTIONALLY` 并给出理由。这一条防的是"把同学自己的
   课表/作息表/提醒记录随安装包发出去"—— v0.9.18~v0.9.27 每轮都靠人肉记得加,
   而这一轮加 `watchdog.log` 时我发现 `weather.json`、`web-sessions/`、
   `web-usage.log` 三份**从来没人加过**,于是把这件事变成脚本。
3. **§2 里程碑只留最近几轮**:更早的整行移到 `docs/handoff/`,主文档才不会长成
   没人读的厚度(读不动的交接文档等于没有交接文档)。
4. **`pyproject.toml` 只是门面,不是第二处要手抄的地方**(v0.9.39 R 批):它的
   `version` 与 `dependencies` 必须走 `dynamic`(分别指向 `minicode.__version__`
   和 `requirements.txt`)。写死了就会漂 —— 而漂掉的元数据比没有元数据更糟,
   别人 `pip install` 拿到的是一个假版本号。
5. **不外发清单自己也不能漂**(v0.9.39 R 批):`SHIP_NEVER` 里每一份都要被
   `.gitignore` 盖住,且 `build_exe.py` 必须**遍历**这份清单而不是自己抄一串名字。

用法: .venv/Scripts/python.exe scripts/check_docs.py [--collect]
   --collect 额外跑一次 `pytest --collect-only -q`,核对 §7 上手指南里写的测试基线条数
   (慢十几秒,提交前跑一次;本轮就是因为没人核,文档里的 1188 和实测的 1142 差着
   46 项漂了一整轮)。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_ROUNDS_KEPT = 5          # HANDOFF §2 主文档里最多留几轮里程碑

# 随包发的产品内容与资源:由 packaging/*.spec 打进包里,压根不是"本机状态"。
SHIP_ASSETS = {
    "kb/school-calendar.json": "手工维护的校历数据,是产品内容不是本机状态",
    "skills": "内置技能目录,随包发",
    "kb": "知识库语料随包发(审核产物 reviewed.json 另有清理语句)",
    "library.db": "离线馆藏库,133 万条书目是随包发的数据",
    "gcode.ico": "应用图标",
    "toast-icon.png": "桌面通知图标",
}

# 确实是本机状态,但打包流程**反过来要它随包发**(或由 spec 注入 exe 内部)。
# 这一份**必须**被扫描器扫得到:扫不到就说明代码里早就不写了,例外条目该删,
# 不然这个字典会变成"检查器红了就往这里加一行"的垃圾桶。
# v0.9.39 R 批:`browser-profile`、`sessions`、`config.json` 从这里删掉了 ——
# 它们本来就是"绝不该在包里"的那一类,如今归 `minicode/state_files.py::SHIP_NEVER`
# 管(那份清单才是打包时真删的东西),挂在例外里等于自己给自己开后门。
STATE_EXEMPT = {
    # 内置校园模型的 Key:packaging/*.spec 在打包时把它塞进 exe 的 _MEIPASS,
    # 让同学开箱即用(仓库里不放,构建机本地未跟踪)。exe 旁边压根不会有这份文件,
    # 所以它要的是「想清楚为什么可以发」,不是清理语句。
    "config.local.json": "构建机本地的内置模型 Key,只进 exe 内部(_MEIPASS),不落 dist 目录",
}
SHIP_INTENTIONALLY = {**SHIP_ASSETS, **STATE_EXEMPT}

_STATE_FILE = re.compile(r"""app_path\(\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))""")
# `CONFIG_PATH = APP_DIR / "config.json"` 这类赋值同样是 APP_DIR / "名字" 的形态,
# 一条模式就够 —— 早先这里写了两条分支,循环却只读第一条,第二条永远漏。
_DIR_FILE = re.compile(r"""APP_DIR\s*/\s*"([^"]+)\"""")
_CONST = re.compile(r"""^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']([^"']+)["']""", re.M)


def version_from_source() -> str:
    text = (ROOT / "minicode" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else "?"


def _module_constants(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _CONST.finditer(src)}


def state_targets() -> dict[str, list[str]]:
    """代码写进数据目录的每一份文件/目录:名字 -> 出处模块。

    只认字面量与"同模块内的简单常量赋值",不猜跨模块引用 —— 猜出来的清单
    会让这条检查看起来能过但其实没在看。
    """
    out: dict[str, list[str]] = {}
    for py in sorted((ROOT / "minicode").glob("*.py")):
        src = py.read_text(encoding="utf-8")
        consts = _module_constants(src)
        found: set[str] = set()
        for m in _STATE_FILE.finditer(src):
            if m.group(1):
                found.add(m.group(1))
            elif m.group(2) in consts:
                found.add(consts[m.group(2)])
        for m in _DIR_FILE.finditer(src):
            if m.group(1):
                found.add(m.group(1))
        for name in found:
            out.setdefault(name, []).append(py.name)
    return out


def check_version() -> list[str]:
    text = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    m = re.search(r"^>\s*更新:[^（(]*[（(]v([0-9.]+)[)）]", text, re.M)
    if not m:
        return ["HANDOFF 抬头没找到「更新:YYYY-MM-DD(vX.Y.Z)」这一行"]
    if m.group(1) != version_from_source():
        return [f"HANDOFF 抬头写 v{m.group(1)},源码是 v{version_from_source()}"]
    return []


def _ship_never() -> dict[str, str]:
    """`minicode/state_files.py::SHIP_NEVER`:打包时必须删掉的本机文件(唯一真相)。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from minicode import state_files
    return {e.name: e.kind for e in state_files.SHIP_NEVER}


def _gitignore_patterns() -> list[str]:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    return [ln.strip().lstrip("/") for ln in lines
            if ln.strip() and not ln.strip().startswith("#")]


def _ignored(name: str, patterns: list[str]) -> bool:
    """名字有没有被 .gitignore 盖住:目录写 `web-sessions/`、文件常写 `/watchdog.log`,
    通配写 `token-usage.db-*` —— 三种形态都算数。"""
    import fnmatch
    return any(fnmatch.fnmatch(name, p.rstrip("/")) for p in patterns)


def check_state_targets() -> list[str]:
    """每一份本机状态:要么 .gitignore + 不外发清单都有,要么列进例外且真有打包语句撑腰。"""
    ignore = _gitignore_patterns()
    ship_never = _ship_never()
    packed = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    for spec in (ROOT / "packaging").glob("*.spec"):
        packed += spec.read_text(encoding="utf-8")
    problems: list[str] = []
    for name, where in sorted(state_targets().items()):
        ignored = _ignored(name, ignore)
        if name in ship_never:
            # 清单里就有 = 打包时真会删掉它;这时不再看「随包发」例外,
            # 免得同名的本机状态被例外条目盖住(v0.9.39 R 批把 browser-profile
            # 从例外搬进清单后,两头都挂过一次名)。
            if not ignored:
                problems.append(f"{name}:在不外发清单里却没被 .gitignore 盖住")
            continue
        if name in SHIP_INTENTIONALLY:
            if name not in packed:
                problems.append(f"{name}:列为「随包发」但 build_exe/打包脚本里没有对应语句")
            continue
        # 走到这里 = 既不在不外发清单、也没列为例外:那就是"代码会往数据目录写这份
        # 文件,但打包时没人管它"。gitignore 有没有都改变不了这个结论,所以两处都点名。
        problems.append(
            f"{name}({'、'.join(sorted(set(where)))}):"
            + ("" if ignored else " 不在 .gitignore,并且")
            + " 不在 state_files.SHIP_NEVER 清单")
    return problems


def check_ship_never_manifest() -> list[str]:
    """不外发清单自己也不能漂:每一项都要被 gitignore 盖住,且打包脚本要真的读它。

    v0.9.39 R 批之前,这份清单是 `build_exe.py` 里一个四十多行的字面量,和
    `.gitignore` 各抄一份;抄第二份就一定会漂,所以这一轮把它收进
    `minicode/state_files.py`,这里只留"两处校验"。
    """
    ignore = _gitignore_patterns()
    ship_never = _ship_never()
    problems: list[str] = []
    if not ship_never:
        return ["SHIP_NEVER 是空的 —— 对账闸扫不到东西就等于没在查"]
    for name in sorted(ship_never):
        if not _ignored(name, ignore):
            problems.append(f"{name}:在不外发清单里,却没有被 .gitignore 盖住(开发态会提交进仓库)")
    build = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    if "SHIP_NEVER" not in build:
        problems.append("build_exe.py 不再遍历 SHIP_NEVER —— 清单退化成第二份手抄的危险回来了")
    return problems


def check_handoff_size() -> list[str]:
    """§2 里程碑行数超了就提醒归档(不做自动搬:搬文档要人看一眼连贯性)。"""
    text = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    section = text.split("## 2.", 1)
    if len(section) < 2:
        return ["HANDOFF 找不到 §2 里程碑表"]
    rows = re.findall(r"^\| v([0-9.]+) ", section[1].split("\n## ")[0], re.M)
    if len(rows) > MAX_ROUNDS_KEPT:
        return [f"HANDOFF §2 留了 {len(rows)} 轮里程碑(上限 {MAX_ROUNDS_KEPT}),"
                f"把最老的挪进 docs/handoff/(现在最老的几轮:{'、'.join(rows[MAX_ROUNDS_KEPT:])})"]
    return []


def check_collected_count() -> list[str]:
    """跑一次 collect-only,把 §7 写的基线数和实际收集数对一遍(慢,可选)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(ROOT), capture_output=True, text=True)
    m = re.search(r"(\d+) tests? collected", proc.stdout or "")
    if not m:
        return [f"pytest --collect-only 没报收集数(退出码 {proc.returncode})"]
    actual = int(m.group(1))
    text = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    claimed = {int(n) for n in re.findall(r"全绿\s*(\d{3,5})\s*项", text)}
    claimed |= {int(n) for n in re.findall(r"(\d{3,5}) 个测试全绿", text)}
    if actual not in claimed:
        return [f"实际收集 {actual} 项,HANDOFF 写的是 {sorted(claimed) or '没写'}"]
    return []


def pyproject_problems(text: str) -> list[str]:
    """对一份 pyproject 文本做对账(拆成纯函数是为了让测试能喂它假文本)。

    只查文本形态(不 import tomllib):这份检查要的是"有没有人往这里抄了一份
    版本号",正则足够,而且不会因为 Python 版本差异让对账闸本身红。
    """
    problems: list[str] = []
    if not re.search(r"""^dynamic\s*=\s*\[[^\]]*"version"[^\]]*\]""", text, re.M):
        problems.append("project.dynamic 里没有 \"version\"")
    if not re.search(r"""^dynamic\s*=\s*\[[^\]]*"dependencies"[^\]]*\]""", text, re.M):
        problems.append("project.dynamic 里没有 \"dependencies\"")
    if 'attr = "minicode.__version__"' not in text:
        problems.append("version 没有指向 minicode.__version__(源码才是唯一真相)")
    if 'file = ["requirements.txt"]' not in text:
        problems.append("dependencies 没有指向 requirements.txt")
    # 写死的字面量:无论是 version = "0.9.39" 还是 dependencies = ["openai>=1.50"]
    for m in re.finditer(r"""^\s*(version|dependencies)\s*=\s*["'\[]""", text, re.M):
        line = m.group(0).strip()
        if "attr" in line or "file" in line:
            continue
        problems.append(f"pyproject 里写死了 {line[:48]} —— 元数据抄一份就一定会漂")
    return problems


def check_pyproject() -> list[str]:
    """pyproject 的 version/dependencies 必须是「指向真源」的动态字段,不能写死。"""
    p = ROOT / "pyproject.toml"
    if not p.exists():
        return ["缺 pyproject.toml(项目元数据的门面)"]
    return pyproject_problems(p.read_text(encoding="utf-8"))


def check_pyproject_resolves() -> list[str]:
    """慢检查(--collect 才跑):让 setuptools 真算一遍元数据。

    静态文本对了不代表装得上 —— dynamic 指向的模块一旦改名,`pip install`
    才会报错,而没人 pip install 的时候这份门面就是张假支票。
    """
    import tempfile

    code, out = _run([
        "-c",
        "import tempfile,os;"
        "from setuptools.build_meta import prepare_metadata_for_build_wheel as p;"
        "d=tempfile.mkdtemp();r=p(d);"
        "t=open(os.path.join(d,r,'METADATA'),encoding='utf-8').read();"
        "print([l for l in t.splitlines() if l.startswith('Version:')])"])
    if code != 0:
        return [f"setuptools 生成元数据失败:退出码 {code}\n      " + (out[-400:] or "")]
    v = re.search(r"Version:\s*([0-9.]+)", out)
    if not v:
        return ["元数据里没有 Version 行"]
    if v.group(1) != version_from_source():
        return [f"元数据算出 {v.group(1)},源码是 {version_from_source()}"]
    return []


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, *args], cwd=str(ROOT),
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    checks = [("版本号一致", check_version),
              ("个人数据三处同步", check_state_targets),
              ("不外发清单自洽", check_ship_never_manifest),
              ("HANDOFF 长度", check_handoff_size),
              ("pyproject 单一事实源", check_pyproject)]
    if "--collect" in sys.argv:
        checks.append(("测试基线条数", check_collected_count))
        checks.append(("元数据真算得出来", check_pyproject_resolves))
    bad = 0
    for label, fn in checks:
        problems = fn()
        print(("  ✓ " if not problems else "  ✗ ") + label)
        for p in problems:
            print("      - " + p)
        bad += len(problems)
    print()
    if bad:
        print(f"FAIL: 文档/数据边界有 {bad} 处对不上")
        return 1
    print("PASS: 版本号、个人数据边界、文档长度、项目元数据全部对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
