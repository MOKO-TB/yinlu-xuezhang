"""一键体检(§6 #19 里那条「把 `check_*` 摆成一屏可点的体检」)。

用户遇到「不工作了」时,以前的路是:要么自己猜,要么来找开发者要一句"你跑一下
scripts/check_docs.py 看看"。可这些自检其实**早就存在**,只是散在四处、各自只认
自己那一摊:`library.catalog_health` 在启动时跑,`browser.health` 挂在 `/browser`,
`mcp.probe_server` 只在 add 时跑一次,四支 `scripts/check_*.py` 只有开发者手敲才跑。
这个模块不重写任何判定 —— 它只把**现成的结论**收进一张表,该问脚本的仍去问脚本
(用 subprocess 调那四支本身,免得同一条规则出现第二份真相)。

三条口径(都是「没有来源的数字一律不说」这条规矩的直接结果):

1. **每行都由一次真调用撑起来**。detail 里那个数(模型条数、库大小、工具数、脚本
   退出码)只能来自本轮真读到的返回值;读不到就写 unknown,并且说清为什么读不到。
   绝不因为"大多数情况是好的"就把一行标成 ok。
2. **分两档成本**。`local` 全是本机内存/文件判断,毫秒级,面板一开就有;`deep`
   (界面上那颗「深检」)要等网络或外部进程 —— 模型探活、浏览器握手、PowerShell 读
   快捷方式,在源码仓库里还包括四道 `check_*` 闸(全跑一分多钟)。装好的包里没有
   `scripts/`,那一组**整组不出现**,而不是摆四行"跳过"糊弄。
3. **修复动作只有两条真路**:`去配置模型`(打开本来就有的模型管理对话框)和
   `问学长`(把一句话填进输入框,由用户自己发)。刻意**不给**"点一下就批准白名单"
   这种按钮 —— 项目白名单的批准必须过确认框那一次点头(v0.9.87 的设计核心),
   面板再开一条捷径等于绕过它。

`project` / `mcp_rows` 由调用方(桥)**递进来**,这里不自己 load 第二次:第二次装载
就是第二份真相,还会绕开门面上 `tool_names` 那套校验(同 v0.9.88 那条口径)。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

OK, WARN, BAD, UNKNOWN, SKIP = "ok", "warn", "bad", "unknown", "skip"

BADGE = {OK: "正常", WARN: "要注意", BAD: "有问题", UNKNOWN: "没查成", SKIP: "不适用"}

GROUPS = (("local", "本机状态", "🖥"),
          ("deep", "联网与外部进程", "🌐"),
          ("repo", "源码仓库闸", "🧪"))

# 装好的包里 scripts/ 不存在,这一组整组不出现。每项:(键, 标题, 脚本, 参数, 秒)
# 秒是**等它的上限**,不是预期用时:check_docs --collect 要跑一遍 pytest 收集(实测 20~30 s),
# 卡在某一台机器上可能更久 —— 宁可报"没等它",不猜结论。
GATE_SCRIPTS = (
    ("gate_docs", "文档与口径对账", "check_docs.py", ("--collect",), 150),
    ("gate_js", "前端内联脚本语法", "check_frontend_js.py", (), 60),
    ("gate_db", "馆藏库三层核对", "check_library_db.py", (), 120),
    ("gate_frozen", "包内版本与冻结针", "check_frozen_version.py", (), 120),
)

# 仓库根:minicode/health.py 的上两级(打包形态下这条路径没有 scripts/,自然不进 repo 组)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _row(key: str, group: str, label: str, status: str,
         detail: str = "", fix: str = "", action: dict | None = None) -> dict:
    return {"key": key, "group": group, "label": label, "status": status,
            "badge": BADGE[status], "detail": detail, "fix": fix,
            "action": action or None}


def _ask(text: str, label: str = "问学长怎么修") -> dict:
    """把一句话**填进输入框**(不自动发):发出去之后走的仍是正常那一轮对话。"""
    return {"kind": "ask", "label": label, "text": text}


def _size_text(n: Any) -> str:
    try:
        size = float(n)
    except (TypeError, ValueError):
        return "未知"
    if size >= 1e9:
        return f"{size / 1e9:.2f} GB"
    if size >= 1e6:
        return f"{size / 1e6:.0f} MB"
    return f"{size / 1e3:.0f} KB"


# ------------------------------------------------------------------ 本机即时项


def _check_form(cfg: dict) -> Callable[[], dict]:
    def run() -> dict:
        frozen, backend = bool(cfg.get("frozen")), str(cfg.get("backend") or "?")
        form = "安装版/便携版(打包运行)" if frozen else "源码运行"
        where = {"local": "用户目录", "exe": "程序旁边"}.get(backend, "程序目录")
        return _row("form", "local", "版本与形态", OK,
                    detail=f"v{cfg.get('version')} · {form} · 数据在{where}"
                           f"({cfg.get('app_dir')})")
    return run


def _check_dirs(cfg: dict) -> Callable[[], dict]:
    def run() -> dict:
        if not cfg.get("app_dir"):
            return _row("dirs", "local", "数据目录可写", UNKNOWN, detail="没拿到数据目录路径")
        if cfg.get("writable"):
            return _row("dirs", "local", "数据目录可写", OK,
                        detail=f"可写:{cfg.get('app_dir')}")
        return _row(
            "dirs", "local", "数据目录可写", BAD,
            detail=f"写不进去:{cfg.get('app_dir')}",
            fix="装在了只读位置(如 Program Files)或磁盘满了。这一项坏了的表现不是报错,"
                "而是「设置改了当时有效、重启又变回去」。挪到可写目录再启动一次。")
    return run


def _check_workspace(workspace: Any) -> Callable[[], dict]:
    def run() -> dict:
        if not workspace:
            return _row("workspace", "local", "工作区", UNKNOWN,
                        detail="这一轮没传工作区,没处可查")
        p = Path(str(workspace))
        if not p.is_dir():
            return _row("workspace", "local", "工作区", BAD,
                        detail=f"目录不存在:{p}",
                        fix="原来选的文件夹被删了或移动了。课表/待办/记忆都还在原来那个"
                            "工作区里,在左边项目列表重新选回它就能看见。")
        try:
            entries = list(p.iterdir())
        except OSError as e:
            return _row("workspace", "local", "工作区", BAD,
                        detail=f"打不开:{type(e).__name__}: {e}",
                        fix="多半是没权限,或者那个盘现在没挂载(U 盘/网络盘)。")
        return _row("workspace", "local", "工作区", OK,
                    detail=f"{p}({len(entries)} 个条目)")
    return run


def _check_model_config(cfg_obj: Any) -> Callable[[], dict]:
    def run() -> dict:
        from minicode.config import BUILTIN_PROVIDER_ID
        if cfg_obj is None:
            return _row("modelcfg", "local", "模型配置", UNKNOWN,
                        detail="这一轮没拿到配置对象")
        provider = getattr(cfg_obj, "provider", None)
        name = getattr(provider, "name", "") or "(没有可用供应商)"
        model = str(getattr(cfg_obj, "model", "") or "").strip() or "未选"
        builtin = bool(provider and getattr(provider, "id", "") == BUILTIN_PROVIDER_ID)
        if not getattr(cfg_obj, "api_key_configured", False):
            return _row("modelcfg", "local", "模型配置", BAD,
                        detail=f"当前供应商「{name}」没有可用密钥,模型「{model}」",
                        fix="没密钥的表现是每句话都回不了。点右边「去配置模型」填 Key;"
                            "内置供应商那条不用填,连不上通常是没在校园网里。",
                            action={"kind": "models", "label": "去配置模型"})
        # 内置供应商的内网地址不下发(与 get_info 同一口径,界面只看得到模型名)
        net = "" if builtin else f" · {getattr(provider, 'base_url', '')}"
        return _row("modelcfg", "local", "模型配置", OK,
                    detail=f"供应商「{name}」· 模型「{model}」· 密钥已配置{net}")
    return run


def _check_permission(cfg_obj: Any, project: Any) -> Callable[[], dict]:
    def run() -> dict:
        mode = str(getattr(cfg_obj, "permission_mode", "") or "") or "(未知档位)"
        if project is None:
            return _row("permission", "local", "权限与项目白名单", UNKNOWN,
                        detail=f"档位「{mode}」;白名单这一轮没递进来。桥里没有现成的装载结果"
                               "时这里不自己 load 第二次 —— 两份装载就是两份真相")
        n = len(getattr(project, "rules", []) or [])
        problems = list(getattr(project, "problems", []) or [])
        head = f"档位「{mode}」"
        tail = (f";另有 {len(problems)} 条装载时被拒收:{' / '.join(problems[:2])}"
                if problems else "")
        if not n:
            why = str(getattr(project, "reason", "") or "")
            if "没有 permissions.json" in why:
                return _row("permission", "local", "权限与项目白名单", OK,
                            detail=f"{head} · 项目白名单:无")
            # 文件在、一条规则都没装起来:那是"写了但没用",不该装作无事
            return _row("permission", "local", "权限与项目白名单", WARN,
                        detail=f"{head} · 项目白名单一条规则都没装起来"
                               + (f"({why})" if why else "") + tail,
                        fix="多半是 JSON 写坏、规则键给错,或文件读不到。在对话里说"
                            "「看看这个项目的白名单为什么没生效」,学长会把逐条原因念给你听。",
                        action=_ask("请看看这个项目的 permissions.json 为什么一条规则都没"
                                    "装起来,并告诉我怎么改。"))
        if getattr(project, "effective", False):
            return _row("permission", "local", "权限与项目白名单",
                        OK if not problems else WARN,
                        detail=f"{head} · 白名单 {n} 条**已生效**(命中就免确认){tail}")
        return _row(
            "permission", "local", "权限与项目白名单", WARN,
            detail=f"{head} · 白名单写了 {n} 条但**未生效**"
                   f"({getattr(project, 'reason', '') or '原因未报'}){tail}",
            fix="写了不等于生效:批准记的是这份文件的内容指纹,改一个字就回到逐次确认。"
                "要生效请在对话里说一声,学长会弹确认框请你核对 —— 面板刻意不给"
                "「一键批准」,那条捷径等于绕过你点头。",
            action=_ask("请把这个项目根下的 permissions.json 白名单批准一下"
                        "(用 permissions_trust 工具,我要在确认框里核对)。"))
    return run


def _check_mcp(mcp_rows: Any) -> Callable[[], dict]:
    def run() -> dict:
        if mcp_rows is None:
            return _row("mcp", "local", "MCP 服务器", UNKNOWN,
                        detail="这一轮没递来 MCP 清单(终端形态没有在线的管理器)")
        if not mcp_rows:
            return _row("mcp", "local", "MCP 服务器", SKIP,
                        detail="一台都没配(这不是故障:不配 MCP 照样能用)")
        on = [r for r in mcp_rows if r.get("connected")]
        off = [r for r in mcp_rows
               if not r.get("connected") and r.get("enabled", True)]
        parts = [f"{r.get('label')}({r.get('n_tools', 0)} 工具)" for r in on]
        detail = f"共 {len(mcp_rows)} 台,连上 {len(on)} 台:" + ("、".join(parts) or "无")
        if off:
            detail += ";没连上:" + "、".join(str(r.get("label")) for r in off)
            return _row("mcp", "local", "MCP 服务器", WARN, detail=detail,
                        fix="没连上的那台,它的工具会**整组消失**(表现是"
                            "「明明配了却没那几样工具」。在对话里说「看看 MCP 状态」,"
                            "学长按配置真连一次,把原因念给你听)。")
        if not on:
            return _row("mcp", "local", "MCP 服务器", WARN,
                        detail=detail + "(配置里都在,但一台都没启用)",
                        fix="要用时在对话里说「启用某台 MCP」。")
        return _row("mcp", "local", "MCP 服务器", OK, detail=detail)
    return run


def _check_library(workspace: Any, frozen: bool) -> Callable[[], dict]:
    def run() -> dict:
        from minicode import library
        h = library.catalog_health(Path(workspace) if workspace else None)
        st, path, size = h.get("status"), h.get("path"), h.get("size")
        where = f"({path})" if path else ""
        if st == "ok":
            return _row("library", "local", "图书馆馆藏库", OK,
                        detail=f"可用:{h.get('total')} 条书目 / {_size_text(size)}{where}")
        if st == "missing" and not frozen:
            return _row("library", "local", "图书馆馆藏库", SKIP,
                        detail=f"源码运行不带这份 1.3 GB 的大库(不在 {path});"
                               "要搜书请用安装包或 win64 zip。",
                        fix="这不是故障:装好的包才带库。开发者要本机也能搜书,"
                            "把完整版里的 library.db 放到 crawled/<域名>/ 下。")
        notice = (library.health_notice(h) or "图书馆搜书不可用").rstrip("。")
        bits = [notice, _size_text(size) + where]
        if h.get("total") is not None:
            bits.append(f"库里 {h.get('total')} 条")
        if h.get("expected"):
            bits.append(f"应为 {_size_text(h.get('expected'))}")
        return _row("library", "local", "图书馆馆藏库", BAD,
                    detail=" · ".join(bits),
                    fix="现场逐项核对(凭证大小 + 开库实查 + 与启动同一套判定)在源码仓库里跑:"
                        "`python scripts/check_library_db.py <安装目录>`;"
                        "装好的包里请重新运行安装包、或重新解压一次。")
    return run


# ------------------------------------------------------------------ 联网/外部进程


def _check_model_net(cfg_obj: Any, probe: Callable, timeout: float) -> Callable[[], dict]:
    def run() -> dict:
        from minicode.model_probe import explain_status
        if cfg_obj is None:
            return _row("modelnet", "deep", "模型服务连不连得上", UNKNOWN,
                        detail="没有配置对象可问")
        base_url = str(getattr(cfg_obj, "base_url", "") or "")
        api_key = str(getattr(getattr(cfg_obj, "provider", None), "api_key", "") or "")
        status, names = probe(base_url, api_key, timeout)
        if status in (200, 404):
            more = (f"列出 {len(names)} 个模型" if names
                    else "这家没实现 /models,但网络与服务都是通的")
            return _row("modelnet", "deep", "模型服务连不连得上", OK,
                        detail=f"能连上(HTTP {status},{more})")
        why = explain_status(status, "")
        if status == 429 or status >= 500:
            return _row("modelnet", "deep", "模型服务连不连得上", WARN, detail=why,
                        fix="这类通常是暂时的(限流或服务端抖动)。对话里连着失败时学长会"
                            "自己起一条看门狗,服务活了会通知你一声。")
        return _row("modelnet", "deep", "模型服务连不连得上", BAD, detail=why,
                    fix="改一下接口地址或密钥再问一次(模型管理对话框里有「测试」)。",
                    action={"kind": "models", "label": "去配置模型"})
    return run


def _check_browser(health: Callable) -> Callable[[], dict]:
    def run() -> dict:
        h = health()
        if h.get("ok"):
            return _row("browser", "deep", "浏览器能力", OK,
                        detail=f"{h.get('browser') or '浏览器'} · {h.get('mode') or '?'}"
                               f" · {h.get('detail') or ''}")
        return _row("browser", "deep", "浏览器能力", BAD,
                    detail=f"不可用:{h.get('detail') or '没找到 Edge/Chrome'}",
                    fix="装一个 Edge 或 Chrome(或设环境变量 YINLUXS_BROWSER 指向浏览器 exe);"
                        "只想用已开远程调试的浏览器,可设 YINLUXS_CDP_URL=http://127.0.0.1:9222。")
    return run


def _check_startup(startup: Callable) -> Callable[[], dict]:
    def run() -> dict:
        st = startup()
        if not st.get("supported"):
            return _row("startup", "deep", "开机自启", SKIP,
                        detail="这个系统/形态没有可写的「启动」文件夹,开不了(不是故障)")
        if st.get("installed") and st.get("ours"):
            return _row("startup", "deep", "开机自启", OK,
                        detail=f"已开启,指向本应用:{st.get('target') or st.get('lnk')}")
        if st.get("installed"):
            return _row("startup", "deep", "开机自启", WARN,
                        detail=f"「启动」里有个同名快捷方式,但指向 "
                               f"{st.get('target') or '(空)'},不是本应用",
                        fix="多半是程序换过路径或重装到别处:勾看着是「开」、开机却不响。"
                            "在对话里说「重新打开开机自启」,学长会先核对指向再写。")
        return _row("startup", "deep", "开机自启", SKIP,
                    detail="没开(这是选择,不是故障):托盘里勾一下或对话里说一声就能开")
    return run


# ------------------------------------------------------------------ 源码仓库闸


def _run_gate(root: Path, name: str, args: tuple, timeout: float) -> tuple[int, str]:
    """真跑那支脚本,原样把退出码与尾部输出带回来(判定仍在脚本自己那里)。"""
    proc = subprocess.run([sys.executable, str(root / "scripts" / name), *args],
                          cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    text = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(ln for ln in text.splitlines() if ln.strip())[-600:]
    return proc.returncode, tail


def _check_gates(root: Path, gate: Callable, has_dist: bool) -> list[dict]:
    out: list[dict] = []
    for key, label, script, args, timeout in GATE_SCRIPTS:
        if key == "gate_frozen" and not has_dist:
            out.append(_row(key, "repo", label, SKIP,
                            detail=f"没有 {root / 'dist'}(还没打过包):这条只在出包后有意义"))
            continue
        try:
            code, tail = gate(root, script, args, timeout)
        except subprocess.TimeoutExpired:
            out.append(_row(key, "repo", label, UNKNOWN,
                            detail=f"{script} 跑到 {timeout} 秒还没回,没等它 —— 不猜结论"))
            continue
        except OSError as e:
            out.append(_row(key, "repo", label, UNKNOWN,
                            detail=f"{script} 没跑起来:{type(e).__name__}: {e}"))
            continue
        how = f"python scripts/{script}" + (" --collect" if args else "")
        if code == 0:
            out.append(_row(key, "repo", label, OK, detail=f"{script} 退出码 0"))
        elif key == "gate_js" and "node" in tail.lower():
            out.append(_row(key, "repo", label, UNKNOWN,
                            detail=f"{script} 退出码 {code},输出里提到 node —— 这台机器"
                                   f"可能没装 Node,那不是前端的错:{tail[-300:]}",
                            fix="装一个 Node 再跑;没 Node 时这条查不成,别当故障。"))
        else:
            out.append(_row(key, "repo", label, BAD,
                            detail=f"{script} 退出码 {code}:{tail}",
                            fix=f"照上面几行改,改完再跑一次 `{how}`。"))
    return out


def _check_injection_log(stats: Callable | None = None) -> Callable[[], dict]:
    """注入防御留痕:这份日志以前只写不读(v0.9.96 才接进面板)。

    口径两条:①**只报计数与类别,不搬正文** —— 记录里的 `arg`/`why` 可能带着本机
    路径或外部内容片段,而这一行既显示在面板上也会进模型回话;②**0 条不等于防线在
    工作**,只代表这台机器还没触发过一次强制确认,所以那句写得很直,不写成"一切正常"。
    """
    def run() -> dict:
        if stats is None:
            from minicode.provenance import log_stats
            stats_ = log_stats()
        else:
            stats_ = stats()
        if not stats_.get("readable"):
            return _row("injection", "local", "注入防御留痕", UNKNOWN,
                        detail="日志文件在,但这一版读不出来(不是没记,是没读成)",
                        fix="不是配置坏了:这一项没查成。明细在数据目录的 "
                            "injection-events.log,打开看一眼即知。")
        n = int(stats_.get("events") or 0)
        if not n:
            return _row("injection", "local", "注入防御留痕", OK,
                        detail="这台机器上还没出现过需要强制确认的敏感动作(0 条)。"
                               "0 条不代表防线在工作,只代表没触发过。")
        gates = "、".join(str(g) for g in (stats_.get("gates") or [])[:4])
        last = str(stats_.get("last_at") or "")[:19].replace("T", " ")
        return _row("injection", "local", "注入防御留痕", OK,
                    detail=f"安全门过问并记下的敏感动作 {n} 次(最近一次 {last or '时刻没记上'};"
                           f"门:{gates or '?'})。面板只给计数与类别,明细在数据目录的 "
                           "injection-events.log —— 里面会有本机路径,截图前自己先看一眼。")
    return run


# ------------------------------------------------------------------------ 汇总


def report(workspace: Any = None, config: Any = None, project: Any = None,
           mcp_rows: Any = None, deep: bool = False, repo: Any = None,
           probe: Callable | None = None, browser_health: Callable | None = None,
           startup: Callable | None = None, gate: Callable | None = None,
           injection_stats: Callable | None = None,
           timeout: float = 10.0) -> dict:
    """算一张体检表。逐项兜异常:某一项自己崩了绝不能把整张表带崩,也不能报成"正常"。

    `repo=None` 时自动判(非打包且 `scripts/check_docs.py` 在);传 False 强制关掉 ——
    测试就靠它不真去跑那四支脚本。五个注入点(probe / browser_health / startup /
    gate / injection_stats)默认取真实现,是给测试钉的,不是给用户配的开关。
    """
    from minicode.paths import diagnostics as paths_diagnostics

    diag = {**paths_diagnostics(), "version": _version()}
    if repo is None:
        repo = (not diag.get("frozen")) and (REPO_ROOT / "scripts" / "check_docs.py").is_file()

    checks: list[tuple[str, str, str, Callable[[], dict]]] = [
        ("form", "local", "版本与形态", _check_form(diag)),
        ("dirs", "local", "数据目录可写", _check_dirs(diag)),
        ("workspace", "local", "工作区", _check_workspace(workspace)),
        ("modelcfg", "local", "模型配置", _check_model_config(config)),
        ("permission", "local", "权限与项目白名单", _check_permission(config, project)),
        ("mcp", "local", "MCP 服务器", _check_mcp(mcp_rows)),
        ("library", "local", "图书馆馆藏库", _check_library(workspace,
                                                            bool(diag.get("frozen")))),
        ("injection", "local", "注入防御留痕", _check_injection_log(injection_stats)),
    ]
    if deep:
        checks += [
            ("modelnet", "deep", "模型服务连不连得上",
             _check_model_net(config, probe or _real_list_models, timeout)),
            ("browser", "deep", "浏览器能力",
             _check_browser(browser_health or _real_browser_health)),
            ("startup", "deep", "开机自启", _check_startup(startup or _real_startup)),
        ]
    rows: list[dict] = []
    for key, group, label, fn in checks:
        try:
            rows.append(fn())
        except Exception as e:          # 出错不伪装成"这项没问题"
            rows.append(_row(key, group, label, UNKNOWN,
                             detail=f"这一项自己检查时出错:{type(e).__name__}: {e}",
                             fix="不是配置坏了,是这一项没查成 —— 把这行截给学长看。"))
    # 四道闸只在深检时跑,而且只在源码仓库跑:一次全跑要一分多钟,把它挂在"打开面板"
    # 上等于让每次体检都先卡一分多钟 —— 那时用户已经关掉了。
    if deep and repo:
        rows += _check_gates(REPO_ROOT, gate or _run_gate, (REPO_ROOT / "dist").is_dir())

    summary = {s: sum(1 for r in rows if r["status"] == s)
               for s in (OK, WARN, BAD, UNKNOWN, SKIP)}
    return {"ok": True, "deep": bool(deep), "repo": bool(repo),
            "groups": [{"key": k, "label": lb, "icon": ic,
                        "rows": [r for r in rows if r["group"] == k]}
                       for k, lb, ic in GROUPS],
            "summary": summary, "total": len(rows),
            "attention": [r["label"] for r in rows if r["status"] in (BAD, UNKNOWN)],
            "verdict": _verdict(summary, rows, len(rows)),
            # 「统计于 HH:MM:SS」那个数是**这次算的**时刻,不是问题发生的时刻
            "checked_at": time.strftime("%H:%M:%S", time.localtime())}


def _verdict(summary: dict, rows: list[dict], total: int) -> str:
    """一句结论:只报数与名字,不安慰(安慰里没有信息)。"""
    def names(status: str) -> str:
        return "、".join(r["label"] for r in rows if r["status"] == status)[:60]

    if summary[BAD]:
        return f"{total} 项里有 {summary[BAD]} 项**有问题**:{names(BAD)}"
    if summary[UNKNOWN]:
        return (f"{total} 项里有 {summary[UNKNOWN]} 项没查成:{names(UNKNOWN)}"
                "(没查成不等于没问题)")
    if summary[WARN]:
        return f"{total} 项里没有硬故障,{summary[WARN]} 项要注意"
    return f"{total} 项检查都没问题"


def _version() -> str:
    from minicode import __version__
    return __version__


def _real_list_models(base_url: str, api_key: str, timeout: float):
    from minicode import model_probe
    return model_probe.list_models(base_url, api_key, timeout=timeout)


def _real_browser_health() -> dict:
    from minicode import browser
    return browser.health()


def _real_startup() -> dict:
    from minicode import tray
    return tray.startup_status()


def render_text(rep: dict) -> str:
    """把一张体检表打成**终端**看的文字版(面板画的是同一个 dict)。

    为什么要有这一段:`python -m minicode.health` 走的是"同学说不清、维护者隔着一段
    文字看"那条路 —— 那边没有窗口也没有输入框,只能靠一段可粘贴的输出。
    这里不重写任何判定:一行行取 `report()` 里的原文,`badge`/`detail`/`fix` 是什么就
    打什么;界面上那颗「问学长怎么修」的按钮在终端点不动,所以把**它要填的那句话**
    原样念出来,让拿着这段文字的人自己发得出去。
    """
    rows_all = [r for g in rep.get("groups") or [] for r in (g.get("rows") or [])]
    head = (f"🩺 一键体检 · {rep.get('verdict') or '(没算出结论)'}"
            f" · 统计于 {rep.get('checked_at') or '(没拿到时刻)'}")
    out = [head]
    if not rep.get("deep"):
        out.append("   (浅检:只问本机内存与文件;要问网络与外部进程加 --deep)")
    for g in rep.get("groups") or []:
        rows = g.get("rows") or []
        if not rows:
            continue        # 整组没出现(装好的包没有 repo 组)—— 不摆一个空标题糊字数
        out.append(f"{g.get('icon') or ''} {g.get('label')}")
        for r in rows:
            out.append(f"  [{r.get('badge')}] {r.get('label')} — "
                       f"{r.get('detail') or '(这一项没给说明)'}")
            if r.get("fix"):
                out.append(f"      怎么修:{r['fix']}")
            act = r.get("action") or {}
            if act.get("kind") == "ask":
                out.append(f"      (界面里点「{act.get('label')}」会把这句填进输入框:"
                           f"{act.get('text')})")
    if not rows_all:
        out.append("(一张表都没算出来)")
    return "\n".join(out)


def main(argv=None) -> int:
    """`python -m minicode.health [--deep] [--json]` —— 不开窗口也能拿到那张表。

    退出码只有一件事可看:**有没有「有问题」这一档**(bad)。1 不代表"坏了三件事",
    只代表"至少一行被判定为有问题";`没查成`(unknown)不算失败 —— 无头模式里没有活的
    权限门与 MCP 连接,那两行本来就该写"没查成",把它们算成失败等于逼着维护者去修
    一个不存在的问题。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    deep = "--deep" in args
    as_json = "--json" in args
    from minicode.workspace import startup_workspace
    # 拿配置这一段**绝不能让进程退出**:`load_config()` 的"没配好就提示退出"是给 CLI
    # 启动用的,而体检最该跑的场景恰恰就是"这台机器配坏了/还没配"。所以先看文件在不在
    # (不在就不调它 —— 它会把 example 复制成 config.json,只读的诊断不该顺手写盘),
    # 再把 SystemExit 吞下来交给表里那一行去说。
    config = None
    try:
        from minicode.config import CONFIG_PATH, load_config
        if Path(CONFIG_PATH).exists():
            config = load_config()
    except SystemExit:
        config = None
    rep = report(workspace=startup_workspace(Path.cwd()), config=config, deep=deep)
    if as_json:
        import json
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 1 if rep.get("summary", {}).get(BAD) else 0
    text = render_text(rep)
    if config is None:
        text += ("\n   (这一轮没拿到可用的 config.json —— 这不是错误:没配好、还没配的机器"
                 "正是体检最该跑的时候,「模型配置」那一行会照实写「没查成」。)")
    print(text)
    return 1 if rep.get("summary", {}).get(BAD) else 0


if __name__ == "__main__":
    raise SystemExit(main())
