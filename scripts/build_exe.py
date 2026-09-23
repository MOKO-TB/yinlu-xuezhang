"""打包 引路学长:PyInstaller 构建 → 清理 → zip 分发包。

用法: python scripts/build_exe.py [--no-zip]
产物: dist/引路学长/引路学长.exe 与 dist/引路学长-<版本>-win64.zip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minicode import __version__  # noqa: E402


def _ensure_collected(dist: Path) -> None:
    """兜住 PyInstaller 6.22 冷构建的收集竞态。

    实测:带 `--clean` 的**首次**构建里,CLI 那个 EXE 已经生成在 build/gcode/ 下,
    但 COLLECT 阶段却报 "Ignoring non-existent resource ... 引路学长-CLI.exe",
    结果 dist/引路学长/ 只有 _internal、两个 exe 都不在(--clean 冷缓存下的顺序问题,
    第二次不带 --clean 就正常)。这里发现缺失就自己补拷,免得包出来是残的。
    """
    work = ROOT / "build" / "gcode"
    for name in ("引路学长.exe", "引路学长-CLI.exe"):
        target = dist / name
        if target.exists():
            continue
        src = work / name
        if src.exists():
            shutil.copy2(src, target)
            print(f"[修复] COLLECT 漏收 {name},已从 build/gcode 补拷")
        else:
            raise AssertionError(f"构建产物缺失: {name}(build/gcode 里也没有)")


LIB_DB_SRC = ROOT / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"


def bundle_library_db(dist: Path) -> None:
    """把离线馆藏库(1.3 GB)拷进便携目录,让安装包/zip 自带图书馆查询能力。

    源库是 WAL 模式,必须用 SQLite 在线备份(直接拷 .db 会丢 -wal 数据、
    复制出来是空表)。构建机没有库时只警告不失败。
    """
    if not LIB_DB_SRC.is_file():
        print(f"!! 未找到馆藏库 {LIB_DB_SRC},跳过内置(包内图书馆查询将不可用)")
        return
    import sqlite3
    dst_dir = dist / "crawled" / "mfindgzyykj.libsp.cn"
    dst = dst_dir / "library.db"
    dst_dir.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size > 0:
        print(f"馆藏库已存在,跳过: {dst}")
        return
    src = sqlite3.connect(f"file:{LIB_DB_SRC}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()
    print(f"已内置馆藏库 {dst} ({dst.stat().st_size/1e9:.2f} GB)")


def stamp_library_db(dist: Path) -> Path | None:
    """给包内馆藏库写完整性凭证(大小/条数/sha256),供安装后与启动时自检。

    每次构建都**重写**:库更新过、或上一次构建中途失败留下半截包时,凭证跟着重算
    才不会“以坏为好”;安装包也会把凭证一起装过去,于是现场一眼就能看出库装全没有。
    """
    import sqlite3

    from minicode import library

    db = dist / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"
    if not db.is_file():
        print("!! 包内没有馆藏库,跳过写凭证(包内图书馆查询将不可用)")
        return None
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        total = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        c.close()
    sp = library.write_stamp(db, total, extra={"app_version": __version__})
    print(f"已写馆藏库凭证 {sp.name}({total} 条,{db.stat().st_size/1e9:.2f} GB)")
    return sp


def write_user_files(dist: Path) -> None:
    """把面向用户的文件放到 exe 旁边(清理冒烟残留 + config 样例 + 使用说明)。

    **必须在编译安装包之前**调用:否则 setup 里不带《使用说明.txt》,
    而分发给同学时最需要的就是里面那两句(要 2 GB 磁盘 / 单文件版不带馆藏库)。
    """
    shutil.rmtree(dist / "sessions", ignore_errors=True)
    (dist / "config.json").unlink(missing_ok=True)
    # 上一次跑起来的程序会在 exe 旁留下能力探测缓存(内含本机模型服务指纹):
    # 那是运行时状态,不该随包发给别人。
    (dist / "model_caps.json").unlink(missing_ok=True)
    # 同类运行时状态一并清掉:token 记账库(含构建机上的用量记录)与
    # 工作区记忆(含本机文件夹路径) —— 分享出去既没用,又像在泄露什么。
    for _name in ("token-usage.db", "token-usage.db-wal", "token-usage.db-shm",
                  "workspaces.json", "workspaces.json.tmp",
                  # v0.9.14 知识库成长闭环的运行时日志(含本机问答/反馈,不该外发)
                  "kb-gaps.jsonl", "kb-candidates.jsonl", "message-feedback.jsonl",
                  # v0.9.18 个人数据与本机缓存:课表属于使用者本人,天气缓存是构建机坐标的
                  "schedule.json", "weather-cache.json", "reminders.json",
                  # v0.9.24 课表覆盖前的备份(同学本人的旧课表,同样不能外发)
                  "schedule.prev.json",
                  "notice-watch.json", "injection-events.log",
                  # v0.9.22 关窗偏好与托盘诊断日志(本机状态,不外发)
                  "close-pref.json", "tray.log",
                  # v0.9.23 首次运行向导的「不再显示」偏好(纯本机状态)
                  "onboarding.json",
                  # v0.9.25 桌面提醒的开关与类别偏好(同样是纯本机状态)
                  "notify-pref.json",
                  # v0.9.26 「今天已弹过哪几条」去重账本(当日本机状态,发给别人等于
                  # 把自己的提醒记录带出去,而且会让新机器的第一条补发被误判成已弹)
                  "pushed-today.json",
                  # v0.9.27 本机作息表(自己学校教务处那张表的抄录,属使用者本人环境状态:
                  # 随包发出去既对别人没用,又会让他一空档直接按你的钟点算)
                  "period-times.json",
                  # v0.9.28 看门狗运行日志(本机弹过哪些提醒、为什么没弹的记录)
                  "watchdog.log",
                  # v0.9.28 对账脚本查出的三处漏网(见 scripts/check_docs.py):
                  # weather.json 是用户自选城市(weather.py 的 CITY_FILE),含位置;
                  # web-usage.log 是局域网问答版的发问记录 + 来源 IP,更不能外发。
                  "weather.json", "web-usage.log",
                  # v0.9.32 情绪日记:全 app 最私密的一份本机记录,随包发出去就是把自己的
                  # 心情史交给别人(连同原子写留下的半截 .tmp)。导出才是显式动作。
                  "mood.json", "mood.json.tmp"):
        (dist / _name).unlink(missing_ok=True)
    # 同上:问答版的会话目录(构建机若真跑过 web_server,这里面就是本机对话)
    shutil.rmtree(dist / "web-sessions", ignore_errors=True)
    # v0.9.30 导入迁移包前的原件备份(构建机若真试过导入,里面是本人文件副本)
    shutil.rmtree(dist / "migrate-backup", ignore_errors=True)
    # v0.9.23:可见浏览器跑过教务导入后,这个 profile 目录里就是**真实的登录态**
    # (cookie / sessionStorage)。随包发给同学 = 把自己的教务账号交出去。
    shutil.rmtree(dist / "browser-profile", ignore_errors=True)
    # v0.9.21:候选审核把人工核实的知识写进 app_path('kb')/reviewed.json;
    # 构建机上若试用过「并入」,那份文件含本机问答,不能随包发给别人。
    (dist / "kb" / "reviewed.json").unlink(missing_ok=True)
    shutil.copy(ROOT / "config.example.json", dist / "config.example.json")
    (dist / "使用说明.txt").write_text(
        "引路学长 —— 校园 AI 助手 / 编程引路智能体(GUI + CLI)\n"
        "====================================================\n\n"
        "【便携版用法】整个文件夹拷到任意目录或 U 盘,双击 引路学长.exe 即可;\n"
        "config.json、sessions/、memory/ 都生成在 exe 同目录,换电脑连文件夹一起搬走。\n\n"
        "1. 引路学长.exe 是图形界面(推荐);引路学长-CLI.exe 是终端版;\n"
        "   引路学长-单文件版.exe 免解压,双击直接打开。\n"
        "2. 工作区:Agent 干活的那个文件夹(它读写文件、存附件/计划/记忆都在这里)。\n"
        "   默认就是本程序所在目录;想换成自己的文件夹,点顶栏的「📁 工作区」\n"
        "   选一个就行。终端版可以用 /workspace <路径> 切换,启动时也可以写\n"
        "   --workspace 指定。换工作区会开一个新会话,原来的会话留档不会丢。\n"
        "3. 模型:开箱即用内置学校服务(校园网内),不填 API Key 就能用。\n"
        "   也可以用更合适的模型:点顶栏的模型标识打开「模型管理」——\n"
        "   左边是供应商(一个供应商 = 一个接口地址 + 一个密钥),右边是它下面的\n"
        "   模型清单:可以手填,也可以点「从服务获取」让服务端自己报有哪些模型;\n"
        "   每个模型能单独启停、单独「测试」(当场验证密钥/地址/模型名对不对)。\n"
        "   你的密钥只存在本机(~/.yinluxs/credentials.json),不写进 config.json,\n"
        "   换电脑分享这个文件夹也不会把密钥带出去。\n"
        "4. 当前对话想留档:点顶栏「⬇ 导出」存成 Markdown(方便贴进文档或发同学);\n"
        "   侧栏搜索框可以按关键词翻历史会话,搜到的会给出上下文片段。\n"
        "   快捷键:Ctrl+K 打开模型管理,Ctrl+N 新建任务。\n"
        "5. 校园问答小工具:首页有「倒计时」chips(放假/四六级等,日期数据在\n"
        "   kb/school-calendar.json,拿到教务处校历后可直接编辑补充);\n"
        "   问绩点/加权平均,把成绩直接贴给它就算;每条回复右侧有 👍/👎,\n"
        "   点了会记进本机反馈日志,帮助学长把回答越改越准。\n"
        "6. 首页还有「今天要不要带伞」天气 chip(数据源 Open-Meteo,免注册免 Key,\n"
        "   出校园网/断网时不显示,不碍事);把课表截图或文字发给学长,它会问你\n"
        "   「开学第一周周一是几号」后存成个人课表(存在本机 schedule.json,不上传)——\n"
        "   之后问「今天/明天有什么课」不用再发图,启动时还会主动提示第一节课。\n"
        "7. 图书馆搜书用的是包内的离线馆藏库 crawled/mfindgzyykj.libsp.cn/library.db\n"
        "   (约 1.31 GB / 133 万条书目)。\n"
        "   - 需要至少 2 GB 空闲磁盘;库没拷全时界面会当场提示「馆藏数据不完整」;\n"
        "   - 单文件版(引路学长-单文件版.exe)**不带**这个馆藏库,单独用无法搜书;\n"
        "     要能搜书请用安装包,或把整个便携文件夹一起拷过去。\n"
        "   - 自检:引路学长-CLI.exe 里输入 /library 看库状态。\n"
        "8. 想接 MCP 服务器:编辑 config.json 的 mcp_servers 数组\n"
        "   (格式见 config.example.json,支持 npx/uvx/python 启动的任意 MCP 服务器);\n"
        "   只有网址没有启动命令的服务器也能连:写 \"transport\": \"http\"(新式)\n"
        "   或 \"sse\"(老式 HTTP+SSE)加 \"url\";需要鉴权的写\n"
        "   \"headers\": {\"Authorization\": \"${MY_MCP_TOKEN}\"} —— 密钥留在环境变量里,\n"
        "   不落进 config.json,报错信息里也不会把它打出来。\n"
        "9. 问网页内容时它可以用真实浏览器看页面(靠 JS 渲染的站点、自己写的本地\n"
        "   页面都能看,还能截图给你)。默认自己起一个干净的后台实例,不会动你\n"
        "   正在用的浏览器,也不带你的登录态;只有要看必须登录才看得到的页面时,\n"
        "   它会先问你才能用你自己的浏览器。终端里输 /browser 可看当前状态。\n"
        "10. 会话留档在 sessions/;技能放 skills/(SKILL.md 格式);知识库放 kb/;\n"
        "   长期记忆在 memory/MEMORY.md。\n"
        "   注:选了别处的工作区后,上面这些(以及附件、计划)都跟随工作区走,\n"
        "   只有 config.json 与会话留档 sessions/ 留在程序目录。\n"
        "11. 点窗口右上角 X 会先问一句:「最小化到托盘」= 窗口收进右下角托盘,\n"
        "   正在跑的任务继续在后台干,双击托盘图标叫回窗口;「退出应用」= 真的退出。\n"
        "   勾了「不再询问」以后想改回来:右键托盘图标选「点 X 时先问我」。\n"
        "12. 安装版如果被装进只读目录(安装时用了管理员权限 → Program Files),\n"
        "   程序数据会自动改存到 %LOCALAPPDATA%\\引路学长,配好的模型 Key 一样不会丢;\n"
        "   数据到底存在哪,看界面:顶栏「模型管理」下方会写明数据目录的真实路径,\n"
        "   第一次打开也有引导页说明;那个目录写不进去时会当场用黄字提醒你,\n"
        "   不会让你以为存好了其实没存。\n"
        "13. 课表可以一键从教务系统导入:点「📅 课表」→「从教务导入」,学长会弹出一个\n"
        "   真浏览器窗口停在登录页 —— **账号、密码、验证码都由你自己在那个窗口里输入**,\n"
        "   学长不看也不存这些(登录态只留在那个浏览器实例里,不进对话、不落盘);\n"
        "   你登录好、打开「整学期」课表页后点「② 读取课表」,学长把解析结果列成预览,\n"
        "   你确认无误(顺便填一下「开学第一周周一是几号」—— 教务页面上没有这个信息)\n"
        "   再点「③ 确认导入」才写进本机课表;不想留登录态了点「清除登录状态」。\n"
        "   导入前学长会把新课表和你本机那份**逐条比对**(哪些课新增、哪些取消、\n"
        "   哪门课换了教室或调了节次)列给你看,不再闷头整表覆盖;真导入后发现不对,\n"
        "   课表面板上有「↩ 换回上一版」可以退回去。\n"
        "14. 复杂任务学长会先列计划(工作区 plans/plan.md)。顶栏「🗂 计划」实时显示\n"
        "   进度、现在能动的是哪几步、哪几步被上游失败卡住了;点「↩ 派发到输入框」\n"
        "   把「接着做哪几步」填进输入框,你过一眼再发 —— 面板只看不改,\n"
        "   打勾仍由学长在做完每一步时自己标。\n"
        "15. 桌面提醒:今日简报、快上课了(课前 20 分钟)、今天的课、临期/逾期的待办、\n"
        "   校历倒计时、官网新通知、有新版本 —— 这些以前只在窗口顶部那条提示栏里,\n"
        "   窗口一最小化就等于没提醒;现在同时弹一条 Windows 桌面通知(右下角,发送方\n"
        "   显示「引路学长」+ 立方体图标;这个身份在弹第一条通知之前就自动登记在\n"
        "   当前用户下(不要管理员),收进托盘里也照样弹。开关在托盘图标右键菜单:\n"
        "   「桌面提醒」(打勾 = 开)、「测试一条桌面通知」;也可以直接跟学长说\n"
        "   「别弹通知」或「看不到通知」。要是通知顶上还顶着一串英文、或者没有图标,\n"
        "   说一声让学长重新登记后再弹一条(Windows 会记住它第一次见到的那个身份)。\n"
        "   窗口正开着且在前面时不会重复弹(那行字你已经看见了),同一条内容一分钟内\n"
        "   也只响一次。要是测试通知回了「已发出」你却没看到,基本是 Windows 自己挡了:\n"
        "   设置 → 系统 → 通知 里允许「引路学长」通知,并确认专注助理没在静音。\n"
        "16. 开机自启:托盘右键菜单里有「开机自动启动」(打勾 = 开),也可以直接跟学长说\n"
        "   「开机自动启动学长」或「别开机就启动」。做法是在你自己「启动」文件夹里放一个\n"
        "   快捷方式 —— 不写注册表、不要管理员权限,你能自己看见、也能自己删掉;开了之后\n"
        "   开机即启动并直接收进托盘(双击托盘图标叫出窗口)。\n"
        "   配合「补发」才有意义:早上没开机,下午打开学长也看得到今天的今日简报;要是\n"
        "   某节课刚开课后一小时内你才开机,学长会补一条「错过的提醒:高等数学 08:00\n"
        "   已开课(过了约 50 分钟)」—— 只补最近的一节,不会连着刷屏,课表里没写钟点的\n"
        "   课也不瞎猜。「今天弹过哪几条」记在 pushed-today.json 里按天滚动,所以同一节课\n"
        "   不会因为你重启了一次就再弹一遍。\n"
        "17. 想知道「我这周什么时候有空」:点顶栏「📅 课表」→「本周」,每天的格子里除了\n"
        "   课程还会列出**空档时段**,并把当天要交的待办(⚑)和校历安排叠在同一列上 ——\n"
        "   学长是按你登记的课表算出来的,不是让模型心算;点某一天能展开看那天空档的\n"
        "   起止节次、这天要交什么。直接对话问「我这周什么时候有空」也是同一份算法。\n"
        "   空档有两种说法,取决于你填没填**作息表**:没填时学长只说「第 3-4 节没课」,\n"
        "   绝不编钟点(课表里没有下课时间);点格子下方「填作息表」,把教务处那张表的\n"
        "   每节课起止时刻录一次(存在本机 period-times.json,不上传),之后才会说\n"
        "   「10:00~11:40 有空」,也才好按钟点安排学习。填了还能「不填了」退回按节次。\n"
        "   两条硬口径:那天不在学期内(开学前/假期)不会说成「整天空着」;某节课没写\n"
        "   节次的那天会直说「空档算不准」,请你把节次补进课表,而不是给个好看的假答案。\n"
        "18. 提醒到底发没发,现在有据可查:每一次推送的决定(推了 / 被拦下 / 没推成)都记在\n"
        "   数据目录的 watchdog.log 里,**只记是哪一类提醒、去重用的那个键、结论**,绝不记通知\n"
        "   正文。觉得「提醒没发」或「弹了好几遍」,直接这么跟学长说就行,它会读日志回话\n"
        "   (desktop_notify 的 log 动作)。这一轮还修了两件实际问题:①校历倒计时、临期待办、\n"
        "   今天的课以前**每轮都重判一遍**,所以你当天重启一次学长它们就又弹一遍,现在和\n"
        "   课前提醒一样按天去重;②一条提醒**两个渠道都没送到**时不再记成「今天已推过」,\n"
        "   下一轮会再试,不会因为一次失败就整天哑掉。\n"
        "19. 待办也能「提前 20 分钟催」:记提醒时把**日期和钟点**一起说给学长(「10 月 8 日\n"
        "   17:00 前交奖学金材料」),它就会像课前那样在到点前 20 分钟催一次;那会儿电脑\n"
        "   没开也不怕,开机第一条会补一句「已过点」。**只说了日期**的仍按天催 —— 学长不会\n"
        "   替你补一个「一般是下午五点」;光说「3 点」这种没讲上午下午的,它宁可不要这个钟点,\n"
        "   因为猜错就是差 12 小时,比不催更误事。\n"
        "20. 换电脑不用重装机:顶栏「💾 数据」打开就是**你的数据都在哪个目录、都有哪些文件**,\n"
        "   按「我的内容 / 设置与偏好 / 统计 / 缓存 / 日志 / 永不导出」六类列开,每一行都写\n"
        "   明它**进不进迁移包**。点「导出迁移包」选个位置,就得到一个 zip(课表、待办、作息表、\n"
        "   记忆、聊天记录、配置都在里面);到新电脑上装好学长,同一面板点「导入迁移包」,\n"
        "   先给你看**每一项会怎么写**(新增 / 更新 / 本机更新所以保留本机的 / 跳过),你点头\n"
        "   才落盘。两条硬规矩:**①包里永远不含 API Key 和教务登录态**(所以到新机器的第一步\n"
        "   是去「模型管理」重填一次 Key),**②导入只新增和更新,绝不删本机任何东西**,比包新\n"
        "   的那份保留本机的,要被替换的先备份进 migrate-backup/。日志、缓存、token 用量统计\n"
        "   是「跟着这台机器走」的东西,不进包;包太大时在导出前取消「包含聊天记录」。\n"
        "21. 论文、题库、笔记、简历这四件事,学长只做**它该做的那一半**(四份新技能):\n"
        "   论文它帮你收窄选题、列提纲(每节写着「要回答什么、要什么材料、大概多少字」)、\n"
        "   把**你给它的**参考文献排成 GB/T 7714、并把你写的段落和原文比一遍告诉你哪几段\n"
        "   太像、怎么改 —— 但它**不替你写正文**,也**绝不替你编文献**:没给到的页码、\n"
        "   卷期、DOI 它会印「【待补:页码】」让你自己去查,因为被查的人是你。\n"
        "   题库只整理**你手上那份**(你自己抄的、老师发的、你的错题)→ Anki / GIFT / Aiken\n"
        "   三种格式;「帮我把某某大学十年真题扒下来」它直接说不做:那是学校或老师的材料,\n"
        "   而且抓来的脏题会毁掉整本题库。导入前它会先校验一遍,专挡**答案错位**那种坑\n"
        "   (错位不会报错,只会让你背下一个错误答案)。课件笔记先探「这份文件里到底有没有\n"
        "   文字」:扫描版 PDF 和整页截图的 PPT 抽不到字,它会**直说做不了**并给你三条路,\n"
        "   而不是照着标题编一章;能做的部分保留页码,它补的话标「(补充,非课件原文)」。\n"
        "   简历改成**按岗位定向**:投技术岗和投运营岗是两份文件,它只会调顺序、换重点、\n"
        "   换说法,**绝不会给你加一条没有的经历**。\n",
        encoding="utf-8",
    )


def main() -> None:
    skip_build = "--skip-build" in sys.argv
    dist = ROOT / "dist" / "引路学长"
    if not skip_build:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
             str(ROOT / "packaging" / "gcode.spec")],
            cwd=ROOT, check=True,
        )
    else:
        print("跳过 PyInstaller(--skip-build),只用现有 dist 产物重新出包")
    _ensure_collected(dist)
    assert (dist / "引路学长.exe").exists(), "构建产物缺失(GUI 版)"
    assert (dist / "引路学长-CLI.exe").exists(), "构建产物缺失(CLI 版)"
    onefile = ROOT / "dist" / "引路学长-单文件版.exe"
    assert onefile.exists(), "构建产物缺失(单文件版)"

    # 内置离线馆藏库:必须在打安装包/zip 之前,否则分发出去不带图书馆数据
    bundle_library_db(dist)
    stamp_library_db(dist)
    # 用户可见文件也要在编译安装包之前写好(安装包递归收 dist/引路学长/*)
    write_user_files(dist)
    lib_db = dist / "crawled" / "mfindgzyykj.libsp.cn" / "library.db"
    lib_db_bytes = lib_db.stat().st_size if lib_db.is_file() else 0

    # Inno Setup 编译安装包(已安装于当前用户目录或 Program Files)
    # /DLibDbBytes 传给 .iss 的 [Code]:装完后校验 1.3 GB 的馆藏库是否完整
    iscc = next((p for p in [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe"] if os.path.exists(p)), None)
    if iscc:
        subprocess.run([iscc, f"/DAppVersion={__version__}",
                        f"/DLibDbBytes={lib_db_bytes}",
                        str(ROOT / "packaging" / "gcode.iss")],
                       check=True, cwd=str(ROOT))
        setup_exe = ROOT / "dist" / f"引路学长-{__version__}-setup.exe"
        assert setup_exe.exists(), "安装包构建失败"
        print(f"安装包: {setup_exe}")
    else:
        print("未找到 Inno Setup,跳过安装包编译(pip install -r requirements-dev 后"
              "运行 scripts/setup_inno.py 可补装)")

    if "--no-zip" in sys.argv:
        print(f"完成(未打包 zip): {dist}")
        return

    zip_base = str(ROOT / "dist" / f"引路学长-{__version__}-win64")
    shutil.make_archive(zip_base, "zip", root_dir=ROOT / "dist", base_dir="引路学长")
    print(f"完成: {zip_base}.zip")
    print(f"    {dist / '引路学长.exe'}")
    print(f"    {onefile}(免解压单文件版,双击直接打开)")


if __name__ == "__main__":
    main()
