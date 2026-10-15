"""可交互产物(Gen UI,v0.9.177)那三道闸的针。

这里判的是**真路**:`minicode.panel.sanitize` 是真删,`Bridge.panel_preview` 是真读
真文件、真解析路径。界面那侧的 `<iframe sandbox>` 不在 pytest 能到的地方,由
`scripts/e2e_ui_panel.py` 在真浏览器里验(这一格的价值全在"执行不了",而 `capsys`
一类的进程内捕获看不见执行与否)。
"""
import pytest

from minicode import panel
from minicode.config import Config, Provider
from minicode.gui_bridge import create_app


# ---------------------------------------------------------------- 删可执行结构

def test_script_blocks_are_gone_and_counted():
    src = ("<div>合计</div><script>alert(1)</script>"
           "<SCRIPT SRC=\"//evil/x.js\"></SCRIPT>")
    body, counts = panel.sanitize(src)
    assert "alert" not in body and "evil" not in body, body
    assert counts["script"] == 2, counts
    assert "合计" in body, "删的是结构,不是正文"


def test_self_closing_script_is_removed():
    body, counts = panel.sanitize("<script/>正文")
    assert "<script" not in body and counts["script"] == 1


def test_event_attributes_removed_including_svg_vectors():
    """SVG 的 XSS 主流向量就是 `onload=`:这张表是 `render_chart` 那条路写出来的。"""
    src = ('<svg onload="evil()"><rect onmouseover=\'evil2()\' width=10 '
           'height=10/></svg>')
    body, counts = panel.sanitize(src)
    assert "evil" not in body, body
    assert counts["on_attr"] == 2, counts
    assert "<svg" in body and "width=10" in body, "图形本体要留下"


def test_risky_tags_and_meta_refresh_are_dismantled():
    src = ('<iframe src="http://x"></iframe><form action="/s"><input>'
           '<link rel=stylesheet href="//x/y.css">'
           '<meta http-equiv="refresh" content="0;url=http://x">留着')
    body, counts = panel.sanitize(src)
    assert "<iframe" not in body and "<form" not in body and "<link" not in body
    assert "http-equiv" not in body.lower()
    assert counts["tag"] >= 3 and counts["meta_refresh"] == 1, counts
    assert "留着" in body and "<input>" in body


def test_javascript_scheme_is_defused_even_entity_encoded():
    """`&#106;avascript:` 是最容易漏的一种:字面比较看不见,浏览器解析后看得见。"""
    for src in ('<a href="javascript:evil()">点</a>',
                '<a href="&#106;avascript:evil()">点</a>',
                '<a href = "JaVaScRiPt:evil()">点</a>',
                '<a href="data:text/html;base64,PHNjcmlwdD4=">点</a>',
                "<a href=vbscript:evil()>点</a>"):          # 裸字(无引号)那种形状也要吃到
        body, counts = panel.sanitize(src)
        assert "evil" not in body and "PHNjcmlwdD4" not in body, src
        assert counts["scheme"] == 1, (src, counts)
        assert "点" in body, "链接文字要留着,只是它不再能执行"


def test_a_dangling_script_tag_without_a_close_is_gone_too():
    """只删成对的 `<script>` 会留下半截标签,而那半截会把后面的正文当脚本文本吞掉。"""
    body, counts = panel.sanitize('<script src="//evil/x.js"><p>表格正文</p>')
    assert "<script" not in body.lower() and "evil" not in body, body
    assert "表格正文" in body and counts["script"] >= 1


def test_ordinary_links_and_anchors_survive():
    body, counts = panel.sanitize('<a href="#sec2">跳</a><a href="notes.html">看</a>'
                                  '<img src="data:image/png;base64,AAA">')
    assert counts["scheme"] == 0
    assert "#sec2" in body and "notes.html" in body and "data:image/png" in body


def test_sanitize_is_quiet_on_empty_and_counts_are_all_zero_when_clean():
    body, counts = panel.sanitize("")
    assert body == "" and not any(counts.values())
    _, counts = panel.sanitize("<p>就一段话</p>")
    assert not any(counts.values())
    assert panel.stripped_note(counts) == "", "什么都没删就别道歉"


def test_the_note_says_what_was_stripped_in_numbers_not_vibes():
    _, counts = panel.sanitize("<script>x</script><a onclick='y'>z</a>")
    note = panel.stripped_note(counts)
    assert "脚本 1 处" in note and "事件属性 1 处" in note, note


# ---------------------------------------------------------------- 读与装

def test_read_refuses_a_file_over_the_cap_instead_of_truncating():
    raw = (b"<p>" + b"x" * panel.PANEL_MAX_BYTES + b"</p>")
    body, counts, why = panel.read_and_sanitize(raw)
    assert body == "" and why and "上限" in why, why


def test_read_falls_back_to_gbk_before_giving_up():
    raw = "<h1>成绩表</h1>".encode("gbk")
    body, _counts, why = panel.read_and_sanitize(raw)
    assert why == "" and "成绩表" in body, body


def test_read_refuses_bytes_that_are_no_text_at_all():
    body, _counts, why = panel.read_and_sanitize(b"\xff\xfe\x00\x01\x81\x82\x41\x42")
    assert body == "" and why, "(空正文 + 没实话 = 界面显示一张白卡)"


@pytest.mark.parametrize("kind", ["html", "svg"])
def test_the_csp_we_inject_is_the_first_thing_in_the_document(kind):
    """CSP 排在**内容之前**:排在后面就管不到前面那一段的解析。"""
    doc = panel.wrap_document("<p>正文</p>", kind)
    assert doc.index("Content-Security-Policy") < doc.index("正文")
    assert "default-src 'none'" in doc
    # 沙箱里断外联是这条 CSP 的活(沙箱本身管不了 `<img src=http://…>` 出网)
    assert "img-src data: blob:" in doc


# ---------------------------------------------------------------- 桥那一层:真文件

def _bridge(tmp_path):
    cfg = Config(providers=[Provider(id="p", name="P", base_url="https://x/v1",
                                      api_key="k", model="m")],
                 active_provider_id="p", mcp_servers=[], sandbox={})
    b, _ui, _s, _a = create_app(cfg, tmp_path)
    return b


def test_panel_preview_renders_a_real_file_from_the_workspace(tmp_path):
    b = _bridge(tmp_path)
    (tmp_path / "复盘.html").write_text(
        "<h1>期中复盘</h1><script>steal()</script><table><tr><td>高数</td></tr></table>",
        encoding="utf-8")
    r = b.panel_preview("复盘.html")
    assert r["ok"], r
    assert "steal" not in r["html"], "删没删要看内容,不是看返回码"
    assert "高数" in r["html"] and "default-src 'none'" in r["html"]
    assert r["stripped"]["script"] == 1 and "脚本 1 处" in r["note"]
    assert r["kind"] == "html"


def test_panel_preview_never_leaks_an_absolute_path(tmp_path):
    """回给界面的那一份里不许有本机绝对路径(与 `_upd_public`、`undo_records` 同口径)。

    这一格特别要紧:预览卡是要**截图分享**的东西,路径印在上面就等于把用户名发出去了。
    """
    b = _bridge(tmp_path)
    (tmp_path / "a.svg").write_text("<svg></svg>", encoding="utf-8")
    r = b.panel_preview("a.svg")
    assert r["ok"]
    assert str(tmp_path) not in repr(r), repr(r)[:200]
    assert "path" not in r, list(r)


@pytest.mark.parametrize("rel,needle", [
    ("../outside.html", "不能开"),
    ("..\\outside.html", "不能开"),
    # 开头一个 "/" 的写法**按工作区相对**处理(与 `get_asset` 同一条口径),所以它
    # 落在工作区里面 —— 越界的那一类是带盘符 / UNC 的,由 `_within` 拦。
    ("/etc/passwd.html", "没有这一份"),
    ("C:\\Windows\\win.ini.html", "外面"),
    ("\\\\fileserver\\share\\x.html", "UNC"),
    ("/etc/passwd", "只就地显示"),               # 不是这几类文件,连拼都不拼
    ("notes.txt", "只就地显示"),
    ("没这个文件.html", "没有这一份"),
])
def test_panel_preview_refuses_by_kind_not_by_mystery(tmp_path, rel, needle):
    """五种"开不出来"要说清是哪一种 —— 一句"预览失败"就是这个项目不许的含糊。"""
    b = _bridge(tmp_path)
    r = b.panel_preview(rel)
    assert r["ok"] is False, rel
    assert needle in r["why"], (rel, r["why"])


def test_panel_preview_refuses_a_symlink_pointing_out_of_the_workspace(tmp_path):
    """工作区里**放一个指出去的链接**也算工作区内的路径,这道闸要按解析后的判断。"""
    import os
    b = _bridge(tmp_path)
    secret = tmp_path.parent / "secret.html"
    secret.write_text("<p>不该看见</p><script>x()</script>", encoding="utf-8")
    link = tmp_path / "link.html"
    try:
        os.link(secret, link)          # 硬链接:resolve() 后仍在工作区内,是**另一回事**
        hard = b.panel_preview("link.html")
        assert hard["ok"] is True, "硬链接不是越界,别把它当攻击拦掉"
    except OSError:
        pass
    try:
        os.symlink(secret, tmp_path / "sym.html")
        r = b.panel_preview("sym.html")
        assert r["ok"] is False and "外面" in r["why"], r
    except (OSError, NotImplementedError):
        pass                            # 这台机器/这个权限不让建符号链接:那条断言没测成


# ---------------------------------------------------------------- gui_ui 那只闸

def test_panel_arg_only_takes_workspace_relative_html_and_svg():
    from minicode.gui_ui import _panel_arg
    assert _panel_arg({"path": "charts/期中.svg"}) == "charts/期中.svg"
    assert _panel_arg({"file_path": "report.html"}) == "report.html"
    # 绝对路径不进事件(它与 `_upd_public`、`undo_records` 是同一条口径)
    assert _panel_arg({"path": r"C:\Users\me\report.html"}) == ""
    assert _panel_arg({"path": "/var/www/x.html"}) == ""
    # 不是这几类扩展名就不挂卡
    assert _panel_arg({"path": "notes.md"}) == ""
    assert _panel_arg({}) == ""
    assert _panel_arg(None) == ""


def test_panel_arg_accepts_windows_separators_and_rejects_other_kinds():
    from minicode.gui_ui import _panel_arg
    assert _panel_arg({"path": "charts\\期中.svg"}) == "charts/期中.svg"
    assert _panel_arg({"path": "复盘.html"}) == "复盘.html"
    assert _panel_arg({"path": "charts/期中.png"}) == "", "图片走 get_asset 那条老路"


def test_the_frontend_layer_can_reach_the_backend_method():
    """桥上有 `panel_preview` 而 `PublicApi` 没转发 = 界面点了没反应(上一轮刚踩过)。"""
    from minicode.gui_bridge import PublicApi
    assert callable(getattr(PublicApi, "panel_preview", None))
