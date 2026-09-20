# -*- coding: utf-8 -*-
"""维护者复盘脚本:知识库成长闭环的三份日志一眼看完。

看三样东西:
  1. kb-gaps.jsonl          答不上来的查询按频次排 —— 就是下一步补语料的清单
  2. kb-candidates.jsonl    kb_suggest 存的候选(等审核并入 kb/*.json)
  3. message-feedback.jsonl 👍/👎 统计与最近的差评样本

只用标准库。日志文件都在应用目录(exe 旁 / 源码仓库根):
    .venv\\Scripts\\python.exe scripts\\review_kb_growth.py
    .venv\\Scripts\\python.exe scripts\\review_kb_growth.py --gaps 30 --days 14
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minicode.growth import (CANDIDATES_FILE, FEEDBACK_FILE, GAPS_FILE,  # noqa: E402
                             app_path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def fmt_ts(ts: str) -> str:
    return str(ts or "")[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库成长闭环复盘")
    ap.add_argument("--gaps", type=int, default=20, help="列出未命中查询的条数")
    ap.add_argument("--days", type=int, default=30, help="只看最近 N 天(默认 30)")
    args = ap.parse_args()
    cutoff = time.time() - args.days * 86400

    print(f"== 知识库成长复盘(最近 {args.days} 天,应用目录 {app_path()})==\n")

    # 1. 未命中查询
    rows = [r for r in read_jsonl(app_path(GAPS_FILE))
            if _within_days(r.get("ts"), cutoff)]
    print(f"一、答不上来的查询({len(rows)} 条登记,去重后按频次):")
    if not rows:
        print("  (最近没有未命中登记 —— 要么没人问,要么都接住了)")
    counter = Counter(r.get("query", "") for r in rows)
    for query, n in counter.most_common(args.gaps):
        print(f"  {n:>3} ×  {query}")
        if n >= 3:
            print("       ^ 高频缺口:建议优先跑对应主题爬虫/补条目")

    # 2. 候选
    cands = [r for r in read_jsonl(app_path(CANDIDATES_FILE))
             if r.get("status", "pending") == "pending"]
    print(f"\n二、知识库候选(待审核 {len(cands)} 条):")
    for r in cands[-15:]:
        title = r.get("source_title") or r.get("source_url", "")
        print(f"  #{r.get('index')} [{fmt_ts(r.get('ts'))}] {r.get('question','')}")
        print(f"       来源: {title[:70]}")
    if cands:
        print("  (审核并入 kb/*.json 后,把对应行的 status 改成 merged)")

    # 3. 反馈
    fb = [r for r in read_jsonl(app_path(FEEDBACK_FILE))
          if _within_days(r.get("ts"), cutoff)]
    up = sum(1 for r in fb if r.get("rating") == 1)
    down = sum(1 for r in fb if r.get("rating") == -1)
    print(f"\n三、消息反馈:👍 {up} / 👎 {down}(共 {len(fb)} 条)")
    for r in [x for x in fb if x.get("rating") == -1][-10:]:
        q = r.get("question", "") or "(无对应问题)"
        a = (r.get("answer", "") or "")[:80]
        print(f"  [{fmt_ts(r.get('ts'))}] 问: {q}")
        print(f"       答: {a}…")
    if down == 0:
        print("  (最近没有差评样本)")
    return 0


def _within_days(ts, cutoff_epoch) -> bool:
    try:
        t = time.mktime(time.strptime(str(ts or "")[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True       # 时间字段坏了宁可多显示,不静默丢
    return t >= cutoff_epoch


if __name__ == "__main__":
    raise SystemExit(main())
