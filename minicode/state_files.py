"""**「哪些本机文件绝不能随安装包发出去」的唯一清单**(v0.9.39 R 批)。

为什么要有这份文件:这一串名字原先是 `scripts/build_exe.py` 里一个四十多行的
元组字面量,v0.9.18~v0.9.27 每轮靠人肉往里加,而 `scripts/check_docs.py` 又是拿
"这个名字在 build_exe.py 文本里出现过没有"来判的 —— 三处各有一份事实,漏一处就是
把同学自己的课表/心情史/教务登录态打进分发包里。现在:

- **这一份是真相**:`build_exe.py` 遍历它删,测试遍历它核。
- **两处只做校验**:`.gitignore` 必须盖住每一个名字(否则开发态就提交进仓库了);
  代码里新写进数据目录的每一份文件(`paths.py` 那套取路径的调用)若没进这份清单,
  对账闸当场红(见 check_docs)。

和 `minicode/mydata.py::TABLE` 的分工(两份清单问的是两个问题,别合并):
`TABLE` 管"**用户自己**搬家时要不要带走"(迁移包,用户点头才走);
这里管"**给别人装机**时要不要留"(分发包,默认一律不留)。
所以 `schedule.json` 在两处都有,但结论相反 —— 一个是"你想带就带",一个是"绝不在包里"。

删的动作只碰**目标目录里**的东西:清单名字全部是数据目录-relative,不做递归匹配、
不碰工作区,免得这个函数变成一个通用删除器。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

FILE = "file"
DIR = "dir"


@dataclass(frozen=True)
class StateFile:
    """一份不随包外发的本机运行时产物。

    `name` 用正斜杠写(相对数据目录,可以有层级如 `kb/reviewed.json`);
    `kind` 决定删它是 `unlink` 还是 `rmtree` —— 写错会让打包时抛异常而不是静默漏删,
    这是有意的:漏删 = 泄露,宁可构建红。
    `why` 不是注释是**内容**:它进不了界面,但下一轮加条目的人得看懂这条为什么在这。
    """

    name: str
    kind: str
    why: str


# 顺序 = build_exe 里原先的写法顺序(按"哪一轮加进来的"排的),留着方便对账。
#
# **这里没有 `config.local.json`** —— 它含内置模型 Key,看着最该删,但它是构建机
# 故意放进包里(或由 spec 注入 exe 内部)让同学开箱即用的那份,归 `check_docs.py`
# 的 `SHIP_INTENTIONALLY` 管;混进这份"删掉"清单会把开箱即用删没。
SHIP_NEVER: tuple[StateFile, ...] = (
    StateFile("sessions", DIR, "会话留档:构建机上跑过的每一句对话都在这里"),
    StateFile("checkpoints", DIR, "改动前像(/undo 与 /rewind 的账本):里面是别人的文件改之前的原文,绝不外发"),
    StateFile("shadow-git", DIR, "影子 git 库(H 的整树检查点):里面是**你工作区每一份文件的原文**,绝不外发、绝不进安装包"),
    StateFile("extensions", DIR, "侧边栏三方扩展(v0.9.159):每个目录是一份别人写的页面代码,装没装、装了谁的全在这里"),
    StateFile("config.json", FILE, "用户配置:构建机这份含模型 Key,由 spec 另行注入内置 Key"),
    StateFile("model_caps.json", FILE, "能力探测缓存:里面是本机模型服务的指纹"),
    StateFile("token-usage.db", FILE, "token 用量库:装着构建机的用量记录"),
    StateFile("token-usage.db-wal", FILE, "SQLite 日志伴生文件,同上"),
    StateFile("token-usage.db-shm", FILE, "SQLite 共享内存伴生文件,同上"),
    StateFile("workspaces.json", FILE, "工作区记忆:里面是本机绝对路径"),
    StateFile("workspaces.json.tmp", FILE, "原子写的半截文件"),
    StateFile("kb-gaps.jsonl", FILE, "知识库成长日志:本机问过却没答上的问题"),
    StateFile("kb-candidates.jsonl", FILE, "候选知识:从本机问答里长出来的"),
    StateFile("message-feedback.jsonl", FILE, "回答的 👍/👎 反馈:含对话摘要"),
    StateFile("kb-hits.jsonl", FILE,
              "知识库检索命中账(问句 + 条目 id):评价绑条目用,含本机提问记录"),
    StateFile("perms-trust.json", FILE,
              "本机的项目核对记录:工作区绝对路径 + 内容指纹,记两件事 —— "
              "permissions.json 白名单批过没有、AGENTS.md 采纳过没有(v0.9.127)"),
    StateFile("schedule.json", FILE, "我的课表:属使用者本人"),
    StateFile("schedule.prev.json", FILE, "换回上一版用的课表备份,同样是本人课表"),
    StateFile("scores.json", FILE, "成绩单存档:带学分与分数的正式成绩,比课表更私密"),
    StateFile("skill-usage.jsonl", FILE, "技能使用账:每次调技能记一条,暴露使用偏好"),
    StateFile("period-times.json", FILE, "本机作息表:发给别人他会按你的钟点算空档"),
    StateFile("reminders.json", FILE, "待办与截止日期:「提醒我 X 号交材料」记在这里"),
    StateFile("notice-watch.json", FILE, "官网通知看门狗:含已通知过的标题"),
    StateFile("injection-events.log", FILE, "上下文注入日志:防注入排查现场,含本机内容"),
    StateFile("close-pref.json", FILE, "关窗时怎么办:最小化到托盘还是直接退出"),
    StateFile("AGENTS.md", FILE,
              "用户级工程规矩:主人自己写给学长看的习惯,发出去的包不该替别人定规矩"),
    StateFile("tray.log", FILE, "托盘与通知的诊断日志:弹不出来时看的就是它"),
    # v0.9.96:入口脚本 `gui_main.py` 写的界面警告日志(原生右键菜单开没开、
    # WebView2 就绪超时)。它一直在数据目录里躺着,而这份清单以前只扫 `minicode/`,
    # 根目录那几个入口不在扫描集里 —— 于是它既没进这里、也没进「我的数据」,
    # 打包时没人删。现在扫描集扩到入口脚本,这条由闸逼出来,不是顺手加的。
    StateFile("gui_warnings.log", FILE,
              "界面警告日志:装好的机器上界面行为不对时看的就是它,含本机路径"),
    StateFile("monitor-pref.json", FILE, "任务监控栏的开合与折叠偏好"),
    StateFile("pricing-override.json", FILE,
              "费用估算的改价(只存与默认价不同的行):是本机对「大概花多少钱」的口径"),
    StateFile("jobs-pref.json", FILE,
              "定时任务的开关(哪几个被关掉):带去别人机器会把他的日程按你的关掉"),
    StateFile("jobs-user.json", FILE,
              "你自己登记的定时任务(名字/节奏/到点要学长跑的那句话):"
              "写的是本机日程与你的话,带去别人机器等于把你的安排塞给他"),
    StateFile("onboarding.json", FILE, "首次运行向导的「不再显示」"),
    StateFile("notify-pref.json", FILE, "桌面提醒开关与类别偏好"),
    StateFile("pushed-today.json", FILE,
              "今天弹过哪几条的去重账本:带去别人机器会把他的第一条补发误判成已弹"),
    StateFile("jobs-state.json", FILE,
              "定时任务账本(每个任务上次何时跑):带去别人机器会把新使用者的第一条周报误判成已跑"),
    StateFile("activate-port.json", FILE,
              "通知激活监听端口:第二个实例靠它找到在跑的这一个,只对本机有意义"),
    StateFile("watchdog.log", FILE, "看门狗日志:本机弹过哪些提醒、为什么没弹"),
    StateFile("weather.json", FILE, "常驻城市:含位置"),
    StateFile("weather-cache.json", FILE, "天气缓存:构建机坐标的结果"),
    StateFile("web-daily.json", FILE,
              "网页版每日额度账本:构建机这份会把它的用量带进包里"),
    StateFile("web-daily.json.tmp", FILE, "原子写的半截文件"),
    StateFile("web-usage.log", FILE, "网页版问答记录:含发问内容与来源 IP,更不能外发"),
    StateFile("web-sessions", DIR, "网页版会话留档:局域网问答版记的本机对话"),
    StateFile("migrate-backup", DIR, "导入迁移包前的原件备份:本人文件副本"),
    StateFile("skill-backup", DIR,
              "导入技能分享包覆盖同名技能之前,那一份原件的备份:本人文件副本"),
    StateFile("campus-choices.json", FILE,
              "地图歧义答案:用户亲口认过的对应关系,属个人使用记录"),
    StateFile("campus-choices.json.tmp", FILE, "原子写的半截文件"),
    StateFile("mood.json", FILE,
              "情绪日记:全 app 最私密的一份,随包发出去就是把自己的心情史交给别人"),
    StateFile("mood.json.tmp", FILE, "情绪日记原子写的半截文件"),
    StateFile("kb/reviewed.json", FILE, "候选审核里人工并入的知识:含本机问答"),
    StateFile("kb-vectors", DIR,
              "向量档缓存(条目指纹 + 向量):构建机问过哪些内容的痕迹。缺了它检索只降级为"
              "纯关键词,并在口径里如实说明,不会假装语义"),
    StateFile("models", DIR, "用时下载的本地模型权重:几百 MB 的可再下资源,压根不进包"),
    StateFile("charts", DIR,
              "图表产物:正常落在**工作区**,这里删的是「有人把工作台选在程序目录」那种情况"),
    StateFile("images", DIR,
              "图片产物:同 charts,落工作区;工作区=程序目录时才会出现在这里"),
    StateFile("browser-profile", DIR,
              "教务导入的浏览器登录态:里面是登录后的 cookie,带走 = 把账号交给别人"),
    StateFile("agents", DIR,
              "本地档位定义(agents/*.md,v0.9.129):这一档多出来的角色规矩是这台机器上的人"
              "自己写的,与用户级 AGENTS.md 同一类 —— 替别人定规矩不该随包发。缺了它只是没有"
              "自定义档,派发那层会照内置口径跑,不会报错"),
)

_BY_NAME: dict[str, StateFile] = {e.name: e for e in SHIP_NEVER}


def names() -> tuple[str, ...]:
    return tuple(e.name for e in SHIP_NEVER)


def remove_from(target_dir: Path) -> list[str]:
    """把这份清单从 `target_dir`(打包产物目录)里删干净,返回真删掉了哪些。

    刻意用 `missing_ok` / `ignore_errors`:构建机上没跑过网页版是常态,不该为此红。
    红只该出现在**清单本身错了**的时候 —— 那由 tests/test_state_files.py 管。
    """
    removed: list[str] = []
    for entry in SHIP_NEVER:
        path = target_dir.joinpath(*entry.name.split("/"))
        if entry.kind == DIR:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed.append(entry.name)
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(entry.name)
    return removed
