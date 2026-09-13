"""图书馆馆藏离线检索:本校图书馆 OPAC 全量书目的本地 SQLite 秒查。

数据来源:图书馆 OPAC(mfindgzyykj.libsp.cn)全量抓取,含书名/作者/出版社/
出版年/ISBN/中图法分类号/索书号/馆藏册数/在架数/馆藏地/条码/简介。
库文件放在工作区 crawled/mfindgzyykj.libsp.cn/library.db(约 1.3 GB,十万级
以上记录),与 kb/*.json 分开:kb.py 每次检索都会把整个 JSON 读进内存做
2-gram 打分,只适合几百条的资料;133 万条书目必须走 SQLite 索引。

用户问「图书馆有没有某本书」「某本书在哪个书库」「索书号是多少」「某作者
有哪些书」时直接查本地库,毫秒级返回,不必联网等 OPAC 响应。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from minicode.paths import app_path, resource_path

DOMAIN = "mfindgzyykj.libsp.cn"
DB_NAME = "library.db"
REL_DIR = ("crawled", DOMAIN)

# 用户问「在哪」时,把馆藏位置排到最前面展示
DEFAULT_TOP_K = 8
MAX_TOP_K = 30

_CACHE: dict[str, tuple[float, sqlite3.Connection]] = {}


def db_candidates(workspace: Path | None = None) -> list[Path]:
    """库文件位置:工作区优先,其次 exe 旁 / 只读资源目录(兼容打包形态)。"""
    out: list[Path] = []
    if workspace:
        out.append(Path(workspace).joinpath(*REL_DIR, DB_NAME))
    out.append(app_path(*REL_DIR, DB_NAME))
    out.append(resource_path(*REL_DIR, DB_NAME))
    seen, uniq = set(), []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def db_path(workspace: Path | None = None) -> Path | None:
    for p in db_candidates(workspace):
        if p.is_file():
            return p
    return None


def conn(path: Path) -> sqlite3.Connection:
    """按 mtime 缓存连接:库更新后自动重连。"""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        raise
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    if hit is not None:
        try:
            hit[1].close()
        except sqlite3.Error:
            pass
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    _CACHE[key] = (mtime, c)
    return c


SEL = (
    "SELECT b.record_id,b.title,b.author,b.publisher,b.year,b.isbn,b.class_no,"
    "b.call_no,b.p_count,b.onshelf,substr(b.abstract,1,220) AS abstract "
    "FROM books b "
)
# 馆藏地(可能多册分布在不同书库,合并去重)
LOC_SQL = (
    "SELECT group_concat(DISTINCT COALESCE(NULLIF(i.real_location,''),i.location_name)) "
    "FROM items i WHERE i.record_id=?"
)


def _fts_expr(query: str) -> str | None:
    """FTS5 trigram:整词加引号,空格视为 AND。"""
    toks = [t for t in query.replace('"', " ").split() if t]
    if not toks:
        return None
    return " AND ".join('"%s"' % t for t in toks)


def _kind_clause(kind: str) -> str:
    if kind == "paper":
        return " AND b.p_count>0"
    if kind == "ebook":
        return " AND b.p_count=0"
    return ""


def _like(term: str) -> str | None:
    """把用户输入转成 LIKE 模式:转义 %/_/\\,全部是通配符时返回 None(避免匹配全库)。"""
    esc = (term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")).strip()
    if not esc:
        return None
    return "%" + esc + "%"


def _rank_sql(i: int) -> str:
    """排序:书名完全匹配 → 书名以查询词开头 → 书名含查询词 → 其他;
    同档按馆藏册数多、书名短优先(更贴近用户想找的那本)。
    参数占位从 ?i 起编号(调用方按序补 query / prefix / contains / limit)。
    """
    return (" ORDER BY CASE WHEN b.title=?%d THEN 0 "
            "WHEN b.title LIKE ?%d ESCAPE '\\' THEN 1 "
            "WHEN b.title LIKE ?%d ESCAPE '\\' THEN 2 ELSE 3 END, "
            "b.p_count DESC, length(b.title) LIMIT ?%d " % (i, i + 1, i + 2, i + 3))


def _rank_vals(query: str) -> list:
    esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return [query, esc + "%", "%" + esc + "%"]


def search(workspace: Path | None, query: str, top_k: int = DEFAULT_TOP_K,
           kind: str = "all") -> list[dict]:
    """混合检索:精确书名 → FTS5 trigram(≥3 字,毫秒级) → LIKE 兜底。

    单字/两字关键词 FTS5 trigram 建不出索引,直接走 LIKE。
    """
    path = db_path(workspace)
    if path is None:
        return []
    query = (query or "").strip()
    if not query:
        return []
    c = conn(path)
    kc = _kind_clause(kind)
    rows: list[sqlite3.Row] = []

    # 1) 精确书名(含「书名. 第N版」这类带后缀的写法)
    try:
        rows += c.execute(
            SEL + "WHERE (b.title=? OR b.title=?)" + kc + " LIMIT 5",
            (query, query + ".")).fetchall()
    except sqlite3.Error:
        pass

    # 2) FTS5(≥3 字符);?1=MATCH ?2..?4=排序 ?5=limit
    if len(query) >= 3:
        expr = _fts_expr(query)
        if expr:
            try:
                rows += c.execute(
                    SEL + "JOIN books_fts f ON f.rowid=b.record_id "
                    "WHERE (books_fts MATCH ?1)" + kc + _rank_sql(2),
                    tuple([expr] + _rank_vals(query) + [top_k])).fetchall()
            except sqlite3.Error:
                pass

    # 3) LIKE 兜底(书名/作者/出版社/分类号/索书号);?1=LIKE ?2..?4=排序 ?5=limit
    if len(rows) < top_k:
        like = _like(query)
        if like:
            try:
                rows += c.execute(
                    SEL + "WHERE (b.title LIKE ?1 ESCAPE '\\' OR b.author LIKE ?1 ESCAPE '\\' "
                    "OR b.publisher LIKE ?1 ESCAPE '\\' OR b.class_no LIKE ?1 ESCAPE '\\' "
                    "OR b.call_no LIKE ?1 ESCAPE '\\')" + kc + _rank_sql(2),
                    tuple([like] + _rank_vals(query) + [top_k])).fetchall()
            except sqlite3.Error:
                pass

    seen, out = set(), []
    for r in rows:
        rid = r["record_id"]
        if rid in seen:
            continue
        seen.add(rid)
        d = dict(r)
        try:
            locs = c.execute(LOC_SQL, (rid,)).fetchone()[0]
        except sqlite3.Error:
            locs = None
        d["locs"] = locs or ""
        out.append(d)
        if len(out) >= top_k:
            break
    return out


def _fmt(entry: dict, idx: int) -> str:
    lines = [f"[{idx}] {entry['title']}"]
    meta = []
    if entry.get("author"):
        meta.append("作者: " + entry["author"])
    if entry.get("publisher"):
        meta.append("出版社: " + entry["publisher"])
    if entry.get("year"):
        meta.append("年份: " + str(entry["year"]))
    if meta:
        lines.append("    " + " · ".join(meta))
    lib = []
    if entry.get("call_no"):
        lib.append("索书号: " + entry["call_no"])
    if entry.get("class_no"):
        lib.append("中图法: " + entry["class_no"])
    p = entry.get("p_count") or 0
    lib.append(f"馆藏: {p} 册" + (f"(在架 {entry['onshelf']})" if entry.get("onshelf") else ""))
    lines.append("    " + " · ".join(lib))
    if entry.get("locs"):
        lines.append("    馆藏地: " + entry["locs"])
    if entry.get("isbn"):
        lines.append("    ISBN: " + str(entry["isbn"]))
    if entry.get("abstract"):
        lines.append("    简介: " + entry["abstract"].replace("\n", " ") + "…")
    return "\n".join(lines)


def library_search(ctx, query: str, top_k: int = DEFAULT_TOP_K,
                   kind: str = "all") -> str:
    """工具处理器:检索本地馆藏书目库。"""
    query = (query or "").strip()
    if not query:
        return "错误: 查询词不能为空。"
    try:
        top_k = max(1, min(int(top_k), MAX_TOP_K))
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    if db_path(ctx.workspace) is None:
        return ("错误: 本地馆藏书目库不存在。请先运行 "
                "crawled/mfindgzyykj.libsp.cn/ 下的抓取脚本生成 library.db。")
    hits = search(ctx.workspace, query, top_k=top_k, kind=kind)
    if not hits:
        return (f"本地馆藏书目库没有匹配「{query}」的记录(全库 133 万条)。"
                "可换作者名、ISBN、书名关键词或中图法分类号再试。")
    header = (f"本地馆藏书目库命中 {len(hits)} 条(数据源:图书馆 OPAC,已离线化):\n")
    tail = ("\n\n引用时给出书名、作者、索书号与馆藏地,并说明「据图书馆 OPAC 馆藏数据」。"
            "馆藏为 0 册的是电子书(无纸本馆藏位置)。")
    return header + "\n\n".join(_fmt(h, i) for i, h in enumerate(hits, 1)) + tail


def catalog_stats(workspace: Path | None = None) -> dict | None:
    """全库统计(给系统提示词/GUI 用)。"""
    path = db_path(workspace)
    if path is None:
        return None
    try:
        c = conn(path)
        total, paper, ebook, cls = c.execute(
            "SELECT COUNT(*), SUM(p_count>0), SUM(p_count=0), COUNT(DISTINCT class_no) "
            "FROM books").fetchone()
        return {"total": total or 0, "paper": paper or 0,
                "ebook": ebook or 0, "classes": cls or 0}
    except sqlite3.Error:
        return None


def library_prompt_section(workspace: Path | None = None) -> str:
    st = catalog_stats(workspace)
    if not st:
        return ""
    return (
        "\n# 本地馆藏书目库\n"
        f"已装载本校图书馆 OPAC 全量书目(共 {st['total']} 条:纸本 {st['paper']} 种、"
        f"电子书 {st['ebook']} 种;覆盖 {st['classes']} 个中图法分类号)。\n"
        "用户问「图书馆有没有某本书 / 某本书在哪 / 索书号是多少 / 某作者有哪些书 / "
        "某类书有哪些」时,必须先调用 library_search 工具查本地库,毫秒级返回,\n"
        "不要再去访问图书馆网站让用户等。library_search 返回书名、作者、出版社、"
        "索书号、中图法分类号、馆藏册数与馆藏地(书库/阅览室)。\n"
        "回答时给出书名+作者+索书号+馆藏地,并说明「据图书馆 OPAC 馆藏数据」;"
        "kind 参数可筛 paper(纸本,有馆藏位置)/ ebook(电子书)/ all(默认)。\n"
        "需要成批书目清单(整类导出、按类浏览)时,数据文件在 "
        "crawled/mfindgzyykj.libsp.cn/opac-catalog/ 下的 CSV。\n"
    )
