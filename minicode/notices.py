"""校园近期通知工具(campus_notices):被问「最近学校有什么事」时现拉官网。

为什么要这个工具:本地知识库(kb/*.json)是爬虫语料,**没有时效性** —— 同学问
「最近学校有什么大事件」,模型手里原本只有旧语料和看门狗已抓的快照,而快照
只用于主动推送、模型看不见。这个工具把 proactive 已经打通的那条路
(博达站群列表页,httpx+bs4 静态解析)开一个**只读的模型出口**:

- latest(默认):现场拉 notice-watch.json 里配置的栏目(默认教务处/学生处
  通知公告,维护者可加源),逐源解析;某个源挂了只跳过它,不影响别的源
  (与看门狗 check_once 同一口径);
- cached:不联网,只回 notice-watch.json 里看门狗上次抓到的标题快照。

诚实边界(与全项目同一条线):
- 只报**真抓到的**标题与深链 URL;标题出自 clean_title(清洗过的列表页锚文本),
  正文不在这里 —— 模型要讲详情就再调 web_crawl 抓那一条,绝不按标题编内容;
- 日期来自列表页的结构化标注,标注看不出就留空,不猜;
- 现拉失败(断网/官网改版)如实说,并退回快照并注明「这是上次抓到的,不是现在」;
  快照也没有就说没有,绝不编一份「最近发生的事」出来。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from minicode import proactive

MAX_LIST = 15        # 一次最多列几条(再多就是刷屏;模型要更多可以分次)


def _fmt(items: list[dict], source_of: dict[str, str]) -> list[str]:
    lines = []
    for it in items:
        when = f"[{it['date']}] " if it.get("date") else ""
        src = source_of.get(it["url"], "")
        tail = f" —— {src}" if src else ""
        lines.append(f"- {when}{it['title']}{tail}\n  {it['url']}")
    return lines


def _snapshot_lines() -> tuple[list[str], str] | None:
    """看门狗快照里的标题(无日期、无正文);没有返回 None。"""
    state = proactive.load_state()
    rows: list[str] = []
    updated = str(state.get("last_check") or 0)
    for src in state.get("sources") or []:
        titles = ((state.get("seen") or {}).get(str(src.get("url") or "")) or {}).get("titles") or []
        name = str(src.get("name") or "官网通知")
        for t in titles[:MAX_LIST]:
            rows.append(f"- {t} —— {name}")
    if not rows:
        return None
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(updated))) \
        if updated else "未知时间"
    return rows, when


def campus_notices(ctx, action: str = "latest", limit: int = 10) -> str:
    """campus_notices 工具处理器:现拉官网通知列表 / 只读看门狗快照。"""
    action = (action or "latest").strip().lower()
    try:
        limit = max(1, min(int(limit or 10), MAX_LIST))
    except (TypeError, ValueError):
        limit = 10

    if action not in ("latest", "cached"):
        return f"错误: 未知 action '{action}'。可用:latest(现拉官网,默认)/cached(只读看门狗快照,不联网)"

    # ctx 可能是 None(测试与子代理那条路就是这么调的),没有工作区就没有可核对的归档。
    note = corpus_note(getattr(ctx, "workspace", None))
    state = proactive.load_state()
    if action == "cached":
        snap = _snapshot_lines()
        if snap is None:
            return ("快照里还没有任何通知(看门狗没跑过或没抓到过)。"
                    "用 action=latest 现场拉一次官网试试。"
                    + (f"\n{note}" if note else ""))
        rows, when = snap
        return (f"看门狗在 {when} 抓到的官网通知标题(**快照,不是现在**;无日期无正文,"
                "要详情用 web_crawl 抓对应链接或让我现拉 latest):\n" + "\n".join(rows)
                + (f"\n{note}" if note else ""))

    # latest:逐源现拉,一个源挂了只跳过它(与看门狗 check_once 同一口径)
    items: list[dict] = []
    source_of: dict[str, str] = {}
    ok_sources, fail_sources = 0, 0
    reported: set[str] = set()
    for src in state.get("sources") or []:
        url = str(src.get("url") or "")
        name = str(src.get("name") or "官网通知")
        if not url.startswith("http"):
            continue
        try:
            latest = proactive.fetch_latest(url)
        except Exception:
            fail_sources += 1
            continue
        ok_sources += 1
        for it in latest:
            if it["title"] in reported:
                continue
            reported.add(it["title"])
            items.append(it)
            source_of[it["url"]] = name
    items = items[:limit]

    if items:
        head = (f"学校官网通知公告(现拉于 {time.strftime('%Y-%m-%d %H:%M')},"
                f"{ok_sources} 个栏目"
                + (f",{fail_sources} 个栏目这次没拉到" if fail_sources else "") + "):")
        lines = _fmt(items, source_of)
        tail = ("以上是**标题与链接**,不是正文 —— 要讲某一件事的详情,先用 web_crawl "
                "抓那条链接再回答,不要按标题猜测内容;日期是列表页标注的发布日期。")
        return "\n".join([head, *lines, tail])

    # 一个源都没拉到:退快照,并把"退了"说出口
    snap = _snapshot_lines()
    why = "断网或官网改版" if fail_sources else "没配置任何通知源"
    if snap is None:
        return (f"现在拉不到官网通知({why}),看门狗快照里也没有 —— 如实告诉同学"
                "「这次查不到,稍后再试」,绝不要凭印象编「最近发生的事」。"
                + (f"\n{note}" if note else ""))
    rows, when = snap
    return (f"现在拉不到官网通知({why})。这是看门狗在 {when} 抓到的标题快照"
            "(**不是现在的实时列表**;无日期无正文):\n" + "\n".join(rows)
            + "\n要详情用 web_crawl 抓对应链接;回答时说清「这是上次抓到的」。"
            + (f"\n{note}" if note else ""))


CRAWLED_DIR_NAME = "crawled"
# 抓取时刻**有两家写侧,各写各的格式**:
#   `minicode/tools/crawl.py:495`(学校官网栏目)→ `%Y-%m-%d %H:%M`
#   `scripts/library_opac/archive.py:164`(馆藏 OPAC 全量快照)→ `%Y-%m-%d %H:%M:%S`
# 读侧只认一种的后果不是报错,是"更新的那份归档被当成没有时刻"直接跳掉 ——
# 2026-10-01 在本机真数据上撞到:OPAC 是 09-13 12:35:54,官网是 09-12 14:33,
# 结果"最近一次抓取"报的是次新的那一次。数字看起来完全正常,这就是它危险的地方。
# 两条针钉住:`CRAWL_STAMP_FORMATS` 必须收着两家写侧各自的格式,显示口径统一 `CRAWL_STAMP_FMT`。
CRAWL_STAMP_FMT = "%Y-%m-%d %H:%M"
CRAWL_STAMP_FORMATS = (CRAWL_STAMP_FMT, "%Y-%m-%d %H:%M:%S")


def _stamp_key(stamp: str) -> float | None:
    """把归档里的时刻解析成可比较的秒;两种已登记的写法之外一律认不出(返回 None)。"""
    for fmt in CRAWL_STAMP_FORMATS:
        try:
            return time.mktime(time.strptime(stamp, fmt))
        except ValueError:
            continue
    return None


def corpus_age(workspace) -> dict | None:
    """本机爬虫语料最近一次抓取是什么时候(读 `crawled/*/index.json` 的 `crawled_at`)。

    为什么要有这个数:提示词一直在喊"本地知识库是旧语料、回答不了时效问题",
    但**旧到什么时候没人说得出**。"三天前抓的"和"八个月前抓的"是两种完全不同的话:
    前者可以说"这台机器上最新的消息就是这些",后者只能现拉。

    只认 `index.json` 里那次抓取亲手写下的 `crawled_at`,**不拿文件 mtime 猜** ——
    仓库/归档文件被 clone、checkout、拷贝一遍时间就全变了,那是个看着可信的假数字。
    两种已登记的写法(`_stamp_key`)都认;认不出的归档**不参与比较、也不算进 sites**,
    而是单独报个数(`unreadable`)—— 悄悄丢掉一份归档的表现是"最近一次抓取"报成次新的那次,
    没有任何地方会红。没有归档、或全都没有可用时刻,返回 None(没有记录就说没有记录)。
    """
    if workspace is None:
        return None
    root = Path(workspace) / CRAWLED_DIR_NAME
    if not root.is_dir():
        return None
    best: tuple[float, str, str] | None = None   # (时刻秒, 显示用的原文, 站点目录名)
    sites = 0        # 读得出抓取时刻的归档数
    unreadable = 0   # 归档在、但里面那个时刻读不出来(没写/写法不认识)
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return None
    for d in dirs:
        idx = d / "index.json"
        if not idx.is_file():
            continue
        try:
            stamp = str(json.loads(idx.read_text(encoding="utf-8")).get("crawled_at") or "")
        except (OSError, ValueError):
            unreadable += 1
            continue
        key = _stamp_key(stamp)
        if key is None:
            unreadable += 1
            continue
        sites += 1
        if best is None or key > best[0]:
            best = (key, stamp, d.name)
    if best is None:
        return None
    return {"at": time.strftime(CRAWL_STAMP_FMT, time.localtime(best[0])),
            "raw": best[1], "host": best[2], "sites": sites, "unreadable": unreadable,
            "days": max(0, int((time.time() - best[0]) // 86400))}


def _unreadable_tail(age: dict) -> str:
    """有几份归档没写下可用时刻 —— 单独说一句,不含糊进「最近一次」里。"""
    if not age.get("unreadable"):
        return ""
    return f"(另有 {age['unreadable']} 个归档没记下能读的抓取时刻,不算在内)"


def corpus_note(workspace) -> str:
    """**工具回话**里那一句「本地这批语料截至什么时候」(与提示词那句是两个场合)。

    提示词那句(`_corpus_line`)是开局就说、说一次;这一句是在模型**正在用本地数据
    回答的那一刻**提醒它:手上的天花板就是这个时刻,再新的东西这里没有。
    没有归档(或没有工作区)就返回空串 —— 回话里一个字都不加,
    写一句「这台机器从没抓过」是替用户下结论。
    """
    age = corpus_age(workspace)
    if age is None:
        return ""
    return (f"本地语料截至 {age['at']}({age['days']} 天前,来自 {age['host']},"
            f"共 {age['sites']} 个站点有归档){_unreadable_tail(age)} —— "
            "比这个时刻更新的事,本地库里没有。")


def _corpus_line(workspace) -> str:
    """提示词里那一句:有记录就给真数字,没记录就整句不说(不编"从没抓过")。"""
    age = corpus_age(workspace)
    if age is None:
        return ""
    return (f"- 本机这批爬虫语料最近一次抓取是 **{age['at']}({age['days']} 天前,"
            f"来自 {age['host']})**,共 {age['sites']} 个站点有归档{_unreadable_tail(age)}。"
            "这个数只用来判断「本地答案能说到多新」:抓取再近也不改变上一条规矩 —— "
            "时效类问题仍然要 campus_notices 现拉。\n")


def notices_prompt_section(workspace=None) -> str:
    """系统提示词段:时效类问题必须现拉,不要拿本地旧语料充数。

    `workspace` 给了才会附那句"这批语料是几天前抓的"(见 `_corpus_line`):
    没有工作区就没有可核对的归档,宁可不写这一句,也不写一句猜的。
    """
    return (
        "\n# 校园近期通知(时效性)\n"
        + _corpus_line(workspace)
        + "- 手上有 campus_notices 工具:现场拉学校官网通知公告栏目的最新标题与链接。"
        "用户问「最近/最新/近日 学校有什么事 / 大事件 / 通知 / 活动 / 官网发了什么」时,"
        "**必须先调 campus_notices**,以拉到的结果为准 —— 本地知识库是爬虫旧语料,"
        "**回答不了时效问题**,也不要把旧语料里的日期当成「最近」。\n"
        "- **放假/调休类问题是重灾区**:「今年中秋国庆怎么放」这种问题,正确做法是"
        "用 campus_notices 现拉官网找「放假安排」类通知,找到就照通知原文回答并附上链接;"
        "**绝不用全国惯例(中秋3天/国庆7天)回答** —— 学校的安排经常连放调休,"
        "与惯例不同,凭惯例答就是错的(实测翻过车)。官网上找不到今年通知,就如实说"
        "「我这边还没查到今年的放假安排,以学校教务处通知为准」并给官网入口。"
        "kb 里若有往年的放假文章(如 2021 年那篇),**不是今年的不能当今年的安排说**。\n"
        "- 工具给的是标题+链接:要讲某件事的详情就再用 web_crawl 抓那条链接,"
        "抓不到就按标题如实说「官网发了这条,详情我没抓到」,绝不编内容。\n"
        "- 拉不到(断网/改版)就如实说这次查不到,建议稍后再试;快照回退时会注明"
        "「是上次抓到的」—— 转述时把这层意思带上。\n"
    )
