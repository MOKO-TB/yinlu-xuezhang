"""校验打包产物里编译进去的 minicode 代码确实是最新版(TARGETS 即当版针)。

PyInstaller 把纯 Python 模块压进 PYZ 归档(嵌在 exe 里),磁盘上看不到 .py 文件,
所以走 PyInstaller 自己的读取器把模块字节码取出来,核对新增符号是否存在。

用法: .venv/Scripts/python.exe scripts/check_frozen_version.py [exe路径]

清单默认对准 **onedir 的 CLI exe**(`dist/引路学长/引路学长-CLI.exe`)。拿单文件版
(`dist/引路学长-单文件版.exe`)核对时,`minicode.web_profile` 那一条必然假红 ——
单文件版是纯桌面形态,入口不 import 网页层,那个模块压根不在它的 PYZ 里。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _src_version() -> str:
    """版本号从源码里读,别在这份脚本里再抄一遍 —— 抄漏一次就是一句假绿的旧话。"""
    p = Path(__file__).resolve().parent.parent / "minicode" / "__init__.py"
    try:
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                      p.read_text(encoding="utf-8"), re.M)
    except OSError:
        return "?"
    return m.group(1) if m else "?"


# 每个模块要求存在的新增符号(v0.9.13:模型清单/导出搜索;v0.9.14:成长闭环/校历/绩点/搜索)
TARGETS: dict[str, tuple[str, ...]] = {
    "minicode.config": ("model_entries", "enabled_models", "sync_model",
                        "_dedupe_models", "_models_from_raw", "PROVIDER_PRESETS"),
    "minicode.gui_bridge": ("test_model", "fetch_models", "get_provider_presets",
                            "export_session", "search_sessions",
                            "rate_message", "get_countdowns",
                            "_provider_credentials",
                            # v0.9.22:按模型能力判定看图 + 写盘失败要说清 + 关窗答复
                            "_vision_on", "_persist_config", "set_vision",
                            "close_choice",
                            # v0.9.23:教务课表导入 + 首次运行向导
                            "schedule_import_open", "schedule_import_read",
                            "schedule_import_model", "schedule_import_confirm",
                            "schedule_import_clear", "schedule_import_status",
                            "_jw_flow", "get_onboarding", "dismiss_onboarding",
                            # v0.9.24:计划面板数据源(只读 + 派发文案)
                            "get_plan"),
    "minicode.model_probe": ("list_models", "explain_status", "remember"),
    # ---- v0.9.22:安装版可用性(数据目录写探针 + 诊断下发) ----
    "minicode.paths": ("_is_writable", "_local_state_dir", "diagnostics",
                       "STATE_BACKEND"),
    # ---- v0.9.22:托盘 + 关窗确认(整套新模块,漏收 = X 又变成直接退出) ----
    "minicode.tray": ("install", "close_choice", "read_pref", "write_pref",
                      "TrayController", "hide_to_tray", "_ensure_icon",
                      "_ensure_parent",
                      # v0.9.25:桌面提醒(漏收 = 最小化后课前提醒彻底没声)
                      "notify", "notify_status", "read_notify_pref",
                      "write_notify_pref", "notify_enabled", "set_notify",
                      "desktop_notify", "KIND_LABELS", "NOTIFY_PREF",
                      # v0.9.25 修:Win11 不显示托盘气球,真通道是 WinRT toast
                      # (漏收 = 装好的包又回到"通知永远看不见")
                      "_toast", "_balloon", "register_notify_identity",
                      "_powershell", "AUMID",
                      # v0.9.25 修第二轮:显示名要写在注册表默认值上、图标路径
                      # 必须 file:///(漏收 = 装好的包通知顶着一串英文、没图标)
                      "_file_uri",
                      # v0.9.26:开机自启(漏收 = 「开机自动启动」说了没反应,
                      # 或更坏:建了快捷方式却没读回核对,开机弹一个死掉的自启项)
                      "_run_ps", "startup_path", "startup_present",
                      "startup_status", "set_startup", "launch_target",
                      "STARTUP_NAME", "BACKGROUND_FLAG", "starts_hidden",
                      # 漏收 = 自启时窗口藏不进托盘也没人把它显示回来(隐形进程)
                      "start_in_tray"),
    "minicode.reasoning": ("reasoning_snapshot", "get_capability",
                           "apply_reasoning_choice"),
    "minicode.session": ("to_markdown", "search_logs", "export_filename"),
    "minicode.skills": ("SKILL_CHANNELS", "describe_channels", "create_skill"),
    "minicode.web_profile": ("网页版不开放会话导出", "search_sessions",
                             # v0.9.23:教务导入需要本机浏览器窗口,网页版必须挡掉
                             "教务课表导入",
                             # v0.9.25:桌面提醒开的是本机通知,网页版同样挡掉
                             # (钉住 toast 字样 = 挡话是改道后那版,不是旧气球版)
                             "桌面提醒", "WinRT toast"),
    # ---- v0.9.14:知识库成长闭环 + 校园场景小工具 ----
    # v0.9.21:候选审核面板(list_candidates/review_candidate/_merge_into_kb)
    "minicode.growth": ("log_kb_gap", "save_candidate", "log_feedback", "kb_suggest",
                        "list_candidates", "review_candidate", "_merge_into_kb",
                        "REVIEWED_KB_FILE"),
    "minicode.calendar": ("campus_calendar", "calendar_payload",
                          "calendar_prompt_section"),
    "minicode.gpa": ("gpa_calc",),
    "minicode.tools.crawl": ("web_search", "_resolve_redirect"),
    # ---- v0.9.15:上下文面板 + py_script ----
    "minicode.tools.shell": ("py_script", "_py_runtime_command"),
    "minicode.model_catalog": ("_context_breakdown", "vision_forced"),
    # ---- v0.9.17:截止提醒 ----
    "minicode.reminders": ("reminder", "add", "due_soon", "parse_due"),
    # ---- v0.9.18:注入防御(provenance 是函数内 import,必须核它真进包了) ----
    "minicode.provenance": ("wrap", "strip_marks", "is_external",
                            "contains_external", "log_escalation"),
    "minicode.sandbox": ("SECRET", "is_credential_filename",
                         "command_hits_credential", "command_hits_network"),
    "minicode.permission": ("note_external", "_is_sensitive_action",
                            "_credential_hit", "_same_site"),
    "minicode.kb": ("crawl_credibility", "host_is_trusted",
                    "CRAWLED_UNVERIFIED"),
    # ---- v0.9.18:课表持久化 + 天气 chip ----
    "minicode.schedule": ("parse_weeks", "classes_on", "next_class_note",
                          "schedule_prompt_section",
                          # v0.9.19:面板视图 + 课前到点提醒
                          "view", "upcoming_class", "_parse_hm",
                          # v0.9.24:覆盖前自动比对 + 换回上一版
                          "compare", "undo", "describe_changes", "prev_path",
                          "COMPARE_FIELDS",
                          # v0.9.26:开机补发"今天已开课却没提醒过"的那节课
                          # (漏收 = 8:50 开机只补简报,错过的课永远没人提)
                          "_note_key", "LATE_WINDOW_MINUTES", "late_class_note",
                          # v0.9.27:空闲时段 + 本机作息表(漏收 = 面板「填作息表」点了
                          # 没反应、问"我什么时候有空"退回模型心算,口径全丢)
                          "PERIOD_TIMES_FILE", "load_period_times",
                          "save_period_times", "_parse_periods", "_row_periods",
                          "free_slots", "week_gaps", "format_week_gaps",
                          "format_free_slots", "_week_overlay"),
    "minicode.weather": ("weather_card", "umbrella_tip", "refresh_async",
                         "get_weather"),
    # ---- v0.9.19:拖拽上传(字节落盘,绕开 WebView2 拿不到本地路径) ----
    "minicode.attachments": ("save_dropped_bytes", "unique_target"),
    # ---- v0.9.20:今日简报聚合 + 推送去重 ----
    "minicode.briefing": ("build", "_schedule_bits", "_weather_bits",
                          "_reminders_bits", "_calendar_bits"),
    # ---- v0.9.16/17/20:主动推荐 + 新版本检查 + 今日简报(合并成一个键,
    #      避免 dict 重复键 last-wins 让早先的 needle 悄悄漏检) ----
    "minicode.proactive": ("check_once", "fetch_latest", "start_loop", "clean_title",
                           "check_update", "push_briefing", "start_class_loop",
                           "calendar_alerts",
                           # v0.9.25:同一份推送同步发到桌面
                           "KIND_TITLES", "desktop_text", "push_desktop",
                           # v0.9.26:去重集落盘 + 开机补发(漏收 = 重启后
                           # "错过的那条永远不补、弹过的那条再弹一次")
                           "PUSHED_FILE", "pushed_path", "load_pushed",
                           "was_pushed", "mark_pushed",
                           "briefing_pushed_today", "catch_up"),
    # ---- v0.9.23:教务课表导入(整套新模块,漏收 = 点「导入课表」直接报错) ----
    "minicode.schedule_import": ("ImportFlow", "parse_grid", "summarize",
                                 "clear_login_state", "GRID_JS", "_norm_periods",
                                 "MODEL_SYSTEM",
                                 # v0.9.24:通用取材 + 「先认版式再挑文法」那条路
                                 "_parse_any", "page"),
    # ---- v0.9.24:结构化解析从"只对着强智"泛化成独立一层(漏收 = 向导和 page_parse 都炸) ----
    "minicode.site_parse": ("parse_page", "parse_qiangzhi_grid", "parse_generic_grid",
                            "_entry_generic", "_entry_qiangzhi", "_weekday_header",
                            "TABLE_JS", "page_parse", "_render_tool_result", "ROWS_CAP"),
    # ---- v0.9.23:首次运行向导 + 数据目录可视化 ----
    "minicode.onboarding": ("payload", "dismiss", "read", "STEPS", "PREF_FILE",
                            "reset"),
    # ---- v0.9.23:按帧读取 + 可见窗口(教务课表走的就是这几个能力) ----
    "minicode.browser": ("adopt_page", "page_frames", "eval_in_frame",
                         "read_frames", "ensure_visible", "_endpoint_port",
                         "FRAME_MAX_CHARS"),
    # ---- v0.9.24:MCP 三种传输(stdio 之外只有网址的服务器也连得上) ----
    "minicode.mcp": ("Transport", "StdioTransport", "HttpTransport", "SseTransport",
                     "make_transport", "_iter_sse", "_safe_url", "_usable_url",
                     "_norm_transport"),
    # ---- v0.9.24:计划面板(漏收 = 顶栏「🗂 计划」点开永远说没计划) ----
    "minicode.planning": ("plan_state", "dispatch_text", "notify_plan"),
}


def _code_names(code) -> str:
    """把一个 code 对象(含嵌套)里所有标识符/常量拼成一个大字符串。"""
    chunks: list[str] = []
    stack = [code]
    while stack:
        c = stack.pop()
        chunks.append(repr(c.co_names))
        chunks.append(repr(c.co_varnames))
        chunks.append(repr(c.co_consts))
        stack.extend(x for x in c.co_consts if hasattr(x, "co_code"))
    return "\n".join(chunks)


def main() -> int:
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else
               r"dist\引路学长\引路学长-CLI.exe")
    if not exe.exists():
        print(f"找不到产物: {exe}")
        return 2
    from PyInstaller.archive.readers import CArchiveReader

    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if n.lower().endswith(".pyz")), None)
    if pyz_name is None:
        print(f"CArchive 里没有 PYZ,条目: {list(car.toc)[:10]}")
        return 2
    pyz = car.open_embedded_archive(pyz_name)     # 已定位好的子归档读取器

    failures: list[str] = []
    for mod, needles in TARGETS.items():
        if mod not in pyz.toc:
            print(f"  ✗ {mod} 不在 PYZ 里")
            failures.append(mod)
            continue
        code = pyz.extract(mod)
        haystack = _code_names(code)
        for needle in needles:
            ok = needle in haystack
            print(("  ✓ " if ok else "  ✗ ") + f"{mod}: {needle}")
            if not ok:
                failures.append(f"{mod}:{needle}")
    print()
    if failures:
        print("FAIL: 包内不是最新版代码 →", failures)
        return 1
    print(f"PASS: 包内后端代码包含 最新版(v{_src_version()})全部新增符号")
    return 0


if __name__ == "__main__":
    sys.exit(main())
