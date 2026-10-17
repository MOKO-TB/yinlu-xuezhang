from minicode.tools.registry import build_default_registry


def test_registry_contents():
    tools = build_default_registry()
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file",
                     "apply_patch",          # v0.9.134:整包统一 diff,紧跟 edit_file
                     "bash",
                     "bash_status", "bash_kill", "bash_input",
                     # v0.9.173:递话(正在跑的对象之间留一句话),与上面三只同族
                     "session_message",
                     "py_script",
                     "glob", "grep", "repo_outline",
                     "diagnostics",
                     # v0.9.164~170 符号与结构一组(registry 里紧跟 diagnostics):
                     # 它们答的是"这段代码长什么样/谁在用",只读那几只免确认。
                     "test_runner",
                     "symbol_search", "find_references", "rename_symbol",
                     "call_hierarchy", "symbol_definition", "code_outline",
                     "permissions", "permissions_trust",
                     "hooks", "hooks_apply", "worktree", "worktree_apply",
                     "web_crawl", "web_search",
                     "browser_open", "browser_eval", "browser_snapshot",
                     "browser_click", "browser_fill", "browser_screenshot",
                     "browser_close", "browser_status", "page_parse",
                     "mcp", "mcp_resource", "mcp_prompt",
                     "use_skill", "plan", "ask_user",
                     "search_knowledge",
                     # v0.9.173:按需加载的门(永远常驻;只读 —— 它只读注册表
                     # 与自己的描述,不碰磁盘、不起进程、不花额度)
                     "tool_search",
                     "export_trace",
                     "export_feedback", "search_history",
                     "ocr_image", "render_chart", "generate_image",
                     # v0.9.173:看图 —— 只读,只是把工作区里已有的图发给模型自己看
                     "view_image",
                     "campus_calendar", "calendar_edit", "gpa_calc",
                     "scores",
                     "kb_suggest", "library_search", "library_guide", "memory",
                     "repo_wiki",            # v0.9.171:代码库百科(只读查询)
                     "reminder", "mood", "schedule", "desktop_notify", "get_weather", "campus_notices",
                     "export_ics",
                     "map_find_place", "map_show_list", "map_highlight_building",
                     "map_drop_pin", "map_clear",
                     # v0.9.154 I:子代理 → 主会话留话(排在最后,见 registry_split 那支)
                     "notify_parent"]
    assert len(set(names)) == len(names)


def test_read_only_flags():
    flags = {t.name: t.read_only for t in build_default_registry()}
    assert flags == {
        "read_file": True,
        "write_file": False,
        "edit_file": False,
        # v0.9.134 补丁工具:它就是"一次写好几份文件"的编辑,写类毫无疑问。
        # 顺带说清一件事:它不在 `tools/concurrency.py` 的 CONCURRENT_SAFE 名单里,
        # 而那份名单要求 `read_only and name in CONCURRENT_SAFE` 两个条件 ——
        # 也就是"只读"是并发的入场券,写类工具连候选都不是(门要问人不能发生在工作线程)。
        "apply_patch": False,
        "bash": False,
        "bash_status": True,           # 只读:查后台任务的进度与输出尾部,不该弹确认框
        "bash_kill": False,            # 终止进程是动手面,必须用户点头
        # v0.9.146:喂输入是**动手面**,与 bash_kill 同档 —— 它能让一条正在跑的命令
        # 往下走(喂进去的可能就是 y、是密码)。它写的是 `text` 而不是 `command`,
        # 所以**不经过命令文本那道判定**,确认框必须说清这一点。
        "bash_input": False,
        # v0.9.173:递话 —— 只把一句话放进另一个盒子,不写盘、不起进程,所以只读。
        # 它不需要确认框的理由与 notify_parent 一字不差(框会弹在递话那一侧,
        # 点头的人管不了对面要不要照做);要堵的是对外形态,web_profile 里排除。
        "session_message": True,
        "permissions": True,           # 只是看一眼这个项目批了什么
        "permissions_trust": False,    # 批准 = 从此免确认放行,必须用户点过头
        "hooks": True,               # 看一眼挂着什么/预览配方,不改任何东西
        "hooks_apply": False,        # 写用户 config.json = 装自动执行入口,必须点头
        # 开一份 Git 工作副本会在仓库旁边建目录并动 git —— 它是**写类**动作,
        # 而注册表目前标的是 read_only=True(全表唯一一个写盘却免确认的工具)。
        # 这条口径可疑,已记 HANDOFF §6 等拍板;这里先照**实测值**钉住,不改行为。
        # v0.9.99 拍板:worktree handler 只跑 worktree list / status 两条只读
        # git 命令,零写入;建副本是它"建议"的命令,走 bash 确认框(见
        # catalog_files.worktree_tools 的注释)。"写盘免确认"的疑案就此销案。
        "worktree": True,
        "worktree_apply": False,
        "py_script": False,          # 内置运行时执行脚本:能读写文件,要确认
        "glob": True,
        "grep": True,
        "repo_outline": True,        # 只读大纲:不写任何东西
        # v0.9.147:诊断**不标只读** —— 它会执行工作区里的检查器(`cargo check` 会跑
        # `build.rs`),与 `hooks`(只读列清单)正好形成对照:列清单只读,真跑要点头。
        "diagnostics": False,
        # v0.9.164~170 符号与结构一组。口径分两档:纯查询的(test_runner 除外的
        # 那几只)只读免确认;而 test_runner 会**真跑**工作区的测试命令(与
        # diagnostics 同一档:可能执行项目里的构建脚本),rename_symbol 会改盘 ——
        # 这两只必须点头。
        "test_runner": False,
        "symbol_search": True,        # 只解析源码找符号定义,不写不跑
        "find_references": True,      # 只找引用与影响面,不写不跑
        "rename_symbol": False,       # dry_run 默认预演,但真改会写多份文件:要确认
        "call_hierarchy": True,       # 只算调用拓扑,不写不跑
        "symbol_definition": True,    # 只取定义位置与代码块,不写不跑
        "code_outline": True,         # 只列文件骨架,不写不跑
        "web_crawl": False,
        "web_search": False,         # 联网访问搜索引擎:与 web_crawl 同级要确认
        # 浏览器会带用户的登录态、browser_eval 能在页面里执行 JS:要确认,
        # 不标只读(browser_status 只是查能力,不驱动页面)。
        "browser_open": False,
        "browser_eval": False,
        "browser_snapshot": True,     # 只列出页面上有什么,不驱动页面
        "browser_click": False,
        "browser_fill": False,
        "browser_screenshot": False,
        "browser_close": False,
        "browser_status": True,
        "page_parse": True,           # 只把页面上的表读成结构化文字,不写文件不驱动页面
        "use_skill": True,
        "search_knowledge": True,
        "tool_search": True,          # v0.9.173:只读(取回工具不改任何本机状态)
        # 会话导成可回放的轨迹 JSON:往工作区**写一份文件**,且内容是对话原文 —— 要确认
        "export_trace": False,
        "export_feedback": False,
        # 翻本机历史会话:只读,且只出用户/助手正文,不下发绝对路径
        "search_history": True,
        # 拍错题识别:不写文件,但 vision 后端会把图发给模型 —— 外发由 trust="external" 管
        "ocr_image": True,
        # 图表:要在 charts/ 落一个文件,和 write_file 同级 —— 但它不联网、不读本机数据
        "render_chart": False,
        # 图片生成:api 一条会把描述发给外部服务并花 Key 额度,card 一条往 images/ 写
        # PNG —— 两条都要确认
        "generate_image": False,
        # v0.9.173:看图 —— 它把工作区里**已有的**一张图发进会话(下一轮模型就能看到)。
        # 不落盘、不联网、不读本机数据,所以是只读;图会随消息外发,那由
        # trust/沙箱那条线管(与 ocr_image 同一档的外发口径)。
        "view_image": True,
        "campus_calendar": True,      # 校历查询:只读本地数据文件
        "gpa_calc": True,             # 绩点计算:纯计算,不落盘
        "kb_suggest": False,          # 写候选文件:确认框就是"用户同意存入"那一步
        "library_search": True,      # 离线书目库查询,只读
        "library_guide": True,       # 图书馆导览(楼层/空间/流程),只读
        "plan": False,
        "ask_user": True,            # 只是问一句,什么都不改:给它弹确认框等于"要确认一次确认"
        "memory": False,
        "repo_wiki": True,            # v0.9.171:代码库百科,只查已生成的大纲,不写不跑
        "reminder": False,           # 待办提醒写本机清单文件,要确认
        "mood": False,               # 情绪记录写本机 mood.json:全 app 最私密的一份,
                                     # 每一次落盘都该在确认框里看得见(与 reminder 同级)
        "schedule": False,           # 课表存本机文件,同上(保存动作本身即用户授意)
        "desktop_notify": False,     # 会改提醒开关(写 notify-pref.json)、还能往桌面弹东西:
                                     # 与 schedule/reminder 同级先确认
        "get_weather": True,         # 只读天气数据(固定数据源),不驱动任何东西
        "campus_notices": True,      # 只读现拉官网通知标题,不提交任何东西
        "export_ics": False,         # 往工作区写 .ics 文件:与 write_file 同一层确认
        "scores": False,             # action=clear 删整份存档:非只读,与 mood 同理
        "mcp": False,
        "mcp_resource": False,                # 改配置并重连外部服务:绝不是只读
        "mcp_prompt": False,                  # 同上:取回的是外部服务器写的一段指示
        "calendar_edit": False,      # 往校历文件追加条目:非只读
        # 地图工具只改界面显示、不动文件:标只读,免得每次定位都弹确认框
        "map_find_place": True,
        "map_show_list": True,
        "map_highlight_building": True,
        "map_drop_pin": True,
        "map_clear": True,
        # v0.9.154 I:提醒一下不该点一次头 —— 它只往内存里加一句话,
        # 不碰文件、不联网、不执行任何东西。
        "notify_parent": True,
    }


def test_openai_schema_shape():
    for t in build_default_registry():
        schema = t.to_openai_schema()
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == t.name
        assert fn["parameters"]["type"] == "object"
        assert isinstance(fn["parameters"].get("properties"), dict)
