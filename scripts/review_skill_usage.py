# -*- coding: utf-8 -*-
"""技能使用账汇总:哪些技能真有人用、用了几次、最近一次什么时候。

数据源:`skill-usage.jsonl`(use_skill 每次成功调用记一条)。
用法(项目根目录):
    .venv/Scripts/python.exe scripts/review_skill_usage.py [--days 30] [--top 10]

--days 只看最近 N 天(默认全部);--top 只列前 K 名(默认 10)。
输出是纯文本表格,直接贴进维护笔记或 issue 都行。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(days: int) -> list[dict]:
    from minicode.paths import app_path
    path = app_path("skill-usage.jsonl")
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    cutoff = time.time() - max(0, days) * 86400 if days > 0 else 0
    rows = []
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
        ts = str(rec.get("ts") or "")
        if cutoff:
            try:
                if time.mktime(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")) < cutoff:
                    continue
            except ValueError:
                pass
        name = str(rec.get("skill") or "").strip()
        if name:
            rows.append({"skill": name, "ts": ts})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="技能使用账汇总")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天(0=全部)")
    ap.add_argument("--top", type=int, default=10, help="只列前 K 名")
    args = ap.parse_args()

    rows = _load(args.days)
    if not rows:
        print("(账本是空的 —— 还没有技能被用过)")
        return 0
    agg: dict[str, dict] = {}
    for r in rows:
        row = agg.setdefault(r["skill"], {"skill": r["skill"], "uses": 0, "last": ""})
        row["uses"] += 1
        if r["ts"] > row["last"]:
            row["last"] = r["ts"]
    ranked = sorted(agg.values(), key=lambda x: -x["uses"])[:max(1, args.top)]
    print(f"共 {len(rows)} 次使用 / {len(agg)} 个技能(最近 {args.days or '全部'} 天):")
    for i, r in enumerate(ranked, start=1):
        print(f"{i:>3}. {r['skill']}  {r['uses']} 次  最近 {r['last']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
