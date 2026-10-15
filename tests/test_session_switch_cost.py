"""切一次会话到底花多少时间 —— 以及"看得见、点不开"那条老 bug。

缘起(用户实测,2026-10-03):"多任务切换时总是要停顿好几秒"。本机量出来的分配是
`build_runner_agent` **1699 ms**,而其中 **1640 ms 是 `library.catalog_stats` 那一条
`COUNT(*)`**(1.31 GB / 133 万条的只读库),而它**每切一次会话、每开一个新任务都要付一遍**。
加进程内缓存之后同一件事实测 **31.8 ms**。

这条文件钉的就是"不许退回去了":
  · 同一份库(路径/mtime/大小都没变)只问一次 SQL;
  · **那四条统计查询各走一条覆盖索引**(并回一条大查询返回值不变,只有计划能抓到);
  · 库被换掉必须重新问(缓存不能把旧数字留在屏上);
  · 缩略图同理 —— 图片没换就不重压,换了就要重压;
  · 侧栏列几条,解析就必须看几条(以前列 50 找 10,排第 11 之后的会话点不开)。
"""
import json
import os
import re
import sqlite3
import time

import pytest

from minicode import attachments, library
from minicode.gui_bridge import SESSION_LIST_LIMIT, create_app
from minicode.session import list_sessions


@pytest.fixture(autouse=True)
def _clean_caches():
    library._STATS_CACHE.clear()
    attachments._PREVIEW_CACHE.clear()
    yield
    library._STATS_CACHE.clear()
    attachments._PREVIEW_CACHE.clear()


def _make_db(tmp_path):
    d = tmp_path / "crawled" / library.DOMAIN
    d.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(d / library.DB_NAME)
    c.executescript(
        "CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, author TEXT, "
        "publisher TEXT, class_no TEXT, p_count INTEGER);"
        "INSERT INTO books(title,author,publisher,class_no,p_count) VALUES"
        "('人工智能导论','甲','社A','TP1',3),('活着','乙','社B','I2',1),"
        "('许三观卖血记','丙','社B','I2',0),('三体','丁','社C','I2',2),"
        "('活着','乙','社B','I2',1);")
    c.commit()
    c.close()
    return d / library.DB_NAME


# ---------------------------------------------------------------- 馆藏统计缓存

def test_catalog_stats_asks_the_database_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "app_path", lambda *a: tmp_path / "none" / "x")
    monkeypatch.setattr(library, "resource_path", lambda *a: tmp_path / "none" / "x")
    _make_db(tmp_path)
    calls = []
    real_conn = library.conn

    def spy(path):
        calls.append(path)
        return real_conn(path)

    monkeypatch.setattr(library, "conn", spy)
    first = library.catalog_stats(tmp_path)
    second = library.catalog_stats(tmp_path)
    assert len(calls) == 1, f"同一份库被问了 {len(calls)} 次,那条 COUNT 要 1.6 秒"
    assert first == second == {"total": 5, "paper": 4, "ebook": 1, "classes": 2}


def test_a_swapped_library_is_recounted_not_served_from_cache(tmp_path, monkeypatch):
    """库换了(重新装包/换了工作区)必须重算 —— 屏上那句"共 N 条"不能是上一个人的数。"""
    monkeypatch.setattr(library, "app_path", lambda *a: tmp_path / "none" / "x")
    monkeypatch.setattr(library, "resource_path", lambda *a: tmp_path / "none" / "x")
    p = _make_db(tmp_path)
    assert library.catalog_stats(tmp_path)["total"] == 5
    c = sqlite3.connect(p)
    c.execute("INSERT INTO books(title,author,publisher,class_no,p_count) "
              "VALUES('第四','戊','社D','TP9',1)")
    c.commit()
    c.close()
    os.utime(p, (time.time() + 5, time.time() + 5))     # 大小与 mtime 都会变
    assert library.catalog_stats(tmp_path)["total"] == 6


def test_cached_stats_are_handed_out_as_copies(tmp_path, monkeypatch):
    """消费方改一下返回值,不许把缓存本身改掉(那是给下一个会话的错误数字)。"""
    monkeypatch.setattr(library, "app_path", lambda *a: tmp_path / "none" / "x")
    monkeypatch.setattr(library, "resource_path", lambda *a: tmp_path / "none" / "x")
    _make_db(tmp_path)
    got = library.catalog_stats(tmp_path)
    got["total"] = 999999
    assert library.catalog_stats(tmp_path)["total"] == 5


# ---------------------------------------------------------------- 统计那条查询的**计划**

def test_stats_queries_never_scan_the_books_table(tmp_path, monkeypatch):
    """四条统计查询各走一条覆盖索引 —— 全表扫那张 1.31 GB 就是冷启动那几秒。

    这支针钉的是**查询计划**而不是返回值:把四条并回旧的那一条大查询
    (`SELECT COUNT(*), SUM(p_count>0), SUM(p_count=0), COUNT(DISTINCT class_no) FROM books`),
    返回的四个数**一个都不会变**,所以普通断言完全抓不到它 —— 而它会安静地把每次
    冷启动从约 0.2 秒带回 1.5 秒(冷盘实测 9.87 秒,§6 第 60 条)。于是这里直接问
    SQLite 要计划:每一条都必须出现 COVERING INDEX,且不许有不带索引的 `SCAN books`。
    """
    monkeypatch.setattr(library, "app_path", lambda *a: tmp_path / "none" / "x")
    monkeypatch.setattr(library, "resource_path", lambda *a: tmp_path / "none" / "x")
    d = tmp_path / "crawled" / library.DOMAIN
    d.mkdir(parents=True, exist_ok=True)
    db = d / library.DB_NAME
    c = sqlite3.connect(db)
    # 建的是**真库的那两个索引**(`idx_books_pcnt` / `idx_books_class`),不是凭空加的:
    # 少了它们,这四条一样会去扫表 —— 那正是"换个学校的库就退回秒级"的路径。
    c.executescript(
        "CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, class_no TEXT, "
        "p_count INTEGER);"
        "CREATE INDEX idx_books_pcnt ON books(p_count);"
        "CREATE INDEX idx_books_class ON books(class_no);"
        "INSERT INTO books(title,class_no,p_count) VALUES"
        "('甲','TP1',3),('乙','I2',0),('丙','I2',2),('丁','I2',1);")
    c.commit()
    c.close()
    st = library.catalog_stats(tmp_path)
    # 数也要对:计划漂亮但列写错(比如把 =0 写成 IS NULL)同样是坏数字。
    assert st == {"total": 4, "paper": 3, "ebook": 1, "classes": 2}
    probe = sqlite3.connect(db)
    try:
        assert len(library._STATS_QUERIES) == 4, "这四条就是统计的全部入口,加一条要写清为什么"
        for sql in library._STATS_QUERIES:
            text = " | ".join(str(r[3]) for r in probe.execute("EXPLAIN QUERY PLAN " + sql))
            assert "COVERING INDEX" in text, f"这条没走覆盖索引,会去扫 1.31 GB 的表:{sql}\n{text}"
            assert not re.search(r"SCAN books(?! USING)", text), \
                f"这条里出现了不带索引的全表扫描:{sql}\n{text}"
    finally:
        probe.close()


# ---------------------------------------------------------------- 缩略图缓存

def test_preview_is_recompressed_only_when_the_image_changes(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "p.png"
    Image.new("RGB", (40, 30), (10, 200, 90)).save(img)
    n = {"c": 0}
    real = attachments.compress_image

    def spy(path, **kw):
        n["c"] += 1
        return real(path, **kw)

    monkeypatch.setattr(attachments, "compress_image", spy)
    a = attachments.preview_data_url(img)
    b = attachments.preview_data_url(img)
    assert a and a == b and n["c"] == 1, "同一张图被重压了"
    time.sleep(0.01)
    img.write_bytes(img.read_bytes() + b"\x00")          # 内容变了
    attachments.preview_data_url(img)
    assert n["c"] == 2, "图片改了还不重压,屏上就是上一张图"


# -------------------------------------------------- 侧栏列几条,解析就得看几条

def _write_sessions(dir_, n):
    dir_.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(n):
        p = dir_ / f"session_2026010{i % 9}_000{i}.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"role": "user", "content": f"第{i}个问题"}) + "\n")
            f.write(json.dumps({"role": "assistant", "content": "答"}) + "\n")
        names.append(p.name)
        time.sleep(0.002)                                 # 让 mtime 有先后
    return names


def test_a_session_beyond_the_tenth_still_opens(tmp_path):
    """以前侧栏列 50 条、`_resolve_session_summary` 只扫 10 条:

    排在第 11~50 的会话**看得见、点不开**,点一下回"会话不存在"。
    """
    sess = tmp_path / "sessions"
    names = _write_sessions(sess, 14)
    assert len(list_sessions(sess)) == 10, "默认限 10 —— 这条前提要成立,否则测不到东西"
    assert len(list_sessions(sess, limit=SESSION_LIST_LIMIT)) == 14

    from minicode.config import Config
    from minicode.llm import AssistantTurn

    class _LLM:
        def stream_chat(self, *a, **k):
            return AssistantTurn(text="x")

    cfg = Config(providers=[], mcp_servers=[], sandbox={})
    bridge, _ui, _a, _s = create_app(cfg, tmp_path, llm=_LLM(), sessions_dir=sess)
    oldest = names[0]                                    # 第 1 条 = 最旧的一条(mtime 升序写)
    got = bridge.resume_session(oldest)
    assert got["ok"] is True, f"第 1 条会话点不开:{got}"
    got2 = bridge.resume_session(names[-1])
    assert got2["ok"] is True
