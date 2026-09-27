# 安全说明

引路学长是一个**跑在你自己 Windows 桌面上的 agent**:它能读你的文件、改你的文件、
以你的身份执行命令、驱动一个真浏览器。所以这份文档先说清一件事:

> 上面这些能力是**真的**。安全模型的目标不是"把它关进笼子",而是
> **"它每一次伸手都要让你看得见、拦得住"**。

## 边界在哪里(代码里就是这四层)

口径来自 `minicode/permission.py` 与 `minicode/sandbox.py` 的模块说明,改这两处时
两份说明与本文一起改。

1. **敏感路径永远拒绝**(`sandbox.SENSITIVE_DIRS`,一个不少地列在这里):
   `~/.yinluxs`(本应用自己的凭据目录)、`~/.ssh`、`~/.aws`、`~/.gnupg`、
   `~/.config/gcloud`、`~/.azure`、`AppData/Roaming/Microsoft/Credentials`。
   **任何权限档位、任何用户确认都越不过这条线** —— "本轮总是允许"和自定义白名单都不行。
2. **工作区围栏**:工作区内的路径默认放行;工作区外默认要用户逐次确认
   (`allow_outside_workspace` 打开才放行)。`ocr_image` 这类自己 resolve 路径的工具
   也走同一道门,否则"给个绝对路径就能把工作区外的文件读走"。
3. **命令管控**:灾难性命令(`rm -rf /`、`format c:`、fork 炸弹……)在
   `check_disaster_command` 里**硬拒绝,不给确认机会**;沙箱另支持禁网命令开关、
   自定义禁配正则(`deny_patterns`)、执行超时上限。
4. **脚本内容一层**(v0.9.39 起):`py_script` / `bash python x.py` 要跑的那个文件
   先按行扫一遍,把命中的**那几行原文**摆进确认框(碰密钥位置 / 跑出用户目录 /
   有外传出口 / 要联网 / 起子进程);子进程环境里名字像凭据的变量默认摘掉
   (`sandbox.strip_env_secrets`,摘了哪几个会在输出末尾告诉你)。

**工作区里的凭据文件是"确认"而不是"禁止"**(`SECRET` 一级):它可能就是你要改的
`.env`。但只要这一轮接触过外部内容(网页、爬来的文本、MCP 返回),升级门就会对
敏感动作强制确认一次 —— 因为提示注入是真的会发生。

## 四档权限模式

判断顺序在 `permission.PermissionGate.check()` 里,档位是**最后**一步 —— 前面那些
门(硬拒绝、沙箱红线、含密钥文件、外部内容升级门)轮不到档位来免。

| 档位 | 放开什么 | **仍然**拦什么 |
|---|---|---|
| `confirm` 变更前确认(默认) | 查询类(`read_only`)自动放行 | 任何变更类工具都要确认 |
| `accept_edits` 自动编辑 | 工作区内的写/改 + 构建类命令首词(`python`/`pytest`/`pip`/`npm`/`make`/`cargo`/`go`…) | 删除、跨区写、敏感命令;表里**刻意不含** `rm`/`del`/`git push` 这类破坏性动词 |
| `accept_all` 自动放行 | 走到档位判断的变更一律放行(老档位,保留兼容旧 config) | 敏感路径 / 灾难命令 / 沙箱红线 / 工作区内含密钥的文件 / 外部内容升级门 |
| `full` 完全访问 | 比 `accept_all` 再多免一条:沙箱里"可配命令"那层确认也放行 | 敏感路径 + 灾难命令**照旧硬拒绝**,档位再高也越不过 |

档位越高是**越信任你**,不是**越信任外面进来的东西**:工作区内那些
`config.local.json` / `credentials.json` / `.env` / `id_rsa` / `*.pem` 一律是
"不禁止但永远要确认"(`SECRET` 一级)—— 否则一条网页里的指令就能让模型无声读走密钥。
本回合接触过外部内容(网页、爬来的文本、MCP 返回)时,敏感动作还会被升级门
强制确认一次,并且**优先级高于档位**。

## 这套东西**不是**什么

写在这里是为了别把它当成它做不到的事:

- **不是隔离**。子进程仍以你当前用户的权限运行;`allow_network: false` 是按命令名
  和脚本导入名拦的门禁,**不是操作系统防火墙**(`sandbox.KNOWN_LIMITS` 里逐条写着)。
  真需要隔离请上 Windows 沙盒或另一台机器。
- **不防恶意软件**,也不防能物理访问这台机器的人。
- **不承诺安全更新**。这是一个单人维护的校园项目:只对最新版负责,没有 CVE 流程、
  没有受支持版本表。

## 数据流向:什么会离开这台机器

- **会出去的**:发给模型的请求(对话内容、附件里被读进来的文本、工具结果),目的地
  是你在「模型管理」里选的那家;联网工具(网页抓取、天气)按各自的目标地址出去。
- **不出厂的**:知识库检索、馆藏库查书(本地 SQLite 133 万条)、OCR(先用 Windows
  自带的离线识别引擎,系统引擎不行才问视觉模型)、SVG 图表、校园地图。
- **没有埋点**:代码里没有 telemetry / 崩溃上报 / Analytics(`minicode/` 全目录搜不到)。

## 本机存了什么

默认落在 `%LOCALAPPDATA%\引路学长`(没有 `LOCALAPPDATA` 时退回 `~/.yinluxs/app`),
**全部是明文**:

- `sessions/*.jsonl` —— 完整对话与工具结果;
- `schedule.json` / `period-times.json` / `reminders.json` / 心情日记 / 长期记忆 —— 你的个人数据;
- `browser-profile/` —— **教务导入时用的独立浏览器 profile**,登录态就在这里;
- `token-usage.db`、`web-usage.log`、`tray.log`、`watchdog.log` —— 用量与运行日志。

学校公用电脑上请用界面里的「🗂 我的数据」面板和教务导入的「清登录态」按钮
(`schedule_import.clear_login_state`,只删落在本应用数据目录里的那个 profile,
被 `YINLUXS_BROWSER_PROFILE` 指到别处时宁可拒绝也不 rmtree)。

## 密钥的规矩(违反过一次就永远有效)

- **API Key 绝不写进 `config.json`,也绝不写进源码。** 用户自己的 key 存在
  `~/.yinluxs/credentials.json`(`save_config` 自动拆出来,读的时候合并);
  内置校园供应商的 key 存在本地未跟踪的 `config.local.json`。
- `tests/test_config.py::test_builtin_provider_key_is_not_hardcoded` 守着这一条。
- 临时覆盖用环境变量 `YINLUXS_BUILTIN_API_KEY`。取 GitHub token 一律在 Python 里
  `subprocess` 取、内存解析、日志只打长度 —— 别写成 shell 管道。
- **分发包里的内置 Key**:安装包里那份校园模型的 key 由 `packaging/*.spec` 塞进 exe
  内部(`_MEIPASS`),让同学开箱即用。它只对校内那个服务有效 —— 但这仍然是个 key:
  别把安装包传到校外公开渠道,别把它反编译后发到群里。

## 分享安装包之前

跑 `python scripts/build_exe.py` 出包(它会按 `minicode/state_files.py::SHIP_NEVER`
里那 43 条**遍历删除**本机运行时产物)。

**不要手工拷 `dist/` 目录发给别人** —— 那会绕过清理清单,把你自己这台机器上的
课表、心情史、教务登录态一起发出去。`scripts/check_docs.py` 会盯住两件事:
代码里新写进数据目录的每一份文件必须同时被 `.gitignore` 和这份清单盖住;
`build_exe.py` 必须遍历这份清单而不是自己抄一串名字。

## 发现安全问题怎么报

这是一个**代码库公开的校园项目**(见 `pyproject.toml` 的 authors)。

- 一般问题:开 Issue 就行。
- **涉及密钥泄露、能读到别人凭据、能把个人数据带出机器的问题:不要开公开 Issue**,
  直接把复现步骤发给作者,并且**先轮换那把 key** —— 只要它可能进过一次提交历史,
  就按已经泄露处理。
- 请不要在公开 Issue / 群里贴 `config.json`、`credentials.json`、`config.local.json`
  或 `sessions/` 的内容,哪怕"看起来没有 key":里面是同学自己的对话、课表和心情记录。
