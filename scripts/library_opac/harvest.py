# -*- coding: utf-8 -*-
"""
广应科图书馆 OPAC 全量书目采集器  (超星/图星 统一检索, 无需登录)
=================================================================
数据源 : https://mfindgzyykj.libsp.cn
特点   : 全馆 ~136 万条书目; 单查询深分页上限 10000 条(offset cap)

分区策略 (自适应二分):
  维度1 中图法分类号前缀  —— 逐字扩展 (第2位: 数字+字母; 第3位起: 数字 . -)
  维度2 出版年区间        —— 前缀切不动时, 按年二分(已验证覆盖 100%)
  维度3 校区 / 馆藏地 / 资源类型 —— 兜底细分
  保证每个叶子分区 <= MAX_N 条, 全部可断点续跑

用法:
  python harvest.py preflight        # 连通性 + 规模
  python harvest.py discover         # 建分区(自适应)
  python harvest.py harvest          # 抓书目
  python harvest.py all              # discover + harvest
  python harvest.py status
  python harvest.py enrich [N]       # 抓某条书的馆藏位置(索书号/馆藏地/条码)
"""
import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

BASE = "https://mfindgzyykj.libsp.cn"
GROUP_CODE = "200542"
LIB_CODE = "20054200001"
OUT_DIR = Path(r"D:\game\library")
DB_PATH = OUT_DIR / "books.db"
LOG_PATH = OUT_DIR / "harvest.log"

ROWS = 50
MAX_N = 9000
WORKERS = 8
PAGE_CAP = 200
RETRY = 4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json;charset=UTF-8",
    "mappingPath": "",
    "groupCode": GROUP_CODE,
    "Origin": BASE,
    "Referer": BASE + "/",
}

SEARCH_BODY = {
    "docCode": [None], "litCode": [], "searchFieldContent": "", "searchField": "classNo",
    "matchMode": "2", "resourceType": [], "subject": [], "discode1": [], "publisher": [],
    "locationId": [], "collectionName": [], "author": [], "langCode": [], "countryCode": [],
    "publishBegin": None, "publishEnd": None, "coreInclude": [], "ddType": [], "verifyStatus": [],
    "group": [], "sortField": "relevance", "sortClause": "desc", "page": 1, "rows": ROWS,
    "onlyOnShelf": None, "campusId": [], "curLocationId": [], "eCollectionIds": [], "kindNo": [],
    "libCode": [], "neweCollectionIds": [], "searchItems": None, "newCoreInclude": [],
    "customSub": [], "customSub0": [],
}

L1 = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
CH2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"          # 二级类目字符
CH3 = "0123456789.-"                                   # 三级及以后
CAMPUS = [["1"], ["2"]]
RTYPE = [["1"], ["2"]]
YEAR_LO, YEAR_HI = 1000, 2100

_log_lock = threading.Lock()
_counter = {"q": 0}
HTTP_POOL = ThreadPoolExecutor(max_workers=20)
_count_cache = {}


def log(*a):
    msg = " ".join(str(x) for x in a)
    with _log_lock:
        line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


_tls = threading.local()


def sess():
    if not hasattr(_tls, "s"):
        s = requests.Session()
        s.headers.update(HEADERS)
        ad = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        _tls.s = s
    return _tls.s


def search(**kw):
    body = dict(SEARCH_BODY)
    for k, v in kw.items():
        body[k] = v
    last = None
    for attempt in range(RETRY):
        try:
            r = sess().post(BASE + "/find/unify/search", json=body, timeout=60)
            j = r.json()
            if j.get("success"):
                with _log_lock:
                    _counter["q"] += 1
                return j.get("data") or {}
            last = j.get("message")
        except Exception as e:                                    # noqa: BLE001
            last = repr(e)[:120]
        time.sleep(0.8 + attempt * 1.2)
    log("  !! search failed:", {k: v for k, v in kw.items() if k != "searchField"}, "->", last)
    return None


# --------------------------------------------------------------- 分区数据结构
def P(classNo="", yLo=None, yHi=None, campus=None, loc=None, rt=None, exact=False):
    return {"classNo": classNo, "yLo": yLo, "yHi": yHi,
            "campus": campus, "loc": loc, "rt": rt, "exact": exact}


def pkey(p):
    return json.dumps(p, sort_keys=True, ensure_ascii=False)


def params(p, **over):
    d = {"searchFieldContent": p["classNo"]}
    if p.get("exact"):
        d["kindNo"] = [p["classNo"]]
    if p.get("yLo") or p.get("yHi"):
        d["publishBegin"] = p["yLo"]
        d["publishEnd"] = p["yHi"]
    if p.get("campus"):
        d["campusId"] = p["campus"]
    if p.get("loc"):
        d["locationId"] = p["loc"]
    if p.get("rt"):
        d["resourceType"] = p["rt"]
    d.update(over)
    return d


def count(p, **over):
    k = pkey({**p, "_o": over})
    if k in _count_cache:
        return _count_cache[k]
    d = search(rows=1, page=1, **params(p, **over))
    v = None if d is None else (d.get("numFound") or 0)
    if v is not None:
        _count_cache[k] = v
    return v


def count_many(plist):
    """并行统计多个分区的 count (复用全局 HTTP 连接池)"""
    out = {}
    futs = {HTTP_POOL.submit(count, p): pkey(p) for p in plist}
    for f in as_completed(futs):
        try:
            out[futs[f]] = f.result()
        except Exception as e:                                    # noqa: BLE001
            log("  count err", repr(e)[:100])
            out[futs[f]] = None
    return out


def child_prefixes(p):
    cn = p["classNo"]
    if cn == "":
        return L1
    if len(cn) == 1:
        chars = CH2
    else:
        chars = CH3
    return [cn + c for c in chars]


def bisect_year(p, n):
    """按出版年把分区一分为二; 返回 [left,right] 或 None"""
    lo = int(p["yLo"] or YEAR_LO)
    hi = int(p["yHi"] or YEAR_HI)

    def c(a, b):
        return count(p, publishBegin=a, publishEnd=b) or 0

    if hi - lo <= 1:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        left = c(p["yLo"], str(mid))
        right = c(str(mid + 1), p["yHi"])
        if left == 0 or right == 0:               # 全在一边
            if left == 0:
                lo = mid
            else:
                hi = mid
            continue
        if max(left, right) * 10 < n * 9:         # 拆得动
            a = P(p["classNo"], p["yLo"], str(mid), p["campus"], p["loc"], p["rt"], p["exact"])
            b = P(p["classNo"], str(mid + 1), p["yHi"], p["campus"], p["loc"], p["rt"], p["exact"])
            return [(a, left), (b, right)]
        if left > right:
            hi = mid
        else:
            lo = mid
    return None


def split(p, n):
    """把一个超过 MAX_N 的分区拆成子分区; 返回 [(child, count), ...]"""
    cn = p["classNo"]
    # 维度1: 出版年二分 (便宜, 覆盖 100%)
    y = bisect_year(p, n)
    if y:
        return y
    # 维度2: 分类号前缀扩展 (+ 前缀本身作为"精确分类号"残余分区)
    if not p.get("exact") and cn:
        kids = [P(c, p["yLo"], p["yHi"], p["campus"], p["loc"], p["rt"]) for c in child_prefixes(p)]
        cm = count_many(kids)
        live = [(k, cm[pkey(k)]) for k in kids if (cm.get(pkey(k)) or 0) > 0]
        rest = count(P(cn, p["yLo"], p["yHi"], p["campus"], p["loc"], p["rt"], exact=True))
        cov = sum(c for _, c in live) + (rest or 0)
        if (len(live) >= 1 or (rest or 0) > 0) and cov >= n * 0.995:
            if (rest or 0) > 0:
                live.append((P(cn, p["yLo"], p["yHi"], p["campus"], p["loc"], p["rt"], exact=True), rest))
            return live
    # 维度3: 校区 / 资源类型
    for dim, vals in (("campus", CAMPUS), ("rt", RTYPE)):
        if p.get(dim):
            continue
        kids = []
        for v in vals:
            k = dict(p)
            k[dim] = v
            kids.append(P(**k))
        cm = count_many(kids)
        live = [(k, cm[pkey(k)]) for k in kids if (cm.get(pkey(k)) or 0) > 0]
        if len(live) >= 2 and sum(c for _, c in live) >= n * 0.90:
            return live
    return [(p, n)]                                # 拆不动, 原样返回


def load_locations():
    try:
        r = sess().post(BASE + "/find/groupResource/dict", json={}, timeout=40)
        locs = (r.json().get("data") or {}).get("locationId") or []
        return [str(x["code"]) for x in locs]
    except Exception:                                             # noqa: BLE001
        return []


# --------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  record_id INTEGER PRIMARY KEY,
  title TEXT, author TEXT, publisher TEXT, isbn TEXT, year TEXT,
  pub_place TEXT, lang TEXT, class_no TEXT, call_no TEXT,
  abstract TEXT, subject TEXT, chi_subject TEXT,
  p_count INTEGER, onshelf INTEGER, e_count INTEGER, d_count INTEGER,
  doc_code INTEGER, doc_name TEXT, price TEXT, book_page TEXT,
  ecol TEXT, pic TEXT, part TEXT
);
CREATE INDEX IF NOT EXISTS idx_books_class ON books(class_no);
CREATE INDEX IF NOT EXISTS idx_books_call  ON books(call_no);
CREATE INDEX IF NOT EXISTS idx_books_pcnt  ON books(p_count);
CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);

CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
  title, author, publisher, abstract, class_no, call_no,
  content='books', content_rowid='record_id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS books_ai AFTER INSERT ON books BEGIN
  INSERT INTO books_fts(rowid,title,author,publisher,abstract,class_no,call_no)
  VALUES (new.record_id,new.title,new.author,new.publisher,new.abstract,new.class_no,new.call_no);
END;

CREATE TABLE IF NOT EXISTS parts(
  pkey TEXT PRIMARY KEY, params TEXT, num_found INTEGER, status TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS pages_done(
  pkey TEXT, page INTEGER, n INTEGER, ts REAL, PRIMARY KEY(pkey, page));
CREATE TABLE IF NOT EXISTS locations(code TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS items(
  item_id INTEGER PRIMARY KEY, record_id INTEGER, call_no TEXT, barcode TEXT,
  location_id TEXT, location_name TEXT, real_location TEXT,
  process_type TEXT, shelf_no TEXT, in_date TEXT, price REAL, fetched_at REAL);
CREATE INDEX IF NOT EXISTS idx_items_rec ON items(record_id);
"""


def db():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=180, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


_tdb = threading.local()


def tdb():
    if not hasattr(_tdb, "c"):
        _tdb.c = db()
    return _tdb.c


def init_db():
    c = db()
    c.executescript(SCHEMA)
    c.close()


# ------------------------------------------------------------------- transform
def rec_row(it, part):
    def s(v):
        if v is None:
            return None
        if isinstance(v, list):
            v = " ".join(str(x) for x in v if x)
        v = str(v).strip()
        return v or None

    cls = it.get("chiClassNo") or []
    cls = cls[0] if cls else None
    ab = it.get("adstract") or it.get("ddAbstract") or ""
    return (
        it.get("recordId"), s(it.get("title")), s(it.get("author")), s(it.get("publisher")),
        s(it.get("isbn")), s(it.get("publishYear")), s(it.get("pubPlace")), s(it.get("langCode")),
        s(cls), s(it.get("callNoOne")), (s(ab) or "")[:600] or None,
        s(it.get("subjectWord")), s(it.get("chiSubjectClass")),
        it.get("physicalCount"), it.get("onShelfCountI"), it.get("eCount"), it.get("dCount"),
        it.get("docCode"), s(it.get("docName")), s(it.get("price")), s(it.get("bookPage")),
        s(it.get("neweCollectionIds")), s(it.get("pic")), part,
    )


INSERT = """INSERT OR IGNORE INTO books(
 record_id,title,author,publisher,isbn,year,pub_place,lang,class_no,call_no,abstract,
 subject,chi_subject,p_count,onshelf,e_count,d_count,doc_code,doc_name,price,book_page,
 ecol,pic,part) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


# ---------------------------------------------------------------------- stages
def preflight():
    init_db()
    log("=== preflight ===")
    log("   locations in dict:", len(load_locations()))
    tot = 0
    for L in L1:
        n = count(P(L))
        log("   %s = %s" % (L, n))
        tot += n or 0
    log("   SUM(L1) =", tot)
    return tot


def discover():
    init_db()
    conn = db()
    conn.execute("PRAGMA synchronous=OFF")
    t0 = time.time()
    queue = [P(L) for L in L1]
    seen = set()
    nleaf = nnode = 0
    while queue:
        log("--- round: %d partitions queued (elapsed %.1f min, queries %d)"
            % (len(queue), (time.time() - t0) / 60, _counter["q"]))
        cm = count_many(queue)

        def _handle(p):
            k = pkey(p)
            if k in seen:
                return None
            n = cm.get(k)
            if n is None:
                return ("retry", p, None, None)
            if n == 0:
                return ("empty", p, 0, None)
            if n <= MAX_N:
                return ("leaf", p, n, None)
            kids = split(p, n)
            if len(kids) == 1:
                return ("unsplit", p, n, None)
            return ("node", p, n, [kk for kk, _ in kids])

        nxt = []
        todo = [p for p in queue if pkey(p) not in seen]
        with ThreadPoolExecutor(max_workers=8) as ex:
            for res in ex.map(_handle, todo):
                if res is None:
                    continue
                kind, p, n, kids = res
                k = pkey(p)
                if kind == "retry":
                    nxt.append(p)
                    seen.discard(k)
                    continue
                seen.add(k)
                if kind == "empty":
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, 0, "empty", time.time()))
                elif kind == "leaf":
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, n, "leaf", time.time()))
                    nleaf += 1
                elif kind == "unsplit":
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, n, "leaf", time.time()))
                    nleaf += 1
                    log("   !! UNSPLITTABLE (n=%s): %s" % (n, k))
                else:
                    nnode += 1
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, n, "node", time.time()))
                    nxt.extend(kids)
        conn.commit()
        queue = [p for p in nxt if pkey(p) not in seen]
        if len(seen) > 20000:
            log("   !! too many partitions, stopping discovery")
            break
    s = conn.execute("SELECT COUNT(*), SUM(num_found) FROM parts WHERE status='leaf'").fetchone()
    log("=== discover done: leaves=%d nodes=%d empty_saved, leaf_sum=%s, queries=%d, %.1f min ==="
        % (nleaf, nnode, s[1], _counter["q"], (time.time() - t0) / 60))
    conn.close()


def supplement(roots):
    """补抓 A-Z 之外的根分区(如数字开头的分类号)。

    原有的按大类根分区是 A-Z;官网还有极少量分类号以数字等开头
    (如 classNo='1' 有 11 条),这些不会被 A-Z 枚举覆盖,单独补一遍。
    已存在的叶子/空分区直接复用,不重抓。
    """
    init_db()
    conn = db()
    conn.execute("PRAGMA synchronous=OFF")
    done = set(r[0] for r in conn.execute(
        "SELECT pkey FROM parts WHERE status IN ('leaf','empty')"))
    t0 = time.time()
    queue = [P(r) for r in roots if pkey(P(r)) not in done]
    log("=== supplement: %d roots queued (%s) ===" % (len(queue), ",".join(roots)))
    seen = set()
    nleaf = 0
    while queue:
        cm = count_many(queue)

        def _handle(p):
            k = pkey(p)
            if k in seen:
                return None
            n = cm.get(k)
            if n is None:
                return ("retry", p, None, None)
            if n == 0:
                return ("empty", p, 0, None)
            if n <= MAX_N:
                return ("leaf", p, n, None)
            kids = split(p, n)
            if len(kids) == 1:
                return ("unsplit", p, n, None)
            return ("node", p, n, [kk for kk, _ in kids])

        nxt = []
        todo = [p for p in queue if pkey(p) not in seen]
        with ThreadPoolExecutor(max_workers=8) as ex:
            for res in ex.map(_handle, todo):
                if res is None:
                    continue
                kind, p, n, kids = res
                k = pkey(p)
                if kind == "retry":
                    nxt.append(p)
                    seen.discard(k)
                    continue
                seen.add(k)
                if kind in ("empty", "leaf", "unsplit"):
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, n, "empty" if kind == "empty" else "leaf",
                                  time.time()))
                    if kind != "empty":
                        nleaf += 1
                        log("   +leaf n=%s %s" % (n, k))
                    if kind == "unsplit":
                        log("   !! UNSPLITTABLE (n=%s): %s" % (n, k))
                else:
                    conn.execute("INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?)",
                                 (k, k, n, "node", time.time()))
                    nxt.extend(kids)
        conn.commit()
        queue = [p for p in nxt if pkey(p) not in seen]
    log("=== supplement done: +%d leaves, %.1f min ===" % (nleaf, (time.time() - t0) / 60))
    conn.close()


def harvest():
    init_db()
    conn = db()
    leaves = conn.execute(
        "SELECT pkey, params, num_found FROM parts WHERE status='leaf' "
        "AND num_found>0 ORDER BY num_found ASC").fetchall()
    done = dict(conn.execute("SELECT pkey, COUNT(*) FROM pages_done GROUP BY pkey").fetchall())
    log("=== harvest start: %d leaves, queries so far %d ===" % (len(leaves), _counter["q"]))
    jobs = []
    for pk, pj, nf in leaves:
        pages = min((nf + ROWS - 1) // ROWS, PAGE_CAP)
        start = done.get(pk, 0) + 1
        for pg in range(start, pages + 1):
            jobs.append((pk, pj, nf, pg))
    log("   page jobs to run:", len(jobs))
    t0 = time.time()
    ins = [0]
    lock = threading.Lock()

    def do(job):
        pk, pj, nf, pg = job
        p = json.loads(pj)
        d = search(page=pg, rows=ROWS, **params(p))
        if d is None:
            return 0
        items = d.get("searchResult") or []
        if not items:
            return 0
        rows = [rec_row(it, p["classNo"]) for it in items if it.get("recordId")]
        c = tdb()
        c.executemany(INSERT, rows)
        c.execute("INSERT OR REPLACE INTO pages_done VALUES(?,?,?,?)", (pk, pg, len(rows), time.time()))
        return len(rows)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(do, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                with lock:
                    ins[0] += f.result() or 0
            except Exception as e:                                # noqa: BLE001
                log("  page err", repr(e)[:150])
            if i % 250 == 0 or i == len(futs):
                el = time.time() - t0
                rate = i / el if el else 0
                log("   %d/%d pages | %d rows | %.1f pg/s | ETA %.0f min"
                    % (i, len(jobs), ins[0], rate, (len(jobs) - i) / rate / 60 if rate else -1))
    n = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    log("=== harvest done: books=%d (%.1f min) ===" % (n, (time.time() - t0) / 60))
    conn.close()


def enrich(limit=100000):
    """批量补齐纸本书的馆藏位置(索书号/馆藏地/条码/在架状态); 断点续跑"""
    init_db()
    conn = db()
    conn.execute("CREATE TABLE IF NOT EXISTS enrich_done("
                 "record_id INTEGER PRIMARY KEY, n_items INTEGER, ts REAL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enrich ON enrich_done(record_id)")
    todo = conn.execute(
        "SELECT b.record_id FROM books b WHERE b.p_count>0 "
        "AND NOT EXISTS(SELECT 1 FROM enrich_done d WHERE d.record_id=b.record_id) "
        "ORDER BY CAST(b.year AS INTEGER) DESC LIMIT ?", (limit,)).fetchall()
    log("=== enrich: %d records queued (workers=%d) ===" % (len(todo), WORKERS))
    t0 = time.time()
    ok = [0]
    none_ = [0]
    lock = threading.Lock()

    def one(rid):
        for a in range(3):
            try:
                r = sess().post(BASE + "/find/physical/groupitems",
                                json={"recordId": rid}, timeout=40)
                j = r.json()
                if j.get("success"):
                    return rid, (j.get("data") or [])
            except Exception:                                     # noqa: BLE001
                pass
            time.sleep(0.3 + a * 0.5)
        return rid, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(one, r[0]) for r in todo]
        for i, f in enumerate(as_completed(futs), 1):
            rid, items = f.result()
            c = tdb()
            if items:
                for it in items:
                    c.execute("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                        it.get("itemId"), rid, it.get("callNo"), it.get("barcode"),
                        str(it.get("locationId")), it.get("locationName"), it.get("realLocationName"),
                        it.get("processType"), it.get("shelfNo"), it.get("inDate"),
                        it.get("listPrice"), time.time()))
                c.execute("INSERT OR REPLACE INTO enrich_done VALUES(?,?,?)", (rid, len(items), time.time()))
                ok[0] += 1
            else:
                c.execute("INSERT OR REPLACE INTO enrich_done VALUES(?,?,?)", (rid, 0, time.time()))
                none_[0] += 1
            if i % 500 == 0:
                el = time.time() - t0
                log("   enrich %d/%d | %d 有条目 | %.1f rec/s | ETA %.0f min"
                    % (i, len(todo), ok[0], i / el if el else 0,
                       (len(todo) - i) / (i / el) / 60 if el else -1))
    log("=== enrich done: %d with items, %d empty (%.1f min) ==="
        % (ok[0], none_[0], (time.time() - t0) / 60))
    conn.close()


def save_locations():
    init_db()
    r = sess().post(BASE + "/find/groupResource/dict", json={}, timeout=40)
    locs = (r.json().get("data") or {}).get("locationId") or []
    c = db()
    for x in locs:
        c.execute("INSERT OR REPLACE INTO locations VALUES(?,?)", (str(x["code"]), x["name"]))
    log("locations saved:", len(locs))


def status():
    conn = db()
    q = lambda s: conn.execute(s).fetchone()[0]                    # noqa: E731
    try:
        log("books         :", q("SELECT COUNT(*) FROM books"))
        log("  paper p>0   :", q("SELECT COUNT(*) FROM books WHERE p_count>0"))
        log("  ebook only  :", q("SELECT COUNT(*) FROM books WHERE p_count=0 AND e_count>0"))
        log("  with abstract:", q("SELECT COUNT(*) FROM books WHERE abstract IS NOT NULL"))
        log("  with callNo :", q("SELECT COUNT(*) FROM books WHERE call_no IS NOT NULL"))
        log("  with classNo:", q("SELECT COUNT(*) FROM books WHERE class_no IS NOT NULL"))
        log("items         :", q("SELECT COUNT(*) FROM items"))
        log("pages_done    :", q("SELECT COUNT(*) FROM pages_done"))
        for st in ("leaf", "node", "empty"):
            log("parts %-6s  :" % st, q("SELECT COUNT(*) FROM parts WHERE status='%s'" % st))
        log("queries issued:", _counter["q"])
        log("db MB         : %.1f" % (DB_PATH.stat().st_size / 1e6 if DB_PATH.exists() else 0))
    except Exception as e:                                        # noqa: BLE001
        log("status err", e)
    conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"preflight": preflight, "discover": discover, "harvest": harvest,
     "all": lambda: (discover(), harvest(), save_locations()),
     "enrich": lambda: enrich(int(sys.argv[2]) if len(sys.argv) > 2 else 500),
     "supplement": lambda: supplement(
         (sys.argv[2] if len(sys.argv) > 2 else "0123456789").split(",")),
     "status": status, "locations": save_locations}.get(cmd, status)()
