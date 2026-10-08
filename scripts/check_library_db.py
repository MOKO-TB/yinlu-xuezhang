"""馆藏库自检:确认某个目录(源码根 / 便携版 / 安装版)里的 library.db 真的能用。

用途:现场排查「图书馆查不到书」时,先分清是**库没装好/读不出来**,还是**库里
真没有这本书**——这两件事以前都会被报成「没有这一本书」(用户报障 B01/WP4)。

分三层验证(从快到慢):
  1. 完整性凭证 `library.db.stamp.json` 记录的 size / count / sha256(构建时写的);
  2. 开库实查条数 + 5 个样本查询(确认真能检索);
  3. `catalog_health()` 的结论(与程序启动时看到的一致)。

用法:
    python scripts/check_library_db.py                  # 查本仓库的库
    python scripts/check_library_db.py D:\\ylxz\\引路学长   # 查安装版目录
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import library  # noqa: E402

# 完整版应有的规模(见 minicode/library.py);明显偏小就是没装好
EXPECT_TOTAL = 1_300_000
SAMPLES = ["三体", "刘慈欣", "C语言程序设计（第5版）", "《C程序设计》", "高等数学（上）"]


def _sha256(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _utf8_console() -> None:
    """这道闸自己打印 ✓/✗。Windows 默认控制台是 GBK,`print` 会直接
    UnicodeEncodeError —— 闸在自家机器上跑不起来就等于没有闸。照 `check_docs.py`
    同一手法执行(被重定向、没有 `reconfigure` 的流原样让它去,不因此炸)。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str]) -> int:
    _utf8_console()
    workspace = Path(argv[1]).resolve() if len(argv) > 1 else ROOT
    print(f"工作区: {workspace}")
    for p in library.db_candidates(workspace):
        print(f"  候选: {p}  exists={p.is_file()}")
    path = library.db_path(workspace)
    if path is None:
        print("!! 找不到 library.db:图书馆检索不可用(工具会明确报「库不存在」)")
        print("   单文件版(引路学长-单文件版.exe)自身不带馆藏库;"
              "要能搜书请用安装包或 win64 zip。")
        return 1

    ok = True
    size = path.stat().st_size
    print(f"\n库文件: {path}\n  大小: {size / 1e9:.2f} GB ({size} 字节)")

    # 1) 凭证比对:分发包里带的就是这一份,对不上说明拿到的不是完整文件
    stamp = library.read_stamp(path)
    if not stamp:
        print("  凭证: 无(开发态或旧包,跳过凭证比对)")
    else:
        print(f"  凭证: 记录 {stamp.get('size')} 字节 / {stamp.get('count')} 条 /"
              f" 构建于 {stamp.get('built_at', '未知')}")
        if isinstance(stamp.get("size"), int) and stamp["size"] != size:
            ok = False
            print(f"  !! 大小与凭证不符:现 {size} 字节,应为 {stamp['size']} 字节"
                  " → 库没装全/被截断,需重新安装或重拷")
        want_hash = stamp.get("sha256")
        if isinstance(want_hash, str) and want_hash:
            print("  正在核对 sha256(1.3 GB,约 5 秒)...")
            got = _sha256(path)
            if got == want_hash:
                print(f"  ✓ sha256 一致: {got[:16]}…")
            else:
                ok = False
                print(f"  !! sha256 不一致: 现 {got[:16]}…,应为 {want_hash[:16]}…"
                      " → 文件被改动过,需重新安装或重拷")

    # 2) 开库实查
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
    if size < 1_000_000_000:
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
        if not hits:
            ok = False

    # 3) 与程序启动时同一套判定
    health = library.catalog_health(workspace)
    print(f"\n启动自检结论: {health.get('status')}")
    notice = library.health_notice(health)
    if notice:
        print(f"  界面会提示: {notice}")
    if health.get("status") != "ok":
        ok = False

    print("\n" + ("=== 自检通过:库可用 ===" if ok else "=== 自检未通过:库有问题 ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
