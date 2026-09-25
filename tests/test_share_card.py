"""回答分享卡片:来源自动带上、超长如实截、路径前端传不进来(本轮功能 2)。

同学的分享主场是微信/QQ 群,而"把一段回答复制成文字发出去"会丢掉出处 —— 这个项目
一路的本能是"引用可核实",所以卡片尾部自动列出回答里出现过的链接;没有链接就整段
不出现,不放一句"来源见聊天"充数。

这份文件守四件事:
1. 来源抽取按出现顺序、去重、不吃中文标点;
2. 超长时**明说**被截断(不静默裁尾),落款与日期恒在;
3. 真写出一个 PNG(Pillow 路线,不是 SVG 糊弄),字节以 PNG magic 开头;
4. 标题里塞 `../../evil` 也逃不出工作区 `images/` —— 路径由后端拼,前端只能给文字。
"""
from pathlib import Path

from minicode import images
from minicode.images import (SHARE_CHAR_BUDGET, compose_share_card, extract_sources,
                             save_share_card)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- 来源抽取

def test_sources_keep_order_and_dedupe():
    text = ("国家励志奖学金见 https://gzasc.edu.cn/a.html，"
            "补充 https://mp.weixin.qq.com/s/xyz，再贴一次 https://gzasc.edu.cn/a.html")
    assert extract_sources(text) == ["https://gzasc.edu.cn/a.html",
                                     "https://mp.weixin.qq.com/s/xyz"]


def test_chinese_punctuation_is_not_swallowed_into_the_url():
    got = extract_sources("详见 https://gzasc.edu.cn/jwgl，逾期不候（重要）")
    assert got == ["https://gzasc.edu.cn/jwgl"]


def test_no_links_yields_no_sources():
    assert extract_sources("图书馆三楼社科阅览室") == []


# ---------------------------------------------------------------- 卡片正文

def test_brand_and_date_are_always_on_the_card():
    text, clipped = compose_share_card("绩点按必修口径算", sources=[], today="2026-09-26")
    assert clipped is False
    assert "引路学长" in text and "广州应用科技学院" in text and "2026-09-26" in text
    assert "来源" not in text, "没有链接时不该出现来源段,更不该写「来源见聊天」"


def test_sources_section_lists_each_url_once():
    text, _ = compose_share_card("结论如下", sources=["https://a.cn/x", "https://b.cn/y"],
                                 today="2026-09-26")
    assert "来源（可点开核实）：" in text
    assert text.count("https://a.cn/x") == 1 and "https://b.cn/y" in text


def test_overlong_answers_are_clipped_and_said_so():
    long = "奖" * (SHARE_CHAR_BUDGET + 400)
    text, clipped = compose_share_card(long, sources=[], today="")
    assert clipped is True
    assert "完整回答请回聊天里看" in text, "截断了就要说,不能让用户以为发出去的是完整版"


def test_source_list_is_capped():
    urls = [f"https://a.cn/{i}" for i in range(12)]
    text, _ = compose_share_card("很多来源", sources=urls, today="")
    assert text.count("https://a.cn/") == images.SHARE_SOURCE_CAP


# ---------------------------------------------------------------- 真落盘

def test_it_writes_a_real_png_into_workspace_images(tmp_path):
    got = save_share_card(tmp_path, "四六级报名见 https://gzasc.edu.cn/cet4 通知")
    assert got["ok"] is True, got
    assert got["path"].startswith("images/")
    assert got["bytes"] > 1000 and got["sources"] == ["https://gzasc.edu.cn/cet4"]
    f = tmp_path / got["path"]
    assert f.is_file() and f.read_bytes()[:8] == PNG_MAGIC


def test_a_second_card_does_not_overwrite_the_first(tmp_path):
    first = save_share_card(tmp_path, "同一句回答", title="绩点")
    second = save_share_card(tmp_path, "同一句回答", title="绩点")
    assert first["path"] != second["path"]
    assert (tmp_path / first["path"]).is_file() and (tmp_path / second["path"]).is_file()


def test_title_cannot_escape_the_images_dir(tmp_path):
    got = save_share_card(tmp_path, "正文在这里", title="../../evil")
    assert got["ok"] is True, got
    written = (tmp_path / got["path"]).resolve()
    assert written.parent == (tmp_path / "images").resolve()
    assert written.is_file()
    assert not (tmp_path / "evil.png").exists()
    assert list(Path(tmp_path).rglob("evil*")) == []


def test_empty_answer_refuses_without_writing_anything(tmp_path):
    got = save_share_card(tmp_path, "   \n  ")
    assert got["ok"] is False and "没有文字内容" in got["msg"]
    assert not (tmp_path / "images").exists(), "失败了就不该留下半个目录或空文件"
