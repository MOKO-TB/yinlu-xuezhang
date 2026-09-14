"""馆藏库自检:确认某个目录(源码根 / 便携版 / 安装版)里的 library.db 真的能用。

用途:现场排查「图书馆查不到书」时,先分清是**库没装好/读不出来**,还是**库里
真没有这本书**——这两件事以前都会被报成「没有这一本书」(用户报障 B01/WP4)。

用法:
    python scripts/check_library_db.py                  # 查本仓库的库
    python scripts/check_library_db.py D:\\ylxz\\引路学长   # 查安装版目录
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import library  # noqa: E402

# 完整版应有的规模(见 minicode/library.py);明显偏小就是没装好
EXPECT_TOTAL = 1_300_000
SAMPLES = ["三体", "刘慈欣", "C语言程序设计（第5版）", "《C程序设计》", "高等数学（上）"]


def main(argv: list[str]) -> int:
    workspace = Path(argv[1]).resolve() if len(argv) > 1 else ROOT
    print(f"工作区: {workspace}")
    cands = library.db_candidates(workspace)
    for p in cands:
        print(f"  候选: {p}  exists={p.is_file()}")
    path = library.db_path(workspace)
    if path is None:
        print("!! 找不到 library.db:图书馆检索不可用(工具会明确报「库不存在」)")
        return 1

    size_gb = path.stat().st_size / 1e9
    print(f"\n库文件: {path}\n  大小: {size_gb:.2f} GB")
    ok = True
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        total = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        print(f"  书目: {total} 条")
    except sqlite3.Error as exc:
        print(f"  !! 读不出来: {type(exc).__name__}: {exc}")
        print("     → 库文件损坏/被截断/被别的进程占用;重装或重拷 library.db")
        return 1

    if total < EXPECT_TOTAL:
        ok = False
        print(f"  !! 条数偏少(完整版约 {EXPECT_TOTAL} 条)→ 库没装全,需要重新安装/拷贝")
    if size_gb < 1.0:
        ok = False
        print("  !! 文件不足 1 GB → 多半是空壳或截断的库")

    print("\n样本查询:")
    for q in SAMPLES:
        try:
            hits = library.search(workspace, q, top_k=1)
        except library.CatalogUnavailable as exc:
            print(f"  ✗ {q:22} → 库读不出来: {exc}")
            ok = False
            continue
        first = hits[0]["title"] if hits else "—"
        print(f"  {'✓' if hits else '✗'} {q:22} → {first}")

    print("\n" + ("=== 自检通过:库可用 ===" if ok else "=== 自检未通过:库有问题 ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
