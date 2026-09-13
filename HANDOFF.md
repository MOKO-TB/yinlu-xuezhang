# 引路学长(Agent)开发交接文档

> 更新:2026-09-13　用途:任何新会话(人或 Agent)读完本文档即可无缝接手。
> 项目代码:`D:\game\mini-code`(引路学长,原 mini-code/Gcode)。

---

## 1. 项目一句话现状

对标星瑶 Y-Code 的自研编码智能体,**v0.8.8**,已完成:Agent 主循环 + 内置 11 工具与 MCP 外部工具 + 权限门与**沙箱边界**(敏感路径硬禁/工作区外逐次确认) + 内置校园模型(界面不提供密钥与模型切换,顶栏标识只展示)+ **附件上传**(文件/图片,可选视觉多模态)+ SKILL.md 技能 + 知识库检索(来源网址+可信度)+ 子代理 + 计划模式 + 长期记忆 + 会话管理 + 上下文压缩 + GUI(暖橙手账风,深浅主题,时间问候首页+动态校园建议 chips,**划词复制**/消息复制与编辑重发,思考过程可折叠,立方体图标+校名 logo)+ 三形态分发(安装包/单文件/便携 zip,另含内置 crawl4ai 的完整版)。

## 2. 已完成事项(按里程碑)

| 里程碑 | 内容 |
|---|---|
| MVP | Agent 主循环(流式+工具调用增量聚合)、6 工具(read/write/edit/bash/glob/grep)、权限门(confirm 模式+灾难命令硬拒绝+工作区路径围栏)、终端 UI(rich+prompt_toolkit) |
| 标准版 | /resume 会话恢复、/undo 写前快照回滚、上下文自动压缩(token 估算+按完整轮切分摘要) |
| GUI | pywebview + 免构建单页前端(暖橙手账设计令牌,html.dark 深浅主题,localStorage 记忆主题);系统托盘(pystray);X 键=最小化到托盘(pywebview closing 事件返回 False 拦截) |
| v0.6.0 子代理 | task 工具:explore(只读侦察)/implement(动手)/review(审查);独立会话+受限工具集(sub_tool_registry,排除 task 防递归,工具名加 "类型·" 前缀)+ max_steps=20 |
| v0.6.1 内置校园模型 | gzasc=广州应用科技学院(http://10.223.24.16:8080/v1,qwen3.8-27b,**key 不进仓库**:由本地未跟踪的 `config.local.json`(`builtin_api_key`)或环境变量 `YINLUXS_BUILTIN_API_KEY` 注入,`_builtin_api_key()` 运行时解析,打包时 spec 把它打进 exe),无 Key 用户默认使用;10.x 私网自动绕过系统代理 |
| v0.7.0 计划模式 | plan 工具(action=write/read/update),2-8 步勾选清单落工作区 plans/plan.md(文件即状态可进 git);复杂任务先列计划→用户确认→逐步执行 |
| v0.8.0 长期记忆 | memory 工具(save/list/search/forget),四类(user/feedback/project/reference),存工作区 memory/MEMORY.md(一条一行人工可编辑),系统提示词自动注入(截 4000 字) |
| 校园语料爬虫 | scripts/crawl_school_topics.py:按主题爬学校官网栏目生成 kb/school-gzasc.json(24 主题,credibility=A)。除通知类栏目外覆盖宿舍与生活服务/校园活动与文体/社团/军训/学生故事/校园讲座/视频图文/服务大厅与办事入口。**正文为空的页面也建条目**:视频页给 `观看地址:`、二维码指引页(热水充值/报修/校园服务公众号)给 `查看地址:`、仅附件页列附件名——保证链接能送到学生手上;失效链接(官网 404 也返 200)靠标题识别拦掉 |
| B站视频知识库 | scripts/crawl_bili_topics.py:按"学生会问的关键词"爬 B 站搜索页(crawl4ai 渲染),生成 kb/bili-videos.json(~86 条,credibility=B-视频经验,含观看地址/UP主/时长/播放量),search_knowledge 直接命中 |
| 微信公众号知识库 | scripts/crawl_wx_topics.py:经搜狗微信搜索爬公众号文章(搜狗→中间页→一次性签名链→正文),生成 kb/wx-articles.json(28 条,credibility=A)。签名链会过期,入库 URL 用"本文短链或搜狗搜索入口" |
| 定向公众号爬虫 | scripts/crawl_wx_account.py:`--pages 3` 定向爬「广州应用科技学院」公众号的肇庆校区内容:搜狗微信搜索分页发现(卡片公众号名在 `span.all-time-y2`,旧的 `a.account` 已失效)+ 文章正文内链 BFS 扩量,生成 kb/wx-gzasc.json(200 条,credibility=A,标题/正文限在肇庆一带地名以免混入校旗新闻)。**链接质量是本项核心**,踩的坑值得记住:①用户可点的是规范永久链接 `s?__biz=&mid=&idx=&sn=&chksm=`(**五要素缺一不可**;缺 chksm 实测只返回 17KB 空壳页,且 **chksm 必须正好 64 位**——从 HTML 里挖常带尾巴(抓到 76 位),多一个字符就打不开,入库前必须归一化);②`<meta og:url>` 只在**直连永久链接**时才有值,搜狗签名链路径为空,所以靠 BFS 把互链文章的规范链接补齐(118/200;其余 82 条退回搜狗入口并在正文注明"多一步点击")。③账号历史页(`/mp/profile_ext`)、合集页(`appmsgalbum`)、不完整链接(缺 sn/chksm)都要登录/打不开,发现只能靠搜狗+互链。④维护命令:`--fix-links`(离线归一化+回填文章身份)、`--resolve-links`(百度搜标题补链,按 mid 匹配+抓取校验防取错;实测产出低且易触百度风控(1 分钟/条),可断点续跑,建议闲时慢慢跑) |
| 小红书知识库 | scripts/crawl_xhs_topics.py:登录态爬小红书搜索笔记(必跑 --login 扫码,登录态存 .automation/ 已 gitignore),生成 kb/xhs-notes.json(30 条,credibility=B-社区经验,含正文/赞数/笔记链接)。爬取用"全新会话+注入 storage_state"(旧自动化配置会被风控标记);详情从 __INITIAL_STATE__.note.noteDetailMap 提取 |
| v0.8.1 校园化门面 | ①聊天首页占位块改为**按系统时间的问候语**(凌晨/清晨/早上/上午/中午/下午/晚上好,同学!)+ 9 个校园问题建议 chips(点击回填输入框),前端 `renderHome()` 渲染,"cleared" 事件复用;②应用图标换成蓝色立方体(覆盖 assets/gcode.ico,exe/安装包/托盘/侧栏 logo 同源;源图 assets/logo_cube_raw.png,前端用 frontend/img/cube.png);③侧栏加广州应用科技学院横版 logo(frontend/img/school.png,黑字透明底,深色主题 CSS invert 反白);④system_prompt.md 新增「校园背景」:广应科默认肇庆校区(肇庆市鼎湖区莲花镇丰乐路20号),校园问题先 search_knowledge 检索 |
| v0.8.2 简化交付 | ①侧栏 logo 去 `rotate(-4deg)` 摆正;②**移除「🔑 密钥」按钮与 API Key 管理弹窗**(顶栏只留供应商下拉,固定用内置广应科 Key,用户不填不改;要换供应商须手编 config.json);③**删除系统托盘与关窗隐藏逻辑**,点 X 即彻底退出(任务管理器不再残留 引路学长 进程),pystray 从 spec/requirements 移除,gui_main.py 不再 import threading/PIL |
| v0.8.3 对话体验 | ①消息悬停操作栏:用户/助手消息**复制**(⧉,navigator.clipboard + execCommand 双兜底;助手复制 markdown 原文,存于 wrap.dataset.raw);②用户消息**编辑重发**(✎):Bridge.edit_resend(ordinal, new_text, expect) 按「第 N 条用户消息」定位截断 session 后重发,expect 原文校验防压缩错位;前端 truncateDomFrom 删旧 DOM 并复用序号;③首页建议 chips **动态化**:minicode/suggestions.py 主题池(18 主题,关键词+ask+followup),collect_user_texts 采当前会话+最近 5 个留档的用户消息打分,聊过的主题换追问话术置顶、未聊的默认补位;get_suggestions 在启动/新建任务时刷新 |
| v0.8.4 划词与思考 | ①**划词浮动复制条**:聊天区内选中文字→mouseup 弹出「📋 复制选区」(position fixed,选区上方,越界翻转),按钮 mousedown preventDefault 保住选区;Esc/滚动/点击别处隐藏;只认 #chat 内的选区;②**思考过程折叠块** `.think`:reasoning_delta 到达时自动展开(💭 思考·进行中…),首个 text_delta 到达即折叠成「💭 思考 · 持续了 N 秒 ▾」,点头部小箭头手动展开回看(正文保留最近 3000 字);③**系统消息不给复制按钮**:renderHistory 里纯「调用工具」消息与 finish_stream 空正文消息移除 .acts,复制只复制正文(dataset.raw 不含工具行) |
| v0.8.5 MCP 客户端 | 新增 `minicode/mcp.py`:stdio JSON-RPC 2.0 客户端(initialize → notifications/initialized → tools/list → tools/call),读线程+Condition 支持超时(启动 10s/调用 60s,可按服务器覆盖),stderr 单独收集作排障线索,`shutil.which` 解析 npx/uvx(.cmd)且 Windows 下 CREATE_NO_WINDOW;外部工具以 `mcp_<服务器>_<工具>` 挂进注册表(名字规范化+去重+64 字符上限),默认**非只读走确认门**,`read_only: true` 才免确认;`config.json` 新增 `mcp_servers`(command/args/env/enabled/read_only/超时/description;Config 里存原文以免 config↔mcp 循环导入),连不上只记日志跳过、绝不阻塞启动;gui_main/CLI 退出走 `agent.close()` 回收子进程;侧栏底部显示 `MCP 名称(工具数)`。另:打包补齐 crawl4ai 版安装包 `packaging/gcode_crawl4ai.iss`(独立 AppId 可与轻量版共存),两个版本号都随 `minicode.__version__` |
| v0.8.6 模型固定 | 顶栏的模型下拉(`<select id="prov">`)换成**纯展示标识** `.modelpill`(span,无下拉箭头、不可点选,内容取 `get_info` 的 active 供应商名 + model),删掉 `provData`/`fillProvSelect`/change 监听;后端 `_ensure_builtin_provider` 不再"有 key 就用用户的",改为**无条件** `active_provider_id = BUILTIN_PROVIDER_ID`——config.json 里残留的其他供应商不删(保用户数据)但永不激活;内置供应商改为**插入 `replace(BUILTIN_PROVIDER)` 副本**(不再共享模块级单例,改一份配置不会污染模板);`load_config` 把补内置提到"providers 为空就退出"之前(空配置也能起);CLI `/model`、`/help` 与 `/skills` `/kb` 文案一并订正(顺带修掉 `ui.info` 会转义 rich 标签导致 `[bold]` 原样显示的老毛病)。**测试口子**:新增 `YINLUXS_LLM_BASE_URL` / `YINLUXS_LLM_MODEL` / `YINLUXS_LLM_API_KEY` 环境变量,在内存里临时改写当前模型端点(不落盘),冻结版 e2e 靠它把 exe 指到本地假 LLM |
| v0.8.7 沙箱与附件 | ①**沙箱**(`minicode/sandbox.py`):路径三级(工作区 INSIDE / 外部 OUTSIDE / 敏感 BLOCKED)+ 命令管控 + 超时上限。敏感目录(`~/.yinluxs` `~/.ssh` `~/.aws` `~/.gnupg`…)永久硬拒,`accept_all` 也不能越过;工作区外默认 ASK,用户放行后本回合内有效(`SandboxState.approved_paths`,回合开始清空),`allow_outside_workspace` 则直接放行。`tools/base.py` 新增 `resolve_path()`(沙箱感知,`ctx.sandbox` 为空时退化为旧的硬围栏)替换 fs 三件套里的 `resolve_in_workspace`;`shell.bash` 受沙箱钳制超时;`PermissionGate(config, sandbox=...)` 联动作判定,`agent._execute` 在用户放行后调 `gate.approve_outside()`。GUI 顶栏「🛡 沙箱开/关」+ CLI `/sandbox [on|off]`。②**附件**(`minicode/attachments.py`):📎 走原生 `create_file_dialog`(经 `ui.window`),文件复制进 `uploads/`(重名加序号,单文件 20MiB 上限),消息注入「[附件] 路径清单」;图片压缩(长边 1536、JPEG 逐级降质量到 1MB、透明 PNG 保格式、GIF 不动、40MP 防炸弹);`config.json` 的 `vision: true` 时把图做成 base64 data URL 作为 content parts 发送(`Session.add_user(text, content)` / `run_turn(..., content)` 支持多模态),否则仅路径。`Attachment.local` 存绝对路径供压缩读取(相对路径会受进程 CWD 影响) |
| v0.8.8 划选修复 | 用户反馈"划词选不中、思考收不起来"——排查确认功能本身在 v0.8.4 已实现,是他跑的是旧包(旧包里「调用工具」消息还带复制按钮)。本轮把拖选的真实障碍一并修掉:①**操作栏改为常驻占位 + visibility 切换**(原 `display:none` 会在悬停瞬间撑开 29px 高度,拖选过程中内容跳动、选不准);②显式 `#chat *{user-select:text}`(防继承干扰),操作栏/思考头部/who 行反而 `user-select:none`;③划词触发从"只监听 mouseup"扩成 **mouseup + pointerup + dblclick + selectionchange(未按键时)** 多路冗余,任一事件在 WebView2 里丢失也能弹条;④思考块箭头加大(13px)+ 悬停高亮 + `title` 提示;⑤首页补一行使用提示(划词复制 / 点箭头展开思考)。实测:思考中展开→出正文自动折叠成「持续了 1 秒」→点箭头展开→再点收起,全程正确;选中文字弹出「📋 复制选区」;纯工具调用消息无复制按钮 |
| v0.8.9 右键菜单 | 输入框/聊天区右键补上原生菜单(撤销/重做/剪切/复制/粘贴/删除/全选)并恢复 Ctrl+C/V/X/A。**根因**:pywebview 把 `AreDefaultContextMenusEnabled` 和 `AreBrowserAcceleratorKeysEnabled` 都设成 `_state['debug']`(platforms/edgechromium.py),正式版 `debug=False` → 右键无菜单、连 Ctrl+V 都不响应。修法:`gui_main.apply_context_menu_settings()` 在 `webview.start(func)` 起的线程里轮询窗口,把两个开关打开,1.5 秒后复查一次(幂等,防 pywebview 自身回调竞态);**关键约束**:`CoreWebView2` 属性只能在 UI 线程访问(后台线程读它会抛 "can only be accessed from the UI thread"),所以必须用控件 `Invoke(System.Action(...))` 切回 UI 线程;控件对象位置也随版本变(实测 5.x 是 `window.native.webview`,旧版在 `native.browser.webview`,故 `_webview2_controls()` 多路径兜底)。失败只写 `gui_warnings.log`,不影响使用;设 `YINLUXS_CTX_DEBUG=1` 会额外记录成功标记,便于验收 |
| v0.8.10 首页校园照片 | 首页问候区改为**校园照片 hero 横幅**:`frontend/img/campus.jpg`(1229×1535 竖版航拍夜景,自带校名 logo,来源:用户 2026-09-13 提供的照片)。CSS `object-fit:cover` + `object-position:center 22%` 取**上部暖橙晚霞横带**(图是上下两场景拼接,中缝和下半段冷蓝操场不用;左上角校名 logo 在横向 center 裁切下多半落在带外,若露出再调)。暗部线性渐变压底,问候语/副标题叠白字(问候的 b 标签用 #FFD9A8 暖橙,深浅主题都一致);「试试这些」改为 chips 行前缀 span;chips/提示条仍在照片下方,主题色不受影响;图随 frontend/ 整目录打包,spec 不用改。聊天开始后 hero 随整个 placeholder 移除,清空会话重建 |
| v0.8.10 助手名改校名 | 助手消息头部的 `GCODE` 标签改成 **广州应用科技学院**(仅前端展示文案,`ensureAssistant()` 里那一处;`window.__gcodeEvent` 事件桥名与 `gcode-theme` 存储键是内部标识,不能动) |
| 打包分发 | PyInstaller 双 exe(GUI+CLI)+ onefile 单文件版 + Inno Setup 安装包(中文向导/仅当前用户/快捷方式/卸载器,卸载保留用户数据) |

## 3. 关键决策(不要推翻)

1. **技术栈**:Python 3.12(venv 在 `.venv`,用 `py -3.12` 创建)+ openai SDK + pywebview + rich + prompt_toolkit;前端**免构建**单页 HTML(marked.js 渲染),不引入 Node 工具链。
2. **GUI 与 CLI 共享引擎**:`minicode/app_setup.py`(build_system_prompt + create_agent)是唯一装配点;GUI 桥 `gui_bridge.py`(js_api 门面 `Bridge.public_api()`),终端 `ui.py`。
3. **密钥安全**:API Key **绝不写进 config.json,也不写进源码**(仓库 gitee.com/inkzero/momo 是公开的),两处都走本地注入:①用户自己的 key 存 `~/.yinluxs/credentials.json`(save_config 自动拆分,load 时 `_merge_credentials` 合并);②内置校园供应商的 key 存本地未跟踪的 `config.local.json` 的 `builtin_api_key`,或临时用环境变量 `YINLUXS_BUILTIN_API_KEY` 覆盖,**打包时由 spec 打进 exe**(用户侧仍是开箱即用)。`tests/test_config.py` 用夹具把该环境变量钉成固定值,并有 `test_builtin_provider_key_is_not_hardcoded` 守着,防止有人把 Key 写回源码。
4. **js_api 必须传门面**(`Bridge.public_api()`):直接传 Bridge 实例会被系统 UIA 客户端沿对象图递归解析到 WinForms,爆栈堵塞桥线程(症状:窗口"未响应"、get_info 永不到达)。
5. **前端初始化必须等 `pywebviewready` 事件**,不能轮询 window.pywebview(竞态)。
6. **权限门**:子代理工具名带 `类型·` 前缀,PermissionGate 用 `rsplit("·",1)[-1]` 取真名(灾难命令拦截才生效);灾难命令(rm -rf /、format c: 等)一律硬拒绝不弹确认。
7. **文件即状态**:计划(plans/plan.md)、长期记忆(memory/MEMORY.md)、会话留档(sessions/*.jsonl)全部落工作区文件,不依赖对话记忆。
8. **版本号**:唯一来源是 `minicode/__init__.py` 的 `__version__`;用户要求**每次更新递增 0.0.1**(0.8.5 → 0.8.6 …),两个打包脚本都从它取名,安装包版本用 `/DAppVersion` 传入。

## 4. 踩过的坑(复现过,别再踩)

- **滚动失效真因**:`.app` 是 grid 但只定义列不定义行 → 隐式行随内容长高 → 聊天容器永不溢出、composer 被顶出窗外。修复:`grid-template-rows:minmax(0,1fr)` + `.main/#chat/#sessions` 各级 `min-height:0`。flex/grid 溢出问题用 browser-use(IAB)注入长内容 + evaluate 比较 scrollHeight/clientHeight 决定性验证。
- **打包**:①spec 内路径必须绝对(相对 spec 目录解析);②Anaconda venv 需在 spec 显式补 `Library/bin` 的 ffi.dll/libexpat.dll 等;③nltk 运行时钩子会崩 → excludes 加 nltk;venv 里的 patchright/scipy/numpy/tokenizers 被牵连收集 → 一并排除(160MB→75MB);④onefile 下资源走 `_MEIPASS`、用户数据走 exe 旁(只查 app_path 会 404/知识库为空);⑤build 前必须关掉所有运行中的引路学长实例(文件占用 WinError 5/32),Git Bash 里 `cd` 出 dist 目录再打包。
- **私网直连**:llm.py `_is_direct_host` 覆盖 127/10/172.16-31/192.168——系统代理(Clash 127.0.0.1:7890)会拦截内网请求返回 502。
- **pywebview 正式版没有右键菜单和 Ctrl+C/V**:`platforms/edgechromium.py` 里 `AreDefaultContextMenusEnabled` / `AreBrowserAcceleratorKeysEnabled` 都跟着 `_state['debug']`,而应用用 `debug=False` 跑 → 右键空白、粘贴快捷键失效。要自己开,见 `gui_main.apply_context_menu_settings()`。
- **`CoreWebView2` 只能在 UI 线程访问**:后台线程(包括 `webview.start(func)` 起的那个)读它会抛 `InvalidOperationException: CoreWebView2 can only be accessed from the UI thread`(底层是 ICoreWebView2Controller 的 QueryInterface 失败)。必须 `view.Invoke(System.Action(fn))` 切回 UI 线程;另外 WebView2 控件对象的位置跨版本不同(5.x 在 `window.native.webview`,旧版在 `native.browser.webview`),取值要用多路径兜底而不是写死一条链。
- **悬停态不要用 display 切换来显隐**:`display:none → flex` 会让消息高度突变,用户正在拖选文字时内容跳动、选不中。改成常驻占位 + `visibility` 切换。
- **测试里的路径**:memory/planning 等模块重构为 workspace 路径参数后,测试直调要用 `tmp_path` 而不是 ctx;monkeypatch `minicode.config.CREDENTIALS_PATH` 避免写真实主目录。

## 5. 测试与验证(改完代码必须做)

```bash
cd /d/game/mini-code
./.venv/Scripts/python.exe -m pytest -q                    # 257 个测试全绿
./.venv/Scripts/python.exe scripts/e2e_fake_server.py      # CLI 端到端(假 SSE 服务器)
./.venv/Scripts/python.exe scripts/e2e_frozen.py           # 打包后 exe 验证:CLI + GUI 启动
./.venv/Scripts/python.exe scripts/e2e_frozen_mcp.py       # 打包后 MCP:连服务器 → 调工具 → 回填模型
./.venv/Scripts/python.exe scripts/e2e_frozen_sandbox.py   # 打包后沙箱:工作区外触发确认且不泄露内容
./.venv/Scripts/python.exe scripts/e2e_frozen_crawl.py     # 打包后爬取 → 入库 → 命中
./.venv/Scripts/python.exe scripts/build_exe.py            # 轻量版:文件夹+zip+单文件+安装包(先关运行中的实例)
./.venv/Scripts/python.exe scripts/build_crawl4ai_exe.py   # 完整版:crawl4ai+浏览器内核,文件夹+zip+安装包
./.venv/Scripts/python.exe scripts/crawl_bili_topics.py    # 增量更新 B 站视频知识库(--only 关键词)
./.venv/Scripts/python.exe scripts/crawl_wx_topics.py      # 增量更新微信公众号知识库(慢,每篇3跳)
./.venv/Scripts/python.exe scripts/crawl_wx_account.py     # 定向爬广应科公众号的肇庆校区内容(搜狗分页+BFS内链)
./.venv/Scripts/python.exe scripts/crawl_wx_account.py --fix-links      # 只归一化/回填链接(离线)
./.venv/Scripts/python.exe scripts/crawl_wx_account.py --resolve-links  # 百度补缺链接(慢,可断点续跑)
./.venv/Scripts/python.exe scripts/crawl_xhs_topics.py     # 增量更新小红书知识库(登录态失效先跑 --login)
```

> 打包后验 MCP 要用 **onedir 的 CLI exe**(`dist/引路学长/引路学长-CLI.exe`):
> 打包版把 config.json 放在 exe 目录,单文件版是 GUI 形态(无控制台),传任务参数只会开窗。
>
> 模型已固定为内置校园模型,所以 e2e 脚本都在子进程环境里设 `YINLUXS_LLM_BASE_URL`
> (必要时 `YINLUXS_LLM_MODEL` / `YINLUXS_LLM_API_KEY`)把端点临时指到本地假 LLM——
> 改 config.json 里的 providers 已经不起作用(会被内置覆盖)。

## 6. 未完成的待办(路线图,按优先级)

1. **子代理并行**:当前 task 工具串行执行;星瑶支持无依赖子任务并行派发(依赖感知调度)。
2. **计划模式依赖图**:计划项加 `depends_on`,主代理按依赖顺序派发。
3. **MCP 增强**:HTTP/SSE 传输、AI 自助配置(`mcp_add_server` 一类)、预设模板库。
4. **沙箱增强**:项目级白名单(`permissions.json` 式的命令前缀/文件路径授权)、Git worktree 隔离工作区。
5. **附件增强**:拖拽上传(pywebview file_drop)、`fetch_image` 抓网页图片、附件在历史会话里的可视化。
6. **计划模式可视化**:GUI 加计划面板(当前计划文件已在聊天中渲染勾选清单,足够 MVP)。
7. **长期记忆增强**:记忆去重/整理(星瑶的 Dream 线程)、用户级全局记忆(跨项目)。
8. **浏览器验证能力**:纯前端项目页面效果只能人肉点检(见 yinlu-xuezhang/AGENT-PLAN.md §8)。
9. **bash 长命令**:后台任务/输出流式化。

## 7. 新会话上手指南

1. 读本文档 + `README.md`(能力/配置/命令全集)。
2. 跑 `pytest -q` 确认基线(应为全绿;若并行会话在改,先跑全量再动手)。
3. 改代码前**重读要编辑的文件**——本仓库有多个会话并行开发(web_crawl、school-qa、koubei-extract 技能与校园语料爬虫均为并行会话所加),编辑前用当前实际内容做锚点,改完跑全量测试。
4. 相关文档:产品侧 `D:\game\yinlu-xuezhang\PLAN.md`(v2.0 冻结)与 `AGENT-PLAN.md`(用本 Agent 开发该项目的计划);产品知识库 JSON 数据源在 `D:\yy\yinlu-master\yinlu-master\`(已拷贝到本仓库 kb/)。
