# -*- coding: utf-8 -*-
"""把图书馆 OPAC 全量书目归档到 mini-code 的 crawled/ 目录。

输出(与项目既有 crawled/<域名>/<主题>/ 约定一致):
  D:\\game\\mini-code\\crawled\\mfindgzyykj.libsp.cn\\
      index.json                     抓取元信息
      library.db                     离线检索库(给 agent 的 library_search 工具用)
      opac-catalog\\
          index.json                 文件清单 + 各分类条数
          README.md                  给人看的说明
          纸本_全量.csv
          纸本_<类号>_<类名>.csv
          电子书_<类号>_<类名>.csv
"""
from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path

SRC_DB = Path(r"D:\game\library\books.db")
DEST = Path(r"D:\game\mini-code\crawled\mfindgzyykj.libsp.cn")
CAT = DEST / "opac-catalog"
SOURCE_URL = "https://mfindgzyykj.libsp.cn/"
LIB_NAME = "广应科图书馆(广州应用科技学院)"

CN = {
    "A": "马列主义_毛泽东思想", "B": "哲学_宗教", "C": "社会科学总论", "D": "政治_法律",
    "E": "军事", "F": "经济", "G": "文化_科学_教育_体育", "H": "语言_文字", "I": "文学",
    "J": "艺术", "K": "历史_地理", "N": "自然科学总论", "O": "数理科学和化学",
    "P": "天文学_地球科学", "Q": "生物科学", "R": "医药_卫生", "S": "农业科学",
    "T": "工业技术", "U": "交通运输", "V": "航空_航天", "X": "环境科学_安全科学",
    "Z": "综合性图书",
}

COLS = ["题名", "作者", "出版社", "出版年", "ISBN", "索书号", "中图法分类号", "馆藏册数",
        "在架数", "语言", "页数", "定价", "主题词", "中图法主题", "馆藏地", "条码", "简介"]
COLS_E = ["题名", "作者", "出版社", "出版年", "ISBN", "中图法分类号", "主题词", "简介"]

EBOOK_SQL = """
SELECT b.title,b.author,b.publisher,b.year,b.isbn,b.class_no,b.subject,b.abstract
FROM books b WHERE b.p_count=0 AND b.class_no LIKE ? ORDER BY b.class_no
"""


def rows_paper(c, where, args=()):
    sql = f"""
    SELECT b.title,b.author,b.publisher,b.year,b.isbn,b.call_no,b.class_no,b.p_count,
           b.onshelf,b.lang,b.book_page,b.price,b.subject,b.chi_subject,
           (SELECT group_concat(DISTINCT COALESCE(NULLIF(i.real_location,''),i.location_name))
              FROM items i WHERE i.record_id=b.record_id) AS locs,
           (SELECT group_concat(DISTINCT i.barcode) FROM items i WHERE i.record_id=b.record_id) AS bcs,
           b.abstract
    FROM books b WHERE {where} ORDER BY b.class_no, b.call_no
    """
    for r in c.execute(sql, args):
        yield [r["title"], r["author"], r["publisher"], r["year"], r["isbn"], r["call_no"],
               r["class_no"], r["p_count"], r["onshelf"], r["lang"], r["book_page"],
               r["price"], r["subject"], r["chi_subject"], r["locs"], r["bcs"],
               (r["abstract"] or "").replace("\n", " ")]


def rows_ebook(c, cls):
    for r in c.execute(EBOOK_SQL, (cls + "%",)):
        yield [r["title"], r["author"], r["publisher"], r["year"], r["isbn"],
               r["class_no"], r["subject"], (r["abstract"] or "").replace("\n", " ")]


def write_csv(path: Path, header, gen) -> int:
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in gen:
            w.writerow(row)
            n += 1
    return n


def snapshot(src: Path, dest: Path) -> None:
    """一致性快照:WAL 模式下直接复制 .db 会丢掉仍在 -wal 里的数据,
    必须走 SQLite 在线备份 API。"""
    if dest.exists():
        dest.unlink()
    for suf in ("-wal", "-shm"):
        p = Path(str(dest) + suf)
        if p.exists():
            p.unlink()
    s = sqlite3.connect(str(src))
    try:
        d = sqlite3.connect(str(dest))
        try:
            s.backup(d)
            d.execute("PRAGMA journal_mode=DELETE")
        finally:
            d.close()
    finally:
        s.close()


def main():
    if not SRC_DB.is_file():
        raise SystemExit("找不到 %s,请先跑 harvest.py" % SRC_DB)
    CAT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    t0 = time.time()
    files = []

    total = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    paper = c.execute("SELECT COUNT(*) FROM books WHERE p_count>0").fetchone()[0]
    ebook = c.execute("SELECT COUNT(*) FROM books WHERE p_count=0").fetchone()[0]
    item_n = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print("全库 %d (纸本 %d / 电子 %d), 馆藏条目 %d" % (total, paper, ebook, item_n), flush=True)

    # 1) 纸本全量
    p = CAT / "纸本_全量.csv"
    n = write_csv(p, COLS, rows_paper(c, "b.p_count>0"))
    files.append({"file": p.name, "kind": "paper", "class": "ALL", "rows": n})
    print("  纸本_全量.csv %d 行" % n, flush=True)

    # 2) 按大类
    for L, name in CN.items():
        cnt = c.execute("SELECT COUNT(*) FROM books WHERE p_count>0 AND class_no LIKE ?",
                        (L + "%",)).fetchone()[0]
        if cnt == 0:
            continue
        p = CAT / ("纸本_%s_%s.csv" % (L, name))
        n = write_csv(p, COLS, rows_paper(c, "b.p_count>0 AND b.class_no LIKE ?", (L + "%",)))
        files.append({"file": p.name, "kind": "paper", "class": L, "rows": n})
        print("  %s %s 纸本 %d" % (L, name, n), flush=True)

    # 3) 电子书按大类
    for L, name in CN.items():
        cnt = c.execute("SELECT COUNT(*) FROM books WHERE p_count=0 AND class_no LIKE ?",
                        (L + "%",)).fetchone()[0]
        if cnt == 0:
            continue
        p = CAT / ("电子书_%s_%s.csv" % (L, name))
        n = write_csv(p, COLS_E, rows_ebook(c, L))
        files.append({"file": p.name, "kind": "ebook", "class": L, "rows": n})
        print("  %s %s 电子书 %d" % (L, name, n), flush=True)

    # 4) 分类分布
    dist = [{"class": r["c"], "name": CN.get(r["c"], ""), "total": r["n"],
             "paper": r["p"] or 0, "ebook": (r["n"] - (r["p"] or 0))}
            for r in c.execute(
                "SELECT substr(class_no,1,1) AS c, COUNT(*) AS n, SUM(p_count>0) AS p "
                "FROM books GROUP BY c ORDER BY c")]
    libs = [dict(r) for r in c.execute(
        "SELECT location_id AS code, location_name AS name, COUNT(*) AS items "
        "FROM items GROUP BY location_id ORDER BY items DESC")]

    # 5) 复制检索库(一致性快照)
    snapshot(SRC_DB, DEST / "library.db")
    print("  library.db %.0f MB" % ((DEST / "library.db").stat().st_size / 1048576), flush=True)

    meta = {
        "domain": "mfindgzyykj.libsp.cn",
        "source_url": SOURCE_URL,
        "library": LIB_NAME,
        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "图书馆 OPAC 统一检索接口全量抓取(按中图法分类号+出版年分区枚举,去重入库)",
        "counts": {"total": total, "paper": paper, "ebook": ebook, "items": item_n},
        "files": files,
        "class_distribution": dist,
        "locations": libs,
        "db": "library.db",
        "agent_tool": "library_search",
    }
    (DEST / "index.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (CAT / "index.json").write_text(
        json.dumps({"counts": meta["counts"], "files": files,
                    "class_distribution": dist}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    write_readme(meta)
    print("完成 %.1f 分钟" % ((time.time() - t0) / 60), flush=True)


def write_readme(meta: dict):
    cnt = meta["counts"]
    lines = [
        f"# {LIB_NAME} · 馆藏书目全量数据",
        "",
        f"- 数据来源：图书馆 OPAC 统一检索（{SOURCE_URL}）",
        f"- 抓取时间：{meta['crawled_at']}",
        f"- 总量：**{cnt['total']:,}** 条书目 —— 纸本 {cnt['paper']:,} 种、"
        f"电子书 {cnt['ebook']:,} 种；馆藏条目 {cnt['items']:,} 册",
        "",
        "## 怎么用",
        "",
        "**最快的方式：直接问 agent。** 本地库已接入 `library_search` 工具，问",
        "「图书馆有没有《活着》」「三体在哪个书库」「余华的书有哪些」即可秒回，不用等官网。",
        "",
        "**想自己翻：**",
        "",
        "- `纸本_全量.csv` —— 全部纸本图书（含索书号、馆藏地、条码）",
        "- `纸本_<类号>_<类名>.csv` —— 按中图法大类拆分，方便按类浏览",
        "- `电子书_<类号>_<类名>.csv` —— 电子书书目（无纸本馆藏位置）",
        "- `../library.db` —— SQLite 库，可自行用 SQL 查询（表 `books` / `items` / `locations`）",
        "",
        "```sql",
        "-- 例：查某本书的索书号与馆藏地",
        "SELECT b.title, b.call_no, i.location_name, i.barcode",
        "FROM books b JOIN items i ON i.record_id = b.record_id",
        "WHERE b.title LIKE '%三体%';",
        "```",
        "",
        "## 字段说明",
        "",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| 索书号 | 书架上找书用的号码（如 I247.5/2531） |",
        "| 中图法分类号 | 中国图书馆分类法分类号（如 I247.5） |",
        "| 馆藏册数 / 在架数 | 全馆册数 / 当前在架可借册数 |",
        "| 馆藏地 | 具体书库或阅览室名称 |",
        "| 条码 | 单册唯一条码 |",
        "",
        "## 中图法分类分布",
        "",
        "| 类号 | 类别 | 总数 | 纸本 | 电子书 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for d in meta["class_distribution"]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            d["class"], d["name"], f"{d['total']:,}", f"{d['paper']:,}", f"{d['ebook']:,}"))
    if meta["locations"]:
        lines += ["", "## 馆藏地分布", "",
                  "| 馆藏地 | 册数 |", "| --- | ---: |"]
        for l in meta["locations"][:40]:
            lines.append("| %s | %s |" % (l["name"], f"{l['items']:,}"))
    lines += ["", "---", "",
              "> 本目录为项目 `crawled/` 下的本地归档（已 gitignore，不入库）。",
              "> 数据仅作检索参考，实际在架状态以图书馆系统实时显示为准。", ""]
    (CAT / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
