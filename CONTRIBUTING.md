# 贡献指南

先说这个项目最在意的一件事:**界面和文档上写的每一句话,都得有一条真的代码或
一次真的运行撑着。** 项目里的原话是「要真正能使用的不能只用于装饰」。下面这些
规矩都是从踩过的问题里长出来的,不是风格偏好。

## 环境

搭环境的命令在 `README.md` 的「快速开始(源码运行)」一节,这里不重抄一份
(重抄的那份一定会和 README 分家)。跑起来是两条:

```bash
python gui_main.py      # 图形界面(pywebview + WebView2)
python main.py          # 终端 REPL —— 与 GUI 同一套引擎,UI 只是两个适配器
```

Windows 是这个项目唯一的运行平台(托盘、桌面通知、教务导入都按 Windows 写)。

## 改完之后要跑的四件事

```bash
python -m pytest -q                                  # ① 全量测试
python scripts/check_docs.py --collect               # ② 文档/数据边界对账(慢十几秒)
python scripts/check_frontend_js.py                  # ③ 前端内联 JS 语法(需要 UTF-8 控制台)
python scripts/gen_changelog.py                      # ④ 重新生成 CHANGELOG.md
```

- **① 不要带 `PYTHONIOENCODING=utf-8` 跑全量** —— 那会让 `test_kb_credibility` 的
  subprocess 采集假红。反过来,**③ 需要**这个环境变量,否则 GBK 控制台读不动中文。
  这种坑都写在脚本自己的 docstring 里,别只记在脑子里。
- ② 是七道闸(名字就是它打印出来的那七行):版本号一致、个人数据三处同步、
  不外发清单自洽、HANDOFF 长度、pyproject 单一事实源,加 `--collect` 才跑的两项
  —— 测试基线条数、元数据真算得出来(让 setuptools 真生成一遍 METADATA)。
  **它红了通常是文档漂了,不是代码坏了** —— 先看报的哪一句。
- ④ 是因为 `CHANGELOG.md` 由 git log 生成,`tests/test_changelog.py` 会核对它有没有漂。

`tests/` 现在有一千多项,全量跑一次约 5 分钟。**当前基线数只写在 `HANDOFF.md` §5**
(那个数由对账闸跟 `--collect-only` 实测比),别抄进你自己的文档或提交信息里。

## 真点击的回归(e2e)

源码文本对账 + 真浏览器点击 + 派生几何量,是这个仓库的三层钉法。前端那一层:

```bash
python scripts/e2e_ui_v0939_p3.py     # 每个版本一批,文件名就是它验的那一版
```

它们共用 `scripts/e2e_launch.py` 起浏览器。**这台机器上 Playwright 只有完整版
chromium、没有 `chromium_headless_shell`,所以那个 helper 找不到可执行的浏览器时
会直接抛错而不是"跳过"** —— 静默跳过的 e2e 会让人以为跑过了:p3 那轮就有 5 个
老 e2e 脚本悄悄没跑过。

**改界面一定要动 e2e。** 只改源码文本的断言钉不住"点开真的看得见"。

## 这里没有第二处要手抄的真相

要加东西之前先确认它归哪一份管:

| 东西 | 唯一真相 | 谁来盯 |
|---|---|---|
| 版本号 | `minicode/__init__.py::__version__` | `check_docs.py`(pyproject 必须 `dynamic`) |
| 依赖 | `requirements.txt` | 同上 |
| 绝不能随包外发的本机文件 | `minicode/state_files.py::SHIP_NEVER` | `check_docs.py`(还要 `.gitignore` 盖住) |
| 工具调用的"一句话说法"与"成没成" | `minicode/toolsum.py` | `tests/test_tool_outcome.py` 扫全仓库不许出现第二份字面量 |
| 打包后 exe 必须还有的内部符号 | `scripts/check_frozen_version.py::TARGETS` | `tests/test_frozen_targets.py`(针本身)+ 构建后跑脚本 |
| 包内《使用说明.txt》 | `scripts/build_exe.py::write_user_files` | 见下一节 |
| 里程碑流水账 | `HANDOFF.md` §2 + `docs/handoff/milestones.md` | `check_docs.py` 盯行数与归档完整性 |

**同一件事在两个地方各写一份,就是给自己埋 bug。** v0.9.39 Z1 那次收口的就是
一个具体例子:成没成的判定被抄了三份(两份 `startswith` 元组、一个 `[:300]`),
差的正是截断长度 —— 表现是"聊天里说这次失败了,监控栏那行却显示成功"。

## 提交信息

标题里**点版本号**,一行说清这一轮做了什么、按谁的口径做:

```
v0.9.40 p4: 侧栏搜索支持按内容片段跳转(用户点名,只本地提交)
```

`CHANGELOG.md` 就是从这些标题生成的:标题里没版本号的提交(纯文档、跑测、重出包)
视为过程性提交,不进变更日志。别在标题里写"按我上次说的"这种只有你自己看得懂的话。

注释和 docstring 默认不写"这段代码做什么"(函数名已经说了),要写**为什么**:
一个隐藏约束、一个反直觉的取舍、一个不能顺手改掉的边界。这个仓库里大量注释是
"为什么长这样、推翻之前先读哪一节",这是它能被一个人维护住的原因。

## 界面改动的额外要求

- 新界面/新入口**必须进包内《使用说明》**(`write_user_files` 里那条编号列表)。
  用户拿到的是免安装包,看不到 README;《使用说明》没写的功能等于没做。
- 面板上每一行数据都要能追到一次真调用或一份真本机数据。追不到的时候**降级口径**
  并写测试去扫输出:比如上下文压缩后早期动作只剩摘要,那一栏就写「本次会话(可见)」;
  耗时没在调用那一刻记下来,重启后旧会话就一行时刻也不显示 —— 没记过的东西不假装显示。
- 不写没有来源的数字(步数、分钟数、走一趟要多久)。手绘校园地图没有比例尺,
  所以走堂预警只说「中间空 0 节」,**不说**「走一趟要几分钟」。

## 分享与发布

- **出包只走 `python scripts/build_exe.py`** —— 它会按 `SHIP_NEVER` 遍历删除运行时
  产物。**手工拷 `dist/` 目录发给别人 = 绕过清理清单**,会把你自己机器上的课表、
  心情记录、教务登录态一起发出去。
- 打包前先关掉正在运行的实例(exe 占着文件,PyInstaller 的清理会失败或留下混着
  新旧的产物)。
- 推远端、发 Release 是维护者的动作。注意**不带参数的 `git push` 推的是这个克隆的
  `origin`**,以 `git remote -v` 为准(这份 `yinlu-agent` 的 origin 是维护机的本地
  路径,不是公网仓库)。想推公网前先看清 `branch.main.remote` 指向谁。
- 密钥的规矩见 `SECURITY.md`,那一条没有商量余地。
