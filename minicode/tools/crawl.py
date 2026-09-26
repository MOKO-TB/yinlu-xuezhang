"""web_crawl 工具:抓取网页/整站为 markdown,可选写入本地知识库。

从起始 url 出发做同域 BFS 跟链(max_pages/max_depth 限流,页间 delay 限速,
跳过 pdf/图片等二进制链接),输出到 crawled/<域名>/ 下:每页一个带 frontmatter
的 .md,附 index.json。to_kb=True 时额外生成 kb/crawl-<域名>.json,条目格式与
kb.py 一致(credibility=A,附来源 url 供核实),可被 search_knowledge 直接检索;
kb_category/kb_tags 给条目打主题标签(检索时标签权重×2),校园站点定向补爬用。

两个引擎,优先用 crawl4ai:
- crawl4ai(带真浏览器,能渲染 JS):源码环境直接用;打包版要求 exe 同级目录有
  ms-playwright/(浏览器内核,便携包里约 272MB,不塞进 exe 以免单文件版每次启动
  解压)。运行时会把 PLAYWRIGHT_BROWSERS_PATH 指过去。
- 内置轻量抓取器(httpx + BeautifulSoup,不启动浏览器):crawl4ai 不可用、或
  打包版没带浏览器内核时自动降级。静态站点(学校站群这类)效果与 crawl4ai 一致,
  需要 JS 渲染的站点会抓不到内容,此时会明确提示。
两个引擎的正文都经 strip_boilerplate + 标题截断,保证入库内容不带整站导航。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from minicode.paths import APP_DIR, FROZEN
from minicode.tools.base import (PathOutsideWorkspace, ToolContext,
                                 resolve_in_workspace)

SKIP_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                   ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif",
                   ".mp3", ".mp4", ".avi", ".exe", ".apk"}
KB_CONTENT_LIMIT = 2000      # 单条知识库内容截断,防 kb/*.json 膨胀
KB_MIN_CONTENT = 200         # 正文有效字数下限,低于此值视为纯导航页,不入库
SUMMARY_TITLES_LIMIT = 20
FETCH_MAX_BYTES = 5 * 1024 * 1024   # 单页响应体上限(v0.9.54):异常大页面不进内存
_ENGINE_NOTE = ""            # 降级到内置抓取器的原因(由 _engine() 写入)

# 站群 CMS 的页脚起始标记:正文之后的"上一条/版权所有/联系方式"都从这里切掉
DEFAULT_CUT_MARKERS = ("上一条", "下一条", "友情链接", "版权所有", "返回顶部")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _md_text(markdown_obj) -> str:
    """crawl4ai 的 result.markdown 是对象(fit_markdown/raw_markdown),兜底转 str。"""
    if markdown_obj is None:
        return ""
    fit = getattr(markdown_obj, "fit_markdown", None)
    if fit:
        return str(fit)
    raw = getattr(markdown_obj, "raw_markdown", None)
    if raw:
        return str(raw)
    return str(markdown_obj)


def _page_title(result, content: str, url: str) -> str:
    """页面标题:crawl4ai 0.9+ 放在 metadata['title'](旧版是 result.title),再退回正文首个标题。

    站内页大量没有 <title>,所以要保留从 markdown 首个 # 标题兜底的分支。
    """
    meta = getattr(result, "metadata", None)
    title = ""
    if isinstance(meta, dict):
        title = str(meta.get("title") or "").strip()
    if not title:
        title = str(getattr(result, "title", "") or "").strip()
    if not title or title == url:
        m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if m:
            title = m.group(1).strip()[:80]
    return title or url


def _plain_text_len(md: str) -> int:
    """正文有效字数:去掉图片/链接语法与空白,用于识别"只剩导航链接"的空页。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md or "")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*#>`~\-\s]+", "", text)
    return len(text)


_MD_ATOM = r"(?:!?\[(?:[^\[\]]|\[[^\]]*\])*\]\([^)]*\))"
_LINK_ONLY_LINE = re.compile(rf"^\s*(?:[*\-+]\s*)?{_MD_ATOM}(?:\s*{_MD_ATOM})*$")
_LIST_HEADING_LINK = re.compile(r"^\s*[*\-+]\s*#+\s*\[")
_EMPTY_ITEM_LINE = re.compile(r"^[\s\-*+•\u200b\u3000]*$")


def strip_boilerplate(md: str, cut_markers=()) -> str:
    """去掉网站每页重复的导航/页脚,只留正文。

    校园站点(尤其站群 CMS)的导航在每页都一样,不清理的话知识库摘要开头全是
    链接,检索和回答都会被带偏。做法是三条通用规则 + 站点的页脚起始标记:
      1. 丢掉"整行只有链接/图片"的行(导航菜单、面包屑链接、附件图标行);
      2. 丢掉 "* ### [栏目](...)" 这类导航标题行;
      3. 丢掉空列表项/零宽字符行(只有图片的菜单项会退化成 "-");
      4. 从 cut_markers 里任意标记所在行开始截断(如"上一条""版权所有")。
    """
    lines = []
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if _LIST_HEADING_LINK.match(line) or _LINK_ONLY_LINE.match(line):
            continue
        if _EMPTY_ITEM_LINE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)
    for marker in cut_markers or ():
        if not marker:
            continue
        m = re.search(rf"^{re.escape(marker)}", text, re.MULTILINE)
        if m and m.start() > 0:
            text = text[:m.start()]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_body(markdown: str, title: str,
                 cut_markers=DEFAULT_CUT_MARKERS) -> str:
    """整页 markdown → 正文:先去导航/页脚,再从文章标题所在行截断。

    标题通常来自 <title>(常带"-学校-栏目"后缀),而正文里的 h1 是干净标题,
    所以两个方向都做一次包含判断;找不到匹配标题就不截断,保持原样。
    """
    text = strip_boilerplate(markdown, cut_markers)
    needle = re.sub(r"\s+", "", title or "")
    if len(needle) >= 6:
        for i, line in enumerate(text.splitlines()):
            if not line.startswith("#"):
                continue
            candidate = re.sub(r"[\s#]+", "", line)
            if len(candidate) >= 6 and (candidate in needle or needle in candidate):
                return "\n".join(text.splitlines()[i:]).strip()
    return text


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", (text or "").strip()).strip("-")
    return (slug or fallback)[:60]


def _crawlable(url: str, host: str, match: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc != host:
        return False
    if p.path.lower().endswith(tuple(SKIP_EXTENSIONS)):
        return False
    if match and match not in url:
        return False
    return True


# ------------------------------------------------------- 内置轻量抓取器(无浏览器)

def _inline_md(tag, base_url: str) -> str:
    """把标签内的文字渲染成一行 markdown(链接保留成 [文字](绝对地址))。"""
    from bs4 import Comment, NavigableString, Tag
    out: list[str] = []
    for child in tag.children:
        if isinstance(child, Comment):          # HTML 注释不是正文(站群模板里很多)
            continue
        if isinstance(child, NavigableString):
            out.append(re.sub(r"\s+", " ", str(child)))
        elif isinstance(child, Tag):
            if child.name in ("script", "style", "img", "svg", "noscript"):
                continue
            if child.name == "a" and child.get("href"):
                text = _inline_md(child, base_url) or child.get("href", "")
                out.append(f"[{text}]({urljoin(base_url, child['href'].strip())})")
            else:
                out.append(_inline_md(child, base_url))
    return "".join(out).strip()


# form 不能跳过:博达站群把新闻正文整段包在 <form name="_newscontent_fromname"> 里,
# 跳掉 form 等于把正文丢了(实测正文只剩 51 字的残渣)。控件本身没有正文,单独跳过。
_SKIP_TAGS = ("script", "style", "noscript", "svg", "iframe", "img",
              "nav", "header", "footer", "aside",
              "input", "select", "textarea", "button", "option")
_INLINE_BLOCKS = ("p", "blockquote", "pre", "dd", "dt", "figcaption")


def _node_md(node, base_url: str, out: list[str]) -> None:
    """递归把块级元素转成 markdown 行(标题/列表/表格/段落)。"""
    from bs4 import Comment, NavigableString, Tag
    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = re.sub(r"\s+", " ", str(child)).strip()
            if text:
                out.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in _SKIP_TAGS:
            continue
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            out.append(f"\n{'#' * int(name[1])} {_inline_md(child, base_url)}\n")
        elif name == "li":
            out.append(f"\n- {_inline_md(child, base_url)}")
        elif name == "tr":
            cells = [_inline_md(cell, base_url)
                     for cell in child.find_all(["td", "th"], recursive=False)]
            if any(cells):
                out.append("\n| " + " | ".join(cells) + " |")
        elif name in _INLINE_BLOCKS:
            # 这些块本身就是一段文字,整体按行内规则渲染,避免丢掉段内链接
            text = _inline_md(child, base_url)
            out.append(f"\n{text}\n" if text else "\n")
        elif name in ("div", "section", "article", "main", "table", "ul", "ol",
                      "form", "tbody", "thead", "dl", "br", "hr", "td", "th"):
            out.append("\n")
            _node_md(child, base_url, out)
            out.append("\n")
        else:
            _node_md(child, base_url, out)


def html_to_markdown(html: str, base_url: str) -> tuple[str, str, list[dict]]:
    """HTML → (标题, markdown 正文, 站内链接列表)。只用 html.parser,不依赖 lxml。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    title = ""
    if soup.title:
        title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)).strip()
    links: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip()).split("#", 1)[0]
        if not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        links.append({"href": href,
                      "text": re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()})
    out: list[str] = []
    _node_md(soup.body or soup, base_url, out)
    md = re.sub(r"[ \t]+\n", "\n", "".join(out))
    return title, re.sub(r"\n{3,}", "\n\n", md).strip(), links


class _NativeResult:
    """对齐 crawl4ai 结果对象的最小接口(success/title/metadata/markdown/links)。"""

    def __init__(self, url: str, *, success: bool, title: str = "", markdown: str = "",
                 links=None, error: str = ""):
        self.url = url
        self.success = success
        self.title = title
        self.metadata = {"title": title} if title else {}
        self.markdown = markdown
        self.links = links or {"internal": [], "external": []}
        self.error_message = error


class NativeCrawler:
    """crawl4ai 不可用时的抓取器:httpx 取 HTML + BeautifulSoup 转 markdown。

    接口刻意与 crawl4ai 的 AsyncWebCrawler 一致(async with + arun),这样 BFS、
    落盘、入库逻辑两个引擎共用一份。需要 JS 渲染的页面这里拿不到内容。
    """

    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout
        self._client = None

    async def __aenter__(self):
        import httpx
        self._client = httpx.AsyncClient(
            follow_redirects=True, timeout=self.timeout,
            headers={"User-Agent": USER_AGENT,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            await self._client.aclose()
        return False

    async def arun(self, url: str, config=None):
        try:
            # 流式读 + 大小上限(v0.9.54):URL 来自模型/用户,一个异常大的响应体
            # 不该把内存吃满(与网页版 MAX_BODY_BYTES 同一族的资源闸)。
            req = self._client.build_request("GET", url)
            resp = await self._client.send(req, stream=True)
        except Exception as e:                      # 网络/超时:交给 BFS 记录失败
            return _NativeResult(url, success=False,
                                 error=f"{type(e).__name__}: {e}"[:200])
        try:
            if resp.status_code >= 400:
                return _NativeResult(url, success=False,
                                     error=f"HTTP {resp.status_code}")
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" not in ctype and "xml" not in ctype:
                return _NativeResult(url, success=False,
                                     error=f"非 HTML 内容({ctype[:40]})")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > FETCH_MAX_BYTES:
                    return _NativeResult(url, success=False,
                                         error=f"响应体超过 {FETCH_MAX_BYTES // (1 << 20)}MB 上限,已中止")
                chunks.append(chunk)
        finally:
            await resp.aclose()
        final = str(resp.url)
        html = (b"".join(chunks)).decode(resp.charset_encoding or "utf-8",
                                         errors="replace")
        title, md, links = html_to_markdown(html, final)
        return _NativeResult(final, success=True, title=title, markdown=md,
                             links={"internal": links, "external": []})


def _frozen_browser_dir() -> Path | None:
    """打包版把浏览器内核放在 exe 同级的 ms-playwright/(不塞进 exe:单文件版每次
    启动都要解压 270MB,太慢)。没有这个目录就退回内置轻量抓取器。"""
    if not FROZEN:
        return None
    candidate = Path(APP_DIR) / "ms-playwright"
    if candidate.is_dir() and any(candidate.glob("chromium*")):
        return candidate
    return None


def _engine():
    """选引擎:优先 crawl4ai(真浏览器,能渲染 JS),否则用内置轻量抓取器。

    打包版能不能用 crawl4ai 取决于 exe 旁有没有 ms-playwright/(浏览器内核);
    没有就自动降级,不会报错。降级原因写进 _ENGINE_NOTE,输出摘要会带上 ——
    否则"完整版为什么没用浏览器"只能靠猜。
    """
    global _ENGINE_NOTE
    _ENGINE_NOTE = ""
    if FROZEN:
        browser_dir = _frozen_browser_dir()
        if browser_dir is None:
            _ENGINE_NOTE = f"未见浏览器内核 {Path(APP_DIR) / 'ms-playwright'}"
            return "native", (lambda: NativeCrawler()), None
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
    try:
        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                              CrawlerRunConfig)
    except ImportError as e:
        _ENGINE_NOTE = f"crawl4ai 导入失败: {type(e).__name__}: {e}"
        return "native", (lambda: NativeCrawler()), None

    browser_cfg = BrowserConfig(headless=True)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=30000,
                               verbose=False)
    return "crawl4ai", (lambda: AsyncWebCrawler(config=browser_cfg)), run_cfg


async def _crawl_site(start_url: str, *, max_pages: int, max_depth: int,
                      match: str, delay: float, site_dir, factory, run_cfg):
    """同域 BFS 抓取并落盘。返回 (pages, failed);pages 项含 content 全文。

    factory 返回支持 async with 的 crawler(真实 crawl4ai 或测试假对象),
    crawler.arun(url=..., config=...) 返回带 success/title/markdown/links 的结果。
    """
    site_dir.mkdir(parents=True, exist_ok=True)
    host = urlparse(start_url).netloc
    queue: list[tuple[str, int]] = [(start_url, 0)]
    visited = {start_url}
    pages: list[dict] = []
    failed: list[dict] = []
    async with factory() as crawler:
        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            try:
                result = await crawler.arun(url=url, config=run_cfg)
            except Exception as e:   # 网络/超时等异常:记录后继续下一页
                failed.append({"url": url, "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            if not getattr(result, "success", False):
                failed.append({"url": url,
                               "error": (getattr(result, "error_message", "") or "抓取失败")[:200]})
                continue
            content = _md_text(getattr(result, "markdown", None))
            title = _page_title(result, content, url)
            content = extract_body(content, title)      # 去掉整站导航/页脚再落盘入库
            fname = f"{len(pages) + 1:03d}-{_slug(urlparse(url).path, 'index')}.md"
            stamp = time.strftime("%Y-%m-%d %H:%M")
            (site_dir / fname).write_text(
                f"---\nurl: {url}\ntitle: {title}\ncrawled_at: {stamp}\n---\n\n{content}\n",
                encoding="utf-8")
            pages.append({"url": url, "title": title, "file": fname, "content": content})
            if depth < max_depth:
                internal = (getattr(result, "links", None) or {}).get("internal", []) or []
                for link in internal:
                    href = link.get("href", "") if isinstance(link, dict) else ""
                    absolute = urljoin(url, (href or "").strip()).split("#", 1)[0]
                    if not absolute or absolute in visited:
                        continue
                    if not _crawlable(absolute, host, match):
                        continue
                    visited.add(absolute)
                    queue.append((absolute, depth + 1))
            await asyncio.sleep(delay)
    return pages, failed


def _write_kb(workspace, host: str, pages: list[dict], kb_source: str,
              category: str = "", extra_tags=(), content_limit: int = KB_CONTENT_LIMIT,
              min_content: int = KB_MIN_CONTENT, filename: str = ""):
    """把抓取结果合并进知识库(格式与 kb.py 的 KbEntry 对齐)。返回 (路径, 写入数, 跳过数)。

    按 url 去重合并而不是整文件覆盖:定向补爬新栏目时保留之前爬过的条目,
    同一 url 重爬则就地更新标题与正文(增量更新,永不丢旧数据)。
    正文过短(纯导航/列表页)的页面只留在 crawled/ 归档,不污染知识库检索。
    """
    import hashlib
    kb_dir = workspace / "kb"
    kb_dir.mkdir(exist_ok=True)
    path = kb_dir / (filename or f"crawl-{host}.json")
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = [e for e in data if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError):
            existing = []
    tags = list(dict.fromkeys(
        [t.strip() for t in [category, *extra_tags, "官网抓取", host] if t and t.strip()]))
    by_url = {e.get("url"): e for e in existing if e.get("url")}
    written = skipped = 0
    for p in pages:
        if not p.get("keep") and _plain_text_len(p["content"]) < min_content:
            skipped += 1
            continue
        # 可信度按域名白名单定(注入防御 M4):以前这里无条件标 A,
        # 等于把"任何爬回来的页面"当权威资料,且会被之后每次检索召回。
        from minicode.kb import crawl_credibility
        entry = {
            "id": f"crawl-{host}-{hashlib.md5(p['url'].encode()).hexdigest()[:8]}",
            "title": p["title"],
            "content": p["content"][:content_limit],
            "url": p["url"],
            "source": kb_source or host,
            "tags": tags,
            "credibility": crawl_credibility(host),
        }
        if p.get("publish_at"):
            entry["publish_at"] = p["publish_at"]
        old = by_url.get(p["url"])
        if old is not None:               # 重爬:更新内容,标签取并集(同页可属多个主题)
            tags_merged = list(dict.fromkeys(list(old.get("tags") or []) + entry["tags"]))
            old.update({k: v for k, v in entry.items() if k != "id"})
            old["tags"] = tags_merged
            written += 1
            continue
        by_url[p["url"]] = entry
        existing.append(entry)
        written += 1
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, written, skipped


def crawl_web(ctx: ToolContext, url: str, max_pages: int = 10, max_depth: int = 1,
              out_dir: str = "crawled", match: str = "", to_kb: bool = False,
              kb_source: str = "", kb_category: str = "", kb_tags=None,
              delay: float = 1.0) -> str:
    """工具处理器:抓取 url 起的同域页面,落盘并返回摘要。"""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "错误: url 必须是带 http:// 或 https:// 的完整网址。"
    host = parsed.netloc
    max_pages = max(1, min(int(max_pages), 100))
    max_depth = max(0, min(int(max_depth), 5))
    delay = max(0.2, min(float(delay), 10.0))
    try:
        site_dir = resolve_in_workspace(ctx, f"{out_dir.strip('/')}/{host}")
    except PathOutsideWorkspace as e:
        return f"错误: {e}"
    try:
        engine, factory, run_cfg = _engine()
    except Exception as e:                                  # 引擎初始化异常
        return f"错误: 抓取引擎初始化失败({type(e).__name__}: {e})。"

    try:
        pages, failed = asyncio.run(_crawl_site(
            url, max_pages=max_pages, max_depth=max_depth, match=match.strip(),
            delay=delay, site_dir=site_dir, factory=factory, run_cfg=run_cfg))
    except Exception as e:
        hint = ("\n提示: 打包版用内置轻量抓取器(不启动浏览器),需要 JS 渲染的站点"
                "可能失败;可在源码环境(装了 crawl4ai)重试。"
                if engine == "native" else "")
        return f"错误: 抓取过程异常({type(e).__name__}: {e}),未完成。{hint}"

    index = {"site": url, "crawled_at": time.strftime("%Y-%m-%d %H:%M"),
             "pages": [{"url": p["url"], "title": p["title"], "file": p["file"]}
                       for p in pages],
             "failed": failed}
    (site_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"爬取完成: 成功 {len(pages)} 页,失败 {len(failed)} 页。"
             f"输出目录 {out_dir.strip('/')}/{host}/(含 index.json 与每页 .md)。"]
    if engine == "native":
        lines.append("引擎: 内置轻量抓取器(未启用浏览器;需要 JS 渲染的站点抓不到)。"
                     + (f"原因: {_ENGINE_NOTE[:160]}" if _ENGINE_NOTE else ""))
    if to_kb and pages:
        extra = kb_tags if isinstance(kb_tags, (list, tuple)) else \
            re.split(r"[,、\s]+", str(kb_tags or ""))
        kb_path, written, skipped = _write_kb(
            ctx.workspace, host, pages, kb_source.strip(),
            category=kb_category.strip(), extra_tags=extra)
        # 可信度**按实际写入的等级说**(v0.9.54):白名单内是 A,其余域名写入的是
        # B-抓取(未核实) —— 这里原来写死 "credibility=A",等于让回话与磁盘
        # 真相不符,模型会照着告诉同学「已按 A 级可信入库」。
        from minicode.kb import crawl_credibility
        cred = crawl_credibility(host)
        lines.append(f"已写入知识库 kb/{kb_path.name}(新增/更新 {written} 条,"
                     f"credibility={cred}),可用 search_knowledge 检索。")
        if cred != "A":
            lines.append(f"(该域名不在校官网白名单里,入库条目标为 {cred};"
                         "引用时按 kb 的可信度口径说明,不要当官方口径转述。)")
        if skipped:
            lines.append(f"另有 {skipped} 页正文过短(纯导航/列表页),只归档未入库。")
        if not written:
            lines.append("提示: 没有可入库的正文,可能是目标站点需要 JS 渲染(打包版"
                         "抓不到),或该页内容确实过短。")
    elif pages:
        lines.append("提示: 这次没有入库(to_kb=false)。要让用户下次问同类问题直接命中,"
                     "请带 to_kb=true 重爬,并给出 kb_category/kb_tags。")
    shown = pages[:SUMMARY_TITLES_LIMIT]
    lines.append("页面清单:")
    lines += [f"{i}. {p['title']}({p['file']},{len(p['content'])} 字)"
              for i, p in enumerate(shown, start=1)]
    if len(pages) > len(shown):
        lines.append(f"…其余 {len(pages) - len(shown)} 页见 index.json")
    if failed:
        lines.append("失败页面:")
        lines += [f"- {f['url']}: {f['error']}" for f in failed[:5]]
    return "\n".join(lines)


# ------------------------------------------------------- web_search(搜索兜底)

SEARCH_TOPK_LIMIT = 10
SEARCH_SNIPPET = 200
SEARCH_TIMEOUT = 15.0


def _http_get(url: str, timeout: float = SEARCH_TIMEOUT) -> tuple[int, str]:
    """同步 GET(与 NativeCrawler 同一套 httpx,冻结版已内置该依赖)。

    流式读 + 同一份大小上限:搜索结果页正常几十 KB,超限截断即可 —— 搜索
    前几 KB 就有结果,不值得为异常响应把内存吃满。
    """
    import httpx
    with httpx.Client(follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": USER_AGENT,
                               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}) as client:
        with client.stream("GET", url) as resp:
            status = resp.status_code
            if status >= 400:
                return status, ""
            parts: list[str] = []
            total = 0
            for chunk in resp.iter_text():
                total += len(chunk)
                parts.append(chunk)
                if total >= FETCH_MAX_BYTES:
                    break                     # 超限截断:搜索用不着整个响应
            return status, "".join(parts)


def _parse_bing(html: str) -> list[dict]:
    """必应结果页(服务端渲染,轻量抓取器就能拿到):li.b_algo > h2 a + 摘要。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        href = str(a["href"])
        if not href.startswith(("http://", "https://")):
            continue
        snippet_el = li.select_one(".b_caption p") or li.select_one("p")
        out.append({
            "title": a.get_text(" ", strip=True)[:120],
            "url": href,
            "snippet": (snippet_el.get_text(" ", strip=True)[:SEARCH_SNIPPET]
                        if snippet_el else ""),
        })
    return out


def _parse_baidu(html: str) -> list[dict]:
    """百度结果页:div.c-container 的 h3 a(链接是百度跳转链,后面再解析)。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for item in soup.select("div.c-container, div.result"):
        a = item.select_one("h3 a")
        if not a or not a.get("href"):
            continue
        href = str(a["href"])
        snippet_el = (item.select_one("[class*=content-right]")
                      or item.select_one(".c-abstract")
                      or item.select_one("[class*=abstract]"))
        out.append({
            "title": a.get_text(" ", strip=True)[:120],
            "url": href,
            "snippet": (re.sub(r"\s+", " ", snippet_el.get_text(" ", strip=True))[:SEARCH_SNIPPET]
                        if snippet_el else ""),
        })
    return out[:SEARCH_TOPK_LIMIT]


def _resolve_redirect(url: str) -> str:
    """解析百度 /link?url=... 跳转链成真实网址(先 HEAD 跟 Location,
    跟不出来再 GET 一次取最终地址;任何失败都保留原链,不让搜索挂掉)。"""
    try:
        import httpx
        headers = {"User-Agent": USER_AGENT}
        current = url
        with httpx.Client(follow_redirects=False, timeout=10.0,
                          headers=headers) as client:
            for _ in range(3):
                resp = client.head(current)
                location = resp.headers.get("location", "") \
                    if resp.status_code in (301, 302, 303, 307, 308) else ""
                if not location:
                    break
                from urllib.parse import urljoin
                current = urljoin(current, location)
        if "baidu.com/link" not in current:
            return current
        resp = httpx.get(current, follow_redirects=True, headers=headers, timeout=10.0)
        return str(resp.url) or url
    except Exception:
        return url


def web_search(ctx, query: str, top_k: int = 6) -> str:
    """web_search 工具处理器:必应优先、百度兜底,返回带网址的结果列表。"""
    query = (query or "").strip()
    if not query:
        return "错误: 搜索词不能为空。"
    top_k = max(1, min(int(top_k), SEARCH_TOPK_LIMIT))
    from urllib.parse import quote_plus

    errors: list[str] = []
    results: list[dict] = []
    engine = ""
    try:
        status, html = _http_get(f"https://cn.bing.com/search?q={quote_plus(query)}"
                                 f"&count={top_k}&setlang=zh-hans&mkt=zh-CN")
        if status == 200:
            results = _parse_bing(html)[:top_k]
            if results:
                engine = "必应"
        else:
            errors.append(f"必应 HTTP {status}")
    except Exception as e:
        errors.append(f"必应({type(e).__name__}: {e})")

    if not results:
        try:
            status, html = _http_get(f"https://www.baidu.com/s?wd={quote_plus(query)}"
                                     f"&rn={top_k}")
            if status == 200:
                results = _parse_baidu(html)[:top_k]
                if results:
                    engine = "百度"
            else:
                errors.append(f"百度 HTTP {status}")
        except Exception as e:
            errors.append(f"百度({type(e).__name__}: {e})")
        for r in results:
            r["url"] = _resolve_redirect(r["url"])

    if not results:
        detail = (";".join(errors) + "。" if errors else "")
        return ("错误: 两个搜索引擎都没有返回结果。"
                + detail +
                "可以改用 browser_open 打开搜索引擎页面重试,或稍后再试。")
    lines = [f"联网搜索「{query}」(引擎: {engine},共 {len(results)} 条):"]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   网址: {r['url']}")
        if r["snippet"]:
            lines.append(f"   摘要: {r['snippet']}")
    lines.append("注意: 以上来自公开网络,回答时注明来源网址;与校园官方信息"
                 "(知识库/官网)冲突时,以官方信息为准。")
    lines.append("结果的发布日期搜索引擎未必标明 —— 时效类问题(报名时间/放假安排/"
                 "招生计划)先用 campus_notices 或 campus_calendar 查官方,别把一条"
                 "没有日期的搜索结果当「今年的」回答。")
    return "\n".join(lines)
