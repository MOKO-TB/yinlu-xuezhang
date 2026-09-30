"""图书馆馆藏离线检索:本校图书馆 OPAC 全量书目的本地 SQLite 秒查。

数据来源:图书馆 OPAC(mfindgzyykj.libsp.cn)全量抓取,含书名/作者/出版社/
出版年/ISBN/中图法分类号/索书号/馆藏册数/在架数/馆藏地/条码/简介。
库文件放在工作区 crawled/mfindgzyykj.libsp.cn/library.db(约 1.3 GB,十万级
以上记录),与 kb/*.json 分开:kb.py 每次检索都会把整个 JSON 读进内存做
2-gram 打分,只适合几百条的资料;133 万条书目必须走 SQLite 索引。

用户问「图书馆有没有某本书」「某本书在哪个书库」「索书号是多少」「某作者
有哪些书」时直接查本地库,毫秒级返回,不必联网等 OPAC 响应。

**完整性自检**:库是 1.3 GB 的大文件,分发/安装/拷贝都可能只拿到半截。
构建时会在库旁边写一份 `library.db.stamp.json` 凭证(大小/条数/sha256),
运行时 `catalog_health()` 拿它比对文件大小,启动就能发现「库没装全」,
不用等用户搜书搜不到才去猜。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from minicode.paths import app_path, resource_path

DOMAIN = "mfindgzyykj.libsp.cn"
DB_NAME = "library.db"
REL_DIR = ("crawled", DOMAIN)

# 用户问「在哪」时,把馆藏位置排到最前面展示
DEFAULT_TOP_K = 8
MAX_TOP_K = 30

_CACHE: dict[str, tuple[float, sqlite3.Connection]] = {}
# 缓存连接跨线程共用(GUI 主线程自检 + Agent 线程检索),而且是 RLock:
# 调用方现在**整条语句**(execute + fetch)都在这把锁里,进去还要再调 conn()。
_LIB_LOCK = threading.RLock()


class CatalogUnavailable(RuntimeError):
    """馆藏库找得到、但读不出来(损坏/被截断/被占用),或者本来就是空的。

    以前 `_fts_rows` / `_like_rows` 用 `except sqlite3.Error: return []` 兜掉,
    于是一个坏库会伪装成「没有这本书」,模型照着回用户「按这个书名没检索到」——
    用户报障「图书馆查询显示没有这一本书」里就有这一路(库文件在,查询全落空)。
    现在显式抛出,handler 会答「检索数据这次没加载起来」,而不是替图书馆下结论。
    """


def _note_error(errors: list[str] | None, exc: BaseException) -> None:
    """把底层 sqlite 错误攒起来(去重),交给上层决定怎么表述。"""
    if errors is None:
        return
    msg = f"{type(exc).__name__}: {exc}"
    if msg not in errors:
        errors.append(msg)


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
    """按 mtime 缓存连接:库更新后自动重连。

    连接必须 check_same_thread=False:自检跑在 webview 主线程,检索跑在
    Agent 后台线程,缓存里的连接会被两边先后使用——SQLite 默认只允许
    创建它的线程用,不然直接 ProgrammingError(用户看到的“图书馆数据读
    不出来”报错就是它)。缓存命中时连接可能已是别的线程建过的,所以
    **读一律走 `_read`**(execute + fetch 在同一次持锁里做完,游标不跨锁使用);
    只在 conn() 里上锁是不够的 —— 两条线程各自 execute 完再各自 fetch,
    取到的是别人那条语句的结果(并行子代理一路一个提示词,实测撞过:fetchone()
    回来 None,上层只捕 sqlite3.Error,于是整路子代理报"执行出错")。
    """
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        raise
    with _LIB_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        if hit is not None:
            try:
                hit[1].close()
            except sqlite3.Error:
                pass
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _CACHE[key] = (mtime, c)
        return c


def _read(c: sqlite3.Connection, sql: str, params: tuple = (),
          one: bool = False):
    """在锁里把一条语句跑完并取完结果。

    **execute 与 fetch 之间不许松锁**:缓存里那是一条跨线程共用的连接
    (`check_same_thread=False`),两条线程各自 execute 完再去 fetch,取到的是
    "最后压上来的那条语句"的结果。实测过的形状:并行子代理各建一次系统提示词
    (都读馆藏统计),`fetchone()` 回来一个 None,`total, paper, ebook, cls = …`
    当场 `TypeError: cannot unpack non-iterable NoneType object` —— 而这条路的
    上层只捕 `sqlite3.Error`,于是整路子代理报"执行出错"。
    """
    with _LIB_LOCK:
        cur = c.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


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


# --------------------------------------------------------------- 查询归一化
# 学生输入常带装饰与标注,而库里标题未必有:「《C程序设计》」「C语言程序设计
# (第5版)」「高等数学(上)」。整串当字面量去查必然 0 命中,于是被误判成
# "馆里没有这本书"。下面几组正则用于逐级放宽(只在上一级落空后才试下一级,
# 不会把"精确命中"放宽成"模糊命中")。

# 整串被书名号/引号包住:《三体》 / "三体"。这时书名本身才是用户要的
_WRAP_PAIR_RE = re.compile(r"^\s*[《〈「『\"'](.+?)[》〉」』\"']\s*$")
# 书名号等装饰字符(用于宽松一级的变体)
_WRAP_RE = re.compile(r"[《》〈〉「」『』\"']")
# 括号标注:(第5版)(修订本)(上册)
_ANNOT_PAREN_RE = re.compile(r"[（(][^）)]{0,12}[）)]")
# 裸标注:第5版 / 第2册 / 修订版
_ANNOT_BARE_RE = re.compile(
    r"第\s*[0-9一二三四五六七八九十百]+\s*[版次册卷辑]|修订版|增订本|影印本")
# 分隔符:半/全角空格、冒号、间隔号、逗号、顿号、斜杠
_SEP_RE = re.compile(r"[\s　：:·・，,；;、/|\\]+")
# 作为独立变体至少要 2 字:单字 LIKE 必然全库乱匹配
MIN_TERM_LEN = 2


def _variants(query: str) -> list[str]:
    """检索变体阶梯(从严到宽):原串 → 去书名号 → 去版次标注 → 拆词。

    返回去重后的候选列表,调用方按序尝试、命中即停。
    """
    out: list[str] = []

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", (s or "").strip())
        if s and s not in out:
            out.append(s)

    q = (query or "").strip()
    if not q:
        return out

    pair = _WRAP_PAIR_RE.match(q)
    if pair:
        # 整串被书名号包住:去壳形态优先(库里标题通常不带《》)
        add(pair.group(1))
        add(q)
    else:
        add(q)
        unwrapped = _WRAP_RE.sub("", q).strip()
        if unwrapped != q:
            add(unwrapped)

    # 去掉版本/卷册标注
    core = _ANNOT_PAREN_RE.sub(" ", _ANNOT_BARE_RE.sub(" ", _WRAP_RE.sub(" ", q)))
    core = re.sub(r"\s+", " ", core).strip()
    add(core)

    # 拆词:按原词序(保留"先试哪个词"的可预期性)
    for tok in _SEP_RE.split(core):
        if len(tok) >= MIN_TERM_LEN:
            add(tok)
    return out


def _fts_rows(c: sqlite3.Connection, variant: str, top_k: int,
              kind: str, errors: list[str] | None = None) -> list[sqlite3.Row]:
    """精确书名 + FTS5 trigram(仅 ≥3 字,毫秒级)。"""
    kc = _kind_clause(kind)
    rows: list[sqlite3.Row] = []
    # 1) 精确书名(含「书名.」这类带后缀的写法)
    try:
        rows += _read(c,
                      SEL + "WHERE (b.title=? OR b.title=?)" + kc + " LIMIT 5",
                      (variant, variant + "."))
    except sqlite3.Error as exc:
        _note_error(errors, exc)
    # 2) FTS5;?1=MATCH ?2..?4=排序 ?5=limit
    if len(variant) >= 3:
        expr = _fts_expr(variant)
        if expr:
            try:
                rows += _read(c,
                              SEL + "JOIN books_fts f ON f.rowid=b.record_id "
                              "WHERE (books_fts MATCH ?1)" + kc + _rank_sql(2),
                              tuple([expr] + _rank_vals(variant) + [top_k]))
            except sqlite3.Error as exc:
                _note_error(errors, exc)
    return rows


def _like_rows(c: sqlite3.Connection, variant: str, top_k: int,
               kind: str, errors: list[str] | None = None) -> list[sqlite3.Row]:
    """LIKE 兜底(书名/作者/出版社/分类号/索书号)。

    注意:前导通配符用不上索引,1.3 M 行是全表扫(约 1.5 s),因此一次检索
    最多只走一次。
    """
    like = _like(variant)
    if not like:
        return []
    try:
        return _read(c,
                     SEL + "WHERE (b.title LIKE ?1 ESCAPE '\\' OR b.author LIKE ?1 ESCAPE '\\' "
                     "OR b.publisher LIKE ?1 ESCAPE '\\' OR b.class_no LIKE ?1 ESCAPE '\\' "
                     "OR b.call_no LIKE ?1 ESCAPE '\\')" + _kind_clause(kind) + _rank_sql(2),
                     tuple([like] + _rank_vals(variant) + [top_k]))
    except sqlite3.Error as exc:
        _note_error(errors, exc)
        return []


def search(workspace: Path | None, query: str, top_k: int = DEFAULT_TOP_K,
           kind: str = "all") -> list[dict]:
    """按变体阶梯检索:精确书名 → FTS5 trigram → LIKE 兜底。

    每个变体先走"精确 + FTS"(毫秒级),命中即停;全落空时只对最宽的那个变体
    做一次 LIKE 全表扫。这样既修掉"带书名号/版次标注就查不到",又不会因为
    多试几级而把耗时翻好几倍。
    """
    path = db_path(workspace)
    if path is None:
        return []
    query = (query or "").strip()
    if not query:
        return []
    try:
        c = conn(path)
    except (sqlite3.Error, OSError) as exc:
        _note_error(None, exc)
        raise CatalogUnavailable(
            f"打开 {path.name} 失败({type(exc).__name__}: {exc})") from exc
    variants = _variants(query)
    errors: list[str] = []

    rows: list[sqlite3.Row] = []
    for v in variants:
        if len(v) < 3:
            # 1-2 字 FTS5 trigram 建不出索引,这个变体只能走 LIKE
            rows = _like_rows(c, v, top_k, kind, errors)
        else:
            rows = _fts_rows(c, v, top_k, kind, errors)
            if rows and len(rows) < top_k:
                rows += _like_rows(c, v, top_k, kind, errors)   # 命中不足时补足(与旧行为一致)
        if rows:
            break

    if not rows and variants and len(variants[-1]) >= 3:
        rows = _like_rows(c, variants[-1], top_k, kind, errors)   # 全落空:对最宽的变体扫一次

    if not rows and errors:
        # 库在,但查不了:这是环境问题,不能回给用户“没有这本书”
        raise CatalogUnavailable(errors[0])

    seen, out = set(), []
    for r in rows:
        rid = r["record_id"]
        if rid in seen:
            continue
        seen.add(rid)
        d = dict(r)
        try:
            row = _read(c, LOC_SQL, (rid,), one=True)
        except sqlite3.Error:
            row = None
        d["locs"] = (row[0] if row else "") or ""
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


def snapshot_date(workspace: Path | None = None) -> str:
    """馆藏数据的抓取日期(stamp 的 built_at 取日期部分);没有凭证返回空串。

    诚实口径:**有凭证才说日期**。没有 stamp(开发态/老包)时不拿库文件的
    mtime 冒充 —— 那可能是拷贝时间,不是抓取时间,说了就是编。
    """
    p = db_path(workspace)
    if p is None:
        return ""
    return str(read_stamp(p).get("built_at") or "").strip()[:10]


def snapshot_note(workspace: Path | None = None) -> str:
    """检索结果尾部那句"在架数是快照不是实时"。没有抓取日期就不说。"""
    day = snapshot_date(workspace)
    if not day:
        return ""
    return (f"(馆藏数据抓取于 {day};「在架 N 册」是那天的快照,不是实时 ——"
            "以现场/图书馆系统为准)")


def _floor_hint(workspace: Path | None, hits: list[dict]) -> str:
    """把命中书目涉及的中图法大类映射成楼层,顺带答掉「在几楼」。

    用户问「有没有X、在哪」时,原来要 library_search + library_guide 两次工具调用
    (两轮模型往返)。这里直接把楼层并进检索结果,一次就够。数据包缺失时静默跳过。
    """
    try:
        from minicode.library_guide import load_facts
        facts = load_facts(workspace)
    except Exception:                                             # noqa: BLE001
        return ""
    if not facts:
        return ""
    table = {r.get("code"): r for r in (facts.get("class_floor_map") or [])}
    codes: list[str] = []
    for h in hits:
        cn = str(h.get("class_no") or "").strip()
        code = cn[:1].upper()
        if code in table and code not in codes:
            codes.append(code)
    if not codes:
        return ""
    parts = []
    for code in codes[:4]:
        row = table[code]
        floors = "、".join(f for f, _ in (row.get("floors") or [])[:2]) or "—"
        locs = ";".join(l["name"].split("-", 1)[-1].strip()
                        for l in (row.get("locations") or [])[:2])
        parts.append(f"{code}({row.get('name', '')})→{floors}" + (f" {locs}" if locs else ""))
    return "\n\n楼层提示(中图法大类→楼层,可直接作答,不必再查):" + " / ".join(parts)


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
    try:
        hits = search(ctx.workspace, query, top_k=top_k, kind=kind)
    except CatalogUnavailable as exc:
        p = db_path(ctx.workspace)
        size = "尺寸未知"
        try:
            size = f"{p.stat().st_size / 1e9:.2f} GB"
        except OSError:
            pass
        return (
            f"错误: 本地馆藏书目库读不出来({exc})。\n"
            f"库文件: {p}({size};完整版应为 1.31 GB / 133 万条书目)。\n"
            "⚠ 这是**检索数据没加载起来**,不是“馆里没有这本书”。\n"
            "请告诉用户「图书馆检索数据这次没读出来,请稍后重试或到检索台查」,\n"
            "**不要**说成“图书馆没有这本书”;同时把上面这行错误原文附给用户/开发者。")
    if not hits:
        total = catalog_total(ctx.workspace)
        if total == 0:
            return ("错误: 本地馆藏书目库是空的(0 条书目),检索不可用。\n"
                    "库文件存在但没有数据——多半是拷贝/安装时只拿到一个空壳。\n"
                    "请告诉用户「图书馆检索数据没加载起来」,不要说成“图书馆没有这本书”。")
        variants = _variants(query)
        tried = " / ".join(f"「{v}」" for v in variants[1:4]) or "—"
        # 落空进成长账本(growth.log_kb_gap,reason=library):维护者才能看到
        # "同学们在找什么书、用什么词搜不到"。库正常才记 —— 库坏/库空是环境问题,
        # 不是检索词的缺口(growth 那边的口径同此)。
        from minicode import growth
        growth.log_kb_gap(query, reason="library")
        return (
            f"本地馆藏书目库没有匹配「{query}」的记录。"
            f"(已自动放宽重试:{tried})\n"
            f"本次对照的是 {db_path(ctx.workspace)}(库内共 {total} 条书目)。\n"
            "⚠ 这只说明**库里没有这种书名写法**,不等于馆里没有这本书。\n"
            "若上面这个总条数是 0 或明显偏小,说明库没装好——那就别说“没检索到”,\n"
            "直接告诉用户检索数据没加载起来。\n"
            "否则请先用**更短的书名关键词**(2-6 字,去掉《》、“（第N版）”这类\n"
            "书名号与版次标注)或作者名/ISBN/中图法分类号再查一次。\n"
            "若仍无结果,向用户如实说「按这个书名没检索到」,并建议改用\n"
            "关键词重试或到图书馆检索台查;不要直接说成“图书馆没有这本书”。")
    header = (f"本地馆藏书目库命中 {len(hits)} 条(数据源:图书馆 OPAC,已离线化):\n")
    tail = ("\n\n引用时给出书名、作者、索书号与馆藏地,并说明「据图书馆 OPAC 馆藏数据」。"
            "馆藏为 0 册的是电子书(无纸本馆藏位置)。")
    snap = snapshot_note(ctx.workspace)
    return (header + "\n\n".join(_fmt(h, i) for i, h in enumerate(hits, 1))
            + _floor_hint(ctx.workspace, hits) + tail
            + (f"\n{snap}" if snap else ""))


def catalog_total(workspace: Path | None = None) -> int | None:
    """库内书目总数;库不在或读不出来返回 None(给“到底加载了没有”做判据)。"""
    return catalog_count_or_error(workspace)[0]


def catalog_count_or_error(workspace: Path | None = None) -> tuple[int | None, str | None]:
    """(书目总数, 错误描述)。库不在 → (None, None);库在但查不了 → (None, 错误)。"""
    path = db_path(workspace)
    if path is None:
        return None, None
    try:
        row = _read(conn(path), "SELECT COUNT(*) FROM books", one=True)
    except (sqlite3.Error, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return (int(row[0]) if row else 0), None


# ------------------------------------------------------------------ 完整性自检
# 库是 1.3 GB 的大文件:安装中途没空间、拷贝被中断、杀软动过它,都可能只留下
# 半截。旧版对此毫无察觉,直到用户搜书才表现为「没有这一本书」——最难查的一类
# 现场问题。构建时写一份凭证(大小/条数/sha256),运行时比对大小即可秒判。

STAMP_SUFFIX = ".stamp.json"


def stamp_path_for(db: Path) -> Path:
    return db.with_name(db.name + STAMP_SUFFIX)


def stamp_path(workspace: Path | None = None) -> Path | None:
    p = db_path(workspace)
    return stamp_path_for(p) if p is not None else None


def read_stamp(db: Path) -> dict:
    """读凭证;没有/坏了就返回空 dict(老包与开发态都没有凭证,不能当错误)。"""
    try:
        data = json.loads(stamp_path_for(db).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_stamp(db: Path, count: int, extra: dict | None = None) -> Path:
    """给库文件写完整性凭证(**构建时**调用;要算完整 sha256,不要放启动路径里)。

    sha256 只在自检脚本里复核:每次启动重算 1.3 GB 太慢,启动只比文件大小。
    """
    h = hashlib.sha256()
    with open(db, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    data = {
        "schema": 1,
        "file": db.name,
        "size": db.stat().st_size,
        "count": int(count),
        "sha256": h.hexdigest(),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.update(extra or {})
    sp = stamp_path_for(db)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return sp


def catalog_health(workspace: Path | None = None) -> dict:
    """馆藏库能不能用:启动自检用(只看文件大小 + 开库查表,毫秒级)。

    status:
      ok          可用
      missing     库文件不在(没装/被杀软删了/单文件版本就不带)
      incomplete  库文件不在凭证记录的大小上(没装全/被截断)
      unreadable  文件在,但打不开或查不了(损坏/被占用)
      empty       库能开,但里面 0 条书目(空壳)
    """
    path = db_path(workspace)
    if path is None:
        return {"status": "missing", "path": None}
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "unreadable", "path": path,
                "detail": f"{type(exc).__name__}: {exc}"}
    stamp = read_stamp(path)
    expect = stamp.get("size")
    if isinstance(expect, int) and expect > 0 and size != expect:
        return {"status": "incomplete", "path": path, "size": size,
                "expected": expect, "count": stamp.get("count")}
    total, err = catalog_count_or_error(workspace)
    if err is not None:
        return {"status": "unreadable", "path": path, "size": size, "detail": err}
    if not total:
        return {"status": "empty", "path": path, "size": size}
    want = stamp.get("count")
    if isinstance(want, int) and want != total:
        return {"status": "incomplete", "path": path, "size": size,
                "total": total, "count": want, "expected": expect}
    return {"status": "ok", "path": path, "size": size, "total": total}


# 完整版参照值(仅用于给用户一句可核对的期望值,不做判定 —— 真正的判定走凭证)
FULL_SIZE_TEXT = "约 1.31 GB / 133 万条书目"
REINSTALL_HINT = ("图书馆搜书会不可用。请腾出磁盘空间后重新运行安装包/重新解压一次。")


def health_notice(health: dict) -> str | None:
    """把自检结果翻成给用户看的一句话;一切正常返回 None(界面不吵)。"""
    st = health.get("status")
    if st == "ok" or st is None:
        return None
    path = health.get("path")
    size = health.get("size")
    size_txt = f"{size / 1e9:.2f} GB" if isinstance(size, int) else "未知"
    if st == "missing":
        return (f"⚠ 图书馆馆藏数据未安装({path})。"
                f"图书馆搜书不可用。完整版包内自带 {FULL_SIZE_TEXT}。")
    if st == "incomplete":
        want = health.get("expected")
        want_txt = f"{want / 1e9:.2f} GB" if isinstance(want, int) else FULL_SIZE_TEXT
        return (f"⚠ 图书馆馆藏数据不完整:现为 {size_txt},应为 {want_txt}。"
                + REINSTALL_HINT)
    if st == "empty":
        return (f"⚠ 图书馆馆藏数据是空的(库内 0 条书目,文件 {size_txt})。"
                + REINSTALL_HINT)
    detail = health.get("detail") or "未知错误"
    return (f"⚠ 图书馆馆藏数据读不出来({detail};文件 {size_txt})。"
            + REINSTALL_HINT)


def catalog_stats(workspace: Path | None = None) -> dict | None:
    """全库统计(给系统提示词/GUI 用)。"""
    path = db_path(workspace)
    if path is None:
        return None
    try:
        c = conn(path)
        row = _read(c,
                    "SELECT COUNT(*), SUM(p_count>0), SUM(p_count=0), COUNT(DISTINCT class_no) "
                    "FROM books", one=True)
    except sqlite3.Error:
        return None
    if row is None:          # 一条 COUNT 不可能没有结果行:出现了就是这条连接被人动过,
        return None          # 按"读不出来"走 —— 宁可少一句馆藏统计,不报一个猜出来的数
    total, paper, ebook, cls = row
    return {"total": total or 0, "paper": paper or 0,
            "ebook": ebook or 0, "classes": cls or 0}


def library_prompt_section(workspace: Path | None = None) -> str:
    st = catalog_stats(workspace)
    if not st or not st.get("total"):
        # 库文件在、但读不出来或是空壳:必须让模型知道,否则它会把「检索不可用」
        # 说成「图书馆没有这本书」(用户报障就是这么来的)。
        if db_path(workspace) is not None:
            return (
                "\n# 本地馆藏书目库(异常)\n"
                f"馆藏库文件在({db_path(workspace)}),但**读不出来或里面没有数据**"
                "(0 条书目)。\n"
                "此时禁止回答“馆里没有这本书 / 没检索到”;一律告诉用户"
                "「图书馆检索数据这次没加载起来,请稍后重试或到图书馆检索台查」。\n"
                "同时把这行异常告知用户/开发者(检查库文件是否完整:完整版约 "
                "1.31 GB、133 万条书目)。\n"
            )
        return ""
    snap_day = snapshot_date(workspace)
    snap_line = (f"馆藏数据抓取于 {snap_day}:「在架 N 册」是**那天的快照**,不是实时 ——"
                 "回答时带上这层意思(「数据显示在架 2 册」),别把它说成此刻一定有书。\n"
                 if snap_day else "")
    return (
        "\n# 本地馆藏书目库\n"
        f"已装载本校图书馆 OPAC 全量书目(共 {st['total']} 条:纸本 {st['paper']} 种、"
        f"电子书 {st['ebook']} 种;覆盖 {st['classes']} 个中图法分类号)。\n"
        + snap_line +
        "用户问「图书馆有没有某本书 / 某本书在哪 / 索书号是多少 / 某作者有哪些书 / "
        "某类书有哪些」时,必须先调用 library_search 工具查本地库,毫秒级返回,\n"
        "不要再去访问图书馆网站让用户等。library_search 返回书名、作者、出版社、"
        "索书号、中图法分类号、馆藏册数与馆藏地(书库/阅览室)。\n"
        "回答时给出书名+作者+索书号+馆藏地,并说明「据图书馆 OPAC 馆藏数据」;"
        "kind 参数可筛 paper(纸本,有馆藏位置)/ ebook(电子书)/ all(默认)。\n"
        "**检索纪律(直接决定用户等待时间)**:\n"
        "- 书名号《》与版次标注“（第N版）/（上）/：副标题”工具**已自动逐级放宽**,\n"
        "  直接用用户原话查一次即可,不必自己删改后再查一次。\n"
        "- 一次 library_search 的结果通常就够答,拿到结果**立刻作答**,不要用同义词、\n"
        "  近义书名、拆词反复重查;确实没命中,最多再试一次作者名或 ISBN 就停。\n"
        "- 查询词只用用户**真实提到**的书名/作者/主题,不要自己臆造书名再去找\n"
        "  (查不到并不代表馆里没有,只代表库里没有这个书名);中文书名不要翻译成外文重试。\n"
        "- **绝不要把“库里没查到”说成“图书馆没有这本书”**:如实说“按这个书名没检索到”,\n"
        "  并建议换关键词重试或到馆检索台查。\n"
        "- 结果里的「楼层提示」已经把中图法大类映射成楼层,据此回答即可,不必再调\n"
        "  library_guide(那是一次多余的模型往返)。\n"
        "需要成批书目清单(整类导出、按类浏览)时,数据文件在 "
        "crawled/mfindgzyykj.libsp.cn/opac-catalog/ 下的 CSV。\n"
    )
