# 引路学长 能力路线图文档

> 生成时间：2026-10-07
> 对比基准：Codex / Claude Code / Cursor / Cline / Roo Code / Aider / ZCode / Qoder
> 版本：v0.9.120+

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

1. **无 OS 级沙箱**——Codex Landlock+seccomp、Claude Code Bash/PowerShell 沙箱、Cursor/Cline 都有。你的黑名单 + 确认框是「问人」路线，人家是「操作系统让它不能做」。Agent 学会绕过确认时（如 `powershell Start-Process -NoNewWindow`），确认框形同虚设。
2. **无 apply_patch 工具**——Codex、Cline、Cursor 标配。只有 edit_file 字符串替换在复杂重构时爆炸。
3. **计划模式无「未批不许写」只读态**——Qoder 有 permissionMode:plan、ZCode plan 禁令不可绕过。你的 permission.py 里 grep plan_mode 零命中。
4. **@ 引用 / 子目录规则嵌套**——Claude Code 四级 CLAUDE.md、Cursor 四类 Rules+AGENTS.md、Cline 跨工具兼容、Roo Code 兼容 .roorules。你只有两层（用户级+项目级根），不支持 @import 递归。

### P1 运行时能力

5. **MCP 缺 OAuth 动态授权、prompts/sampling/elicitation、自身不作为 MCP server**——ZCode OAuth 两条流、Claude Code `claude mcp serve`、Cursor roots/elicitation。
6. **Hooks 事件仅 7 个，失败语义硬编码 fail-open，缺可配置 fail_closed**——Claude Code 30+、Qoder 27+、Cline 15+。
7. **子代理无嵌套、并发上限、恢复仍在运行的能力**——Claude Code（嵌套3层/并发20）、Qoder（worktree isolation + teams）、Cursor（云子代理+并发20）。
8. **上下文压缩前 checkpoint 未隔离对话级回滚**——ZCode 同款限制（fork_required），Claude Code 明确压缩前 checkpoint 不受影响。

### P2 生态/差异化

9. **无 Shadow Git 检查点**——Cline/Roo Code/Claude Code 用 Shadow Git 或文件系统快照，比自建的 undo 记录更可靠。
10. **无自动记忆**——Claude Code / Codex 都有每轮复盘自选留机制，你只有手动 save/forget。
11. **无跨工具规则/子代理目录兼容**——Cursor 兼容 .claude/agents/、.codex/agents/；Cline 兼容 .cursorrules/.windsurfrules；你外部定义「只读收窄」上已做前置，兼容读取是低门槛高回报。
12. **无硬预算上限**——Cursor Cloud 强制 spend limit、ZCode goal tokenBudget，你是触顶交代而非「超标即停」。
13. **无云/远程执行体**——Cursor Cloud Agents、Claude Code cloud sessions、Codex cloud、Qoder Cloud Agents 全是独立产品线。
14. **无浏览器/计算机接管**——Cursor Browser 工具、Claude Code claude-in-chrome、ZCode 内置面板、Roo auto-approve。
15. **无语音输入**——Aider /voice、Codex audio config+voice_transcription、Cursor voice-input（文档 3 处）。

## 四、推荐路线图（12 条功能按 ROI 排序）

### 阶段一：生态兼容 + 重构体验（低门槛高回报）

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| A | 项目级 AGENTS.md 支持子目录嵌套 | 生态位，竞品全在做，边际成本最低 | 递归扫描子目录 AGENTS.md + basename 排序 + 现有注入顺序 |
| B | apply_patch 工具 | multi-file 重构时 edit_file 字符串替换爆炸，竞品全包 | patch_text → files[]{path,type,structuredPatch[],gitDiff{}}，错误码编号化 |
| D | Plan mode「未批不许写」态 | 安全红线，Qoder/ZCode 都有 | permissionMode:plan + EnterPlanMode/ExitPlanMode；plan 态 Bash/Edit/Write 整批 deny，只读放行 |

### 阶段二：运行时硬实力

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| C | MCP OAuth 动态授权 | ZCode 都做了，缺的话 MCP 产品力低一档 | type:client_credentials + type:authorization_code 两条流，${ENV} 二次注入 |
| F | Hook 失败语义 fail_open/fail_closed | 钩子从「能用」变「可配」，Cline 有 | hooks.py 加 failure_mode 配置；pre 脚本失败走之 |
| G | 会话级 spend limit（硬预算） | session_token_limit 现在是交代而非「停」 | agent.py 请求循环顶部查 tokens_used >= budget，超走 max_steps_reached 同出口 |
| L | 子代理并发上限 + 嵌套 | Claude Code 嵌套3/并发20、Qoder 权限位、Cursor 并发20 | profile 加 maxConcurrent=10 + maxDepth=3，前端嵌套树展示 |

### 阶段三：生态 + 扩展

| 编号 | 功能 | 理由 | 最低实现 |
|---|---|---|---|
| E | 子代理外部目录兼容（读） | 生态利好，只读层面兼容即可 | .cursor/agents/、.claude/agents/、.codex/agents/ 扫描 + frontmatter name/description |
| H | Shadow Git 检查点 | 自建 undo vs Shadow Git，后者不动仓库历史 | 改前 git add -N + git commit --no-verify；/rewind 三档 |
| I | 跨会话消息工具（子代理 → 主会话） | 子代理没有提问通道，补齐「信箱」 | 子代理会话侧加 SendInterjection 工具 |
| J | @ 引用 + .cursorrules/.clinerules 兼容 | 用户定位具体规则文件时要做 | @<path> importer + .cursor/rules/*.mdc / .clinerules/*.md 读取 |
| K | 语音输入 | Aider /voice、Codex voice_transcription、Cursor 文档 3 处、Qoder 3 处 | --voice 或输入框 🎤 → winrt ScreenCapturer → SpeechRecognizer/离线 STT → 拼消息 |

## 五、当前版本自画像

- **强项**：独立完整体、桌面 GUI、个人数据闭环、验收闸、钩子改写重跑、无头模式 6 态、microcompact、token 日预算
- **弱项**：无 OS 沙箱、无 apply_patch、无 Plan mode 只读、无子目录 AGENTS.md、Hooks 不可配失败语义、子代理无嵌套/并发控制
- **下一步**：A → B → D（看文档 + 落地）

---

> 自画像：独立完整体、桌面 GUI、个人数据闭环、验收闸、钩子改写、无头 6 态、microcompact、token 预算。短板：沙箱、apply_patch、Plan 只读、子目录规则、Hooks 失败语义、子代理嵌套。下一步：A→B→D（看文档 + 落地）。