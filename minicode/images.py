"""图片生成:两条后端 —— 配了 Key 走真文生图,没配 Key 只做本地文字排版图。

口径与 `ocr.py` 完全一致(**能用什么由探测函数自己说,不能用时只报缺什么**):

- **api**:OpenAI 兼容的 `POST /images/generations`。Key **不新开一条路**——
  `capabilities.images` 里只写 `provider_id`,运行时去 `providers` 里借那份
  Key(由 `~/.yinluxs/credentials.json` 管)。`save_config` 落盘前还会把
  capabilities 里手写的 api_key 洗掉,所以 Key 不会跟着分享包走。
  本机能连上真文生图端点吗?这轮**没有 Key 可测**,所以请求体/返回解析都是
  按公开格式写死 + 用假 POST 钉住形状;真端点第一次调用成功与否,以工具回显的
  status 与文件字节数为准,不提前替它背书。
- **card**:Pillow 把文字排成 PNG(日签/通知卡/金句卡)。这是**真出图**,
  但它画的是"字",不是"猫" —— 没配 Key 时想要插图,本模块只会说明缺什么,
  不会拿一张排版图冒充 AI 绘图。

字体:`card` 需要系统里真有中文字体(Windows 一般是 msyh.ttc)。找不到中文字体
时仍会出图,但中文会是方框 —— 这一点写进回显,不藏。
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from urllib.request import urlopen

DEFAULT_IMAGE_PATH = "/images/generations"
DEFAULT_SIZE = "1024x1024"
TIMEOUT_SECONDS = 120.0
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024      # 返回的图超过这个大小就拒绝落盘
MAX_CARD_HEIGHT = 8000                     # 本地卡片画布高度上限(像素)
CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",           # 微软雅黑(Vista 起自带)
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
CONFIG_SHAPE = (
    'config.json 里加:"capabilities": {"images": '
    '[{"provider_id": "你的供应商id", "model": "文生图模型名", '
    '"path": "/images/generations", "size": "1024x1024"}]} '
    "(Key 仍由该供应商已有的凭据机制管,别写在这里)"
)


class ImageError(ValueError):
    """输入本身不成立(缺文字/尺寸非法/未知后端)。"""


# --------------------------------------------------------------------- 后端可用性

def _providers(cfg) -> list:
    return list(getattr(cfg, "providers", None) or [])


def _provider_by_id(cfg, pid: str):
    for p in _providers(cfg):
        if str(getattr(p, "id", "")) == str(pid):
            return p
    return None


def _entries(cfg) -> list[dict]:
    """capabilities.images 可以是单条 dict、也可以是列表(老配置容忍)。"""
    raw = (getattr(cfg, "capabilities", None) or {}).get("images")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def api_targets(cfg) -> list[dict]:
    """把每条配置解析成"能不能用 + 为什么"。绝不在这里发请求。"""
    out = []
    for e in _entries(cfg):
        pid = str(e.get("provider_id") or "").strip()
        model = str(e.get("model") or "").strip()
        prov = _provider_by_id(cfg, pid) if pid else None
        base_url = str(e.get("base_url") or
                       (getattr(prov, "base_url", "") if prov else "")).strip()
        key = str(getattr(prov, "api_key", "") or "").strip() if prov else ""
        why = []
        if not pid:
            why.append("没写 provider_id")
        elif prov is None:
            why.append(f"配置里的 provider_id「{pid}」在供应商列表里找不到")
        if prov is not None and not key:
            why.append("该供应商还没有 Key(去 ~/.yinluxs/credentials.json 那份凭据里配)")
        if not model:
            why.append("没写文生图 model 名")
        if not base_url:
            why.append("没有可用的 base_url")
        out.append({
            "provider_id": pid, "model": model, "base_url": base_url,
            "api_key": key,                        # 只在内存里用,绝不回显
            "path": str(e.get("path") or DEFAULT_IMAGE_PATH),
            "size": str(e.get("size") or DEFAULT_SIZE),
            "ready": not why,
            "why": "；".join(why),
        })
    return out


def local_ready() -> tuple[bool, str]:
    """本地排版出图能不能用:Pillow + 中文字体,两项分开报,缺哪个说哪个。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False, "本机没有 Pillow(内置运行时应该带;缺了只能走文生图接口)"
    if not _cjk_font():
        return True, "Pillow 可用,但没找到中文字体:图里的中文会变成方框"
    return True, "Pillow 可用"


def probe(cfg=None) -> dict:
    """自述:文生图接口有几条能用、本地排版行不行、缺什么。"""
    if cfg is None:
        return {"api": [], "api_ready": False,
                "why": "运行时没把配置传进来(工具不该自己 load_config)",
                "local": local_ready()[1], "how": CONFIG_SHAPE}
    targets = api_targets(cfg)
    return {
        "api": [{k: v for k, v in t.items() if k != "api_key"} for t in targets],
        "api_ready": any(t["ready"] for t in targets),
        "why": (targets[0]["why"] if targets and not any(t["ready"] for t in targets)
                else ("没配置" if not targets else "")),
        "local": local_ready()[1],
        "how": CONFIG_SHAPE,
    }


# --------------------------------------------------------------------- 本地排版出图

_SIZE = re.compile(r"^(\d{3,4})x(\d{3,4})$")


def _parse_size(text: str, default: tuple[int, int]) -> tuple[int, int]:
    """解析 `宽x高`。给了却看不懂时报错,不悄悄用默认值(尺寸是要对用户的)。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return default
    m = _SIZE.match(raw)
    if not m:
        raise ImageError(f"看不懂这个尺寸「{text}」:写成 1024x1024 这样(数字 x 数字)")
    w, h = int(m.group(1)), int(m.group(2))
    if not (128 <= w <= 4096 and 128 <= h <= 4096):
        raise ImageError(f"尺寸 {w}×{h} 超出可接受范围(128–4096)")
    return w, h


def _cjk_font() -> str | None:
    for cand in CJK_FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _wrap(text: str, per_line: int) -> list[str]:
    """按字符数硬换行(中文没有空格可靠)。只折行,不改字。"""
    out = []
    for raw in str(text or "").splitlines() or [""]:
        line = raw.strip()
        if not line:
            out.append("")
            continue
        while len(line) > per_line:
            out.append(line[:per_line])
            line = line[per_line:]
        out.append(line)
    return out


def render_card(text: str, title: str = "", width: int = 1080,
                height: int = 1440, accent: str = "#4C78A8") -> bytes:
    """把文字排成一张竖版卡片 PNG(真像素,不是 SVG、也不是 AI 绘图)。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise ImageError(f"本机没有 Pillow,本地排版出图走不通:{e}") from e
    body = _wrap(text, 22)
    if not [x for x in body if x]:
        raise ImageError("本地排版出图要有文字:content/text 是空的,排不出东西")
    font_path = _cjk_font()
    try:
        big = ImageFont.truetype(font_path, 64) if font_path else ImageFont.load_default()
        mid = ImageFont.truetype(font_path, 44) if font_path else ImageFont.load_default()
        small = (ImageFont.truetype(font_path, 28) if font_path
                 else ImageFont.load_default())
    except OSError as e:                       # 字体在但读不出来(权限/损坏)
        raise ImageError(f"找到字体但读不动:{e}") from e
    img = Image.new("RGB", (width, height), "#FFFFFF")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 14], fill=accent)
    title_lines = _wrap(title, 16) if title else []
    needed = 120 + len(title_lines) * 86 + 30 + len(body) * 66 + 140
    if needed > height:
        # 宁可把画布拉高,也不把字裁掉 —— 但也给个上限,免得一句长文撑出天文数字
        if needed > MAX_CARD_HEIGHT:
            raise ImageError(f"文字太多(需要约 {needed} px 高,上限 {MAX_CARD_HEIGHT} px):"
                             "请减少内容,或分几张卡片生成")
        height = needed
        img = Image.new("RGB", (width, height), "#FFFFFF")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, width, 14], fill=accent)
    y = 120
    for ln in title_lines:
        d.text((90, y), ln, fill="#1A1A1A", font=big)
        y += 86
    if title_lines:
        y += 30
    for ln in body:
        d.text((90, y), ln, fill="#333333", font=mid)
        y += 66
    stamp = time.strftime("%Y-%m-%d %H:%M")
    d.text((90, height - 90), f"引路学长本地排版 · {stamp}", fill="#8A8A8A", font=small)
    if not font_path:
        d.text((90, height - 50), "(未找到中文字体:中文可能显示为方框)",
               fill="#C0392B", font=small)
    d.rectangle([0, height - 14, width, height], fill=accent)
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------- 文生图接口

def _result_bytes(payload: dict, target: dict) -> tuple[bytes, str]:
    """从返回体里取图字节。认 data[0].url / data[0].b64_json,其余如实报错。"""
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ImageError("返回里没有 data 数组(接口格式与本模块假设不一致)")
    item = data[0]
    b64 = item.get("b64_json")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64), "b64_json"
        except Exception as e:
            raise ImageError(f"b64_json 解不出来:{e}") from e
    url = item.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return _download(url), "url"
    raise ImageError(f"返回里既没有 b64_json 也没有可用 url:{list(item)[:5]}")


def _download(url: str) -> bytes:
    try:
        with urlopen(url, timeout=TIMEOUT_SECONDS) as r:   # noqa: S310  # https 白名单已在上面
            data = r.read(MAX_DOWNLOAD_BYTES + 1)
    except Exception as e:
        raise ImageError(f"图片地址下载失败:{type(e).__name__}: {e}") from e
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ImageError(f"图片超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB,拒绝落盘")
    if not data:
        raise ImageError("下载到的文件是 0 字节,不算成功")
    return data


def call_api(prompt: str, target: dict, *, size: str = "",
             timeout: float = TIMEOUT_SECONDS) -> tuple[bytes, dict]:
    """真发一次文生图请求,返回 (图片字节, 元信息)。失败抛 ImageError。"""
    from minicode import model_probe

    payload = {"model": target["model"], "prompt": str(prompt or "").strip(),
               "n": 1, "size": size or target.get("size") or DEFAULT_SIZE}
    if not payload["prompt"]:
        raise ImageError("文生图要有描述文字:prompt 是空的")
    path = target.get("path") or DEFAULT_IMAGE_PATH
    status, text = model_probe._post(target["base_url"], path, payload,
                                     target["api_key"], timeout)
    if status != 200:
        raise ImageError(f"接口返回 {status}:{model_probe.explain_status(status, text)}"
                         f"(请求 {path},模型 {payload['model']})")
    try:
        body = json.loads(text)
    except ValueError:
        raise ImageError(f"接口返回不是 JSON(前 120 字:{text[:120]})") from None
    data, kind = _result_bytes(body if isinstance(body, dict) else {}, target)
    return data, {"model": payload["model"], "size": payload["size"],
                  "path": path, "took": kind, "bytes": len(data)}


# --------------------------------------------------------------------- 工具出口

def _unique(out_dir: Path, stem: str, suffix: str) -> Path:
    p = out_dir / f"{stem}{suffix}"
    i = 2
    while p.exists():                          # 同名不覆盖:上一张不该被这张弄丢
        p = out_dir / f"{stem}-{i}{suffix}"
        i += 1
    return p


def _rel(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def image_generate(ctx, prompt: str = "", text: str = "", title: str = "",
                   backend: str = "auto", size: str = "", name: str = "") -> str:
    """generate_image 工具:出图并落到工作区 images/ 下。

    backend=auto:有可用的文生图接口就走接口;否则只要给了 text 就做本地排版卡片。
    两条都不成立时**只说明缺什么**,不生成任何文件。
    """
    from minicode.charts import slugify

    want = (backend or "auto").strip().lower()
    if want not in ("auto", "api", "card"):
        return f"不认识的后端「{want}」:可选 auto / api(文生图接口) / card(本地排版卡片)。"
    cfg = getattr(ctx, "config", None)
    workspace = Path(getattr(ctx, "workspace", ".") or ".").expanduser().resolve()
    out_dir = workspace / "images"

    try:
        if want in ("auto", "api"):
            targets = [t for t in api_targets(cfg) if t["ready"]]
            if want == "api" and not targets:
                p = probe(cfg)
                return (f"没走成文生图接口:{'没有配好的条目' if not p['api'] else p['why']}\n"
                        f"{CONFIG_SHAPE}")
            if targets:
                t = targets[0]
                started = time.time()
                data, meta = call_api(prompt, t, size=size)
                out_dir.mkdir(parents=True, exist_ok=True)
                path = _unique(out_dir, slugify(name or title or prompt or "image"), ".png")
                path.write_bytes(data)
                elapsed = int((time.time() - started) * 1000)
                return (f"已用文生图接口出图:{_rel(path, workspace)}"
                        f"（{meta['size']}，{meta['bytes']:,} 字节，{elapsed} ms，"
                        f"模型 {meta['model']}，图源 {meta['took']}）\n"
                        f"描述：{str(prompt or '')[:120]}\n"
                        "注意：图由外部接口生成，本模块没有校验它画了什么；"
                        "聊天里可用 ![](" + _rel(path, workspace) + ") 引用。")
        # 走到这里:auto 且没有可用接口，或明确要 card
        if not str(text or "").strip():
            p = probe(cfg)
            return ("这次没有出图:本地排版卡片要有文字(text)，文生图接口又不可用"
                    f"（{p['why'] or '未配置'}）。\n"
                    f"本地状态：{p['local']}\n{CONFIG_SHAPE}\n"
                    "说明：没有接口时我只会把给定文字排成图，不会凭空画插图——"
                    "「画一只猫」这类需求必须配文生图模型才行。")
        w, h = (1080, 1440)
        if size:
            w, h = _parse_size(size, (w, h))
        png = render_card(text, title=title, width=w, height=h)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = _unique(out_dir, slugify(name or title or text[:8] or "card"), ".png")
        path.write_bytes(png)
        font = _cjk_font()
        return (f"已生成文字卡片:{_rel(path, workspace)}（{w}×{h}，"
                f"{len(png):,} 字节，字体 {Path(font).name if font else '未找到中文字体'}）\n"
                f"卡片文字共 {len([x for x in _wrap(text, 22) if x])} 行（含换行折行）\n"
                "注意：这是本地排版渲染，不是 AI 绘图——图里的内容就是你给的这些字。"
                "聊天里可用 ![](" + _rel(path, workspace) + ") 引用。")
    except ImageError as e:
        return f"这次没有出图:{e}"
    except OSError as e:
        return f"图算好了但写不出去:{out_dir}（{type(e).__name__}: {e}）"


# ---------------------------------------------------------------- 回答分享卡片
#
# 同学的分享主场是微信/QQ 群:查到的一条校历、算好的绩点、整理出的知识条目,现在
# 只能"复制一段文字"发出去。这几行把一段回答排成一张能直接发群的竖版卡片,
# 走的是上面同一条 Pillow 路线(真像素、系统里那份中文字体),不引浏览器 canvas
# 那条管线 —— 后者要在 WebView2 里外嵌 SVG/字体,多两处会静默出白图的坑。
#
# 三条口径:
# - **来源 URL 自动带上**:回答里出现过 http(s) 链接就列进卡片尾部。延续的是这个项目
#   "引用可核实"的本能 —— 分享出去的东西自带出处。没有链接就整段不出现,不放一句
#   "来源见聊天"充数。
# - **超长如实截断并说明**:放不下时截到预算,并在末尾留一行"完整回答请回聊天里看",
#   绝不悄悄裁掉尾巴让用户以为发出去的是完整版。
# - **落盘复用 images/ 那一套**:同名不覆盖(`_unique`)、目录由后端定,前端传不进路径。

SHARE_CHAR_BUDGET = 1500      # 卡片正文的字符预算(超了如实截,不静默裁尾)
SHARE_MAX_BYTES = 6_000_000   # 一张卡片的字节上限,防病态输入撑爆磁盘
SHARE_SOURCE_CAP = 6          # 来源最多列几条(再多群里没人点,还会把正文挤没)
_URL_RE = re.compile(r"https?://[^\s）)】>\"'，。;；、]+")


def extract_sources(text: str) -> list[str]:
    """按出现顺序取出回答里的链接并去重。没链接就是空列表 —— 不编来源。"""
    out: list[str] = []
    for url in _URL_RE.findall(str(text or "")):
        if url not in out:
            out.append(url)
    return out


def compose_share_card(text: str, sources: list[str] | None = None,
                       today: str = "") -> tuple[str, bool]:
    """拼出卡片正文,返回 (文字, 是否被截断)。"""
    body = " ".join(str(text or "").split())
    urls = [u for u in (sources if sources is not None else extract_sources(body))
            if u][:SHARE_SOURCE_CAP]
    clipped = len(body) > SHARE_CHAR_BUDGET
    if clipped:
        body = body[:SHARE_CHAR_BUDGET].rstrip() + "…"
    lines = [body, ""]
    if urls:
        lines.append("来源（可点开核实）：")
        lines += [f"· {u}" for u in urls]
        lines.append("")
    lines.append("—— 引路学长 · 广州应用科技学院")
    if today:
        lines.append(today)
    if clipped:
        lines.append("（内容较长，卡片只排到这里；完整回答请回聊天里看）")
    return "\n".join(lines), clipped


def save_share_card(workspace, text: str, title: str = "") -> dict:
    """把一段回答排成分享卡片 PNG,写进工作区 `images/`。

    失败一律回 `{"ok": False, "msg": 真原因}`(没有 Pillow / 中文字体读不动 /
    文字为空 / 超上限 / 写盘报错) —— 界面要能把这句话给用户看,而不是"生成失败"三个字。
    """
    from minicode.charts import slugify

    root = Path(workspace).expanduser().resolve()
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "msg": "这段回答没有文字内容，排不出卡片"}
    block, clipped = compose_share_card(body, today=time.strftime("%Y-%m-%d"))
    try:
        png = render_card(block, title=(title or "校园问答")[:40])
    except ImageError as e:
        return {"ok": False, "msg": str(e)}
    if len(png) > SHARE_MAX_BYTES:
        return {"ok": False,
                "msg": f"卡片有 {len(png):,} 字节，超过 {SHARE_MAX_BYTES:,} 的上限，没写盘"}
    try:
        out_dir = root / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = _unique(out_dir, slugify("share-" + (title or body[:8])), ".png")
        path.write_bytes(png)
    except OSError as e:
        return {"ok": False, "msg": f"写不进 images/：{type(e).__name__}: {e}"}
    return {"ok": True, "path": _rel(path, root), "bytes": len(png),
            "truncated": clipped, "sources": extract_sources(body)[:SHARE_SOURCE_CAP]}
