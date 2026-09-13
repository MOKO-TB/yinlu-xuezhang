# -*- coding: utf-8 -*-
"""从离线馆藏库生成 skills/library-guide/data/library-facts.json。

这部分是「图书馆导览」模块内置的真实数据包：馆藏统计、中图法分类分布、
书库↔楼层映射、大类→楼层导览、在架/借出快照。全部来自
crawled/mfindgzyykj.libsp.cn/library.db（图书馆 OPAC 全量抓取），可重跑。

注意：借阅规则、开放时间钟点、座位预约属于「官方口径」，OPAC 与图书馆站
都取不到（两站都是 JS 门户），不在这里生成，也不得编造——模块只在
UNVERIFIED 里登记待补充项与官方入口，由人工补齐。

用法：
    py -3.12 scripts/library_opac/make_guide_facts.py
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"
OUT = ROOT / "skills" / "library-guide" / "data" / "library-facts.json"

OPAC_URL = "https://mfindgzyykj.libsp.cn/"
ILS_URL = "https://lib.gzasc.edu.cn/"
PORTAL_URL = "https://findgzyykj.libsp.cn/#/Home"

CN = {
    "A": "马列主义、毛泽东思想", "B": "哲学、宗教", "C": "社会科学总论",
    "D": "政治、法律", "E": "军事", "F": "经济", "G": "文化、科学、教育、体育",
    "H": "语言、文字", "I": "文学", "J": "艺术", "K": "历史、地理",
    "N": "自然科学总论", "O": "数理科学和化学", "P": "天文学、地球科学",
    "Q": "生物科学", "R": "医药、卫生", "S": "农业科学", "T": "工业技术",
    "U": "交通运输", "V": "航空、航天", "X": "环境科学、安全科学", "Z": "综合性图书",
}
FLOOR = {"1": "一楼", "2": "二楼", "3": "三楼", "4": "四楼",
         "5": "五楼", "6": "六楼", "7": "七楼", "8": "八楼", "9": "九楼"}
FLOOR_ORDER = ["一楼", "二楼", "三楼", "四楼", "五楼", "六楼", "七楼", "八楼", "九楼"]

# 馆舍空间与官方入口：来自学校官方微信号 / 图书馆站，标注来源，人工维护。
SPACES = [
    {"name": "图书馆主楼", "fact": "肇庆校区图书馆坐落北岭山下、丰乐渠畔，2022 年落成；"
     "2023 年 9 月 6—9 层投入使用，共九层。", "source": "学校官方微信号"},
    {"name": "1—5 层 阅览区", "fact": "总阅览座位近 4000 个；很多座位靠窗，可看青山与丰乐渠；"
     "配单人隔间。", "source": "学校官方微信号"},
    {"name": "6—9 层 文化场馆", "fact": "设艺术馆、非遗馆、科技馆、教师发展中心；"
     "可上天台看晚霞。", "source": "学校官方微信号"},
    {"name": "一楼", "fact": "一楼报告厅（大型讲座、宣讲常用场地）。", "source": "学校官网新闻"},
    {"name": "三楼", "fact": "三楼小礼堂；301 流通书库、302 特色书展区。", "source": "学校官网新闻 + OPAC 馆藏地"},
    {"name": "七楼", "fact": "7 楼非遗馆东侧大厅（如“校长下午茶”活动场地）。", "source": "学校官方微信号"},
    {"name": "九楼", "fact": "905 智慧教室；九楼教育新书区、北面临窗高吧台。", "source": "学校官网新闻 + OPAC 馆藏地"},
    {"name": "公共设施", "fact": "设党建馆、咖啡吧、报告厅、小礼堂；1—5 楼座位近 4000 个。",
     "source": "学校官方微信号"},
    {"name": "服务定位", "fact": "图书馆定位为校园信息资源中心、自主学习中心、学术交流中心、"
     "文化传承中心。", "source": "图书馆站 lib.gzasc.edu.cn 站点简介"},
]

OFFICIAL_CHANNELS = [
    {"name": "师生图书服务平台（OPAC 检索/续借/预约）", "url": PORTAL_URL,
     "use": "查书、查在架、续借、预约请求、委托借书"},
    {"name": "图书馆借还书系统", "url": ILS_URL, "use": "个人借阅记录、借还操作"},
    {"name": "图书馆 OPAC（本次数据源）", "url": OPAC_URL, "use": "馆藏全量检索（本模块已离线化）"},
]

# 官方口径待补充项：没有可靠来源就留空，绝不编造数字。
UNVERIFIED = [
    {"item": "开馆/闭馆时间（各楼层、节假日）",
     "why": "图书馆站与 OPAC 均为 JS 门户，页面内无开放时间文本，未取到官方口径",
     "how": "由图书馆官方公众号或 lib.gzasc.edu.cn 通知栏补齐后，写入本文件 open_hours 字段"},
    {"item": "借阅规则（可借册数、借期天数、续借次数与天数、超期处理）",
     "why": "同上，未取到官方成文规则；仅能从 OPAC 观察到“借出-应还日期”字段",
     "how": "以图书馆《读者手册》/借还书系统公告为准，补齐后写入 borrowing_rules"},
    {"item": "座位预约系统入口与规则",
     "why": "OPAC 的预约服务是“图书预约请求”，不是座位预约；座位预约在独立系统/APP，本站未接入",
     "how": "向图书馆确认后补 seat_booking 字段（系统名、入口、可约时段、违约规则）"},
    {"item": "办证/开通借阅权限流程",
     "why": "新生入馆教育后开通借阅权限的说法见于项目内示例数据，非官方来源",
     "how": "以图书馆通知为准，补齐后写入 reader_card"},
]


def floor_of(name: str) -> str | None:
    """从馆藏地名称里解析楼层：先认 3 位房号（201/805/901对面），再认“X楼”。

    房号是个 3 位数，首位即楼层；用前后非数字断言，避免切到“书墙20”这类编号。
    """
    m = re.search(r"(?<!\d)(\d)\d\d(?!\d)", name)
    if m:
        return FLOOR.get(m.group(1))
    for cn, code in (("一楼", "1"), ("二楼", "2"), ("三楼", "3"), ("四楼", "4"),
                     ("五楼", "5"), ("六楼", "6"), ("七楼", "7"), ("八楼", "8"),
                     ("九楼", "9")):
        if cn in name:
            return FLOOR[code]
    return None


def main() -> None:
    if not DB.is_file():
        raise SystemExit(f"找不到馆藏库：{DB}")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row

    total, paper, ebook, onshelf, classes = c.execute(
        "SELECT COUNT(*), SUM(p_count>0), SUM(p_count=0), SUM(onshelf), "
        "COUNT(DISTINCT class_no) FROM books").fetchone()
    items = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    # 单册实时状态（在架 / 借出 / 逾期 / 丢失）
    live = {"on_shelf": 0, "borrowed": 0, "overdue": 0, "lost": 0, "other": 0}
    due_dates: list[str] = []
    for r in c.execute(
            "SELECT process_type, fetched_at, COUNT(*) n FROM items GROUP BY 1,2"):
        pt = (r["process_type"] or "").strip()
        if pt.startswith("借出"):
            live["borrowed"] += r["n"]
            m = re.search(r"(\d{4}-\d{2}-\d{2})", pt)
            if m and m.group(1).startswith("20"):
                due_dates.append(m.group(1))
                due = time.strptime(m.group(1), "%Y-%m-%d")
                if due < time.localtime(r["fetched_at"] or 0):
                    live["overdue"] += r["n"]
        elif pt == "在架":
            live["on_shelf"] += r["n"]
        elif pt == "丢失":
            live["lost"] += r["n"]
        else:
            live["other"] += r["n"]

    # 中图法各大类（全库口径）。少数条目的分类号首字母是小写（数据噪声），
    # 按大写归并，避免出现两个“D 政治、法律”。
    acc: dict[str, dict] = {}
    for r in c.execute(
            "SELECT substr(class_no,1,1) c, COUNT(*) n, SUM(p_count>0) p "
            "FROM books GROUP BY 1"):
        code = (r["c"] or "").upper()
        if code not in CN:
            continue
        d = acc.setdefault(code, {"code": code, "name": CN[code],
                                  "total": 0, "paper": 0, "ebook": 0})
        d["total"] += r["n"]
        d["paper"] += r["p"] or 0
        d["ebook"] += r["n"] - (r["p"] or 0)
    dist = sorted(acc.values(), key=lambda d: -d["total"])

    # 馆藏地 × 中图法大类（只统计有实物的单册）
    loc_cls = collections.defaultdict(collections.Counter)
    cls_loc = collections.defaultdict(collections.Counter)
    loc_items = collections.Counter()
    for r in c.execute(
            "SELECT i.location_name loc, b.class_no cn, COUNT(*) n FROM items i "
            "JOIN books b ON b.record_id=i.record_id GROUP BY 1,2"):
        loc, code = r["loc"] or "", (r["cn"] or "")[:1].upper()
        loc_items[loc] += r["n"]
        if code in CN:
            loc_cls[loc][code] += r["n"]
            cls_loc[code][loc] += r["n"]

    by_floor: dict[str, list] = collections.defaultdict(list)
    for loc, n in loc_items.most_common():
        fl = floor_of(loc) or "其他（书墙/走廊）"
        top_cls = [k for k, _ in loc_cls[loc].most_common(3)]
        by_floor[fl].append({
            "name": loc, "items": n,
            "real_name": loc.split("-", 1)[-1].strip(),
            "classes": top_cls,
            "class_names": [CN[k] for k in top_cls],
        })
    floor_guide = [{"floor": f, "locations": by_floor[f]}
                   for f in FLOOR_ORDER + ["其他（书墙/走廊）"] if by_floor.get(f)]

    class_floor = []
    for code in sorted(cls_loc, key=lambda k: -sum(cls_loc[k].values())):
        fl = collections.Counter()
        for loc, n in cls_loc[code].items():
            fl[floor_of(loc) or "其他（书墙/走廊）"] += n
        class_floor.append({
            "code": code, "name": CN[code],
            "floors": [[f, n] for f, n in fl.most_common() if f in FLOOR_ORDER],
            "locations": [{"name": loc, "items": n}
                          for loc, n in cls_loc[code].most_common(5)],
        })

    facts = {
        "meta": {
            "module": "library-guide",
            "title": "图书馆导览（新生入馆教学模块）",
            "school": "广州应用科技学院（广应科）",
            "campus": "肇庆校区（北岭山下、丰乐渠畔）",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "catalog_source": "图书馆 OPAC 全量抓取（mfindgzyykj.libsp.cn）",
            "catalog_fetched_at": "2026-09-13",
            "note": "catalog/location/class_floor 为机器生成的真实数据；"
                    "spaces/official_channels 为官方来源人工维护；"
                    "unverified 为未取到官方口径的待补充项，禁止编造。",
        },
        "catalog": {
            "total_records": total, "paper": paper, "ebook": ebook,
            "onshelf_records": onshelf or 0, "item_records": items,
            "class_numbers": classes,
        },
        "live_snapshot": {
            "as_of": "2026-09-13（抓取快照，非实时）",
            **live,
            "borrowed_due_range": [min(due_dates), max(due_dates)] if due_dates else None,
        },
        "class_distribution": dist,
        "floor_guide": floor_guide,
        "class_floor_map": class_floor,
        "spaces": SPACES,
        "official_channels": OFFICIAL_CHANNELS,
        "unverified": UNVERIFIED,
        # 留给人工补齐官方口径的槽位（保持为 null，便于脚本回填）
        "open_hours": None,
        "borrowing_rules": None,
        "seat_booking": None,
        "reader_card": None,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {OUT}")
    print(f"  书目 {total:,} 条（纸本 {paper:,} / 电子书 {ebook:,}），"
          f"馆藏单册 {items:,} 册，覆盖 {classes:,} 个分类号")
    print(f"  书库/书墙 {len(loc_items)} 个，楼层 {len(floor_guide)} 组，"
          f"中图法大类 {len(class_floor)} 个")
    print(f"  单册状态：在架 {live['on_shelf']:,} / 借出 {live['borrowed']}"
          f"（其中逾期 {live['overdue']}）/ 丢失 {live['lost']}")


if __name__ == "__main__":
    main()
