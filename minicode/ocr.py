"""OCR —— 把图片里的文字取出来。两条后端,哪条真能跑就实说哪条。

为什么要两条:

* ``system`` = Windows 自带的 ``Windows.Media.Ocr`` 引擎。**不联网、不含模型权重**
  (用的是系统语言包),但要一组可选的 ``winrt-*`` Python 绑定。装了才有,
  没装就照实说没装,并给出可照抄的安装命令 —— 不会静默换成"猜字"。
* ``vision`` = 当前配置里的视觉模型。分享包里的同学开箱就能用(内置 Key),
  代价是要联网;而且"这个模型到底能不能看图"只认 :mod:`minicode.model_probe`
  的实测缓存结论,不按模型名瞎猜。

2026-09-24 在本机用一张三行中文题图(29,188 B,Pillow 渲染)真跑过 system 后端,
结果记在这里,免得把这功能说成比实际更强:

* 行数、行序、汉字内容基本对(3 行全认出来);
* 但 ``f(x)`` 认成 ``f 冈``、``x^2`` 认成 ``x ^ 2``、减号 ``−`` 认成汉字 ``一``、
  ``x=1`` 认成 ``x=l``;每个汉字之间还会插一个空格。

所以下面所有出口都带一行"后端 + 告警",OCR 文本只当**待核对的草稿**,
不当已核实的原文。真要准,拿视觉模型那条。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

# 本机实测的最小依赖集:少任何一个都会在半路 ModuleNotFoundError
# (winrt.windows.foundation.collections / winrt.windows.storage.streams 都是运行时才拉)。
SYSTEM_DISTS = (
    "winrt-Windows.Media.Ocr",
    "winrt-Windows.Storage",
    "winrt-Windows.Storage.Streams",
    "winrt-Windows.Graphics.Imaging",
    "winrt-Windows.Globalization",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
)
INSTALL_HINT = "pip install " + " ".join(SYSTEM_DISTS)

SYSTEM_WARN = ("Windows 引擎对公式、上下标、手写不可靠(本机实测:f(x)→f 冈、"
               "x^2→x ^ 2、x=1→x=l)。以下文本是待核对草稿,不是已核实原文。")
VISION_PROMPT = ("逐字转录图片中出现的全部文字,按原有顺序与分行输出。"
                 "看不清或认不出的地方写「(看不清)」,不要按常识补全,不要解题,"
                 "不要翻译,不要额外解释。")
VISION_MAX_TOKENS = 2000
TEXT_LIMIT = 6000


class BackendMissing(RuntimeError):
    """指定后端在这台机器上真用不了。message 里必须是能照做的下一步。"""


# --------------------------------------------------------------- 系统引擎

def _import_system_modules():
    """取 WinRT OCR 需要的投影模块;缺哪个就报哪个(不猜"大概能行")。"""
    try:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    except Exception as e:                      # ImportError / 投影内部的其他坑
        missing = getattr(e, "name", "") or ""
        raise BackendMissing(
            f"本机没有 Windows OCR 的 Python 绑定({type(e).__name__}: {e})"
            + (f";缺的模块是 {missing}" if missing else "")
            + f"。要装就照抄:{INSTALL_HINT}(约 4.3 MB,不含模型权重,"
            "识别数据用系统语言包)") from e
    return OcrEngine, BitmapDecoder, StorageFile, FileAccessMode


def _create_engine(OcrEngine, lang: str | None):
    """造引擎,顺带把"用了哪个语言包"说清楚。

    不 ``new`` Language 对象:系统不认的 tag 会直接抛异常(这条在 PowerShell 侧踩过)。
    只在 ``available_recognizer_languages`` 真列出来的那些里挑。
    """
    available = [str(l.language_tag) for l in OcrEngine.available_recognizer_languages]
    if lang:
        hit = next((l for l in OcrEngine.available_recognizer_languages
                    if str(l.language_tag).lower() == lang.lower()), None)
        if hit is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
            note = (f"系统没装「{lang}」识别包(可选:{', '.join(available) or '无'}),"
                    "已改用用户配置文件语言")
        else:
            engine = OcrEngine.try_create_from_language(hit)
            note = ""
    else:
        engine = OcrEngine.try_create_from_user_profile_languages()
        note = ""
    if engine is None:
        raise BackendMissing(
            f"Windows 没有可用的 OCR 语言引擎(系统列出:{', '.join(available) or '无'})。"
            "到 设置 → 时间和语言 → 语言和区域 里添加带「其他设备体验」的中文/英文语言包")
    used = ""
    try:
        used = str(engine.recognizer_language.language_tag)
    except Exception:
        pass
    return engine, used, note, available


def system_ready() -> bool:
    """这台机器现在能不能走系统引擎(绑定 + 语言包两头都算)。"""
    try:
        OcrEngine, *_ = _import_system_modules()
        return _create_engine(OcrEngine, None)[0] is not None
    except BackendMissing:
        return False


def normalize_system_text(text: str) -> str:
    """收掉 Windows 引擎在相邻汉字之间插的空格。

    只删「两侧都是中日韩文字或全角标点」的那个空格。``f 冈``、``x ^ 2`` 这种
    误识别不能靠删空格掩盖 —— 掩盖了就等于把错字洗成看着对的,更坏。
    """
    cjk = "\\u3000-\\u303f\\u3400-\\u4dbf\\u4e00-\\u9fff\\uff00-\\uffef"
    prev = None
    while prev != text:
        prev = text
        text = re.sub(f"(?<=[{cjk}]) (?=[{cjk}])", "", text)
    return text


def recognize_system(path: Path, lang: str | None = None) -> dict:
    """走 Windows 自带引擎。返回 {ok,text,lines,engine,note,warn,why,elapsed_ms}。"""
    started = time.perf_counter()
    out = {"ok": False, "text": "", "lines": [], "engine": "", "note": "",
           "warn": SYSTEM_WARN, "why": "", "elapsed_ms": 0}
    OcrEngine, BitmapDecoder, StorageFile, FileAccessMode = _import_system_modules()
    engine, used, note, _available = _create_engine(OcrEngine, lang)
    out.update(engine=used, note=note)
    try:
        file = StorageFile.get_file_from_path_async(str(path)).get()
    except Exception as e:
        out["why"] = f"系统引擎读不到这张图({type(e).__name__}: {e})"
        return out
    try:
        with file.open_async(FileAccessMode.READ).get() as stream:
            decoder = BitmapDecoder.create_async(stream).get()
            bitmap = decoder.get_software_bitmap_async().get()
            result = engine.recognize_async(bitmap).get()
            lines = [normalize_system_text(str(l.text)) for l in result.lines]
    except Exception as e:
        out["why"] = (f"系统引擎解图失败({type(e).__name__}: {e})。"
                      "Windows OCR 只吃系统能解码的位图格式(png/jpg/bmp/…)")
        return out
    out.update(ok=True, lines=lines,
               text="\n".join(t for t in lines if t.strip())
               or normalize_system_text(str(result.text or "")))
    # 这一版投影没把 decoder_width 暴露出来(实测 AttributeError):取不到就不写,
    # 不拿 0×0 撑场面。
    w = getattr(decoder, "decoder_width", 0) or getattr(bitmap, "pixel_width", 0)
    h = getattr(decoder, "decoder_height", 0) or getattr(bitmap, "pixel_height", 0)
    if w and h:
        out["image"] = "%dx%d" % (int(w), int(h))
    out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    if not out["text"].strip():
        out["ok"] = False
        out["why"] = (f"系统引擎({used or '未知语言'})跑完了但一个字都没认出来 —— "
                      "可能是图太糊/字太小,或这根本不是文字图")
    return out


# --------------------------------------------------------------- 视觉模型

def _vision_targets(cfg) -> list[dict]:
    """列出真能发图的 (供应商, 模型)。

    判据只用 :func:`minicode.model_probe.cached` 的实测结论:
    ``supports_vision is False`` 的直接排除;没探测过的保留,但标 ``probed=False``,
    让上层把"这次是先试一手"讲明白 —— 不因模型名里有个 "vision" 就当它能看图。
    """
    from minicode import model_probe

    targets: list[dict] = []
    for p in getattr(cfg, "providers", []) or []:
        # has_key 是 property(不是方法),占位符密钥也算没有
        if not getattr(p, "enabled", True) or not getattr(p, "has_key", False):
            continue
        for model in p.enabled_models():
            caps = model_probe.cached(model, p.base_url)
            vision = caps.get("supports_vision") if isinstance(caps, dict) else None
            if vision is False:
                continue
            targets.append({"provider": p.name or p.id, "model": model,
                            "base_url": p.base_url, "api_key": p.api_key,
                            "probed": vision is True})
    active = getattr(cfg, "provider", None)
    if active is not None:
        targets.sort(key=lambda t: (t["model"] != active.model,
                                    t["provider"] != (active.name or active.id),
                                    not t["probed"]))
    else:
        targets.sort(key=lambda t: not t["probed"])
    return targets


def vision_ready(cfg=None) -> dict:
    if cfg is None:
        # 不去 load_config():那是 CLI 入口(读不到配置就 sys.exit),而且会把
        # 构建机的 config.json 当成本机的读。配置一律由调用方(ctx.config)带进来。
        return {"ready": False, "why": "运行时没把配置传进来,视觉模型这条无法判定",
                "model": ""}
    targets = _vision_targets(cfg)
    if not targets:
        return {"ready": False, "why": "没有配好密钥、且未被实测判定「不能看图」的模型",
                "model": ""}
    best = targets[0]
    return {"ready": True, "model": best["model"], "provider": best["provider"],
            "why": "" if best["probed"] else "该模型的视觉能力还没实测过,本次先试一手"}


def recognize_vision(path: Path, cfg=None) -> dict:
    """把图交给视觉模型转录。返回 {ok,text,model,why,elapsed_ms}。"""
    from minicode import attachments, model_probe

    out = {"ok": False, "text": "", "model": "", "why": "", "elapsed_ms": 0,
           "warn": "转录内容来自模型,可能夹带它自己的补全,关键处请按图核对。"}
    if cfg is None:
        raise BackendMissing("运行时没把配置传进来,视觉模型这条走不了"
                             "(工具调用应带 ctx.config)")
    targets = _vision_targets(cfg)
    if not targets:
        raise BackendMissing("没有可用的视觉模型:配一个带密钥、且没被实测判为"
                             "「不支持图片」的模型(设置 → 模型)")
    target = targets[0]
    url = attachments.image_data_url(path)
    if not url:
        out["why"] = f"图片读取或压缩失败,没法发给模型:{path.name}"
        return out
    started = time.perf_counter()
    status, body = model_probe._post(target["base_url"], "/chat/completions", {
        "model": target["model"], "max_tokens": VISION_MAX_TOKENS, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
    }, target["api_key"], 120.0)
    out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    out["model"] = f"{target['provider']}/{target['model']}"
    if status != 200:
        out["why"] = (f"视觉模型这一路没走通:{model_probe.explain_status(status, body)}"
                      + ("" if target["probed"] else "(该模型的视觉能力本就未实测确认)"))
        return out
    text = _chat_text(body)
    if not text.strip():
        out["why"] = "视觉模型返回 200 但正文是空的,本次没有可交付的识别结果"
        return out
    out.update(ok=True, text=text.strip())
    return out


def _chat_text(body: str) -> str:
    try:
        raw = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):        # 部分网关回多段 content parts
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(content or "")


# --------------------------------------------------------------- 对外出口

def ocr_probe(cfg=None) -> dict:
    """两条后端的真实状态,给工具自述和界面用。"""
    try:
        system = {"ready": True, "why": "",
                  "engine": _create_engine(_import_system_modules()[0], None)[1]}
    except BackendMissing as e:
        # 自述出口自己兜一遍安装提示:哪怕上游只回了半句话,"下一步怎么装"也不该丢
        why = str(e)
        if "pip install" not in why:
            why += "。要装就照抄:" + INSTALL_HINT
        system = {"ready": False, "why": why}
    try:
        vision = vision_ready(cfg)
    except Exception as e:                       # 配置读不动也不该让自述崩
        vision = {"ready": False, "why": f"读配置失败:{type(e).__name__}: {e}"}
    return {"system": system, "vision": vision,
            "install_hint": INSTALL_HINT}


def recognize(path: Path, *, backend: str = "auto", lang: str | None = None,
              cfg=None) -> dict:
    """按 backend 跑 OCR,失败原因如实带回。

    ``auto`` 先走系统引擎(不联网、不花额度),它不可用才退到视觉模型;
    两条都试过还没成时,``why`` 里是两条各自的真原因,不是笼统的"识别失败"。
    """
    backend = (backend or "auto").strip().lower()
    if backend not in ("auto", "system", "vision"):
        return {"ok": False, "why": f"不认识的后端「{backend}」,可选 auto/system/vision"}
    # 系统引擎的 StorageFile 只认绝对路径:相对路径进去会以
    # OSError(UNABLE_TO_ML_PATH) 收场,而且报错文本完全看不出是路径的问题(实测踩过)。
    path = Path(str(path)).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    attempts: list[str] = []
    order = {"auto": ("system", "vision"), "system": ("system",),
             "vision": ("vision",)}[backend]
    for name in order:
        try:
            res = (recognize_system(path, lang) if name == "system"
                   else recognize_vision(path, cfg))
        except BackendMissing as e:
            attempts.append(f"[{name}] {e}")
            continue
        except Exception as e:
            attempts.append(f"[{name}] {type(e).__name__}: {e}")
            continue
        if res.get("ok"):
            res["backend"] = name
            if attempts:      # auto 模式下先试过别的后端:把真原因留着,别让它悄悄消失
                res["note"] = ((res.get("note") + " ") if res.get("note") else "") \
                    + "本次还试过:" + " / ".join(attempts)
            return res
        attempts.append(f"[{name}] {res.get('why') or '没认出内容'}")
    return {"ok": False, "text": "", "backend": "", "attempts": attempts,
            "why": ";".join(attempts)}


def ocr_image(ctx, path: str, backend: str = "auto",
              lang: str = "", max_chars: int = TEXT_LIMIT) -> str:
    """工具出口:识别图片文字,并在结果里自述用了哪条后端、有什么坑。

    v0.9.172+:`.pdf` 走**同一条出口**的 PDF 分支(抽页内嵌图 → 逐页识别),
    工具名与参数面一格没变 —— 模型那边只多认识一种"能喂的文件"。
    """
    workspace = Path(getattr(ctx, "workspace", ".") or ".")
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = (workspace / p).resolve()
    if not p.is_file():
        return f"找不到这张图片:{p}(工作区是 {workspace};给个存在的 png/jpg 路径)"
    if p.suffix.lower() == ".pdf":
        return ocr_pdf(ctx, p, backend=backend, lang=lang, max_chars=max_chars)
    res = recognize(p, backend=backend, lang=(lang or None),
                    cfg=getattr(ctx, "config", None))
    if not res.get("ok"):
        return _no_backend_block(ctx, res.get("why") or "两条后端都没走通")
    text = str(res.get("text") or "")
    clipped = len(text) > max_chars
    head = (f"OCR 完成｜后端:{'Windows 自带引擎 ' + res.get('engine','') if res['backend'] == 'system' else '视觉模型 ' + res.get('model','')}"
            f"｜{'离线' if res['backend'] == 'system' else '联网'}"
            f"｜{res.get('elapsed_ms', 0)}ms")
    if res.get("image"):
        head += f"｜图 {res['image']}"
    if res.get("lines"):
        head += f"｜{len(res['lines'])} 行"
    body = text[:max_chars]
    out = [head, f"文件:{p}", "--- 识别文本 ---", body]
    if clipped:
        out.append(f"……(文本 {len(text)} 字,已截到 {max_chars} 字;要全文把 max_chars 调大)")
    out.append("--- 告警 ---")
    out.append(res.get("warn") or "")
    if res.get("note"):
        out.append(f"备注:{res['note']}")
    return "\n".join(x for x in out if x)


# ------------------------------------------------------------ PDF 分支(v0.9.172+)

# 扫描版/图片版 PDF 的每一页通常就是**一张内嵌大图** —— pypdf 能直接抽出来,
# 于是"PDF 页 → 图"这一环不需要任何新依赖(不引 pymupdf/poppler 那类要外部
# 二进制的光栅化器)。抽不出图的页如实点名(矢量图/带文字层那类,请走 pdf 技能
# 直接抽文本),**绝不跳过装看不见**。上限是"别让一份 300 页课件把一轮对话撑爆"。
PDF_PAGE_MAX = 60
PDF_IMAGE_MIN_BYTES = 2048      # 小于这个的多半是页眉装饰/图标,不当正文页图


def _no_backend_block(ctx, why: str) -> str:
    """没有可用 OCR 后端时的同一段实话(图片与 PDF 两条路共用一份文案)。"""
    probe = ocr_probe(getattr(ctx, "config", None))
    return ("这次没有可用的 OCR 后端。\n"
            f"· 系统引擎:{'可用' if probe['system']['ready'] else probe['system']['why']}\n"
            f"· 视觉模型:{'可用(' + probe['vision'].get('model','') + ')' if probe['vision']['ready'] else probe['vision']['why']}\n"
            f"· 本次失败原因:{why}\n"
            "下一步可选:装本机离线组件(不联网)—— " + INSTALL_HINT
            + ";或在「设置 → 模型」里配一个能看图的模型,再用 backend=vision 重试。")


def _pdf_page_images(p: Path, pages_max: int = PDF_PAGE_MAX):
    """抽每页**最大的那张内嵌图**。回 (pages, skipped, total_pages, err)。

    pages   = [{"page": 页号(1 起), "name": 图名, "data": bytes}]
    skipped = 逐页的人话(没有可抽取的图 / 图太小当装饰跳过)
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], [], 0, ("没装 pypdf(内置运行时应该带;缺了就只能装一个,"
                           "或把 PDF 拆成图片再喂给 ocr_image)")
    try:
        reader = PdfReader(str(p))
        total = len(reader.pages)
    except Exception as e:
        return [], [], 0, f"这份 PDF 读不开:{type(e).__name__}: {e}"
    pages, skipped = [], []
    for i, page in enumerate(reader.pages[:pages_max], start=1):
        try:
            imgs = list(page.images)
        except Exception as e:
            skipped.append(f"第 {i} 页的图抽不出来({type(e).__name__})")
            continue
        if not imgs:
            skipped.append(f"第 {i} 页没有可抽取的图(矢量图或带文字层 —— "
                           "那几页请用 pdf 技能直接抽文本)")
            continue
        # 一页多图时取面积/体积最大的那张:扫描版整页图一定比页眉图标大得多
        best = max(imgs, key=lambda im: len(bytes(im.data or b"")))
        data = bytes(best.data or b"")
        if len(data) < PDF_IMAGE_MIN_BYTES:
            skipped.append(f"第 {i} 页只抽到很小的图({len(data)} 字节),"
                           "当装饰跳过了")
            continue
        pages.append({"page": i, "name": str(getattr(best, "name", "") or ""),
                      "data": data})
    return pages, skipped, total, ""


def ocr_pdf(ctx, p: Path, *, backend: str = "auto", lang: str = "",
            max_chars: int = TEXT_LIMIT) -> str:
    """PDF 分支:逐页抽图 → 逐页识别 → 按页锚点拼文本。

    三条与图片那条**同款**的诚实口径:没有后端时说清缺什么;单页识别失败
    只报那一页(不拖垮整份、也不假装读过);整段超 max_chars 截断并说明。
    """
    import tempfile
    pages, skipped, total, err = _pdf_page_images(p)
    if err:
        return err
    if not pages:
        head = (f"这份 PDF 里没有可抽取的页图({total} 页)。\n"
                "多半是**矢量/带文字层的 PDF** —— 那类直接用 pdf 技能抽文本即可,"
                "不需要 OCR。")
        if skipped:
            head += "\n逐页情况:\n· " + "\n· ".join(skipped[:8])
        return head
    cfg = getattr(ctx, "config", None)
    blocks: list[str] = []
    used_backends: set[str] = set()
    failures: list[str] = []
    budget = max(200, int(max_chars))
    for item in pages:
        suffix = Path(item["name"]).suffix or ".png"
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                tmp = Path(f.name)
                f.write(item["data"])
            res = recognize(tmp, backend=backend, lang=(lang or None), cfg=cfg)
        except Exception as e:
            res = {"ok": False, "why": f"{type(e).__name__}: {e}"}
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if not res.get("ok"):
            failures.append(f"第 {item['page']} 页没认出来({res.get('why') or '空结果'})")
            continue
        used_backends.add(str(res.get("backend") or ""))
        text = str(res.get("text") or "").strip()
        if not text:
            failures.append(f"第 {item['page']} 页认出来是空的")
            continue
        blocks.append(f"--- 第 {item['page']} 页 ---\n{text}")
    if not blocks:
        why = ";".join(failures[:4]) or "每一页都没认出内容"
        return _no_backend_block(ctx, why) if not used_backends else \
            "抽到的页图一张都没认出内容:\n· " + "\n· ".join(failures[:8])
    body = "\n\n".join(blocks)
    clipped = len(body) > budget
    out = [f"OCR 完成(PDF 扫描版)｜{len(pages)} 页有图、{len(blocks)} 页认出文字"
           f"｜共 {total} 页｜后端:" + "/".join(sorted(x for x in used_backends if x)),
           f"文件:{p}", "--- 识别文本(按页) ---", body[:budget]]
    if clipped:
        out.append(f"……(文本 {len(body)} 字,已截到 {budget} 字;要更多把 max_chars 调大)")
    if skipped:
        out.append("--- 跳过的页 ---")
        out.append("· " + "\n· ".join(skipped[:12])
                   + (f"\n(另有 {len(skipped) - 12} 条)" if len(skipped) > 12 else ""))
    if failures:
        out.append("--- 没认出来的页 ---")
        out.append("· " + "\n· ".join(failures[:12]))
    out.append("--- 告警 ---")
    out.append(SYSTEM_WARN if "system" in used_backends else
               "视觉模型可能夹带补全:取到的文本只当待核对草稿。")
    return "\n".join(x for x in out if x)
