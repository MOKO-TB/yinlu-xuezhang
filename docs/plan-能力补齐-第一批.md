# 能力补齐 · 第一批计划书

> 生成时间:2026-10-09
> 代码基线:v0.9.138(HEAD `7ec5e37`;`build_default_registry()` 实测 **59** 只工具、其中 **27** 只只读)
> 对标依据:`coding-agent-capability-matrix.md`(§9 汇总表 / §10D 差距自检)、`docs/ROADMAP_IMPROVEMENTS.md`,以及 2026-10-09 对当前工作树的一次逐条读码复核
> 取证方式:本计划书里每一处「现状」都带 `文件:行号`,行号取自 `7ec5e37` 这一版工作树。**引用前先 `git log -1`** —— 这份工作树有多个会话并行改,行号会漂。可复核的命令列在 §八。
> 状态:**计划书本体;未改任何代码,未新增工具。**
> 进度修订(**2026-10-10,五件全部交完**):
> - **1-1 MCP server 模式** → **v0.9.144 已交**(`minicode/mcp_server.py` + `--mcp-serve`,只做 stdio 单客户端、白名单显式列举、外部 `tools/call` 走同一道门且 ASK 一律拒;测试 +14)。
> - **1-3 日预算从「提醒」改成「硬闸」+ 每任务预算** → **v0.9.144 已交**(顺手补了计划外的 `task_token_budget`;触顶在两步之间收尾、必须说出口;测试 +15)。
> - **1-4 无头加 `--output-format stream-json`** → **v0.9.144 已交**(NDJSON、`seq` 严格递增、最后一行是与 `--json` 同构的 `result`、与 `--json` 互斥、与 `--output-schema` 正交;测试 +22)。
> - **1-5 `bash_input`** → **v0.9.146 已交**(唯一开关是 `stdin=DEVNULL`→`PIPE`;独立 `stdin_lock` 防死锁、超长拒绝不截断、不做 Ctrl+C/EOF;顺手按领域拆出 `catalog_bg.py` 解掉 400 行墙;测试 +9,另加 `e2e_bg_jobs.py` 第 4 场真进程验证)。
> - **1-2 `diagnostics`** → **v0.9.147 已交**(新模块 `minicode/diagnostics.py` + `catalog_diag.py`;与语法底线**分权不取代**;「没做检查」绝不被说成「检查过了没问题」;测试 +18)。
>
> **一处偏离**:§1-2 的落点清单要在 `hook_packs` 再加一条「改完跑检查器」的配方 —— 1-2 已把这件事做成**原生且自动**的,再加一条钩子就是给同一件事造第二处真相,所以**不做**(理由记在 `HANDOFF.md` §2 v0.9.147 那一行)。
> **一处修正**:本文 §1-5 的落点清单写「`bash_input` 的 `Tool` 插在 `catalog_files.py` 的 `bash_kill` 之后」,而那份目录当时已经 398 行、闸在上限 400 —— 照原文做会当场撞闸。实际做法是连同后台三只一起拆出 `catalog_bg.py`,再把 `catalog_files.tools()` 拆成 `tools()` + `script_tools()`,好让 `registry.py` 把后台那一族精确插在 `bash` 与 `py_script` 之间(顺序即行为面,不因拆文件而变)。

---

## 〇、这份计划书要解决什么

上一轮对标复核的结论是:本项目在**功能面**上已经追上甚至超过八家竞品平均水位(注入防御闭环、计划验收闸、七个语义化退出码、个人数据闭环、microcompact、审批四档 × 记住批准,**这些都是竞品没有或没做全的**),真正落后的是三块**能力密度**:

1. **操作系统级隔离** —— 现在只有 Job Object(进程树回收),没有降权/AppContainer,也没有真正的出站网络策略;
2. **语言智能** —— 只有 Python 的 `ast.parse` 语法自检(`minicode/agent.py:2059`),没有类型/lint 诊断回灌,没有语义代码检索,没有符号级导航;
3. **子代理规模化** —— 有 6 路并行与外部档位文件(`minicode/agent_profiles.py`),但没有嵌套、没有 worktree 隔离、不能给跑着的子代理递话。

这三块都偏"大件"。**第一批刻意只挑五件"投进去小、回报立刻看得见、彼此不打架"的**,共同点是:**没有一件碰权限门的核心判定,也没有一件碰沙箱边界** —— 所以回归面可控,可以一轮一件地交。

五件按性质分三类:

| 性质 | 编号 | 一句话 | 为什么现在做 |
|---|---|---|---|
| 把已有的**半格**补满 | 1-3 日预算硬闸 | 会话预算早有硬闸,日预算只 `push("info")` 提醒 | 机制、账本、聚合函数全都现成,差一个判定位置 |
| | 1-4 无头 `stream-json` | 无头只认 `text`/`json`,七码退出码已经很细 | `headless.py` 自己写着"没做",理由(无界面)在本条不成立 |
| 新**入口** | 1-1 MCP server 模式 | 让 Claude Code / Cursor / Qoder 直接调本项目的只读工具 | 注册表 + stdio JSON-RPC 解析全是现成的,成本最低的差异化 |
| 新**工具 + 新模块** | 1-2 `diagnostics` | 把"改完知道对不对"从 Python 语法扩到多语言 | 补的是编码能力里最实的一块闭环 |
| | 1-5 `bash_input` | 给后台任务写 stdin | 解开 `npm init` / REPL / 要 y-N 的脚手架这类命令 |

**不在这份计划书里的**(属于批次 2/3,另有 ROI 排序,混进来会让一轮改太多层):OS 沙箱 S5b/S5c 与出站域策略、代码语义索引(`embed.py` 复用)、对话级 `rewind`、子代理 worktree 隔离与 steer、MCP OAuth 与 prompts/sampling/elicitation、自动记忆、语音输入、生态规则文件兼容(`.cursor/rules` 等)。

> 一条口径先写在最前面:**这五件都不许"假装做了"**。检查器没装就说没装、沙箱/服务起不来就如实说未启用、事件流没落地就不许把 `text` 说成 `stream-json`。这个项目最贵的坏法是"界面上说安全/说支持,实际没拦/没做",本轮每一节的「诚实边界」都是照这条写的。

---

## 一、总览

| 编号 | 缺什么 | 竞品对照 | 依赖 | 建议轮次 | 风险 | 动工具面? |
|---|---|---|---|---|---|---|
| 1-3 | 日预算只提醒不成闸 | ZCode goal `tokenBudget` 自行 `budget_limited`;Cursor Cloud spend limit | 无 | 第一轮(最小,先走) | 低 | 否 |
| 1-4 | 无头无 `stream-json` | Codex `codex exec`、Claude Code `-p --output-format stream-json`、ZCode 三值输出 | 无 | 第一轮 | 低 | 否 |
| 1-1 | 自身不能作 MCP server | Claude Code `claude mcp serve`;ZCode/Qoder 均无 | 无 | 第一轮 | 低-中 | **否**(是入口,不是工具) |
| 1-2 | 编辑后只有 Python 语法自检 | Cursor/Cline/Roo 有 LSP 诊断回灌;Aider `--auto-lint` 默认开 | 无 | 第二轮(与 1-5 合并) | 中 | **是**(+1 只工具) |
| 1-5 | 后台任务不能写 stdin | Cline/Roo 的终端集成可往终端里打字;Codex 有 background terminal | 无 | 第二轮(与 1-2 合并) | 中 | **是**(+1 只工具) |

**建议顺序的理由**:1-3 与 1-4 是"补半格",各自独立、几乎不可能互相影响,先走能把版本号推起来、把回归基线刷新一次;1-1 独立于工具面(它加的是入口不是工具),放第一轮末尾能顺带用上刷新后的基线;1-2 与 1-5 **必须合并成一轮** —— 它们要改同一处 `minicode/tools/registry.py` 的装配顺序、同两张数字表(见 §2.2),分两轮就是把同一笔"文档/顺序税"交两遍。

> ⚠ 五件**不要一次提交**。每件独立提交、独立跑全量、独立点版本号 —— 这个仓库的回归面靠"一轮一个可回退单元"控制,合并提交会让出问题时无法二分。

---

## 二、共同前置与共同税(每一件都要交)

这个仓库对「新增东西」的硬规矩写在 `CONTRIBUTING.md`,这里不重抄一份,只列本轮五件**一定会碰到**的那几笔税。

### 2.1 每次改完必跑(不许跳)

```bash
python -m pytest -m "not slow" -q        # ① 快车道(实测约 100 s)
python -m ruff check .                   # ② 静态检查
python scripts/check_docs.py --collect   # ③ 八道闸 + 测试基线条数对账
python scripts/check_frontend_js.py      # ④ 前端内联 JS 语法(需 UTF-8 控制台)
python scripts/gen_changelog.py          # ⑤ 重新生成 CHANGELOG
python -m pytest -q                      # 提交前再跑一次全量(实测约 350 s)
```

两条容易踩的:**①② 不要带 `PYTHONIOENCODING=utf-8`**(会让 `test_kb_credibility` 的子进程采集假红),**④ 必须要**这个环境变量。①③ 的耗时大户都打了 `slow` 标记,名单与秒数在 `pytest.ini` 里(`tests/test_markers.py` 会钉住"快车道里不许有那五个文件")。

### 2.2 工具面税(1-2 与 1-5 会触发)

**每加一只工具,要同时动五处**,漏一处就有闸会红:

| # | 要动的东西 | 唯一真相 / 谁来盯 |
|---|---|---|
| 1 | 装配顺序:`minicode/tools/registry.py::build_default_registry()` | 文件 docstring 明写「顺序不许随手改」,模型每轮拿到的 tools 数组就是这个顺序 |
| 2 | 顺序表:`tests/test_registry_split.py:28` 的 `ORDER`,以及 `:111` 那一行的 `len(ORDER) == len(set(ORDER)) == 59` 字面量 | 逐字钉住;`test_order_pin_is_not_stale` 还会拿运行期结果反查 |
| 3 | 三份活文档里的工具总数:README(`README.md:56` 那句「59 个内置工具」)、`system_prompt.md`、随包《使用说明》 | `scripts/check_docs.py:376 check_agent_doc_sync()` 拿 `build_default_registry()` 实测核对(`:384-396`) |
| 4 | 网页版表态:`minicode/web_profile.py:53 EXCLUDED_TOOLS`(或在白名单里加一条) | 表里的每一条都要求**写清为什么**,测试会断言它不在网页白名单里 |
| 5 | 冻结针:`scripts/check_frozen_version.py::TARGETS`(若新增了配置项/新模块/新公开符号) | `tests/test_frozen_targets.py` 盯针本身。漏收的表现是「配置里写了没人读」 |

所以本轮的算术是:**59 → 61 只**。1-2 与 1-5 合并一轮,是为了让这张表只动一次。

### 2.3 新增落盘文件 → 四处登记税

若某条引入新的本机文件(本轮预计**没有**,见各节的落点清单),必须同时进:

- `minicode/state_files.py:51 SHIP_NEVER`(若绝不外发)
- `.gitignore`
- `minicode/mydata.py:101 TABLE`(个人数据一览)
- 打包清理清单(`scripts/build_exe.py` 走的就是 `SHIP_NEVER`)

> 本轮的一个设计取向:能**复用现有账本**就不新开一份。例:1-1 的调用留痕建议复用 `provenance.log_escalation()`(`minicode/provenance.py:81`,写 `injection-events.log`,已登记、已有滚动上限),而不是新建 `mcp-server.log` —— 新开一份就要交上面这笔税,还会造出第二份"安全留痕"的真相。

### 2.4 网页版表态(逐条)

五件里凡是**新增工具**的(1-2、1-5),都要在 `EXCLUDED_TOOLS` 里逐条写下理由。**本轮不新增任何网页端能力**:网页版是访客只读问答(见 `minicode/web_ui.py:52` 的 `confirm_tool` 一律拒绝),而 1-5 是"往一个正在跑的进程写数据"、1-2 是"读服务器工作区的源码" —— 两条都不该给访客。1-1 是**另一个入口**,不改网页版。

### 2.5 文档税

每条都要动:`HANDOFF.md` §2(新行 + 按规矩把最老的搬进 `docs/handoff/`,主文档只留最近 5 轮)、§6(改口/收口)、§5 与 §7(基线条数)、`README.md`、`system_prompt.md`、随包《使用说明》(`scripts/build_exe.py::write_user_files` 的编号列表)。

> 提醒:`check_docs.py` 的「档位与数字口径同步」只查三份活文档(README / system_prompt / 使用说明),`HANDOFF` 与 `milestones` 的历史叙述**不在管辖内** —— 旧轮次写「59 只」是当时的实情,把它改成 61 就是造假冒充当前基线。

### 2.6 提交规范

标题里**点版本号**,一行说清这一轮做了什么、按谁的口径做:`v0.9.139 p1: 日预算从提醒改成硬闸(用户点名,只本地提交)`。没点版本号的提交(纯文档、跑测、重出包)视为过程性提交,不进 CHANGELOG。

---

## 三、逐条实施计划

> 五节的行号都取自 `7ec5e37`。凡是标「**新增**」的是本方案要创建的东西,其余都是已存在的落点。

### 1-1 MCP server 模式

#### 定位

把本项目已有的**校园只读能力**接上外部生态:让 Claude Code / Cursor / Qoder 一类 MCP 客户端直接调 `library_search`(133 万条离线馆藏)、`campus_calendar`、`gpa_calc`、`search_knowledge`、地图五件等。这三条理由都落在现有件上:

1. **协议侧几乎现成**:JSON-RPC 逐行 stdio 的编解码、按 id 等响应、错误还原,客户端侧已写全(`minicode/mcp.py:398-413`、`:420-428`、`:788-801`);服务端要写的是同一形状的另一半。
2. **工具侧完全现成**:工具是数据(`minicode/tools/base.py:82-96`),handler 签名与 CLI/GUI 调的是同一个,不必写第二套实现。
3. **已经有"对外收敛"的样板**:网页版把 59 只工具收成显式白名单 + 逐条排除理由(`minicode/web_profile.py:37-50`、`:53-128`),并证明"确认通道一律拒绝"在这一架构里可行(`minicode/web_ui.py:52-63`)。

> 竞品口径:Claude Code 有 `claude mcp serve`,这一条**本仓库无法核实**(HANDOFF 里只有客户端侧调研记录),计划书按"未核实"标注,不作为论据强度 —— 选它的理由是上面三条本地理由。

#### 现状与缺口

| 已有 | 位置 |
|---|---|
| 工具注册表(唯一装配点) | `minicode/tools/registry.py:29-56`(现 50 行,docstring 明写顺序不许改) |
| `Tool` 字段与 JSON Schema 参数 | `minicode/tools/base.py:82-96`(`parameters` 在 `:85`,必须是 object) |
| stdio 逐行协议与错误形状 | `minicode/mcp.py:347-349`、`:398-413`、`:420-428`、`:788-801` |
| 协议常量与"不假装支持"口径 | `mcp.py:54`(`PROTOCOL_VERSION = "2024-11-05"`)、`:59`(`CLIENT_PRIMITIVES`)、`:710`(握手声明空 capabilities) |
| 权限门(与 CLI/GUI 同一份) | `minicode/permission.py:684-818`(`check()`),三值 `ALLOW/ASK/REJECT` 在 `:36-38` |
| 无人点头就拒绝的两处先例 | `web_ui.py:52-63`(对外一律拒)、`minicode/headless.py:209-216`(记 `asks_refused` 回 `"no"`) |
| 装配次序(照抄即可) | `minicode/app_setup.py:306-318`(ToolContext+沙箱+注册表)、`:345`(沙箱构造) |
| 入口与参数风格 | `main.py:679-698`(`pop_workspace_flag`)、`:701-712`(`startup_workspace`,不合法 `SystemExit(2)`) |
| 协议形状的现成参照 | `tests/fake_mcp_server.py`(真能跑的 stdio JSON-RPC 服务端:`initialize`/`tools/list`/`tools/call`,UTF-8 处理在 `:12-19`) |
| 安全留痕账(已登记、有滚动上限) | `minicode/provenance.py:81-126`(`log_escalation` → `injection-events.log`,记录形状 `:107-114`) |

**缺什么**:入站 JSON-RPC 分发器(全仓没有)、`initialize`/`tools/list`/`tools/call` 的服务端实现(只存在于测试替身)、服务端能力声明、stdio 生命周期与退出约定、面向外部调用方的工具白名单。

#### 设计决策

- **传输先只做 stdio**:与客户端侧形状逐字对齐,一个 `for line in sys.stdin` 加一对读写函数即可;不开端口、不要证书。**不做 http/sse** —— 一开 http 就必须同时回答鉴权、多客户端并发、工作区归属三个问题,第一版一个都答不上来;且 `web_server.py` 的 `serve` 已经占了"对外 HTTP 服务"这个心智位置。
- **外部来的 `tools/call` 走同一道门**:判定一律 `PermissionGate.check()`,不写第二份。**ASK 一律拒绝**,不做替代决策 —— 与 `web_ui.py:52-63`、`headless.py:209-216` 同构。理由:即使 `permission_mode` 是 `full`,`check()` 仍会对越界路径、工作区凭据文件、`.git` 写入、外部内容升格、项目 ask 规则、规划锁返回 ASK(它们全都排在档位判断之前);而且沙箱的越界授权是**给本机用户当场点的那一次头**(`permission.py:414-425`),外部客户端没有这条路径,把它当"已授权"就是凭空造一个用户。
- **不做例外档**(不给 `--mcp-yolo`):口径同 `headless.py:9-15`「自动化不是绕过确认的第二条路」。要放开请在 `config.json` 改沙箱/权限档,那是本机主人的显式选择。
- **白名单显式列举,绝不用 `Tool.read_only` 过滤**:`web_profile.py:5-8` 的教训 —— `read_file`/`glob`/`grep` 都是 `read_only=True`,而工作区根就有含内置 Key 的 `config.local.json`。

| 建议暴露 | 声明位置 | 说明 |
|---|---|---|
| `library_search` / `library_guide` | `minicode/tools/catalog_places.py:18-70` | 离线馆藏,对外核心资产 |
| `campus_calendar` / `gpa_calc` | `catalog_campus.py:21-109` | 纯本地查/算,无落盘 |
| `search_knowledge` | `catalog_knowledge.py:128-153` | 本地库检索,带来源与可信度 |
| `campus_notices` / `get_weather` | `catalog_remind.py:200-238` | 只读取数;**若定"服务端不出网"则一并排除** |
| 地图五件 | `catalog_places.py:77-173` | 只读;图形效果依赖 `ctx.ui`,无界面时只剩文字 |

- **坚决不暴露**:`bash`/`py_script`/`write_file`/`edit_file`/`apply_patch`(等于把本机交出去 → 无条件远程代码执行)、MCP 管理面(`mcp`/`mcp_resource`)、文件系统原语(`read_file`/`glob`/`grep`/`repo_outline`)、私有数据(`scores`/`mood`/`memory`/`reminder`/`schedule`)、`ask_user`(要人在场点)、`bash_status`(在新进程里没内容,且是"本机在跑什么"的痕迹)。
- **工作区沿用既有参数** `--workspace/-w`,不新增 `--mcp-workspace`;不合法路径按既有语义退 2。**不建 Agent、不跑模型、不连出站 MCP**,因此也不加载 AGENTS.md(它只进系统提示词,而这里没有系统提示词)。
- **审计只记"被拒的那一次"**,复用 `provenance.log_escalation()`(拒绝天然带 `note`),**不开第二本账** —— 那本账的定位是"只记安全门开口的动作,量极小"( `provenance.py:92-94`),全量记录会把它变成噪音账。给它加一个可选 `client` 字段。
- **`clientInfo` 是自述标签,不是身份**:stdio 下一进程一客户端,"谁在调"= 本次 `initialize` 的 `clientInfo.name/version`,无凭据无校验,只能进日志,不能进判定。
- **重复 `initialize` 只更新标签并如实报**,绝不假装支持多会话。

> **待拍板(本方案的推荐值)**:① 服务端**不**同时连出站 MCP(第一版;避免主人的 MCP 凭据被外部客户端间接使用);② 审计**只记拒绝**(理由见上)。两处若要改口径,改的是这一节的落点清单,不动架构。

#### 落点清单

| 文件 | 改什么 |
|---|---|
| **新增** `minicode/mcp_server.py` | `serve(config, workspace)` 主循环;`SERVE_TOOLS` 白名单 + `EXCLUDED_TOOLS: dict[str, str]`(逐条理由,形状照 `web_profile.py:53-128`);`_dispatch(msg)` 处理 `initialize`/`notifications/initialized`/`tools/list`/`tools/call`/未知方法;`_spec(tool)` 把 `Tool` 转 `{name, description, inputSchema}`(直接用 `Tool.parameters`);按 `app_setup.py:306-318` 的次序装 runtime(不建 LLM/Agent/子代理);一个 `_NoUI`(吞掉 `push`/`info`)供 `ctx.ui` 用 |
| `main.py` | 新增 `pop_mcp_serve_flag(argv)`(与 `pop_workspace_flag` `:679-698` 同风格);分支必须在 `TerminalUI`(`:845` 一带)**之前** return —— 它会往 stdout 打横幅,而 stdout 只能是协议报文;`--mcp-serve` 与 `--json` 互斥,同时给按参数错退 2 |
| `minicode/headless.py` | 不改:复用 `ensure_utf8_stdout()`(`:84`)钉 UTF-8、复用 `EXIT_ARGS`。**不要**复用 `HeadlessUI`(`:134`) —— 它包的是 Agent 回调面 |
| `minicode/app_setup.py` | 第一版**不动**(装配次序照抄)。要共用再抽 `build_offline_runtime()`,别现在改 `create_agent` 的调用面 |
| `README.md` | 「MCP 服务器」一节加"本 agent 也可作 server 被调用";CLI 用法表加一行;目录树加 `mcp_server.py`。**工具总数不变(59)**,所以工具数对账不用动 |
| **新增** `tests/test_mcp_serve.py` | 见验收判据;命名与 `tests/test_mcp_transport.py` 一族对齐 |
| `minicode/state_files.py` + `.gitignore` | **仅当**真新增本机数据文件(如全量审计账)才登记;本方案不需要 |

#### 验收判据

| 判据 | 具体断言 |
|---|---|
| 最小会话跑通 | `initialize` → 有 `protocolVersion`/`capabilities.tools`/`serverInfo`;`notifications/initialized` 不产生响应;`tools/list` 条数 == 白名单条数 |
| 只读工具真能调 | `tools/call` 调 `library_search`(中文书名)→ `content[0].type == "text"`、`isError` 不为真、中文不乱码 |
| **负例:写类被拒** | `tools/call` 调 `write_file` → 工具级失败且文本点名"需要人确认、当前无人在场",**不是** `-32601` 未知方法 |
| **负例不是假绿** | 临时把白名单放进 `write_file` 时能真写(证明拒绝来自权限门,不是"工具压根没挂");同时静态断言它在排除表里 |
| 协议错误分得清 | 未知方法 `-32601`、未知工具 `-32602`、坏 JSON 行忽略并继续(形状照 `tests/fake_mcp_server.py:83-88`、`:102-106`) |
| stdout 纯净 | 整场会话 stdout 每行都能 `json.loads`(契约同 `headless.py:1-8`) |
| 端到端自证 | 用自家 `McpClient` 连本服务端走完三步(两边形状不一致当场暴露) |
| 生命周期 | stdin 关闭 → 退出 0;`--workspace` 不存在 → 退出 2 |
| 审计留痕 | 被拒那一次在 `injection-events.log` 出现一行,含 gate/tool/why 与 `client` |
| 基线不掉 | `pytest -q` 仍全绿(当前基线 3553 项,`HANDOFF.md:832`) |

#### 诚实边界

不做 http/sse、不做 `prompts`/`sampling`/`elicitation`(口径见 `docs/roadmap-MCP-resources-prompts.md:22-25`、`:45-52`)、不做服务端发起通知/进度(本仓库客户端把 `id` 为空的报文直接丢弃)、不做并发多客户端、不做远程鉴权、不"顺手放开一点"白名单、不做提示词注入面。

---

### 1-2 `diagnostics` 工具 + 编辑后诊断回灌配方

#### 现状

| 事实 | 位置 |
|---|---|
| 唯一的编辑后检查是 **Python 语法级** | `minicode/agent.py:2059-2093`(`_py_syntax_feedback`),调用点 `:1426` |
| 它覆盖的边界 | 只对 `write_file`/`edit_file`(`SYNTAX_CHECK_TOOLS`,`:39`)、只对 `.py`(`:2075`)、只对成功落盘(`:2071-2073`)、≤512 KB(`SYNTAX_SCAN_LIMIT`,`:40`,判定 `:2082`)、只做 `ast.parse`(`:2088`),报「第 N 行 + 原文」(`:2090-2092`) |
| 类型/lint/未定义名字/`.ts` 类型错/测试失败 | **一条都不覆盖** |
| "改完跑测试"是可选钩子配方 | `minicode/hook_packs.py:91-106`(`run-tests`),声明在 `:78` 的 `TEMPLATES` |
| 默认一条都不挂 | `hook_packs.py:11-12` 明写理由;`list_line` 会如实说"一条都没挂" |
| 三步纪律 | `preview`(`:156`,不写任何东西 + 一次性凭据)、`apply`(`:192`,先备份旧 config) |
| 钩子能回灌的键 | `minicode/hooks.py:152-154`、`:242-249`:只有 `updated_output`(正文,追加不替换)与 `context`(一行附注);post 载荷现在带 `args`(`:163-170`) |
| 仓里那句口径 | `coding-agent-capability-matrix.md:351`:「**不可关**的语法反馈底线 `_py_syntax_feedback`(刻意不被 H6 替换,避免两套真相)」 |

#### 设计

**① 新增 `minicode/diagnostics.py`:按扩展名挑检查器**(全部是固定 argv,不接受模型给的命令/参数/环境变量/shell 字符串):

| 扩展名 | 检查器 | 探测 | 必须如实说的话 |
|---|---|---|---|
| `.py` | `ast.parse`(底线,沿用现口径) | 标准库,永远在 | 不归本模块开关(见 ⑤) |
| `.py` | `ruff check --output-format concise --no-cache` | `find_spec("ruff")` | 打包版**一定没有**(dev 依赖,见 `tests/test_lint_gate.py`)→ 跳过并说明 |
| `.ts`/`.tsx` | 工作区 `node_modules` 下的 `tsc --noEmit` | 找 `node_modules/.bin/tsc*` | 报的是**整个项目**的类型图 |
| `.js` | `node --check`(eslint 存在才跑) | `shutil.which("node")` | eslint **会加载并执行**工作区插件 |
| `.go` | `go vet ./`(cd 到该文件目录) | `which("go")` | 粒度是**包**,文案必须写清 |
| `.rs` | `cargo check` | `which("cargo")` | 粒度是整个 **crate**,且会跑 `build.rs` = **会执行工作区代码** |
| 未知 | 什么都不做 | —— | 如实说"没有这个文件类型的检查器" |

**② 检查器不存在就跳过并说明;走内部 subprocess,不走 bash 链。** 走 `bash` 意味着每次编辑后弹一次确认框,用户点几十次才能换来一次自动回灌 —— 那等于把闭环做成没有。而 `_run_config_hook` / `hooks.py` 已确立先例:**用户配置过的、argv 固定的自动命令不经确认框**(风险写进 preview)。代价是必须把可执行面收死:无 shell、无模型可控参数、无环境注入、argv 由扩展名查表得出,路径先过 `resolve_path`(`tools/base.py:126-155`);**cwd 固定工作区**(`hooks.py:258-261` 记过打包版 cwd 的坑);**超时自己有一条**并明确"没跑完就说没跑完",绝不把超时当通过。

**③ 权限姿态**:`diagnostics` 工具必须 `read_only=False`(会执行工作区代码),与 `hooks`(只读列清单)形成对照:**列清单只读、真跑检查器要点头**。

**④ 去重与"没修好"**:缓存挂 agent 实例(同 `_hook_bad` 的口径)。键 `(相对路径, 检查器 id)`;指纹 = 规范化后(去绝对路径与耗时)的「行号+规则码+首行消息」取哈希。文件变了但指纹相同 → **不沉默**,回一行"仍是上次那 N 条,这次改动没有解决它";同指纹连续 ≥2 次压成一行;文件删除则清键。比"重复就彻底不说"好:沉默会把"你改了但没修好"变成没人知道。

**⑤ 与 `_py_syntax_feedback` 的关系:并存但分权,不取代。** 三条理由:仓库已把口径写死(`matrix:351`);打包版里 ruff 一定不在(`tests/test_lint_gate.py`:dev 依赖不进包),一旦取代,装好的包里 `.py` 检查会掉进"检查器不存在"分支 = 底线没了;两套真相的具体危害是"同一文件两句不同的话",分权后**语法结论只有一处**(`ast.parse`),`diagnostics` 的 `.py` 档**只看 ruff**。配套小重构:把 `SYNTAX_CHECK_TOOLS`(`agent.py:39`)与 `SYNTAX_SCAN_LIMIT`(`:40`)提到 `diagnostics.py` 当公共口径,两处共用同一份边界;**不换 `compile()`/`py_compile`**(前者是行为变更,后者有写 `.pyc` 的副作用)。

**⑥ 回灌位置**:诊断正文插在 post 钩子正文之后、**`_truncate` 之前**(`agent.py:1420-1424`) —— 理由引仓库自己那句「放在 `_truncate` 之前:绕过它等于给一条外部命令开了无上限的出口」(`agent.py:1417-1419`),不自己发明第二个上限。未知扩展名在两条路径上说法不同(有意):模型显式调 `action=check` 必须回答"没有这个类型的检查器";自动路径上不吭声(否则每次编辑 `.md` 都附一句废话),但**检查器缺失**这件事每个进程/工作区第一次遇到时说一次。

#### 落点清单

| 落点 | 文件:行号 | 要动什么 |
|---|---|---|
| **新增模块** | `minicode/diagnostics.py` | 检查器表 + 探测 + argv 构造 + cwd/超时 + 指纹缓存 + 裁剪 |
| **新增目录文件** | `minicode/tools/catalog_diag.py` | `diagnostics` 的 `Tool(...)`。**不能塞进 `catalog_files.py`** —— 它已 **398 行**,而 `tests/test_registry_split.py:149-153` 断言每份目录 `< 400` 行,只剩 1 行余量 |
| 注册表装配 | `minicode/tools/registry.py:36-56` | 加一行 `*catalog_diag.tools()`(现 50 行,`:143` 断言 ≤60,仍过) |
| 顺序与份数钉子 | `tests/test_registry_split.py:28`(ORDER)、`:111`(59→60)、`:146`(CATALOGS 7→8) | 建议挨着 `repo_outline` 插(同属"看代码"一族) |
| handler 形态 | `tests/test_registry_split.py:127-134` | handler 必须来自 `minicode.` 模块,**不能就地 lambda** |
| `Tool` 字段 | `minicode/tools/base.py:82-96` | `read_only=False`;`concurrent_safe` 保持默认 `False` |
| 回灌位置 | `minicode/agent.py:1420-1424` | 见设计 ⑥ |
| 底线保留 | `minicode/agent.py:2059-2093` | **语义不动**,只把两个常量归一处 |
| hook 配方 | `minicode/hook_packs.py:78-107` | 加第三条 `TEMPLATES`(形状照 `:91-106`),`risk` 字段照 `:13` 的纪律写清"会执行工作区代码" |
| 配方文案同步 | `minicode/tools/catalog_files.py:315`、`:328` | 描述里"两条配方"改三条;enum 加新名字 |
| 网页版表态 | `minicode/web_profile.py:53` | 进 `EXCLUDED_TOOLS` 并写理由(会跑服务器上的检查器、摊出服务器工作区路径与类型错) |
| 工具数对账 | `README.md:56`、`system_prompt.md`、随包《使用说明》 | 59 → 60;`scripts/check_docs.py:376-396` 会拿实测核 |
| 冻结针 | `scripts/check_frozen_version.py:31`(TARGETS) | 新模块加一条键(并在 `tests/test_frozen_targets.py` 里不破) |
| 测试 | **新增** `tests/test_diagnostics.py` | 见下 |

#### 验收判据

| 判据 | 怎么测 |
|---|---|
| 语法错真进回灌 | 临时工作区写语法错的 `.py`,`FakeLLM` + `run_turn`,断言工具结果里有行号(照 `tests/test_agent.py:619-634`) |
| **底线不许退化** | `tests/test_agent.py:619-658` 三条必须保持全绿(写坏带回行号 / 写好的不噪声 / 非 `.py` 不检)—— 这是"不取代底线"的机器可读验收 |
| 检查器不存在不报假成功 | monkeypatch 探测返回"没有":断言回灌为空、有"没做检查"、且**不出现**任何"通过/干净/OK"字样 |
| 去重与"没修好" | 连续两次同一批错误:第二次不是全文重复而是"仍是上次那 N 条";第三次压成一行 |
| 超时不等于通过 | 检查器替身超时:断言文案说"没跑完",不含通过结论 |
| 路径边界 | 工作区外/敏感路径:断言**根本没起子进程** |
| schema 形状 | `tests/test_tool_schema_shape.py`:新工具 `parameters` 里不许出现并集 `type`(那次真实 400 事故) |
| 配方三步 | 照 `tests/test_hook_packs.py`:preview 不写任何东西、正文必须出现"会执行工作区代码" |
| 文档对账 | `python scripts/check_docs.py --collect` 必须零问题 |

#### 诚实边界

不做 LSP(要每语言一个常驻服务 + 握手 + 增量同步;"一步一命令"的进程模型与它不同构,留批次 3)、不做自动修复(`--fix` 都是写文件,要单独过门与进撤销账,还要说清"是检查器改的")、**不联网装检查器**(`npx`/`cargo`/`go` 会拉包并改变用户磁盘)、不做"每次编辑都跑全部语言"、不做跨语言全项目体检(那是 CI 的事)。

---

### 1-3 日预算从「提醒」改成「硬闸」+ 每任务预算

#### 现状(并纠正文档漂移)

**会话级硬闸已经存在**,但**不在矩阵文档写的 289-297 行**:

| 事实 | 真实位置 |
|---|---|
| 会话闸取值 | `minicode/agent.py:306`(与一轮总时限 `:301-302` 同一处读) |
| 会话闸判定 | `agent.py:312-318`,在 `for _ in range(self.max_steps)`(`:311`)**循环顶部**,用 `turn_tokens` 比上限 |
| 触顶行为 | `ui.token_limit_reached()` → `_wrap_up(TOKEN_LIMIT_NOTE)`(`:370` 定义) → `return`;口径是「此刻不再开新的工具轮,正在跑的步骤照常收尾」(`:315-316`) |
| 收尾文案 | `agent.py:99-101`(`TOKEN_LIMIT_NOTE`) |
| 日预算字段 | `minicode/config.py:288`(`session_token_limit` 在 `:283`),解析 `:573-574`,落盘 `:696-697` |
| 提醒调用点 | `agent.py:282`(`run_turn` 开头,**每回合一次,不在请求循环里**) |
| 提醒实现 | `agent.py:1746-1775`:`usage.budget_state(limit)` → 只在 `near`/`over` 两档 `push("info")`(`:1773`);档位去重在实例内存(`:1757-1758`) |
| **代码自己写的口径** | `agent.py:1751`:「读不到用量(库损坏)就静默:**预算是提醒,不是闸门**。」 |
| 日账与状态(现成) | `minicode/usage.py:257-264`(`today_total`)、`:272-292`(`budget_state`,三档 + `remaining`)、`:103-146`(`record_usage`) |
| 界面那一格 | `minicode/gui_bridge.py` 的 `get_token_usage` / `get_usage_dashboard`;前端 `frontend/index.html` 的 `ubudget` 条;CLI `main.py:411-422`(`/usage`) |

> **行号纠偏**:矩阵 `coding-agent-capability-matrix.md:349` 与 `docs/ROADMAP_IMPROVEMENTS.md:49` 都写会话闸在 `agent.py:289-297`、日预算在 `1638-1660`。真实位置是 `:312-318` 与 `:1746-1775`;289-297 现在是 `add_user`/回合序号/事件钩子那一段。

另确认一件影响设计的事:`minicode/headless.py` 全文没有这两个预算键 —— 无头模式只能靠 `config.json` 设,没有 CLI 旗标。

#### 设计

**① 判定位置:请求循环顶部,与会话闸同一个出口。** 四条理由:出口只有一个(今天"两步之间收手"的判定全在 `:312-318`,收尾只有 `_wrap_up` 一条路;放工具执行后就成了第二个出口,而 `_dispatch_tool_calls` 的返回值本身已经表示"该收尾");账的时点一致(循环顶读到的是上一次请求后的账;放执行后则账没变但上下文变了,收尾那次会把刚执行完、模型还没读过的结果再发一遍,等于为一个注定要停的步骤多付一份 prompt);**说话必须排在动作之前**(放执行后,用户看到的是"文件已经改了,然后说预算到了");优先级只能在一个 `if` 序列里判。

**② 触顶行为**:沿用 `_wrap_up`,新增文案常量(照 `TOKEN_LIMIT_NOTE` 形状):说明已到今天总额度、已花多少、**还剩什么没做**,并给两条下一步(等明天 / 点「今天继续」)。界面走 `push("info")` 而**不是** `error` —— 护栏起效不是故障(该口径已写在 `gui_ui.py:180-181`、`:187-188`)。机器可读那份:`limits` 里落 `{"kind": "daily_budget"}`,**退出码沿用 `EXIT_LIMITED = 4`**(`headless.py:35` 注释本来就写着"被上限收尾(步数/时限/**预算**/连挂)"),退出码清单一个字都不用改。

**③ 同时触顶报哪一条:正文报会话那条,但两条原因都留痕。** 会话闸数的是本实例的账(用户当场改一个数就能继续),日预算数的是这台机器今天全供应商的账 —— **报更窄的那一条,用户才知道该动哪个旋钮**;同时 `limits` 里两条都在,并在正文补一句"今天也已经超了"。

**④ 放行口:给一次显式 override,一次一放、只到本次进程、只有用户能按。** 不给就没有退路(用户被别的工作区用掉额度后只能去改 config 的数字 —— 那比给一个明确的「今天继续」更糟:改数字是把闸门本身拆了)。状态挂进程内存(同 `_budget_noted` 的"不落盘"口径),重启即恢复闸门。入口只给用户(GUI 用量弹窗一颗按钮 + CLI `/budget continue`);**模型不许通过工具调用拿到放行** —— 能自己拆闸的闸等于没有。放行时 `budget_state` **不改 `level`**(仍是 `over`),只加 `overridden: True`,界面与拦停提示都写「日预算已放行(本次运行)」:把 `over` 改写成 `ok` 就是"有超支说成没超支"。

**⑤ `task_token_budget` 挂在一次 `run_turn` 上**(不是会话):会话粒度已被 `session_token_limit` 占了,同粒度两个数迟早对不上;界面上最小可指认的"任务"就是用户发的那句话。实现必须**新增本轮增量字段**(每轮开头归零)—— 现有 `turn_tokens` 是 agent 生命周期累计(`agent.py:655-665`),名字与语义都不能复用。代价要写进文案:跨多轮的长任务由用户说"继续"开启新一轮,那时预算重新算一格。

#### 落点清单

| 落点 | 文件:行号 | 要动什么 |
|---|---|---|
| 闸门判定 | `minicode/agent.py:311-325` | 在会话闸同一 `if` 序列加日预算与任务预算两处;同日触顶只收尾一次 |
| 收尾常量 | `minicode/agent.py:99-101` | 加两条文案 |
| 本轮增量 | `agent.py:307` 一带、`:655-674` | 加 `self._run_tokens` 累计与每轮归零 |
| 提醒改口径 | `agent.py:1746-1775` | 保留 80% 提醒;**必须改掉 `:1751` 那句"预算是提醒,不是闸门"** |
| 配置字段 | `minicode/config.py:283-288` | 新增 `task_token_budget: int = 0`;`daily_token_budget` 注释改成"硬上限 + 一次放行" |
| 解析与落盘 | `config.py:573-574`、`:696-697` | 两处都登记 |
| 日账状态 | `minicode/usage.py:272-292` | `budget_state` 加 `overridden`;新增放行/查询/清除三个**进程内**函数(不落盘) |
| 日聚合 | `usage.py:257-264`、`:103-146` | **不新增查询**(已够用) |
| 无头退出码 | `minicode/headless.py` 的 limits 落点与 `exit_code_for` | 加两个回调落 `{"kind": ...}`;退出码沿用 `EXIT_LIMITED` |
| 两套界面 | `minicode/ui.py`、`minicode/gui_ui.py` 的 token_limit 回调旁 | 各加两个同形回调(走 `info`) |
| GUI 载荷 / CLI | `minicode/gui_bridge.py` 用量两处、`main.py:411-422`、`frontend/index.html` 的 `ubudget` 那一格 | 带 `overridden`;补"已放行"与任务预算上限 |
| 配置示例与文档 | `config.example.json`、`README.md` 配置一节 | 新键、默认值、口径 |
| 冻结针 | `scripts/check_frozen_version.py:31` | 新公开名进 `minicode.usage` / `minicode.agent` 那两条键 |
| state / mydata | `minicode/mydata.py:179-180` | **不动**:放行位不落盘,`token-usage.db` 早已登记为统计 |

#### 验收判据

| 判据 | 怎么测 |
|---|---|
| 触顶后不再发请求且收尾带新文案 | 假 LLM 第一回合就回 `tool_calls`,把日预算调到极小并往假库写超支数;断言最后一次请求工具数组为空、正文含新常量、没有第二次带工具的请求(照 `tests/test_agent.py:745-769`) |
| 检查在**循环顶部** | 断言被拦那一步的 `tool_call_id` 在会话里,而下一步的**不在**(`tests/test_agent.py:768-769` 就是这么钉的) |
| 退出码 | 参数化表加一行 `daily_budget_reached` → `EXIT_LIMITED`,且 `limits` 的 `kind == "daily_budget"`(照 `tests/test_headless.py:158-169`) |
| 优先级 | 两个闸同时超:正文只有会话那条,`limits` 里两条原因都在 |
| 默认 0 = 不限 | 照 `tests/test_agent.py:772-783` |
| 任务预算跨轮归零 | 与 `tests/test_agent.py:786-801`(会话闸**跨轮累计**)正好相反:同一 agent 连续两轮,第二轮重新计数 |
| 放行位不落盘 | 放行后仍报 `over` 且 `overridden=True`、闸不拦;清除后恢复;模块状态清空后 `overridden` 为假 |
| 界面载荷 | 在 `tests/test_usage_budget.py` 现有的 `get_token_usage` / `get_usage_dashboard` 两条上补 `today.budget.overridden` 断言 |

测试落点:`tests/test_usage_budget.py`(日预算状态/去重/载荷)、`tests/test_agent.py` 的「成本闸门」段(`:734-801`)、`tests/test_headless.py`。

#### 诚实边界

不做「按天自动降级模型」(静默换便宜模型会让"这次用的哪个模型"在留档、`turn_usage` 事件与面板上出现对不上的事实)、不做跨机器同步配额(`token-usage.db` 是本机统计),不做"压低质量换 token"的自动策略(压缩阈值/步数/思考档位都不因预算自动调),放行位不落盘(重启要重新点一次 —— 有意的不便)。

---

### 1-4 无头模式加 `--output-format stream-json`

#### 先回答 HANDOFF 那个悬着的问题:读者是谁

`HANDOFF.md:774` 已把这格登记为"仍没做",并写死了前置条件:「真要做就先定读者是谁(CI 日志?还是给别的 agent 当子进程驱动?),不是补个 emitter 就完事」。

**本方案拍的答案:读者是"把学长当子进程驱动的编排层"**(CI 脚本、外层 agent、批处理壳),不是"盯着 CI 日志看的人"。三条推论决定后面的设计:① 需要的是**过程可判**,所以事件必须结构化且**名字稳定**;② 人话仍然属于 stderr(`HeadlessUI._note` 的改道是既有事实,见 `headless.py:185-188`);③ **结局仍以最后一份结果对象 + 退出码为准**,事件流只补"过程",不替代结局 —— 因此本轮**不新增退出码**、不改 `exit_code_for`。

#### 现状

| 事实 | 位置 |
|---|---|
| 合法取值只有两个 | `headless.py:28`:`OUTPUT_FORMATS = ("text", "json")` |
| 认三种写法 | `headless.py:99-131` |
| 认不出的值当场报错,文案里直接写着"stream-json 没做" | `headless.py:123-126`(那句理由要一起删) |
| 报错类型与退出码 | `headless.py:69-70` → `main.py:820-822`(`EXIT_ARGS = 2`) |
| 图形入口靠它拒旗标 | `headless.py:73`(`headless_flags_in`,按前缀认) |
| 七个退出码 | `headless.py:31-37`(0 做成 / 1 模型网络 / 2 参数 / 3 没人点头 / 4 被上限 / 5 中断或管道关闭 / 6 schema 不合);优先级 `:285-295` |
| HeadlessUI **一个字都不往 stdout 写** | `headless.py:185-188`(`_note` → stderr);正文只累在内存(`:170-171`);stdout 上唯一的东西是最后那份 JSON(`main.py:864-878`) |
| 今天不是"纯文本",是"纯 JSON 单文档" | 同上;**`on_text_delta` 的结构化信息在无头形态下完全没被消费** |

**一处必须先拍死的顺序真相**:`headless.py:103` 的 docstring 写"两个都给时 `--output-format` 胜出",但实现是**最后出现的那个胜出**(两处都只是覆盖)。现有测试只钉了一种顺序(`tests/test_headless.py:34-37`)。加新值前必须先把这条改清楚。

#### 设计

**① 事件行形状:沿用前端与 `GuiUI.push` 已有的 `{"type": ..., ...}`**,一行一条、`seq` 递增、`type` 必在。

| `type` | 载荷 |
|---|---|
| `stream_start` | `task`、`format`、`model` |
| `text_delta` / `reasoning_delta` | `text` |
| `finish_stream` | —— |
| `tool_start` | `name`、`summary`、`trust` |
| `tool_done` | `name`、`ok`、`error_preview` |
| `hard_reject` | `name` |
| `compacted` | `before`、`after` |
| `retry` | `attempt`、`error`(类型 + 截断到 160 字) |
| `result` | 与 `--json` 那份对象**同构**,外加 `type` |

**`retry` 是"复用前端名"规则下的一处刻意例外**:GUI 前端根本收不到重试事件 —— `GuiUI.on_retry` 把它折成了一句中文 `push("info")`,消费方要读就得解析中文句子,那正是这轮要省掉的事。所以新起 `retry` 并在文档里点名。《沿用 `json.dumps(..., ensure_ascii=False)`,单行 —— 多行就不是 NDJSON 了。`

**② 三条必须拍死的口径**:

1. **`--json` 与 `stream-json` 互斥,报 `EXIT_ARGS`**。`headless.py:4-8` 写下的是契约:调用方拿的是 `json.loads(stdout)`;NDJSON 让这个 `json.loads` 必然失败,而它长成"任务没做成"的样子(同一句话 `:8` 自己说过)。两条都给时靠位置决定形状会让"CI 里换个参数顺序就静默换了形状"。
2. **`--output-schema` 与 `stream-json` 可以同时用,但不再强制改成 json**:改成"**仅当 `fmt == "text"` 时**强制 json"。理由:schema 只做两件事(照它问模型、验回来),与"过程怎么上线"正交。`structured_output`/`schema_violations` 只出现在 `result` 行里,`EXIT_SCHEMA_VIOLATION` 仍排最后。
3. **stdout 干净是硬约束,且要写成测试**。现状已满足一半(HeadlessUI 自己实现的那批回调没有一处 print 到 stdout);新增部分守两条:事件只写 stdout 且**每行 flush**;人读的话(`info`/`error`/`banner`/`tool_progress`)继续只走 stderr。`tool_progress` 特意不映射 —— 它是 15 秒一条的节流行,进 stdout 只给解析器添噪,而"这一步在跑"已由 `tool_start` 表达。**真正的风险点是透传不是新增代码**:`HeadlessUI.__getattr__`(`:258-260`)会把没想到的方法转给内层 `TerminalUI`,而那里有直接往真 stdout 渲染 rich 面板的方法 —— 今天到不了那里(一键任务拒斜杠命令),但这是"今天安全、明天有人加一个会 print 的方法就污染契约"的形状,所以验收里放一条"整段 stdout 逐行可 `json.loads`"的断言,而不是试图在 emitter 里拦。

**③ 写失败的语义**:stdout 被下游关掉(`BrokenPipeError`)时与 `EXIT_INTERRUPTED = 5` 对齐(`:36` 注释本来就写着"Ctrl+C **或管道关闭**"):捕获后置 `ui.interrupted = True` 并停止发射,让 `exit_code_for` 自己得出 5,不新开判断。

#### 落点清单

| 位置 | 改什么 |
|---|---|
| `minicode/headless.py:28` | `OUTPUT_FORMATS` 加 `"stream-json"` |
| `headless.py:123-126` | 删掉"stream-json 没做…"那半句 |
| `headless.py:99-131` | 值域 + 互斥;顺带把 `:103` 的 docstring 与 last-wins 实现对账 |
| `headless.py:73` | 代码不用改(已按前缀认),只同步 docstring |
| `headless.py:134-260` | 新增事件出口(`EventStream`:带锁、带 `seq`、写 stdout 并 flush),在现有回调里顺带发一行;补一个 `push`(对齐 `gui_ui.py:50-58`) |
| `main.py:837-840` | `ensure_utf8_stdout()` 条件从 `== "json"` 扩到两种格式 |
| `main.py:846-849` | `hui` 构造条件同步扩 |
| `main.py:864-878` | `fmt` 参数化;退出前保证 `result` 行已 flush |
| `main.py:823-826` | schema 与格式的关系改成"仅 text 时强制 json" |
| `main.py:7-8`、`:905`、`:909` | 用法注释与两句报错里的 `--json` 改成两种格式都说得通的说法 |
| `README.md:151-182` | 加 `stream-json` 说明与"stdout 只有 NDJSON、人话在 stderr" |
| `scripts/build_exe.py` 的《使用说明》对应两条 | 同上(`check_docs.py` 抠的就是这段字面量) |
| `scripts/check_frozen_version.py:288` | `minicode.headless` 那条键登记新公开名 |
| `tests/test_headless.py:46-50` | 那条"`stream-json` 必须是错的"改写成"拼错的值仍要拒"(换 `jsonl`),否则新功能与旧断言对撞 |
| `HANDOFF.md:774` | 把"仍没做的是 `stream-json`"改写成已做 + 本轮拍定的读者 |

#### 验收判据

| # | 判据 | 怎么测 |
|---|---|---|
| 1 | stdout 每行都能 `json.loads` | `capsys.readouterr().out.splitlines()` 逐行解 |
| 2 | 第一行 `stream_start`、最后一行 `result`,`seq` 严格递增无洞 | 直调 `cli.run_json(..., fmt="stream-json")` |
| 3 | `text_delta` 按序拼接 == `result.response` | 假 LLM 分两段吐字 |
| 4 | `tool_start`/`tool_done` 与 `result.tool_calls` 逐条对得上(含 `ok`) | 复用 `tests/test_headless.py` 现有的成败判定口径(只认 `toolsum`) |
| 5 | 确认被拒时 stdout **没有**确认类事件,只有 `asks_refused` + 退出码 3 | 同现有形状 |
| 6 | 两个旗标同时给 → `UsageError`;坏值仍 `UsageError` | 纯函数直测 `pop_output_flags` |
| 7 | `--output-schema` + `stream-json`:格式仍是流、`structured_output` 有值、不合时退 6 | 复用 `tests/test_output_schema.py` 形状 |
| 8 | 人话一律在 stderr | `cap.out` 不含中文提示句、`cap.err` 里有 |
| 9 | 写 stdout 抛 `BrokenPipeError` 时不死、退出码 5 | monkeypatch 成会抛的假流 |
| 10(可选) | 真起 `main.py --output-format stream-json "…"` 端到端 | 先例:`tests/test_py_script.py` 起真 `main.py`;不进默认快车道 |

测试落点:主力 `tests/test_headless.py`(已有参数/静默/结局三族),事件族超过 ~120 行则另起 `tests/test_stream_json.py`(先例 `tests/test_output_schema.py`)。

#### 诚实边界

**不承诺事件行序以外的语义**(只保证"行的先后 = 回调发生的先后",不保证与留档消息一一对应,也不保证 `tool_start`/`tool_done` 一定成对 —— 中断、撞上限、硬拒都会让 `tool_done` 不出现);**不做 backpressure**(消费方读得慢就阻塞在写上;不选"写不进去就丢事件",那与"不许假装"的口径冲突);不把 stderr 结构化;不做流式 schema 校验(仍整轮结束一次验);不改 `text|json` 的既有形状;不做多轮/REPL;不外发工具参数正文(继续只记长度);不加时间戳(要加就得先定义基准时钟)。

---

### 1-5 `bash_input` —— 给后台命令写 stdin

#### 现状

| 事实 | 位置 |
|---|---|
| **前台** bash 的 stdin 是 `DEVNULL` | `minicode/tools/shell.py:142`;注释原文:「stdin 给 DEVNULL:以前它继承控制台,GUI 那边等于"永远没人输入",命令会干等;**交互式命令仍然不该走这里(那是 PTY 那条没做的路)**,但现在不会吊住。」 |
| **后台**也是 `DEVNULL` | `shell.py:426-430`(`:430`) |
| `py_script` 同 | `shell.py:809`(本轮不动它) |
| 工具描述已把这条写成契约 | `minicode/tools/catalog_files.py:106-108`:「**命令不会等你输入**:它的标准输入是空的……需要喂输入就把内容写成工作区文件,再用重定向交给命令。」 |

**`_BgJob` 里到底有没有保存 `proc.stdin`**:它保存了 `proc`(`shell.py:320`),但**没有** stdin 字段(`__init__` 字段全集在 `:317-338`)。这一点直接决定成本 —— 因为 `proc` 已经留着,**要拿到写入口只需要把 `:430` 的 `stdin=subprocess.DEVNULL` 改成 `subprocess.PIPE`**(今天 `proc.stdin is None`,DEVNULL 不建管道)。**真正的开关是那一个参数,不是一个字段。**

后台任务今天只能:起(`:416-447`,上限 12 个在跑)、读输出(`:341-368`,`BG_BUFFER_CHARS` 留尾部)、`bash_status`(只读、不弹框)、`bash_kill`(连整棵树收:`:497-543`)、跑完补验收账/弹通知/给模型一行现状(`:616-670`)、`atexit` 收口(`:559-573`)。**"顺序取决于上一步输出"的交互式流程今天无解** —— 预写文件 + 重定向对这类流程无效。

#### 设计

签名:`bash_input(id, text, newline=True, wait_ms=1500, reason="")`

| 项 | 语义 | 理由 |
|---|---|---|
| `id` | 只认**后台** job 编号,解析复用 `_bg_job_or_error`(`shell.py:546`) | 那个函数里有一处规矩(`j3`/`3` 两种写法都认),第二份 id 解析迟早与它分家 |
| `text` | 上限 **4000 字**,超了**拒绝不截断** | 截断等于把半句话喂进去,而"喂进去半句"是最难查的坏法 |
| `newline=True` | 自动补 `\n` | 一行输入是最常见形态 |
| `newline=False` | 行尾交给调用方 | 单键菜单、`(y/n)`、raw 模式需要;不另开 `crlf=` 开关(两套行尾同时在路上的话,"为什么这条命令没接住"没法归因) |
| 返回 | `job.line()` 现状行 + 写结果 + `job.tail(n)` | 与 `bash_status` 同一份事实,不拼第二份真相 |
| `wait_ms` | 默认 1500、上限 5000 | **只是让尾巴更可能带上回应,不是保证**:输出要经 `_pump` 按行读到才会出现在 `tail()` 里 |

**中断与 EOF 都不做**:往管道写 `0x03` **不会**产生 Ctrl+C(那是控制台事件,不是 stdin 字符)—— 要停就是 `bash_kill`;关 stdin 之后这条 job 再也写不进东西,而它可能还要跑很久,真需要 EOF 的命令今天就该走重定向文件。

**安全口径**:

| 决定 | 内容 |
|---|---|
| 走确认门 | `Tool` **不标** `read_only`(与 `bash_kill` 同档) |
| 确认框要说清 | `explain.risk_notes` 加一条、固定话术进 `sandbox.KNOWN_LIMITS`(与 `background` 完全同款手法),话术必须写明:**写进去的内容不经过命令文本那道判定**(`bash` 的 `check` 看的是 `args["command"]`,而 `bash_input` 的 `text` 是另一个键) |
| 只对后台生效 | 前台保持 `DEVNULL`:前台是同步等结果的调用,根本没有"写进去"的时机 |
| 已结束的 job | 明确报错(复用 `bash_status` 那句"已经结束"的口径) |
| 不做批量灌 | 一次一行;`tail` 是按字节切尾的,没有行归属信息 |
| 无头模式 | 与所有动手面一样被拒(退出码 3);要喂输入仍走重定向文件 |

**Windows 上管道 stdin 的编码与阻塞**:

- 写成 **UTF-8 字节**(与读侧 `_decode` 先试 utf-8 一致,与子进程 `PYTHONIOENCODING=utf-8` 一致);**不做 GBK 开关**(两个编码同时在路上,"谁把中文读错了"没法归因)。
- **每次写完必须 `flush()`**(默认 `bufsize` 是带缓冲的);行尾默认只写 `\n` 一个字节(子进程侧 CRT 文本模式读的时候会把 CRLF 折成 LF)。
- **最容易写坏的一处:写绝不能不持有 `job.lock`**。那个锁被读侧与播报用着(`_append`/`tail`/`_bg_notify_done`);管道写满(对方不读)会阻塞在 `write` 上,若此时持有 `job.lock` → 读线程拿不到锁 → 子进程写 stdout 也可能堵 → **真死锁**。方案:独立 `stdin_lock` + 短命写线程 + `join(超时 2 秒)`;超时后如实报"这条命令此刻没在读输入"并**保持它运行**(不杀、不重试)。

#### 落点清单

| 位置 | 改什么 |
|---|---|
| `minicode/tools/shell.py:430` | `DEVNULL` → `subprocess.PIPE`(唯一真正的开关) |
| `shell.py:142`(前台)、`:809`(`py_script`) | **不动**(理由要写在这一轮说明里,免得下一轮有人以为"顺手也开了") |
| `shell.py:317-338`(`_BgJob.__init__`) | 新增 `self.stdin_lock = threading.Lock()` |
| `shell.py:358-368`(`_pump` 收尾) | `proc.wait()` 之后补 close stdin(整段兜异常)—— 同 `:362-363` 的句柄理由 |
| `shell.py:599-607` 之后 | 新 handler `bash_input(...)`,与 `bash_status`/`bash_kill` 排在一起 |
| `shell.py:289-298` 一带 | 新常量 `BG_INPUT_MAX_CHARS`、`BG_INPUT_WAIT_MS`(与 `BG_TAIL_DEFAULT` `:292` 同处) |
| `shell.py:546` | **复用** `_bg_job_or_error`,不另写 id 解析 |
| `minicode/tools/catalog_files.py` | 在 `bash_kill` 的 `Tool` 之后、`py_script` 之前插一只 `Tool(name="bash_input", ...)`,**不标** `read_only`。**顺序不能乱改**(`registry.py` docstring 写着顺序被逐字钉住;"真要重排请单独一轮") |
| `catalog_files.py:106-108` | `bash` 描述改口:前台仍然等不到输入,**后台**可用 `bash_input` 喂 |
| `minicode/tools/concurrency.py` | 名单**不加** `bash_input`(新工具默认不进池,默认站在安全这一边) |
| `minicode/explain.py` 的 `risk_notes` | 加一条,与 `bash_kill` 并列 |
| `minicode/sandbox.py` 的 `KNOWN_LIMITS` | 新增 `"bash_input"` 固定话术,由 `explain` 引用 |
| `minicode/web_profile.py:53` | `EXCLUDED_TOOLS` 加一条(访客能对主人的进程说"y") |
| `tests/test_registry_split.py:28`、`:111` | `ORDER` 插 `"bash_input"`;59 → 60(1-2 之后 → 61) |
| `scripts/check_frozen_version.py:505` | `minicode.tools.shell` 那条键加 `bash_input` 与新常量 |
| `README.md:56`、`:65`、`system_prompt.md`、《使用说明》、HANDOFF §1/§2 | 计数、清单、后台那一条的四条边界 |

#### 验收判据

测试文件**必须放 `tests/test_shell.py`**:后台那一族已经在这里,而且该文件有一条 autouse fixture 把真桌面通知替换成记账 —— 在别处写用例会**真弹窗**(`tests/conftest.py` 的 `ctx` fixture 也在那里)。

| # | 判据 | 怎么测 |
|---|---|---|
| 1 | 写一行能被子进程读到并回显 | 临时工作区落一个读 stdin 回显的脚本 → 后台起 → `bash_input` → **轮询** `bash_status` 直到 tail 出现回显(不要 `sleep` 定值) |
| 2 | `newline=False` 不补行尾 | 子进程按**字节**读并回显 repr;断到的是 `b'y'` 而不是 `b'y\n'` |
| 3 | 中文往返 | 写 `"继续"` → 回显是 `继续`(同时钉住 UTF-8 口径) |
| 4 | 不读 stdin 的命令不吊死 | 后台起一条长睡命令,写一次 → 在 `wait_ms` 内回话、任务仍在跑、**没被杀** |
| 5 | 已结束/未知 id 给人话不是异常 | 复用现有两条的形状 |
| 6 | 超长 `text` **拒绝**(不是截断) | 传上限+1,断言回话里有"拒绝"且 tail 里没有半句 |
| 7 | 权限分档没弄错 | 加断言:`bash_input` 的 `read_only` 为假、参数集合恰为 `{id, text, newline, wait_ms, reason}` |
| 8(e2e) | 真机器上仍是那一套 | 在 `scripts/e2e_bg_jobs.py` 加第 4 个 case |

**Windows 上最小人工验证**:① 在工作区写一个 `for line in sys.stdin: print("echo:"+line.strip(), flush=True)` 的脚本;② 让学长 `python -u echo_stdin.py`(后台);③ `bash_input(id="jN", text="hello")`;④ `bash_status(id="jN")` 尾部应出现 `echo:hello`。**坚持用脚本文件而不是 `python -c "…"`**:`resolve_shell_argv`(`shell.py:66-76`)在 auto 下可能给 Git Bash、也可能落回 cmd,同一个带引号的 `-c` 字符串在两条路上不通用,那会把"验证失败"变成"引号写错了"。

#### 诚实边界

| 不做 | 为什么 |
|---|---|
| **pty / ConPTY** | 今天的子进程接的是管道不是终端:程序看不到 tty,不会打印提示、不做行编辑、没有自动 flush |
| **交互式全屏程序(vim/top/less)** | 要 ioctl 拿窗口尺寸、要终端驱动按键、要能收 Ctrl+C;管道下要么报错,要么把转义序列糊进按行读法 |
| **`\x03` 中断** | 管道里的 `0x03` 不产生 Ctrl+C;要停用 `bash_kill` |
| **EOF / 关闭 stdin 的动作** | 关了这条 job 就再也写不进去,而它可能还要跑很久 |
| **自动应答 / 提示识别 / 自动补全** | 去 grep 输出里的 `(y/n)` 然后自动补一个 `y`,等于"程序按某段文本替用户点头" —— 与「没答不等于同意」直接冲突 |
| **对写进去的内容做沙箱判定** | 命令文本那道判定看的是 `args["command"]`,`bash_input` 没有这个键,**不会**被它拦 —— 所以这条边界必须写进确认框话术,而不是假装它被扫过 |
| **保证"它读到了"** | `wait_ms` 只是等一等;唯一证据是 `bash_status` 里真读到的字节。回话里不许写"已输入成功,它应该会继续"这类话 |
| **跨进程/跨会话的 stdin 转发、前端按钮** | 前者要新协议;后者要桥层加方法,而"桥层不加没人调用的方法"是已写下的口径 |



## 四、执行顺序与依赖

```
第一轮 1-3 日预算硬闸      ── 最小、独立(agent/config/usage,不碰工具面)
       1-4 无头 stream-json ── 只动 headless.py
       1-1 MCP server 模式  ── 新模块 + 新入口(不碰工具面)
              │
第二轮 1-2 diagnostics  ┐
       1-5 bash_input   ┘  同一轮:共享 registry 顺序 + 两张数字表,省一次全量回归
```

- 每件独立提交、独立跑全量、独立点版本号;**第二轮内部也建议分两次提交**(先 1-2 再 1-5),只是共用同一次"工具面税"的改动,合起来跑一轮全量。
- 第一轮三件互不依赖,顺序可换;**1-3 建议先走** —— 它最小、且能把"闸门触顶怎么收尾"这条路径先在真机上验一遍,后面 1-1 的拒绝路径可以照它的形状写。
- 1-1 建议放第一轮末尾:它要复用 `headless` 那套"没有人点头就拒绝"的口径(见其节),等 1-4 把无头那侧理顺后再动,读码成本更低。

---

## 五、明确不做的事(守住边界)

这几条不是漏做,是**撞项目自己写下的边界**,重提会引出错误的补齐动作:

1. **PTY / 全屏交互程序** —— `minicode/tools/shell.py:138` 的原文是「交互式命令仍然不该走这里(那是 PTY 那条没做的路)」。1-5 只补**后台任务的管道 stdin**,不改这句话的适用范围,也不支持 `vim`/`top` 这类要吃终端尺寸的程序。
2. **MCP server 暴露写类/命令类工具** —— 外部客户端没有工作区授权,把 `bash`/`write_file`/`apply_patch` 暴露出去等于绕过整套权限门。1-1 只白名单只读工具。
3. **1-1 的远程鉴权与 HTTP/SSE** —— 先只做 stdio 单客户端;不做令牌、不做多客户端并发、不监听端口。
4. **1-2 联网装检查器 / 自动修复 / LSP** —— 检查器不存在就跳过并说明(不假装跑过);诊断只报告不回写代码;真正的 LSP 客户端留到批次 3。
5. **1-3 静默截断 / 按天自动降级模型 / 跨机配额同步** —— 触顶必须**说出口**再收尾;不做"偷偷换个便宜模型接着跑"(那会把"谁花了多少"变成两处真相)。
6. **1-4 backpressure / 断线重连语义** —— 事件流只保证"事件顺序 + stdout 干净";写不动就按进程结束处理。
7. **任何一件都不许"假装做了"** —— 不可用即降级并如实说(见 §〇 最后那条口径)。

---

## 六、总体验收清单

每件收口时必须同时满足:

- [ ] 快车道 + 全量 + `ruff` + `check_docs --collect` + `check_frontend_js` + `gen_changelog` 全绿
- [ ] 新增行为有测试钉住;**关掉开关后与今天逐字一致**(有对照测试)
- [ ] 新增工具同步了 §2.2 那五处(装配顺序 / `ORDER` + 字面量 / 三份活文档 / `web_profile` 表态 / 冻结针);`web_profile` 那一条要**写理由**,不是漏写
- [ ] 新落盘文件过了四处登记税(§2.3);能复用现有账本的没有新开
- [ ] `HANDOFF` §2/§5/§6/§7、README、`system_prompt.md`、《使用说明》同步
- [ ] 提交标题点了版本号
- [ ] 若动界面,有一条真浏览器 e2e(只改源码文本的断言钉不住"点开真的看得见")
- [ ] 每条支路的**失败结局**都拿真进程跑过一遍(参考 §七 那条 v0.9.137 的教训)

---

## 七、风险登记表

| 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|
| 日预算闸门误伤"正在跑的最后一步" | 判定放在工具执行后 | 半截任务被砍、工具调用与结果不成对 | 判定放**请求循环顶部**(与 `session_token_limit` 同位),只管"不许再开新一轮" |
| 闸门与提醒重复报 | 两处各写一套阈值 | 同一天弹两条、口径不一的数字 | 阈值只留 `usage.budget_state()`(`minicode/usage.py:272`)一份,提醒与闸门读同一个 `level` |
| `stream-json` 污染 stdout | 进度/横幅仍走 stdout | 调用方 `json.loads` 崩,且崩的样子与"任务没做成"一样 | 沿用 `headless.py:5-8` 那条契约:人类可读的一律 stderr;用解析器测试钉住 |
| MCP server 被外部客户端当"免确认后门" | 收到 `tools/call` 直接执行 | 本机文件/命令被外部进程驱动 | 白名单只读工具 + **无人点头一律拒绝**(与 `web_ui.py:52`、`headless.py:9-15` 同一口径)+ 逐条留痕到 `injection-events.log` |
| 诊断回灌变成刷屏 | 每次编辑都回灌同一条错误 | 上下文被同一句话吃掉、模型反复修同一处 | 错误指纹去重(同文件同错误只报一次,内容变了才重置)+ 输出裁剪 |
| 诊断与现有语法自检**两套真相** | hook 配方又实现一遍语法检查 | 同一错误两个说法,谁先谁后说不清 | 内置 `ast` 自检保持"不可关的底线"不动,配方只做重活(类型/lint/测试)且**调用同一个模块** |
| `bash_input` 写坏正在跑的进程 | 往非交互命令乱写 stdin | 命令行为不可预期、甚至挂住 | 只对**后台任务**开放(前台命令的 `DEVNULL` 不动)+ 明确编码 + 默认自动补换行并在回话里写明写了什么 |
| 工具数从 59 漂到 61 没同步文档 | 只改了代码 | `check_docs` 红,或更坏:文档说 59、实际 61 | §2.2 那五处一次改完;合并 1-2/1-5 就是为了只动一次 |
| 文档与代码漂移(行号) | 并行会话改了同一批文件 | 后面的人照旧行号读错结论 | 每条「现状」都带行号 + `git log -1` 提示;§八 给可复核命令 |

> **一条来自 v0.9.137 的教训**(写在 `docs/plan-P0-安全与正确性收口.md` 顶部):源码层的测试看不见经过真流的那一坨 traceback —— 那一轮 `--json` 在模型故障时**整份 JSON 消失**,而 3500 项测试全绿。所以本轮 1-1 与 1-4 都必须拿**真进程**把失败结局跑一遍(拒绝一次写类调用、把 stdout 接到解析器上跑一次),不能只靠单测。

---

## 八、附录:现状证据(可复核)

以下命令在仓库根目录运行,输出就是本计划书各节「现状」那一栏的依据:

```bash
git log -1                                                  # 行号基线:7ec5e37
python -c "from minicode.tools.registry import build_default_registry as b; r=b(); print(len(r), sum(1 for t in r if t.read_only))"
                                                            # 期望:59 27

# 1-3 日预算:会话有闸、日预算只是提醒
rg -n "session_token_limit|daily_token_budget" minicode/agent.py minicode/config.py
rg -n "预算是提醒" minicode/agent.py                        # 期望:命中 agent.py:1751 那句原话
rg -n "def budget_state" minicode/usage.py                  # 期望:usage.py:272

# 1-4 无头输出与退出码
rg -n "OUTPUT_FORMATS|^EXIT_" minicode/headless.py           # 期望:text/json + 七个码

# 1-1 MCP:客户端有,服务端没有
rg -n "只实现了 tools|resources / prompts / sampling / logging" minicode/mcp.py
rg -n "mcp_resource" minicode/tools/catalog_web.py           # 期望:resources 已有工具入口(注释与代码不一致的那处)
rg -n "protocolVersion|tools/list|tools/call" tests/fake_mcp_server.py
                                                            # 期望:现成的 stdio JSON-RPC 假服务器,可当协议参照

# 1-2 诊断:只有 Python 语法自检
rg -n "_py_syntax_feedback|SYNTAX_CHECK_TOOLS|SYNTAX_SCAN_LIMIT" minicode/agent.py
rg -n "lsp|LanguageServer|pyright|eslint" minicode        # 期望:零命中(这些检查器一个都没接)
rg -n "ruff" minicode                                     # 期望:只有 3 处无关命中 ——
                                                          #   llm.py:172 注释 / permspec.py:67 命令名 / fs.py:287 跳过目录。
                                                          #   **别把这条写成"零命中"**:那样下一个人会以为检查器接过了

# 1-5 stdin:现值是 DEVNULL
rg -n "stdin=" minicode/tools/shell.py                       # 期望:前台/后台都是 DEVNULL
rg -n "PTY 那条没做的路" minicode/tools/shell.py              # 期望:命中 shell.py:138

# 工具面税(§2.2)
rg -n "ORDER|== 59" tests/test_registry_split.py
rg -n "def check_agent_doc_sync|tool_count_problems" scripts/check_docs.py
rg -n "EXCLUDED_TOOLS" minicode/web_profile.py
```

> 引用行号前先 `git log -1`。这份工作树有多个会话并行改,行号会漂 —— 上面每一个 `期望` 都是 2026-10-09 在 `7ec5e37` 上实测过的。
