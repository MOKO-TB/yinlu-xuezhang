"""文档与数据边界的对账(v0.9.28 A 批)。三件事只要漂了就当场红,不等用户发现。

1. **版本号只有一处真相**:HANDOFF 抬头写的 `v0.9.xx` 必须等于 `minicode.__version__`。
2. **个人数据文件三处同步**:代码里凡是往数据目录写的每一份本机文件(`app_path("x")`
   与 `APP_DIR / "x"`),必须**同时**出现在 `.gitignore` 和 `build_exe.py` 的清理清单里,
   或者写进下面的 `SHIP_INTENTIONALLY` 并给出理由。这一条防的是"把同学自己的
   课表/作息表/提醒记录随安装包发出去"—— v0.9.18~v0.9.27 每轮都靠人肉记得加,
   而这一轮加 `watchdog.log` 时我发现 `weather.json`、`web-sessions/`、
   `web-usage.log` 三份**从来没人加过**,于是把这件事变成脚本。
3. **§2 里程碑只留最近几轮**:更早的整行移到 `docs/handoff/`,主文档才不会长成
   没人读的厚度(读不动的交接文档等于没有交接文档)。

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

# 确实是本机状态,但打包流程另有处理(整目录 rmtree / 只进 exe 内部)。
# 这一份**必须**被扫描器扫得到:扫不到就说明代码里早就不写了,例外条目该删,
# 不然这个字典会变成"检查器红了就往这里加一行"的垃圾桶。
STATE_EXEMPT = {
    "browser-profile": "教务导入的浏览器登录态:build_exe 里整目录 rmtree",
    "sessions": "会话留档:build_exe 里整目录 rmtree(构建机的对话不能跟包走)",
    "config.json": "用户配置:构建机上的这份含模型 Key,由 build_exe 单独删",
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


def check_state_targets() -> list[str]:
    """每一份本机状态:要么 .gitignore + 清理清单都有,要么列进例外且真有打包语句撑腰。"""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    packed = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    for spec in (ROOT / "packaging").glob("*.spec"):
        packed += spec.read_text(encoding="utf-8")
    problems: list[str] = []
    for name, where in sorted(state_targets().items()):
        # gitignore 里目录写 `web-sessions/`、文件常写 `/watchdog.log`,两头都算数
        ignored = re.search(r"(?m)^/?%s/?$" % re.escape(name), ignore) is not None
        if name in SHIP_INTENTIONALLY:
            if name not in packed:
                problems.append(f"{name}:列为「随包发」但 build_exe/打包脚本里没有对应语句")
            continue
        cleaned = f'"{name}"' in packed or f"/{name}\"" in packed or name in packed
        if not ignored or not cleaned:
            problems.append(
                f"{name}({'、'.join(sorted(set(where)))}):"
                + (" 不在 .gitignore" if not ignored else "")
                + (" 不在 build_exe 清理清单" if not cleaned else ""))
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


def main() -> int:
    checks = [("版本号一致", check_version),
              ("个人数据三处同步", check_state_targets),
              ("HANDOFF 长度", check_handoff_size)]
    if "--collect" in sys.argv:
        checks.append(("测试基线条数", check_collected_count))
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
    print("PASS: 版本号、个人数据边界、文档长度三项全部对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
