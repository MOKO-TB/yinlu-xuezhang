"""Token 用量记账与统计:SQLite 落库,按天/按模型聚合。

设计对齐星瑶 Y-Code 的 token_usage 表(参考 docs/superpowers/specs/
2026-08-09-gui-token-statistics-design.md),但按引路学长的形态收敛:
- 只记**服务端返回的真实 usage**(prompt/completion/total);usage 为 None
  就不记账,绝不估算——不虚构数字是底线;
- 不记 cache 命中分母(校园内网服务不返回缓存明细);
- 库文件放 app_path("token-usage.db"),跟随 exe(绿色便携)。

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
            "  total_tokens INTEGER NOT NULL DEFAULT 0)"
        )
        _CONN.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(date)"
        )
        _CONN.commit()
    return _CONN


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


def record_usage(provider: str, model: str, usage: dict | None) -> bool:
    """追加一条真实 token 用量;usage 缺失/非法时静默跳过(绝不记账估算值)。

    返回是否真的记了账。失败只返回 False,绝不影响对话主流程。
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
                " total_tokens) VALUES (?,?,?,?,?,?,?)",
                (ts, day, str(provider or ""), str(model or ""),
                 prompt, completion, total),
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
    days_out = []
    for d in day_lookup.values():
        d["models"].sort(key=lambda m: (-m["total"], m["key"]))
        days_out.append(d)
    return {"ok": True,
            "range": {"days": days, "start_date": start.isoformat(),
                      "end_date": end.isoformat()},
            "summary": summary, "models": models, "days": days_out}


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
