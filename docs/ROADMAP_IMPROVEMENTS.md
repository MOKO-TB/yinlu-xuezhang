# 引路学长 能力路线图文档

> 生成时间：2026-10-07
> 对比基准：Codex / Claude Code / Cursor / Cline / Roo Code / Aider / ZCode / Qoder
> 版本：v0.9.132
> **修订（2026-10-08）**：这份文档按 v0.9.120 的工作树写，那之后并行会话又落了六轮，
> 下面逐条按**当前代码**复核过并就地改口（改的每一条都给了 `文件:行号`，方法同矩阵 §10D）。
> 保留原始判断的痕迹是为了说明代价：**「grep 关键词零命中」不等于「这个能力没有」**——
> 本文原 P0 第 3 条就是这么误判了 `plan_lock`（实现换了名字），白登记一轮。

## 一、定位

引路学长是**国内首个将桌面 GUI + CLI 同核、子代理、MCP、钩子、计划依赖、无头模式完整拉通的本地 Agent**。它不是任何竞品的小弟，而是独立堆出来的完整体。

## 二、相对优势（>= 竞品平均水位）

| 能力 | 状态 | 竞品对照 |
|---|---|---|
| 完整桌面 GUI（玻璃拟态工作台） | ✅ | 仅 Qoder/ZCode 有 |
| 个人数据闭环（课表+成绩+校历+提醒+空闲时段看板+导出日历）| ✅ | 独门 |
| 执行前问一句安全边界 | ✅ | Cursor「steer running agent」同形 |
| 计划依赖图 + shell 验收闸 | ✅ | ZCode/Qoder 有 plan，验收真跑独有 |
| 工具钩子：前置改写 + 重跑门 | ✅ | Claude Code / Qoder 有 |
| 无头模式 6 种语义退出码 | ✅ | Codex 仅 0/1，ZCode 未记录 |
| 会话级 token 日预算 + 触顶收尾 | ✅ | Cursor Cloud / ZCode goal 有 |
| microcompact 只读结果占位 | ✅ | ZCode / Claude / Codex 三家有 |

## 三、核心差距

> ⚠ **这一节今天不能照着读（2026-10-15 逐条重核）**：15 条里**只剩三条还是差距**（#1 的 S5b/S5c、#8 压缩前可回滚、#13 云/远程），其余**要么已落地、要么被推翻、要么是本项目的边界不是债**。逐条划掉的字**留着**，因为它记着"当初为什么觉得这是差距"——那段理由才是这一节的用处。对档案在 `coding-agent-capability-matrix.md` §10D（那一份才是逐格带行号的现状）。

### P0 安全防线（实质性差距）

1. **无 OS 级沙箱**——Codex Landlock+seccomp、Claude Code Bash/PowerShell 沙箱、Cursor/Cline 都有。你的黑名单 + 确认框是「问人」路线，人家是「操作系统让它不能做」。Agent 学会绕过确认时（如 `powershell Start-Process -NoNewWindow`），确认框形同虚设。（登记为 P0-5，见 `docs/plan-P0-安全与正确性收口.md`；建议先做只加 Job Object 的 S5a，不动权限语义。）**→ 2026-10-15：这一条只剩一半。**S5a 已于 v0.9.135 落地（`minicode/win_sandbox.py`：Job Object 让"终止"成为操作系统保证的终止，整棵树随 `bash_kill`/超时/退出回收，另有单进程 2 GB 与树内 512 进程兜底；起不来就降级并照说"未启用"），**欠的是 S5b 降权与 S5c AppContainer**（`win_sandbox.py:5` 明写"不做降权、不做 AppContainer"，两件板要先拍：降权后用户授权过的工作区外路径还写不写得进、低完整性进程读不到 `%LOCALAPPDATA%` 里的 config.json —— §6 第 52 条）。另有一句当时没料到的：**「完全不管网络」这句今天也不再成立**（N1 出站网址策略 v0.9.141 `minicode/netguard.py`，判定排在只读放行与档位之前 `permission.py:702`；OS 级那一半确实还不管）。
2. ~~**无 apply_patch 工具**~~ —— **v0.9.134 已落地**（`minicode/patch.py` + `apply_patch`：统一 diff 多文件段一次交齐，任一文件对不上就整包一个字节都不写）。登记时那句话留着说明它为什么值得做：Codex、Cline、Cursor 标配，而只有 `edit_file` 字符串替换时，复杂重构会爆炸。
3. ~~**计划模式无「未批不许写」只读态**~~ —— **已落地，本条作废（v0.9.129，2026-10-08 复核）**：原判断来自「`permission.py` 里 grep `plan_mode` 零命中」，而实现用的名字是 `plan_lock`/`plan_locked`。现状：`sandbox.py:662-677` 上锁/解锁（锁跨回合保留）→ `permission.py:181,195` 判定 → **排在档位与白名单之前**（`permission.py:586-591`，所以 `accept_all`/`full`/项目白名单都放不过）→ 只有核对单 verdict=`approve` 才解（`planning.py:487,521,578`），界面「批准这版计划」走同一个 `unlock_plan`（`gui_bridge.py:1250-1263`），`tests/test_plan_lock.py` 钉住五条行为。**留着这段划掉的字是为了记方法论：找不到时先怀疑关键词。**
4. ~~**@ 引用 / 子目录规则嵌套**~~——Claude Code 四级 CLAUDE.md、Cursor 四类 Rules+AGENTS.md、Cline 跨工具兼容、Roo Code 兼容 .roorules。你只有两层（用户级+项目级根），不支持 @import 递归。**→ v0.9.149 第三级落地**（`app_setup.subdir_agents_layers` `:185`：按**被碰到的那个文件**往上找层、顺序 = 根→最近、越近越后所以更具体；采纳**按层记账、一层一次点头**；工作区外那份不算、读不动那份直接不进这一列），v0.9.153 又让根那一层收 `.cursorrules`/`.clinerules`/`.cursor/rules/*.mdc`，v0.9.172 补 `/instructions adopt|revoke` 与无头 `--adopt-project-agents` 闭环。**`@import` 递归仍欠**，而且它和 `@` 提及不是一回事（那是给人指材料，这是让 `AGENTS.md` 自己拉文件，多的是注入面）。

### P1 运行时能力

5. ~~**MCP 缺 OAuth 动态授权、prompts/sampling/elicitation、自身不作为 MCP server**~~——ZCode OAuth 两条流、Claude Code `claude mcp serve`、Cursor roots/elicitation。**→ 这一条四个半项里三个已交、一个是有意不做**：`client_credentials` v0.9.156（`minicode/mcp_oauth.py`，令牌到期自动换、401 自愈一次）、prompts v0.9.172+（`mcp.py:868` 拉 `prompts/list`、`:952` `get_prompt`，能力行自动收窄）、elicitation 接协议不接表单（`mcp.py:988-999` 按规范回 `decline` 并记日志，不再静默丢弃让对端干等）、**本 agent 反过来作 MCP 服务端 v0.9.144**（`--mcp-serve`，只 stdio 单客户端 + 显式只读白名单 + ASK 一律拒）。**有意不做两条**：`authorization_code`（要在用户机器上开回调端口 + 拉浏览器 + 管 PKCE/refresh token —— §6 第 64 条，拒绝而不是忽略）、`sampling`（`mcp.py:59`：让一台外部服务器花这台机器的额度 = 花额度的事必须由人点头）。⚠ **剩一处真欠**：MCP 的 progress/logging。原来这里写着"两处",另一处是 `mcp_prompt` 那只工具 —— **已于 v0.9.175 补上**(list/get、`trust=external` 且非只读:取回的是那台服务器写的一段指示,过确认框、套溯源围栏,与 `mcp_resource` 同一道门;网页版与 `--mcp-serve` 两侧都排除)。
6. ~~**Hooks 事件仅 5 个（原文写 7 个，2026-10-08 数错）**~~——实到的是 `pre_tool`/`post_tool`/`permission_request`/`session_start`/`user_prompt_submit`（`hooks.py:135-137,219,300`）；失败语义硬编码 fail-open，缺可配置 fail_closed（pre 脚本出错固定按不干预放行，`hooks.py:261`）。Claude Code 30+、Qoder 27+、Cline 15+。**→ 这半条整条作废（2026-10-15 按名字读到）**：事件面 v0.9.173 起是 **9 类**（工具侧 `TOOL_EVENT_KINDS` `hooks.py:69` 四类含 `post_tool_failure`；会话侧 `EVENT_KINDS` `:85-86` 五类含 `stop`/`subagent_start`/`subagent_stop`，合并成 `:88` `ALL_KINDS`），失败语义**已做成可选项**（`failure_mode` 与 `on_error` 是同一件事、两个名字都认，单条赢全局、**写错的值一律按 `closed` 拦** —— `hooks.py:104-112`，上面那句"`hooks.py:261` 固定放行"是旧状态）。**真欠的只剩 `pre_compact`/`post_compact` 两类，而且是刻意押后**（`hooks.py:27`：压缩那条路最脆，不在出包那一轮往上面插管子；与 #8 是同一条根）。数量上 Qoder 27 事件仍然多——**但事件数不是这一格的判据**，"pre 改写后重新过一遍门"那条根（`agent.py:63,1528`）才是，别为了对齐数字去补一串没人消费的事件位。
7. **子代理无嵌套、无「恢复仍在跑的」能力**——**并发上限其实有**（`MAX_PARALLEL_TASKS = 6`，`subagents.py:66,742-743`，超出当场报错而不是排队；原文这条也写虚了）。Claude Code（嵌套3层/并发20）、Qoder（worktree isolation + teams）、Cursor（云子代理+并发20）。**✅ 外部定义文件已落地（v0.9.129 `agent_profiles.py`）**：工作区 `agents/*.md` 声明一档，红线「只能收窄」按名字逐行读到（`agent_profiles.py:10-13,131,155,161`，越界那一份整份丢掉并说理由），装载点 `subagents.py:152`。**→ 2026-10-15：这一条两项都已交，各带一句限定**。**嵌套 v0.9.155 落地**（`GLOBAL_SUBAGENT_CEILING=24` 全机在飞上限 + `MAX_SUB_DEPTH=3` + `config.subagent_nesting`，**开关默认关 = 今天的行为**；三处咬合是这一格真正难的地方：文件占用表要跨层共用、并发上限要有全局那一道、超时要能覆盖后代 —— 见 §6 第 63 条）；**「给在跑的那一路递话」v0.9.173 落地**（`minicode/mailbox.py` + `session_message` `catalog_bg.py:115`，子代理每一路开一个盒子、标签带路号，递过去的句子落进对方下一次请求的**系统侧注记**、绝不冒充用户说的话）。⚠ 一句要说准的限制：**主代理在 `task` 里等着的时候自己调不了那只盒子**（它正阻塞等报告），所以即时的投递方是**并发会话与界面上的人**；「唤醒已经结束的那一路」也没有 —— ZCode 的 `SendMessage` 三态 `queued|steered|resumed_background` 里第三态我们不做。**没做前端嵌套树**（机制默认关时它没有可展示的东西）。
8. **上下文压缩前 checkpoint 未隔离对话级回滚**——ZCode 同款限制（fork_required），Claude Code 明确压缩前 checkpoint 不受影响。**→ 仍欠，但这一条现在比登记时便宜得多**：本轮在 `compaction.py` 里 grep `snapshot`/`shadow_git`/`checkpoint` **零命中**（压缩这条路上确实没落还原点），而 v0.9.157~171 已经把 Shadow Git 做完整套 —— **差的只是"在压缩那一刻调一次 `shadow_git.snapshot`"这一根线**。与 #6 末尾那件 `pre_compact` 是同一条根，要做一起拍（先拍"压缩点上要不要给外部脚本否决权"，按 `stop` 那一条的先例，答案应该是**只观测不拦**）。

### P2 生态/差异化

9. ~~**无 Shadow Git 检查点**~~——Cline/Roo Code/Claude Code 用 Shadow Git 或文件系统快照，比自建的 undo 记录更可靠。**→ v0.9.157 起落地、v0.9.171 全链路闭环**（`minicode/shadow_git.py`：`ensure` `:90` 起独立 git-dir 不碰用户仓库历史、`snapshot` `:121`、`tracked_paths` `:176`、`restore_only` `:190` 语义定成「**只还原，绝不删**」、`drop` `:229`；`/snapshots` `/restore` + 命令面板 + CLI 清理 + GUI 门面，见 §6 第 65 条）。⚠ 一句当时没写下的取舍：**自建 undo 记录保留、不被影子仓库取代** —— 两套各有各的范围（影子仓库管工作区文件，undo 记录管"这一处改动"那份账），把它们并成一个才是第二次真相。原文那句"比自建的 undo 记录更可靠"只对了半边：更可靠的是**跨重启与重命名追踪**，不是"这一处是哪一笔"。
10. ~~**无自动记忆**~~——Claude Code / Codex 都有每轮复盘自选留机制，你只有手动 save/forget。**→ 核实为"已经在了，只是形状不同"（`docs/plan-能力补齐-第二批.md:187-191`）**：`memory` 工具的 description 直接把**什么时候该记**写给模型看（`catalog_knowledge.py:312`：「action=save：用户提到偏好/项目约定/纠正/参考资料时记下来」+ `forget` + `dream=整理去重，覆盖前自动备份`），所以"模型自己决定记什么"这条是有的。**刻意不做的是后台自动抽取**：另跑一次模型扫对话 = 替用户决定什么值得记 + 要花额度。**同一条线还不越**：压缩摘要里那行「记忆候选」只**提示**用 `action=save`、绝不自动落盘 —— 记忆是跨会话通道，替外部内容点头等于给注入开一个**持久化**的口子。
11. ~~**无跨工具规则/子代理目录兼容**~~——Cursor 兼容 .claude/agents/、.codex/agents/；Cline 兼容 .cursorrules/.windsurfrules；你外部定义「只读收窄」上已做前置，兼容读取是低门槛高回报。**→ v0.9.152/153 都读了**（`agent_profiles.py:42` `COMPAT_PROFILE_DIRS` = `.cursor/agents`/`.claude/agents`/`.codex/agents`；`app_setup.py:43,50` 收 `.cursorrules`/`.clinerules`/`.cursor/rules/*.mdc`）。**三条比"低门槛高回报"更硬的口径**：①只认工作区里的那几个（不翻本机 `~/.claude/agents`）；②顺序即优先级，本项目自己的 `agents/` 在前；③四条红线对它们一样适用，且根那一层的指纹**覆盖拼起来的一整段正文**——改 `.cursorrules` 一个字也回到"没采纳"。⚠ 原文那句"低门槛高回报"在这一格上是**误判**：多认一个目录 = 多几条"别人写的文件对你说话"的入口，每一条都要走同一次点头，这是加税不是捡钱。
12. ~~**日预算不会「超标即停」**~~——Cursor Cloud 强制 spend limit、ZCode goal tokenBudget 自行进 `budget_limited`。**会话级那一半其实有硬闸**（`session_token_limit` 在循环顶部查、触顶这一次不许再调工具）；缺的是日预算：`daily_token_budget` 只 `push("info")` 提醒。**→ 日预算那一半由 v0.9.144 补成硬闸**，同轮顺手补了计划外的 `task_token_budget`（每回合），今天三份一起在 `agent.py:438-439` 读、在循环顶部查（`:452-471`），各带一句自己的话（`TOKEN_LIMIT_NOTE`/`TASK_BUDGET_NOTE`/`DAILY_BUDGET_NOTE` `:110-125`）。⚠ 上面那句「代码自己写着预算是提醒不是闸门」引的是**旧行号与旧状态**（本轮按名字重读，`agent.py:1638-1660` 那处已经变了）；**这一格今天真正要守的不再是"有没有闸"，而是"闸关的时候要说话"** —— 被预算收尾的一轮不许长得像"任务做完了"。（原文这条把两个数一起否了，2026-10-08 收窄；2026-10-15 整条收口。）
13. **无云/远程执行体**——Cursor Cloud Agents、Claude Code cloud sessions、Codex cloud、Qoder Cloud Agents 全是独立产品线。**→ 2026-10-15：这一条改标〔不做〕，不是待办**。本地优先是立场不是债：一旦有"在别处跑"，这台机器上的围栏、本机信任表、`SHIP_NEVER` 那本账全部要重算，而重算的方向是把"这台机器的主人点过头"换成"某个服务端认为可以"。⚠ 竞品这一维**还在往前走**（ZCode v3.15.1 的 commit 标题里有 remote workspace，它更早还有 SSH/WSL/Docker 与手机接管）——**记清楚：这是"我们不参与这一维"，读到"落后"是错的**。
14. ~~**无浏览器/计算机接管**~~——Cursor Browser 工具、Claude Code claude-in-chrome、ZCode 内置面板、Roo auto-approve。**→ 前一半被十一只工具推翻**（`catalog_web.py:22-247`：`web_crawl`/`web_search` + `browser_open`/`eval`/`snapshot`/`click`/`fill`/`screenshot`/`close`/`status` + `page_parse`，`snapshot` 那份带序号的元素清单是 click/fill 的定位依据），**后一半〔不做〕**：全仓 `pyautogui`/`computer_use` 零命中 —— "在真浏览器里替人办一件事"与"接管整机工作台"不是一回事，后者的授权粒度这台机器给不出。**这一条原本把两件事捆在一句里，捆着就永远只能整条读成差距**。
15. ~~**无语音输入**~~——Aider /voice、Codex audio config+voice_transcription、Cursor voice-input（文档 3 处）。**→ 这一条的形状很特别：做过、又按用户要求整体撤下（v0.9.174）**。v0.9.172+ 落过一次（`minicode/voice.py` + 输入行 🎤，离线 Windows 语音识别、只填不发、四种"没听到"各有实话），随后删干净：模块与 8 支测试、桥的两只门面（`voice_probe`/`voice_listen`，Bridge 与 PublicApi 两层各一份）、那颗 🎤 连 CSS/JS、四处冻结针、e2e 的桩与那一场检查、《使用说明》原第 148 条（后 5 条顺延成 148~152）。**历史账保留**并标「已撤下，别再按它核对代码」（§6 第 73 条）。**把它读成待办是错的**：那三家有语音不构成这里的理由。

## 四、推荐路线图（12 条功能按 ROI 排序）

### 阶段一：生态兼容 + 重构体验（低门槛高回报）

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| ~~A~~ | ~~项目级 AGENTS.md 支持子目录嵌套~~ **已于 v0.9.149 落地**（`app_setup.subdir_agents_layers` + `Agent._layer_feedback`；按**被碰到的那个文件**取层，不是按 cwd，也不是递归扫全工作区；采纳按层记账，一层一次点头）。⚠ **原来那句"最低实现"是错的**：它写的是"递归扫描子目录 AGENTS.md + basename 排序 + 现有注入顺序"，只说了**怎么找**，没说**怎么采纳、什么时候注入** —— 照它做会做出一个"每层都靠一次工作区级点头"的东西，而"每层各自采纳"正是这一格在安全上唯一比两级更严的地方。 | 生态位，竞品全在做，边际成本最低 | 递归扫描子目录 AGENTS.md + basename 排序 + 现有注入顺序 |
| ~~B~~ | ~~apply_patch 工具~~ **已于 v0.9.134 落地**（`minicode/patch.py`；矩阵 §10D「专用补丁工具」那一格 ✖→✅） | multi-file 重构时 edit_file 字符串替换爆炸，竞品全包 | patch_text → files[]{path,type,structuredPatch[],gitDiff{}}，错误码编号化 |
| ~~D~~ | ~~Plan mode「未批不许写」态~~ **已于 v0.9.129 落地**（见 §三 P0 第 3 条的行号） | 安全红线，Qoder/ZCode 都有 | 实现形状与原文设想不同但覆盖相同：不是 `EnterPlanMode/ExitPlanMode` 两个工具，而是 `plan(action=write)` 上锁 + 核对单 `approve` 才解 + 判定排在档位之前（因此钩子的 `allow` 也绕不过，与 v0.9.115「pre 改写后重跑门」同一条根） |

### 阶段二：运行时硬实力

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| ~~C~~ | ~~MCP OAuth 动态授权~~ **`client_credentials` 已于 v0.9.156 落地**（`minicode/mcp_oauth.py`），**`authorization_code` 明确不做**（`HANDOFF` §6 64：它要在用户机器上**开回调端口** + **拉起浏览器** + 管 PKCE/refresh token —— 那是另外几笔账；配置里写了它会得到一句"没有实现 + 两条替代路"，**拒绝而不是忽略**）。⚠ **原文那句"`${ENV}` 二次注入"背后有一个真坑**：`os.path.expandvars` 对**没设**的变量**原样留下**，所以"展开后是空的"这个判据永远不触发；判据必须是"还剩没剩 `${`"。另一处：解析后的 `${ENV}` **已经展开**，拿它回写 config.json 就是**把密钥落盘**（所以原文与展开文各留一份）。 | ZCode 都做了，缺的话 MCP 产品力低一档 | type:client_credentials + type:authorization_code 两条流，${ENV} 二次注入 |
| ~~F~~ | ~~Hook 失败语义 fail_open/fail_closed~~ **本来就不成立**（2026-10-10 复核）：`minicode/hooks.py` 早就有 `on_error`（`block`=fail-closed 是默认 / `continue`=fail-open）与 per-entry `timeout` —— 它只是**名字不叫 `failure_mode`**，又是一次「grep 关键词零命中」 | 钩子从「能用」变「可配」，Cline 有 | ~~hooks.py 加 failure_mode 配置~~ **v0.9.173 已加**(`failure_mode` 与 `on_error` 是同一件事、两个词都认、支持在 `hooks` 顶层写全局默认;同一轮事件面从 7 类补到 9 类: |
| ~~G~~ | ~~会话级 spend limit（硬预算）~~ **早就在**（2026-10-10 复核）：`session_token_limit` 在请求循环顶部查、触顶这一次不许再调工具（`agent.py`）；**日预算那一半由 v0.9.144 补成硬闸** | session_token_limit 现在是交代而非「停」 | agent.py 请求循环顶部查 tokens_used >= budget，超走 max_steps_reached 同出口 |
| ~~L~~ | ~~子代理并发上限 + 嵌套~~ **已于 v0.9.155 落地**（`subagents.GLOBAL_SUBAGENT_CEILING=24` + `MAX_SUB_DEPTH=3` + `config.subagent_nesting`，**开关默认关 = 今天的行为**）。⚠ **原文那三个数背后真正要解的是三处咬合**（`HANDOFF` §6 63）：①文件占用表要**跨层共用**（不然父批与子批各一张，同一个文件可以跨层同时被写而两边都不报错）；②并发上限要有**全局**那一道（批次上限管不到嵌套，也管不到"后台会话各建各的 Agent"那一层）；③超时要能**覆盖后代**（今天不覆盖：父级到点放弃，而孙辈还在改文件）。**没做前端嵌套树**（机制默认关时它没有可展示的东西，而且那是展示层）。 | Claude Code 嵌套3/并发20、Qoder 权限位、Cursor 并发20 | profile 加 maxConcurrent=10 + maxDepth=3，前端嵌套树展示 |

### 阶段三：生态 + 扩展

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| ~~E~~ | ~~子代理外部目录兼容（读）~~ **已于 v0.9.152 落地**（`agent_profiles.COMPAT_PROFILE_DIRS` = `.cursor/agents/` / `.claude/agents/` / `.codex/agents/`，追加进 `profile_dirs()` 的工作区那一段）。**三条比原文更严的口径**：①**只认工作区里的那几个**，不去翻你本机的 `~/.claude/agents`（这一层的语义是"仓库自带的定义跟着仓库走"，读工作区外面是另一笔账）；②**顺序即优先级**——本项目自己的 `agents/` 在前面，同名时它赢；③**四条红线对它们一样适用**，这是这一层唯一可能出事的地方（它多开了一个读 markdown 的口子，而那里的文件是别人写的、会跟着 clone 走），有一支针拿一份写了提权字段的 `.cursor` 文件去撞。 | 生态利好，只读层面兼容即可 | .cursor/agents/、.claude/agents/、.codex/agents/ 扫描 + frontmatter name/description |
| ~~H~~ | ~~Shadow Git 检查点~~ **已于 v0.9.157~171 全链路闭环**（`minicode/shadow_git.py`：`ensure`/`snapshot`/`tracked_paths`/`restore_only`/`drop`，回滚语义「只还原，绝不删」；`/snapshots` `/restore` + 命令面板 + CLI 清理 + GUI 门面。见 §6 第 65 条与 §三 第 9 条）。⚠ **这一行是 2026-10-15 才补划的**：代码早就交了，表格里一直没划线 —— 而这张表是"下一轮的起点"，漏划一格的后果就是把做完的再干一遍（同一轮还漏划过 §三 的 4/5/6/7/9/10/11/12/14/15 十条）。 | 自建 undo vs Shadow Git，后者不动仓库历史 | 改前 git add -N + git commit --no-verify；/rewind 三档 —— **实际形状不同**：影子仓库不是"改前 add -N 提交进用户仓库"而是**独立 git-dir**（用户仓库历史一个字节不动），回滚是 `restore_only` 而非 `git checkout`（**绝不删**），所以不存在"顺手改了用户的 index" |
| ~~I~~ | ~~跨会话消息工具（子代理 → 主会话）~~ **已于 v0.9.154 落地**（`Agent.queue_note` + `catalog_sub.notify_parent`）。⚠ **照原文那句直接做会写错**:现成的插话信箱落进留档时是 `role: user`(`_take_inbox` 里做 `session.add_user`),子代理的提醒走那条路就成了"用户说过的话" —— 所以这一条**另开了一条系统侧通道**(并进 `_pending_prior`,与三个事件钩子附话同一条),而不是复用信箱。 | 子代理没有提问通道，补齐「信箱」 | 子代理会话侧加 SendInterjection 工具 |
| ~~J~~ | ~~@ 引用 + .cursorrules/.clinerules 兼容~~ **已于 v0.9.153 落地**（`@` 引用早在 v0.9.64 就有了，这一轮补的是后半截：`app_setup.COMPAT_RULE_FILES` + `CURSOR_RULE_DIR`）。**一处比原文更硬的形状**：根那一层从"一份文件"变成"**一组来源**"，`sha` 覆盖**拼起来的一整段正文** —— 改 `.cursorrules` 里一个字也要回到"没采纳"，否则"别人的文件改了、点头照旧有效"就是一条看不见的缝。**`.mdc` 只读没有条件的那几条**，带 `globs` 的不读但在报表里点名（本项目刻意不做规则引擎）。 | 用户定位具体规则文件时要做 | @<path> importer + .cursor/rules/*.mdc / .clinerules/*.md 读取 |
| ~~K~~ | ~~语音输入~~ **已交付又撤下** | Aider /voice、Codex voice_transcription、Cursor 文档 3 处、Qoder 3 处 | --voice 或输入框 🎤 → winrt ScreenCapturer → SpeechRecognizer/离线 STT → 拼消息 |

## 五、当前版本自画像

- **强项**（2026-10-10 v0.9.152 复核；**本轮新增的加粗**）：独立完整体、桌面 GUI、个人数据闭环、验收闸、钩子改写重跑（`on_error` 与 per-entry `timeout` 可配，见 `minicode/hooks.py`）**+ 事件钩子三点齐了（`session_start` / `user_prompt_submit` / `stop`，v0.9.151）**、无头模式 6 态 7 码（含 `--output-schema`）**+ 事件流（`--output-format stream-json`，v0.9.144）**、microcompact、**日预算已是硬闸（v0.9.144，同日补上计划外的 `task_token_budget`）**、**规划锁（未批不许写）**、**子代理档位可自定义，v0.9.152 起还会认别的工具放在仓库里的档位目录（`.cursor/agents/` 等）**、**改动前先读过（v0.9.133）**、**「以后都允许」落一条本机规则（v0.9.133）**、**`apply_patch` 一次落一整包且对不上就整包不写（v0.9.134）**、**Job Object 让「终止」成为操作系统保证的终止（v0.9.135）**、**本 agent 反过来可作 MCP 服务端（`--mcp-serve`，v0.9.144）**、**`bash_input` 给后台命令喂 stdin（v0.9.146）**、**`diagnostics` 编辑后多语言诊断回灌（v0.9.147）**、**`--version` 零成本本地旗标（v0.9.145）**、**指令文件第三级：子目录就近 `AGENTS.md`，按被碰到的文件取层、每层各自采纳（v0.9.149）**、**别的工具的规则文件进同一层（`.cursorrules` / `.clinerules` / `.cursor/rules/*.mdc`，v0.9.153）**、**子代理 → 主会话留话（`notify_parent`，走系统侧注记而**不是**用户插话那条路，v0.9.154）**、**子代理的并发边界（全机在飞上限 + 文件占用表跨层共用，v0.9.155）与嵌套开关（默认关）**、**MCP 的 OAuth 动态授权(`client_credentials`,令牌到期自动换、401 自愈一次,v0.9.156)**、**（2026-10-15 补）Shadow Git 检查点全链路（v0.9.157~171，回滚语义「只还原，绝不删」）**、**会话分叉的图形入口（`/fork` + Fork 菜单，v0.9.171，与命令行同一个 `session.fork_log_file`）**、**代码智能八只（repo_wiki / test_runner / symbol_search / find_references / rename_symbol / call_hierarchy / symbol_definition / code_outline，v0.9.163~173）**、**命令面板与快捷键速查中心（Ctrl+Shift+P）**、**钩子事件面 7→9 类与 `failure_mode` 可配（v0.9.173）**、**给在跑的子代理与别的活会话递话（`mailbox` + `session_message`，落系统侧注记不冒充用户，v0.9.173）**、**看图 `view_image`（模型能看见自己刚画出来的那张，v0.9.173）**、**`ocr_image` 吃扫描版 PDF（v0.9.172+）**、**分层指令采纳闭环 `/instructions adopt|revoke` + 无头 `--adopt-project-agents`（v0.9.172）**、**MCP 服务端补到 prompts、elicitation 按规范回 decline（v0.9.172+）**、**工具面三层投影 `tool_profile`（v0.9.173，默认关）**、**扩展中心页一屏摊开五类扩展且"没装上"逐条带原因（v0.9.172+）**、**App 更新检查走 GitHub Release + 整段更新日志卡 + 证书被本机代理接管时换系统证书库再试一次（v0.9.172+/174）**、**出站网址策略 N1（v0.9.141，模型能自己填网址那三条路交给主人管）**
- **弱项**（2026-10-10 逐条读码复核，上一版那四条里**三条已收、只剩一条**）：①**OS 沙箱只走了 S5a**（降权 S5b、AppContainer S5c 未做，理由与前置决策见 `docs/plan-P0-安全与正确性收口.md` §三.5 与 `HANDOFF.md` §6 第 52 条）。**已收的三条**：~~日预算只提醒不成闸~~ → v0.9.144 改成硬闸；~~无头模式没有 `stream-json` 事件流~~ → v0.9.144 补上；~~指令文件只有两级~~ → **v0.9.149 补上第三级**（子目录就近 `AGENTS.md`，按被碰到的文件取层、每层各自采纳，见 §四 A 与 `docs/roadmap-分层指令文件.md` §九）。
- **下一步**（2026-10-15 重写：这一节原来是"照它读"的入口，所以它必须只列真欠的）：**第一批五件**与**第二批三件**全部交完，**§四 那张 12 条表里只剩一件没做**（见下表 K 已撤下、其余全划）。按账的性质排：
  1. **每天在付的一笔**（唯一一条用户每次都感受到的）：冷启动约 10.2s，其中 **9.87s** 是 `library.py:676` 在 1.31 GB 只读馆藏库上跑一次全表统计（§6 第 60 条明写"没修"；`_STATS_CACHE` 只救同进程的第二次问）。
  2. **一次性账**：§6 第 52 条欠的 **S5b/S5c**（等两件板）、**`pre_compact` + 压缩前落还原点**（同一条根，Shadow Git 已在手上）、**`AGENTS.md` 的 `@import` 递归**。
  3. **三处小账**（原四处;**`mcp_prompt` 那只工具已于 v0.9.175 收掉** —— 它登记时等的"注册表精确清单恢复绿"其实 v0.9.173 就绿了,账却白躺了三十来个版本,这条教训写在 `docs/plan-能力补齐-第二批.md` §四 与本表 §三 第 5 条）：MCP 的 progress/logging、`apply_patch` 不收 Codex 方言与 rename/二进制（**有意**）、**命令行那一侧的插话**（`ui.py:65,74` 仍是阻塞 `input()`）。
  ⚠ **别再按旧句子读这一节**：上一版写的"本文的 A 与 C/E/H/I/J/K"里，**A/C/E/H/I/J/K 七条到今天全部已交或已作废**（A v0.9.149、C v0.9.156 半 + 半有意不做、E v0.9.152、H v0.9.157~171、I v0.9.154、J v0.9.153、K 交付后 v0.9.174 撤下）。**本文 §四 那张 12 条路线图现在十二条全部有下落**：D 与 G 是既成事实、F 本来就不成立、其余按上面的版本号一条条划掉了。

---

> ⚠ **这一段旧自画像已删掉内容、只留一句指路（2026-10-15）**。原来这里抄了一份上面的强项清单，还写着「短板只剩两条：OS 沙箱只到 S5a、指令文件两级而非三级」—— 后半句在 v0.9.149 之后就错了（第三级已落），而它错着的同时 §五 那两条 bullet 是对的：**同一条现状写在两处，一定会有一份漂**（本项目为这件事专门立过"一件事只有一处真相"表，见 `CONTRIBUTING.md`）。现状一律读**上面 §五 那两条 bullet + §四 那张 12 条表**，这一处不再复述。
>
> **改口史（留着是为了记方法论，不是为了好看）**：第一版列了七条短板，逐条读码复核后只剩四条；v0.9.132 那次复核又作废了一条（「规划模式没有未批不许写」是**关键词找错**——实现叫 `plan_lock`）；v0.9.145/v0.9.146/v0.9.147 这三轮把剩下四条里的两条真做掉了（日预算硬闸、无头事件流），另两条仍在。**「grep 关键词零命中」证明的是「这个名字没有」，不是「这个能力没有」——下 ✖ 之前先换两个名字再找一遍。** 同一份文档里 §四 那张路线图的 D / F / G 三条也都是这么被误登记的（D 的 `plan_lock` 早在 v0.9.129、F 的 `on_error` 一直都在、G 的会话级硬闸 `session_token_limit` 也在），已在 §五 那行写明。