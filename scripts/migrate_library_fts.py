"""把馆藏库迁到 v0.9.187 的检索结构:title_norm 归一化列 + unicode61 逐字索引。

为什么是**显式脚本**而不是启动时自动迁移:库是 1.31 GB / 133 万行,迁移要更新
一百多万行并重建索引(本机实测约 2 分钟)。放进启动路径等于把"打开学长"变成
几分钟的等待,而且迁移期间库不可查 —— 什么时候付这笔钱必须由人决定:构建端
在出包之前跑,或者拿到旧库的同学手动跑一次。

动作(除 VACUUM 外全在**一个事务**里,中途失败整体回滚,不留半迁移的库):

1. `books` 加 `title_norm` 列,逐行填归一化标题(只用于匹配,不改展示值);
2. 建 `idx_books_norm` 索引;
3. 删掉旧的 trigram `books_fts`,重建为 **unicode61 逐字表**(contentless,
   索引的是归一化之后逐字空格化的字段)——查询用短语匹配,1 字即可命中,
   所以 2 字查询不再走 1.3 GB 的 LIKE 全表扫(实测 3.4 s → 0.012 s);
4. 提交后 `VACUUM` 收空间,并**重写馆藏库凭证**(sha256 变了:不重写的话,
   下次更新会认为本机馆藏库与发布不一致而重下整库)。

保留旧库兼容:程序按 `sqlite_master` 里那条建表 SQL 判断库是 unicode61 还是
trigram,旧库走原来的变体阶梯,不因为代码升级就把旧库判死。

用法:
    python scripts/migrate_library_fts.py --db crawled/<站点>/library.db
    python scripts/migrate_library_fts.py --db ... --force    # 已迁过也重迁
    python scripts/migrate_library_fts.py --db ... --no-vacuum  # 省掉收尾的 VACUUM
"""
from __future__ import annotations

import argparse

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minicode import __version__, library  # noqa: E402

BATCH = 20000
# 迁移后的 FTS 字段:**不含 abstract**。摘要平均 164 字,逐字建索引会多出两亿个
# token(体积远大于收益),而书名/作者/出版社/分类号/索书号才是找书真正输入的。
# 旧库的 trigram 索引过摘要,迁到新结构后这一项**不再全文检索** —— 这是有意
# 取舍,写在这里免得下一轮以为是漏了。
FTS_FIELDS = ("title", "author", "publisher", "class_no", "call_no")


def _fts_flavor(c: sqlite3.Connection) -> str:
    row = c.execute("SELECT sql FROM sqlite_master WHERE name='books_fts'").fetchone()
    return "unicode61" if row and "unicode61" in (row[0] or "").lower() else "trigram"


def _has_norm(c: sqlite3.Connection) -> bool:
    cols = c.execute("PRAGMA table_info(books)").fetchall()
    return any(str(r[1]) == "title_norm" for r in cols)


def migrate(db: Path, *, force: bool = False, vacuum: bool = True) -> dict:
    """跑一次迁移,回一份"做了什么"的账。可重入:已迁过就跳过(除非 force)。"""
    db = Path(db)
    if not db.is_file():
        raise SystemExit(f"找不到馆藏库:{db}")
    size_before = db.stat().st_size
    c = sqlite3.connect(db, timeout=30)
    try:
        flavor = _fts_flavor(c)
        has_norm = _has_norm(c)
        if not force and flavor == "unicode61" and has_norm:
            print("这个库已经是 v0.9.187 的检索结构,跳过(要重迁加 --force)")
            return {"skipped": True}
        total = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        print(f"库:{db}")
        print(f"  行数 {total:,}｜当前 {size_before / 1e9:.2f} GB｜"
              f"FTS={flavor}｜title_norm={'有' if has_norm else '无'}")
        c.execute("PRAGMA journal_mode=DELETE")
        t0 = time.time()
        c.execute("BEGIN IMMEDIATE")
        try:
            if not has_norm:
                t = time.time()
                c.execute("ALTER TABLE books ADD COLUMN title_norm TEXT")
                print(f"  ① 加 title_norm 列:{time.time() - t:.1f}s")

            t = time.time()
            batch: list[tuple] = []
            done = 0
            for rid, title in c.execute("SELECT record_id, title FROM books"):
                batch.append((library.norm_title(title), rid))
                done += 1
                if len(batch) >= BATCH:
                    c.executemany("UPDATE books SET title_norm=? WHERE record_id=?", batch)
                    batch = []
            if batch:
                c.executemany("UPDATE books SET title_norm=? WHERE record_id=?", batch)
            print(f"  ② 填归一化标题 {done:,} 行:{time.time() - t:.1f}s")

            t = time.time()
            c.execute("CREATE INDEX IF NOT EXISTS idx_books_norm ON books(title_norm)")
            print(f"  ③ 建 title_norm 索引:{time.time() - t:.1f}s")

            t = time.time()
            # 爬虫产的库里有一条 books_ai 触发器按**旧列**(含 abstract)往 FTS 写;
            # 不摘掉它,迁移后下一次插书就会当场报错(harvest.init_db 也会摘,
            # 但直接对本库跑脚本时只有这一道)。
            c.execute("DROP TRIGGER IF EXISTS books_ai")
            c.execute("DROP TABLE IF EXISTS books_fts")   # 连带阴影表一起删
            c.execute(
                "CREATE VIRTUAL TABLE books_fts USING fts5("
                + ",".join(FTS_FIELDS)
                + ", content='', tokenize='unicode61')")
            print(f"  ④ 重建 unicode61 逐字 FTS:{time.time() - t:.1f}s")

            t = time.time()
            rows = c.execute(
                "SELECT record_id," + ",".join(FTS_FIELDS) + " FROM books")
            cols = ",".join(FTS_FIELDS)
            qmarks = ",".join("?" * (len(FTS_FIELDS) + 1))
            batch = []
            done = 0
            for row in rows:
                rid = row[0]
                vals = [library._spaced(library.norm_title(v)) for v in row[1:]]
                batch.append((rid, *vals))
                done += 1
                if len(batch) >= BATCH:
                    c.executemany(
                        f"INSERT INTO books_fts(rowid,{cols}) VALUES({qmarks})", batch)
                    batch = []
            if batch:
                c.executemany(
                    f"INSERT INTO books_fts(rowid,{cols}) VALUES({qmarks})", batch)
            print(f"  ⑤ 灌入逐字索引 {done:,} 行:{time.time() - t:.1f}s")
            c.commit()
        except BaseException:
            c.rollback()
            raise
        print(f"迁移事务完成:{time.time() - t0:.0f}s")
    finally:
        c.close()

    if vacuum:
        t = time.time()
        c2 = sqlite3.connect(db, timeout=60)
        try:
            c2.execute("VACUUM")
        finally:
            c2.close()
        print(f"VACUUM:{time.time() - t:.0f}s")
    size_after = db.stat().st_size
    # 凭证必须重写:sha256 变了,不重写会让下次更新把本机库判成"与发布不一致"。
    c3 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        total = c3.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        c3.close()
    sp = library.write_stamp(db, total, extra={"app_version": __version__})
    print(f"已重写凭证 {sp.name}({total:,} 条)")
    print(f"体积 {size_before / 1e9:.2f} GB → {size_after / 1e9:.2f} GB")
    return {"skipped": False, "rows": total,
            "size_before": size_before, "size_after": size_after}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="library.db 的路径(显式给,不猜)")
    ap.add_argument("--force", action="store_true", help="已迁过也重迁")
    ap.add_argument("--no-vacuum", action="store_true", help="跳过收尾的 VACUUM")
    args = ap.parse_args(argv)
    migrate(Path(args.db), force=args.force, vacuum=not args.no_vacuum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
