"""哪些工具可以和同一条消息里的邻伴一起跑(§6 50(b) 那批并发的资格表)。

为什么单独立一个文件:`registry.py` 有针要求它"只管装配顺序、不超过 60 行"
(v0.9.39 #84 那次把 987 行拆成 catalog_* 就是为了这个),把名单与标记循环塞进
装配函数正好会顶破那道针 —— 那是它在说"你放错地方了",不是针太严。

这是**逐条看过实现**才写进来的名单,不是"只读就进来"。被排除的几类各有原因:
  browser_* / page_parse —— 走 CDP 与 playwright 同步 API,跨线程共用一条会话;
  ask_user —— 只读、门也不问,但弹框发生在 handler 里:进池子等于**从工作线程
               弹确认框**(`Bridge._confirm_lock` 一次一框的前提就破了),而且不报错;
  map_* / campus_notices / get_weather / ocr_image —— 要么改共用视图、要么写缓存
               文件、要么抢一颗 ONNX 会话:并发不省时间,还多一类交叉状态;
  search_knowledge —— 检索那一刻要往 kb-hits.jsonl 记命中(反馈绑条目的坐标),
               两路同时记会把"哪次提问引用了哪条"错配;
  worktree / bash_status —— 起子进程或读作业表,串行更便宜也更好判断。
名单之外还有一道运行时判据(`agent._lane_ok`:没配钩子、门不会问),两个条件都过
才真的同跑。
"""
from __future__ import annotations

CONCURRENT_SAFE = frozenset({
    "read_file", "glob", "grep", "repo_outline",          # 纯文件读
    "use_skill", "search_history",                         # 读技能与会话留档
    "campus_calendar", "gpa_calc",                         # 只读校历与绩点计算
    "library_search", "library_guide",                     # 馆藏(共用连接有并发针,§2 v0.9.95)
    "permissions", "hooks",                                # 只读诊断,不碰配置
})


def mark_concurrent(tools) -> None:
    """给名单内且只读的工具点亮旗标。

    新加一只工具**不会自动进池**(默认 False),把名字从名单里划掉就退回串行 ——
    默认值站在安全这一边,这一点由 `test_concurrent_safe_is_opt_in_and_only_on_read_only_tools`
    钉住。
    """
    for t in tools:
        t.concurrent_safe = bool(t.read_only and t.name in CONCURRENT_SAFE)
