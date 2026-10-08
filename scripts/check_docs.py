"""文档与数据边界的对账(v0.9.28 A 批)。五件事只要漂了就当场红,不等用户发现。

1. **版本号只有一处真相**:HANDOFF 抬头写的 `v0.9.xx` **和 §2 最新那一行里程碑**都要
   等于 `minicode.__version__`(第二问是 v0.9.132 补的:v0.9.131 那轮只忘了点源码的
   `__version__`,两处一起停在旧值时第一问自洽地绿,包里的 exe 因此带着错版本)。
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
   --collect 额外跑一次 `pytest --collect-only -q`,核对文档写的测试基线条数
   (慢十几秒,提交前跑一次;本轮就是因为没人核,文档里的 1188 和实测的 1142 差着
   46 项漂了一整轮)。这一项要**两边只有一个数且相等**:上一版判的是"实测在文档提到
   过的数里",文档留了历史数时它会绿着放过漂掉的当前基线 —— 历史条数请写成
   「N passed」,别用「全绿 N 项」那个句式。
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

    **扫描集必须含根目录那几个入口**:`gui_main.py` / `main.py` / `web_main.py`
    也在往数据目录写(实测 `gui_warnings.log` 就是 `gui_main.py` 写的),而它们
    不在 `minicode/` 下 —— 只看包内模块的那版扫不到,于是那份日志既不进
    SHIP_NEVER 也不进「我的数据」,打包时没人删它。
    """
    sources = sorted((ROOT / "minicode").glob("*.py")) + [
        ROOT / name for name in ("gui_main.py", "main.py", "web_main.py")
        if (ROOT / name).exists()]
    out: dict[str, list[str]] = {}
    for py in sources:
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


def _milestone_versions(text: str) -> list[str]:
    """HANDOFF §2 那张表里的里程碑版本号(按行序)。表头行 `| 里程碑 | 内容 |` 天然不匹配。"""
    section = text.split("## 2.", 1)
    if len(section) < 2:
        return []
    return re.findall(r"^\| v([0-9.]+) ", section[1].split("\n## ")[0], re.M)


def _ver_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def version_problems(text: str, source: str) -> list[str]:
    """版本对账的判定(拆成纯函数是为了让测试能喂假文本 —— 同 count_problems)。

    两处各自独立地跟着源码比,防的是两种不同的漂法:
    - **抬头 vs 源码**:项目一直在守的那条(对外一句话现状不能说成上一个版本);
    - **§2 最新那一行 vs 源码**:v0.9.131 就是从这里漂的 —— `71ecb39` 写了 §2 的行、
      README 的一行、《使用说明》的第 113 条、冻结针八根,唯独没点 `__version__`,
      于是「文档说 131、装出来的 exe 叫 0.9.130」。当时的闸只比抬头,而那一轮连抬头
      都忘了改,两处一起停在旧值,检查器自洽地绿着 —— 只有「最新里程碑必须等于源码」
      这一条抓得住它。
    """
    problems: list[str] = []
    m = re.search(r"^>\s*更新:[^（(]*[（(]v([0-9.]+)[)）]", text, re.M)
    if not m:
        problems.append("HANDOFF 抬头没找到「更新:YYYY-MM-DD(vX.Y.Z)」这一行")
    elif m.group(1) != source:
        problems.append(f"HANDOFF 抬头写 v{m.group(1)},源码是 v{source}")
    rows = _milestone_versions(text)
    if not rows:
        problems.append("HANDOFF §2 找不到里程碑行(表被改写了?版本对账第二问失效)")
    else:
        newest = max(rows, key=_ver_key)
        if newest != source:
            problems.append(
                f"HANDOFF §2 最新里程碑是 v{newest},源码是 v{source} —— "
                f"这一轮的版本号没跟着涨(或涨了没写行),包里的 exe 会带着错的版本")
    return problems


def check_version() -> list[str]:
    return version_problems((ROOT / "HANDOFF.md").read_text(encoding="utf-8"),
                            version_from_source())


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
    if "## 2." not in text:
        return ["HANDOFF 找不到 §2 里程碑表"]
    rows = _milestone_versions(text)
    if len(rows) > MAX_ROUNDS_KEPT:
        return [f"HANDOFF §2 留了 {len(rows)} 轮里程碑(上限 {MAX_ROUNDS_KEPT}),"
                f"把最老的挪进 docs/handoff/(现在最老的几轮:{'、'.join(rows[MAX_ROUNDS_KEPT:])})"]
    return []


def claimed_counts(text: str) -> set[int]:
    """从 HANDOFF 文本里扫出所有"全绿 N 项 / N 个测试全绿"的条数(纯函数,给测试喂假文本)。"""
    found = {int(n) for n in re.findall(r"全绿\s*(\d{3,5})\s*项", text)}
    return found | {int(n) for n in re.findall(r"(\d{3,5}) 个测试全绿", text)}


def count_problems(actual: int, text: str) -> list[str]:
    """基线条数对账的判定(拆成纯函数是为了让测试能喂它假文本 —— 同 pyproject_problems)。

    这里只准出现**一个**数:上一版判的是 `actual in claimed`,于是文档里留下三个
    历史数时,实测等于其中任何一个都会绿 —— 漂掉的当前基线照样过。
    """
    claimed = claimed_counts(text)
    if not claimed:
        return ["HANDOFF 没写全绿条数(§5 与 §7 上手指南至少各要有一处)"]
    if claimed != {actual}:
        return [f"HANDOFF 写的基线是 {sorted(claimed)},实测 {actual} 项 —— "
                f"历史条数别用「全绿 N 项」这个句式写(改成「N passed」),否则旧数会冒充当前基线"]
    return []


def check_collected_count() -> list[str]:
    """跑一次 collect-only,把文档写的基线数和实际收集数对一遍(慢,可选)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(ROOT), capture_output=True, text=True)
    m = re.search(r"(\d+) tests? collected", proc.stdout or "")
    if not m:
        return [f"pytest --collect-only 没报收集数(退出码 {proc.returncode})"]
    return count_problems(int(m.group(1)),
                          (ROOT / "HANDOFF.md").read_text(encoding="utf-8"))


# ---------------------------------- 子代理档位与数字口径的文档对账(v0.9.83)

_PARALLEL_HINT = ("子代理", "并行")
_LIMIT_RES = (re.compile(r"上限\s*(\d+)(?:\s*→\s*(\d+))?"),
              re.compile(r"最多\s*(\d+)\s*个"))


def doc_sync_problems(types, labels, max_parallel, n_skills,
                      readme, prompt, manual) -> list[str]:
    """活文档(README / system_prompt / 随包《使用说明》)与代码口径对账,纯函数。

    为什么要有这道闸:v0.9.81/82 两轮撞到的漂移全是**同一个形状** —— 代码改了、
    文档没跟上,而没有任何一处会报错:《使用说明》只写四个领域档(实际六个)、
    "skills/ 里那 22 份技能"(实测 21)、README 还写着"上限 4 个"(v0.9.60 起是 6,
    漂了八个版本)。`audit` 那档子代理能人肉查这件事,但"能查"和"每次都查"是两回事
    —— 闸放在提交前,漂移当场红,比派一路子代理事后翻仓库便宜。

    三条规则都刻意收窄,免得把历史叙述当成当前口径:
    ①**档位覆盖**:除 `auto` 外每个档位名(或它的中文标签)必须在这三份活文档里出现过;
    ②**技能份数**:只认《使用说明》里那句总数(README 的"7 份技能"是历史句,不算总数);
    ③**并行上限**:只在提到子代理/并行的行里查「上限 N」「上限 A→B」「最多 N 个」,
      箭头形式取**后一个数**(那是"从 4 放到 6"的意思);附件那行的「上限 20 MiB」
      不归这里管。
    """
    problems: list[str] = []
    docs = {"README.md": readme, "system_prompt.md": prompt, "《使用说明》": manual}

    for key in types:
        if key == "auto":
            continue
        alias = str(labels.get(key) or "")
        for name, text in docs.items():
            if key not in text and (not alias or alias not in text):
                problems.append(f"档位 {key}({alias or '无中文名'})"
                                f"在 {name} 里一次都没提")

    for m in re.finditer(r"(\d+)\s*份技能", manual):
        n = int(m.group(1))
        if n != n_skills:
            problems.append(f"《使用说明》写「{n} 份技能」,"
                            f"实测 skills/ 下是 {n_skills} 份")

    for name, text in docs.items():
        for line in text.splitlines():
            if not any(h in line for h in _PARALLEL_HINT):
                continue
            for rx in _LIMIT_RES:
                for mm in rx.finditer(line):
                    nums = [int(g) for g in mm.groups() if g]
                    if not nums:
                        continue
                    # 取最后一个数:「上限 4→6」的意思是"从 4 放到了 6",当前值是 6
                    got = nums[-1]
                    if got != max_parallel:
                        problems.append(
                            f"{name} 写「{mm.group(0).strip()}」,现在的上限是 "
                            f"{max_parallel}:" + line.strip()[:70])
    return problems


def _manual_text() -> str:
    """从 scripts/build_exe.py 里抠出《使用说明》那段字面量。

    不整份读:脚本自己也有一堆数字,整份扫会把无关数字算进口径。区域定位手法与
    `tests/test_manual_entries.py` 一致(第一条起到 write_text 的 encoding 终止符)。
    """
    src = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    start = src.find('\n        "1. ')
    end = src.find('",\n        encoding="utf-8"', start)
    return src[start:end] if start > 0 and end > start else ""


# 「内置 N 工具」这类**当前态**数字:两种词序都要认(HANDOFF 写"内置 56 工具",
# README 写"56 个内置工具"),只认一种就等于给漂留了一个口子。
_TOOL_COUNT_PATTERNS = (r"内置\s*(\d+)\s*(?:个)?工具", r"(\d+)\s*个内置工具")


def tool_count_problems(n_tools: int, current_texts: "list[tuple[str, str]]") -> list[str]:
    """每一处写「内置 N 工具」的**当前态**文本,都必须等于注册表实测条数。

    单独成一个函数、并且只吃调用方递来的 (出处, 文本) 对,是为了能被喂假文本测:
    工具数每加一只就会变一次,而这些句子是**当前态**描述,不是历史 ——
    历史行(§2 及以后、CHANGELOG)不许被改,所以那些文本根本不该递进来。
    """
    problems: list[str] = []
    for where, text in current_texts:
        for pat in _TOOL_COUNT_PATTERNS:
            for m in re.finditer(pat, text):
                got = int(m.group(1))
                if got != n_tools:
                    problems.append(f"{where} 写「{m.group(0)}」,注册表实测 {n_tools} 个")
    return problems


def _handoff_section_1() -> str:
    """HANDOFF §1 那一段(当前态一句话),只有它里面的数字算"当前口径"。"""
    text = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    m = re.search(r"^## 1\.(.*?)(?=^## 2\.)", text, re.S | re.M)
    return m.group(1) if m else ""


def check_agent_doc_sync() -> list[str]:
    """提交前对账:代码里的档位/上限/技能数/工具数,活文档是不是也这么说。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from minicode import router
        from minicode.subagents import (MAX_PARALLEL_TASKS, SUBAGENT_TYPES,
                                        TYPE_KEYS)
        from minicode.tools.registry import build_default_registry
    except Exception as e:                 # 源码读不到不该由对账闸顶罪
        return [f"读不到子代理档位口径({type(e).__name__}: {e})"]
    labels = {k: v.label for k, v in SUBAGENT_TYPES.items()}
    labels.update({k: v.label for k, v in router.DOMAINS.items()})
    n_skills = len(list((ROOT / "skills").glob("*/SKILL.md")))
    problems = doc_sync_problems(
        list(TYPE_KEYS), labels, MAX_PARALLEL_TASKS, n_skills,
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "system_prompt.md").read_text(encoding="utf-8"),
        _manual_text())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    problems += tool_count_problems(len(build_default_registry()),
                                    [("HANDOFF §1", _handoff_section_1()),
                                     ("README", readme)])
    return problems


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


def _utf8_console() -> None:
    """这道闸自己打印 ✓/✗。Windows 默认控制台是 GBK,print 直接 UnicodeEncodeError ——
    照 CONTRIBUTING「不带 PYTHONIOENCODING 跑」的写法执行就是 traceback,等于没有这道闸。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass          # 被重定向/没有 reconfigure 的输出流:原样让它去


def main() -> int:
    _utf8_console()
    checks = [("版本号一致", check_version),
              ("个人数据三处同步", check_state_targets),
              ("不外发清单自洽", check_ship_never_manifest),
              ("HANDOFF 长度", check_handoff_size),
              ("档位与数字口径同步", check_agent_doc_sync),
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
