"""library_search 工具:本地馆藏库检索的单元测试(用临时库,不依赖线上数据)。"""
import sqlite3
from pathlib import Path

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
