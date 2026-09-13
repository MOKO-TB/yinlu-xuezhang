"""按主题爬取学校官网栏目,生成带来源链接的校园问答知识库。

与 minicode/tools/crawl.py 的通用 BFS 爬虫不同,这里按"用户会问什么"组织:
    每个主题 = 若干栏目列表页(可翻页) + 关键词过滤 + 主题标签
列表页只用来找文章链接;文章页抓整页 markdown 后调用 strip_boilerplate 去掉
每页重复的导航/页脚,再从文章标题处截断,只留正文。这样标题、发布日期、正文
三者都能保留(用 CSS 选择器只取正文容器会丢掉标题和发布日期)。

结果:
    crawled/<host>/school-qa/*.md   每篇文章的正文归档(带 frontmatter)
    kb/school-<学校简称>.json       知识库条目(带 url/publish_at/tags,credibility=A)

用法:
    .venv/Scripts/python.exe scripts/crawl_school_topics.py                  # 全量
    .venv/Scripts/python.exe scripts/crawl_school_topics.py --only 奖助学金   # 只跑一个主题
    .venv/Scripts/python.exe scripts/crawl_school_topics.py --limit 3        # 每个主题最多 3 篇
    .venv/Scripts/python.exe scripts/crawl_school_topics.py --dry-run        # 只列链接不抓正文

配置见 scripts/school_topics.json;换学校时改配置即可,不用改代码。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:                    # 支持从任意目录调用
    sys.path.insert(0, str(ROOT))

from minicode.tools.crawl import (KB_MIN_CONTENT, _md_text,  # noqa: E402
                                  _plain_text_len, _slug, _write_kb,
                                  DEFAULT_CUT_MARKERS, extract_body,
                                  strip_boilerplate)

DEFAULT_CONFIG = ROOT / "scripts" / "school_topics.json"
PAGE_DELAY = 0.6


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 文本处理

_LEAD_NOISE = re.compile(
    r"^\s*(?:"
    r"20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2}\s*日?"      # 2026-09-10 / 2026年9月10日
    r"|\d{1,2}\s+20\d{2}[-/.]\d{1,2}"                  # 14 2020-04
    r"|\d{1,2}[-/.]\d{1,2}\s+20\d{2}"                  # 09-11 2026
    r"|\d{1,2}[-/.]\d{1,2}"                            # 09-11
    r"|\d{1,2}月\d{1,2}日"
    r")\s*")

# 注意:re 的字符类里不解析 \uXXXX,中文冒号必须写成字面量
_PUB_DATE_RE = re.compile(r"发布[日时][期间]\s*[:：]?\s*"
                          r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
_FULL_DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
_MDY_RE = re.compile(r"(\d{1,2})[-/.](\d{1,2})\s+(20\d{2})")
_MONTHS = {name.lower(): i
           for i, names in enumerate(
               [("january", "jan"), ("february", "feb"), ("march", "mar"),
                ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
                ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
                ("november", "nov"), ("december", "dec")], start=1)
           for name in names}
_EN_DATE_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(20\d{2})")


def _clean_title(raw: str, suffix_cut: list[str]) -> str:
    """去掉标题里的栏目/站点后缀与列表页前面的日期噪声。"""
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    for cut in suffix_cut:
        idx = text.find(cut)
        if idx > 0:
            text = text[:idx]
    for _ in range(3):                                # 反复剥离前缀日期,直到稳定
        new = _LEAD_NOISE.sub("", text)
        if new == text:
            break
        text = new
    return text.strip(" -—·|")


def _meta_title(result) -> str:
    """整页抓取时标题在 metadata 里(crawl4ai 0.9+);兜底取正文首个 # 标题。"""
    meta = getattr(result, "metadata", None)
    if isinstance(meta, dict) and meta.get("title"):
        return str(meta["title"]).strip()
    title = str(getattr(result, "title", "") or "").strip()
    if title:
        return title
    m = re.search(r"^#\s+(.+)$", _md_text(getattr(result, "markdown", None)), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _find_publish_at(html: str, listing_text: str = "") -> str:
    """发布日期:先看页面里的"发布日期:",再退回列表页链接文本里的日期。

    招生网等子站用的是英文日期(发布时间:May 28, 2026),所以要一并解析。
    """
    m = _PUB_DATE_RE.search(html or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _EN_DATE_RE.search(html or "")
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = _FULL_DATE_RE.search(listing_text or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _MDY_RE.search(listing_text or "")            # 列表页常见 "09-11 2026"
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return ""


_ATTACH_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")


def _doc_ext_from_url(url: str) -> str:
    """从 URL 认出"这是文档附件"及它的后缀,不是文档就返回空串。

    博达的附件是 `virtual_attach_file.vsb?...&e=.pdf`,但同一路径也用来送二维码图片和
    视频(`e=.png` / `e=.mp4`),所以必须看后缀白名单:否则二维码会被当附件,
    热水充值页就被写成"仅附件"而不是"扫码查看"。
    """
    low = (url or "").lower().split("#", 1)[0]
    m = _EXT_IN_QUERY_RE.search(low)
    if m and m.group(1) in _ATTACH_EXT:
        return m.group(1)
    if "download.jsp" in low:
        return m.group(1) if m else ".file"
    path = low.split("?", 1)[0]
    for ext in _ATTACH_EXT:
        if path.endswith(ext):
            return ext
    return ""


def _attachment_names(result, body: str = "") -> list[str]:
    """页面上的附件名(学校很多管理办法只在附件里,正文只有一行"附件:xxx")。

    两个来源都看:①crawl4ai 抽出的链接;②正文 markdown——附件链接长这样
    `[管理办法.pdf](/virtual_attach_file.vsb?...&e=.pdf)`,链接文本有时为空,
    这时按后缀合成一个名字,总比把整页丢掉好。
    """
    out: list[str] = []
    for link in (getattr(result, "links", None) or {}).get("internal", []) or []:
        if not isinstance(link, dict):
            continue
        ext = _doc_ext_from_url(link.get("href") or "")
        if not ext:
            continue
        name = re.sub(r"\s+", " ", (link.get("text") or "")).strip()
        if not name:
            name = f"附件({ext})"
        if name not in out:
            out.append(name[:120])
    for name in _attachment_names_from_body(body):
        if name not in out:
            out.append(name)
    return out


_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\((https?://[^)]+)\)")
_EXT_IN_QUERY_RE = re.compile(r"[?&]e=(\.?[a-zA-Z0-9]{1,5})", re.I)


def _attachment_names_from_body(body: str) -> list[str]:
    """从正文 markdown 里找附件链接名(`[名称](virtual_attach_file.vsb?...&e=.pdf)`)。"""
    out: list[str] = []
    for text, url in _MD_LINK_RE.findall(body or ""):
        ext = _doc_ext_from_url(url)
        if not ext:
            continue
        name = re.sub(r"\s+", " ", text).strip()
        if not name:
            name = f"附件({ext})"
        if name not in out:
            out.append(name[:120])
    return out


def _attachment_only_page(title: str, publish_at: str, names: list[str]) -> str:
    """仅附件页的条目正文:说清"全文在附件里、要验证码"并列出附件名,便于检索与引用。"""
    lines = [f"# {title}"]
    if publish_at:
        lines.append(f"发布日期:{publish_at}")
    lines.append("正文以官网附件形式发布(在官网页面上输入验证码后可下载),本条目为文件说明:")
    lines += [f"- 附件:{n}" for n in names[:5]]
    lines.append("如需原文,请打开来源网址在线查看或下载。")
    return "\n".join(lines)


# 视频页正文为空(内容全在播放器里),必须靠页面结构识别,否则会被当空页丢弃。
# 注意:.vsb 附件既可能是视频也可能是二维码图片(热水充值/报修页),所以只能认
# <video> 标签和明确的视频后缀,.vsb 不能算视频证据(否则图片指引页会被误标成视频)。
_VIDEO_MARKERS = ("<video", ".mp4", ".m3u8")


def _has_video(result) -> bool:
    """页面内嵌了播放器或视频流地址(视频聚焦/校园活动等栏目)。"""
    html = (getattr(result, "html", "") or "").lower()
    return any(m in html for m in _VIDEO_MARKERS)


def _video_page(title: str, publish_at: str, url: str = "") -> str:
    """视频条目的正文:短视频站抓不到字幕,但页面链接本身就能看。"""
    lines = [f"# {title}"]
    if publish_at:
        lines.append(f"发布日期:{publish_at}")
    lines.append("本条为视频内容:打开来源网址即可在线观看(页面内嵌播放器)。")
    if url:
        lines.append(f"观看地址: {url}")
    return "\n".join(lines)


def _has_image(result, body: str = "") -> bool:
    """正文内容区里有图(热水充值/报修的二维码、校园风光照片)。

    只看整页有没有 <img> 会误判:任何页面的页眉都有 logo。所以只认正文容器
    (`#vsb_content` / `.v_news_content`)里的图,或正文 markdown 里的图片链接。
    这类页文字为 0,但"去哪儿办"本身就是答案,所以要留成条目+链接。
    """
    if re.search(r"!\[[^\]]*\]\((?:https?://[^)]*(?:virtual_attach_file|"
                 r"__local|/uploadfiles/)[^)]*)\)", body or ""):
        return True
    html = getattr(result, "html", "") or ""
    for marker in ('id="vsb_content"', 'class="v_news_content"',
                   "class='v_news_content'"):
        idx = html.find(marker)
        if idx >= 0 and "<img" in html[idx:idx + 4000].lower():
            return True
    return False


def _image_page(title: str, publish_at: str, url: str = "") -> str:
    """图片/二维码指引页的条目正文。"""
    lines = [f"# {title}"]
    if publish_at:
        lines.append(f"发布日期:{publish_at}")
    lines.append("本条内容以图片/二维码形式发布(如操作指引、名单扫描件),"
                 "文字无法完整提取,请打开来源网址查看。")
    if url:
        lines.append(f"查看地址: {url}")
    return "\n".join(lines)


# 页面右侧悬浮的"分享到微信/抖音/小红书"图标:既不是正文也没信息量,
# 不清理就会占满条目开头,把检索摘要浪费掉。
# 注意:抓下来的 markdown 里是转义过的 `javascript:void\(0\);`(带反斜杠),
# 所以正则得容忍转义,否则整个清理都失效。
_JS_VOID = r"javascript\s*:\s*void\\?\(\s*0\s*\\?\)"
_SHARE_ICON_RE = re.compile(
    r"!?\[?\s*!\[[^\]]*\]\([^)]*\)\s*\]?\(" + _JS_VOID + r";?[^)]*\)")
_QR_IMG_RE = re.compile(r"!?\[?\s*!\[[^\]]*\]\([^)]*qrcode[^)]*\)\s*\]?")
_EMPTY_JS_LINK_RE = re.compile(r"\[?\s*\]?\(" + _JS_VOID + r";?[^)]*\)")
# 剥掉 markdown 结构后剩下的裸令牌(`(javascript:void\(0\);)` 成串出现),
# 不清掉会让"这页只剩页脚"的判定失效。
_BARE_JS_RE = re.compile(r"\(?\s*" + _JS_VOID + r";?\s*\)?", re.I)
_BOILERPLATE_ONLY_LIMIT = 60     # 除去标题/页脚后不足这么多字,就不算文章正文


def _strip_share_noise(text: str) -> str:
    """去掉分享图标/二维码图片组成的假正文行,再把因删除产生的空行归并。"""
    text = _SHARE_ICON_RE.sub(" ", text or "")
    text = _QR_IMG_RE.sub(" ", text)
    text = _EMPTY_JS_LINK_RE.sub(" ", text)
    text = _BARE_JS_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# 抓不到正文时别把 404 提示页当文章存进知识库
_ERROR_TITLE_RE = re.compile(r"404|错误提示|页面未找到|not\s*found|系统提示", re.I)


def _is_error_page(title: str) -> bool:
    return bool(_ERROR_TITLE_RE.search(title or ""))


# 页面底部/侧边的固定噪声:页脚宣传语、地址、发布时间标签等
_FOOTER_NOISE_MARKERS = ("适者大成", "招生专线", "朱村大道东", "丰乐路20号",
                         "共赴美好未来", "最新动态", "Share:")
_BODY_NOISE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#+\s*)?(?:发布时间|发布日期|发表时间|Share|当前位置|"
    r"来源|作者|点击|浏览次数|字体|打印|分享|上一篇|下一篇|上一条|下一条)"
    r"[:：]?.*$",
    re.M)


def _body_is_boilerplate_only(body: str) -> bool:
    """正文里除了标题/发布时间/页脚宣传语外几乎没别的字 → 这不是一篇文章。

    热水充值、报修、视频这类页面的 markdown 去导航后只剩标题+页脚(也能凑过
    KB_MIN_CONTENT),直接入库就是把页脚噪声当内容，得改走"媒体页"分支把链接给出去。
    """
    text = _BODY_NOISE_LINE_RE.sub("", body or "")
    keep: list[str] = []
    for line in text.splitlines():
        s = line.strip().strip("#").strip()
        if not s or s.startswith("!") or _match_any(s, _FOOTER_NOISE_MARKERS):
            continue
        keep.append(s)
    rest = "\n".join(keep)
    return _plain_text_len(rest) < _BOILERPLATE_ONLY_LIMIT


def _is_article_url(url: str) -> bool:
    p = urlparse(url)
    return "/info/" in p.path and p.path.lower().endswith(".htm")


def _match_any(text: str, words) -> bool:
    return any(w and w in text for w in words or ())


def _listing_urls(seed: dict) -> list[str]:
    """栏目首页 + 翻页地址。

    博达 CMS 主站翻页是 <栏目>/N.htm(N 从 1 开始 = 第 2 页),但新闻网子站是倒序
    (第 2 页 = /news/xxyw/120.htm)。所以配置里可以用 list_pages 直接写明翻页地址,
    不用猜规则。
    """
    url = seed["url"]
    urls = [url]
    extra = seed.get("list_pages")
    if extra:
        return urls + [u for u in extra if u and u != url]
    pages = max(1, int(seed.get("pages", 1)))
    if pages > 1 and url.lower().endswith(".htm"):
        base = url[:-4]
        urls += [f"{base}/{i}.htm" for i in range(1, pages)]
    return urls


# ---------------------------------------------------------------- 抓取

async def _fetch(crawler, url: str, cfg):
    try:
        return await crawler.arun(url=url, config=cfg)
    except Exception as e:                            # 单页失败不影响整体
        _log(f"    !! 抓取失败 {url} ({type(e).__name__}: {e})")
        return None


async def _listing_articles(crawler, cfg, page_url: str, host: str,
                            cache: dict) -> list[dict]:
    """抓一个列表页并返回其中的文章链接(未过滤)。

    结果按 URL 缓存:二级学院首页这类栏目会被多个主题用到(考研/榜样/就业/学风),
    同一次运行里只请求一次,既省时间也更礼貌。
    """
    if page_url in cache:
        return cache[page_url]
    items: list[dict] = []
    result = await _fetch(crawler, page_url, cfg)
    if result is not None and getattr(result, "success", False):
        links = (getattr(result, "links", None) or {}).get("internal", []) or []
        seen: set[str] = set()
        for link in links:
            if not isinstance(link, dict):
                continue
            href = urljoin(page_url, (link.get("href") or "").strip()).split("#", 1)[0]
            if not href or href in seen or not _is_article_url(href):
                continue
            netloc = urlparse(href).netloc
            if netloc and not netloc.endswith(host):
                continue
            seen.add(href)
            items.append({"url": href,
                          "raw_title": re.sub(r"\s+", " ", (link.get("text") or "")).strip()})
    cache[page_url] = items
    return items


async def _collect_links(crawler, cfg, topic: dict, host: str, limit: int,
                         cache: dict) -> list[list[dict]]:
    """按栏目分桶收集候选文章链接(已按 include/exclude 过滤)。

    分桶是为了后面轮转取人:不这样做,排在前面的老栏目会占满名额,
    把另一个栏目里最新的通知挤掉。
    """
    include = topic.get("include") or []
    exclude = topic.get("exclude") or []
    seen: set[str] = set()
    buckets: list[list[dict]] = []
    for seed in topic.get("seeds") or []:
        bucket: list[dict] = []
        for page_url in _listing_urls(seed):
            if len(bucket) >= limit * 2:
                break
            cached = page_url in cache
            items = await _listing_articles(crawler, cfg, page_url, host, cache)
            added = 0
            for item in items:
                raw_title = item["raw_title"]
                if item["url"] in seen or _match_any(raw_title, exclude):
                    continue
                if include and not _match_any(raw_title, include):
                    continue
                seen.add(item["url"])
                bucket.append(item)
                added += 1
            _log(f"    · {page_url} -> 新增候选 {added}(本栏目 {len(bucket)})"
                 f"{'[缓存]' if cached else ''}")
            if not cached:
                await asyncio.sleep(PAGE_DELAY)
        if bucket:
            buckets.append(bucket)
    return buckets


def _interleave(buckets: list[list[dict]]) -> list[dict]:
    """各栏目轮转取人,保证每个栏目的最新内容都有机会入选。"""
    out: list[dict] = []
    depth = 0
    while any(len(b) > depth for b in buckets):
        for bucket in buckets:
            if len(bucket) > depth:
                out.append(bucket[depth])
        depth += 1
    return out


async def crawl_topic(crawler, cfg, topic: dict, site_cfg: dict, limit: int,
                      dry_run: bool, archive_dir: Path, cache: dict) -> list[dict]:
    """抓一个主题:列链接 → 抓正文 → 返回可直接写库的 page 字典列表。"""
    host = site_cfg["host"]
    suffix_cut = site_cfg.get("title_suffix_cut") or []
    cut_markers = site_cfg.get("cut_markers") or []
    _log(f"  [主题] {topic['id']}")

    candidates = _interleave(
        await _collect_links(crawler, cfg, topic, host, limit, cache))
    for url in topic.get("articles") or []:           # 配置里写死的文章(列表页抓不到链接的站点)
        candidates.append({"url": url, "raw_title": ""})
    if dry_run:
        for cand in candidates[:limit]:
            _log(f"      - {cand['url']}")
        return []

    pages: list[dict] = []
    empty = 0
    seen_titles: set[str] = set()
    for cand in candidates:
        if len(pages) >= limit:
            break
        url = cand["url"]
        result = await _fetch(crawler, url, cfg)
        if result is None or not getattr(result, "success", False):
            continue
        meta_title = _meta_title(result)
        if _is_error_page(_clean_title(meta_title, suffix_cut) or meta_title):
            empty += 1                      # 404 提示页:官网会把失效链接也渲染成 200
            continue
        content = _strip_share_noise(
            extract_body(_md_text(getattr(result, "markdown", None)),
                         meta_title, cut_markers or DEFAULT_CUT_MARKERS))
        keep = False
        kind = ""
        if (_plain_text_len(content) < KB_MIN_CONTENT
                or _body_is_boilerplate_only(content)):
            # 两种情况都说明"这页没有真正的正文",但内容本身对学生仍有用:
            # 附件页(管理办法/培养方案)、视频页、二维码指引页(热水充值/报修)。
            names = _attachment_names(result, content)
            title_guess = _clean_title(meta_title, suffix_cut) \
                or _clean_title(cand.get("raw_title", ""), suffix_cut)
            publish_guess = _find_publish_at(getattr(result, "html", "") or "",
                                             cand.get("raw_title", ""))
            if names and title_guess:
                content = _attachment_only_page(title_guess, publish_guess, names)
                keep, kind = True, "仅附件"
            elif title_guess and _has_video(result):
                content = _video_page(title_guess, publish_guess, url)
                keep, kind = True, "视频页"
            elif title_guess and _has_image(result, content):
                content = _image_page(title_guess, publish_guess, url)
                keep, kind = True, "图文指引"
            else:
                empty += 1
                continue
        title = (_clean_title(meta_title, suffix_cut)
                 or _clean_title(cand.get("raw_title", ""), suffix_cut) or url)
        # 官网会把同一条通知挂到两个 URL 上(不同栏目各发一次),按标题去重省一个检索位
        key = re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        publish_at = _find_publish_at(getattr(result, "html", "") or "",
                                      cand.get("raw_title", ""))
        page = {"url": url, "title": title, "content": content, "keep": keep,
                "publish_at": publish_at, "topic": topic["id"],
                "file": f"{topic['id']}-{len(pages) + 1:03d}-"
                        f"{_slug(urlparse(url).path, 'article')}.md"}
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / page["file"]).write_text(
            f"---\nurl: {url}\ntitle: {title}\ntopic: {topic['id']}\n"
            f"publish_at: {publish_at}\ncrawled_at: {time.strftime('%Y-%m-%d %H:%M')}\n"
            f"---\n\n{content}\n", encoding="utf-8")
        pages.append(page)
        _log(f"      ✓ {title[:46]}({publish_at or '无日期'},"
             f"{_plain_text_len(content)} 字{', ' + kind if kind else ''})")
        await asyncio.sleep(PAGE_DELAY)
    _log(f"    {topic['id']}:入库 {len(pages)} 篇,跳过空正文 {empty} 篇")
    return pages


def _prune_entries(cfg: dict, entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """清理已入库但已被关键词排除的条目。返回 (保留, 删除)。

    只在条目所属的**每个**主题都排除它时才删:同一条文章可能同时属于两个主题
    ("挑战杯"在就业主题里是噪音,在学科竞赛主题里是正主),不能因为一个主题
    不要就把共享条目删掉。
    """
    topics = {t["id"]: t for t in cfg.get("topics") or []}
    kept, removed = [], []
    for e in entries:
        own = [topics[t] for t in (e.get("tags") or []) if t in topics]
        if own and all(_match_any(e.get("title", ""), t.get("exclude") or []) for t in own):
            removed.append(e)
        else:
            kept.append(e)
    return kept, removed


def _load_kb(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _rebuild_archive_index(archive_dir: Path, site: str = "") -> int:
    """扫描归档目录的 .md frontmatter,重建 index.json。

    按目录实际内容重建而不是累积内存里的结果:单主题补爬(--only)不会把整份清单
    覆盖成只剩那一个主题。
    """
    rows: list[dict] = []
    for f in sorted(archive_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        meta: dict[str, str] = {}
        m = _FRONTMATTER_RE.match(text)
        if m:
            for line in m.group(1).splitlines():
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        rows.append({"topic": meta.get("topic", ""), "title": meta.get("title", ""),
                     "url": meta.get("url", ""), "publish_at": meta.get("publish_at", ""),
                     "file": f.name})
    rows.sort(key=lambda r: (r["topic"], r["publish_at"] or "", r["file"]))
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "index.json").write_text(
        json.dumps({"site": site, "rebuilt_at": time.strftime("%Y-%m-%d %H:%M"),
                    "count": len(rows), "pages": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------- 主流程

async def main() -> int:
    ap = argparse.ArgumentParser(description="按主题爬取校园官网,生成问答知识库")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--only", default="", help="只抓某个主题 id(模糊匹配)")
    ap.add_argument("--limit", type=int, default=0, help="每主题最多几篇(默认取配置)")
    ap.add_argument("--dry-run", action="store_true", help="只列候选链接,不抓正文")
    ap.add_argument("--prune", action="store_true",
                    help="只清理已入库、但被主题 exclude 关键词排除的条目(不联网)")
    args = ap.parse_args()

    site_cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    topics = site_cfg["topics"]
    if args.only:
        topics = [t for t in topics if args.only in t["id"]]
        if not topics:
            _log(f"没有匹配的主题: {args.only}")
            return 1
    limit_default = int(site_cfg.get("max_articles_per_topic", 10))

    kb_path = ROOT / site_cfg["output_kb"]
    if args.prune:
        entries = _load_kb(kb_path)
        kept, removed = _prune_entries({"topics": topics} if args.only else site_cfg, entries)
        kb_path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
        _log(f"清理完成:{len(entries)} 条 → {len(kept)} 条(删除 {len(removed)} 条)")
        for e in removed[:15]:
            _log(f"  - 删除 {e.get('title', '')[:56]}")
        return 0

    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError:
        _log("错误: 未安装 crawl4ai。请执行 pip install -U crawl4ai && crawl4ai-setup")
        return 1

    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=45000,
                               verbose=False)
    archive_dir = ROOT / site_cfg.get("out_dir", f"crawled/{site_cfg['host']}/school-qa")
    t0 = time.time()
    total = 0
    listing_cache: dict = {}          # 列表页 URL -> 文章链接,同一次运行内复用
    _log(f"开始爬取 {site_cfg['site']}(主题 {len(topics)} 个,"
         f"每主题上限 {args.limit or limit_default} 篇)")

    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        for topic in topics:
            limit = args.limit or int(topic.get("max_articles") or limit_default)
            pages = await crawl_topic(crawler, run_cfg, topic, site_cfg, limit,
                                      args.dry_run, archive_dir, listing_cache)
            if pages:
                path, written, skipped = _write_kb(
                    ROOT, site_cfg["host"], pages, site_cfg["site"],
                    category=topic["id"], extra_tags=topic.get("tags") or [],
                    content_limit=int(site_cfg.get("content_limit", 2000)),
                    min_content=KB_MIN_CONTENT,
                    filename=Path(site_cfg["output_kb"]).name)
                _log(f"    → {path.name}:写入/更新 {written} 条(跳过低质 {skipped})")
                total += written

    if not args.dry_run:
        archived = _rebuild_archive_index(archive_dir, site_cfg["site"])
        _log(f"\n完成:共写入知识库 {total} 条,用时 {time.time() - t0:.0f}s。"
             f"文件 kb/{Path(site_cfg['output_kb']).name},"
             f"归档 {archived} 篇于 {archive_dir.name}/(含 index.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
