"""「我的数据」:本机数据一览 + 迁移包的导出/导入(v0.9.30,C 批)。

为什么需要它:同学问的第一句话经常是"我的东西到底存在哪、换台电脑还在不在"。
数据本来就散在两个地方 —— `paths.APP_DIR`(exe 旁或 `%LOCALAPPDATA%\\引路学长`)
里十几个文件,加上 `~/.yinluxs/`(密钥 + 跨项目记忆)。看不见就没法管,更没法迁移。

三条硬规矩(和课表那套"没写钟点就绝不编钟点"同源):

1. **两道闸,不是一道**。`TABLE` 是白名单:没在里面登记的 path 一个字节都进不了包。
   `deny_reason()` 是黑名单:`credentials.json`、`config.local.json`、
   `browser-profile/`(教务登录态)命中即 skip。**为什么要两道** —— 只留白名单,
   下一个人往 TABLE 里加数据文件时顺手写进 `credentials.json` 就漏了;只留黑名单,
   下一批新增的 `mood.json` 挨着缓存文件放、被误归类就漏了。两道闸各自有测试。
   面板上这三个是**看得见**的(告诉你密钥在哪、为什么不带走),只是永远不进包。
2. **config.json 导出前洗 Key**。它是唯一"既是配置又可能被填了 Key"的文件:
   `providers[].api_key`、任何层的 `api_key/key/token/secret/password/authorization`
   一律清空;写成 `${ENV_NAME}` 形式的(见 config.example.json 的 MCP 示例)**保留**,
   因为那本来就是占位而非密钥。洗掉几条记进清单,新机器上重填一次是有意的。
3. **导入绝不覆盖比它新的本机数据**,而且是**纯追加**:磁盘上不会有任何文件因为
   导入被删除。要替换的(包比本机新)先整份复制进 `migrate-backup/<时间戳>/`,
   只留最近 3 份。判定用 zip 里记的原始 mtime 和本机 mtime 比 —— 留 2 秒容差,
   因为 zip 的 `date_time` 是 DOS 秒级(甚至 2 秒粒度)取整的。

工作区里的东西(`memory/`、`plans/`、`uploads/`)、`token-usage.db`(SQLite 常驻
连接在 Windows 上有文件锁)、各类日志与缓存**都不在包内**:面板逐条写明原因。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from minicode import config as config_mod
from minicode import paths

FORMAT = "yinluxs-migration"
FORMAT_VERSION = 1           # 只增不减:importer 遇到比它大的版本直接拒
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "files/"    # zip 内布局:files/<root>/<key>
BACKUP_DIR = "migrate-backup"    # 导入前的原件备份(必须三处登记:gitignore/清理清单/本表)
BACKUP_KEEP = 3
MTIME_TOLERANCE = 2.0            # 秒;zip 存不了亚秒时间戳
MAX_PACK_BYTES = 2 * 1024 ** 3   # 超过就是不导:"这包发出去自己都打不开"
SCRUB_KEYS = ("api_key", "apikey", "key", "token", "access_token",
              "secret", "password", "authorization")

# ------------------------------------------------------------------ 数据分类

PERSONAL = "personal"
PREF = "pref"
STATS = "stats"
CACHE = "cache"
LOG = "log"
NEVER = "never"

CATEGORY_META: dict[str, dict[str, str]] = {
    PERSONAL: {"label": "我的内容", "hint": "你自己填进去/学长替你记下的东西,换机器最想带走"},
    PREF: {"label": "设置与偏好", "hint": "模型配置、提醒开关、关窗偏好——密钥不在这里"},
    STATS: {"label": "统计", "hint": "token 用量,跟着这台机器走"},
    CACHE: {"label": "缓存", "hint": "删了会自动重新生成,不占迁移包的位置"},
    LOG: {"label": "日志与留档", "hint": "排查问题用的现场,含本机问答内容,不外发"},
    NEVER: {"label": "永不导出的东西",
            "hint": "密钥与教务登录态:学长压根不提供「带上它们」的选项"},
}


@dataclass(frozen=True)
class Entry:
    """一份本机数据的**声明**:它是什么、在哪、能不能进包。

    `key` 用正斜杠写(和 zip 内路径同形),`root` 决定挂在哪个目录下:
    "app" = `paths.app_path()`,`home` = `~/.yinluxs`(`config.CREDENTIALS_PATH`
    的父目录 —— home 下**只允许精确点名**,绝不整目录扫,否则密钥会被顺走)。
    `kind`:file 单文件 / family 目录里按 pattern 挑 / dir 整个目录只报状态。
    """

    key: str
    label: str
    category: str
    pack: bool
    root: str = "app"
    kind: str = "file"
    pattern: str = ""
    note: str = ""
    restart: bool = False
    # W2(v0.9.99):数据归属 —— "workspace"=每个工作区各一份(切换工作区看到的是
    # 那边的),"global"=全局一份(课表/成绩这类校园数据,与代码仓库无关)。
    # 空串 = 不适用(留档/日志/缓存没有"归属"一说)。面板逐行带出,写明而不靠猜。
    scope: str = ""


TABLE: tuple[Entry, ...] = (
    # ---- 我的内容
    Entry("checkpoints", "改动前像(撤销用)", PERSONAL, False, kind="family",
          pattern="*.json",
          note="/undo 与 /rewind 的账本:存的是文件被改之前的原文 —— 换一台机器拿去退没有意义还可能退错,所以**永不进迁移包**"),
    Entry("shadow-git", "影子 git 库(整树检查点)", PERSONAL, False, kind="family",
          pattern="*",
          note="H 的检查点:能盖住 bash/py_script 间接改过的文件 —— 里面是工作区每一份文件的原文,绝不外发、永不进迁移包"),
    Entry("sessions", "聊天记录", PERSONAL, True, kind="family", pattern="*.jsonl",
          note="每一次对话的留档;包太大可以在导出时不勾它"),
    Entry("memory/MEMORY.md", "长期记忆(跨项目)", PERSONAL, True, root="home",
          scope="global",
          note="学长记住的你的偏好,换机器靠它才不用重新介绍自己"),
    Entry("schedule.json", "我的课表", PERSONAL, True, restart=True,
          scope="global",
          note="导入教务或手填的课表"),
    Entry("schedule.prev.json", "上一版课表", PERSONAL, False,
          note="只为「换回上一版」存在,换机器用不上", scope="global"),
    Entry("scores.json", "成绩存档", PERSONAL, True,
          scope="global",
          note="从教务导入的正式成绩,绩点从这里算"),
    Entry("skill-usage.jsonl", "技能使用账", PERSONAL, False,
          note="每次调技能记一条;哪些技能真有人用,从这里看"),
    Entry("period-times.json", "本机作息表", PERSONAL, True, restart=True,
          scope="global",
          note="每节上下课钟点,空档看板按它算"),
    Entry("reminders.json", "待办与截止日期", PERSONAL, True, restart=True,
          note="「提醒我 X 号交材料」记在这里"),
    Entry("mood.json", "情绪日记", PERSONAL, False,
          note="一天一条的自己。**刻意不进迁移包**:这是整个 app 里最私密的一份,"
               "不该顺手躺在一个可能被打进压缩包、发到云盘的「搬家文件」里;"
               "要带去新机器请用情绪面板的「导出」自己存、自己拷"),
    Entry("kb/reviewed.json", "人工核实过的知识", PERSONAL, True,
          note="你在候选面板点过「并入」的内容"),
    Entry("campus-choices.json", "地图歧义答案", PERSONAL, True,
          note="你在「A栋」这类说法上亲口认过指哪栋,下次不用再问"),
    Entry("skills", "自己添加的技能", PERSONAL, True, kind="family", pattern="*.md",
          note="技能面板里你自己写的提示词"),
    Entry("kb-gaps.jsonl", "没答上来的问题", PERSONAL, False,
          note="本机成长日志,新机器重新积累即可"),
    Entry("kb-candidates.jsonl", "候选知识", PERSONAL, False,
          note="本机成长日志,不随包外发"),
    Entry("message-feedback.jsonl", "回答的 👍/👎 反馈", PERSONAL, False,
          note="本机成长日志,含对话摘要"),
    Entry("kb-hits.jsonl", "知识库检索命中账", PERSONAL, False,
          note="问句 + 命中的条目 id,供评价绑条目;含本机提问记录"),
    Entry("perms-trust.json", "项目白名单与指令文件的本机批准记录", PERSONAL, False,
          note="工作区绝对路径 + 内容指纹;换机器带走等于替那台机器上的项目批了权限、"
               "也替它采纳过别人写的 AGENTS.md"),
    # ---- 设置与偏好
    Entry("config.json", "模型与权限配置", PREF, True, restart=True,
          note="导出前会洗掉里面的 API Key,新机器要重填一次"),
    Entry("workspaces.json", "工作区列表", PREF, True, restart=True,
          scope="global",
          note="里面是本机绝对路径,新机器认不出来时重选一次目录就行"),
    Entry("notify-pref.json", "桌面提醒开关", PREF, True, restart=True,
          note="每一类提醒各自开在哪一档(类别表只有一份:tray.NOTIFY_KINDS — 这里不写「几类」,加一类这种数就会漂)"),
    Entry("close-pref.json", "关窗时怎么办", PREF, True),
    Entry("monitor-pref.json", "任务监控栏开合", PREF, True,
          note="这一栏展开还是收起、哪几段被折叠过 —— 纯界面偏好,不含任务内容"),
    Entry("pricing-override.json", "费用估算改价", PREF, True,
          note="只存与默认价不同的行(USD/每百万 token);删掉即回默认价目。"
               "改完在下一次统计时生效,不用重启"),
    Entry("jobs-pref.json", "定时任务开关", PREF, True,
          note="被关掉的那几个不再自动跑;这是界面偏好,跑过的账在 jobs-state.json"),
    Entry("jobs-user.json", "我的定时任务", PERSONAL, True, restart=True,
          note="你自己在「自动化」面板里加的那几条:名字、节奏、到点要跑的那句话。"
               "换电脑默认带走(与待办同一条);删掉这个文件就全没了"),
    Entry("onboarding.json", "向导「不再显示」", PREF, True),
    Entry("weather.json", "常驻城市", PREF, True,
          note="首页天气 chip 按这个城市查"),
    Entry("notice-watch.json", "官网通知看门狗", PREF, True,
          note="开关 + 已通知过的标题(所以同一批通知不会二次打扰)"),
    Entry("AGENTS.md", "我的工程规矩(用户级指令)", PREF, True, scope="global",
          note="存在就每轮自动进系统提示词:注释密度、回复格式这类按你的习惯来。"
               "沙箱边界、敏感路径硬禁、确认门不吃这一份,任何指令文件都改不动它们"),
    Entry("agents", "我的档位定义(agents/*.md)", PREF, True, kind="family", pattern="*.md",
          note="自己加的子代理档:`base:` 指要收窄的那一档,正文是这一档多出来的规矩。"
               "只能往窄里改(工具子集、只读只增不减、步数只减不增),提权字段装载时"
               "一律撕掉并说给你听 —— 换电脑带走的是口径,不带也只是一档都没有"),
    # ---- 统计
    Entry("token-usage.db", "token 用量库", STATS, False,
          note="SQLite 常驻连接在 Windows 上会锁文件,统计也跟着机器走,不进包"),
    # ---- 缓存
    Entry("model_caps.json", "模型能力探测结果", CACHE, False,
          note="删了下次自己探"),
    Entry("weather-cache.json", "天气缓存", CACHE, False, note="几分钟就过期"),
    Entry("pushed-today.json", "今天弹过哪些提醒", CACHE, False,
          note="带去新机器会把它的第一条补发误判成「已弹过」"),
    Entry("jobs-state.json", "定时任务上次何时跑", CACHE, False,
          note="周期任务的账本(周复盘/缺口周报那类);带去新机器会误判成「这周已跑过」"),
    Entry("activate-port.json", "通知激活监听端口", CACHE, False,
          note="点通知直达用的端口;换机器/重启后由新实例重写"),
    # ---- 日志与留档
    Entry("tray.log", "托盘与通知日志", LOG, False),
    Entry("gui_warnings.log", "界面警告日志", LOG, False,
          note="界面本身不对时看的就是它(原生右键菜单/WebView2 就绪);只记发生了什么,不记对话内容"),
    Entry("watchdog.log", "提醒看门狗日志", LOG, False,
          note="只记结论,不记正文;截图发给维护者看的就是它"),
    Entry("injection-events.log", "上下文注入日志", LOG, False),
    Entry("web-daily.json", "网页版每日额度账本", LOG, False,
          note="今天网页版用了多少发问额度;带去别人机器会把他的额度误判成已用"),
    Entry("web-usage.log", "网页版问答记录", LOG, False,
          note="含发问内容与来源 IP,只留在本机"),
    Entry("web-sessions", "网页版会话留档", LOG, False, kind="family",
          pattern="*.jsonl", note="同学问的话记在这里,不随包外发"),
    Entry(BACKUP_DIR, "导入前的原件备份", LOG, False, kind="dir",
          note="每次导入要替换本机文件前先复制一份留在这里,只保最近 3 份"),
    # ---- 永不导出(面板看得见,代码进不去)
    Entry("credentials.json", "API Key", NEVER, False, root="home",
          note="密钥不进包:换机器请在新机器的「模型设置」里重填一次"),
    Entry("config.local.json", "内置模型 Key", NEVER, False,
          note="同上:它是构建机注入的凭据,不是你的数据"),
    Entry("browser-profile", "教务登录态", NEVER, False, kind="dir",
          note="里面是登录后的 cookie:带走 = 把账号交给别人"),
)

_BY_KEY: dict[tuple[str, str], Entry] = {(e.root, e.key): e for e in TABLE}


# ---------------------------------------------------------------------- 路径与闸


def _root_dir(root: str) -> Path:
    """根目录**每次调用时**解析:测试靠 monkeypatch `paths.APP_DIR` /
    `config.CREDENTIALS_PATH` 指到临时目录,import 期绑死就会静默打不中。"""
    if root == "home":
        return Path(config_mod.CREDENTIALS_PATH).parent
    return paths.app_path()


def entry_path(entry: Entry, *extra: str) -> Path:
    return _root_dir(entry.root).joinpath(entry.key, *extra)


def deny_reason(path: Path) -> str:
    """第二道闸:哪怕将来有人把这几样写进了 TABLE,包也带不走它们。"""
    try:
        parts = [p.lower() for p in Path(path).resolve().parts]
    except OSError:
        parts = [p.lower() for p in Path(path).parts]
    if "credentials.json" in parts:
        return "API Key 存放处:密钥永不进包"
    if "config.local.json" in parts:
        return "内置模型 Key:密钥永不进包"
    if "builtin_key.json" in parts:
        return "内置模型 Key(v0.9.105 迁出的新家):密钥永不进包"
    if "browser-profile" in parts:
        return "教务登录态 cookie:登录态永不进包"
    return ""


def _files_of(entry: Entry) -> list[tuple[str, Path]]:
    """这个条目实际要进包的文件 [(zip 内 key, 绝对路径)];没有文件就是空表。

    `home` 根只走 `kind="file"` 的精确点名,所以 `~/.yinluxs` 下的其它东西
    (credentials.json 就在隔壁)连"被枚举"都不会发生。
    """
    if entry.kind == "dir":
        return []
    base = entry_path(entry)
    if entry.kind == "family":
        if not base.is_dir():
            return []
        try:
            rows = sorted(p for p in base.iterdir()
                          if p.is_file() and fnmatch.fnmatch(p.name, entry.pattern))
        except OSError:
            return []
        return [(f"{entry.key}/{p.name}", p) for p in rows]
    return [(entry.key, base)] if base.is_file() else []


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, names in os.walk(path):
            for n in names:
                total += _size(Path(root) / n)
    except OSError:
        pass
    return total


def _count_lines(path: Path) -> int | None:
    """jsonl 的行数(=条数);读不动就不报数,不报 0 骗人。"""
    try:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


# ------------------------------------------------------------------ 面板数据


def inventory(workspace: str = "") -> dict:
    """「我的数据」面板的 payload:数据在哪 + 每类有哪些文件(只读,不改任何东西)。

    缺文件不是错误:`exists=False` 如实报出来,面板据此显示"还没生成过"。
    `workspace` 由桥传进来(它才知道当前会话真正在哪个目录),这里不猜。
    """
    info = paths.diagnostics()
    groups: list[dict] = []
    totals = {"bytes": 0, "pack_bytes": 0, "pack_files": 0, "files": 0}
    warnings: list[str] = []
    for cat in (PERSONAL, PREF, STATS, CACHE, LOG, NEVER):
        meta = CATEGORY_META[cat]
        items: list[dict] = []
        for entry in TABLE:
            if entry.category != cat:
                continue
            base = entry_path(entry)
            files = _files_of(entry) if entry.category not in (NEVER,) else []
            if entry.kind == "dir":
                exists = base.is_dir()
                nbytes = _dir_bytes(base) if exists else 0
                count = None
                if exists:
                    try:
                        count = sum(len(ns) for _r, _d, ns in os.walk(base))
                    except OSError:
                        count = None
            elif entry.kind == "family":
                exists = bool(files) or base.is_dir()
                nbytes = sum(_size(p) for _k, p in files)
                count = len(files) if base.is_dir() else None
            else:
                exists = base.is_file()
                nbytes = _size(base) if exists else 0
                count = _count_lines(base) if (exists and entry.key.endswith(".jsonl")) \
                    else None
            totals["bytes"] += nbytes
            if exists:
                totals["files"] += 1
            in_pack = bool(entry.pack and files)
            if in_pack:
                totals["pack_bytes"] += nbytes
                totals["pack_files"] += len(files)
            items.append({
                "key": entry.key,
                "label": entry.label,
                "scope": entry.scope,
                "path": str(base),
                "exists": exists,
                "bytes": nbytes,
                "size": _fmt_bytes(nbytes),
                "count": count,
                "pack": in_pack,
                "note": entry.note,
            })
        if items:
            groups.append({"category": cat, "label": meta["label"],
                           "hint": meta["hint"], "items": items})
    if not info.get("writable"):
        warnings.append("这个数据目录写不进去(多半是装进了 Program Files):"
                        "保存设置会失败,迁移包也仍然能导,只是新机器要重配一次")
    return {
        "ok": True,
        "app_dir": str(info.get("app_dir") or ""),
        "backend": info.get("backend") or "",
        "writable": bool(info.get("writable")),
        "workspace": str(workspace or ""),
        "home_dir": str(_root_dir("home")),
        "groups": groups,
        "totals": {**totals, "size": _fmt_bytes(totals["bytes"]),
                   "pack_size": _fmt_bytes(totals["pack_bytes"])},
        "warnings": warnings,
        "excluded_labels": [
            {"label": e.label, "why": e.note or "不属于迁移范围"}
            for e in TABLE if not e.pack and e.category in (NEVER, STATS)],
        "app_version": _app_version(),
    }


def _app_version() -> str:
    try:
        from minicode import __version__
        return __version__
    except Exception:
        return "?"


def _packable(include_sessions: bool = True) -> list[Entry]:
    out = []
    for entry in TABLE:
        if not entry.pack:
            continue
        if entry.category not in (PERSONAL, PREF):
            continue          # 白名单之外再兜一层:统计/缓存/日志永不进包
        if not include_sessions and entry.key in ("sessions", "memory/MEMORY.md"):
            continue
        out.append(entry)
    return out


def pack_plan(include_sessions: bool = True) -> list[tuple[Entry, str, Path]]:
    """(条目, zip 内 key, 绝对路径) 的有序清单:导出与"将要导哪些"的预估共用它。"""
    rows: list[tuple[Entry, str, Path]] = []
    for entry in _packable(include_sessions):
        for key, path in _files_of(entry):
            rows.append((entry, key, path))
    rows.sort(key=lambda r: (r[0].root, r[1]))
    return rows


# ------------------------------------------------------------- config.json 洗 Key


_SECRET_WORDS = {"key", "apikey", "api", "token", "secret", "password",
                 "authorization", "auth", "credential"}


def _is_secret_field(name: str) -> bool:
    """字段名是不是密钥类:`api_key`、`X-API-Key`、`access_token` 都算,
    但 `${MY_TOKEN}` 这种**值**是环境变量占位,由调用方另判。"""
    low = str(name or "").strip().lower()
    if low in SCRUB_KEYS:
        return True
    return any(part in _SECRET_WORDS for part in re.split(r"[_\-\s.]", low) if part)


def _is_env_placeholder(value) -> bool:
    """`${MY_MCP_TOKEN}`:config.example.json 教用户这么写,发请求时才展开,
    压根不是明文密钥 —— 清掉它只会让新机器少一段能用的配置。"""
    text = str(value or "").strip()
    return text.startswith("${") and text.endswith("}") and len(text) > 3


def _scrub_config(raw: bytes) -> tuple[bytes, int]:
    """把 config.json 里的密钥字段清空,返回 (内容, 清掉几处)。

    解析不了就**整份不要**(宁可缺配置,也不能把可能的密钥带出去):
    调用方拿到 -1 会把这个文件记进 skipped。
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return b"", -1
    n = 0

    def walk(node) -> None:
        nonlocal n
        if isinstance(node, dict):
            for k in list(node.keys()):
                if _is_secret_field(k):
                    value = node.get(k)
                    if value and not _is_env_placeholder(value):
                        node[k] = ""
                        n += 1
                else:
                    walk(node[k])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), n


# ------------------------------------------------------------------ 导出


def _drop_tmp(tmp: "Path | None") -> None:
    """失败收尾:别让半个 .tmp 包留在用户目录里(删不掉也不报错盖住原话)。"""
    if tmp is None:
        return
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def export_package(dest, include_sessions: bool = True) -> dict:
    """把「我的内容 + 设置」打成一个迁移包;返回逐条结果,任何失败都不抛。

    `dest` 是**目标 zip 路径**(由桥的"另存为"决定,绝不从前端拿路径)。
    整包先写 `.tmp` 再 `os.replace`:失败不会留下半个包。
    """
    target = Path(dest)
    plan = pack_plan(include_sessions)
    denied: list[dict] = []
    rows: list[tuple[Entry, str, Path]] = []
    total = 0
    for entry, key, path in plan:
        reason = deny_reason(path)
        if reason:
            denied.append({"key": key, "reason": reason})
            continue
        total += _size(path)
        rows.append((entry, key, path))
    if not rows:
        return {"ok": False, "msg": "没有可导出的数据(课表、待办、记忆都还是空的)"}
    if total > MAX_PACK_BYTES:
        return {"ok": False,
                "msg": f"要导的内容约 {_fmt_bytes(total)},超过 {MAX_PACK_BYTES // 1024 ** 3} GB "
                       "不导 —— 这么大的包自己也发不出去。导出时取消「包含聊天记录」再试"}
    files: list[dict] = []
    skipped: list[dict] = []
    scrubbed = 0
    tmp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry, key, path in rows:
                arc = f"{PAYLOAD_PREFIX}{entry.root}/{key}"
                try:
                    st = path.stat()
                    shipped: bytes | None = None
                    if key == "config.json":
                        content, n = _scrub_config(path.read_bytes())
                        if n < 0:
                            skipped.append({"key": key, "reason": "配置读不懂,不导出"})
                            continue
                        scrubbed += n
                        shipped = content
                        info = zipfile.ZipInfo(arc, date_time=time.localtime(
                            st.st_mtime)[:6])
                        info.compress_type = zipfile.ZIP_DEFLATED
                        zf.writestr(info, content)
                    else:
                        zf.write(path, arc)
                    files.append({"key": key, "root": entry.root,
                                  "category": entry.category,
                                  "bytes": len(shipped) if shipped is not None
                                  else st.st_size,
                                  "sha256": hashlib.sha256(shipped).hexdigest()
                                  if shipped is not None else _sha256(path),
                                  "mtime": round(st.st_mtime, 3)})
                except OSError as e:
                    skipped.append({"key": key, "reason": f"读不出来({type(e).__name__})"})
            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "app_version": _app_version(),
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "state_backend": paths.diagnostics().get("backend") or "",
                "include_sessions": bool(include_sessions),
                "scrubbed_secret_fields": scrubbed,
                "files": files,
                "skipped": skipped,
                "notes": [
                    "密钥与教务登录态不在包内:credentials.json、config.local.json、browser-profile/",
                    "config.json 里的 API Key 已清空,新机请重填",
                    "token 用量、缓存、日志与网页版留档不在包内",
                ],
            }
            zf.writestr(MANIFEST_NAME,
                        json.dumps(manifest, ensure_ascii=False, indent=1))
        tmp.replace(target)
    except OSError as e:
        _drop_tmp(tmp)
        return {"ok": False, "msg": f"写入失败:{type(e).__name__}"}
    except Exception as e:                   # zip 内部异常也不能留半个包
        _drop_tmp(tmp)
        return {"ok": False, "msg": f"打包失败:{type(e).__name__}"}
    return {"ok": True, "path": str(target), "files": len(files),
            "bytes": _size(target), "size": _fmt_bytes(_size(target)),
            "content_bytes": total, "content_size": _fmt_bytes(total),
            "skipped": skipped + denied,
            "scrubbed": scrubbed,
            "excluded": [d["key"] for d in denied],
            "app_version": manifest["app_version"]}


# ------------------------------------------------------------------ 导入


_lock = threading.Lock()
_previews: dict[str, dict] = {}     # nonce -> {path, rows, created_at}
_PREVIEW_TTL = 900                  # 预览 15 分钟有效:过期就重新选一次文件


def _key_is_safe(key: str) -> bool:
    """zip 里的 key 会变成本机路径,所以先把它当**外来输入**看:
    绝对路径、盘符、`..`、反斜杠一律拒(经典 zip-slip)。"""
    if not key or key.startswith("/") or "\\" in key or ":" in key:
        return False
    return all(part not in ("", ".", "..") for part in key.split("/"))


def _entry_for(root: str, key: str) -> Entry | None:
    """白名单解析:精确命中,或落在某个 family 条目的目录+pattern 里。"""
    entry = _BY_KEY.get((root, key))
    if entry is not None:
        return entry if entry.kind == "file" else None
    head, _, tail = key.partition("/")
    if not tail or "/" in tail:
        return None
    candidate = _BY_KEY.get((root, head))
    if candidate is not None and candidate.kind == "family" and \
            fnmatch.fnmatch(tail, candidate.pattern):
        return candidate
    return None


def read_package(src) -> dict:
    """校验一个迁移包并给出**逐条决定**,但一个字都不写。

    拒绝顺序:不是 zip → 没有/坏 manifest → 格式不认识 → 版本比本机新 →
    单个文件不安全或校验不过(只拒那一件)。
    """
    path = Path(src)
    if not path.is_file():
        return {"ok": False, "msg": "找不到这个文件"}
    if not zipfile.is_zipfile(path):
        return {"ok": False, "msg": "这不是迁移包(不是 .zip 文件)"}
    try:
        with zipfile.ZipFile(path) as zf:
            if MANIFEST_NAME not in zf.namelist():
                return {"ok": False, "msg": "包里没有 manifest.json,不敢用"}
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"ok": False, "msg": "包里的清单读不懂:文件可能已损坏"}
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        return {"ok": False, "msg": "这不是引路学长的迁移包"}
    try:
        pack_version = int(manifest.get("format_version") or 0)
    except (TypeError, ValueError):
        pack_version = 0
    if pack_version > FORMAT_VERSION:
        return {"ok": False,
                "msg": f"这份迁移包来自更新的学长格式(v{pack_version} > v{FORMAT_VERSION}),"
                       "请先升级本机版本再导入"}
    rows: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for item in manifest.get("files") or []:
            raw_key = str(item.get("key") or "")
            root = str(item.get("root") or "app")
            arc = f"{PAYLOAD_PREFIX}{root}/{raw_key}"
            row = {"key": raw_key, "root": root,
                   "label": raw_key.rsplit("/", 1)[-1],
                   "bytes": int(item.get("bytes") or 0)}
            if root not in ("app", "home") or not _key_is_safe(raw_key):
                row.update(action="blocked", reason="路径不安全,已拒绝")
                rows.append(row)
                continue
            entry = _entry_for(root, raw_key)
            if entry is None:
                row.update(action="blocked", reason="不认识这个数据项(本机版本可能太旧)")
                rows.append(row)
                continue
            reason = deny_reason(entry_path(entry, *raw_key.split("/")[1:])
                                 if entry.kind == "family" else entry_path(entry))
            if reason:
                row.update(action="blocked", reason=reason)
                rows.append(row)
                continue
            row["label"] = entry.label if entry.kind == "file" else raw_key
            row["restart"] = bool(entry.restart)
            if arc not in names:
                row.update(action="blocked", reason="清单说有,包里却没有这个文件")
                rows.append(row)
                continue
            local = _root_dir(root).joinpath(*raw_key.split("/"))
            try:
                st = local.stat()
                local_mtime, local_bytes = st.st_mtime, st.st_size
            except OSError:
                local_mtime, local_bytes = None, None
            if local_mtime is not None and \
                    local_mtime > float(item.get("mtime") or 0) + MTIME_TOLERANCE:
                row.update(action="kept-newer",
                           reason="本机这份比包里更新,保留本机的(要覆盖请先在面板里确认)",
                           local_bytes=local_bytes)
                rows.append(row)
                continue
            row.update(action="written" if local_mtime is None else "replaced",
                       pack_mtime=float(item.get("mtime") or 0),
                       sha256=str(item.get("sha256") or ""))
            rows.append(row)
    counts = {k: sum(1 for r in rows if r["action"] == k)
              for k in ("written", "replaced", "kept-newer", "blocked")}
    return {"ok": True, "path": str(path), "manifest": manifest,
            "from_version": str(manifest.get("app_version") or "?"),
            "created_at": str(manifest.get("created_at") or ""),
            "rows": rows, "counts": counts,
            "bytes": sum(int(r.get("bytes") or 0)
                         for r in rows if r["action"] in ("written", "replaced"))}


def import_pick(src) -> dict:
    """选包 + 给预览(带 nonce),**不写盘**。真正落盘要另一次显式确认。"""
    result = read_package(src)
    if not result.get("ok"):
        return result
    nonce = f"p{int(time.time() * 1000):x}{os.getpid():x}"
    with _lock:
        for stale in [k for k, v in _previews.items()
                      if time.time() - v.get("created", 0) > _PREVIEW_TTL]:
            _previews.pop(stale, None)
        _previews[nonce] = {"path": result["path"], "created": time.time(),
                            "rows": result["rows"]}
    preview = [{k: v for k, v in r.items() if k not in ("sha256",)}
               for r in result["rows"]]
    return {"ok": True, "nonce": nonce, "path": result["path"],
            "from_version": result["from_version"], "created_at": result["created_at"],
            "rows": preview, "counts": result["counts"], "size": _fmt_bytes(result["bytes"]),
            "expires_in": _PREVIEW_TTL}


def _backup_root(stamp: str) -> Path:
    return paths.app_path(BACKUP_DIR, stamp)


def _prune_backups() -> None:
    root = paths.app_path(BACKUP_DIR)
    if not root.is_dir():
        return
    try:
        keep = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    except OSError:
        return
    for old in keep[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


def import_apply(nonce: str) -> dict:
    """按预览确认落盘:纯追加 + 替换前先备份 + 逐条校验 sha256。"""
    with _lock:
        preview = _previews.get(str(nonce or ""))
    if not preview:
        return {"ok": False, "msg": "预览已过期,请重新选一次迁移包"}
    src = Path(preview["path"])
    result = read_package(src)             # 落盘前**再判一次**新旧:预览到确认之间本机可能变了
    if not result.get("ok"):
        _previews.pop(str(nonce), None)
        return result
    fresh = {r["key"] + "|" + r["root"]: r for r in result["rows"]}
    report = {"written": [], "replaced": [], "kept_newer": [], "blocked": []}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = _backup_root(stamp)
    restart_needed = False
    try:
        with zipfile.ZipFile(src) as zf:
            for row in preview["rows"]:
                key, root = row["key"], row["root"]
                now = fresh.get(f"{key}|{root}")
                if now is None:
                    report["blocked"].append(key)     # 复检时这一件已经不认识/不在了
                    continue
                action = now["action"]
                if action == "blocked":
                    report["blocked"].append(key)
                    continue
                if action == "kept-newer":
                    report["kept_newer"].append(key)  # 本机比包新:一个字节都不碰
                    continue
                target = _root_dir(root).joinpath(*key.split("/"))
                arc = f"{PAYLOAD_PREFIX}{root}/{key}"
                try:
                    data = zf.read(arc)
                except (OSError, KeyError):
                    report["blocked"].append(key)
                    continue
                if now.get("sha256") and hashlib.sha256(data).hexdigest() != now["sha256"]:
                    report["blocked"].append(key)       # 只拒这一件,不否决整包
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if action == "replaced":
                        bpath = backup.joinpath(root, *key.split("/"))
                        bpath.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, bpath)
                    tmp = target.with_name(target.name + ".migrate.tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, target)
                    # 时间戳复原成包里那份的:不然"刚导入"会被下一次导入误判成
                    # "本机更新过",用户想再导一遍就被 kept-newer 挡回来。
                    os.utime(target, (time.time(), now.get("pack_mtime") or time.time()))
                except OSError:
                    report["blocked"].append(key)
                    continue
                report["replaced" if action == "replaced" else "written"].append(key)
                if now.get("restart"):
                    restart_needed = True
    except (OSError, zipfile.BadZipFile):
        return {"ok": False, "msg": "包在写入过程中读不出来了,已停止(已写的部分保持原样)"}
    if report["replaced"]:
        try:
            (backup / "decisions.json").write_text(json.dumps(
                {"source": str(src), "replaced": report["replaced"],
                 "kept_newer": report["kept_newer"], "blocked": report["blocked"],
                 "app_version": _app_version()},
                ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        _prune_backups()
    _previews.pop(str(nonce), None)
    return {"ok": True, "path": str(src),
            "written": len(report["written"]), "replaced": len(report["replaced"]),
            "kept_newer": len(report["kept_newer"]), "blocked": len(report["blocked"]),
            "written_keys": report["written"], "replaced_keys": report["replaced"],
            "kept_newer_keys": report["kept_newer"], "blocked_keys": report["blocked"],
            "backup": str(backup) if report["replaced"] else "",
            "restart_needed": restart_needed}


def forget_preview(nonce: str) -> None:
    """用户看了预览说"算了":把这次预览丢掉(不给"过期还能写"留口子)。"""
    with _lock:
        _previews.pop(str(nonce or ""), None)
