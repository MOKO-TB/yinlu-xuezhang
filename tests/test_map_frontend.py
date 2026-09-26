"""地图前端(map.js)与后端 campus.py 的对账针:normalize 规则必须同源。

map.js 手抄了后端 campus.normalize() 的三样东西:动作/疑问词表、标点正则、
栋/座/楼等价。注释里写着"规则不一致就会出现问了能查到、手输搜到的不一样"
—— 自觉不够,哪天后端加一个新俗称词前端就静默漂。这根针从 map.js 源文本里
把那几段字面量抽出来与后端逐项对账;改任何一边,另一边不同步就当场红。
"""
import re
from pathlib import Path

from minicode import campus

ROOT = Path(__file__).resolve().parent.parent
MAP_JS = ROOT / "frontend" / "map.js"

BS = "\\"


def _strip_esc(pattern: str) -> str:
    """把两边正则剥掉反斜杠再比:JS 与 Python 在字符类里的转义写法不同
    (py 写 \\.,JS 直接写 .),语义相同、字面不同,逐字比会假红。"""
    return pattern.replace(BS + BS, "").replace(BS, "")


def _js_section(src: str) -> str:
    m = re.search(r"const ASK_WORDS = \[(.*?)\];", src, re.S)
    assert m, "map.js 里找不到 ASK_WORDS 数组(前端重命名了?把测试一起改)"
    return m.group(1)


def test_ask_words_match_backend():
    src = MAP_JS.read_text(encoding="utf-8")
    js_words = re.findall(r'"([^"]+)"', _js_section(src))
    assert sorted(js_words) == sorted(campus._ASK_WORDS), (
        "map.js 的动作/疑问词表与 campus._ASK_WORDS 漂了 —— 两边要一起改,"
        "否则会出现「问学长能定位、自己搜搜不到」(或反过来)")


def test_punct_pattern_matches_backend():
    src = MAP_JS.read_text(encoding="utf-8")
    m = re.search(r's = s\.replace\(/(.+?)/g, ""\);', src)
    assert m, "map.js 里找不到标点正则"
    assert _strip_esc(m.group(1)) == _strip_esc(campus._DROP.pattern), (
        "map.js 的标点剥离正则与 campus._DROP 漂了")


def test_unit_pattern_matches_backend():
    src = MAP_JS.read_text(encoding="utf-8")
    m = re.search(r'return s\.replace\(/(.+?)/g, ""\);', src)
    assert m, "map.js 里找不到栋/楼等价正则"
    assert _strip_esc(m.group(1)) == _strip_esc(campus._UNIT.pattern), (
        "map.js 的栋/座/楼等价正则与 campus._UNIT 漂了")
