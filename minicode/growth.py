"""知识库成长闭环的落盘层:检索未命中登记 / 知识候选 / 消息反馈。

三个文件都是**应用级**数据(存 APP_DIR,exe 旁),不是工作区数据 —— 知识库
本身是全局的,反馈记录关心的也是"这个应用答得怎么样",跟着工作区走没意义:

- kb-gaps.jsonl          search_knowledge 未命中的查询(维护者据此补语料)
- kb-candidates.jsonl    kb_suggest 工具存的知识候选(审核后并入 kb/*.json)
- message-feedback.jsonl 前端 👍/👎 反馈(发现"答得差"的问题)

全部 JSONL、追加写;GUI 多会话线程与网页版多访客线程会并发写,统一走模块级
锁。记录失败一律静默返回 False —— 成长闭环是锦上添花,绝不能拖垮对话主流程
(与 usage.py 记账的取舍一致)。
"""
from __future__ import annotations

import json
import re
import threading
import time

from minicode.paths import app_path

GAPS_FILE = "kb-gaps.jsonl"
CANDIDATES_FILE = "kb-candidates.jsonl"
FEEDBACK_FILE = "message-feedback.jsonl"
# v0.9.80:检索**命中**账(与 kb-gaps 对称)—— 评价绑条目 id 的唯一来源:
# 结果文本里没有 id,会话留档里也没有,只有检索发生的那一刻知道。
HITS_FILE = "kb-hits.jsonl"

QUERY_EXCERPT = 200       # gap 登记里查询词的截断长度
FEEDBACK_EXCERPT = 400    # 反馈里答案摘要的截断长度

_LOCK = threading.RLock()      # 可重入:log_kb_gap 持锁时还要调 _file_seen_today
# 当日已登记过的查询(进程内去重):同一个词反复搜不命中只记一条,
# 重启后清空 —— 文件里可能有跨进程的重复,review 脚本按频次归并时自然处理。
_SEEN_GAPS: set[tuple[str, str]] = set()

# 跨进程的当日去重(v0.9.45):_SEEN_GAPS 只活在进程内,而 e2e 界面实测每轮
# 都是全新进程 —— 一场评测跑 20 遍,「社团招新」「图书馆几点闭馆」就能在
# 账本里各灌 20 条,review 时像 20 个同学在问一样。写之前先看账本里今天
# 已有哪些词(按天缓存一次),与原设计"同一天同一查询只记一次"完全同一意图,
# 只是把去重从进程内升级到账本内。
_seen_cache_key: str = ""      # "{账本路径}|{日期}":路径变了(monkeypatch/换 APP_DIR)缓存必须失效
_seen_from_file: set[str] = set()


def _file_seen_today(today: str) -> set[str]:
    """账本里**今天**已登记过的查询(归一化后);按(路径,日期)缓存,坏行跳过。"""
    global _seen_cache_key, _seen_from_file
    path = str(app_path(GAPS_FILE))
    with _LOCK:
        key = f"{path}|{today}"
        if _seen_cache_key == key:
            return set(_seen_from_file)
        rows: set[str] = set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if today not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict) and str(rec.get("ts") or "").startswith(today):
                        rows.add(normalize_query(str(rec.get("query") or "")))
        except OSError:
            pass
        rows.discard("")                     # 空查询行不该挡住任何东西
        _seen_cache_key, _seen_from_file = key, rows
        return set(rows)


def _append(path, record: dict) -> bool:
    """追加一条 JSONL;任何失败都静默(文件系统问题不该影响对话)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except OSError:
        return False


def normalize_query(query: str) -> str:
    """查询词归一化(去空格标点、小写):用于当日去重判断。"""
    return re.sub(r"[\s\W\u3000-\u303f]+", "", str(query or ""), flags=re.UNICODE).lower()


def within_days(ts, cutoff_epoch: float) -> bool:
    """一条记录的时间戳是否还在窗口内(`ts` 是本模块写出去的那种 `%F %T` 字符串)。

    时间字段坏了宁可算"在窗口内"(多显示一条),也不静默丢掉 —— 这条口径原来是
    `scripts/review_kb_growth.py` 里的私有函数,B1 也要用同一份,所以搬到这里当
    唯一真相(脚本改成调它)。
    """
    try:
        t = time.mktime(time.strptime(str(ts or "")[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    return t >= cutoff_epoch


def log_kb_gap(query: str, reason: str = "no-hit") -> bool:
    """search_knowledge 未命中时登记一条;同一天同一查询只记一次(跨进程也算)。

    reason: no-hit=库里有数据但没匹配上(真正要补的缺口)。
    知识库为空的报错不算缺口,调用方不应在这种情况下调本函数。
    """
    query = str(query or "").strip()
    if not query:
        return False
    today = time.strftime("%Y-%m-%d")
    key = (today, normalize_query(query))
    with _LOCK:
        if key in _SEEN_GAPS or key[1] in _file_seen_today(today):
            return False
        _SEEN_GAPS.add(key)

    return _append(app_path(GAPS_FILE), {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query[:QUERY_EXCERPT],
        "reason": reason,
    })


def gap_digest(days: int = 7, top: int = 5,
               now: "float | None" = None) -> "list[dict] | None":
    """kb-gaps.jsonl 最近 `days` 天的问法聚合(top N);一条都没有回 None。

    A4 缺口周报(proactive._job_gap_digest)的唯一数据源。按 normalize_query
    归组 —— 同一个问法的变体算一条,展示**最早**出现的那条原文;days 数的是
    **出现过的不同日期**:账本按「同一天同一问法只记一次」落盘(v0.9.45 起),
    但旧账本/手工编辑可能留有同日重复,数日期才不会虚报"出现 N 天"。
    no-hit(库里有数据没匹配上)与 library(馆藏搜书落空)都算缺口,一起进。
    **只读**:这里绝不改任何文件。
    """
    try:
        raw = app_path(GAPS_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    moment = time.time() if now is None else float(now)
    cutoff = moment - max(1, int(days)) * 86400
    groups: "dict[str, dict]" = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue                      # 半截行:跳过,不让整份周报崩掉
        if not isinstance(rec, dict) or not within_days(rec.get("ts"), cutoff):
            continue
        q = str(rec.get("query") or "").strip()
        if not q:
            continue
        row = groups.setdefault(normalize_query(q), {"query": q, "dates": set()})
        row["dates"].add(str(rec.get("ts"))[:10])
    if not groups:
        return None
    rows = [{"query": g["query"], "days": len(g["dates"])}
            for g in groups.values()]
    return sorted(rows, key=lambda g: -g["days"])[:max(1, int(top))]


def save_candidate(question: str, answer: str = "", source_url: str = "",
                   source_title: str = "", note: str = "") -> dict:
    """存一条知识库候选(只追加候选文件,绝不改正式知识库)。

    返回 {"ok", "msg", "path", "index"};index 是它在候选文件里的序号(1 起),
    便于告诉用户"已存为第 N 条候选"。
    """
    question = str(question or "").strip()
    answer = str(answer or "").strip()
    source_url = str(source_url or "").strip()
    if not question or not answer:
        return {"ok": False, "msg": "问题与答案都不能为空"}
    if not source_url.lower().startswith(("http://", "https://")):
        return {"ok": False, "msg": "缺少可靠的来源网址(http/https),不存入候选"}
    path = app_path(CANDIDATES_FILE)
    with _LOCK:      # 读条数与写行要原子,序号才不会重
        index = 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                index = sum(1 for line in f if line.strip()) + 1
        except OSError:
            pass
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "index": index,
            "question": question[:300],
            "answer": answer[:2000],
            "source_url": source_url,
            "source_title": str(source_title or "").strip()[:200],
            "note": str(note or "").strip()[:200],
            "status": "pending",       # 维护者审核后改为 merged/rejected
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            return {"ok": False, "msg": f"写入候选文件失败:{exc}"}
    return {"ok": True, "msg": f"已存为第 {index} 条知识库候选", "path": str(path),
            "index": index}


def log_feedback(rating: int, question: str = "", answer: str = "",
                 session: str = "", kb_ids: "list[str] | None" = None) -> bool:
    """记录一条消息级 👍/👎 反馈(rating: 1 / -1)。

    kb_ids(v0.9.80):这条回答**引用过的知识库条目 id**(调用方按"最后一条用户
    消息之后"的检索窗口绑定)。有就随记录存档 —— 逐条命中率账本
    (`kb_appraisal`)靠它把评价落到条目上;没有就不写这个字段,
    老记录格式一字不变。
    """
    rating = 1 if int(rating) > 0 else (-1 if int(rating) < 0 else 0)
    if rating == 0:
        return False
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rating": rating,
        "question": str(question or "").strip()[:QUERY_EXCERPT],
        "answer": str(answer or "").strip()[:FEEDBACK_EXCERPT],
        "session": str(session or "")[:80],
    }
    ids = [str(i)[:80] for i in (kb_ids or []) if str(i).strip()][:12]
    if ids:
        record["kb_ids"] = ids
    return _append(app_path(FEEDBACK_FILE), record)


def log_kb_hits(query: str, ids: "list[str]") -> bool:
    """search_knowledge **命中**时登记一条(v0.9.80,与 kb-gaps 对称的账)。

    为什么要有这本账:评价落到条目上,前提是知道"这次回答引用了哪些条目" ——
    而检索结果文本里没有 id(format_results 只打标题),会话留档里也没有;
    所以在检索发生的那一刻把 id 记在账上(精确),评价时按"最后一条用户消息
    之后"的窗口取回(同轮归因)。登记失败静默,绝不影响回答。
    """
    query = str(query or "").strip()
    clean = [str(i)[:80] for i in (ids or []) if str(i).strip()][:12]
    if not query or not clean:
        return False
    return _append(app_path(HITS_FILE), {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query[:QUERY_EXCERPT],
        "ids": clean,
        "n": len(clean),
    })


def last_hits_since(when: str) -> "list[str]":
    """取"时刻 when 之后"所有检索命中的条目 id(去重、保持先后)。

    when 用会话消息的 ts(YYYY-MM-DD HH:MM:SS,字符串字典序即时间序)。
    when 为空(老留档没 ts)时**返回空** —— 宁可这次评价不绑条目,
    也不拿全库的命中硬凑一个归因。
    """
    start = str(when or "").strip()
    if not start:
        return []
    out: list[str] = []
    try:
        with open(app_path(HITS_FILE), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or str(rec.get("ts") or "") <= start:
                    continue
                for i in rec.get("ids") or []:
                    if str(i) and str(i) not in out:
                        out.append(str(i))
    except OSError:
        return []
    return out[:24]


def kb_appraisal(days: int = 30, now: "float | None" = None) -> list[dict]:
    """逐条命中率账本(v0.9.80):每条知识库条目被哪几次回答引用过、评价如何。

    **口径要说不含糊**:`down` 是"引用过这条的那次回答被点了 👎"的次数,
    不是"这条知识是错的" —— 一次回答可能引用多条,差评也可能因为别的原因
    (问法没接住、答得不全)。它是线索,不是判决;补不补语料仍由人决定。

    只读 message-feedback.jsonl 里带 kb_ids 的记录(老记录没有这个字段,
    不参与统计);days 按 ts 截断,now 可注入(测试喂文本)。
    """
    cutoff = (now if now is not None else time.time()) - days * 86400
    agg: dict[str, dict] = {}
    try:
        raw = app_path(FEEDBACK_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        ids = [str(i) for i in (rec.get("kb_ids") or []) if str(i).strip()]
        if not ids:
            continue
        stamp = str(rec.get("ts") or "")
        if not within_days(stamp, cutoff):
            continue
        rating = 1 if int(rec.get("rating") or 0) > 0 else -1
        for i in ids:
            row = agg.setdefault(i, {"id": i, "uses": 0, "up": 0, "down": 0})
            row["uses"] += 1
            row["up" if rating > 0 else "down"] += 1
    return sorted(agg.values(), key=lambda r: (-r["uses"], r["id"]))


def kb_suggest(ctx, question: str, answer: str = "", source_url: str = "",               source_title: str = "", note: str = "") -> str:
    """kb_suggest 工具处理器:经权限确认后落一条知识库候选。

    本工具不标 read_only —— 确认框本身就是"用户同意存入"的那一步:
    模型在确认框里说清要存什么,用户点允许才会执行到这里。
    """
    result = save_candidate(question, answer=answer, source_url=source_url,
                            source_title=source_title, note=note)
    if not result.get("ok"):
        return f"错误: {result.get('msg', '存入候选失败')}。没有写入任何东西。"
    return (f"{result['msg']}(文件 {result['path']})。"
            "注意: 候选要等维护者审核合并后才会进入正式知识库,现在还没生效 —— "
            "请如实告诉用户「已记录为候选」,然后继续正常回答他的问题。")


# ---------------------------------------------------------------- 候选审核(v0.9.21)
#
# kb-candidates.jsonl 攒下来一直靠手工审核(路线图第 13 条的收尾):
# 技能面板加「候选」tab,列表/并入/拒绝都在 GUI 里完成。
# 并入的目标是**应用目录 kb/reviewed.json**(load_kb 会扫到它),
# 绝不去改仓库里爬虫生成的 kb/*.json —— 那是生成物,手改会被下次爬虫覆盖。

REVIEWED_KB_FILE = "reviewed.json"      # app_path("kb") 下的人工审核库


def _candidates_path():
    return app_path(CANDIDATES_FILE)


def _read_candidate_lines() -> list[tuple[int, dict]]:
    """读候选文件,返回 [(行号, 记录)];坏行跳过不炸。"""
    out: list[tuple[int, dict]] = []
    try:
        with open(_candidates_path(), "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append((lineno, rec))
    except OSError:
        pass
    return out


def list_candidates(status: str = "") -> list[dict]:
    """候选清单(默认全部,可按 pending/merged/rejected 过滤),新的在前。"""
    rows = [rec for _ln, rec in _read_candidate_lines()]
    if status:
        rows = [r for r in rows if str(r.get("status") or "pending") == status]
    rows.reverse()                       # JSONL 是追加写,倒序=最新在前
    return rows


def _reviewed_kb_path():
    return app_path("kb") / REVIEWED_KB_FILE


def _merge_into_kb(rec: dict) -> tuple[bool, str]:
    """把一条候选并入 app_path('kb')/reviewed.json(不存在就建)。"""
    path = _reviewed_kb_path()
    entries: list = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            entries = data
    except (OSError, json.JSONDecodeError):
        entries = []
    idx = rec.get("index") or (int(time.time()) % 100000)
    content = str(rec.get("answer") or "").strip()
    note = str(rec.get("note") or "").strip()
    if note:
        content += f"\n(补充: {note})"
    entry = {
        "id": f"reviewed_{idx}",
        "title": str(rec.get("question") or "").strip(),
        "content": content,
        "url": str(rec.get("source_url") or "").strip(),
        "source": str(rec.get("source_title") or "").strip() or "候选审核",
        "credibility": "A-已审核",      # 用户本人点「并入」= 人工核实过
        "tags": ["已审核"],
        "reviewed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    entries = [e for e in entries
               if not (isinstance(e, dict) and e.get("id") == entry["id"])]
    entries.append(entry)                # 同 id 重复并入:覆盖旧的,不堆叠
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return False, f"写知识库失败:{exc}"
    return True, str(path)


def review_candidate(index, action: str) -> tuple[bool, str]:
    """审核一条候选:action=merge(并入正式库)/reject(拒绝)。

    按记录里的 index 字段定位(save_candidate 保证唯一递增);已处理过的
    不重复动作。整文件重写成临时文件再原子替换,全程持模块锁。
    """
    action = str(action or "").strip().lower()
    if action not in ("merge", "reject"):
        return False, "错误: action 只能是 merge 或 reject"
    try:
        want = int(index)
    except (TypeError, ValueError):
        return False, "错误: index 必须是候选编号(数字)"
    with _LOCK:
        rows = _read_candidate_lines()
        target = next((rec for _ln, rec in rows
                       if rec.get("index") == want), None)
        if target is None:
            return False, f"没有编号 {want} 的候选(可能文件被手工改过)"
        cur = str(target.get("status") or "pending")
        if cur != "pending":
            return False, f"编号 {want} 已经是 {cur},不重复处理"
        merged_path = ""
        if action == "merge":
            ok, info = _merge_into_kb(target)
            if not ok:
                return False, info       # 知识库没写成功就不改状态,可以重试
            merged_path = info
        target["status"] = "merged" if action == "merge" else "rejected"
        target["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            path = _candidates_path()
            tmp = path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                for _ln, rec in rows:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(path)
        except OSError as exc:
            return False, f"更新候选状态失败:{exc}(知识库文件未受影响)"
    if action == "merge":
        return True, f"已并入知识库({merged_path}),新会话检索即可命中"
    return True, f"编号 {want} 已标记为拒绝"
