"""向量档:给知识库检索补一层语义召回,后端可换、可缺,缺了就如实退回关键词。

三档后端,按"能不能真的跑"排:

1. ``api``  —— 走 OpenAI 兼容的 ``/v1/embeddings`` 端点。密钥**不新开路径**:
   ``capabilities.embeddings.provider_id`` 指向 config.json 里已有的供应商,
   运行时借它那把 Key(密钥由 credentials 机制单独管,绝不进分享包)。
2. ``local`` —— 本地 ONNX 小模型。**池化方式必须跟模型自己的口径一致**:
   BGE v1.5 系(BAAI 官方与 ``启用说明`` 都是 CLS),mean 是给 mean-pooled
   系模型留的旁路,``capabilities.embeddings.pooling`` 自己点。取错池化不会
   报错,只会让所有向量落在一个谁也没验证过的空间里 —— 所以这一项**写进
   检索口径给用户看**,不藏在代码里。
   权重**不进包**(实测 bge-small-zh-v1.5 的 ONNX 94,835,369 B),源码运行用
   ``scripts/fetch_embed_model.py --repo <组织>/<仓库>`` 下到数据目录;装好的
   包里没有 scripts/,那一路是把两个文件直接放进 ``<数据目录>/models/<模型名>/``。
3. ``none``  —— 上面两档都不可用。此时**上层必须写"本次只有关键词检索"**,
   把 BM25 说成语义检索就是装饰,本项目不允许。

向量索引落在数据目录,跟着使用者走、不进仓库;缓存以条目正文的 sha1 为键,
所以库里改了哪几条就只重算哪几条 —— 868 条这种量级,增量比全量重要。
缓存文件名还带着**会改变向量本身的那几个口径**(local 档:池化与截断宽度),
换了口径就是一套新缓存,绝不拿旧向量当真值。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

CACHE_VERSION = 1
BATCH_LIMIT = 32            # 一次请求送多少条:端点普遍有 input 数量上限,取保守值
TEXT_LIMIT = 1500           # 每条参与 embedding 的正文字数上限
API_TIMEOUT = 60.0          # embedding 请求单独给短超时:它不该拖住一次检索

# 池化:模型自己的口径,取错了不报错、只是全体向量搬家。BGE v1.5 = CLS。
POOLINGS = ("cls", "mean")
DEFAULT_POOLING = "cls"
# 512 = bge-small-zh-v1.5 config.json 里的 max_position_embeddings(2026-10-06 实读)。
# 原先默认 256 是把长条目后半截悄悄丢了 —— 建索引时没人告诉你丢了。
DEFAULT_MAX_SEQ_LEN = 512


class EmbedUnavailable(RuntimeError):
    """向量档不可用。message 就是给用户看的原因,不要吞掉。"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def entry_text(title: str, content: str) -> str:
    """送进 embedding 的那串字:标题 + 正文截断。

    单独成一个函数,是为了让"缓存键"和"实际送出去的文本"永远同源 ——
    两边算得不一样时,缓存会悄悄失效(键算的是 A,发给模型的是 B)。
    """
    return _norm(f"{title}\n{(content or '')[:TEXT_LIMIT]}")


def content_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def entry_key(entry) -> str:
    return f"{getattr(entry, 'source_file', '?')}#{getattr(entry, 'id', '?')}"


# --------------------------------------------------------------- 后端探测

def embeddings_spec(cfg) -> dict:
    """config.json 里 capabilities.embeddings 那一段(没有就空 dict)。

    只有一份取法:探测、取向量、kb 的 min_sim 都从这里读,免得两处各认一种写法。
    """
    caps = getattr(cfg, "capabilities", None) or {}
    spec = caps.get("embeddings") if isinstance(caps, dict) else None
    return spec if isinstance(spec, dict) else {}


def _download_hint() -> str:
    """缺权重时那句「怎么办」。装好的包里没有 scripts/,指那条路在包里是死路。"""
    if getattr(sys, "frozen", False):
        return ("把 model.onnx 与 vocab.txt 放进上面那个目录(文件名要一模一样,"
                "权重不进安装包,装好的包里也得自己放这两个)")
    return ("跑 scripts/fetch_embed_model.py --repo <组织>/<仓库> 下载,"
            "或手工把 model.onnx 与 vocab.txt 放进上面那个目录")


def detect(cfg=None) -> dict:
    """按 config.json 探测本次可用的后端;真正的判定在 _detect(那里才认 spec)。"""
    return _detect(embeddings_spec(cfg), cfg)


def _detect(spec: dict, cfg=None) -> dict:
    """返回本次可用后端:{backend, model, dim, ready, why, source, pooling, ...}。

    这个 dict 会被上层原样写进检索结果里,所以 why 必须是**人能照做的下一步**
    (缺什么、去哪儿配),而不是"初始化失败"。
    """
    out = {"backend": "none", "model": "", "dim": 0, "ready": False, "why": "",
           "source": "", "pooling": "", "max_seq_len": 0, "query_prefix": ""}
    if not spec:
        out["why"] = ("未配置向量后端:在 config.json 的 capabilities.embeddings 里写 "
                      '{"backend":"api","provider_id":"<已有供应商>","model":"<embedding 模型名"},'
                      '或 "backend":"local" 并把 model.onnx + vocab.txt 放进 '
                      "<数据目录>/models/<模型名>/(源码运行也可以跑 fetch_embed_model.py "
                      "下载权重,仓库名要自己给,脚本不替你猜)")
        return out
    want = str(spec.get("backend") or "api").strip().lower()
    out["dim"] = _as_int(spec.get("dim"), 0)
    out["model"] = str(spec.get("model") or "").strip()
    out["query_prefix"] = str(spec.get("query_prefix") or "")
    pooling = str(spec.get("pooling") or DEFAULT_POOLING).strip().lower()
    if pooling not in POOLINGS:
        out["why"] = (f'capabilities.embeddings.pooling 写的是 "{pooling}",程序不认:'
                      f"只能 {' 或 '.join(POOLINGS)}(BGE v1.5 这类中文模型用 cls,"
                      "mean-pooled 那一系才用 mean)")
        return out
    out["pooling"] = pooling
    out["max_seq_len"] = _as_int(spec.get("max_seq_len"), DEFAULT_MAX_SEQ_LEN)
    if want == "local":
        model_dir = local_model_dir(spec)
        missing = [n for n in ("model.onnx", "vocab.txt") if not (model_dir / n).is_file()]
        if missing:
            out["why"] = (f"本地模型缺文件 {', '.join(missing)}(期望目录:{model_dir})——"
                          + _download_hint())
            return out
        if not _onnx_available():
            out["why"] = (f"权重在 {model_dir},但本机没有 onnxruntime 这个包:"
                          "源码运行 `pip install onnxruntime`,装好的包里则要说一声"
                          "——这一版构建已把它打进包(_internal 里带 onnxruntime),"
                          "老包里没有,得换新包")
            return out
        out.update(backend="local", ready=True, source=str(model_dir))
        return out
    # api 档
    if not out["model"]:
        out["why"] = 'api 后端要 capabilities.embeddings.model 给出 embedding 模型名(不猜)'
        return out
    resolved = _api_endpoint(cfg, spec)
    if not resolved:
        out["why"] = ("api 后端没找到可用的供应商:provider_id 指向的供应商不存在或没有密钥"
                      f"(当前 provider_id={spec.get('provider_id') or '未填'})")
        return out
    out.update(backend="api", ready=True, source=resolved[0])
    return out


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _api_endpoint(cfg, spec: dict):
    """按 provider_id 借已有供应商的 base_url + key;借不到返回 None。

    刻意不读 capabilities 里的 api_key:密钥只有一条路(credentials / 内置注入),
    多开一条就多一处会跟着便携包外发的地方。
    """
    providers = getattr(cfg, "providers", None) or []
    pid = str(spec.get("provider_id") or "").strip()
    provider = None
    for p in providers:
        if pid and p.id == pid:
            provider = p
            break
    if provider is None and not pid:
        provider = getattr(cfg, "provider", None)
    if provider is None or not getattr(provider, "has_key", False):
        return None
    base = str(getattr(provider, "base_url", "") or "").strip()
    if not base:
        return None
    return (base, str(getattr(provider, "api_key", "") or ""), provider.id)


def local_model_dir(spec: dict) -> Path:
    return models_dir() / str(spec.get("model") or "bge-small-zh")


def models_dir() -> Path:
    from minicode.paths import app_path
    d = app_path("models")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _onnx_available() -> bool:
    """只回答"这台机器上有没有 onnxruntime 这个包",**不去真 import**。

    为什么不真 import:2026-10-06 在构建机(Anaconda 基底的 venv)实测,
    `import onnxruntime` 在 pyd 的 PyInit 里直接 access violation —— 整个进程没,
    而 access violation 是 Python 抓不住的(不是异常,没有 try/except 的位置)。
    探针要是敢 import,用户配错一次环境就把学长整个带走。
    真正的加载失败由 `_session_for()` 兜:能抓到的 ImportError/DLL 问题在那里
    变成一句 EmbedUnavailable;抓不到的那类属于机器环境问题,只能修环境。
    """
    import importlib.util
    try:
        return importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):        # 父包本身坏了
        return False


# --------------------------------------------------------------- 索引缓存

def _profile_tag(backend: str, pooling: str, max_seq_len: int) -> str:
    """只有真的会改变向量数值的口径才进缓存文件名。

    api 档的向量由端点算,pooling / max_seq_len 那两个本地旋钮动它不着 —— 把它们
    写进 api 的缓存名,等于用户改一个不起作用的数字就要重刷几百条 embedding,
    那是他的额度与等待。local 档正相反:那两个数**就是**向量的定义,换口径必须
    换文件,否则旧的 mean 向量会被当成 CLS 空间里的真值用(不报错,只是全错)。
    """
    if backend != "local":
        return ""
    return f"-{pooling or DEFAULT_POOLING}-{max_seq_len or DEFAULT_MAX_SEQ_LEN}"


def cache_path(backend: str, model: str, pooling: str = "",
               max_seq_len: int = 0) -> Path:
    from minicode.paths import app_path
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "-",
                 f"{backend}-{model or 'default'}{_profile_tag(backend, pooling, max_seq_len)}")
    return app_path("kb-vectors") / f"{tag}.json"


def load_cache(backend: str, model: str, pooling: str = "",
               max_seq_len: int = 0) -> dict:
    p = cache_path(backend, model, pooling, max_seq_len)
    blank = {"version": CACHE_VERSION, "backend": backend, "model": model,
             "pooling": pooling, "max_seq_len": max_seq_len, "items": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return blank
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        return blank
    if not isinstance(raw.get("items"), dict):
        raw["items"] = {}
    raw.setdefault("pooling", pooling)
    raw.setdefault("max_seq_len", max_seq_len)
    return raw


def save_cache(cache: dict) -> None:
    p = cache_path(str(cache.get("backend") or ""), str(cache.get("model") or ""),
                   str(cache.get("pooling") or ""), _as_int(cache.get("max_seq_len"), 0))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)          # 原子替换:半截的向量缓存比没有缓存更糟(会把旧维度当真)


# --------------------------------------------------------------- 取向量

def embed_texts(texts, cfg=None, spec: dict | None = None, session=None) -> list[list[float]]:
    """真的去算向量;不可用时抛 EmbedUnavailable(带上 detect 的 why)。"""
    payload = [_norm(t)[:TEXT_LIMIT] for t in texts]
    if not payload:
        return []
    # spec 只解析一次:detect 用哪份、_api_endpoint 借哪家的 Key,必须是同一份,
    # 否则会出现"探测时核的是 A 供应商,真发请求时借的却是 B"。
    spec = spec if spec else embeddings_spec(cfg)
    info = _detect(spec, cfg)
    if not info["ready"]:
        raise EmbedUnavailable(info["why"])
    if info["backend"] == "api":
        vecs = _embed_api(payload, info, cfg, spec)
    elif info["backend"] == "local":
        vecs = _embed_local(payload, info, spec, session=session)
    else:
        raise EmbedUnavailable(info["why"] or "没有可用的向量后端")
    if len(vecs) != len(payload):
        raise EmbedUnavailable(f"后端返回 {len(vecs)} 条向量,和送出去的 {len(payload)} 条不符")
    return vecs


def query_vector(query: str, cfg=None) -> list[float]:
    """查询侧向量。检索指令**只**加在查询上,文档侧永远不带(非对称检索的用法)。

    默认不带,要带就在 config 里明确写 `query_prefix`。理由不是风格:
    2026-10-06 拿组长那份 model.onnx 在 200 条真馆藏上实测(查询=条目标题),
    加上「为这个句子生成表示以用于检索相关文章：」把自召回 Top-1 从
    200/200 打到 130/200,第一二名相似度差从 0.1186 缩到 0.0233 —— 这句官方
    建议在这个模型、这套语料上是**负收益**的,所以程序不替他加,数字摆在这里。
    """
    spec = embeddings_spec(cfg)
    prefix = str(spec.get("query_prefix") or "")
    text = f"{prefix}{query}" if prefix else (query or "")
    vecs = embed_texts([text], cfg, spec=spec)
    return vecs[0] if vecs else []


def _embed_api(payload, info, cfg, spec) -> list[list[float]]:
    base, key, _pid = _api_endpoint(cfg, spec)
    model = info["model"]
    try:
        from openai import OpenAI
    except Exception as e:                       # 打包版缺 SDK 时别把检索一起拖崩
        raise EmbedUnavailable(f"import openai 失败:{e}") from e
    out: list[list[float]] = []
    client = OpenAI(base_url=base, api_key=key or "EMPTY", timeout=API_TIMEOUT, max_retries=0)
    for i in range(0, len(payload), BATCH_LIMIT):
        chunk = payload[i:i + BATCH_LIMIT]
        try:
            resp = client.embeddings.create(model=model, input=chunk)
        except Exception as e:
            # 只带异常类型与端点,绝不把 key 拼进消息里(它可能出现在日志/界面上)
            raise EmbedUnavailable(f"调用 {base} 的 embeddings 失败:{type(e).__name__}: {e}"[:300]) from e
        data = getattr(resp, "data", None) or []
        if len(data) != len(chunk):
            raise EmbedUnavailable(f"端点返回 {len(data)} 条,请求 {len(chunk)} 条")
        out.extend([list(item.embedding) for item in data])
    return out


def _embed_local(payload, info, spec, session=None) -> list[list[float]]:
    model_dir = local_model_dir(spec)
    try:
        import numpy as np                        # 只在真的走本地档时才要
    except Exception as e:
        raise EmbedUnavailable(f"本地档需要 numpy:{e}") from e
    sess = session or _session_for(model_dir / "model.onnx")
    max_len = _as_int(info.get("max_seq_len"), DEFAULT_MAX_SEQ_LEN)
    tok = _tokenizer_for(model_dir / "vocab.txt", max_len)
    names = [inp.name for inp in sess.get_inputs()]
    batch = tok.batch(payload)
    feeds = {}
    if "input_ids" in names:
        feeds["input_ids"] = np.asarray(batch["input_ids"], dtype=np.int64)
    if "attention_mask" in names:
        feeds["attention_mask"] = np.asarray(batch["attention_mask"], dtype=np.int64)
    if "token_type_ids" in names:
        feeds["token_type_ids"] = np.asarray(batch["token_type_ids"], dtype=np.int64)
    out = sess.run(None, feeds)[0]               # (batch, seq, dim)
    if info.get("pooling") == "mean":
        mask = np.asarray(batch["attention_mask"], dtype=out.dtype)
        vecs = (out * mask[..., None]).sum(1) / np.clip(mask.sum(1)[..., None], 1e-9, None)
    else:                                        # cls:BGE v1.5 官方取法
        vecs = out[:, 0, :]
    vecs = _normalize(vecs.tolist())
    if info.get("dim") and vecs and len(vecs[0]) != info["dim"]:
        raise EmbedUnavailable(
            f"这个模型出来的是 {len(vecs[0])} 维,config 里 capabilities.embeddings.dim "
            f"写的却是 {info['dim']} —— 两个数必有一个不对,先核对权重与配置再说")
    return vecs


# 会话与分词器都按"它依赖的那个数"缓存:权重换个文件(mtime 变)绝不能继续用旧的。
_SESSIONS: dict[tuple[str, int], object] = {}
_TOKENS: dict[tuple[str, int, int], "WordPiece"] = {}
_RUNTIME_KEEP = 2     # 同时留着两份运行时,说明使用者在换模型 —— 旧的那份没用了


def _runtime_key(path: Path) -> tuple[str, int]:
    try:
        return (str(path.resolve()), path.stat().st_mtime_ns)
    except OSError:
        return (str(path), 0)


def _session_for(path: Path):
    """建一次 InferenceSession 远贵于推理一次,所以留着复用。

    实测(2026-10-06,同一份权重、本机 CPU):冷建会话 **224ms**,热会话单次查询
    **2.4ms** —— 差两个数量级。原先每次语义检索都新建会话,用户感觉到的"慢"
    全都花在这里,不是模型。
    """
    key = _runtime_key(path)
    sess = _SESSIONS.get(key)
    if sess is None:
        try:
            sess = _new_session(path)
        except Exception as e:
            raise EmbedUnavailable(
                f"打不开 {path.name}:{type(e).__name__}: {str(e)[:180]}"
                "(权重文件是不是半截的、或者缺同名的外部数据文件?)") from e
        if len(_SESSIONS) >= _RUNTIME_KEEP:
            _SESSIONS.clear()
            _TOKENS.clear()
        _SESSIONS[key] = sess
    return sess


def _tokenizer_for(path: Path, max_len: int) -> "WordPiece":
    key = _runtime_key(path) + (max_len,)
    tok = _TOKENS.get(key)
    if tok is None:
        tok = WordPiece(path, max_len=max_len)
        if len(_TOKENS) >= _RUNTIME_KEEP:
            _TOKENS.clear()
        _TOKENS[key] = tok
    return tok


def _new_session(path):
    import onnxruntime as ort
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _normalize(vecs: list[list[float]]) -> list[list[float]]:
    out = []
    for v in vecs:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([round(x / n, 6) for x in v])
    return out


def ensure_vectors(entries, cfg=None, build: bool = False, progress=None) -> dict:
    """给一批条目取向量。

    ``build=False``(默认,检索路径上用它):只读已落盘的缓存,缺几条就报几条,
    **绝不因为"顺手"去发几百条请求** —— 首次建索引是几百次 embedding 调用的事,
    必须让使用者明确点一次头。``build=True`` 才真去补算缺的部分(增量:库里
    改了哪几条就只重算哪几条,868 条这种量级,增量比全量重要)。

    返回 {"ready","backend","model","why","vectors","embedded","cached","missing",
    "total","elapsed_ms"} —— 上层拿这些字段如实描述这次检索,**包括失败那次的失败原因**。
    """
    t0 = time.time()
    info = detect(cfg)
    report = {"ready": info["ready"], "backend": info["backend"], "model": info["model"],
              "why": info["why"], "vectors": [], "embedded": 0, "cached": 0,
              "missing": 0, "total": len(entries), "elapsed_ms": 0,
              "pooling": info["pooling"], "max_seq_len": info["max_seq_len"],
              "query_prefixed": bool(info["query_prefix"])}
    if not entries:
        report["elapsed_ms"] = int((time.time() - t0) * 1000)
        return report
    if not info["ready"]:
        # 后端都没配,就别去读缓存、别算几百个 sha1 —— 直接如实报告缺多少条。
        report["missing"] = len(entries)
        report["elapsed_ms"] = int((time.time() - t0) * 1000)
        return report
    cache = load_cache(info["backend"], info["model"], info["pooling"], info["max_seq_len"])
    items = cache["items"]
    todo: list[tuple[int, str, str]] = []
    vectors: list[list[float] | None] = [None] * len(entries)
    for i, e in enumerate(entries):
        text = entry_text(getattr(e, "title", ""), getattr(e, "content", ""))
        key = entry_key(e)
        sha = content_sha1(text)
        rec = items.get(key)
        if isinstance(rec, dict) and rec.get("sha") == sha and isinstance(rec.get("vec"), list):
            vectors[i] = rec["vec"]
            report["cached"] += 1
        else:
            todo.append((i, key, text))
    report["missing"] = len(todo)
    if todo and build:
        try:
            fresh = embed_texts([t for _, _, t in todo], cfg)
        except EmbedUnavailable as e:
            report.update(ready=False, backend=info["backend"], why=str(e),
                          elapsed_ms=int((time.time() - t0) * 1000))
            return report
        for (i, key, _t), vec in zip(todo, fresh):
            vectors[i] = [round(float(x), 6) for x in vec]
            items[key] = {"sha": content_sha1(_t), "vec": vectors[i]}
            report["embedded"] += 1
        report["missing"] = 0
        if report["embedded"]:
            cache["dim"] = len(vectors[todo[0][0]] or [])
            save_cache(cache)
    report["vectors"] = [v or [] for v in vectors]
    report["dim"] = len(report["vectors"][0]) if report["vectors"] and report["vectors"][0] else 0
    report["elapsed_ms"] = int((time.time() - t0) * 1000)
    if progress:
        progress(report)
    return report


# ------------------------------------------------- 纯 Python WordPiece 分词
#
# 中文章 BERT 系词表的通用形态:[CLS] [SEP] [PAD] [UNK] + 汉字(每字一个 token)
# + 拉丁词与 ## 后缀。这里只实现这套通用规则,不针对某个具体模型调参。

class WordPiece:
    def __init__(self, vocab_path: Path, max_len: int = 256):
        self.max_len = max(16, int(max_len))
        self.vocab: dict[str, int] = {}
        try:
            lines = Path(vocab_path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for i, line in enumerate(lines):
            tok = line.strip()
            if tok:
                self.vocab.setdefault(tok, i)
        self.unk = self.vocab.get("[UNK]", 0)
        self.cls = self.vocab.get("[CLS]", 101)
        self.sep = self.vocab.get("[SEP]", 102)
        self.pad = self.vocab.get("[PAD]", 0)

    @staticmethod
    def _chunks(text: str) -> list[str]:
        """中文逐字切开(每个字前面补空格,标准做法),拉丁/数字按词。"""
        text = (text or "").lower()
        out: list[str] = []
        buf = ""
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(" " + ch)
            elif ch.isspace():
                if buf:
                    out.append(buf)
                    buf = ""
            else:
                buf += ch
        if buf:
            out.append(buf)
        return out

    def encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for chunk in self._chunks(text):
            if chunk.startswith(" "):
                target, start = chunk[1:], 0
            else:
                target, start = chunk, 0
            ok = True
            while start < len(target):
                matched = None
                for end in range(len(target), start, -1):
                    piece = target[start:end]
                    if start > 0:
                        piece = "##" + piece
                    if piece in self.vocab:
                        matched = (piece, end)
                        break
                if matched is None:
                    ok = False
                    break
                ids.append(self.vocab[matched[0]])
                start = matched[1]
            if not ok:
                ids.append(self.unk)
        ids = ids[:self.max_len - 2]
        return [self.cls] + ids + [self.sep]

    def batch(self, texts) -> dict:
        rows = [self.encode(t) for t in texts]
        width = max((len(r) for r in rows), default=2)
        input_ids, mask, types = [], [], []
        for r in rows:
            pad = width - len(r)
            input_ids.append(r + [self.pad] * pad)
            mask.append([1] * len(r) + [0] * pad)
            types.append([0] * width)
        return {"input_ids": input_ids, "attention_mask": mask, "token_type_ids": types}
