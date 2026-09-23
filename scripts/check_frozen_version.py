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
                            "get_plan",
                            # v0.9.30:「我的数据」面板 + 迁移包(漏收一条 = 装好的包里
                            # 点「数据」没反应;漏 _ask_open_path = 导入第 1 步选不了包)
                            "mydata_inventory", "mydata_export",
                            "mydata_import_pick", "mydata_import_apply",
                            "mydata_forget_import", "mydata_open_dir",
                            "_ask_open_path",
                            # v0.9.32:情绪面板五根线(漏一条 = 点了按钮没反应)
                            "mood_review", "mood_log", "mood_export",
                            "mood_clear", "mood_help",
                            # v0.9.34:右侧「本次会话」面板两根线(漏 get_activity =
                            # 点开一栏空白;漏 reveal_file = 产出行点不动,只能看)
                            "get_activity", "reveal_file",
                            # v0.9.37:任务监控栏的开合与折叠偏好(漏 get_monitor_pref =
                            # 每次开机都回到默认收起,「记住我的选择」这句成了空话;
                            # 漏 set_monitor_pref = 点段头有反应但下次全忘)
                            "get_monitor_pref", "set_monitor_pref"),
    # v0.9.34:活动面板的数据源整模块(漏收 = 装好的包右边那一栏永远"读不出来")
    # v0.9.37:这一栏改叫「任务监控」,多了此刻与时间线两段(漏 timeline =
    # 那一整段消失,用户仍然只能看到五组按类别堆的行,看不到"先做了什么、
    # 哪一步失败了";漏 now_line = 正在跑那一步永远不显示)
    "minicode.activity": ("session_activity", "GROUPS", "FAILED_PREFIXES",
                          "FILE_TOOLS", "URL_TOOLS", "QUERY_TOOLS",
                          "timeline", "now_line", "_turn_split", "_pair_times",
                          "TURN_LIMIT", "TURNS_LIMIT"),
    # v0.9.37:工具口径只有一份真相 —— 聊天里的工具卡片、右侧时间线、实时表三处
    # 都调它。漏收 = 三处措辞各自漂移(时间线里写「读取」,卡片里写「读文件」),
    # 而配对靠的是同一个 summarize 结果,漂了就再也对不上,行行都显示不了耗时。
    "minicode.toolsum": ("summarize", "verb", "short_name", "agent_type",
                         "SUBAGENT_PREFIX_SEP"),
    # v0.9.37:折叠偏好整模块(漏收 = 点段头直接抛异常,整栏打不开)
    "minicode.monitor": ("read_pref", "write_pref", "pref_path", "SECTIONS",
                         "DEFAULTS", "PREF_FILE", "_clean_folded"),
    # v0.9.37:实时耗时表(漏 live_rows = 时间线一行时刻都没有;漏 live_abandon =
    # 按了停止还在转圈,那一行永远装作在跑)
    "minicode.gui_ui": ("live_rows", "live_now", "live_abandon", "_live_begin",
                        "_live_end", "LIVE_CAP"),
    # v0.9.30:迁移包整模块是新文件,漏收 = 装好的包连面板都打不开
    "minicode.mydata": ("inventory", "pack_plan", "export_package", "read_package",
                        "import_pick", "import_apply", "forget_preview",
                        "deny_reason", "TABLE", "BACKUP_DIR", "MTIME_TOLERANCE",
                        "FORMAT_VERSION"),
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
                      "start_in_tray",
                      # v0.9.28:action=log 读看门狗运行日志(漏收 = 「提醒为什么
                      # 没发」在装好的包里永远只能靠猜)
                      "wd_log_tail", "watchdog_log_path"),
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
                          "calendar_prompt_section",
                          # v0.9.32:「每年重复」事件(漏收 = 心理普查那类年度事件不再投影)
                          "is_yearly", "expand_repeats", "_shift_year"),
    # v0.9.32:情绪日记。HELP_FACTS(给人看)/ HELP_CAVEATS(只对模型说)两段都得在 ——
    # 合并在一个常量里,面板就会把"绝不许编号码"这类内部规矩摆到用户面前。
    "minicode.mood": ("log", "weekly_review", "list_recent", "export_text", "clear_all",
                      "mood_prompt_section", "mood", "load_entries", "save_entries",
                      "CRISIS_WORDS", "HELP_FACTS", "HELP_CAVEATS", "HELP_BLOCK"),
    "minicode.gpa": ("gpa_calc",),
    "minicode.tools.crawl": ("web_search", "_resolve_redirect"),
    # ---- v0.9.15:上下文面板 + py_script ----
    "minicode.tools.shell": ("py_script", "_py_runtime_command"),
    "minicode.model_catalog": ("_context_breakdown", "vision_forced"),
    # ---- v0.9.17:截止提醒 ----
    "minicode.reminders": ("reminder", "add", "due_soon", "parse_due",
                           # v0.9.29:待办带钟点 → 到点前 20 分钟催(漏收 = 记进去了
                           # 但那条细轮询压根不看它,表现是"到点没反应")
                           "parse_clock", "parse_when", "upcoming_due",
                           "late_due", "DUE_LEAD_MINUTES", "LATE_WINDOW_MINUTES"),
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
                          "format_free_slots", "_week_overlay",
                          # v0.9.35:走堂预警接进工具/提示词/面板(漏收 = 装好的包
                          # 里 walk 模块在但没人调,问"赶不赶得及"仍靠模型心算)
                          "risk 的 when", "upcoming_lines", "walk_index_ready",
                          # v0.9.36:面板的处数与"本周最多那天"由后端一次算好(漏收 =
                          # 徽章回退成列表长度、周视图那句汇总干脆不出现)
                          "walk_total", "walk_week",
                          "walk_skipped"),
    "minicode.weather": ("weather_card", "umbrella_tip", "refresh_async",
                         "get_weather"),
    # ---- v0.9.19:拖拽上传(字节落盘,绕开 WebView2 拿不到本地路径) ----
    "minicode.attachments": ("save_dropped_bytes", "unique_target"),
    # ---- v0.9.35:走堂换楼预警整模块(漏收 = 装好的包里"要不要早点出发"
    #      全退回模型心算,分钟数/no_gap 口径全丢)----
    "minicode.walk": ("day_risks", "week_risks", "format_risks", "format_week_risks",
                      "upcoming_lines", "worst_item", "item_text", "place_of",
                      "adjacency", "campus_index", "_index", "_walks", "_clashes",
                      # v0.9.36:走堂第二步 —— 真实处数、出门前那一句、整周汇总
                      # (漏收 = 简报与周视图那两句退化成"列表有几行报几处")
                      "total_items", "brief_phrase", "week_summary",
                      "MAX_ITEMS", "MAX_SKIPPED", "_NO_INDEX"),
    # ---- v0.9.20:今日简报聚合 + 推送去重 ----
    "minicode.briefing": ("build", "_schedule_bits", "_weather_bits",
                          "_reminders_bits", "_calendar_bits",
                          # v0.9.36:早上的桌面通知要说得出"最早那一处是哪两栋"
                          "brief_phrase"),
    # ---- v0.9.16/17/20:主动推荐 + 新版本检查 + 今日简报(合并成一个键,
    #      避免 dict 重复键 last-wins 让早先的 needle 悄悄漏检) ----
    "minicode.proactive": ("check_once", "fetch_latest", "start_loop", "clean_title",
                           "check_update", "push_briefing", "start_tick_loop",
                           "calendar_alerts",
                           # v0.9.25:同一份推送同步发到桌面
                           "KIND_TITLES", "desktop_text", "push_desktop",
                           # v0.9.26:去重集落盘 + 开机补发(漏收 = 重启后
                           # "错过的那条永远不补、弹过的那条再弹一次")
                           "PUSHED_FILE", "pushed_path", "load_pushed",
                           "was_pushed", "mark_pushed",
                           "briefing_pushed_today", "catch_up",
                           # v0.9.28:校历/待办/今天的课 也走按天账本 + 看门狗运行日志
                           # (漏收 = 一天内重启就重复弹,且用户说"提醒没发"时
                           #  action=log 读出来永远是"还没有记录")
                           "_push_once", "_round", "_push_calendar",
                           "_push_reminders", "_push_today_class", "_note_payload",
                           # v0.9.29:课前那条细轮询泛化成"到点提醒器"(课前 + 待办)
                           "_push_class", "_push_due", "_tick",
                           "WATCHDOG_LOG", "watchdog_log_path",
                           "wd_log", "wd_log_tail"),
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
    # v0.9.33:对话流里的计划卡片靠 in_progress/running 画出"正在做哪一步"
    # (漏收 = 卡片只剩一堆待办和勾,转圈的那一行永远不出现)
    "minicode.planning": ("plan_state", "dispatch_text", "notify_plan",
                          "in_progress", "running"),
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
