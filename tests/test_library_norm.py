"""馆藏检索 v0.9.187:title_norm 归一化 + unicode61 逐字索引。

三组断言:
1. `norm_title` 的口径(只用于匹配、不改展示;哪些字符去掉、哪些保留);
2. **迁移库**上的检索:2 字走索引(证明 `_like_rows` 一次都没被调用)、书名号/
   全角/大小写/空格差异被归一化吃掉、未命中不扫表;
3. **旧库**(trigram、无 title_norm)照旧工作,且迁移脚本能把它迁到新结构、
   幂等、重写凭证。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from minicode import library  # noqa: E402


COLUMNS = ("record_id,title,author,publisher,isbn,year,pub_place,lang,class_no,"
           "call_no,abstract,subject,chi_subject,p_count,onshelf,e_count,d_count,"
           "doc_code,doc_name,price,book_page,ecol,pic,part")


def _books(rows):
    """(rid, title, author, publisher, p_count) → 24 列的行。"""
    out = []
    for rid, title, author, pub, pc in rows:
        out.append((rid, title, author, pub, None, "2021", "北京", "chi",
                    "I247.5", "I247.5/1", "摘要文本。", None, None, pc, pc, 0,
                    0, 1, "图书", "45.00", "191页", None, None, None))
    return out


def _make_old_db(tmp_path: Path, rows) -> Path:
    """旧结构:trigram FTS + 触发器,没有 title_norm。"""
    d = tmp_path / "crawled" / library.DOMAIN
    d.mkdir(parents=True)
    p = d / library.DB_NAME
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE books(record_id INTEGER PRIMARY KEY, title TEXT, author TEXT,
      publisher TEXT, isbn TEXT, year TEXT, pub_place TEXT, lang TEXT,
      class_no TEXT, call_no TEXT, abstract TEXT, subject TEXT, chi_subject TEXT,
      p_count INTEGER, onshelf INTEGER, e_count INTEGER, d_count INTEGER,
      doc_code INTEGER, doc_name TEXT, price TEXT, book_page TEXT, ecol TEXT,
      pic TEXT, part TEXT);
    CREATE TABLE items(item_id TEXT PRIMARY KEY, record_id INTEGER, call_no TEXT,
      barcode TEXT, location_id TEXT, location_name TEXT, real_location TEXT,
      process_type TEXT, shelf_no TEXT, in_date TEXT, price TEXT, fetched_at REAL);
    CREATE VIRTUAL TABLE books_fts USING fts5(
      title, author, publisher, abstract, content='books',
      content_rowid='record_id', tokenize='trigram');
    CREATE TRIGGER books_ai AFTER INSERT ON books BEGIN
      INSERT INTO books_fts(rowid,title,author,publisher,abstract)
      VALUES (new.record_id,new.title,new.author,new.publisher,new.abstract);
    END;
    """)
    c.executemany(f"INSERT INTO books({COLUMNS}) VALUES({','.join('?'*24)})", _books(rows))
    c.commit()
    c.close()
    return p


@pytest.fixture
def sample_rows():
    return [
        (1, "高等数学(上)", "同济大学数学系编", "高等教育出版社", 6),
        (2, "高等数学(下)", "同济大学数学系编", "高等教育出版社", 5),
        (3, "三体", "刘慈欣著", "重庆出版社", 3),
        (4, "C程序设计", "谭浩强编著", "清华大学出版社", 4),
        (5, "活着", "余华著", "作家出版社", 2),
        (6, "许三观卖血记", "余华著", "作家出版社", 1),
    ]


def _isolate(monkeypatch, tmp_path: Path) -> None:
    """把 app/resource 回退路径指到空目录:否则开发态项目根下真实的
    crawled/.../library.db 会被「库不存在」类用例误命中(与 test_library 同一做法)。"""
    empty = tmp_path / "no-app-data"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(library, "app_path", lambda *a: empty.joinpath(*a))
    monkeypatch.setattr(library, "resource_path", lambda *a: empty.joinpath(*a))


def _migrated(tmp_path, rows, monkeypatch):
    from migrate_library_fts import migrate

    db = _make_old_db(tmp_path, rows)
    r = migrate(db, vacuum=False)
    assert not r.get("skipped")
    _isolate(monkeypatch, tmp_path)
    return db


# ---------------------------------------------------------------- 归一化口径

def test_norm_title_strips_decoration_but_keeps_title_content():
    assert library.norm_title("《三体》") == "三体"
    assert library.norm_title("Ｃ程序设计") == "c程序设计", "全角转半角 + 大小写折叠"
    assert library.norm_title("高等 数学") == "高等数学", "空白去掉"
    assert library.norm_title("Python-从入门到实践") == "python从入门到实践", "连字符去掉"
    assert library.norm_title("高等数学（第5版）") == "高等数学(第5版)", "括号保留且半角化"
    assert library.norm_title("C++ Primer") == "c++primer", "加号是书名的一部分"
    assert library.norm_title("") == ""


# ---------------------------------------------------------------- 迁移库检索

def test_two_char_query_is_index_served_without_any_like_scan(tmp_path, monkeypatch, sample_rows):
    """2 字查询在迁移库上必须**一次 LIKE 都不走** —— 那是 3.4 s 的全表扫。"""
    _migrated(tmp_path, sample_rows, monkeypatch)
    called = []
    monkeypatch.setattr(library, "_like_rows",
                        lambda *a, **k: called.append(1) or [])
    hits = library.search(tmp_path, "数学", top_k=10)
    assert {h["title"] for h in hits} == {"高等数学(上)", "高等数学(下)"}
    assert called == [], "迁移库的 2 字查询落到了 LIKE 全表扫"


def test_decoration_and_width_variants_are_normalized_away(tmp_path, monkeypatch, sample_rows):
    _migrated(tmp_path, sample_rows, monkeypatch)
    assert library.search(tmp_path, "《三体》", top_k=5)[0]["title"] == "三体"
    assert library.search(tmp_path, "Ｃ程序设计", top_k=5)[0]["title"] == "C程序设计"
    assert library.search(tmp_path, "高等 数学", top_k=5)[0]["title"].startswith("高等数学")


def test_two_char_author_query_finds_all_their_books(tmp_path, monkeypatch, sample_rows):
    _migrated(tmp_path, sample_rows, monkeypatch)
    hits = library.search(tmp_path, "余华", top_k=10)
    assert {h["title"] for h in hits} == {"活着", "许三观卖血记"}


def test_a_miss_does_not_fall_back_to_a_full_table_scan(tmp_path, monkeypatch, sample_rows):
    _migrated(tmp_path, sample_rows, monkeypatch)
    called = []
    monkeypatch.setattr(library, "_like_rows", lambda *a, **k: called.append(1) or [])
    assert library.search(tmp_path, "物理学概论不存在", top_k=5) == []
    assert called == []
    assert library.search(tmp_path, "量子", top_k=5) == [] and called == []


# ---------------------------------------------------------------- 旧库兼容

def test_an_old_trigram_db_still_works_through_the_old_ladder(tmp_path, monkeypatch, sample_rows):
    """没迁移过的库照旧:trigram 检索 + 前缀/LIKE 兜底,不因代码升级而判死。"""
    _make_old_db(tmp_path, sample_rows)
    _isolate(monkeypatch, tmp_path)
    assert library._fts_flavor(library.conn(tmp_path / "crawled" / library.DOMAIN / library.DB_NAME)) == "trigram"
    hits = library.search(tmp_path, "三体", top_k=5)
    assert hits and hits[0]["title"] == "三体"
    # 旧库的 2 字查询仍走 LIKE:结果必须对(慢是旧库的已知代价)
    assert {h["title"] for h in library.search(tmp_path, "数学", top_k=10)} == {
        "高等数学(上)", "高等数学(下)"}


def test_migration_is_idempotent_and_rewrites_the_stamp(tmp_path, sample_rows):
    from migrate_library_fts import migrate

    db = _make_old_db(tmp_path, sample_rows)
    first = migrate(db, vacuum=False)
    assert not first.get("skipped")
    stamp = library.stamp_path_for(db)
    assert stamp.is_file(), "迁移改变了库文件,凭证必须重写(否则下次更新重下整库)"
    data = json.loads(stamp.read_text(encoding="utf-8"))
    assert data["size"] == db.stat().st_size
    assert data["count"] == len(sample_rows)
    assert json.loads(json.dumps(data)).get("app_version")

    again = migrate(db, vacuum=False)
    assert again.get("skipped") is True

    c = sqlite3.connect(db)
    cols = [r[1] for r in c.execute("PRAGMA table_info(books)")]
    assert "title_norm" in cols
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name='books_fts'").fetchone()[0]
    assert "unicode61" in sql.lower()
    assert c.execute("SELECT COUNT(*) FROM books_fts").fetchone()[0] == len(sample_rows)
    trig = c.execute("SELECT name FROM sqlite_master WHERE name='books_ai'").fetchone()
    assert trig is None, "旧触发器必须摘掉(它按旧列写,迁移后会报错)"
    c.close()
