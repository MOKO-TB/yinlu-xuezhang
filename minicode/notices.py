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

import time

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

    state = proactive.load_state()
    if action == "cached":
        snap = _snapshot_lines()
        if snap is None:
            return ("快照里还没有任何通知(看门狗没跑过或没抓到过)。"
                    "用 action=latest 现场拉一次官网试试。")
        rows, when = snap
        return (f"看门狗在 {when} 抓到的官网通知标题(**快照,不是现在**;无日期无正文,"
                "要详情用 web_crawl 抓对应链接或让我现拉 latest):\n" + "\n".join(rows))

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
                "「这次查不到,稍后再试」,绝不要凭印象编「最近发生的事」。")
    rows, when = snap
    return (f"现在拉不到官网通知({why})。这是看门狗在 {when} 抓到的标题快照"
            "(**不是现在的实时列表**;无日期无正文):\n" + "\n".join(rows)
            + "\n要详情用 web_crawl 抓对应链接;回答时说清「这是上次抓到的」。")


def notices_prompt_section() -> str:
    """系统提示词段:时效类问题必须现拉,不要拿本地旧语料充数。"""
    return (
        "\n# 校园近期通知(时效性)\n"
        "- 手上有 campus_notices 工具:现场拉学校官网通知公告栏目的最新标题与链接。"
        "用户问「最近/最新/近日 学校有什么事 / 大事件 / 通知 / 活动 / 官网发了什么」时,"
        "**必须先调 campus_notices**,以拉到的结果为准 —— 本地知识库是爬虫旧语料,"
        "**回答不了时效问题**,也不要把旧语料里的日期当成「最近」。\n"
        "- **放假/调休类问题是重灾区**:「今年中秋国庆怎么放」这种问题,正确做法是"
        "用 campus_notices 现拉官网找「放假安排」类通知,找到就照通知原文回答并附上链接;"
        "**绝不用全国惯例(中秋3天/国庆7天)回答** —— 学校的安排经常连放调休,"
        "与惯例不同,凭惯例答就是错的(v0.9.45 前实测翻过车)。官网上找不到今年通知,就如实说"
        "「我这边还没查到今年的放假安排,以学校教务处通知为准」并给官网入口。"
        "kb 里若有往年的放假文章(如 2021 年那篇),**不是今年的不能当今年的安排说**。\n"
        "- 工具给的是标题+链接:要讲某件事的详情就再用 web_crawl 抓那条链接,"
        "抓不到就按标题如实说「官网发了这条,详情我没抓到」,绝不编内容。\n"
        "- 拉不到(断网/改版)就如实说这次查不到,建议稍后再试;快照回退时会注明"
        "「是上次抓到的」—— 转述时把这层意思带上。\n"
    )
