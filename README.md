# 引路学长

一个运行在 Windows 桌面的编程引路智能体——**图形界面**里交任务,它自己读代码、改文件、跑命令,直到把事情做完;像学长带学弟一样,动手前讲思路,关键处给理由。附赠同核引擎的终端版。

架构对标 [星瑶 Y-Code](https://gitee.com/feng-chenhao/xingyao-y-code) 的核心骨架:**Agent 主循环 + 工具注册表 + 权限门 + 技能系统**,Python 实现,GUI 与 CLI 共享同一套引擎(前端为免构建单页应用 + pywebview)。

## 功能

- **桌面 GUI**:暖橙手账风,深色/浅色一键切换;会话列表、Markdown 流式对话、工具调用卡片、思考流
- **多供应商 API Key**:内置智谱 GLM / DeepSeek 模板,界面里「🔑 密钥」增删改查供应商、一键切换;也可直接编辑 `config.json`(providers 数组,兼容旧扁平格式)
- **本地知识库检索**:`kb/` 目录放 JSON 资料库(校园通知、经验库等,自带 credibility 可信度分级与来源网址),`search_knowledge` 工具按 中文 2-gram 打分检索(标题×3+标签×2+正文×1),回答引用时输出来源网址供用户核实,非 A 级数据强制标注"未经核实"
- **SKILL.md 技能系统**:`skills/` 目录放技能(带 name/description 的 SKILL.md),自动注入系统提示词,模型用 `use_skill` 工具按需读取全文;内置 git-commit / python-testing / code-review 三个示例
- **权限确认**:写文件(带预览)、编辑(带 diff)、执行命令先弹窗确认,可选"本轮总是允许"或切"自动放行"
- **安全兜底**:`rm -rf /`、`format c:`、fork 炸弹等灾难性命令硬拒绝;文件工具拒绝越出工作区
- **`/undo` 回滚**(CLI):写前快照,可连续撤销
- **上下文自动压缩**:token 估算接近窗口上限时,把早期对话折叠成摘要(按完整轮切分)
- **会话持久化**:每轮写入 `sessions/*.jsonl`;GUI 侧栏一键恢复,CLI `/resume`
- **9 个内置工具**:`read_file` / `write_file` / `edit_file` / `bash` / `glob` / `grep` / `use_skill` / `search_knowledge` / `web_crawl`
- **网页抓取**:`web_crawl` 基于 crawl4ai 把网页/整站抓成 markdown(同域 BFS 跟链、限页限速),可选 to_kb 一键写入本地知识库 `kb/crawl-<域名>.json`,由 search_knowledge 带来源网址检索
- **OpenAI 兼容多模型**:DeepSeek / Kimi / Qwen / 智谱通吃;本地服务(127.0.0.1)自动绕过系统代理

## 快速开始(源码运行)

```bash
cd mini-code
py -3.12 -m venv .venv                     # Python 3.10+
.venv\Scripts\activate
pip install -r requirements.txt
crawl4ai-setup                            # web_crawl 首次使用前执行,下载浏览器内核

copy config.example.json config.json       # 填入 api_key(或在 GUI 里点「🔑 密钥」)

python gui_main.py                         # 图形界面
python main.py                             # 终端 REPL
```

### 常见模型的 base_url

| 供应商 | base_url | model 示例 |
|---|---|---|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.6` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi | `https://api.moonshot.cn/v1` | `kimi-k2-0905-preview` |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3-coder-plus` |

## 用法

```bash
python gui_main.py                  # 图形界面
python main.py                      # 交互式 REPL
python main.py "修复 pytest 报错"    # 一次性执行
echo "列出 TODO" | python main.py   # 管道输入
```

CLI 命令:`/help` `/clear` `/model`(供应商列表)`/history` `/resume [<n>]` `/undo` `/compact` `/skills` `/kb`(知识库统计)`/exit`;`Ctrl+C` 中断当前回合。

## 技能(SKILL.md)

把技能放进程序目录的 `skills/`(每个技能一个文件夹,内含 `SKILL.md`):

```markdown
---
name: git-commit
description: 生成符合规范的 git 提交信息
---
(给模型的指导正文……)
```

系统提示词会列出可用技能,模型接到相关任务时自动用 `use_skill` 读取全文后再行动。CLI 用 `/skills` 查看已装技能。

## 配置项(config.json)

| 字段 | 说明 | 默认 |
|---|---|---|
| `providers` | 供应商数组:`{id, name, base_url, api_key, model}` | 智谱 + DeepSeek 模板 |
| `active_provider` | 当前使用的供应商 id | 第一个 |
| `permission_mode` | `confirm` 变更逐次确认;`accept_all` 自动放行 | `confirm` |
| `shell` | `auto` 自动选 Git Bash→cmd;可指定 `bash` / `cmd` / `powershell` | `auto` |
| `max_output_chars` | 工具输出回填给模型前的截断长度 | `20000` |
| `request_timeout_seconds` | 单次 LLM 请求超时 | `300` |
| `context_window` | 模型上下文窗口(token),用于压缩触发判断 | `131072` |
| `compact_at_ratio` | 估算 token 达窗口该比例时自动压缩 | `0.7` |

## 项目结构

```
minicode/
├── agent.py        # Agent 主循环(回合内 模型↔工具 迭代)+ 快照回滚 + 压缩触发
├── app_setup.py    # CLI/GUI 共享装配:system prompt(含技能注入)+ Agent 构建
├── llm.py          # OpenAI 兼容流式调用 + 工具调用增量聚合 + 重试 + 本地代理绕行
├── skills.py       # SKILL.md 技能加载 + use_skill 工具处理器
├── kb.py           # 本地知识库:异构 JSON 加载 + 2-gram 检索 + 来源网址输出
├── compaction.py   # token 估算 + 按完整轮切分的上下文压缩
├── permission.py   # 权限门 + 灾难命令黑名单
├── session.py      # 消息历史 + JSONL 留档 + 会话恢复
├── paths.py        # 源码/打包双形态路径解析
├── ui.py           # 终端 UI:rich 流式 Markdown / 工具卡片 / 确认
├── gui_ui.py       # GUI 适配器:引擎事件 → webview 推送,停止=中断
├── gui_bridge.py   # pywebview js_api(门面对象)+ 供应商/密钥管理
├── config.py       # 多供应商配置:解析/迁移/保存
└── tools/          # base(Tool 定义与路径安全)+ fs / shell / search / crawl / registry
main.py             # CLI 入口与 REPL
gui_main.py         # GUI 入口(pywebview 窗口)
frontend/           # 免构建单页前端(暖橙手账风,深浅双色)
skills/             # 内置技能(git-commit / python-testing / code-review)
kb/                 # 本地知识库 JSON(notices / knowledge,带来源网址与可信度)
packaging/gcode.spec  # PyInstaller 双 exe 配置
scripts/            # 打包与端到端验证脚本
```

## 测试

```bash
python -m pytest                        # 93 个单元测试,不依赖网络
python scripts/e2e_fake_server.py       # CLI 端到端冒烟(假服务器,不花 token)
python scripts/e2e_frozen.py            # 打包后验证:CLI 全链路 + GUI 启动
```

## 打包发布

```bash
pip install -r requirements-dev.txt
python scripts/build_exe.py        # 构建 dist/引路学长/ 并生成 引路学长-<版本>-win64.zip
python scripts/e2e_frozen.py       # 对 exe 做端到端验证
```

产物为 onedir 绿色便携包,双 exe:
- **引路学长.exe** —— 图形界面版(无控制台窗口)
- **引路学长-CLI.exe** —— 终端版

`config.json` 在 exe 旁自动生成;图标、system_prompt、前端页面、内置技能已打包。

## 路线图(下一阶段)

- [ ] 子代理(`task` 工具):explore / implement / test 分工,并行派发
- [ ] 计划模式:复杂任务先建计划再按依赖图执行
- [ ] MCP 客户端
- [ ] 长期记忆(用户偏好/项目约定)

## License

[MIT](LICENSE)
