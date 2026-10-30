"""从网页抓一张图到工作区(计划书阶段 4,v0.9.189)。

为什么值得有:学长已经能看图(`view_image`)、能取字(`ocr_image`),但那张图
只能来自用户自己上传或本机文件。网页里的图(校园卡样图、通知里的表格截图、
课件封面)以前只能让模型现写 `py_script` 去 requests —— 而那是用户机器上一次
真实的网络请求,没有任何边界。

八条边界(计划书点名的,每条都有测试):

1. **只认 http/https**;`file:`/`data:`/其它协议一律拒(不能拿它读本机)。
2. **SSRF 硬闸,而且逐跳验**:回环、私网(10/172.16-31/192.168)、链路本地
   (169.254/fe80,含云元数据 169.254.169.254)、IPv6 回环与 ULA 一律拒;
   **主机名先解析再看解析结果**(防"域名指向私网");重定向的每一跳都重新验一遍 ——
   只验起始 URL 的写法会被"公网地址 302 到 127.0.0.1"绕过。
3. **重定向 ≤ 5 跳**,超了如实说停了。
4. **大小上限**(Content-Length 与流式读双闸,默认 10 MB),超了**不落半截**。
5. **按文件头认图**(PNG/JPEG/GIF/WEBP/BMP);HTML、SVG 与未知一律拒 ——
   后缀和 Content-Type 都不信(SVG 是 XML,能带脚本,不在"图片"这一格里)。
6. **落到工作区 `uploads/`**,旁边写一份 `<文件名>.source.json`(来源 URL、最终 URL、
   抓取时刻、sha256、字节数、真实 MIME),历史里据此能说清"这张图从哪来的"。
7. **不自动把图发给模型** —— 抓回来只是"落在工作区",要不要看/取字由下一次
   调用决定(与"附件不自动上传"同一条口径)。
8. **出站策略**:`netguard.decide` 拒的网址**根本不发请求**;`ask` 档由权限门
   在调用前问(本模块不另开提问通道)。

**诚实边界(写在这里,别当已做到)**:①走系统代理时,实际解析与连接可能发生在
代理那一侧 —— 这条闸拦的是 URL 里的主机名与它在本机解析到的地址,拦不住代理
内部的路由;②解析与连接之间理论上仍可被 DNS 重绑定绕过(不固定 IP 连接)。
它防的是"模型随手填一个内网地址",不是一条网络边界。
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_MB_LIMIT = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_ALLOWED_SCHEMES = ("http", "https")
# 这些主机名先点名拒掉(有些机器上它们会被 hosts 指到私网,也可能压根解析不出来)
_BAD_HOSTS = {"localhost", "metadata", "metadata.google.internal",
              "instance-data", "instance-data.ec2.internal"}


class FetchError(ValueError):
    """抓取没成。message 要是**给人看的一句话**,点名是哪一条边界拦下的。"""


def _blocked_ip(text: str) -> str:
    """这个 IP 是不是"内网/本机/元数据"那一类;是就回原因,不是回空串。"""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if ip.is_loopback:
        return "回环地址"
    if ip.is_link_local:
        return "链路本地地址(云元数据 169.254.169.254 就在这一类)"
    if ip.is_private:
        return "私网地址"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "保留/组播/未指定地址"
    return ""


def host_blocked(host: str) -> str:
    """主机名该不该拒:先看名字,再**解析**看它指向哪(防域名指向私网)。

    解析失败回一句"解析不了" —— 那与"拒绝"是两种不同的说法,但对"要不要发请求"
    是同一个答案(解析不了也发不出去)。
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return "网址里没有主机名"
    if host in _BAD_HOSTS or host.endswith(".localhost"):
        return f"本机/元数据主机名({host})"
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return f"解析不了主机名({host})"
    for info in infos:
        addr = str(info[4][0])
        why = _blocked_ip(addr)
        if why:
            return f"主机名解析到{why}({host} → {addr})"
    return ""


def check_url(url: str) -> tuple[bool, str]:
    """URL 层面的闸:协议、账号密码、主机。回 (能不能发, 不能的原因)。"""
    text = str(url or "").strip()
    if not text:
        return False, "没有给网址"
    parsed = urlparse(text)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, (f"只认 http/https 的网址(拿到的是「{parsed.scheme or '没有协议'}」)"
                       "—— file:/data: 这类不能当网页图抓")
    if parsed.username or parsed.password:
        return False, "网址里带了账号密码,不接(凭据不该出现在这种参数里)"
    why = host_blocked(parsed.hostname or "")
    if why:
        return False, why
    return True, ""


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """按**文件头**认图,回 (扩展名, MIME);认不出回 None。

    SVG 刻意不在列:它是 XML,能带脚本,而这一格的语义是"抓一张位图",
    不是"把网页内容搬进工作区"。
    """
    head = bytes(data or b"")[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if head.startswith(b"BM"):
        return ".bmp", "image/bmp"
    return None


def _safe_stem(host: str) -> str:
    out = "".join(ch if (ch.isalnum() or ch in "-_.") else "-"
                  for ch in (host or "image").lower())
    return (out.strip("-.") or "image")[:40]


def _request(client, url: str, timeout: float):
    """发一次 GET(不自动跟跳转);返回响应上下文管理器。"""
    return client.stream("GET", url, timeout=timeout,
                         headers={"User-Agent": _UA,
                                  "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})


def fetch(workspace: Path, url: str, *, config=None, client=None,
          max_bytes: int = MAX_BYTES, timeout: float = 20.0,
          want_name: str = "") -> dict:
    """抓图并落盘。成功回 {path, name, size, sha256, url, final_url, mime}。

    `client` 只给测试注入(httpx.MockTransport);生产路径自己建。
    任何一条边界拦下都抛 FetchError,消息**指名道姓**。
    """
    from minicode import netguard

    cur = str(url or "").strip()
    hops = 0
    own_client = client is None
    if own_client:
        import httpx
        client = httpx.Client(follow_redirects=False, trust_env=True)
    try:
        while True:
            ok, why = check_url(cur)
            if not ok:
                raise FetchError(why)
            if config is not None:
                verdict, host = netguard.decide(config, cur)
                if verdict == netguard.REJECT:
                    raise FetchError(
                        f"出站策略拒了这个网址({host or '看不出主机名'});"
                        "这次一个请求都没有发出去")
            with _request(client, cur, timeout) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location") or ""
                    if not loc:
                        raise FetchError(f"服务器回了 {resp.status_code} 却没给新地址")
                    hops += 1
                    if hops > MAX_REDIRECTS:
                        raise FetchError(f"重定向超过 {MAX_REDIRECTS} 跳,停了"
                                         "(这种链要么坏了,要么在绕边界的闸)")
                    cur = urljoin(cur, loc)
                    continue                      # 下一跳重新过 check_url + 策略
                if resp.status_code != 200:
                    raise FetchError(f"服务器回了 HTTP {resp.status_code}")
                ctype = str(resp.headers.get("content-type") or "").lower()
                if ctype.startswith("text/") or "html" in ctype or "xml" in ctype:
                    raise FetchError(f"这个地址给的是网页/文本({ctype}),不是图片")
                declared = int(resp.headers.get("content-length") or 0)
                if declared and declared > max_bytes:
                    raise FetchError(f"这张图太大({declared / 1e6:.1f} MB,"
                                     f"上限 {max_bytes / 1e6:.0f} MB)")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise FetchError(f"这张图超过上限 {max_bytes / 1e6:.0f} MB,"
                                         "已经停下,不会落半截")
                data = bytes(buf)
                final_url = cur
                break
    finally:
        if own_client:
            client.close()

    sniffed = sniff_image(data)
    if sniffed is None:
        raise FetchError("抓回来的东西文件头认不出是图片(可能是网页、SVG 或别的东西)")
    ext, mime = sniffed
    import hashlib
    from minicode import attachments
    sha = hashlib.sha256(data).hexdigest()
    stem = _safe_stem(Path(want_name).stem if want_name else
                      (urlparse(final_url).hostname or "image"))
    att = attachments.save_dropped_bytes(workspace, stem + ext, data)
    # 来源小票:与图放一起,历史里"这张图从哪来的"能查(不是新账本,是这份附件的注)
    side = Path(att.local).with_name(Path(att.local).name + ".source.json")
    side.write_text(json.dumps({
        "url": str(url), "final_url": final_url,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha, "bytes": len(data), "mime": mime,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"path": att.path, "name": Path(att.path).name, "size": len(data),
            "sha256": sha, "url": str(url), "final_url": final_url,
            "mime": mime, "sidecar": side.name}


def fetch_image(ctx, url: str, name: str = "", max_mb: int = 10) -> str:
    """工具出口:抓一张网页图到工作区,回一句能照着做的话。"""
    from minicode.attachments import human_size
    ws = Path(getattr(ctx, "workspace", ".") or ".").expanduser().resolve()
    mb = max(1, min(int(max_mb or 10), MAX_MB_LIMIT))
    try:
        got = fetch(ws, url, config=getattr(ctx, "config", None),
                    max_bytes=mb * 1024 * 1024, want_name=str(name or ""))
    except FetchError as e:
        return f"没抓到:{e}"
    except Exception as e:  # 网络栈深处的意外也要落到人话
        return f"没抓到:{type(e).__name__}: {e}"
    return (
        f"已抓取图片:{got['name']}({human_size(got['size'])},真身是 {got['mime']})\n"
        f"来源:{got['url']}\n"
        f"sha256:{got['sha256'][:16]}…(来源小票:{got['sidecar']})\n"
        f"已保存在工作区 uploads/,**没有发给我看** —— 要看内容用 view_image(把 "
        f"{got['path']} 发过来),要取字用 ocr_image。")
