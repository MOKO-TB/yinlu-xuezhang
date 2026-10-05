"""Token 用量记账与统计:SQLite 落库,按天/按模型聚合。

设计对齐星瑶 Y-Code 的 token_usage 表(参考 docs/superpowers/specs/
2026-08-09-gui-token-statistics-design.md),但按引路学长的形态收敛:
- 只记**服务端返回的真实 usage**(prompt/completion/total);usage 为 None
  就不记账,绝不估算——不虚构数字是底线;
- cache 命中分母不记(v0.9.117 起补 `cached_tokens` 一列:端点**回报了才**
  落,0 是"端点说了零命中",NULL 是"端点根本没说",两回事);
- 库文件放 app_path("token-usage.db"),跟随 exe(绿色便携)。

W0(v0.9.114 观测线)补的三列,服务「跨会话总账」(dsh-watcher Insights 的地基):
- `session_id`:留档文件名(stem)—— 跨重启稳定的会话身份;空串 = 没有留档
  的运行(子代理、纯内存会话),它们**不进**「有账的会话数」;
- `elapsed_ms`:这一次 LLM 请求的实测流式耗时(turn.elapsed_ms),跨会话
  「累计耗时」就靠它,不再让"18.5 小时"这种数只能靠猜;
- `retries`:这次请求成功之前撞了几次重试(agent 层计数;请求本身没记上账时,
  这笔重试挂在下一条有账的记录上 —— 重试真实发生过,不该凭空消失)。
老库自动补列(ALTER TABLE),旧行按缺省值读,不迁移不重建。

线程模型:Agent 每回合在后台线程里记账,GUI 查统计在主线程,SQLite
连接用 check_same_thread=False + 一把模块级锁串行化,足够这里的使用强度。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from minicode.paths import app_path

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None    # 测试注入用


def db_path() -> Path:
    return _DB_PATH if _DB_PATH is not None else app_path("token-usage.db")


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(str(db_path()), check_same_thread=False)
        _CONN.execute(
            "CREATE TABLE IF NOT EXISTS token_usage ("
            "  ts INTEGER NOT NULL, date TEXT NOT NULL,"
            "  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',"
            "  prompt_tokens INTEGER NOT NULL DEFAULT 0,"
            "  completion_tokens INTEGER NOT NULL DEFAULT 0,"
            "  total_tokens INTEGER NOT NULL DEFAULT 0,"
            "  session_id TEXT NOT NULL DEFAULT '',"
            "  elapsed_ms INTEGER, retries INTEGER NOT NULL DEFAULT 0,"
            "  ttft_ms INTEGER, reasoning_tokens INTEGER,"
            "  cached_tokens INTEGER)"
        )
        _migrate_columns(_CONN)
        _CONN.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(date)"
        )
        _CONN.commit()
    return _CONN


# W0 补的三列:(列名, 建列语句)。CREATE TABLE IF NOT EXISTS 对**已存在**的表
# 不补列,所以老库要在这里逐列 ALTER —— 缺谁补谁,重复执行是空转不是错误。
# W5/W6 再补两列:ttft_ms(首字延迟)与 reasoning_tokens(思考 token)都是
# **端点回报了才有**的实测,NULL = 未记录 —— 绝不当 0(0 是"瞬间首字/零思考",
# NULL 是"端点根本没说",两回事)。
# v0.9.117 补 cached_tokens(缓存命中),同一个 NULL 口径 —— llm 层早在
# v0.9.114 就把 prompt_tokens_details.cached_tokens 抓进了 turn.usage,
# 这一列只是把那份事实落进账本。
_NEW_COLUMNS = (
    ("session_id", "TEXT NOT NULL DEFAULT ''"),
    ("elapsed_ms", "INTEGER"),
    ("retries", "INTEGER NOT NULL DEFAULT 0"),
    ("ttft_ms", "INTEGER"),
    ("reasoning_tokens", "INTEGER"),
    ("cached_tokens", "INTEGER"),
)


def _migrate_columns(conn: sqlite3.Connection) -> None:
    have = {str(r[1]) for r in conn.execute("PRAGMA table_info(token_usage)")}
    for name, ddl in _NEW_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE token_usage ADD COLUMN {name} {ddl}")


def set_db_path(path: Path | None) -> None:
    """切换数据库文件(测试用);None 恢复默认路径。会关掉旧连接。"""
    global _CONN, _DB_PATH
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except sqlite3.Error:
                pass
            _CONN = None
        _DB_PATH = path


def record_usage(provider: str, model: str, usage: dict | None,
                 session_id: str = "", elapsed_ms: int | None = None,
                 retries: int = 0, ttft_ms: int | None = None,
                 reasoning_tokens: int | None = None,
                 cached_tokens: int | None = None) -> bool:
    """追加一条真实 token 用量;usage 缺失/非法时静默跳过(绝不记账估算值)。

    W0 起多带三个可选事实:`session_id`(留档文件名,空串=没有留档的运行)、
    `elapsed_ms`(这次请求的实测流式耗时)、`retries`(这次请求成功前的重试
    次数)。W5/W6 再带两个**端点回报了才有**的实测:`ttft_ms`(首字延迟)与
    `reasoning_tokens`(思考 token)—— None 就是「未记录」,绝不落 0
    (0 是"瞬间首字/零思考",None 是"端点根本没说",两回事)。
    v0.9.117 再带 `cached_tokens`(缓存命中),同一个 NULL 口径。
    这些都不改变"usage 缺失就不记账"的底线 —— 没有用量时其余的数也不落库,
    宁缺毋错。返回是否真的记了账。失败只返回 False,绝不影响对话主流程。
    """
    if not isinstance(usage, dict):
        return False
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    if prompt is None or completion is None:
        return False                       # 字段缺失:宁可不记,不猜
    total = _as_int(usage.get("total_tokens"))
    if total is None:
        total = prompt + completion
    ts = int(time.time())
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    try:
        with _LOCK:
            _conn().execute(
                "INSERT INTO token_usage "
                "(ts, date, provider, model, prompt_tokens, completion_tokens, "
                " total_tokens, session_id, elapsed_ms, retries, ttft_ms, "
                " reasoning_tokens, cached_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, day, str(provider or ""), str(model or ""),
                 prompt, completion, total, str(session_id or ""),
                 _as_int(elapsed_ms), max(0, _as_int(retries) or 0),
                 _as_int(ttft_ms), _as_int(reasoning_tokens),
                 _as_int(cached_tokens)),
            )
            _conn().commit()
        return True
    except sqlite3.Error:
        return False


def _as_int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
        return n if n >= 0 else None
    return None


# ------------------------------------------------------------ 查询聚合

FIELDS = ("prompt", "completion", "total", "requests")


def statistics(days: int = 7) -> dict:
    """按天 + 按模型聚合最近 days 天的用量(本地时区,与写入口径一致)。

    返回 {"ok": True, "range": {...}, "summary": {...}, "models": [...],
    "days": [{date, prompt, completion, total, requests, models: [...]}]}。
    零用量日期保留空记录,时间序列完整;异常时返回 {"ok": False, "msg": ...}。
    """
    if days not in (1, 7, 30):
        return {"ok": False, "msg": "统计范围仅支持 1、7、30 天"}
    try:
        rows = _rows(days)
        elapsed_ms, retries, sessions = _extra_totals(days)
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"读用量数据失败:{type(e).__name__}: {e}"}

    from datetime import date as _date, timedelta as _td

    end = _date.today()
    start = end - _td(days=days - 1)
    day_lookup = {
        (start + _td(days=i)).isoformat():
            {"date": (start + _td(days=i)).isoformat(),
             **{f: 0 for f in FIELDS}, "models": []}
        for i in range(days)
    }
    summary = {f: 0 for f in FIELDS}
    model_totals: dict[str, dict] = {}

    for row in rows:
        provider = str(row.get("provider") or "")
        model = str(row.get("model") or "")
        key = f"{provider}/{model}" if provider else model
        item = model_totals.setdefault(
            key, {"key": key, "provider": provider, "model": model,
                  **{f: 0 for f in FIELDS}})
        for f in FIELDS:
            v = int(row.get(f, 0) or 0)
            item[f] += v
            summary[f] += v
        day_item = day_lookup.get(str(row.get("date") or ""))
        if day_item is not None:
            for f in FIELDS:
                day_item[f] += int(row.get(f, 0) or 0)
            day_item["models"].append({
                "key": key, "provider": provider, "model": model,
                **{f: int(row.get(f, 0) or 0) for f in FIELDS},
            })

    models = sorted(model_totals.values(),
                    key=lambda m: (-m["total"], m["key"]))
    for m in models:
        m.pop("requests", None)   # 模型级不显示请求数,免得一行太宽
    # W5/W6:按模型的实测画像(耗时/重试/首字延迟/思考 token)并进同一行 ——
    # 前端的风云榜从这一份行里挑冠亚,不再开第二个查询面。profile 没有的键
    # (同库同源,理论不会发生)保持 None,不编 0。
    try:
        profile = _model_profile(days)
    except sqlite3.Error:
        profile = {}
    for m in models:
        p = profile.get((str(m.get("provider") or ""), str(m.get("model") or "")),
                        {})
        m["elapsed_ms"] = p.get("elapsed_ms", 0)
        m["retries"] = p.get("retries", 0)
        samples = p.get("ttft_samples", 0)
        m["ttft_samples"] = samples
        m["ttft_ms"] = (p.get("ttft_sum_ms", 0) // samples) if samples else None
        m["reasoning_samples"] = p.get("reasoning_samples", 0)
        m["reasoning_tokens"] = (p.get("reasoning_tokens", 0)
                                 if p.get("reasoning_samples") else None)
    # W0:跨会话总账的补充面 —— 耗时/重试/有账的会话数,都是真记下来的列;
    # 老库旧行按缺省值(0/空)读,不猜。
    summary["elapsed_ms"] = elapsed_ms
    summary["retries"] = retries
    summary["sessions"] = sessions
    # W2:估算费用。价目缺行=未知、绝不当 $0,这是 pricing 模块自己的口径;
    # 这一块出了任何岔子都不该拖垮用量统计本身,所以整段兜住。
    try:
        from minicode import pricing
        summary.update(pricing.summarize(models))
    except Exception:
        pass
    days_out = []
    for d in day_lookup.values():
        d["models"].sort(key=lambda m: (-m["total"], m["key"]))
        days_out.append(d)
    return {"ok": True,
            "range": {"days": days, "start_date": start.isoformat(),
                      "end_date": end.isoformat()},
            "summary": summary, "models": models, "days": days_out}


def today_total() -> int:
    """今天(本地时区)已花的 total tokens;读不到就是 0(C1,v0.9.108)。"""
    try:
        rows = _rows(1)
    except sqlite3.Error:
        return 0
    return sum(int(r.get("total", 0) or 0) for r in rows
               if str(r.get("date") or "") == _today_iso())


def _today_iso() -> str:
    from datetime import date as _date
    return _date.today().isoformat()


def budget_state(limit: int) -> dict:
    """日预算的状态(C1,v0.9.108):limit<=0 = 未启用,只报今天的数。

    口径:**只看今天**,不做滚动平均 —— 预算的意义是"今天还剩多少",
    昨天花超了不该让今天一开始就是红的。阈值分三档:<80% 正常、
    80%~100% 接近、≥100% 超支;条目里带"剩多少"而不是只报百分比。
    """
    spent = today_total()
    if limit <= 0:
        return {"enabled": False, "spent": spent, "limit": 0,
                "ratio": 0.0, "level": "off"}
    ratio = spent / limit
    if ratio >= 1.0:
        level = "over"
    elif ratio >= 0.8:
        level = "near"
    else:
        level = "ok"
    return {"enabled": True, "spent": spent, "limit": limit,
            "ratio": round(ratio, 4), "level": level,
            "remaining": max(0, limit - spent)}


def _extra_totals(days: int) -> "tuple[int, int, int]":
    """(累计耗时 ms, 重试次数, 有账的会话数)—— W0 三列上的一次聚合。

    会话数只数 `session_id` 非空的行:子代理与纯内存会话没有留档身份,
    把它们数进去,"有统计的对话 N/M"就成了对不上号的账。
    """
    from datetime import date as _date, timedelta as _td

    start = (_date.today() - _td(days=days - 1)).isoformat()
    with _LOCK:
        row = _conn().execute(
            "SELECT COALESCE(SUM(elapsed_ms), 0), COALESCE(SUM(retries), 0),"
            "       COUNT(DISTINCT CASE WHEN session_id != ''"
            "            THEN session_id END) "
            "FROM token_usage WHERE date >= ?", (start,)).fetchone()
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


def _model_profile(days: int) -> dict[tuple[str, str], dict]:
    """按 (provider, model) 聚合 W5/W6 的实测列:耗时/重试/首字延迟/思考 token。

    NULL 语义贯穿到底:`ttft_ms`/`reasoning_tokens` 是「端点回报了才有」的
    实测 —— 样本数=0 时平均值回 None(未记录),绝不当 0:0 是"瞬间首字/
    零思考",None 是"端点根本没说",两回事。
    """
    from datetime import date as _date, timedelta as _td

    start = (_date.today() - _td(days=days - 1)).isoformat()
    return _profile_between(start, _date.today().isoformat())


def _profile_between(d0: str, d1: str) -> dict[tuple[str, str], dict]:
    """_model_profile 的区间版:v0.9.117 dashboard 的起止日期不再固定是"近 N 天"。"""
    with _LOCK:
        rows = _conn().execute(
            "SELECT provider, model,"
            "       COALESCE(SUM(elapsed_ms), 0), COALESCE(SUM(retries), 0),"
            "       SUM(CASE WHEN ttft_ms IS NOT NULL THEN 1 ELSE 0 END),"
            "       COALESCE(SUM(ttft_ms), 0),"
            "       SUM(CASE WHEN reasoning_tokens IS NOT NULL THEN 1 ELSE 0 END),"
            "       COALESCE(SUM(reasoning_tokens), 0)"
            " FROM token_usage WHERE date BETWEEN ? AND ? GROUP BY provider, model",
            (d0, d1)).fetchall()
    out: dict[tuple[str, str], dict] = {}
    for provider, model, elapsed, retries, ttft_n, ttft_sum, r_n, r_sum in rows:
        out[(str(provider or ""), str(model or ""))] = {
            "elapsed_ms": int(elapsed or 0), "retries": int(retries or 0),
            "ttft_samples": int(ttft_n or 0), "ttft_sum_ms": int(ttft_sum or 0),
            "reasoning_samples": int(r_n or 0),
            "reasoning_tokens": int(r_sum or 0),
        }
    return out


def _rows(days: int) -> list[dict]:
    from datetime import date as _date, timedelta as _td

    start = (_date.today() - _td(days=days - 1)).isoformat()
    with _LOCK:
        cur = _conn().execute(
            "SELECT date, provider, model, prompt_tokens, completion_tokens, "
            "       total_tokens, COUNT(*) AS requests "
            "FROM token_usage WHERE date >= ? "
            "GROUP BY date, provider, model, prompt_tokens, completion_tokens, "
            "         total_tokens",
            (start,),
        )
        return [
            {"date": r[0], "provider": r[1], "model": r[2],
             "prompt": r[3], "completion": r[4], "total": r[5],
             "requests": r[6]}
            for r in cur.fetchall()
        ]


# ------------------------------------------------------------ 仪表盘(v0.9.117)

RANGE_KINDS = ("day", "week", "month", "all", "custom")


def resolve_range(kind: str, start: str | None = None,
                  end: str | None = None) -> "tuple[str, str] | None":
    """区间语义 → (起, 止) ISO 日期(含两端);认不出返回 None,绝不猜。

    周=近 7 天、月=近 30 天 —— 与老弹窗「今日/近 7 天/近 30 天」同一把尺,
    不另发明日历口径;all 的起点是库里最早那天。custom 两端都要是合法
    ISO 日期且起≤止,止落在未来收敛到今天:起比今天还晚说明整段无意义,
    返回 None 让调用方明说「区间不合法」,而不是画一块空图。
    """
    from datetime import date as _date, timedelta as _td

    kind = str(kind or "")
    today = _date.today()
    if kind == "day":
        return today.isoformat(), today.isoformat()
    if kind == "week":
        return (today - _td(days=6)).isoformat(), today.isoformat()
    if kind == "month":
        return (today - _td(days=29)).isoformat(), today.isoformat()
    if kind == "all":
        with _LOCK:
            row = _conn().execute("SELECT MIN(date) FROM token_usage").fetchone()
        first = str(row[0] or "") if row else ""
        return (first or today.isoformat()), today.isoformat()
    if kind == "custom":
        if not (isinstance(start, str) and isinstance(end, str)
                and start.strip() and end.strip()):
            return None
        try:
            s = _date.fromisoformat(start.strip())
            e = _date.fromisoformat(end.strip())
        except ValueError:
            return None
        if s > e or s > today:
            return None
        if e > today:
            e = today
        return s.isoformat(), e.isoformat()
    return None


def dashboard(kind: str = "week", start: str | None = None,
              end: str | None = None) -> dict:
    """用量示意改版(v0.9.117)的一揽子数据面,一次查询出齐全页所需。

    - `summary`:区间汇总,含 cached_tokens/reasoning_tokens 的 NULL 口径
      (全区间端点都没回报 → None =「未记录」,绝不落 0);
    - `days`:按天明细,对话数 = 当天 distinct 有账会话(跨供应商去重);
    - `models`:按模型聚合,带 share 占比、W5/W6 实测画像(风云榜从这份行
      里挑)与估算费用 —— 与 statistics 同一把 pricing 尺;
    - `providers`:按供应商分组(用量示意页的来源卡);provider 为空的行
      如实归 "" 组,前端标「未记供应商」;
    - `tiles`/`meta`:四块统计砖(近7天/近30天/日均/有账会话)与开始使用/
      活跃天数 —— **全期口径,不随区间变**,与热力图(全期)同一理由:
      那几块讲的是"这个库的一生",不是"这个区间"。
    任何一层读挂了返回 {"ok": False, "msg": ...},绝不拿半份账凑页面。
    """
    from datetime import date as _date, timedelta as _td

    try:
        rng = resolve_range(kind, start, end)
        if rng is None:
            return {"ok": False,
                    "msg": "统计区间不合法(认得:日/周/月/总计/自定义,"
                           "自定义要起≤止的合法日期)"}
        d0, d1 = rng
        today = _date.today()
        with _LOCK:
            rows = _conn().execute(
                "SELECT date, provider, model,"
                "       COALESCE(SUM(prompt_tokens), 0),"
                "       COALESCE(SUM(completion_tokens), 0),"
                "       COALESCE(SUM(total_tokens), 0),"
                "       SUM(cached_tokens),"
                "       SUM(CASE WHEN cached_tokens IS NOT NULL THEN 1 ELSE 0 END),"
                "       SUM(reasoning_tokens),"
                "       SUM(CASE WHEN reasoning_tokens IS NOT NULL THEN 1 ELSE 0 END),"
                "       COUNT(*)"
                " FROM token_usage WHERE date BETWEEN ? AND ?"
                " GROUP BY date, provider, model", (d0, d1)).fetchall()
            day_rows = _conn().execute(
                "SELECT date,"
                "       COALESCE(SUM(prompt_tokens), 0),"
                "       COALESCE(SUM(completion_tokens), 0),"
                "       COALESCE(SUM(total_tokens), 0),"
                "       SUM(cached_tokens), SUM(reasoning_tokens), COUNT(*),"
                "       COUNT(DISTINCT CASE WHEN session_id != ''"
                "            THEN session_id END)"
                " FROM token_usage WHERE date BETWEEN ? AND ?"
                " GROUP BY date ORDER BY date", (d0, d1)).fetchall()
            sessions = _conn().execute(
                "SELECT COUNT(DISTINCT CASE WHEN session_id != ''"
                "            THEN session_id END)"
                " FROM token_usage WHERE date BETWEEN ? AND ?", (d0, d1)
            ).fetchone()[0]
            meta = _conn().execute(
                "SELECT MIN(date), COUNT(DISTINCT date),"
                "       COUNT(DISTINCT CASE WHEN session_id != ''"
                "            THEN session_id END),"
                "       COALESCE(SUM(CASE WHEN date >= ? THEN total_tokens END), 0),"
                "       COALESCE(SUM(CASE WHEN date >= ? THEN total_tokens END), 0)"
                " FROM token_usage",
                ((today - _td(days=6)).isoformat(),
                 (today - _td(days=29)).isoformat())).fetchone()
            heat = _conn().execute(
                "SELECT date, COALESCE(SUM(total_tokens), 0) FROM token_usage"
                " GROUP BY date ORDER BY date").fetchall()
        # 画像查询自带加锁(_profile_between 内部自己 with _LOCK)—— 必须放在
        # 上面的锁块**外面**:threading.Lock 不可重入,锁里再锁就是自己等自己。
        profile = _profile_between(d0, d1)
    except sqlite3.Error as e:
        return {"ok": False, "msg": f"读用量数据失败:{type(e).__name__}: {e}"}

    summary = {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
    model_totals: dict[tuple[str, str], dict] = {}
    for (date, provider, model, p, c, t, cache_sum, cache_n,
         r_sum, r_n, n) in rows:
        summary["prompt"] += int(p or 0)
        summary["completion"] += int(c or 0)
        summary["total"] += int(t or 0)
        summary["requests"] += int(n or 0)
        key = (str(provider or ""), str(model or ""))
        item = model_totals.setdefault(key, {
            "key": f"{key[0]}/{key[1]}" if key[0] else key[1],
            "provider": key[0], "model": key[1],
            "prompt": 0, "completion": 0, "total": 0, "requests": 0,
            "_cache_sum": 0, "_cache_n": 0, "_r_sum": 0, "_r_n": 0})
        item["prompt"] += int(p or 0)
        item["completion"] += int(c or 0)
        item["total"] += int(t or 0)
        item["requests"] += int(n or 0)
        item["_cache_sum"] += int(cache_sum or 0)
        item["_cache_n"] += int(cache_n or 0)
        item["_r_sum"] += int(r_sum or 0)
        item["_r_n"] += int(r_n or 0)

    # NULL 口径:整个区间一次实测都没有 → None(未记录);有样本才给和。
    cache_sum_all = sum(m["_cache_sum"] for m in model_totals.values())
    cache_n_all = sum(m["_cache_n"] for m in model_totals.values())
    r_sum_all = sum(m["_r_sum"] for m in model_totals.values())
    r_n_all = sum(m["_r_n"] for m in model_totals.values())
    summary["cached_tokens"] = cache_sum_all if cache_n_all else None
    summary["cached_samples"] = cache_n_all
    summary["reasoning_tokens"] = r_sum_all if r_n_all else None
    summary["reasoning_samples"] = r_n_all
    summary["sessions"] = int(sessions or 0)

    models = []
    for key, m in model_totals.items():
        m.pop("_cache_sum"), m.pop("_cache_n"), m.pop("_r_sum"), m.pop("_r_n")
        p = profile.get(key, {})
        m["elapsed_ms"] = p.get("elapsed_ms", 0)
        m["retries"] = p.get("retries", 0)
        samples = p.get("ttft_samples", 0)
        m["ttft_samples"] = samples
        m["ttft_ms"] = (p.get("ttft_sum_ms", 0) // samples) if samples else None
        m["reasoning_samples"] = p.get("reasoning_samples", 0)
        m["reasoning_tokens"] = (p.get("reasoning_tokens", 0)
                                 if p.get("reasoning_samples") else None)
        m.pop("requests", None)      # 与 statistics 同一取舍:模型行不摆请求数
        models.append(m)
    models.sort(key=lambda m: (-m["total"], m["key"]))
    for m in models:
        m["share"] = (round(m["total"] / summary["total"] * 100, 1)
                      if summary["total"] else 0.0)
    # 估算费用:与 statistics 同一把 pricing 尺,费用口径两视图不岔开。
    try:
        from minicode import pricing
        summary.update(pricing.summarize(models))
    except Exception:
        pass

    # 来源卡:按供应商聚合(模型数按 distinct 口径,同一供应商的模型不重数)。
    prov: dict[str, dict] = {}
    for m in models:
        item = prov.setdefault(m["provider"],
                               {"provider": m["provider"], "total": 0,
                                "models": set()})
        item["total"] += m["total"]
        item["models"].add(m["model"])
    providers = [{"provider": k, "total": v["total"],
                  "share": (round(v["total"] / summary["total"] * 100, 1)
                            if summary["total"] else 0.0),
                  "models": len(v["models"])}
                 for k, v in prov.items()]
    providers.sort(key=lambda x: (-x["total"], x["provider"]))

    days_out = [{"date": r[0], "prompt": int(r[1] or 0),
                 "completion": int(r[2] or 0), "total": int(r[3] or 0),
                 "cached_tokens": int(r[4]) if r[4] is not None else None,
                 "reasoning_tokens": int(r[5]) if r[5] is not None else None,
                 "requests": int(r[6] or 0), "sessions": int(r[7] or 0)}
                for r in day_rows]
    return {"ok": True,
            "range": {"kind": str(kind or ""), "start_date": d0,
                      "end_date": d1},
            "summary": summary, "models": models, "providers": providers,
            "days": days_out,
            "heatmap": [{"date": r[0], "total": int(r[1] or 0)}
                        for r in heat],
            "tiles": {"d7": int(meta[3] or 0), "d30": int(meta[4] or 0),
                      "daily_avg": int(int(meta[4] or 0) / 30),
                      "sessions": int(meta[2] or 0)},
            "meta": {"first_date": str(meta[0]) if meta[0] else None,
                     "active_days": int(meta[1] or 0),
                     "today": today.isoformat()}}
