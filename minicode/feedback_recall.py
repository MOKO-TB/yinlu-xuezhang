"""把**已经记录下来**的信号回头用起来(v0.9.39 B0/B1)。

方案在主线那份 `D:/game/mini-code/docs/roadmap-v0.9.39-信号回灌.md`(本树没带副本)。

上一轮重读代码时发现的浪费是同一类:信号早就在记,但记录从不改变行为 ——
👎 落进 `message-feedback.jsonl` 之后只有一个开发者脚本读过它;`search_knowledge`
连着三次空手,界面也毫无察觉。这个模块补的就是"回头用它"这一步。

两条口径,都**不新增任何落盘文件**:

- **B0 反复搜空**:同一个查询词在可见上下文里已经空手过几次?第 3 次还空就在工具
  结果末尾附一行(给模型:别再换着说法搜同一个词),监控栏那一行从第 2 次起标
  「×N 未命中」(给人看)。**只认跨轮的重复** —— 一次任务里换关键词补搜是正常检索,
  弹提示就是刷屏。
- **B1 差评避让**:这次的提问与最近某条 👎 是不是"同一类问题"?是就给主循环一句
  **系统侧先验**,让它换个方式答(补来源、给步骤)。判定接 `memory.is_same_memory`
  那一份 2-gram 口径(整体 ≥0.75 或覆盖 ≥0.85),**不引入向量模型、不调模型算相似度**;
  够不到阈值就当没这回事 —— 认错问题的代价("我没说过不满意啊")远大于少一次提示。
  而且**绝不当众复述**用户点过 👎:旧差评只作先验,回话里不许出现那句话。

纯计算 + 只读:除 `recent_low_rated` 读一份 JSONL 外都吃传进来的消息列表;
所有出口都**失败即退回"不提示"**,回灌坏了不该带走一轮对话。
"""
from __future__ import annotations

import json
import time

# 已经空手过这么多次之后,**再空一次**才在工具结果里附那句(即第 3 次)。
# 前两次是正常检索:模型本来就该试着换关键词。
REPEAT_ASK_N = 2
# 监控栏那一行从第几次空手起标「×N 未命中」:比上面松一档 —— 给人看的只要"事实",
# 第 2 次就已经是事实了;给模型的提示要更保守,免得打断正常的检索节奏。
MARK_FROM = 2
MATCH_DAYS = 30           # 差评只在最近这么多天里当先验(陈年 👎 不该影响今天)
MAX_LOW_RATED = 200       # 一次最多读多少条差评:够判同类,又不至于越攒越慢


def empty_hits(messages) -> list[tuple[str, str, int]]:
    """按先后返回 `(call_id, 当时的查询词, 这是该词第几次空手)`。

    **只扫一遍**,B0 的两个消费者(工具结果那一句、监控栏那个 ×N)都从这里派生 ——
    两边各算一次迟早对不上,那就是"聊天里说搜到了、监控栏写着没搜到"。
    """
    from minicode import activity, growth, kb     # 延迟导入:activity 反过来要用本模块

    calls, results, order = activity._call_index(messages)
    seen: dict[str, int] = {}
    out: list[tuple[str, str, int]] = []
    for cid in order:
        call = calls.get(cid) or {}
        if call.get("name") != "search_knowledge":
            continue
        if not kb.is_no_hit(results.get(cid)):
            continue          # 搜到了 / 还没回来 / 报错了 —— 都不是"搜空"
        query = str((call.get("args") or {}).get("query") or "")
        key = growth.normalize_query(query)
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append((cid, query, seen[key]))
    return out


def empty_hits_by_call(messages) -> dict[str, int]:
    """`call_id -> 这次是该词第几次空手`(监控栏按行标注用)。"""
    return {cid: n for cid, _q, n in empty_hits(messages)}


def repeat_empty_queries(messages) -> dict[str, int]:
    """归一化查询词 -> 已经空手过几次(只收**空过**的词)。"""
    from minicode import growth

    out: dict[str, int] = {}
    for _cid, query, n in empty_hits(messages):
        out[growth.normalize_query(query)] = n
    return out


def repeat_note(times: int) -> str:
    """附在工具结果末尾的那一句。措辞只说事实 + 下一步,不评价模型也不吓用户。"""
    return (f"×{times} 未命中:这个词在本地知识库里已经连续 {times} 次什么都没搜到。"
            "别再换着说法搜同一个词 —— 要么如实告诉用户「库里没有这一条」并给下一步"
            "(问教务 / 看学校官网 / 用 kb_suggest 记成待审核候选),"
            "要么换一个**不同角度**的关键词(比如从「怎么申请」换成具体项目名)。")


# ------------------------------------------------------------------ B1 差评避让

def recent_low_rated(days: int = MATCH_DAYS, limit: int = MAX_LOW_RATED) -> list[dict]:
    """最近 `days` 天里的 👎(带归一化后的问题)。读不到/坏行一律跳过,不抛。"""
    from minicode import growth
    from minicode.paths import app_path

    cutoff = time.time() - max(0, int(days)) * 86400
    rows: list[dict] = []
    try:
        with open(app_path(growth.FEEDBACK_FILE), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue                     # 半截行(写到一半断电)不影响别的
                if int(rec.get("rating") or 0) != -1:
                    continue
                if not growth.within_days(rec.get("ts"), cutoff):
                    continue
                question = str(rec.get("question") or "").strip()
                if not question:
                    continue                     # 没有问题原文就没法判"是不是同一类"
                rows.append({"question": question,
                             "q_norm": growth.normalize_query(question),
                             "answer": str(rec.get("answer") or ""),
                             "ts": str(rec.get("ts") or "")})
    except OSError:
        return []
    return rows[-limit:]                         # 只留最近的:判同类不需要全史


def match_low_rated(query: str, rows: list[dict] | None = None) -> dict | None:
    """这次的提问是否与某条历史 👎 属于同一类问题。不够像就 `None`。

    判定用 `memory.is_same_memory`(整体 2-gram ≥0.75,或较短一条被覆盖 ≥0.85),
    与长期记忆去重同一份口径 —— 不为了这一处另定一个阈值。
    """
    from minicode import growth, memory

    text = str(query or "").strip()
    if not text:
        return None
    if rows is None:
        rows = recent_low_rated()
    best: dict | None = None
    best_score = -1.0
    for row in rows:
        question = row.get("question") or ""
        if not question:
            continue
        if not memory.is_same_memory(text, question):
            continue
        score = memory.similarity(growth.normalize_query(text),
                                 row.get("q_norm") or growth.normalize_query(question))
        if score > best_score:                   # 同分取先出现的那条(确定、可测)
            best, best_score = row, score
    if best is None:
        return None
    return {"question": best["question"],
            "similarity": round(max(best_score, 0.0), 3),
            "ts": best.get("ts", "")}


def avoidance_hint(matched: dict) -> str:
    """命中历史差评时塞给主循环的那一句**系统侧先验**(不进用户可见文案)。"""
    return ("【本轮先验·不要念给用户】这一类问题此前答得不好。这次换一种方式答:"
            "先给结论**再给依据与来源**,能拆步骤的拆步骤,拿不准就直说本地库里没有、"
            "并给出下一步(问教务 / 看官网 / 记成 kb_suggest 候选)。"
            "**不要**把这条先验念出来,也不要提起用户给过什么反馈 —— 那是把别人"
            "私下的一次点击当众复述。")
