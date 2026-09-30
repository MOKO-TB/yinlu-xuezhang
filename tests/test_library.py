"""library_search 工具:本地馆藏库检索的单元测试(用临时库,不依赖线上数据)。"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import minicode.paths as paths_mod
from minicode import library
from minicode.tools.base import ToolContext


def _make_db(tmp_path: Path, with_items: bool = True) -> Path:
    d = tmp_path / "crawled" / library.DOMAIN
    d.mkdir(parents=True)
    p = d / library.DB_NAME
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE books(
      record_id INTEGER PRIMARY KEY, title TEXT, author TEXT, publisher TEXT, isbn TEXT,
      year TEXT, pub_place TEXT, lang TEXT, class_no TEXT, call_no TEXT, abstract TEXT,
      subject TEXT, chi_subject TEXT, p_count INTEGER, onshelf INTEGER, e_count INTEGER,
      d_count INTEGER, doc_code INTEGER, doc_name TEXT, price TEXT, book_page TEXT,
      ecol TEXT, pic TEXT, part TEXT);
    CREATE TABLE items(
      item_id TEXT PRIMARY KEY, record_id INTEGER, call_no TEXT, barcode TEXT,
      location_id TEXT, location_name TEXT, real_location TEXT, process_type TEXT,
      shelf_no TEXT, in_date TEXT, price TEXT, fetched_at REAL);
    CREATE VIRTUAL TABLE books_fts USING fts5(
      title, author, publisher, abstract, content='books', content_rowid='record_id',
      tokenize='trigram');
    """)
    books = [
        (1, "活着", "余华著", "北京十月文艺出版社", "978-7-5302-2153-2", "2021", "北京",
         "chi", "I247.5", "I247.5/2531", "《活着》讲述了福贵一生的故事。", "长篇小说",
         "文学-新体长篇、中篇小说", 6, 4, 0, 0, 1, "图书", "45.00", "191页", None, None, None),
        (2, "活着", "老树画画著", "上海书画出版社", "978-7-5479-2601-7", "2021", "上海",
         "chi", "I287.8", "I287.8/1", "绘本。", None, None, 2, 2, 0, 0, 1, "图书",
         "58.00", "1册", None, None, None),
        (3, "许三观卖血记", "余华著", "作家出版社", "978-7-5063-8988-1", "2012", "北京",
         "chi", "I247.5", "I247.5/862", "许三观靠卖血渡过难关。", None, None,
         3, 3, 0, 0, 1, "图书", "28.00", "259页", None, None, None),
        (4, "三体", "刘慈欣著", "重庆出版社", "978-7-5366-9293-0", "2008", "重庆",
         "chi", "I247.5", "I247.5/868", "地球文明向宇宙发出信息。", None, None,
         0, 0, 1, 0, 1, "图书", "23.00", "302页", None, None, None),
        (5, "人工智能导论", "王万森编著", "高等教育出版社", "978-7-04-055234-1", "2021",
         "北京", "chi", "TP18", "TP18/123", "人工智能基础教材。", None, None,
         4, 2, 0, 0, 1, "图书", "39.00", "300页", None, None, None),
    ]
    c.executemany("INSERT INTO books(record_id,title,author,publisher,isbn,year,pub_place,"
                  "lang,class_no,call_no,abstract,subject,chi_subject,p_count,onshelf,"
                  "e_count,d_count,doc_code,doc_name,price,book_page,ecol,pic,part) "
                  "VALUES(" + ",".join("?" * 24) + ")", books)
    c.execute("INSERT INTO books_fts(rowid,title,author,publisher,abstract) "
              "SELECT record_id,title,author,publisher,abstract FROM books")
    if with_items:
        c.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("i1", 1, "I247.5/2531", "G0000001", "16", "301 流通书库", "301 流通书库",
             "在架", None, None, None, 0),
            ("i2", 1, "I247.5/2531", "G0000002", "16", "301 流通书库", "301 流通书库",
             "在架", None, None, None, 0),
            ("i3", 5, "TP18/123", "G0000003", "11", "201 文学新书区", "201 文学新书区",
             "在架", None, None, None, 0),
        ])
    c.commit()
    c.close()
    return p


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, config=None)


def _isolate(monkeypatch, tmp_path: Path) -> None:
    """把 app/resource 回退路径也指到临时目录:否则开发态项目根下真实存在
    crawled/.../library.db 时,「库不存在」的用例会误命中真实数据。"""
    empty = tmp_path / "no-app-data"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(library, "app_path", lambda *a: empty.joinpath(*a))
    monkeypatch.setattr(library, "resource_path", lambda *a: empty.joinpath(*a))


def test_db_path_found_under_workspace(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    assert library.db_path(tmp_path) == \
        tmp_path / "crawled" / library.DOMAIN / library.DB_NAME


def test_db_path_none_when_missing(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert library.db_path(tmp_path) is None


def test_exact_title_match_first(tmp_path):
    _make_db(tmp_path)
    hits = library.search(tmp_path, "活着")
    assert hits and hits[0]["title"] == "活着"
    assert hits[0]["call_no"]
    assert len(hits) >= 2


def test_author_search(tmp_path):
    _make_db(tmp_path)
    hits = library.search(tmp_path, "余华")
    titles = {h["title"] for h in hits}
    assert "活着" in titles and "许三观卖血记" in titles


def test_fts_three_char(tmp_path):
    _make_db(tmp_path)
    hits = library.search(tmp_path, "人工智能")
    assert hits and hits[0]["title"] == "人工智能导论"


def test_location_attached(tmp_path):
    _make_db(tmp_path)
    hits = library.search(tmp_path, "活着")
    assert hits[0]["locs"] == "301 流通书库"


def test_kind_filter(tmp_path):
    _make_db(tmp_path)
    assert library.search(tmp_path, "三体", kind="paper") == []
    assert library.search(tmp_path, "三体", kind="ebook")


def test_handler_output_has_loc_and_source(tmp_path):
    _make_db(tmp_path)
    out = library.library_search(_ctx(tmp_path), "活着")
    assert "活着" in out and "索书号" in out and "馆藏地" in out
    assert "OPAC" in out


def test_handler_no_hit_message(tmp_path):
    _make_db(tmp_path)
    out = library.library_search(_ctx(tmp_path), "不存在的书名XYZ")
    assert "没有匹配" in out


def test_handler_missing_db(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    out = library.library_search(_ctx(tmp_path), "活着")
    assert "不存在" in out


def test_handler_empty_query(tmp_path):
    _make_db(tmp_path)
    assert "不能为空" in library.library_search(_ctx(tmp_path), "   ")


def test_prompt_section_silent_without_db(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert library.library_prompt_section(tmp_path) == ""


def test_prompt_section_warns_when_db_unreadable(tmp_path, monkeypatch):
    """库在但读不出来:要在系统提示词里明确警告,别让模型说“没这本书”。"""
    _isolate(monkeypatch, tmp_path)
    _broken_db(tmp_path, b"SQLite format 3\x00" + b"\x00" * 4096)
    sec = library.library_prompt_section(tmp_path)
    assert "异常" in sec
    assert "没加载起来" in sec


def test_prompt_section_reports_counts(tmp_path):
    _make_db(tmp_path)
    sec = library.library_prompt_section(tmp_path)
    assert "library_search" in sec and "5" in sec


def test_like_escape(tmp_path):
    """% 与 _ 不该被当成通配符。"""
    _make_db(tmp_path)
    assert library.search(tmp_path, "%") == []


def test_multiword_falls_back_to_first_token(tmp_path):
    """多词 AND 查不到时按词序退回单关键词:别把"组合词不存在"当成"库里没有"。"""
    _make_db(tmp_path)
    assert library.search(tmp_path, "死神尽头 查无此词") == []   # 每个词都不存在
    hits = library.search(tmp_path, "活着 死神尽头")            # AND 查不到 → 退回"活着"
    assert hits and hits[0]["title"] == "活着"
    hits = library.search(tmp_path, "死神尽头 活着")            # 首词不存在 → 退回第二个词
    assert hits and hits[0]["title"] == "活着"


# ------------------------------------------------- 查询归一化(B01 类误报修复)
# 真实用户输入常带书名号《》与版次标注“（第N版）”,而库里标题未必有:
# 整串当字面量查必然 0 命中,于是被报成"图书馆没有这本书"。


def _add_books(tmp_path: Path, rows: list[tuple]) -> None:
    """往已建好的临时库追加书目(重建 FTS,外部内容表要显式 rebuild)。"""
    p = tmp_path / "crawled" / library.DOMAIN / library.DB_NAME
    c = sqlite3.connect(p)
    c.executemany(
        "INSERT INTO books(record_id,title,author,publisher,isbn,year,pub_place,"
        "lang,class_no,call_no,abstract,subject,chi_subject,p_count,onshelf,"
        "e_count,d_count,doc_code,doc_name,price,book_page,ecol,pic,part) "
        "VALUES(" + ",".join("?" * 24) + ")", rows)
    c.execute("INSERT INTO books_fts(books_fts) VALUES('rebuild')")
    c.commit()
    c.close()


def _book(rid: int, title: str, author: str = "某作者", cls: str = "O13",
          pc: int = 2) -> tuple:
    return (rid, title, author, "某出版社", None, "2020", "北京", "chi",
            cls, f"{cls}/{rid}", "简介。", None, None, pc, pc,
            0, 0, 1, "图书", "39.00", "300页", None, None, None)


def test_variants_ladder_order(tmp_path):
    """阶梯从严到宽:原串 → 去书名号/版次标注 → 拆词。"""
    assert library._variants("《高等数学》")[0] == "高等数学"   # 整串包住→去壳优先
    vs = library._variants("C语言程序设计（第5版）")
    assert "C语言程序设计" in vs
    assert vs.index("C语言程序设计（第5版）") < vs.index("C语言程序设计")
    vs = library._variants("三体：地球往事")
    assert "三体" in vs and "地球往事" in vs


def test_book_title_marks_are_stripped(tmp_path):
    """《书名》应能查到书名本身(库里标题不带书名号)。"""
    _make_db(tmp_path)
    assert library.search(tmp_path, "《人工智能导论》")          # 修复前:0 命中
    hits = library.search(tmp_path, "《人工智能导论》")
    assert hits[0]["title"] == "人工智能导论"


def test_wrapped_query_prefers_the_book_itself(tmp_path):
    """《活着》要返回《活着》本身,而不是恰好含“《活着》”的其它书。"""
    _make_db(tmp_path)
    _add_books(tmp_path, [_book(101, "《活着》解读", cls="I247.5")])
    hits = library.search(tmp_path, "《活着》")
    assert hits and hits[0]["title"] == "活着"


def test_edition_annotation_ignored(tmp_path):
    """“（第N版）”这类标注库里未必有,应去掉后再查。"""
    _make_db(tmp_path)
    _add_books(tmp_path, [_book(102, "高等数学. 上册. 第2版")])
    for q in ("高等数学（第2版）", "高等数学(第2版)", "高等数学 第2版"):
        hits = library.search(tmp_path, q)
        assert hits, f"{q} 应该能查到"            # 修复前:（第2版）形态 0 命中
        assert hits[0]["title"].startswith("高等数学")


def test_volume_annotation_ignored(tmp_path):
    """“（上）”“（上、下册）”等卷册括注同样应能回退到书名主体。"""
    _make_db(tmp_path)
    _add_books(tmp_path, [_book(103, "线性代数  第6版")])
    hits = library.search(tmp_path, "线性代数（上）")
    assert hits and hits[0]["title"].startswith("线性代数")


def test_subtitle_colon_falls_back_to_core(tmp_path):
    """“书名：副标题”副标题写法对不上时,退回书名主体。"""
    _make_db(tmp_path)
    hits = library.search(tmp_path, "人工智能导论：从理论到实践")
    assert hits and hits[0]["title"] == "人工智能导论"


def test_hit_message_does_not_claim_book_absent(tmp_path):
    """真查不到时的提示语不能说成“图书馆没有这本书”,并要引导换关键词再试。"""
    _make_db(tmp_path)
    out = library.library_search(_ctx(tmp_path), "不存在的书名XYZ")
    assert "没有匹配" in out
    assert "不等于馆里没有这本书" in out
    assert "不要直接说成" in out


def _isolate_guide(monkeypatch, tmp_path):
    """把导览数据包的回退路径也指到空目录,免得开发态真实数据包混进断言。"""
    from minicode import library_guide
    empty = tmp_path / "no-app-data"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(library_guide, "app_path", lambda *a: empty.joinpath(*a))
    monkeypatch.setattr(library_guide, "resource_path", lambda *a: empty.joinpath(*a))


def test_floor_hint_appended_when_pack_present(tmp_path, monkeypatch):
    """有导览数据包时,检索结果直接带上楼层,省掉第二次工具调用。"""
    _isolate_guide(monkeypatch, tmp_path)
    _make_db(tmp_path)
    d = tmp_path / "skills" / "library-guide" / "data"
    d.mkdir(parents=True)
    (d / "library-facts.json").write_text(
        '{"class_floor_map":[{"code":"I","name":"文学",'
        '"floors":[["二楼",9394]],'
        '"locations":[{"name":"广应科图书馆-201 文学新书区","items":9378}]}]}',
        encoding="utf-8")
    out = library.library_search(_ctx(tmp_path), "活着")
    assert "楼层提示" in out and "二楼" in out and "201 文学新书区" in out


def test_floor_hint_absent_without_pack(tmp_path, monkeypatch):
    _isolate_guide(monkeypatch, tmp_path)
    _make_db(tmp_path)
    assert "楼层提示" not in library.library_search(_ctx(tmp_path), "活着")


# ---------------------------------------- 库坏了 / 库是空的 ≠ 馆里没这本书
# 用户报障「图书馆查询显示没有这一本书」里还有一路:库文件在(所以不会报「库
# 不存在」),但读不出来——安装/拷贝只拿到空壳、文件被截断、或被别的进程占用。
# 这种情况以前被 `except sqlite3.Error: return []` 吞成「没有命中」,于是模型回
# 用户「按这个书名没检索到」。下面锁住新行为:显式报「读不出来」,不说「没有」。


def _broken_db(tmp_path: Path, payload: bytes) -> Path:
    p = tmp_path / "crawled" / library.DOMAIN / library.DB_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def test_zero_byte_db_is_not_reported_as_absent(tmp_path, monkeypatch):
    """0 字节空壳库:db_path 找得到,sqlite 当空库打开 → 查表就报错。"""
    _isolate(monkeypatch, tmp_path)
    _broken_db(tmp_path, b"")
    out = library.library_search(_ctx(tmp_path), "三体")
    assert "没有匹配" not in out
    assert "读不出来" in out or "空的" in out


def test_truncated_db_is_not_reported_as_absent(tmp_path, monkeypatch):
    """截断的库文件:同样不能说成「馆里没有这本书」。"""
    _isolate(monkeypatch, tmp_path)
    _broken_db(tmp_path, b"SQLite format 3\x00" + b"\x00" * 4096)
    out = library.library_search(_ctx(tmp_path), "三体")
    assert "没有匹配" not in out
    assert "读不出来" in out
    assert "不是“馆里没有这本书”" in out


def test_search_raises_catalog_unavailable(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _broken_db(tmp_path, b"SQLite format 3\x00" + b"\x00" * 4096)
    with pytest.raises(library.CatalogUnavailable):
        library.search(tmp_path, "三体")


def test_empty_but_valid_db_is_not_reported_as_absent(tmp_path, monkeypatch):
    """表建好了但 0 条书目:库没装好,不是「馆里没有」。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    p = tmp_path / "crawled" / library.DOMAIN / library.DB_NAME
    c = sqlite3.connect(p)
    c.execute("DELETE FROM books")
    c.execute("DELETE FROM items")
    c.commit()
    c.close()
    assert library.catalog_total(tmp_path) == 0
    out = library.library_search(_ctx(tmp_path), "三体")
    assert "没有匹配" not in out
    assert "空的" in out


def test_miss_message_shows_catalog_total(tmp_path):
    """真没命中时也要报「库内共 N 条」:库没装好一眼就能看出来。"""
    _make_db(tmp_path)
    out = library.library_search(_ctx(tmp_path), "不存在的书名XYZ")
    assert "没有匹配" in out
    assert "条书目" in out and "库内共 5 条书目" in out


# ------------------------- 完整性凭证 + 启动自检(分享给同学后“搜不到书”的头号原因)

def test_stamp_roundtrip(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    db = library.db_path(tmp_path)
    sp = library.write_stamp(db, 5, extra={"app_version": "0.9.02"})
    assert sp.is_file() and sp.name == "library.db.stamp.json"
    st = library.read_stamp(db)
    assert st["size"] == db.stat().st_size and st["count"] == 5
    assert len(st["sha256"]) == 64 and st["app_version"] == "0.9.02"


def test_read_stamp_tolerates_missing_and_broken(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    db = library.db_path(tmp_path)
    assert library.read_stamp(db) == {}                  # 没凭证(开发态/旧包)
    library.stamp_path_for(db).write_text("not json", encoding="utf-8")
    assert library.read_stamp(db) == {}                  # 凭证坏了也不能抛


def test_health_ok(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    db = library.db_path(tmp_path)
    library.write_stamp(db, 5)
    h = library.catalog_health(tmp_path)
    assert h["status"] == "ok" and h["total"] == 5
    assert library.health_notice(h) is None              # 正常时不打扰用户


def test_health_missing(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    h = library.catalog_health(tmp_path)
    assert h["status"] == "missing" and h["path"] is None
    assert "未安装" in library.health_notice(h)


def test_health_incomplete_when_size_differs(tmp_path, monkeypatch):
    """核心场景:库只拷了一半 → 必须在启动时就报「不完整」,不能等搜书失败。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    db = library.db_path(tmp_path)
    library.write_stamp(db, 5)
    with open(db, "r+b") as f:                            # 截掉尾巴模拟没拷全
        f.truncate(db.stat().st_size - 4096)
    h = library.catalog_health(tmp_path)
    assert h["status"] == "incomplete"
    assert h["size"] != h["expected"]
    msg = library.health_notice(h)
    assert "不完整" in msg and "重新" in msg


def test_health_unreadable(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _broken_db(tmp_path, b"SQLite format 3\x00" + b"\x00" * 4096)
    h = library.catalog_health(tmp_path)
    assert h["status"] == "unreadable" and "file is not a database" in h["detail"]
    assert "读不出来" in library.health_notice(h)


def test_health_empty(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    c = sqlite3.connect(library.db_path(tmp_path))
    c.execute("DELETE FROM books")
    c.commit()
    c.close()
    h = library.catalog_health(tmp_path)
    assert h["status"] == "empty"
    assert "空的" in library.health_notice(h)


def test_health_incomplete_when_count_differs(tmp_path, monkeypatch):
    """大小对得上但条数对不上(比如被换成了别的库)→ 也算不完整。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    db = library.db_path(tmp_path)
    library.write_stamp(db, 999)
    h = library.catalog_health(tmp_path)
    assert h["status"] == "incomplete" and h["count"] == 999 and h["total"] == 5


def test_health_ignores_stampless_db(tmp_path, monkeypatch):
    """没有凭证(开发态、旧包)时只做开库校验,不能误报。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    assert library.catalog_health(tmp_path)["status"] == "ok"


# ---------------------------------------------------------------- 快照口径与搜书账本(v0.9.43)

def test_snapshot_note_empty_without_stamp(tmp_path, monkeypatch):
    """没有构建凭证就不说日期 —— 不拿库文件 mtime 冒充抓取时间。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    assert library.snapshot_date(tmp_path) == ""
    assert library.snapshot_note(tmp_path) == ""


def test_snapshot_note_carries_built_at(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    db = _make_db(tmp_path)
    library.write_stamp(db, count=5)
    day = library.snapshot_date(tmp_path)
    assert day and len(day) == 10
    note = library.snapshot_note(tmp_path)
    assert day in note and "快照" in note


def test_search_result_mentions_snapshot(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    db = _make_db(tmp_path)
    library.write_stamp(db, count=5)
    out = library.library_search(_ctx(tmp_path), "活着")
    assert "抓取于" in out and "快照" in out


def test_search_miss_logs_library_gap(tmp_path, monkeypatch):
    """搜书落空(库正常)要进成长账本 reason=library;库坏/库空不算缺口不记。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    from minicode import growth
    monkeypatch.setattr(paths_mod, "APP_DIR", tmp_path / "app")
    growth._SEEN_GAPS.clear()
    ctx = _ctx(tmp_path)
    out = library.library_search(ctx, "不存在的书名XYZ")
    assert "没有匹配" in out
    gap_file = (tmp_path / "app" / growth.GAPS_FILE)
    rows = [json.loads(line) for line in gap_file.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert len(rows) == 1 and rows[0]["reason"] == "library"
    assert rows[0]["query"] == "不存在的书名XYZ"


def test_search_miss_not_logged_when_db_unreadable(tmp_path, monkeypatch):
    """库读不出来(CatalogUnavailable)是环境问题,不记缺口、不替图书馆下结论。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    from minicode import growth
    monkeypatch.setattr(paths_mod, "APP_DIR", tmp_path / "app")
    growth._SEEN_GAPS.clear()
    # 把库写成半个字节:catalog_health 会判 unreadable/empty,搜书报 CatalogUnavailable
    db = library.db_path(tmp_path)
    real = db.read_bytes()
    db.write_bytes(real[:200])
    out = library.library_search(_ctx(tmp_path), "活着")
    assert "检索数据" in out
    assert not (tmp_path / "app" / growth.GAPS_FILE).exists()


# ------------------------------------------------ 跨线程共用连接(v0.9.94 的并行子代理实测撞过)


def _stats_of(tmp_path):
    """这份夹具库的真实统计(数字全部来自 _make_db 里那 5 本书,不是估的)。"""
    return {"total": 5, "paper": 4, "ebook": 1, "classes": 3}


def test_read_holds_the_lock_across_execute_and_fetch():
    """`_read` 的规矩是「execute 与 fetch 之间不松锁」,这条要能量出来。

    手法:第二条线程等 `_read` 进到 execute 之后来抢锁 —— **在 fetch 还没做完之前**
    就抢到,说明锁松了(那才是出事的样子)。只在 conn() 里上锁防不住:两条线程各自
    execute 完再各自 fetch,拿回来的是别人那条语句的结果(v0.9.94 的并行子代理实测
    过 fetchone() 直接给 None,而上面一层只捕 sqlite3.Error,于是整路"子代理执行出错")。
    """
    entered = threading.Event()
    state = {"fetch_done": False, "抢到了没取结果": False}

    def other():
        entered.wait(5)                       # 等 _read 已经拿着锁进了 execute
        got = library._LIB_LOCK.acquire(timeout=5)
        if got and not state["fetch_done"]:
            state["抢到了没取结果"] = True
        if got:
            library._LIB_LOCK.release()

    class Cur:
        def fetchone(self):
            time.sleep(0.3)                   # "执行完了、结果还没取"的那段窗口
            state["fetch_done"] = True
            return (7,)

    class Conn:
        def execute(self, sql, params=()):
            entered.set()
            return Cur()

    th = threading.Thread(target=other)
    th.start()
    try:
        assert library._read(Conn(), "SELECT 1", (), one=True) == (7,)
    finally:
        th.join(timeout=10)

    assert state["fetch_done"] is True, "探针没跑到 fetch,这条测了个空"
    assert state["抢到了没取结果"] is False, "_read 在 execute 与 fetch 之间松了锁"
    assert library._LIB_LOCK.acquire(blocking=False) is True   # 用完必须放回去
    library._LIB_LOCK.release()


def test_catalog_stats_none_row_degrades_to_no_statistics(tmp_path, monkeypatch):
    """一条 COUNT 没有结果行 = 这条连接被人动过:按「读不出来」走,不报猜出来的数。"""
    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    monkeypatch.setattr(library, "_read", lambda c, sql, params=(), one=False: None)
    assert library.catalog_stats(tmp_path) is None


def test_parallel_catalog_reads_all_get_their_own_result(tmp_path, monkeypatch):
    """八条线程一起读馆藏(就是并行子代理各建一次系统提示词那个形状)。

    修之前的实测症状:某一路 `TypeError: cannot unpack non-iterable NoneType object`,
    而 run_subagent 只捕 sqlite3.Error,于是那一路整条报"子代理执行出错"。
    """
    import threading

    _isolate(monkeypatch, tmp_path)
    _make_db(tmp_path)
    want = _stats_of(tmp_path)
    errors: list[str] = []
    start = threading.Barrier(8)

    def worker(i: int):
        start.wait()                      # 尽量让八条线程真撞在一起
        try:
            for _ in range(60):
                st = library.catalog_stats(tmp_path)
                if st != want:
                    errors.append(f"第{i}路统计不对:{st}")
                    return
                rows = library.search(tmp_path, "活着" if i % 2 else "三")
                if not rows:
                    errors.append(f"第{i}路检索空手(库里明明有)")
                    return
        except Exception as e:            # noqa: BLE001 —— 这里要抓的就是"任何异常"
            errors.append(f"第{i}路抛了 {type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=120)
    assert not errors, errors
