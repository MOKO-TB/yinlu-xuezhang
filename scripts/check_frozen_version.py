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
                        "_dedupe_models", "_models_from_raw", "PROVIDER_PRESETS",
                        # v0.9.39:两条时限是真的在拦东西(漏收 = 配置文件里的数字没人读)
                        "turn_time_limit_seconds", "subagent_time_limit_seconds",
                        # v0.9.155 L:嵌套开关。漏收 = 包里**永远读不到它**,
                        # 于是用户在 config.json 里打开之后一切照旧、且没有任何声音
                        # (与"配置读了没人接"同一种坏法)。
                        "subagent_nesting",
                        # v0.9.95:按档位分模型(漏收 = config.json 里写了 subagent_models 没人读,
                        # 而报告还在说「本档模型 X」—— 那就是当着面编一句没依据的话)
                        "subagent_models",
                        # v0.9.141 出站策略(漏收 = config.json 里写了 net_policy
                        # 没人读,而模板与说明都宣称这三档存在)
                        "net_policy", "_net_policy_or_empty",
                        # v0.9.39 B0/B1:回灌开关(漏收 = config.json 里写了没人读)
                        "feedback_recall",
                        # v0.9.40 D2:回合步数上限(漏收 = 配置文件里的数字没人读)
                        "max_steps",
                        # v0.9.130 模型配置编辑器:漏收这两根 = 界面上填的 cfg 存不下来,
                        # 而保存照样回「已保存」—— 数值静默漂回探测与内置目录。
                        "model_cfg_from_raw", "MODEL_CFG_KEYS",
                        # v0.9.142 智能配置:让位的是整份声明,读的一只手必须换
                        # (漏 `effective_model_cfg` = 开关存进去了、界面灰掉了,
                        #  而运行时照读手填的那几项 —— 又一个样子货)
                        "effective_model_cfg",
                        # v0.9.133 P0-1:漏收 = config.json 里那枚开关没人读,
                        # 界面说明写「关掉就回到以前的行为」而实际关不掉 —— 一句假话
                        "read_before_edit"),
    "minicode.gui_bridge": ("test_model", "fetch_models",
                            # v0.9.142「智能配置」那一屏的数据源(漏收 = 前端那只开关
                            # 永远停在「正在问这台机器知道些什么…」,而界面看着正常)
                            "model_auto_info",
                            # v0.9.106 Q1:MCP 热重载换过注册表要重新对齐工具表
                            # (漏收 = 装好的包里那条「现有会话下一轮即可用」又是假的)
                            "refresh_registry",
                            # v0.9.101 ①R1:审查视图的三个门面(漏 undo_step/undo_all =
                            # 面板一行行列出来了却点不动;漏 undo_records = get_activity
                            # 里那份 changes 永远是空的,整段不出现)
                            "undo_records", "undo_step", "undo_all", "undo_diff",
                            # v0.9.101 ③B1:跨会话看板门面 + 等人答的那两个旗
                            "get_board", "_board_sessions", "_board_background",
                            "_board_plan", "_board_jobs", "_ask_open", "_confirm_open",
                            # v0.9.101 ④L3:侧栏指令层那一行的后端
                            "_instructions_info",
                            "export_session", "search_sessions",
                            # v0.9.138 侧栏会话菜单那三颗(漏 rename_session = 图形侧
                            # 还是改不了名,只有 CLI 能用 —— 正是用户报的那件事;
                            # 漏 archive_session = 菜单上那颗按下去没处回;
                            # 漏 copy_share_image = 📋 点了静默,图仍只在文件夹里)
                            "rename_session", "archive_session", "copy_share_image",
                            "rate_message", "get_countdowns",
                            "_provider_credentials",
                            # v0.9.22:按模型能力判定看图 + 写盘失败要说清 + 关窗答复
                            "_vision_on", "_persist_config", "set_vision",
                            "close_choice",
                            # v0.9.96:提问卡三件套 + 叫醒等待的那条路
                            # (漏收 = 前端点了没处回,或停止键叫不醒正在等的那条线程)
                            "ask_user", "ask_reply", "_ask_trace_text",
                            "abandon_pending", "_wait_event",
                            # v0.9.97:界面上记一条长期记忆(漏收 = 按钮点了没方法)
                            "remember",
                            # v0.9.23:教务课表导入 + 首次运行向导
                            "schedule_import_open", "schedule_import_read",
                            "schedule_import_model", "schedule_import_confirm",
                            # 2026-10-01:两根 *_import_status 的针跟着删了 —— 方法本身
                            # 是死代码(前端一次都没调,而开面板就把状态清空),详见 §6 #45
                            "schedule_import_clear",
                            "_jw_flow", "get_onboarding", "dismiss_onboarding",
                            # v0.9.59:成绩导入三步向导(漏收 = 面板按钮点了没反应,
                            # 或读到了成绩却永远写不进 scores.json)
                            "scores_import_open", "scores_import_read",
                            "scores_import_model", "scores_import_confirm",
                            "scores_import_clear",
                            "_scores_flow",
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
                            # v0.9.75:课表面板「导出到日历」(漏 export_ics =
                            # 按钮点了没反应,GUI 没有导出入口的老缺口回来了)
                            "export_ics",
                            # v0.9.37:任务监控栏的开合与折叠偏好(漏 get_monitor_pref =
                            # 每次开机都回到默认收起,「记住我的选择」这句成了空话;
                            # 漏 set_monitor_pref = 点段头有反应但下次全忘)
                            "get_monitor_pref", "set_monitor_pref",
                            # v0.9.39 p1:右键粘贴走本机剪贴板(漏 read_clipboard =
                            # 前端那句"桥在不在"的判定永远为假,静默退回浏览器授权条,
                            # 弹条这个 bug 装了包还在);get_skill_body 是同一轮补的
                            # 漏转发(桥里有、门面没接 → 桌面版点技能卡片只有 TypeError)
                            "read_clipboard", "get_skill_body",
                            # v0.9.117:用量示意改版门面(漏收 = 桌面版点开
                            # 用量弹窗只有 TypeError,老圆环图已经退休)
                            "get_usage_dashboard",
                            # v0.9.39 p2:标题栏染色 + 改字(漏 set_chrome = 深色界面顶上
                            # 仍顶着一条白标题栏,用户点名的就是那一条;开关都不通,
                            # 前端只会静默什么也不做)
                            "set_chrome",
                            # v0.9.69:恢复会话的附件标签(漏收 = 切回旧会话时
                            # resumed 载荷永远不带 atts,缩略图一个都不出现)
                            "restore_user_atts",
                            # v0.9.97:界面上记一条长期记忆(漏收 = 按钮点了没方法)
                            "remember",
                            # v0.9.97 原钉的 get_health/_as_bool 随「体检」功能删除
                            # (v0.9.100),health.report 的针移到 minicode.health 组
                            # v0.9.111 p9/p12:环境信息那一屏的门面与那条「列与读同一个数」
                            # (漏 get_env_info = 界面那一节永远不出现;漏 SESSION_LIST_LIMIT
                            # = 侧栏列 50 条而点开只认前 10 条 —— 第 11 条以后「看得见但点不动」,
                            #   这一条是修切换卡顿时顺手挖出来的真缺陷)
                            "get_env_info", "invalidate_env_info", "SESSION_LIST_LIMIT",
                            # v0.9.112:终端面板与自动化面板的三个门面(漏 get_terminal
                            # = 那一屏永远空;漏 set_job_enabled = 开关点了没处去)
                            "get_terminal", "get_automation", "set_job_enabled",
                            # v0.9.113:无边框那五颗门面 + 课表面板里的成绩读数
                            # (漏 win_close = 无边框时用户没有任何办法关窗;
                            #  漏 get_score_stats = 「成绩」那一档永远在读不到)
                            "win_state", "win_minimize", "win_zoom", "win_close",
                            "win_resize", "get_score_stats",
                            # v0.9.114:自动化新建/删除、计划核对单、技能分享包、
                            # 托盘那叠最近会话的取法
                            "add_automation", "remove_automation", "plan_review",
                            "plan_review_reply", "_plan_review_trace",
                            "export_skill_pack", "import_skill_pack",
                            "apply_skill_pack", "recent_for_tray"
                            ),
    # v0.9.39 p2:标题栏整模块。漏收 = 桥里那句 `from minicode import window_chrome`
    # 在装好的包里 ImportError,而它被前端那个 try/catch 吞掉 → 表现为"改了个寂寞"。
    "minicode.window_chrome": ("apply", "chrome_for", "blend", "colorref",
                               "IMMERSIVE_DARK_MODE", "CAPTION_COLOR",
                               "TEXT_COLOR", "BORDER_COLOR",
                               # v0.9.112 p4:pywebview 每回都在写的 38 也要跟着应用
                               # 主题写(漏收 = 深色界面顶上还是系统那条白)
                               "TITLEBAR_MODE", "TITLEBAR_DARK", "TITLEBAR_LIGHT",
                            # v0.9.112 p7:开机静默子进程的模态错误框(漏收 = 弹窗又回到用户脸上)。
                            "silence_child_error_dialogs",
                            # v0.9.113 p5/p6/p7:无边框之后补回来的三件 —— 最大化让出
                            # 任务栏、边缘缩放由界面发起、"到底有没有标题栏"读样式位。
                            # 漏收 = 装好的包里无边框窗盖住任务栏、八条边拖不动。
                            "maximize_bounds", "work_area", "window_rect",
                            "_apply_edge", "resize_edge", "EDGES",
                            "style_of", "is_borderless", "is_maximized"),
    # v0.9.39 p1:剪贴板整模块。漏收 = 装好的包里右键粘贴永远走不到本机这条,
    # 那条「xx 想要查看复制到剪贴板的文本和图像」的横条照旧弹(功能"看起来在",
    # 只是每次都违和),所以连 bmp_from_dib 一起钉 —— 它是图片那半的唯一出口。
    "minicode.clipboard": ("read_clipboard", "bmp_from_dib", "dib_to_file",
                           # v0.9.138 写方向(分享图一键复制)。漏 png_to_dib = 图根本
                           # 转不成位图,那颗 📋 永远回一句失败;漏 write_image = 桥上有
                           # 方法、剪贴板那条路是空的(源码态全绿,装好的包里点不动)。
                           "png_to_dib", "write_image", "GMEM_MOVEABLE",
                           "CF_UNICODETEXT", "CF_DIB", "TEXT", "IMAGE",
                           "EMPTY", "UNAVAILABLE"),
    # v0.9.34:活动面板的数据源整模块(漏收 = 装好的包右边那一栏永远"读不出来")
    # v0.9.37:这一栏改叫「任务监控」,多了此刻与时间线两段(漏 timeline =
    # 那一整段消失,用户仍然只能看到五组按类别堆的行,看不到"先做了什么、
    # 哪一步失败了";漏 now_line = 正在跑那一步永远不显示)
    "minicode.activity": ("session_activity", "GROUPS", "FAILED_PREFIXES",
                          "FILE_TOOLS", "URL_TOOLS", "QUERY_TOOLS",
                          "timeline", "now_line", "_turn_split", "_pair_times",
                          "TURN_LIMIT", "TURNS_LIMIT",
                          # v0.9.81:子代理的联网动作进「网页查阅」栏(与 v0.9.76
                          # 的产出尾注同一套机制;漏收 = 那一栏对子代理永远空)
                          "_web_row", "_WEB_NOTE_LINE", "_WEB_VERBS",
                          # v0.9.92:父行下面挂的子步骤(漏 _children = 时间线里那一层
                          # 整块消失而面板不报错;漏 _dur_text = 时长出现第二份格式化)
                          "_children", "_dur_text",
                          # 出包对账挖出来的欠账(v0.9.76 当时就没进针表):子代理那份
                          # 报告开头的 [files|档位] 尾注要有人解析,产出才进得了面板。
                          # 漏 _FILE_TOOLS_LINE = 正则没了、行行都不认;漏 _extract_files
                          # = 认了解析不收 —— 两种表现都是「装好的包里子代理写的文件
                          # 不出现在产出栏」,而不报错。
                          "_FILE_TOOLS_LINE", "_extract_files",
                          # v0.9.114 观测线(W1/W4/W7/W8/W9):重复失败探测(漏
                          # findings = 同款调用连挂 3 次面板一声不吭,止损闸停了
                          # 也没人看见;漏 call_signature = 探测器与止损闸各写
                          # 一份"同样",迟早漂成「闸停了卡没立」)、×N 归类
                          # (漏 _group_rows = 同款调用一行行排几百 KB)、每轮
                          # 思考段(漏 REASONING_JOIN_CLIP = 各步拼接不再整体
                          # 截断,几十 KB 思考撑爆载荷)、同批标记(漏 batch =
                          # 模型一轮同时要的几步看不出是一批下发的)
                          "findings", "call_signature", "_group_rows",
                          "_group_member", "FAIL_CHAIN_MIN", "FINDINGS_LIMIT",
                          "CHAIN_KEEP", "FINDING_PREVIEW", "REASONING_JOIN_CLIP"),
    # v0.9.37:工具口径只有一份真相 —— 聊天里的工具卡片、右侧时间线、实时表三处
    # 都调它。漏收 = 三处措辞各自漂移(时间线里写「读取」,卡片里写「读文件」),
    # 而配对靠的是同一个 summarize 结果,漂了就再也对不上,行行都显示不了耗时。
    # v0.9.39 Z1:这里还多了"这一步成没成"的唯一口径(漏 is_failure = 终端 / 工具卡片 /
    # 监控栏三处又各自写一份前缀判断,而这三处一致是"不能只当装饰"的硬要求)
    "minicode.toolsum": ("summarize", "verb", "short_name", "agent_type",
                         "SUBAGENT_PREFIX_SEP", "FAILED_PREFIXES", "ERROR_PREVIEW",
                         "is_failure", "error_preview"),
    # v0.9.37:折叠偏好整模块(漏收 = 点段头直接抛异常,整栏打不开)
    # v0.9.101 ③B1:跨会话看板。漏 aggregate = 那一屏根本没有后端;
    # 漏 BOUNDARY_NOTE/scan_note = "在跑"变成了一句没有边界的断言(它在桥内存里,
    # 跨进程看不见),而这一屏最容易骗人的就是"没读到"被画成"没有任务"。
    "minicode.board": ("aggregate", "row", "_row", "ROW_LIMIT", "SESSION_SCAN",
                       "STATES", "BOUNDARY_NOTE"),
    # v0.9.103 ①R4:终端那侧的动手面。漏 confirm_destructive = 包里的 CLI 退文件前
    # 一声不问(磁盘上的东西直接被换掉);漏 SLASH_COMMANDS = 补全与"未知命令"那句
    # 仍是各抄一份的旧版,新命令 /instructions 打得出前半截却不补全。
    "minicode.ui": ("confirm_destructive", "SLASH_COMMANDS", "help_text",
                    "confirm_tool", "ask_user"),
    "minicode.monitor": ("read_pref", "write_pref", "pref_path", "SECTIONS",
                         "DEFAULTS", "PREF_FILE", "_clean_folded"),
    # v0.9.37:实时耗时表(漏 live_rows = 时间线一行时刻都没有;漏 live_abandon =
    # 按了停止还在转圈,那一行永远装作在跑)
    "minicode.gui_ui": ("confirm_tool",  # v0.9.60:label 参数要能传到 handler
                        # v0.9.114:核对单在界面适配器上的那个方法
                        # (漏收 = 工具侧 getattr 拿不到,静默不弹卡)
                        "plan_review", "plan_review_handler",
                        "live_rows", "live_now", "live_abandon", "_live_begin",
                        "_live_end", "LIVE_CAP",
                        # v0.9.139 流式增量攒批(漏收 = 包里还是"每个增量一次
                        # evaluate_js"那一版:往返占掉 100% 的时间,用户点开的
                        # 装好的包继续卡,而开发态跑得动 —— 这类坏法不报错)
                        "flush", "_queue_locked", "_due_locked", "_flush_locked",
                        "_send_locked", "_timer_flush",
                        "COALESCED_EVENTS", "FLUSH_MS", "FLUSH_CHARS",
                        # v0.9.90:长命令的实时那一行(漏收 = bash 那边 getattr 拿不到方法,
                        # 进度静默没有 —— 而这一轮的卖点恰恰是"中间那段不再是黑的")
                        "tool_progress",
                        # v0.9.96:提问卡(漏收 = 工具在、界面没这条方法,ask_user 只能
                        # 回一句"这个界面没接提问通道" —— 功能像坏了,其实是包缺一层)
                        "ask_user",
                        # v0.9.92:并行子代理内部每一步的记账通道(漏收 = 批量模式又变回
                        # 黑箱:聊天区安静是设计,但监控栏连"哪一路在跑什么、哪一步挂了"
                        # 都没了,而这种静默失灵一句报错都不会有)
                        "live_child_begin", "live_child_end", "live_child_progress",
                        "CHILD_CAP",
                        # v0.9.39:一轮总时限到点要说得出声(漏收 = 停下时界面一个字也不显示,
                        # 用户只看到"它不动了",以为卡死)
                        "turn_time_limited",
                        # v0.9.77:失败连击止损要说得出声(同上)
                        "fail_streak_limit",
                        # v0.9.78:成本闸门到点要说得出声(同上)
                        "token_limit_reached"),
    # v0.9.30:迁移包整模块是新文件,漏收 = 装好的包连面板都打不开
    "minicode.mydata": ("inventory", "pack_plan", "export_package", "read_package",
                        "import_pick", "import_apply", "forget_preview",
                        "deny_reason", "TABLE", "BACKUP_DIR", "MTIME_TOLERANCE",
                        "FORMAT_VERSION"),
    # v0.9.89:体检模块(**v0.9.100 起只剩 report 这一个消费者** —— 面板/CLI 已删,
    # 反馈包 export_feedback 靠它取浅检数据;BADGE/GROUPS/render_text/main 随 UI 删)
    "minicode.health": ("report", "GATE_SCRIPTS", "_verdict",
                        "_check_gates", "_run_gate"),
    "minicode.model_probe": ("list_models", "explain_status", "remember"),
    # ---- v0.9.100:工具钩子(漏收 = 装好的包里 pre 拦截/post 附行静默消失,
    # 用户配了钩子以为在审计,其实什么都没跑) ----
    # v0.9.115 H1:漏 matched 的后果比漏 run_hook 更隐蔽 —— 红线预检拿不到"有没有
    # 钩子认领这一步",异常被兜成 False,于是**整条钩子一步都不跑**,而 config 里明明写着。
    "minicode.hooks": ("run_hook", "_render", "HOOK_TIMEOUT", "matched",
                       "EXIT_DENY", "HOOK_TIMEOUT_MAX",
                       # v0.9.115 H4:非工具类事件那张名单(漏收 = `deny 没生效` 那句
                       # 提醒不再说出,用户配了拒绝钩子却拦住任何东西都没有)
                       "EVENT_KINDS"),

    # ---- v0.9.120:无头模式(`--json`)----
    # 漏 HeadlessUI / EXIT_ASK_REFUSED 的表现不是报错,而是**装好的包里那条路上
    # 没人替你把确认挡住**:同一份 CLI 在源码里会拒,在包里悄悄放行 —— 换入口
    # 不该换权限,这条判断只能有一处。漏 _brief 则是反过来的坏法:工具参数正文
    # (可能是同学的课件原文或凭据)整包进那份常被贴进 CI 日志的输出。
    # v0.9.126:`headless_flags_in` 是图形入口那道闸唯一的判定来源(漏收 =
    # GUI 又回到"给了 --json 也静默开一个永不退出的窗口")
    # v0.9.131 改动前像落盘:漏收的表现是"重启后清单空了"这件本该被修好的事
    # 又回来了,而且没人报错 —— 读不懂的账本一律当没有,是设计而不是故障。
    "minicode.checkpoints": ("encode_stack", "decode_stack", "save", "load",
                             "sweep_orphans", "drop", "CKPT_MAX_FILE_BYTES",
                             "CKPT_MAX_RECORDS", "CKPT_VERSION"),
    "minicode.headless": ("HeadlessUI", "pop_output_flags", "exit_code_for",
                          "headless_flags_in",
                          "build_result", "render", "status_for", "_brief",
                          "OUTPUT_FORMATS", "EXIT_ASK_REFUSED", "UsageError",
                          # v0.9.136 --output-schema:漏 `pop_output_schema` = 这个旗标
                          # 会被当成一次性任务的一部分(它进的是 argv),漏
                          # `EXIT_SCHEMA_VIOLATION` = 不合 schema 与"做成了"共用退出码 0,
                          # CI 就永远看不出那次拿到的不是结构化结果
                          # v0.9.137:漏 `llm_failure` 说明包里还是那版"记录字段与
                          # `llm_error` 回调同名"的 headless —— 模型一报错,stdout 那份
                          # JSON 整个消失,而退出码 1 恰好与文档里的运行时码同数,
                          # 只看号码一切正常,坏的是载荷。
                          "pop_output_schema", "EXIT_SCHEMA_VIOLATION",
                          "SCHEMA_FLAG", "EXIT_INTERRUPTED", "EXIT_LIMITED",
                          "llm_failure",
                          # v0.9.144 --output-format stream-json:漏 `EventStream` =
                          # 装好的包里那三种写法里少了事件流这一条(旗标当场报错),
                          # 而 README/《使用说明》已经在教它怎么用。
                          "EventStream"),
    # v0.9.136 结构化输出这一格的三件事:读 schema、要 schema、验 schema。
    # 漏 `load` = 旗标能传但没人核对形状(什么怪形状都会流到模型面前);
    # 漏 `validate` = "不合 schema 就明说"这句承诺没了;漏 `extract` = 代码围栏里
    # 那份正确的 JSON 会被判成"回答里找不到 JSON",而回答明明是对的。
    "minicode.schema": ("load", "validate", "extract", "instruction",
                        "SchemaError", "SUPPORTED", "IGNORABLE", "TYPES"),

    # ---- v0.9.22:安装版可用性(数据目录写探针 + 诊断下发) ----
    "minicode.app_setup": (
        # v0.9.100 F1 + v0.9.101 ④L1/L3:两级指令文件。漏 user_agents_prompt_section =
        # 装好的包里用户写的那份规矩根本不进提示词(源码态一切正常,最典型的一种漂);
        # 漏 instruction_layers/instructions_line = 侧栏那一行永远空着,"读没读到"又变回扯皮。
        "build_system_prompt", "agents_prompt_section", "user_agents_prompt_section",
        "instructions_prompt_section", "instruction_layers", "instructions_line",
        # v0.9.153 J:根那一层从"一份文件"变成"一组来源"(`AGENTS.md` + `.cursorrules`
        # + `.clinerules` + `.cursor/rules/*.mdc`),取层与合并搬进了这两支 —— 漏它们 =
        # 装好的包里那些名字**一份都不认**(用户从 Cursor / Cline 带来的规矩静默消失,
        # 而源码态一切正常)。`project_agents_path` 随之**删掉**:它原先唯一的调用点是
        # `/instructions` 报路径,现在一个调用点都没有了 —— 而针只该钉"丢了会坏的东西",
        # 钉一个没人调用的函数是假闸。
        "_root_sources", "_combined_root_found",
        "COMPAT_RULE_FILES", "CURSOR_RULE_DIR", "CURSOR_RULE_SUFFIXES",
        "_read_instruction", "_instruction_body",
        # v0.9.127 §四.2「首次采纳点头」:漏 `agents_adoption` = 装好的包里没人知道
        # 这份项目文件采没采纳(那一段就静默不注入,而侧栏照旧报"项目级 37 行");
        # 漏 `adopt_agents` = 点了头也没处记,每一问都重弹一张卡。
        "agents_adoption", "adopt_agents",
        # v0.9.149 指令文件第三级(子目录就近 AGENTS.md)。漏 `subdir_agents_layers` =
        # 装好的包里那一层永远取不到(表现是"我在子目录写了规矩,它跟没看见一样",
        # 而源码态一切正常);漏 `layer_prompt_section` = 取到了也没人拼那一段;
        # 漏 `adopt_layer`/`layer_adopted` = 子目录那一层点不了头,而"每层各自采纳"
        # 正是这一层在安全上唯一的严格之处 —— 它静默失效等于把那次点头省掉了。
        "subdir_agents_layers", "layer_at", "layer_prompt_section",
        "adopt_layer", "layer_adopted", "_rel_dir",
        "INSTRUCTION_GUARD", "USER_AGENTS_NAME", "AGENTS_CHAR_LIMIT", "AGENTS_FILES",
        "apply_retention"),
    "minicode.paths": ("_is_writable", "_local_state_dir", "diagnostics",
                       "STATE_BACKEND"),
    # ---- v0.9.22:托盘 + 关窗确认(整套新模块,漏收 = X 又变成直接退出) ----
    "minicode.tray": (
        # 2026-10-01:界面警告日志从此有人读、也终于有盖(漏收 = 同学说「右键粘贴不出来」
        # 时学长查不到证据;或那份裸日志在用户机器上无限长下去)
        "warn_log_tail", "WARN_LOG_FILE", "cap_log",
        # v0.9.98:自启「位置变了」探针/修复/启动核对(漏收 = 重装挪位后
        # 勾看着是开、开机不响,而谁也说不清为什么)
        "startup_stale", "repair_startup_shortcut", "check_stale_startup",
        "install", "close_choice", "read_pref", "write_pref",
        # v0.9.114 补刀:「最近会话」那一项的点击动作工厂。漏收的代价不是"少一根针"
        # 而是"这轮那个害人的写法又回来了":pystray 只收 0/1/2 个位置参数的 callable,
        # 3 个直接 ValueError → 整颗图标建不起来 → 同学点「最小化到托盘」= 程序没了。
        "_recent_opener",
                      "TrayController", "hide_to_tray", "_ensure_icon",
                      "_ensure_parent",
                      # v0.9.25:桌面提醒(漏收 = 最小化后课前提醒彻底没声)
                      "notify", "notify_status", "read_notify_pref",
                      "write_notify_pref", "notify_enabled", "set_notify",
                      "desktop_notify", "KIND_LABELS", "NOTIFY_PREF",
                      # v0.9.57 A4:类别表本身也要在包里(漏收 = review/gaps
                      # 两类在装好的包里关不掉,set_notify 把它们当未知丢掉)
                      "NOTIFY_KINDS",
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
                      "wd_log_tail", "watchdog_log_path",
                      # v0.9.114 图 p8/p9:托盘菜单照 Qoder 重排(漏 _build_menu =
                      # 那三颗新按钮整个不在;漏 set_recent_provider = 桥交不进来的
                      # 取法,「最近会话」那一叠永远不出现)
                      "_build_menu", "_recent_sessions", "_tray_open",
                      "_refresh_menu", "set_recent_provider", "MENU_RECENT"),
    # v0.9.72:通知点击直达整模块(漏收 = 装好的包里点通知没反应、协议注册
    # 不发生、激活监听不起来 —— 用户看到的还是"通知只有字,点了等于没点")
    "minicode.activate": ("KIND_PANELS", "panel_uri", "parse_uri",
                          "register_protocol", "handoff", "start_listener",
                          "pending_uri_arg", "take_pending_panel"),
    "minicode.reasoning": ("reasoning_snapshot", "get_capability",
                           "apply_reasoning_choice",
                           # v0.9.130:声明优先于内置表那两层(漏收 = 🧠 按钮又开始只认模型名)
                           "declared_capability", "_render_template"),
    "minicode.session": ("to_markdown", "search_logs", "export_filename",
                         # v0.9.126 改名/归档/标题一处真相(漏 `display_title` =
                         # 手改的名字在某个入口不生效,同一条会话顶两个名字;漏 `patch_start_meta` =
                         # 改个名字能把留档改坏)
                         "display_title", "current_title", "set_title",
                         "set_archived", "patch_start_meta", "normalize_title",
                         # v0.9.40 D0:跨天重建提示词要真换得掉 messages[0]
                         "refresh_system_prompt",
                         # 跨会话记忆:模型侧工具与 CLI /find 共用这一份取数,
                         # 漏收 = 装好的包里学长仍然记不住上次聊过什么
                         "history_hits", "search_history",
                         # v0.9.105 「第几问」这个坐标的算法只有一份(漏收 = 装好的包里
                         # 编辑重发与 /rewind 各数一遍,数错一格退的就是别人那几问的文件)
                         "user_message_at",
                         # v0.9.105 「第几问」这个坐标的算法只有一份(漏收 = 装好的包里
                         # 编辑重发与 /rewind 各数一遍,数错一格退的就是别人那几问的文件)
                         "user_message_at",
                         # 崩溃/强关留下的悬空 tool_calls:漏收 = 装好的包里恢复一次
                         # 中断过的会话,以后每一轮都被端点 400
                         "repair_tool_pairs"

                           # v0.9.98:运行轨迹导出(漏收 = 装好的包里报障拿不出流水)
                           , "export_trace", "export_trace_tool",
                           # v0.9.114 W7:思考摘要进留档(漏 REASONING_CLIP = 几十 KB
                           # 思考原文撑爆留档;漏 LOCAL_KEYS = 出口剥不干净,严格
                           # 端点对未知字段报 400,"下一轮"莫名 400 回溯不到这轮)
                           "REASONING_CLIP", "LOCAL_KEYS"),
    # v0.9.40 D1/D2:压缩保头+尾、工具 schema 进估算(漏收 = 装好的包里这两条退化
    # 回"保尾丢头"和"只数消息"的旧尺子,而界面看不出来)
    # v0.9.106 Q2:占用明细那一处真相搬进 compaction(面板与命令行读同一份);
    # 漏收 = 装好的包里 /history 又退回"只有一个总数"的老样子
    "minicode.compaction": ("clip_history", "schema_tokens", "HISTORY_HEAD_CAP",
                            "context_breakdown",
                            # v0.9.120 microcompact:漏收的表现不是报错,而是装好的包里
                            # 长对话又变回"每一步整份重发旧工具结果"(省字那笔账静默失效),
                            # 以及那三个数漂了没人对账。
                            "microcompact", "micro_placeholder",
                            "tool_names_by_call", "MICRO_KEEP_RECENT",
                            "MICRO_MIN_CHARS", "MICRO_FLOOR_TOKENS",
                            # v0.9.121「记忆候选」那根线:漏收的表现是摘要里又只
                            # 留下那行字没人接 —— 候选产出了,主对话却不知道拿它办
                            "_summary_message", "MEMORY_CANDIDATE_MARK"),
    # v0.9.121 记忆注入改按条让位(漏收 = 装好的包里又回到"按字符硬切":
    # 半条记忆当完整句子用,而且整块丢掉项目级那批新的)
    # v0.9.126 并发资格表(漏收 = 包内没人知道哪几只工具能同跑,或更糟:默认全放)
    "minicode.tools.concurrency": ("CONCURRENT_SAFE", "mark_concurrent"),
    "minicode.memory": ("prompt_section", "_fit_levels", "inject_line",
                        "MEMORY_CHAR_LIMIT"),
    "minicode.skills": ("SKILL_CHANNELS", "describe_channels", "create_skill",
                        # v0.9.63:技能使用账(漏收 = 装好的包里用了技能不记账,
                        # 「哪些技能真有人用」永远没有答案)
                        "log_skill_use", "usage_summary", "USAGE_FILE",
                        # v0.9.69:使用账并进技能面板(漏收 = get_skills 永远
                        # 不带 uses/last,面板上「用过 N 次」一行都不出现)
                        "merge_usage",
                        # v0.9.109:账带上「这次核对缺什么」+ 派档前那句的措辞
                        # (漏收 usage_gaps = 派出去之前那句「本机还缺」永远不出现,
                        #  而警语只剩子代理自己撞上的那一半)
                        "usage_gaps", "gap_note"),
    "minicode.web_profile": ("网页版不开放会话导出",
                             # v0.9.113:成绩那一屏与窗口控件对访客整组拒绝(漏了就是
                             # 网页版能把主人的服务器窗口最小化/关掉、看见主人的成绩单)
                             "网页版不开放成绩导入与「我的成绩」",
                             "网页版不开放窗口控件", "get_score_stats", "win_resize",
                             # v0.9.114:新建/删除定时任务、批准学长的计划、
                             # 往服务器技能目录里写别人发来的技能
                             "网页版不开放新建/删除定时任务", "plan_review_reply",
                             "网页版不开放技能分享包", "add_automation",
                             "apply_skill_pack",
                             "search_sessions",
                             # v0.9.23:教务导入需要本机浏览器窗口,网页版必须挡掉
                             "教务课表导入",
                             # v0.9.25:桌面提醒开的是本机通知,网页版同样挡掉
                             # (钉住 toast 字样 = 挡话是改道后那版,不是旧气球版)
                             "桌面提醒", "WinRT toast"),
    # ---- v0.9.14:知识库成长闭环 + 校园场景小工具 ----
    # v0.9.21:候选审核面板(list_candidates/review_candidate/_merge_into_kb)
    "minicode.growth": ("log_kb_gap", "save_candidate", "log_feedback", "kb_suggest",
                        "list_candidates", "review_candidate", "_merge_into_kb",
                        "REVIEWED_KB_FILE",
                        # v0.9.39 B0/B1:归一化与时间窗是"是不是同一个词/还在不在窗口里"
                        # 的唯一口径(漏收 = 回灌按自己的规矩另判一套)
                        "normalize_query", "within_days", "FEEDBACK_FILE",
                        # v0.9.57 A4:缺口周报的聚合器(漏收 = 周五那条永远
                        # "这周没有缺口",而界面一切正常)
                        "gap_digest",
                        # v0.9.80:检索命中账与逐条命中率(漏收 = 装好的包里
                        # 评价永远绑不到条目,命中率账本永远是空的)
                        "HITS_FILE", "log_kb_hits", "last_hits_since",
                        "kb_appraisal"),
    # v0.9.39 B0/B1:把已经记下来的信号回头用起来(反复搜空 / 差评避让)。
    # 漏收的表现是"装了包之后,连着搜空不再提示、历史差评不再影响答法",而界面一切正常。
    "minicode.feedback_recall": ("empty_hits", "empty_hits_by_call",
                                 "repeat_empty_queries", "repeat_note",
                                 "recent_low_rated", "match_low_rated",
                                 "avoidance_hint", "REPEAT_ASK_N", "MARK_FROM",
                                 "MATCH_DAYS"),
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
    # v0.9.101 ③B1:bg_facts = 看板「后台命令」那几行的真源(漏收 = 那一栏永远空,
    # 而 bash_status 的人话文本还在,最容易写成"两边各数一遍");
    # v0.9.101 ②V1:note_verify_run 的调用点在这里(漏收 = 验收账本永远空,
    # 带判据的步骤从此标不上 done)。
    # v0.9.102 之后补:后台跑完那一刻补进验收账(漏 _bg_note_verify = 慢验收永远
    # 标不了 done;漏 raw = 拿截成 120 字那份去比对判据,永远对不上)
    "minicode.win_sandbox": ("Job", "Limits", "create", "attach", "close",
                             "DEFAULT_MEMORY_BYTES", "DEFAULT_ACTIVE_PROCESS",
                             "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
                             "JOB_OBJECT_LIMIT_PROCESS_MEMORY",
                             "JOB_OBJECT_LIMIT_ACTIVE_PROCESS",
                             "JobObjectExtendedLimitInformation", "_kern",
                             "_JOBOBJECT_EXTENDED_LIMIT_INFORMATION", "_IS_WIN"),
    # v0.9.135 P0-5-S5a Job Object(整棵进程树可回收 + 资源上限)。漏 `_new_job` =
    # 三层挂接全断,`bash_kill` 回到只能靠 taskkill 数;漏 `_job_tail`/`_job_status_line`
    # = 未启用时没人说"未启用",而回话里那句"整棵进程树已随这次终止一起回收"
    # 就成了当着面说的一句假话。
    "minicode.tools.shell": ("bg_facts", "_bg_note_verify", "raw", "recorded",
                             "_new_job", "_job_tail", "_job_status_line",
                             # v0.9.104:跑完那一刻的回执(漏收 `_bg_notify_done` =
                             # 装好的包里后台命令跑完照样没人吭声,而描述已经在教模型
                             # "会弹一条通知" —— 那就是当着面说一句做不到的话;
                             # 漏 BG_NOTIFY_KIND 更坏:那一类不在开关表里,用户关不掉)
                             "_bg_notify_done", "BG_NOTIFY_KIND",
                             "notified",
                             "py_script", "_py_runtime_command",
    # v0.9.84:后台命令(漏收 = `bash background=true` 在装好的包里静默变回同步等,
    # 长任务照样 300 秒被砍,而工具描述还在教模型用后台)
    "bash_status", "bash_kill", "_BgJob", "_bg_start", "_bg_kill_tree",
    "_bg_shutdown", "MAX_BG_JOBS",
    # v0.9.146:给后台命令喂输入(漏收 `bash_input` = 装好的包里那只工具没了,
    # 而工具描述还在教模型"只有后台任务能用 bash_input 往里写";
    # 漏 `stdin_lock` 更坏:它唯一的用途是把"管道写满"挡在 `job.lock` 之外 ——
    # 少了它写线程会与读线程抢同一把锁,表现是**偶发死锁**,不是一条报错)
    "bash_input", "stdin_lock", "BG_INPUT_MAX_CHARS", "BG_INPUT_WAIT_MS",
    "BG_INPUT_WAIT_MAX", "BG_INPUT_JOIN_S",
    # v0.9.85:状态注入(漏收 = 模型只能自己想起来轮询,后台跑完了也没人告诉它,
    # 于是它会替用户宣布一个还没发生的结果)
    "bg_brief_for_model", "BG_KEEP_FINISHED", "_bg_prune_locked",
    # v0.9.90:前台命令边跑边读 + 进度上报(漏收 = 装好的包里长命令中间又是黑的,
    # 而工具描述与 HANDOFF 都写着"每 15 秒报一行";_drain 漏了更坏:回到只读一条管道,
    # 两路都写满时子进程直接卡死在那条命令上)
    "PROGRESS_EVERY", "_Progress", "_drain", "_report_progress",
                            # v0.9.112「终端」面板:后台命令 + 输出末尾(漏收 = 面板永远空)。
                            "terminal_facts"),
    # ---- v0.9.147:编辑后诊断回灌 ----
    # 漏收 `diagnostics` = 装好的包里"改完知道对不对"退回只有 Python 语法底线,
    # 而工具描述已经在教模型它认得六种扩展名;漏 `CHECKERS` 更坏:那一格变成
    # "没有这个类型的检查器",而回话看着像个正常结论 —— 「没做检查」被读成
    # 「检查过了没问题」正是这一路最贵的坏法。
    "minicode.diagnostics": ("diagnostics", "checker_for", "run_checker",
                             "fingerprint", "DiagnosisCache", "CHECKERS",
                             "CHECK_TIMEOUT", "SYNTAX_CHECK_TOOLS",
                             "SYNTAX_SCAN_LIMIT"),
    # ---- v0.9.39 #84:注册表按领域拆成 6 份目录(registry.py 只管装配顺序)----
    # 漏收一份 = 装好的包里**那个领域的工具整组消失**,而 build_default_registry()
    # 不报错(它只是拼不出那一段),模型侧表现是"这个能力没有了"。
    "minicode.tools.registry": ("build_default_registry",
                                # v0.9.154:漏 = 注册表不装配那一份目录,工具静默少一只
                                "catalog_sub"),
    # v0.9.110:工具 schema 的并集类型收平在**唯一出口**上 —— 这条针漏了,
    # 包里就还是那份会把 LiteLLM 类网关打成 400 的旧 schema。
    "minicode.tools.base": ("_flatten_schema_types", "_pick_type", "PathOutsideWorkspace",
                            # v0.9.154 I:这个字段就是"子代理有没有人可转达"的开关。
                            # 漏收 = 工具侧 `getattr(ctx, "notify_parent", None)` 永远是
                            # None,主会话自己调会拿到"你就是主会话"那句**本意正确、
                            # 此处错**的回话 —— 而子代理那边同样一句。
                            "notify_parent",
                            # v0.9.155 L:两个跨层字段。漏 `subagent_depth` = 包里
                            # "到顶了没有"永远读到 0(嵌套会一直往下派);漏
                            # `batch_locks` = 文件占用表跨不了层(§6 63 ①)。
                            "subagent_depth", "batch_locks"),
    "minicode.tools.catalog_files": ("tools", "perms_tools",
                         # v0.9.98:worktree 只读工具(漏收 = 模型看不到,
                         # "再开一份工作"只剩嘴上说)
                         "worktree_tools",
                         # v0.9.116 L3:钩子配方两只(漏收 = 工具描述里许诺着"有现成
                         # 配方可以挂",包里根本没有)
                         "hooks_tools"),
    "minicode.tools.fs": ("repo_outline", "write_file", "edit_file",
                          # v0.9.116 #49a:行尾与编码保真那几只手。漏 `_dominant_eol`
                          # 或 `_write_exact` 的表现最阴:CRLF/GBK 文件照写不误,只是
                          # 又变回"整份转 UTF-8/LF"那个被抱怨的老毛病。
                          "_dominant_eol", "_write_exact", "_decode_full",
                          "_unreadable", "_existing_form"),
    # v0.9.134 P0-3 整包补丁。漏 `parse` = 工具注册了却读不出补丁(handler 只会回那句
    # "这份补丁读不出来",看着像模型的错);漏 `apply` = 算得出来但没人落盘;漏
    # `patch_paths` 最坏 —— 权限门逐份判定与改动前像都靠它,收了 `apply_patch` 却没
    # 收它,包里的补丁就能绕过凭据/`.git`/越界那三层(那正是设计里点名要防的洞)。
    "minicode.patch": ("parse", "apply", "apply_patch", "patch_paths", "build",
                       "FilePatch", "Hunk", "MAX_FILES", "PATCH_FUZZ",
                       "ADD", "DELETE", "UPDATE"),
    "minicode.worktree": ("worktree_facts", "suggest", "parse_worktree_list",
                          "build_add_command", "suggest_copy_path",
                          "worktree_tool",
                          # v0.9.105 相对 HEAD 那一档(漏 changes = 模型叫得到 list 却
                          # 叫不到 changes,回话里"看得见但退不掉"那句就永远说不出来;
                          # 漏 ACTIONS = 参数 enum 与实现各说一套,加一类忘一处没人红)
                          "changes", "parse_status_porcelain", "parse_numstat",
                          "ACTIONS", "CHANGE_ROW_LIMIT",
                            # v0.9.112 p7:先看盘再起进程 + 起不来就熔断(漏收 = 装好的包里开监控栏又弹一次 0xc0000142)。
                            "find_repo_root", "git_unavailable", "_reset_git_breaker",
                          # v0.9.119 分支审查那档(漏 branch_overview/branch_file_diff =
                          # 模型叫得到 action 却没有实现;漏 _safe_rev = 选项注入没人挡)
                          "branch_overview", "branch_file_diff",
                          "parse_name_status", "_safe_rev",
                          "BRANCH_COMMIT_LIMIT", "BRANCH_DIFF_LIMIT"),
    "minicode.tools.catalog_web": ("tools",),
    "minicode.tools.catalog_knowledge": ("skills_plan_search", "kb_suggest_tool",
                                         "memory_tool",
                                         # v0.9.96:动手前问一句(漏收 = 模型根本看不到这只工具)
                                         "ask_user",
                                         # v0.9.98:运行轨迹导出(漏收 = 报障时拿不出流水)
                                         "trace_tool"),
    "minicode.tools.catalog_media": ("tools",),
    # v0.9.98:提醒一族从 catalog_campus 搬去 catalog_remind(399 行热点拆分),
    # 漏收的表现是装好的包里提醒/课表/心情/通知/天气整组消失
    "minicode.tools.catalog_campus": ("calendar_gpa",),
    "minicode.tools.catalog_remind": ("remind_tools",),
    "minicode.tools.catalog_places": ("library_tools", "map_tools"),
    # v0.9.154 I:子代理 → 主会话留话那一只。漏收 = 装好的包里整只工具不在
    # (源码态一切正常,而用户那边"子代理提醒不到我"这件事没有任何声音)。
    "minicode.tools.catalog_sub": ("tools", "notify_parent", "NOTE_MAX_CHARS"),
    # v0.9.106:面板与 /history 共用的那份明细住在 compaction(见那处的针);
    # 这一只模块负责"一次读数拿到数与来源"(漏收 = 又开始各量一遍)
    "minicode.model_catalog": ("context_reading", "vision_forced",
                               # v0.9.111 p8:顶部药丸要说「这个任务真正在用的模型」,
                               # 而「会话单独切过」这件事载荷里原本没有 —— 前端只能拿
                               # 两个名字比着猜(漏收 = 界面又开始猜,全局默认名顶上来了)
                               "session_override",
                               # v0.9.130:「声明 > 探测 > 目录」那把尺子的三个出口
                               "model_info", "window_for", "vision_capable"),
    # v0.9.111 p9:监控栏那一节「环境信息」的全部取材(漏 collect = 整节不出现;
    # 漏 _added_removed = 有未跟踪文件时它会报一个看着完整的 +0 −0,那比没有更坏)
    "minicode.envinfo": ("collect", "_added_removed"),
    # ---- v0.9.17:截止提醒 ----
    "minicode.reminders": ("reminder", "add", "due_soon", "parse_due",
                           # v0.9.29:待办带钟点 → 到点前 20 分钟催(漏收 = 记进去了
                           # 但那条细轮询压根不看它,表现是"到点没反应")
                           "parse_clock", "parse_when", "upcoming_due",
                           "late_due", "DUE_LEAD_MINUTES", "LATE_WINDOW_MINUTES",
                           # v0.9.56:循环待办(漏收 = 「每周/每N天」的规则在装好的包里
                           # 静默失明:到期不滚、finish 不推进,清单一整天停在过期的那班)
                           "parse_repeat", "repeat_days", "next_occurrence",
                           "roll_repeats", "REPEAT_WEEKLY"),
    # ---- v0.9.42:定时任务底座(纯函数 + 账本);v0.9.56 A2 起被 proactive 引用,
    #      必须进包 —— 漏收 = import proactive 直接炸,整个看门狗起不来 ----
    # v0.9.101 ③B1:JobSpec 多了 `label`(看板那一行给人看的名);漏收 = 装好的包里
    # 看板只能列出 mood_weekly_review 这种内部 id。
    "minicode.jobs": ("JobSpec", "label", "due", "due_jobs", "load_state", "last_run",
                      # v0.9.114 自动化「新建」:同学自己登记的那几条存在
                      # jobs-user.json,由 proactive.all_specs() 并进任务表。
                      # 漏收的表现是"看 + 开关"照旧像在工作,而加完重启就没了。
                      "user_path", "load_user", "save_user", "user_specs",
                      "add_user", "remove_user", "spec_of", "USER_FILE",
                      "MAX_USER_JOBS",
                      "runs_of", "mark_run", "log_outcome", "STATE_FILE",
                      # v0.9.79:定时任务可观测(漏收 = 装好的包里「这周任务
                      # 跑没跑、为什么没跑」永远答不上来)
                      "snapshot", "rhythm_text",
                      # v0.9.112 自动化那一屏:清单、下一班、开关与那份「关掉了」的账。
                            "facts", "next_due", "set_enabled", "read_disabled"),
    # ---- v0.9.58:.ics 兜底导出(漏收 = 「把课表导进系统日历」在装好的包里
    #      做不到,而对话里什么错都不报——工具压根没注册) ----
    "minicode.ics_export": ("collect", "render", "export", "export_ics_tool",
                            "ICS_FILE_NAME", "_week_runs"),
    # ---- v0.9.59:成绩单存档与导入(漏收 = 「导入成绩」按钮走了个寂寞,
    #      或 scores 工具在包里不存在,绩点永远要手贴) ----
    "minicode.scores": ("load", "save", "clear", "view", "courses_for_gpa",
                        "parse_score_table", "parse_scores_with_model",
                        "scores_tool", "SCORES_FILE",
                        # v0.9.113 p3/p4:课表面板「成绩」那一档的数(与 gpa 同源)+
                        # 真机表头的三种形状。漏 stats = 那六个格子永远是「—」;
                        # 漏 _cols_of/semester_hint_of = 认列又退回"含关键字就认"。
                        "stats", "_cols_of", "_find_header", "_looks_like_header",
                        "semester_hint_of", "_header_echo", "STATS_ROW_LIMIT"),
    # v0.9.113:成绩那一屏的取材改成了"逐张页逐帧",转出去的名字也要在包里
    "minicode.scores_import": ("ScoresFlow", "_read_scores"),
    # ---- v0.9.18:注入防御(provenance 是函数内 import,必须核它真进包了) ----
    "minicode.provenance": ("wrap", "strip_marks", "is_external",
                            "contains_external", "log_escalation",
                            # v0.9.150 门名全集与那张映射搬到了这一侧(见 §2 那一行)。
                            # 漏 `SECURITY_LOG_GATES` = 包内又退回"从文案里猜密钥二字",
                            # git / project_rule 全被记成 escalation —— 事后线索把答案
                            # 扔了,而没有任何一处会红;漏 `GATE_KINDS` = 那份"日志里
                            # 可能出现哪些词"的全集在装好的包里不存在,而这一列的读者
                            # 恰恰是**手上没有源码**的事后排查人。
                            "SECURITY_LOG_GATES", "GATE_KINDS"),
    "minicode.sandbox": ("SECRET", "is_credential_filename",
                         "command_hits_credential", "command_hits_network",
                         # v0.9.129 命令层「两层都扫不到」那一类(漏 `command_hits_stealth`
                         # = `Start-Process -WindowStyle Hidden` 与 `-enc <base64>` 又从
                         # 命令层与脚本内容层之间漏过去 —— 而这条坏法不报错,只是没人问);
                         # 漏 `lock_plan`/`unlock_plan` = 规划锁没有状态可存,门永远不锁
                         "command_hits_stealth", "STEALTH_PATTERNS",
                         "lock_plan", "unlock_plan",
                         # v0.9.39:沙箱第四层(脚本内容级检测)+ 子进程密钥剥离。
                         # 漏收 = 确认框又只看命令字面,`python 一个偷偷读密钥的脚本`
                         # 一路放行(表现是"没弹框就跑了",而不是报错,所以必须有这根针)
                         "scan_script_text", "script_risk_note", "script_hits",
                         "scrub_env", "KNOWN_LIMITS", "SCRIPT_CREDENTIAL_PATTERNS",
                         "SCRIPT_ESCAPE_PATTERNS", "SCRIPT_EGRESS_PATTERNS",
                         "command_touches_outside_path",
                         # v0.9.104:`.git` 这一层(漏收 = 包里的学长还是把
                         # `.git/hooks/pre-commit` 当普通文件写 —— 那一行脚本会被以后
                         # 每次 git 操作自动执行,而这份漏收**不会有任何报错**,
                         # 表现就是"它怎么又一声不问就改了东西")
                         "is_git_internal", "git_write_hit", "command_hits_git",
                         "GIT_PATH_TOKEN", "GIT_DIR_NAME", "SCRIPT_GIT_PATTERNS"),
    "minicode.permission": ("note_external", "_is_sensitive_action",
                            "_credential_hit", "_same_site",
                            # v0.9.141 出站策略进了权限门(漏 `netguard` = 包里的门
                            # 根本不查这一层,config.json 里的 net_policy 成了装饰,
                            # 而《使用说明》第 127 条还在教用户怎么配它)
                            "netguard",
                            # v0.9.129 规划锁排在档位之前(漏 `plan_locked` = 包里的门
                            # 根本不问这把锁 → 核对单白批;漏 `_plan_locked_denial` =
                            # 拒了却不给出口,模型只能原地打转或劝用户去改错的地方)
                            "plan_locked", "_plan_locked_denial",
                            # v0.9.104:上面那一层接到门里的那只手(漏收 = 判定还在
                            # sandbox 里,但 PermissionGate 不叫它 → 四档一律照旧放行)
                            "_git_hit",
                            # v0.9.115 H3:"是哪条门开的口"这个结构字段(漏收 = agent 取
                            # 不到 ask_kind → 三类红线级的 ASK 被当成普通一问交给了钩子,
                            # 于是凭据文件与 `.git` 由一条命令替用户点头 —— 正是最坏那种)
                            "_ask", "ask_kind",
                            # v0.9.122 并发批次的只读预演(漏收 = 批前没法判
                            # "这一步会不会问人",于是要么继续串行、要么两个确认框
                            # 串味 —— 而后者不报错,只是问错了次序)
                            "peeking", "would_ask",
                            # v0.9.133 P0-1 没读过的文件不许直接改。漏 `note_read` =
                            # 那本账没人记也无人写(读过的照样被问、没读过的照样改,
                            # 两头都错);漏 `_unread_existing` = 判定还在但门不叫它,
                            # 表现是"这枚钉装了个空的";漏 `_whitelist_or_unread_ask` =
                            # 用户自己写的白名单被这枚启发式顶掉,那是本末倒置
                            "note_read", "_unread_existing",
                            "_whitelist_or_unread_ask",
                            # v0.9.133 P0-2 那两只手(漏 `can_remember` = 按钮出现的
                            # 条件不在门上,前端自己猜,猜错就给出一颗点不成的按钮;
                            # 漏 `remember` = 用户点了「以后都允许」而门里那份装载结果
                            # 没重载,同一轮下一步照样再问 —— 那句按钮的话就成了一句假话;
                            # 漏 NEVER_REMEMBER_ASKS = 凭据/`.git`/外部内容那几类也能被
                            # 一次点头永久放行,这是这一路最不该开的那个口子)
                            "can_remember", "remember", "NEVER_REMEMBER_ASKS",
                            # v0.9.134 补丁逐份过同一套门。漏 `_patch_gate` = 一包补丁
                            # 里藏 `.env` 或 `.git/hooks/` 直接绕过凭据与仓库那一层(最坏
                            # 那种洞);漏 `_views` = 用户写的 deny/allow 对整包补丁是装饰
                            # 品(只认 args['path'],而补丁没有那个键)
                            "_patch_gate", "_views", "_target_paths",
                            "PATCH_TOOL_NAME"),
    "minicode.kb": ("crawl_credibility", "host_is_trusted",
                    # v0.9.126 相似度判据拆两道 + 一份配置读法(漏 `_spec_float` =
                    # 两个数各认一种配置写法,一处调了另一处不生效)
                    "DEFAULT_MIN_SIM", "DEFAULT_REL_MARGIN",
                    "_min_sim", "_rel_margin", "_spec_float",
                    "CRAWLED_UNVERIFIED",
                    # v0.9.39 B0:"没查到"这句话与判它的那一处必须同时在包里 ——
                    # 只收一个,回灌就静默失灵(界面照旧,只是再也不提示)
                    "NO_HIT_TEXT", "is_no_hit",
                    # v0.9.90 未命中即给联网入库路径(第三梯队 ⑦):这句话是**写死在
                    # 工具回话里**的,不靠模型自觉 —— 漏收 = 包内未命中又变回一句
                    # 「没查到」,模型爱查不查,而源码测试全绿看不出差别
                    "FALLBACK_HINT",
                    # v0.9.123:查询侧向量走 embed.query_vector 那一条(检索指令加不加
                    # 只在那一处判)。漏收 = 包里还是旧接线 embed_texts([query]),
                    # 文档向量与查询向量两头不一致 —— 不报错,只是语义命中悄悄变差。
                    "query_vector"),
    # ---- v0.9.141:出站网址策略(N1)。漏收 = 装好的包里这一层根本不存在,
    # 而 config.example 与《使用说明》第 127 条都在教用户配它 —— 那正是
    # "配置写了没人读"这一族最难自己发现的形状。
    "minicode.netguard": ("URL_TOOLS", "host_of", "decide", "gate", "note",
                          "_match", "_any", "policy", "ALLOW", "ASK", "REJECT"),
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
                          # v0.9.58:课起止钟点的公开口(.ics 导出/后续面板共用;
                          # 漏收 = 导出的日历里课全降级成全天事件)
                          "row_clock",
                          # v0.9.36:面板的处数与"本周最多那天"由后端一次算好(漏收 =
                          # 徽章回退成列表长度、周视图那句汇总干脆不出现)
                          "walk_total", "walk_week",
                          "walk_skipped"),
    "minicode.weather": ("weather_card", "umbrella_tip", "refresh_async",
                         "get_weather"),
    # ---- v0.9.19:拖拽上传(字节落盘,绕开 WebView2 拿不到本地路径) ----
    # v0.9.74:对话型 slash 命令表(漏收 = 装好的包里 / 面板只有动作没有命令)
    "minicode.slash_commands": ("PROMPT_COMMANDS", "expand", "has_arg"),
    "minicode.attachments": ("save_dropped_bytes", "unique_target",
                             # v0.9.69:恢复会话的附件标签(漏收 = 旧会话恢复后
                             # 附件又只剩被剥掉的字面,缩略图永远不出现)
                             "parse_attachment_note"),
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
    # ---- v0.9.39(refactor 分支):检索/外接能力/领域路由三批新模块 ----
    # 漏收的后果各不相同,但都属"装好的包里功能静默退化":
    # retrieval 不在 = kb 退回朴素关键词匹配(BM25F/RRF/元数据过滤全丢,还照旧报"已按相关度排序");
    # embed 不在 = 语义档永远起不来;
    # router 不在 = task 的 auto 与四个领域档一起消失(只剩 explore/implement/review)。
    "minicode.retrieval": ("tokenize", "KeywordIndex", "fields_of", "rrf",
                           "order_by_fused", "cosine", "rank_by_vector",
                           "filter_meta", "reason_line", "Hit", "RRF_K",
                           "FIELD_WEIGHTS"),
    "minicode.embed": ("detect", "embed_texts", "ensure_vectors", "cache_path",
                       "load_cache", "save_cache", "entry_key", "entry_text",
                       "EmbedUnavailable", "CACHE_VERSION",
                       # v0.9.123:本地向量档进包 + 池化口径摆到明面上。
                       # 漏 embeddings_spec = kb 的 min_sim 与探测各认一种配置写法;
                       # 漏 query_vector = 查询侧那半套规矩(默认不加指令)没人实现,
                       #   检索还是拿"带不带指令"两头的向量在比;
                       # 漏 _profile_tag = 换池化/换截断宽度仍在复用旧向量 —— 不报错,
                       #   只是每条命中都是另一个空间里的数;
                       # 漏 _session_for = 每次语义检索都重建一次 ONNX 会话(慢一个量级)。
                       "embeddings_spec", "query_vector", "_profile_tag", "_session_for",
                       "POOLINGS", "DEFAULT_POOLING", "DEFAULT_MAX_SEQ_LEN"),
    "minicode.ocr": ("recognize", "ocr_image", "ocr_probe", "recognize_system",
                     "recognize_vision", "system_ready", "vision_ready",
                     "normalize_system_text", "SYSTEM_WARN", "VISION_PROMPT",
                     "BackendMissing"),
    "minicode.charts": ("chart_file", "render", "normalize_data", "nice_ticks",
                        "render_bar", "render_line", "render_pie", "ChartError",
                        "CHART_TYPES", "MAX_POINTS", "PIE_MAX_SLICES", "slugify"),
    "minicode.images": ("image_generate", "render_card", "call_api", "probe",
                        "api_targets", "local_ready", "CONFIG_SHAPE",
                        "ImageError", "MAX_DOWNLOAD_BYTES", "MAX_CARD_HEIGHT"),
    "minicode.router": ("DOMAINS", "DomainSpec", "classify", "best", "resolve_type",
                        "extra_prompt", "hit_line", "skills_of", "Match",
                        # v0.9.109:派档前那句「本机还缺」的那半(漏收 = 账有了、
                        # 读者没了 —— 技能缺依赖还是只有子代理自己撞上去才知道)
                        "gap_clause"),
    # v0.9.39:一轮总时限是真的在拦东西(漏收 = 配置文件里的数字没人读)
    "minicode.agent": ("run_turn", "turn_time_limited", "TASK_BUDGET_NOTE",
                       "DAILY_BUDGET_NOTE",
                       # v0.9.154 I 子代理 → 主会话留话:漏 `queue_note`/`_take_notes`
                       # = 装好的包里那条通道整个没有,而工具那一侧还在 —— 子代理调用
                       # 会走到"找不到入口"的分支、回一句"这里没有可以转达的对象",
                       # 看起来像"主会话那边满着",其实是包里少了一段。
                       "queue_note", "_take_notes", "NOTE_MAX",
                       # v0.9.105 /rewind 点头前那一屏:预览与动手共用一份筛选
                       # (漏 rollback_preview = 命令行只能盲退;漏 _rollback_scope =
                       # 两处各筛一遍,总有一天预览说 2 处而实际退 1 处)
                       "rollback_preview", "_rollback_scope", "_rollback_arg",
                       # v0.9.97:量窗口那把尺子的实测下限(漏收 = 装好的包里压缩又回到
                       # 只按字符估,英文/代码多的会话会一路撞到真实窗口才压)
                       # v0.9.106 Q1:改名 context_pair(数与来源一次读数)+ 这把尺子
                       # 随注册表现算(tools_tokens)+ 换过工具清单要能真调用
                       # (refresh_registry)。漏收 = 装好的包里还是"副本 + 只换列表"
                       # 那一版:MCP 热重载后新工具看得见调不动,压缩按旧清单算。
                       "context_pair", "context_source", "REAL_FLOOR_WINDOW",
                       "tools_tokens", "refresh_registry",
                       # v0.9.39 B0/B1:两个挂钩 + 那条"只活在一次请求里"的先验
                       "_messages_for_request", "_note_low_rated_prior",
                       "_mark_repeat_empty",
                       # v0.9.115 H1/H3:钩子接进门里的那几只手。漏 `_matched_config_hook`
                       # = 红线预检永远拿不到"有没有钩子认领",整条 pre 不跑;漏
                       # `_apply_hook_input` = 改写无声失效(钩子自称改了而执行的是原参数);
                       # 漏 `_ask_via_permission_hook`/`NON_DELEGABLE_ASKS` = 代答要么整个
                       # 不生效,要么连那三类红线级的门也一起替用户点头。
                       "_matched_config_hook", "_apply_hook_input", "_hook_blocked_text",
                       "_log_hook_rewrite", "_note_hook_health",
                       "_ask_via_permission_hook", "NON_DELEGABLE_ASKS",
                       # v0.9.123 安全日志里"是哪道门问的"那一列 —— **v0.9.150 起
                       # 那张表与门名全集搬到了 `minicode.provenance`**(见那一组),
                       # 这里只留"谁来用"这半截:`_log_security_ask`。
                       "_log_security_ask",
                       # v0.9.133 P0-1 那支笔(漏 `_note_read_path` = 判定与账都在门里,
                       # 却没有任何地方往里记 → 读过的文件照样被问,而这枚钉看起来是装的;
                       # 漏 READ_NOTED_TOOLS = 名单漂了没人知道,写成功不再算读过)
                       "_note_read_path", "READ_NOTED_TOOLS",
                       # v0.9.134 一包补丁的前像与读账都按多份展开(漏 `_snapshot_one`
                       # = 整包只存了第一份原文,退回去就把别的文件留在改过的样子;
                       # 漏 PATCH_TOOL = 补丁干脆不进回滚账)
                       "_snapshot_one", "PATCH_TOOL",
                       # v0.9.126 同回合并发 + 超长输出转存(漏 `_lane_ok` =
                       # 池子内没人在判断资格,确认框可能从工作线程弹出来;
                       # 漏 `_spill_tail` = 超长那半截又凭空消失而不落文件)
                       "_dispatch_tool_calls", "_lane_ok", "_run_batch",
                       "_account", "_spill_tail", "OUTPUT_SPILL_DIR",
                       "TOOL_PARALLEL_MAX",
                       # v0.9.127 回合中途插话(漏 `queue_interjection` = 界面那句
                       # "补一句"当场消失;漏 `_take_inbox` = 收进去了却没人读,
                       # 信箱成为第二份历史 —— 用户看见的话永远不会进模型上下文)
                       "queue_interjection", "_take_inbox", "INBOX_MAX",
                       # v0.9.127 §四.2 项目指令的首次采纳点头(漏 `_ask_project_agents`
                       # = 那份别人写的文件在包里照样静默注入,而源码态是"不点就不注入"
                       # —— 两态行为不一样,正是冻结针要抓的那种);
                       # 漏 `_ask_pick` = 答案读不回来,点了头也算没点。
                       "_ask_project_agents", "_ask_pick", "_agents_refused",
                       # v0.9.125 三段拆分的接缝(漏 `_run_handler` = 包里没有那段
                       # "唯一可进线程池"的边界,并发批次只能整步丢进线程 → 确认框
                       # 从工作线程弹出来,而这条坏法不报错)
                       "Prepared", "_prepare", "_run_handler", "_finish",
                       "HARD_REJECT_NOTE", "HOOK_BAD_STREAK",
                       # v0.9.115 H4:两个事件钩子(漏 `_collect_event_hook` = 事件照叫、
                       # 附的那句话永远进不了请求;漏 `_fire_start_hooks` = 整类事件不跑)。
                       # v0.9.151 起第三个事件 `stop`(漏 `_fire_stop_hooks` = 装好的包里
                       # 那一类**从不触发**,而它正是"答完记一笔"唯一能挂的地方 ——
                       # 配置里写着、界面上也说过,就是不发,没有任何一处会红)。
                       "_fire_start_hooks", "_fire_stop_hooks", "_collect_event_hook",

                       # v0.9.40 D0/D2:跨天重建 + 撞上限后的收尾调用
                       "_refresh_clock", "_wrap_up",
                       # v0.9.77:失败连击止损(漏收 = 装好的包里同一个调用
                       # 连挂 N 次只会烧 token 到 max_steps/整轮时限)
                       "FAIL_STREAK_LIMIT", "FAIL_STREAK_NOTE", "_fail_streak_hit",
                       # v0.9.87:免确认放行的留痕(漏收 = 白名单生效了却没人记得
                       # 是谁批的,事后查不到)
                       "_note_project_allow",
                       # v0.9.78:成本闸门(漏收 = config 里的上限在包里没人读,
                       # 跑飞了还是只能等时间限)
                       "TOKEN_LIMIT_NOTE", "session_token_limit",
                       # v0.9.101 ①R1:改动审查清单与逐文件回退(漏 undo_records =
                       # 面板那一整段没了;漏 _mark_after_mutation = 漂移永远报不出来,
                       # 面板就敢把"被命令改过的那一处"也当成还能退)
                       "undo", "undo_all", "undo_records", "CHANGE_VIEW_LIMIT",
                       # v0.9.101 ①R2:某一处的 diff(漏 undo_diff = 「看改了什么」那颗
                       # 芯片点了永远转圈;漏 DIFF_SCAN_BYTES = 包里回到"多大的文件都硬去
                       # 读一遍"的老行为,一次展开变成一次大扫描)
                       "undo_diff", "DIFF_CONTEXT", "DIFF_LINE_LIMIT",
                       "DIFF_SCAN_BYTES",
                       # v0.9.103 ①R3:编辑重发连着退文件(漏 rollback_to_ordinal =
                       # 桥调不到就永远不退;漏 _epoch/_turn_ordinal = 回合坐标不记账,
                       # 压过一次历史后敢按号退别人的文件 —— 那是动手面,不是一句错话)
                       "rollback_to_ordinal", "_epoch", "_turn_ordinal",
                       "_mark_after_mutation", "_drifted", "_apply_undo",
                       "_change_display", "_undo_extra_tail"),
    "minicode.subagents": (
        # v0.9.98:并行写的文件占用闸(漏收 = 装好的包里两路同写一个文件
        # 退回"后写覆盖先写",用户还看不见任何报错)
        "_BatchFileLocks", "_guard_write_tools", "_lock_key",
        # v0.9.155 L:全机在飞那一道名额 + 深度上限。漏 `_acquire_slot`/`_release_slot`
        # = 包里没有那层闸(嵌套打开时线程数上界回到 6+36+216);漏 `MAX_SUB_DEPTH`
        # = "到顶了没有"永远判假,递归一路往下;`inflight_count` 是它唯一的读数口。
        "_acquire_slot", "_release_slot", "_gated", "inflight_count",
        "GLOBAL_SUBAGENT_CEILING", "MAX_SUB_DEPTH",
        # v0.9.154 I:装配那一头的容器(漏 = 子代理的 `ctx.notify_parent` 永远是 None,
        # 而工具和通道都好好的 —— 表现成"主会话收不到子代理的提醒"且没有任何报错)
        "parent_inbox",
        "type_spec", "_await", "_timeout_note", "TYPE_KEYS",
                           "DOMAIN_KEYS",
                           # v0.9.60:批量放开写类型的两根支柱(漏收 = 混合批次
                           # 静默退回"只读才并行",而工具描述还在许诺能并行写)
                           "MAX_PARALLEL_TASKS", "_TaggedUI",
                           # v0.9.76:产出尾注(漏收 = 子代理写过的文件在监控栏
                           # 「产出」永远隐形 —— 并行写是卖点,产出却看不见)
                           "_files_note",
                           # v0.9.81:联网尾注(同一个盲区还剩「网页查阅」那一栏 ——
                           # 漏收 = 子代理爬的页面/查的库在面板上永远不出现)
                           "_web_note",
                           # v0.9.95:按档位分模型的判定(漏收 = 配置读了、报告却仍说主模型,
                           # 或反过来在报告里编一句「本档模型 X」而代码里根本没这回事)
                           "model_choice",
                           # v0.9.129 本地档位定义(agents/*.md)接进派发那一段。
                           # 漏 type_keys = task 那份清单里没有用户自己起的档名,模型叫得出
                           #   也看不见,等于这层只能靠猜名字用;
                           # 漏 unknown_tools/dispatch_notes/notes_prefix = 红线 4 断了,
                           #   "tools: 里写了一只这档拿不到的工具"会静默少给而不吭声。
                           "type_keys", "profiles_view", "_profile_bases",
                           "_apply_profile", "unknown_tools", "dispatch_notes",
                           "notes_prefix"),
    # v0.9.129 本地档位定义整只模块(agents/*.md → 只能收窄的一档)。
    # 漏收的表现不是报错:用户在工作区写了一份 agents/scout.md,应用**完全当它不存在**,
    # 而任务面板里那一档永远派不出去,一句解释都没有 —— 与"配置读了没人接"同一种坏法。
    "minicode.agent_profiles": ("load", "Profile", "profile_dirs", "PROFILE_DIR",
                               # v0.9.152 E「子代理外部目录兼容」:漏 `COMPAT_PROFILE_DIRS`
                               # = 装好的包里那三个目录**一份都不认**(用户从 Cursor /
                               # Claude Code / Codex 带来的档位定义静默消失,而源码态
                               # 一切正常 —— 与上面那条"完全当它不存在"同一种坏法)。
                               "COMPAT_PROFILE_DIRS",
                               "PRIVILEGED_KEYS", "NEVER_CHILD_TOOLS"),
    # v0.9.95:换模型重建客户端的那条构造(漏收 = 档位分模型整块没了)
    "minicode.llm": ("scoped",                 # v0.9.110:400 摘字段重发的那张梯子
                             "LEAN_LADDER", "BODY_PARSE_MARKS", "lean",
                             # v0.9.143 撞超限压一次再试(漏 `is_context_overflow` =
                             # agent 那边 import 就炸;漏标记表 = 认不出超限,白压或漏压)
                             "is_context_overflow", "_OVERFLOW_MARKERS"),
    # v0.9.96:提问的"形状与说法"一份真相(漏收 = 工具、桥、界面三层各判各的,
    # 最坏的那种是"没等到回答"被写成"用户同意了")
    "minicode.asking": ("normalize", "format_answers", "ask_user",
                        "MODE_UNANSWERED", "MAX_QUESTIONS"),
    # v0.9.95:馆藏连接的"整条语句在同一次持锁里跑完"(漏收 = 装好的包里并行派活
    # 随机一路"子代理执行出错",而这种只在并发下复现的毛病最难从用户话里倒推)
    "minicode.library": ("_read",
                         # v0.9.111 卡顿那一件:全库 COUNT 的结果必须有地方记住
                         # (漏 _STATS_CACHE = 每切一次会话都重算一遍 1.6 秒,而这正是
                         # 用户报的「切换任务总是要停顿好几秒」的那一根)
                         "_STATS_CACHE"),
    # v0.9.97:语料新鲜度(漏收 = 提示词里那句"这批语料是几天前抓的"永远不出现,
    # 而它存在的意义就是让模型别把旧语料当"最近")
    "minicode.notices": ("corpus_age", "_corpus_line", "CRAWL_STAMP_FMT",
                         # 两种写侧格式与解析那一步(漏收 = 装好的包里只认一种写法,
                         # 更新的那份归档被悄悄跳掉,"最近一次抓取"报成次新的)
                         "CRAWL_STAMP_FORMATS", "_stamp_key", "_unreadable_tail",
                         # 工具回话那一句(漏收 = 提示词里有年龄、回话里却没有,模型
                         # 拿到本地条目时照样会把旧语料当"最近")
                         "corpus_note",
                         "notices_prompt_section"),
    # v0.9.39:确认框要能讲清新工具的代价(漏收 = 弹了框但没说图会被发出去/文件落在哪)
    "minicode.explain": ("risk_notes", "permission_why", "action_reason"),
    # ---- v0.9.16/17/20:主动推荐 + 新版本检查 + 今日简报(合并成一个键,
    #      避免 dict 重复键 last-wins 让早先的 needle 悄悄漏检) ----
    "minicode.proactive": ("check_once", "fetch_latest", "start_loop", "clean_title",
                           # v0.9.114:任务表 = 代码登记的 + 同学自己加的
                           # (漏 all_specs = 新建的那几条在装好的包里根本不跑)
                           "all_specs", "_job_user",
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
                           # v0.9.56:定时任务表接线(漏收 = A4 的周期任务在装好的
                           # 包里永远没人跑,而界面一切正常)
                           "JOB_SPECS", "JOB_HANDLERS", "_run_due_jobs",
                           # v0.9.98:周期自动备份(漏收 = 装好的包里这一班静默跳过,
                           # 用户以为在备份,其实从来没有)
                           "_job_backup", "_rotate_backups", "_BACKUP_PREFIX",
                           "BACKUP_KEEP",
                           # v0.9.57 A4:两个真任务(漏收 = 周五周日什么都不发生,
                           # 任务表在装好的包里退回装饰品)
                           "_job_weekly_review", "_job_gap_digest",
                           "WATCHDOG_LOG", "watchdog_log_path",
                           "wd_log", "wd_log_tail"),
    # ---- v0.9.23:教务课表导入(整套新模块,漏收 = 点「导入课表」直接报错) ----
    "minicode.schedule_import": ("ImportFlow", "parse_grid", "summarize",
                                 "clear_login_state", "GRID_JS", "_norm_periods",
                                 "MODEL_SYSTEM",
                                 # v0.9.24:通用取材 + 「先认版式再挑文法」那条路
                                 "_parse_any", "page",
                            # v0.9.112 p1:成绩读不到的根 —— 每一步先接管那张页(会话按线程分格)。
                            "anchor_existing", "no_page_msg",
                            # v0.9.113 p3:接管到了**框架页**不等于看到了成绩表 ——
                            # 逐张候选页 × 逐帧都翻。漏收 = 又回到"第一帧有表就交差"。
                            "page_targets", "candidate_pages", "dumps", "scans",
                            "_page_urls", "MAX_PAGES"),
    # v0.9.114 新功能③:技能分享包。整模块新的 —— 漏收的表现不是报错,而是
    # 装好的包里技能详情那颗「打包分享」点了没反应(桥里那句 import 在方法体内)。
    "minicode.skill_pack": ("pack", "preview", "apply", "skill_dir_of",
                            "stash_existing", "MANIFEST",
                            "MAX_FILES", "BACKUP_DIR", "_slug", "_key_is_safe"),
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
                         "FRAME_MAX_CHARS",
                         # v0.9.98:标签页可见可管(漏收 = browser_status 退回
                         # 只报"能不能用",开了几页、各看什么又变成黑箱)
                         "list_tabs", "close_tab",
                         # v0.9.113:登录后的教务往往同时开着框架页与真表页,而
                         # adopt_page 只拿第一张匹配的 —— 先列出有几张才谈逐张试。
                         "page_targets",
                         # v0.9.139:配置被占用那一族(漏收 = 包里还是"Edge 启动后
                         # 立刻退出,无法启用浏览器能力"那一句死结,教务导入在
                         # 装好的包里继续点多少次「再读一次」都是同一个红字)
                         "BrowserProfileBusy", "_profile_endpoint", "_spawn",
                         "_write_marker", "_read_marker", "_clear_marker",
                         "_pid_alive", "_sibling_profiles", "last_note",
                         "MAX_PROFILE_TRIES"),
    # ---- v0.9.24:MCP 三种传输(stdio 之外只有网址的服务器也连得上) ----
    # v0.9.62:校历补录助手(漏收 = 「贴通知录考试周」在包里做不到,
    # 考试周数据继续空置,A5 考试闭环继续卡着)
    "minicode.calendar_edit": ("validate", "preview_text", "commit",
                               "calendar_edit_tool",
                               # v0.9.73:删除与修订(漏收 = 装好的包里录错一条
                               # 校历永远改不了,退回手编 JSON)
                               "remove_preview_text", "remove_commit",
                               "revise_preview_text", "revise_commit",
                               "_resolve_targets", "_backup"),
    "minicode.mcp": ("Transport", "StdioTransport", "HttpTransport", "SseTransport",
                     "make_transport", "_iter_sse", "_safe_url", "_usable_url",
                     "_norm_transport",
                     # v0.9.61:断线重连与自助管理(漏收 = 服务器重启后工具全哑
                     # 到重启应用;「对话里加个 MCP 服务器」在包里做不到)
                     "McpTransportError", "restart", "probe_server",
                     "admin", "admin_tool", "set_admin_hook",
                     # v0.9.102:B1 resources 接入(漏收 = 装好的包里资源明明
                     # 连着,模型却永远读不到)
                     "mcp_resource_handler", "read_resource", "live_manager",
                     "set_manager_hook",
                     # v0.9.70:预设模板库(漏收 = 包里说「没有叫 xx 的预设」,
                     # 对话里加服务器退回手编 JSON)
                     "MCP_PRESETS", "find_preset", "build_preset_config",
                     # v0.9.82:能力协商照实说(漏收 = 包里添加服务器那句话与
                     # 侧栏悬停都不再讲「对端声明了什么、学长只接了 tools」,
                     # 用户又会以为接上就等于全能用)
                     "CLIENT_PRIMITIVES", "caps_line", "server_caps",
                     # v0.9.121 工具结果那一份文本的出口(漏收 = 装好的包里
                     # image/audio 又整坨 base64 灌进上下文、structuredContent
                     # 明明有数据却回「工具返回空结果」—— 两处都不报错,
                     # 一个是每轮多付一笔读不懂的字,一个是把有说成没有)
                     "format_call_result", "_inline_note", "_data_size_note",
                     "STRUCTURED_MAX", "BASE64_INLINE_MAX",
                     # v0.9.122 工具级名单(漏收 = 配了 deny_tools 却一只没少,
                     # 而侧栏报的还是服务器声明的那只数 —— 两处数字各说各话)
                     "mount_names", "client_mount", "mount_line",
                     "allow_tools", "deny_tools"),
    # v0.9.144 MCP server 模式:本 agent 反过来被外部客户端按 stdio 调用。
    # 漏收的表现全是"不报错的那种":装好的包里 `--mcp-serve` 无人应答(客户端
    # 握手超时),或白名单/排除表被截断成"少暴露一只"而没人发现。
    "minicode.mcp_server": ("serve", "SERVE_TOOLS", "EXCLUDED_TOOLS", "_Server",
                            "_spec", "_NoUI", "pop_mcp_serve_flag",
                            "PROTOCOL_VERSION", "SERVE_FLAG"),
    # ---- v0.9.24:计划面板(漏收 = 顶栏「🗂 计划」点开永远说没计划) ----
    # v0.9.33:对话流里的计划卡片靠 in_progress/running 画出"正在做哪一步"
    # (漏收 = 卡片只剩一堆待办和勾,转圈的那一行永远不出现)
    # v0.9.87:项目级权限白名单(漏收 = permissions.json 在装好的包里根本没人读,
    # 用户批了权限却发现每次还是被问,或更糟:模型自己写的那份被当真)
    "minicode.permspec": ("load", "parse_rules", "Rule", "Spec", "decide",
                          "command_head", "SAFE_COMMAND_HEADS", "trust", "revoke",
                          "approved_sha", "trust_path", "permissions",
                          "permissions_trust", "PERMISSIONS_FILE", "TRUST_FILE",
                          # v0.9.116 L2:ask/deny 的三条命脉。漏 `hit` = 门只查得到
                          # allow,用户写的"必须问我/不许做"在包里静默失效 —— 比没有
                          # 这条功能更糟,因为文件确实躺在项目根上。
                          "hit", "summary", "DECISIONS",
                          # v0.9.127 §四.2:同一行上的第二件事(指令文件的采纳)。
                          # 漏 `adopt_agents` = 点头没处落;漏 `agents_adopted_sha` =
                          # 落下了也没人读得回来 —— 两条都是"每问重弹一张卡"那种坏法。
                          "adopt_agents", "agents_adopted_sha", "_row", "_put_row",
                          # v0.9.149 第三级的按层记账。漏 `agents_adopted_layers` =
                          # 侧栏那一行报不出"这一层采纳了没有";漏新那个键名 =
                          # 子目录层的点头写进去又读不回来(与上面同一种坏法)。
                          "agents_adopted_layers", "AGENTS_LAYERS_KEY",
                          # v0.9.133 P0-2「以后都允许」。漏 `remember` = 那颗按钮点下去
                          # 什么都不写(界面上说"已记住"而盘上一条都没有);漏
                          # `granted_rule` = 按钮出现的条件与真写下去的判断分成两套,
                          # 迟早出现"按钮给了却点不成";漏 `granted_rules` = 本机那本账
                          # 写了没人读,免确认动作存了个寂寞;漏 GRANTED_KEY = 装载时
                          # 找错键名,整本账静默变空。
                          "remember", "granted_rule", "granted_rules",
                          "granted_candidate", "GRANTED_KEY", "MAX_GRANTED",
                          "ORIGIN_GRANTED", "to_raw"),
    # v0.9.116 L3:内置钩子配方(整只模块新加;漏 apply = 挂不上,漏 list_line =
    # 连"有没有挂着"都答不出)
    "minicode.hook_packs": ("preview", "apply", "list_line", "TEMPLATES",
                            "_render_spec", "_write_helper"),
    "minicode.planning": ("plan_state", "dispatch_text", "notify_plan",
                          "in_progress", "running",
                          # v0.9.114 新功能①:计划核对单(漏 review_plan = plan 写完
                          # 什么都不弹,那张"批准/改一条/重排"的卡整个不在包里)
                          "review_plan", "REVIEW_MODES", "_apply_review_edit",
                          # v0.9.81:计划与子代理对上号(漏收 = task 的 step 参数
                          # 在装好的包里静默失效,计划面板看不出哪一步正由谁在做)
                          "dispatch_start", "dispatch_end", "_as_step_no",
                          # v0.9.101 ②V1:步骤的验收判据(漏 verify_of = 老文件照样解析
                          # 但判据永远读不出来,硬闸就成了假闸;漏 note_verify_run =
                          # bash 跑完没人记账,带判据的步骤从此永远标不上 done)
                          "VERIFY_RE", "verify_of", "note_verify_run",
                          "find_verify_run", "_with_verify", "_verify_gate",
                          "VERIFY_RUNS_LIMIT"),
    # v0.9.114 W2/W4/W5/W6:估算费用与用量画像。漏收 = token 弹窗里「估算费用」
    # 永远「未知」、风云榜四卡永远空 —— 价目缺行显示未知是**诚实**,
    # 整个模块不在包里是**假绿**(界面照旧弹,只是每一栏都空)。
    "minicode.pricing": ("RATES", "RATES_SOURCE", "OVERRIDE_FILE", "rate_for",
                         # v0.9.138 命名空间那一步(漏 _query_forms = `global:xxx` 这种
                         # 带前缀的名字又判成"未知",有价说成没价,与把没价说成 $0 同样坏);
                         # 以及分币种那两条(漏 _cur 就是人民币报价的行被当成美元合计)
                         "_query_forms", "_cur", "_sym",
                         "estimate", "summarize", "load_override",
                         "save_override", "effective_rates"),
    "minicode.usage": ("record_usage", "statistics", "budget_state",
                       "continue_daily_budget", "reset_daily_budget_override",
                       # v0.9.114 W0/W5/W6:三列迁移 + 按模型画像(漏 _model_profile
                       # = 风云榜四卡没有数据来源;漏 _extra_totals = 头部累计耗时
                       # 与会话数永远 0 —— 不是没数据,是没人去数)
                       "_model_profile", "_extra_totals",
                       # v0.9.117 用量示意改版:区间解析 + 仪表盘一揽子聚合
                       # (漏 resolve_range = 五档区间没法定起止;漏 dashboard =
                       # 新弹窗整页空,老 statistics 还在,只是没人喂新版面)
                       "resolve_range", "dashboard"),
}

# ---- 包内前端(v0.9.94 起):界面那半边不在 PYZ 里,它由 packaging/gcode.spec 的
# datas 整目录收进 `_internal/frontend/`。上面那套针表对它是瞎的 —— 于是「装好的
# 包里少了这一屏」只能靠这条补上。漏收的表现全是不报错的那种:控件不在,点了没反应,
# 或者干脆还是旧的那副样子。
FRONTEND_TARGETS: tuple[str, ...] = (
    # v0.9.94 p3/p5:权限档位改成界面自己画的浮层菜单(原生 select 的白字在深色
    # 模式下看不见)。漏 permPill = 顶栏那个按钮整个没了;漏 permMenu = 点了没菜单
    "permPill", "permMenu", "PERM_META",
    # v0.9.113 p5/p6/p7:无边框那一套(漏一条 = 装好的包里那三颗/那 8 条把手没了,
    # 而窗口已经没标题栏了 —— 人就真的关不掉、也缩不了)
    "pywebview-drag-region", "winInit", "win_state", "win_minimize", "win_zoom",
    "win_close", "win_resize", "rzBind", 'data-e="se"', "tb-drag", "winBtns",
    # v0.9.113 p1/p2:收起栏搬到对话区左上角(漏 = 那颗又搬回入口栏,人找不到)
    'id="btnSideToggle"', 'class="tb-ico"', 'classList.toggle("on", !!closed)',
    # v0.9.113 p3/p4:课表面板「成绩」那一档(漏 = 那六个数与学期下拉整个不在包里)
    "scScoresPanel", "scStatCard", "scStatsGet", "get_score_stats", 'data-tab="scores"',
    "esc(String(v))",
    # v0.9.113 p8/p9:右缘导航离边留距 + 浅色看得见 + 跳过去那一枚一直亮
    "--rail-tick", "right:12px", "scTickMark",
    # v0.9.113 ①:认不出表头时那颗"摊给学长看"(漏 = 失败又变回死路)
    "jwPeekPage", "让学长看这一页的表格结构",
    # v0.9.114 p1:三颗窗口按钮画成 SVG 且宽高写在 CSS(只给属性会被 flex 吃成 0)
    ".tb-ico svg{ display:block; width:17px; height:17px;", "winCall",
    'classList.toggle("maxed", winMaxed)',
    # v0.9.114 p2:「选学期」自绘浮层 + 系统画的部分跟着主题
    "scSemBtn", "scSemMenu", "function scSemToggle", "color-scheme:dark",
    # v0.9.114 p3:入口栏不出滚动条(但 overflow-y:auto 必须留着)
    "scrollbar-width:none",
    # v0.9.114 p5/p6:自动化「新建」那张表单
    "jbForm", "jbSave", "jbSetCadence", "jbRenderDays", "add_automation",
    "autoNew", "remove_automation", "jb-tag",
    # v0.9.114 新功能①:计划核对单
    "planReviewOverlay", "renderPlanReview", "prReply", "plan_review_reply",
    # v0.9.114 新功能③:技能分享包两颗
    "btnSkillPack", "btnSkillPackImport", "skPackExport", "skPackImport",
    "export_skill_pack", "apply_skill_pack",
    # v0.9.114 p8/p9:托盘推的 open_panel 在界面侧有落点
    'name === "new_task"', 'name === "providers"', '"session:"',
    # 上一轮那颗死钮的教训钉在包里:关闭那颗必须真的发出去且回话有人看
    'winCall(api.win_close(), "关闭")',
    "jwPeekPage", "让学长看这一页的表格结构",
    # v0.9.94 p6:会话轮次导航条(右侧细刻度 + hover 预览)
    "chat-rail", "rebuildChatRail", "showRailPop",
    # v0.9.94 p7/p8:附件图片点开看 + 左右切换(以前放进去的照片根本没法查看)
    "imgLightbox", "openLightbox",
    # v0.9.94 p1/p2:主题切换时让系统标题栏跟着换色(后端那半另有针)
    "set_chrome",
    # v0.9.96:提问卡(漏收 = 后端推 ask_request 而界面没人接,用户看到的是
    # "学长卡住了" —— 这一层在包里,前端是整份文件拷进去的,漏的是改旧版没覆盖)
    "askOverlay", "renderAsk", "ask_reply", "ask-trace",
    # v0.9.97:长期记忆面板(界面上从此能记一条,不只 CLI 的 /remember)
    "memoryOverlay", "rememberIt", "memCountLine",
    # v0.9.101 ①R1:改动审查那一段(漏 buildChanges = 监控栏里没有「本轮改动」;
    # 漏 undo_step/undo_all = 行列出来了但点不动,回退仍然是"一次一步全凭猜")
    "buildChanges", "undoChange", "undo_step", "undo_all", "本轮改动",
    # v0.9.101 ①R2:diff 那一块(漏 toggleChangeDiff = 芯片是死的;漏 ch-diff =
    # 有函数没样式,+ 与 − 挤成一坨没有颜色分)
    "toggleChangeDiff", "undo_diff", "ch-diff", "act-chip", "d-more",
    # v0.9.103 ①R3:编辑重发那一句确认(漏这两个标签 = 按钮又变回含糊的「确定/取消」,
    # 用户不知道按下去会退文件;漏 appCancelCb = 取消那一支什么都不做,只重发这条路就断了)
    "退回文件并重发", "保留文件,只重发", "appCancelCb", "undo_records",
    # v0.9.101 ④L3:指令层那一行(漏 renderInstrLine = 后端算了没人画;漏
    # instrLine = 前端有函数但没有那个 DOM,首屏直接抛)
    "renderInstrLine", "instrLine",
    # v0.9.101 ③B1:跨会话看板那一屏(漏 loadBoard = 按钮点了没数据;漏 boardOverlay
    # = JS 有函数但没有 DOM,首屏绑定直接抛;漏 btnBoard = 入口没了)
    "loadBoard", "boardOverlay", "btnBoard", "boardRow", "boardAct",
    # v0.9.111 p9:监控栏那一节「环境信息」的前端半边(漏 buildEnv = 后端给了数据没人画,
    # 整节不出现;漏 actEnvSummary = 段标题那句摘要没了,折叠着看不出改了点什么)
    "buildEnv", "actEnvSummary", "envRow",
    # v0.9.111 p8:顶部药丸写「这个会话真正在用的模型」(漏 syncTopPill = 事件推过来
    # 没人重画,顶上还是那句全局默认 —— 用户原话「让用户无法判断到底调用的哪一个模型」)
    "syncTopPill",
    # v0.9.111 p6:模型清单按供应商分组(漏 tgroup = 分组标题没了,又变回一长条不分家的列表)
    "tgroup",
    # v0.9.111 p12:提示只提醒一次(漏 hintSeen = 每次都画;漏 markHintSeen = 点了「知道了」
    # 当场消失、下次开机又回来 —— 那只算「藏了一次」,不算「记住一次」)
    "hintSeen", "markHintSeen",
    # v0.9.111 p10:上下文面板那四行明细的 class —— 它原本叫 `tk`,和计划卡片的 `.tk`
    # 撞名被撑到 69px 一行(整个面板 491px)。改名 `tkn` 是这次收窄的**真正**修法,
    # 针打在这儿,以后谁把它改回 `tk` 会在出包时红一次。
    "tkn",
    # v0.9.112 p2/p5:轮次那一叠小卡、收起侧栏、终端与自动化两个面板
    # (漏 turnPopToggle = 刻度点了没反应,还是"一长条看不到头";
    #  漏 toggleSide = Ctrl+B 与那颗按钮都是死的;
    #  漏 termLoad/autoLoad = 两个弹层有了壳,里面永远没数据)
    "turnPopToggle", "toggleSide", "applySide", "termLoad", "autoLoad",
    "buildSubagents", "subagentRows", "actGroupFold",
    # p7/p8:地图舞台的下限与 git 降级那两行 —— 样式与字段名都在前端这一侧
    "min-height:min(42vh, 300px)", "e.git_note", "e.chrome",
    # v0.9.114 观测线(W1/W2/W4/W7/W8/W9):监控栏翻车段 + 失败卡 + 定位现场
    # + token 弹窗风云榜四卡 + 每轮思考折叠段 + ×N 归类 + 同批 ∥ 标记 +
    # 头部第二行。漏收的表现全是不报错的那种:面板少了那一段、点了卡片没反应、
    # 弹窗里没有那一栏、模型一轮同时要的几步看不出是一批。
    "act-failhead", "act-find", "locateFinding", "act-flash",
    "usage-cards", "usage-card", "uc-head", "uc-line", "uc-sub",
    "fmtCost", "fmtDur",
    "act-reason", "act-reason-cap", "act-reason-body", "💭 思考",
    '×${item.group_n} 同款调用', "same-batch",
    "累计耗时", "有账的会话",
    # v0.9.114 的旧针「缓存命中(端点不回报)」在 v0.9.117 随改版退役 —— 那句话
    # 在界面上没有了:缓存命中现在端点回报就记账,没回报显示「未记录」。
    # v0.9.117 用量示意改版:两栏仪表盘 + 全期热力图 2D/3D + 五档区间
    # (漏一条 = 装好的包里用量弹窗还是旧圆环那副样子,或热力图切不过去)
    "get_usage_dashboard", "usageCustom", "buildHeatGrid", "renderHeat3D",
    "TOKEN 总数", "uh-grid", "每日明细", "模型明细",
    # v0.9.118 3D 全屏透视(漏一条 = 入口按钮不见,或全屏转不起来)
    "perspOverlay", "btnPerspOpen", "drawPersp", "PERSP_LEVELS",
    # v0.9.120 用量面板右上角那颗刷新按钮:选择器必须带 `.usage-head` 前缀才压得住
    # `.dialog button{padding:8px 20px}`(单类名特异性更低)。漏回来 = 那颗又撑成
    # 42×26 的横胶囊、图标落在右半边 —— 界面细节没人会去重查,所以钉在包里。
    ".usage-head .usage-refresh{",
    # v0.9.127 回合中途插话的前端半边(漏 `interject` = 跑着的时候按 Enter 又什么都不
    # 做,后端那个信箱成了没人喂的空格子;漏 `sealStreamingText` = 插话气泡画出来了,
    # 可后面每一条 text_delta 仍往上面那段里追加 —— 回答长在插话头上;漏 `INPUT_HINT`
    # = 提示语从 HTML 属性里读一次这件事没了,改成写死两份)
    "function interject", "function sealStreamingText", "INPUT_HINT",
    # v0.9.128 改动审查那一屏的三处补正(漏 `buildUnledger`/`sameFile` = 账外那片又
    # 只剩环境栏两个总数,或把"我改过的"重复说成"退不掉";漏 `maybeChangeReviewHint`
    # = 关着监控栏的人依然永远等不到那条入口,这一件就白做;漏 `.act-grp`/`.chg-hint`
    # = 有函数没样式,分组头与那条入口挤成一坨看不见)
    "function buildUnledger", "function sameFile",
    "function maybeChangeReviewHint", "function openChangesSection",
    ".act-grp", ".chg-hint",
    # v0.9.133 确认框那颗「以后都允许」(漏 `btnRemember` = 按钮整个不见了,后端那
    # 一路记规则的代码成了没人按的开关;漏 `can_remember` = 按钮无条件常亮,四类
    # "永远不给记住"的动作也亮着,点了才说记不成;漏 `.b-remember{` = 它与「本轮
    # 总是允许」长得一模一样,误点一次就把一次性的点头变成长期授权)
    "btnRemember", 'confirm_reply("remember")', "can_remember",
    ".b-remember{",
    # ---- v0.9.138 六件里的界面那五件 ----
    # 漏 `function thinkTick` = 思考那一行又回到"进行中…"冻住的样子(正文其实
    # 在 DOM 里,只是那块永远 display:none —— 用户看到的正是"看不到它在想什么");
    # 漏 `function renderMathBlocks` / `vendor/katex/katex.min.js` = 公式又印成
    # `\boxed{...}` 源码;漏 `function queueDraft` = Enter 在忙时还是立刻插话,
    # 打错一个字没有反悔的余地;漏 `function openSessMenu` = 侧栏还是只有一颗 ✕,
    # 图形侧改不了名;漏 `.shrc` = 分享图那颗复制按钮不见了。
    "function thinkTick", "function thinkStop", "function isWideCp",
    "function renderMathBlocks", "function ensureKatex", "function estTokens",
    "vendor/katex/katex.min.js", "math-d",
    "function queueDraft", "function renderDrafts", "draftBar",
    "function openSessMenu", "function startSessRename", "sess-rename",
    ".sm", "contextmenu", "archived",
    ".shrc", "copy_share_image", "function costBoth",
    # v0.9.139 攒批的那一半:后端一次送一批,前端这只必须在场,否则装好的包里
    # 流式正文**一个字都不出现**(evaluate_js 调一个不存在的函数,异常被 push
    # 那层吞掉 —— 又是一次"不报错的坏法")。
    "window.__gcodeEvents",
    # v0.9.142「智能配置」那一格(漏 `mcfApplyAuto` = 开关点下去什么都不发生;
    # 漏 `mcfAutoNote` = 灰掉了却不解释,那正是这一轮要消灭的"不会配")
    "mcfApplyAuto", "mcfAutoNote", "id=\"mcfAuto\"", "function mcfApplyAuto",
)


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
    # 逐针打印 ✓/✗:Windows 默认控制台是 GBK,print 当场 UnicodeEncodeError ——
    # 出包后这道验证闸照文档跑就是 traceback(check_docs.py / check_frontend_js.py
    # 同一个坑,都已修)。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass          # 被重定向/没有 reconfigure 的输出流:原样让它去
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

    # 包内前端:界面那半边不在 PYZ 里(由 spec 的 datas 整目录收进去),对着文件核。
    html = next((c for c in (exe.parent / "_internal" / "frontend" / "index.html",
                             exe.parent / "frontend" / "index.html") if c.exists()), None)
    if html is None:
        print("  ✗ 包里没有 frontend/index.html")
        failures.append("frontend:缺失")
    else:
        fsrc = html.read_text(encoding="utf-8")
        for needle in FRONTEND_TARGETS:
            ok = needle in fsrc
            print(("  ✓ " if ok else "  ✗ ") + f"frontend: {needle}")
            if not ok:
                failures.append(f"frontend:{needle}")
    print()
    if failures:
        print("FAIL: 包内不是最新版代码 →", failures)
        return 1
    print(f"PASS: 包内后端 {sum(len(v) for v in TARGETS.values())} 根针 + 前端 "
          f"{len(FRONTEND_TARGETS)} 根针,全部命中最新版(v{_src_version()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
