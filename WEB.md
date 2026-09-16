# 网页版(校园助手)实现说明

> 新增于 2026-09-13。面向"同学打开网址就能用"的只读问答形态。
> 需求与决策全文见 `D:\game\agent网页版-实施方案.md`;本文件只讲**已实现的东西、为什么这么做、怎么验证**。

## 1. 一句话

同一套引擎 + 同一套前端,多了一个浏览器外壳:同学用校园网打开 `http://<服务器IP>:8480` 就能问校园问题,只能碰校园知识库 / 图书馆馆藏 / 校园地图 / 技能说明。

## 2. 怎么跑

**最省事:双击项目根目录的 `run_web.cmd`**(它直接用 venv 里的解释器,避开"`python` 指向 Microsoft Store 存根"的坑;窗口保持开着,关掉即停止)。需要换端口就 `run_web.cmd --port 9000`。

命令行方式(**依赖在项目的 `.venv` 里,必须用它的解释器**;`py -3.12` 走的是系统解释器,没有 openai 等依赖):

```bash
cd yinlu-xuezhang
./.venv/Scripts/python.exe web_main.py      # Git Bash(注意不是 .venv\Scripts\activate,那是 cmd 语法)
.venv\Scripts\activate && python web_main.py  # cmd / PowerShell
python web_main.py --open                   # 顺带开本机浏览器
python web_main.py --host 127.0.0.1         # 只给自己用
python main.py --web                        # 等价入口
python web_main.py --turn-cap 2 --daily-cap 1000 --sessions-dir D:\web-sessions
```

首次启动 Windows 会弹防火墙询问,**要允许专用网络**,否则局域网内其他设备连不上本端口。

**`frontend/index.html` 打不开时**:控制台会打印缺哪个文件 —— 在项目根目录运行,或确认分发包含 `_internal/frontend`。

**`run_web.cmd` 必须保持纯 ASCII**:cmd.exe 按本地 ANSI 代码页解析 `.cmd`,里面写中文会被拆成乱码命令(实测报 `'t' 不是内部或外部命令`);中文提示一律交给 Python 输出(它本来就是 UTF-8)。

### 打不开时的排查顺序

1. **控制台有没有打印"已启动"那几行**?没有就是服务没起来 —— 看上面的解释器问题。
2. `netstat -ano | findstr 8480` 有没有 `LISTENING`。
3. 浏览器地址必须是 `http://`(不是 https),本机用 `http://127.0.0.1:8480`。
4. 本机能开、别的设备不能开 → 防火墙没放行,或不在同一网段(见下面"部署前提")。

开发时看效果(带本地假模型,不连校园网):

```bash
python _tmp_dev_check/serve_web_e2e.py --port 8481
```

用真校园模型做一次冒烟(问一个问题就退出):

```bash
python _tmp_dev_check/check_real_llm.py
```

**部署前提**:服务必须跑在能访问校园内网模型端点(`10.223.24.16:8080`)的机器上。注意"能访问模型端点"不等于"机器在校园网里" —— 本机只有一个 `192.168.31.x`(家用路由器网段)时,横幅会打印"局域网访问"并明确警告:校园网里的同学打不开这些地址,得把服务跑在校园网内的机器上。

**响应速度**:实测一次"图书馆在哪 + 开放时间"要 38 秒(模型调了 `map_find_place` / `library_guide` / `search_knowledge` 三个工具)。多工具问题就是这个量级,所以全局并发上限默认压到 3,避免把校园模型端点打满。

## 3. 结构:新增三个模块,引擎零改动

```
浏览器(frontend/index.html 一行未改)
  ├─ POST /api/<方法名>   入站:门面白名单里的方法
  └─ GET  /api/events    出站:SSE 事件流
        ▲
        │ frontend/bridge-web.js(服务时注入 <head>,把 pywebview.api 换成 fetch)
        │
minicode/web_server.py   ThreadingHTTPServer + 路由 + SSE + 静态文件 + 访客/限流
minicode/web_ui.py       WebUI(GuiUI 子类):push → 队列;confirm_tool 一律拒绝
minicode/web_profile.py  工具白名单 + 对外系统提示词 + PublicFacade
        ▼
[ 引擎原样 ] agent.py / llm.py / tools/* / sandbox.py / session.py / kb / library …
```

复用点:`gui_bridge.Bridge` 已经是多运行器结构(`SessionRunner` + `SessionGuiUI` 按会话打事件标记),所以"每个访客一套会话"直接建立在这套机制上,没有新增并发模型。

- 每个浏览器访客一个 cookie 身份 → 一个 `WebBridge` 实例(私有 `sessions_dir`)→ 一份 `LLMClient`(很轻)。访客之间不共享任何内存状态。
- 对外模式**不连 MCP**:不拉起任何第三方子进程。
- 前端零改动靠 `bridge-web.js`:方法表按位置参数 → 具名参数映射,`open_url` 就地用 `window.open`(否则会在服务器上开浏览器),并用注入的 CSS 藏掉所有者专属控件(密钥/沙箱/撤销/附件/权限模式)。**工作区胶囊**不用 CSS 藏 —— 它自己看 `get_info().workspace` 是不是空,空就整个隐起来(这五个方法在对外门面已整组拒绝,见下节第 4 条)。

## 4. 安全不变式(改动时别破坏)

| # | 不变式 | 为什么 |
|---|---|---|
| 1 | 工具集来自 `PUBLIC_TOOLS` **显式白名单**,绝不用 `Tool.read_only` 过滤 | 该标志语义是"执行前不需要确认",不是"读不到敏感数据" —— `read_file`/`glob`/`grep` 都是 `read_only=True` |
| 2 | 工作区里不能有密钥文件;内置 Key 走 `YINLUXS_BUILTIN_API_KEY` 环境变量注入 | `config.local.json`(含校园 Key)就在工作区根目录,第 1 条一旦写错,读文件类工具就能拿走它 |
| 3 | `get_asset` 只放行图片扩展名 | 它本身是覆盖整个工作区的文件读取原语 |
| 4 | 对外门面不开放:供应商/密钥、沙箱开关、权限模式、附件、记忆整理、`consolidate_memory`、**工作区管理（`get_workspaces`/`pick_workspace`/`switch_workspace`/`reset_workspace`/`forget_workspace` 五个方法整组拒绝）** | 前两项会写 `config.json`;记忆里是所有者自己的笔记;**工作区是服务器自己的目录选择 —— 访客既不能看见服务器路径也不能改它**(前端拿到空 `workspace` 就把胶囊整个隐起来) |
| 5 | 对外系统提示词**不注入** `memory/MEMORY.md`,也不用编码 agent 那套工作准则 | 提示词写着"你能读写文件"而工具里没有,模型会胡言乱语并误导同学 |
| 6 | 会话浏览只在自己访客目录内 | `list_sessions(sessions_dir)` 是按目录扫的,共用目录等于把所有者全部历史对话(可删)交给同学 |
| 7 | `Origin`/`Referer` 与 `Host` 同源校验 | 否则你浏览器里任意网站都能 POST 本机端口来驱动这台服务器 |
| 8 | `WebUI.confirm_tool` 永远返回 `"no"` | 确认通道是"单例 Event + 前端点击唤醒",无登录多访客下没法安全配对,没人点就永久挂住一个线程 |
| 9 | 新回合开始前清掉 `stop_requested` | 那个停止标志是跨回合的,而 SSE 断开(刷新/切标签/自动重连)是常态,不清会让下一轮在第一个增量处被打断 |
| 10 | 只读工具集之外必须排除 `task`(子代理) | 子代理可携带 implement 类写工具,绕过白名单 |

## 5. 滥用控制(无登录时的唯一手段)

| 项 | 默认值 | 位置 |
|---|---|---|
| 每访客限流 | 12 次/分钟(令牌桶) | `RateLimiter` |
| 全局并发 | 3 个回合同时进行,超出回"前面还有 N 位同学" | `WebApp.try_reserve_turn` + `on_busy` |
| 每日总量 | 2000 次(内存计数,重启清零) | `DailyCounter` |
| 访客回收 | 30 分钟无动静则关闭(停掉空跑回合) | `WebApp.evict_idle`(60 秒扫一次) |
| 用量留痕 | 时间 / 来源 IP / 访客号 / 提问前 80 字 → `web-usage.log` | `WebApp.log_usage` |

并发计数靠 `busy` 事件(回合开始/结束的唯一可靠信号),`reserved_turns` 解决"检查名额"与"真正开跑"之间的空窗。

## 6. 验证

```bash
./.venv/Scripts/python.exe -m pytest tests/test_web_server.py -q   # 15 项
./.venv/Scripts/python.exe -m pytest -q                            # 全量
./.venv/Scripts/python.exe scripts/e2e_web.py                      # 端到端(假模型 + 真实 HTTP)
```

`tests/test_web_server.py` 钉住的边界:首页 shim 注入、静态目录穿越被挡、所有者专属方法一律拒绝、未知方法(含 `bash`/`read_file`/`task`)拒绝、`get_info` 脱敏(工作区路径/内网端点/Key 都不出现)、`get_asset` 只放行图片、注册表 ⊆ 白名单且全部 `read_only`、系统提示词不含记忆与编码准则、跨站 Origin 403、限流与配额、两访客上下文与留档互不可见、SSE 完整回合(text_delta / tool_start / tool_done / finish_stream / busy)、并发上限拒绝第二位同学、坏请求体 400。

`scripts/e2e_web.py` 是真 HTTP 全链路(含 `search_knowledge` 真跑一次知识库)并复核越权与跨站。

**浏览器实测(2026-09-13)**:`_tmp_dev_check/serve_web_e2e.py` + 内置浏览器打开页面 —— 启动后 `#ver` 显示版本、顶栏模型标识正确、首页与建议 chips 正常、所有者控件已隐藏;输入问题回车发送,回复经 SSE 流式渲染,工具卡显示 `search_knowledge query=图书馆 ✓`,思考与工具调用折叠成"💭 工作过程 · 1 个工具调用",无报错提示。

## 7. 已知缺口(按优先级)

1. **打包未接**:`web_main.py` 还没进 PyInstaller spec(前端目录本来就整包打,`minicode/` 里的新模块会被自动收集;需要的是给 exe 加一个"网页版"入口,以及 `--web` 参数的启动脚本/快捷方式)。打包前记得先跑全量测试确认工具注册表一致。
2. **没有开机自启/常驻方案**:服务器被关机或进程被杀就断了,建议补一份任务计划程序/nssm 说明。
3. **SSE 断线不补发**:页面刷新会丢正在流式渲染的那半段(会话留档在服务端,`refresh_sessions` 能拿回完整历史,但当前回合的中间态丢失)。
4. **`web-sessions/` 没有保留策略**:目前只靠访客回收(内存态)与手动清理,长期运行建议按天清理磁盘目录。
5. **没有登录**:用户明确选择"校园网内完全开放",因此无法追责到人,只能做到按 IP 限流 + 日志。
6. **对外提示词是模块内常量**(`web_profile.PUBLIC_PROMPT`),不像 `system_prompt.md` 那样可以外部编辑 —— 当初为了不改打包 spec 才这么做;若要做成可配置资源,记得同步 spec 的 datas。
