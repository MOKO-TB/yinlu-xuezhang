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

### P0 安全防线（实质性差距）

1. **无 OS 级沙箱**——Codex Landlock+seccomp、Claude Code Bash/PowerShell 沙箱、Cursor/Cline 都有。你的黑名单 + 确认框是「问人」路线，人家是「操作系统让它不能做」。Agent 学会绕过确认时（如 `powershell Start-Process -NoNewWindow`），确认框形同虚设。（登记为 P0-5，见 `docs/plan-P0-安全与正确性收口.md`；建议先做只加 Job Object 的 S5a，不动权限语义。）
2. ~~**无 apply_patch 工具**~~ —— **v0.9.134 已落地**（`minicode/patch.py` + `apply_patch`：统一 diff 多文件段一次交齐，任一文件对不上就整包一个字节都不写）。登记时那句话留着说明它为什么值得做：Codex、Cline、Cursor 标配，而只有 `edit_file` 字符串替换时，复杂重构会爆炸。
3. ~~**计划模式无「未批不许写」只读态**~~ —— **已落地，本条作废（v0.9.129，2026-10-08 复核）**：原判断来自「`permission.py` 里 grep `plan_mode` 零命中」，而实现用的名字是 `plan_lock`/`plan_locked`。现状：`sandbox.py:662-677` 上锁/解锁（锁跨回合保留）→ `permission.py:181,195` 判定 → **排在档位与白名单之前**（`permission.py:586-591`，所以 `accept_all`/`full`/项目白名单都放不过）→ 只有核对单 verdict=`approve` 才解（`planning.py:487,521,578`），界面「批准这版计划」走同一个 `unlock_plan`（`gui_bridge.py:1250-1263`），`tests/test_plan_lock.py` 钉住五条行为。**留着这段划掉的字是为了记方法论：找不到时先怀疑关键词。**
4. **@ 引用 / 子目录规则嵌套**——Claude Code 四级 CLAUDE.md、Cursor 四类 Rules+AGENTS.md、Cline 跨工具兼容、Roo Code 兼容 .roorules。你只有两层（用户级+项目级根），不支持 @import 递归。

### P1 运行时能力

5. **MCP 缺 OAuth 动态授权、prompts/sampling/elicitation、自身不作为 MCP server**——ZCode OAuth 两条流、Claude Code `claude mcp serve`、Cursor roots/elicitation。
6. **Hooks 事件仅 5 个（原文写 7 个，2026-10-08 数错）**——实到的是 `pre_tool`/`post_tool`/`permission_request`/`session_start`/`user_prompt_submit`（`hooks.py:135-137,219,300`）；失败语义硬编码 fail-open，缺可配置 fail_closed（pre 脚本出错固定按不干预放行，`hooks.py:261`）。Claude Code 30+、Qoder 27+、Cline 15+。
7. **子代理无嵌套、无「恢复仍在跑的」能力**——**并发上限其实有**（`MAX_PARALLEL_TASKS = 6`，`subagents.py:66,742-743`，超出当场报错而不是排队；原文这条也写虚了）。Claude Code（嵌套3层/并发20）、Qoder（worktree isolation + teams）、Cursor（云子代理+并发20）。**✅ 外部定义文件已落地（v0.9.129 `agent_profiles.py`）**：工作区 `agents/*.md` 声明一档，红线「只能收窄」按名字逐行读到（`agent_profiles.py:10-13,131,155,161`，越界那一份整份丢掉并说理由），装载点 `subagents.py:152`。
8. **上下文压缩前 checkpoint 未隔离对话级回滚**——ZCode 同款限制（fork_required），Claude Code 明确压缩前 checkpoint 不受影响。

### P2 生态/差异化

9. **无 Shadow Git 检查点**——Cline/Roo Code/Claude Code 用 Shadow Git 或文件系统快照，比自建的 undo 记录更可靠。
10. **无自动记忆**——Claude Code / Codex 都有每轮复盘自选留机制，你只有手动 save/forget。
11. **无跨工具规则/子代理目录兼容**——Cursor 兼容 .claude/agents/、.codex/agents/；Cline 兼容 .cursorrules/.windsurfrules；你外部定义「只读收窄」上已做前置，兼容读取是低门槛高回报。
12. **日预算不会「超标即停」**——Cursor Cloud 强制 spend limit、ZCode goal tokenBudget 自行进 `budget_limited`。**会话级那一半其实有硬闸**（`session_token_limit` 在循环顶部查、触顶这一次不许再调工具，`agent.py:289-297`）；缺的是日预算：`daily_token_budget` 只 `push("info")` 提醒，代码自己写着「预算是提醒,不是闸门」（`agent.py:1638-1660`）。所以真差距是「把日预算也做成闸门」，不是「没有预算」。（原文这条把两个数一起否了，2026-10-08 收窄。）
13. **无云/远程执行体**——Cursor Cloud Agents、Claude Code cloud sessions、Codex cloud、Qoder Cloud Agents 全是独立产品线。
14. **无浏览器/计算机接管**——Cursor Browser 工具、Claude Code claude-in-chrome、ZCode 内置面板、Roo auto-approve。
15. **无语音输入**——Aider /voice、Codex audio config+voice_transcription、Cursor voice-input（文档 3 处）。

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
| C | MCP OAuth 动态授权 | ZCode 都做了，缺的话 MCP 产品力低一档 | type:client_credentials + type:authorization_code 两条流，${ENV} 二次注入 |
| ~~F~~ | ~~Hook 失败语义 fail_open/fail_closed~~ **本来就不成立**（2026-10-10 复核）：`minicode/hooks.py` 早就有 `on_error`（`block`=fail-closed 是默认 / `continue`=fail-open）与 per-entry `timeout` —— 它只是**名字不叫 `failure_mode`**，又是一次「grep 关键词零命中」 | 钩子从「能用」变「可配」，Cline 有 | hooks.py 加 failure_mode 配置；pre 脚本失败走之 |
| ~~G~~ | ~~会话级 spend limit（硬预算）~~ **早就在**（2026-10-10 复核）：`session_token_limit` 在请求循环顶部查、触顶这一次不许再调工具（`agent.py`）；**日预算那一半由 v0.9.144 补成硬闸** | session_token_limit 现在是交代而非「停」 | agent.py 请求循环顶部查 tokens_used >= budget，超走 max_steps_reached 同出口 |
| L | 子代理并发上限 + 嵌套 | Claude Code 嵌套3/并发20、Qoder 权限位、Cursor 并发20 | profile 加 maxConcurrent=10 + maxDepth=3，前端嵌套树展示 |

### 阶段三：生态 + 扩展

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| ~~E~~ | ~~子代理外部目录兼容（读）~~ **已于 v0.9.152 落地**（`agent_profiles.COMPAT_PROFILE_DIRS` = `.cursor/agents/` / `.claude/agents/` / `.codex/agents/`，追加进 `profile_dirs()` 的工作区那一段）。**三条比原文更严的口径**：①**只认工作区里的那几个**，不去翻你本机的 `~/.claude/agents`（这一层的语义是"仓库自带的定义跟着仓库走"，读工作区外面是另一笔账）；②**顺序即优先级**——本项目自己的 `agents/` 在前面，同名时它赢；③**四条红线对它们一样适用**，这是这一层唯一可能出事的地方（它多开了一个读 markdown 的口子，而那里的文件是别人写的、会跟着 clone 走），有一支针拿一份写了提权字段的 `.cursor` 文件去撞。 | 生态利好，只读层面兼容即可 | .cursor/agents/、.claude/agents/、.codex/agents/ 扫描 + frontmatter name/description |
| H | Shadow Git 检查点 | 自建 undo vs Shadow Git，后者不动仓库历史 | 改前 git add -N + git commit --no-verify；/rewind 三档 |
| ~~I~~ | ~~跨会话消息工具（子代理 → 主会话）~~ **已于 v0.9.154 落地**（`Agent.queue_note` + `catalog_sub.notify_parent`）。⚠ **照原文那句直接做会写错**:现成的插话信箱落进留档时是 `role: user`(`_take_inbox` 里做 `session.add_user`),子代理的提醒走那条路就成了"用户说过的话" —— 所以这一条**另开了一条系统侧通道**(并进 `_pending_prior`,与三个事件钩子附话同一条),而不是复用信箱。 | 子代理没有提问通道，补齐「信箱」 | 子代理会话侧加 SendInterjection 工具 |
| ~~J~~ | ~~@ 引用 + .cursorrules/.clinerules 兼容~~ **已于 v0.9.153 落地**（`@` 引用早在 v0.9.64 就有了，这一轮补的是后半截：`app_setup.COMPAT_RULE_FILES` + `CURSOR_RULE_DIR`）。**一处比原文更硬的形状**：根那一层从"一份文件"变成"**一组来源**"，`sha` 覆盖**拼起来的一整段正文** —— 改 `.cursorrules` 里一个字也要回到"没采纳"，否则"别人的文件改了、点头照旧有效"就是一条看不见的缝。**`.mdc` 只读没有条件的那几条**，带 `globs` 的不读但在报表里点名（本项目刻意不做规则引擎）。 | 用户定位具体规则文件时要做 | @<path> importer + .cursor/rules/*.mdc / .clinerules/*.md 读取 |
| K | 语音输入 | Aider /voice、Codex voice_transcription、Cursor 文档 3 处、Qoder 3 处 | --voice 或输入框 🎤 → winrt ScreenCapturer → SpeechRecognizer/离线 STT → 拼消息 |

## 五、当前版本自画像

- **强项**（2026-10-10 v0.9.152 复核；**本轮新增的加粗**）：独立完整体、桌面 GUI、个人数据闭环、验收闸、钩子改写重跑（`on_error` 与 per-entry `timeout` 可配，见 `minicode/hooks.py`）**+ 事件钩子三点齐了（`session_start` / `user_prompt_submit` / `stop`，v0.9.151）**、无头模式 6 态 7 码（含 `--output-schema`）**+ 事件流（`--output-format stream-json`，v0.9.144）**、microcompact、**日预算已是硬闸（v0.9.144，同日补上计划外的 `task_token_budget`）**、**规划锁（未批不许写）**、**子代理档位可自定义，v0.9.152 起还会认别的工具放在仓库里的档位目录（`.cursor/agents/` 等）**、**改动前先读过（v0.9.133）**、**「以后都允许」落一条本机规则（v0.9.133）**、**`apply_patch` 一次落一整包且对不上就整包不写（v0.9.134）**、**Job Object 让「终止」成为操作系统保证的终止（v0.9.135）**、**本 agent 反过来可作 MCP 服务端（`--mcp-serve`，v0.9.144）**、**`bash_input` 给后台命令喂 stdin（v0.9.146）**、**`diagnostics` 编辑后多语言诊断回灌（v0.9.147）**、**`--version` 零成本本地旗标（v0.9.145）**、**指令文件第三级：子目录就近 `AGENTS.md`，按被碰到的文件取层、每层各自采纳（v0.9.149）**、**别的工具的规则文件进同一层（`.cursorrules` / `.clinerules` / `.cursor/rules/*.mdc`，v0.9.153）**、**子代理 → 主会话留话（`notify_parent`，走系统侧注记而**不是**用户插话那条路，v0.9.154）**
- **弱项**（2026-10-10 逐条读码复核，上一版那四条里**三条已收、只剩一条**）：①**OS 沙箱只走了 S5a**（降权 S5b、AppContainer S5c 未做，理由与前置决策见 `docs/plan-P0-安全与正确性收口.md` §三.5 与 `HANDOFF.md` §6 第 52 条）。**已收的三条**：~~日预算只提醒不成闸~~ → v0.9.144 改成硬闸；~~无头模式没有 `stream-json` 事件流~~ → v0.9.144 补上；~~指令文件只有两级~~ → **v0.9.149 补上第三级**（子目录就近 `AGENTS.md`，按被碰到的文件取层、每层各自采纳，见 §四 A 与 `docs/roadmap-分层指令文件.md` §九）。
- **下一步**：`docs/plan-能力补齐-第一批.md` 的五件**已全部交完**（1-1 / 1-3 / 1-4 → v0.9.144，1-5 → v0.9.146，1-2 → v0.9.147）。剩下的按 ROI 排：§6 第 52 条欠的 S5b/S5c、本文的 A（子目录嵌套）与 C/E/H/I/J/K、以及 §6 第 53 条那几处小账。**本文 §四 那张 12 条路线图里，D 与 G 已经是既成事实**（D 的 `plan_lock` v0.9.129 落地、G 的会话级硬闸 `session_token_limit` 早就在循环顶部查），**F 也本来就不成立**（`on_error` 就是可配的失败语义）—— 三条都别再当待办读。

---

> 自画像（2026-10-10 v0.9.147 复核）：独立完整体、桌面 GUI、个人数据闭环、验收闸、钩子改写（`on_error`/`timeout` 可配）、无头 6 态 7 码（含 `--output-schema`）+ **事件流**、microcompact、**日预算硬闸**、规划锁、子代理自定义档位、改动前先读过、批准可落成本机规则、`apply_patch` 整包原子、Job Object 进程树回收、**MCP 服务端模式**、**`bash_input`**、**`diagnostics`**、**`--version`**。短板**只剩两条**：OS 沙箱只到 S5a（降权与 AppContainer 未做）、指令文件两级而非三级。
>
> **改口史（留着是为了记方法论，不是为了好看）**：第一版列了七条短板，逐条读码复核后只剩四条；v0.9.132 那次复核又作废了一条（「规划模式没有未批不许写」是**关键词找错**——实现叫 `plan_lock`）；v0.9.145/v0.9.146/v0.9.147 这三轮把剩下四条里的两条真做掉了（日预算硬闸、无头事件流），另两条仍在。**「grep 关键词零命中」证明的是「这个名字没有」，不是「这个能力没有」——下 ✖ 之前先换两个名字再找一遍。** 同一份文档里 §四 那张路线图的 D / F / G 三条也都是这么被误登记的（D 的 `plan_lock` 早在 v0.9.129、F 的 `on_error` 一直都在、G 的会话级硬闸 `session_token_limit` 也在），已在 §五 那行写明。