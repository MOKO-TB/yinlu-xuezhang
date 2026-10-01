"""反馈包(R2,v0.9.99):把「同学机器上的运行现场」缝成一份能发出去的文件。

为什么要有它:这个产品是出包发给同学的,但同学机器上的体检表、缺口账、技能账
维护者**全看不到** —— 同学报问题只能靠截图。v0.9.98 的 export_trace 与
v0.10.01 的体检 CLI 把两块最难的料备齐了,这里只做缝合:一次调用产出
**单一 JSON**(手动发给维护者),不再让用户拼四份截图。

隐私三条(与迁移包同一套口径的方向,但不共用它的闸):
1. **密钥/凭据/配置文件一个字节都不读** —— 体检表各行的 detail 只写状态
   与目录说明(health.report 的口径:探针只回状态),本模块自己绝不碰
   credentials.json / config.local.json / browser-profile;
2. **心情/课表/成绩等个人数据不进包** —— 只带缺口问法(top N,匿名)与
   技能使用统计(只有技能名与次数);
3. **轨迹默认不带** —— 它含对话原文;include_trace=True 才内嵌,且文件头
   的 privacy note 会写明。文件由用户**手动**发送,发之前自己过一眼。
"""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path

REPORT_FORMAT = "yinlu-feedback"
REPORT_VERSION = 1


def build_report(workspace=None, config=None, include_trace: bool = False,
                 trace_messages: "list | None" = None,
                 note: str = "") -> dict:
    """拼一份反馈包(纯数据,不落盘 —— 落盘归工具处理器)。逐段兜异常:
    任何一段拿不到就写 {"error": …},绝不整包失败,也不谎报"正常"。"""
    from minicode import __version__
    from minicode.paths import diagnostics

    diag = diagnostics()
    rep: dict = {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "app": {
            "app_version": __version__,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "frozen": bool(diag.get("frozen")),
            "data_dir": str(diag.get("backend") or ""),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "note": str(note or "").strip(),
        "privacy": ("本文件含:体检表(状态与目录说明)、最近 7 天检索缺口问法、"
                    "技能使用统计" + ("、会话轨迹(含对话原文)" if include_trace else "")
                    + ";不含密钥/凭据/配置内容,不含心情/课表/成绩。发送前请过一眼。"),
    }

    # ---- 体检(浅检;repo=False:反馈包不跑四道闸,那是开发机的事)----
    try:
        from minicode import health
        full = health.report(workspace=workspace, config=config, deep=False,
                             repo=False)
        rows = []
        for g in (full.get("groups") or []):
            for r in (g.get("rows") or []):
                rows.append({"key": r.get("key"), "label": r.get("label"),
                             "status": r.get("status"), "detail": r.get("detail")})
        rep["health"] = {"verdict": full.get("verdict") or "", "rows": rows}
    except Exception as e:
        rep["health"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 缺口问法(最近 7 天 top10):同学哪里没被服务好的直接证据 ----
    try:
        from minicode import growth
        gaps = growth.gap_digest(days=7, top=10) or []
        rep["kb_gaps"] = [{"query": g.get("query"), "days": g.get("days")}
                          for g in gaps]
    except Exception as e:
        rep["kb_gaps"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 技能使用(全量聚合取前 10):哪些技能真的在被用 ----
    try:
        from minicode import skills
        rep["skill_usage"] = skills.usage_summary()[:10]
    except Exception as e:
        rep["skill_usage"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 轨迹(可选,默认不带)----
    if include_trace:
        try:
            from minicode import __version__ as _v
            from minicode.session import trace_payload
            payload, _rows, _n = trace_payload(trace_messages or [],
                                               app_version=_v)
            rep["trace"] = payload
        except Exception as e:
            rep["trace"] = {"error": f"{type(e).__name__}: {e}"}
    return rep


def export_feedback_tool(ctx, include_trace=False, note: str = "",
                         reason: str = "") -> str:
    """export_feedback 工具处理器:拼包落工作区根,回话教清发送方式。"""
    sess = getattr(ctx, "session", None)
    msgs = getattr(sess, "messages", None) if sess else None
    rep = build_report(workspace=getattr(ctx, "workspace", None),
                       config=getattr(ctx, "config", None),
                       include_trace=bool(include_trace),
                       trace_messages=msgs if include_trace else None,
                       note=note)
    dest = Path(ctx.workspace) / \
        f"反馈-{time.strftime('%Y%m%d-%H%M')}.json"
    try:
        dest.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except OSError as e:
        return f"错误: 反馈包写不进去:{type(e).__name__}: {e}"
    kb = rep.get("kb_gaps") or []
    n_gaps = len(kb) if isinstance(kb, list) else 0
    n_trace = (rep.get("trace") or {}).get("count") if include_trace else None
    lines = [f"已生成反馈包 → {dest.name}",
             f"内容:体检表 · 最近 7 天检索缺口 {n_gaps} 条 · 技能使用统计"
             + (f" · 会话轨迹 {n_trace} 条消息" if include_trace else "")]
    if note:
        lines.append(f"附言:{note}")
    lines.append("**发出去之前请自己打开过一眼**(文件里写着含什么、不含什么),"
                 "然后把它发给维护者即可 —— 报问题只需要这一个文件,不用截图。")
    return "\n".join(lines)
