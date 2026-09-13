# -*- coding: utf-8 -*-
"""从离线馆藏库生成 kb/library-gzasc.json。

kb/ 是给 search_knowledge 用的「几百条以内」资料库(kb.py 每次全量加载做
2-gram 打分),不适合放 133 万条书目;书目走 library_search + SQLite。
这里只放「图书馆本身」的常识条目:馆藏规模、分类分布、馆藏地分布、怎么找书。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB = Path(r"D:\game\mini-code\crawled\mfindgzyykj.libsp.cn\library.db")
OUT = Path(r"D:\game\mini-code\kb\library-gzasc.json")
SOURCE_URL = "https://mfindgzyykj.libsp.cn/"
SOURCE = "广应科图书馆 OPAC"

CN = {
    "A": "马列主义、毛泽东思想、邓小平理论", "B": "哲学、宗教", "C": "社会科学总论",
    "D": "政治、法律", "E": "军事", "F": "经济", "G": "文化、科学、教育、体育",
    "H": "语言、文字", "I": "文学", "J": "艺术", "K": "历史、地理",
    "N": "自然科学总论", "O": "数理科学和化学", "P": "天文学、地球科学",
    "Q": "生物科学", "R": "医药、卫生", "S": "农业科学", "T": "工业技术",
    "U": "交通运输", "V": "航空、航天", "X": "环境科学、安全科学", "Z": "综合性图书",
}

FLOOR = {"2": "二楼", "3": "三楼", "4": "四楼", "5": "五楼", "6": "六楼",
         "7": "七楼", "8": "八楼", "9": "九楼"}


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    total, paper, ebook = c.execute(
        "SELECT COUNT(*), SUM(p_count>0), SUM(p_count=0) FROM books").fetchone()
    items = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    cls = c.execute("SELECT COUNT(DISTINCT class_no) FROM books").fetchone()[0]
    dist = list(c.execute(
        "SELECT substr(class_no,1,1) AS c, COUNT(*) AS n, SUM(p_count>0) AS p "
        "FROM books GROUP BY c ORDER BY n DESC"))
    locs = list(c.execute(
        "SELECT location_name AS name, COUNT(*) AS n FROM items "
        "GROUP BY location_name ORDER BY n DESC"))
    top = [r for r in dist if CN.get(r["c"])][:8]

    entries = []

    # 1) 馆藏规模
    entries.append({
        "id": "lib-001",
        "title": "图书馆馆藏规模:书目 %s 种(纸本 %s 种 + 电子书 %s 种)" % (
            f"{total:,}", f"{paper:,}", f"{ebook:,}"),
        "content": (
            "广应科图书馆藏数据(据图书馆 OPAC 统一检索库全量统计,抓取时间 %s):\n"
            "书目总数 %s 种;其中纸本图书 %s 种(可到馆借阅),电子图书 %s 种;\n"
            "馆藏单册条目约 %s 册,覆盖 %s 个中国图书馆分类法分类号。\n"
            "查某一本具体的书(有没有、索书号、在哪个书库)请用馆藏检索,可直接秒回;\n"
            "本条目只描述馆藏总体规模。"
        ) % (time.strftime("%Y-%m-%d"), f"{total:,}", f"{paper:,}", f"{ebook:,}",
             f"{items:,}", f"{cls:,}"),
        "url": SOURCE_URL, "source": SOURCE,
        "tags": ["图书馆", "馆藏", "馆藏规模", "藏书量", "书目", "纸本", "电子书"],
        "credibility": "A", "publish_at": time.strftime("%Y-%m-%d"),
    })

    # 2) 分类分布
    lines = ["广应科图书馆中图法各大类藏书量(种,据 OPAC 全量统计):"]
    for r in top:
        lines.append("- %s %s:共 %s 种(纸本 %s 种)" % (
            r["c"], CN[r["c"]], f"{r['n']:,}", f"{r['p'] or 0:,}"))
    lines.append("文学(I)、工业技术(T)、经济(F)是馆藏量最大的三类。")
    lines.append("想按类浏览全部书目,可查 crawled/mfindgzyykj.libsp.cn/opac-catalog/ 下按类拆分的 CSV。")
    top3 = "、".join("%s %s(%s 万种)" % (r["c"], CN[r["c"]].split("、")[0],
                                        round(r["n"] / 10000, 1)) for r in top[:3])
    entries.append({
        "id": "lib-002",
        "title": "图书馆藏书最多的类别:" + top3,
        "content": "\n".join(lines),
        "url": SOURCE_URL, "source": SOURCE,
        "tags": ["图书馆", "中图法", "分类", "馆藏分布", "文学", "工业技术", "经济"],
        "credibility": "A", "publish_at": time.strftime("%Y-%m-%d"),
    })

    # 3) 馆藏地(楼层/书库)
    lines = ["广应科图书馆馆藏地分布(册数,据 OPAC 在架馆藏统计):"]
    for r in locs[:18]:
        lines.append("- %s:%s 册" % (r["name"], f"{r['n']:,}"))
    lines += [
        "",
        "图书馆按楼层分设「新书区」与「书墙」,索书号首字母对应中图法大类,"
        "不同大类放在不同楼层(如 I 文学在二楼 201、T 工业技术在八楼、"
        "计算机类在八楼 805、经济类在四楼与五楼中庭)。",
        "具体某一本书在哪个书库,用馆藏检索查该书即可返回馆藏地。",
    ]
    entries.append({
        "id": "lib-003",
        "title": "图书馆各书库与阅览室分布(按楼层)",
        "content": "\n".join(lines),
        "url": SOURCE_URL, "source": SOURCE,
        "tags": ["图书馆", "书库", "馆藏地", "楼层", "阅览室", "新书区", "书墙", "在哪"],
        "credibility": "A", "publish_at": time.strftime("%Y-%m-%d"),
    })

    # 4) 怎么找书
    entries.append({
        "id": "lib-004",
        "title": "怎么在图书馆找一本书:用索书号 + 馆藏地",
        "content": (
            "找书步骤:\n"
            "1. 先查这本书在不在馆:问「图书馆有没有《书名》」,或让 agent 查馆藏,\n"
            "   返回结果里有「在架数」——在架数大于 0 说明现在可以去书架拿。\n"
            "2. 记下两个关键信息:索书号(如 I247.5/2531)和馆藏地(如 301 流通书库)。\n"
            "3. 到对应书库,按索书号找:索书号 = 中图法分类号 + 种次号,\n"
            "   先按字母/数字顺序定位大类,再按种次号顺序找到具体那一本。\n"
            "4. 中图法分类号的首字母决定楼层区域(如 I=文学、T=工业技术、"
            "F=经济、TP=计算机)。\n"
            "索书号、馆藏地、条码都能在馆藏检索结果里直接看到。"
        ),
        "url": SOURCE_URL, "source": SOURCE,
        "tags": ["图书馆", "借书", "找书", "索书号", "中图法", "馆藏地", "在架", "怎么找"],
        "credibility": "A", "publish_at": time.strftime("%Y-%m-%d"),
    })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入 %s,%d 条" % (OUT, len(entries)))


if __name__ == "__main__":
    main()
