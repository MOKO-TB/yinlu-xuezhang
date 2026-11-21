# -*- coding: utf-8 -*-
"""卡顿取证:一段真回答流进来时,时间到底花在哪。

为什么要有这一场:用户报"使用中有明显的卡顿感",而这句话有好几个可能的病灶
(每增量一次 JS 往返 / markdown 整段重排 / 每次渲染都滚到底 / 每次增量都去折叠
所有 trace)。凭印象挑一个去优化,大概率是把第二贵的当第一贵。

这一场把真前端(同一份 index.html、同一条 `__gcodeEvent` 入口)开在真 Chromium 里,
量四个数:

  A. 一发一往返:600 条 text_delta 每条一次 page.evaluate —— 对应今天
     `GuiUI.push` 每个增量一次 `window.evaluate_js` 的形状;
  B. 一次往返喂 600 条:同样的内容,循环放在 JS 里面 —— 差值就是"往返本身"的代价;
  C. 增量渲染的分项耗时:renderMd / toBottom / foldAllTraces 各占多少;
  D. 越答越慢的那条曲线:前 50 条 vs 后 50 条增量的单条耗时 —— 整段重排是
     O(n²) 的,这一段量出来的是"答到后面为什么开始卡"。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/e2e_ui_lag.py
"""
from __future__ import annotations

import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from e2e_launch import launch          # noqa: E402

STUB_JS = r"""
window.__CALLS__ = [];
const base = {
  get_info: () => Promise.resolve({
    version: "0.9.139", app_name: "引路学长", permission_mode: "confirm",
    permission_modes: [{value: "confirm", label: "变更前确认"}],
    model: "test-model", active_provider: "test", workspace: "D:/demo/campus",
    workspace_name: "campus", workspace_is_default: true,
    providers: [{id: "test", name: "Test", model: "test-model", has_key: true}],
    api_key_configured: true, memory_count: 0, memory_user_count: 0,
    mcp_servers: [], skills: [], kb_count: 0, sandbox: null, campus_map: null,
    library_health: {}, paths: {app_dir: "D:/demo", writable: true},
    instructions: {ok: true, line: "", layers: []},
    session_metrics: {ok: false, selectable: []}}),
  get_activity: () => Promise.resolve({ok: true, summary: {}, timeline: {}, groups: []}),
  get_monitor_pref: () => Promise.resolve({ok: true, open: false, folded: {}}),
  set_monitor_pref: () => Promise.resolve({ok: true}),
  get_env_info: () => Promise.resolve({ok: true, is_repo: false, files: []}),
  refresh_sessions: () => Promise.resolve({ok: true, items: []}),
  get_recent_projects: () => Promise.resolve({ok: true, current: {}, default: {}, recent: []}),
  get_reasoning: () => Promise.resolve({visible: false, options: [], choice: "default"}),
  get_suggestions: () => Promise.resolve({suggestions: []}),
};
window.pywebview = {api: new Proxy(base, {
  get: (t, k) => (k in t) ? t[k]
    : ((...a) => { window.__CALLS__.push([k, ...a]); return Promise.resolve({ok: true}); })
})};
window.__ev = (o) => window.__gcodeEvent(o);
/* 最后一条助手消息:不能用 `.msg.assistant:last-of-type` —— `:last-of-type` 说的是
   "同类型兄弟里的最后一个",占位/工具卡那些 div 排在后面时它就不匹配了,量出来的
   会是另一条消息(我自己先被它骗过一次:600 条增量之后读到"37 字")。 */
window.__last = () => {
  const a = document.querySelectorAll(".msg.assistant");
  return a.length ? a[a.length - 1] : null;
};
/* 分项计时不靠 monkey-patch:index.html 是经典脚本,`function renderMd(){}` 建的是
   全局词法绑定,`window.renderMd = …` 盖不住它 —— 所以这里直接按名字调用被测函数,
   量"调用它一次要多久",而不是偷偷换掉它。 */
window.__bench = (name, arg, reps) => {
  const fn = window[name];
  if (typeof fn !== "function") return {missing: name};
  const t0 = performance.now();
  for (let i = 0; i < reps; i++) fn(arg);
  return {ms: performance.now() - t0, reps: reps, name: name};
};
"""

N = 600
# 一条真回答的形状:中文 + 列表 + 代码块 + 表格,越接近真实用量越能暴露整段重排的代价。
PIECE = "这一项要注意的地方不少,来看下面这段:\n- 第一点\n- 第二点\n"


def _serve(directory: Path):
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(_QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html", httpd.shutdown


def check_session_switches(page):
    report = page.evaluate(r"""() => {
      window.__historyFixture = Array.from({length: 150}, (_, i) => [
        {role:'user', text:'question '+i},
        {role:'assistant', text:'', tools:'read_file'},
        {role:'tool', name:'read_file', text:'result'},
        {role:'assistant', text:'## Answer '+i+'\n- first\n- second'}]).flat();
      const t = performance.now();
      for (let i = 0; i < 5; i++) renderHistory(__historyFixture);
      return {fiveSwitchesMs: performance.now()-t,
        historicalTimers: [...document.querySelectorAll('#chat .trace')]
          .filter(x => x._workTimer).length,
        enabledRegenerate: document.querySelectorAll('#chat .rt-re:not(:disabled)').length,
        users: document.querySelectorAll('#chat .msg.user').length,
        tools: document.querySelectorAll('#chat .toolcard').length,
        runningTraces: document.querySelectorAll('#chat .trace:not([data-done]):not([hidden])').length};
    }""")
    assert report["historicalTimers"] == report["runningTraces"] == 0, report
    assert report["enabledRegenerate"] == 1, report
    assert report["users"] == report["tools"] == 150, report
    page.locator('#chat .trace:not([hidden]) .tr-head').last.click()
    assert page.locator('#chat .trace.open').count() == 1

    assert page.evaluate("""() => {
      __ev({type:'cleared', session:'old'});
      __ev({type:'reasoning_delta', session:'old', text:'thinking'});
      __ev({type:'text_delta', session:'old', text:'draft'});
      __ev({type:'text_delta', session:'old', text:' more'});
      const old = cur;
      __ev({type:'cleared', session:'new', restoring:true});
      __ev({type:'resumed', session:'new', messages:[], busy:false});
      return !old.timer && !old.trace._workTimer && !old.think._thTimer;
    }"""), "old stream timers survived navigation"
    assert page.locator('#chat .msg').count() == 0
    page.evaluate("""() => {
      __ev({type:'text_delta', session:'old', text:'must stay in background'});
      __ev({type:'user', session:'new', text:'new question'});
      __ev({type:'text_delta', session:'new', text:'new answer'});
      __ev({type:'finish_stream', session:'new'});
    }""")
    assert page.locator('#chat .msg.assistant .body').inner_text() == 'new answer'

    page.evaluate("""() => {
      window.__switchCalls = [];
      window.__switchReplies = new Map();
      window.__switchConcurrent = 0;
      window.__switchMaxConcurrent = 0;
      window.__switchItems = selected => ['a','b','c','d'].map((id, i) => ({
        file:id, index:i+1, title:'Session '+id, time:'2026-11-20', n_messages:2,
        current:id===selected, ws_current:true, workspace:''}));
      window.__showSession = id => {
        __ev({type:'cleared', session:id, restoring:true});
        __ev({type:'resumed', session:id, busy:id==='b', messages:[
          {role:'user',text:'Question '+id}, {role:'assistant',text:'Answer '+id}]});
        __ev({type:'sessions', items:__switchItems(id)});
      };
      pywebview.api.resume_session = id => {
        __switchCalls.push(id);
        __switchConcurrent++;
        __switchMaxConcurrent = Math.max(__switchMaxConcurrent, __switchConcurrent);
        return new Promise((resolve, reject) => __switchReplies.set(id, (mode='ok') => {
          __switchReplies.delete(id);
          __switchConcurrent--;
          if (mode === 'reject') { reject(new Error('offline')); return; }
          if (mode === 'error') { resolve({ok:false,msg:'not available'}); return; }
          if (id === 'new-session') {
            __ev({type:'cleared', session:id});
            __ev({type:'sessions', items:__switchItems(id)});
          } else __showSession(id);
          resolve({ok:true,session:id});
        }));
      };
      pywebview.api.new_task = () => pywebview.api.resume_session('new-session');
      pywebview.api.switch_workspace = path => pywebview.api.resume_session('workspace:'+path);
      pywebview.api.reset_workspace = () => pywebview.api.resume_session('default-workspace');
      __showSession('a');
    }""")

    def ready(n):
        page.wait_for_function('(n) => __switchCalls.length === n', arg=n)

    def reply(id_, mode="ok"):
        page.evaluate('([id, mode]) => __switchReplies.get(id)(mode)', [id_, mode])

    def idle():
        page.wait_for_function('sessSwitchActive === null && sessSwitchPending === null')

    def click(id_):
        page.get_by_text('Session ' + id_, exact=True).click()

    page.evaluate("""() => {
      const rows = document.querySelectorAll('#sessions .sess');
      rows[1].click(); rows[2].click(); rows[3].click();
    }""")
    ready(1)
    assert page.evaluate('__switchCalls') == ['d']
    reply('d')
    idle()
    assert page.locator('#chat .body').inner_text() == 'Answer d'

    click('b')
    ready(2)
    click('c')
    page.wait_for_function('sessSwitchTimer === 0')
    click('d')
    page.wait_for_function('sessSwitchTimer === 0')
    assert page.evaluate('__switchCalls') == ['d', 'b']
    reply('b')
    ready(3)
    assert page.locator('#btnStop').is_visible()
    assert page.evaluate('__switchCalls') == ['d', 'b', 'd']
    reply('d')
    idle()
    assert not page.locator('#btnStop').is_visible()

    click('b')
    ready(4)
    click('d')
    reply('b')
    ready(5)
    reply('d')
    idle()
    assert page.evaluate('currentSessionKey') == 'd'

    for mode in ('error', 'reject'):
        page.evaluate("__showSession('d')")
        click('b')
        page.wait_for_function("__switchReplies.has('b')")
        reply('b', mode)
        idle()
        click('c')
        page.wait_for_function("__switchReplies.has('c')")
        reply('c')
        idle()
        assert page.evaluate('currentSessionKey') == 'c'

    before = page.evaluate('__switchCalls.length')
    page.evaluate("() => { __showSession('a'); stepSession(1); stepSession(1); stepSession(1); }")
    ready(before + 1)
    assert page.evaluate('__switchCalls.at(-1)') == 'd'
    reply('d')
    idle()

    before = page.evaluate('__switchCalls.length')
    page.evaluate("""() => {
      __showSession('a');
      sessQuery = 'question';
      $('sessSearch').value = sessQuery;
      renderSessionHits({query:sessQuery, results:['b','c','d'].map(file => ({file,hits:1}))});
      document.querySelectorAll('#sessions .sess').forEach(el => el.click());
    }""")
    ready(before + 1)
    assert page.evaluate('__switchCalls.at(-1)') == 'd'
    reply('d')
    idle()
    assert page.locator('#sessSearch').input_value() == ''
    assert page.evaluate('__switchMaxConcurrent') == 1

    before = page.evaluate('__switchCalls.length')
    page.evaluate("() => { __showSession('a'); switchSessionSoon('b'); switchSessionSoon('a'); }")
    idle()
    assert page.evaluate('sessSwitchTimer') == 0
    assert page.evaluate('__switchCalls.length') == before

    page.evaluate("() => { switchSessionSoon('b'); newTaskInProject(); }")
    ready(before + 1)
    assert page.evaluate('__switchCalls.at(-1)') == 'new-session'
    reply('new-session')
    idle()
    assert page.evaluate('currentSessionKey') == 'new-session'
    assert page.evaluate('__switchCalls.length') == before + 1

    click('b')
    page.wait_for_function("__switchReplies.has('b')")
    page.evaluate("() => { switchSessionSoon('c'); newTaskInProject(); }")
    reply('b')
    page.wait_for_function("__switchReplies.has('new-session')")
    click('d')
    reply('new-session')
    page.wait_for_function("__switchReplies.has('d')")
    reply('d')
    idle()
    assert page.evaluate('__switchCalls.slice(-3)') == ['b', 'new-session', 'd']
    assert page.evaluate('__switchMaxConcurrent') == 1

    page.evaluate("""() => {
      __showSession('a');
      sessQuery = 'question';
      $('sessSearch').value = sessQuery;
      renderSessionHits({query:sessQuery, results:[{file:'a',hits:1}]});
      __ev({type:'cleared', session:'external-new'});
      __ev({type:'sessions', items:__switchItems('external-new')});
    }""")
    assert page.locator('#sessions .sess').count() == 1
    page.locator('#sessions .sess').click()
    page.wait_for_function("__switchReplies.has('a')")
    reply('a')
    idle()
    assert page.evaluate('currentSessionKey') == 'a'
    assert page.locator('#sessSearch').input_value() == ''

    before = page.evaluate('__switchCalls.length')
    page.evaluate("() => { switchSessionSoon('b'); doSwitchWorkspace('other-project'); }")
    ready(before + 1)
    assert page.evaluate('__switchCalls.at(-1)') == 'workspace:other-project'
    reply('workspace:other-project')
    idle()
    assert page.evaluate('currentSessionKey') == 'workspace:other-project'
    assert page.evaluate('__switchCalls.length') == before + 1

    page.evaluate("doSwitchWorkspace('third-project')")
    page.wait_for_function("__switchReplies.has('workspace:third-project')")
    page.evaluate("() => { switchSessionSoon('b'); doSwitchWorkspace('', true); }")
    reply('workspace:third-project')
    page.wait_for_function("__switchReplies.has('default-workspace')")
    reply('default-workspace')
    idle()
    assert page.evaluate('currentSessionKey') == 'default-workspace'
    assert page.evaluate('__switchMaxConcurrent') == 1

    lazy = page.evaluate(r"""async () => {
      const messages = Array.from({length:1500}, (_,i) => [
        {role:'user', text:'第 '+(i+1)+' 问：检查功能'},
        {role:'assistant', text:'## 回答 '+i+'\n- 第一点\n- 第二点'}]).flat();
      const t = performance.now();
      renderHistory(messages);
      const ms = performance.now() - t;
      const pendingAtInsert = document.querySelectorAll('#chat [data-md-pending]').length;
      await new Promise(r => setTimeout(r, 250));
      const pendingAfterIdle = document.querySelectorAll('#chat [data-md-pending]').length;
      const renderedNearBottom = document.querySelectorAll('#chat .msg.assistant .body:not([data-md-pending]) h2').length;
      ensureAllHistoryBodies();
      return {ms, pendingAtInsert, pendingAfterIdle, renderedNearBottom,
        users: document.querySelectorAll('#chat .msg.user').length,
        lastOrd: [...document.querySelectorAll('#chat .msg.user')].pop()?.dataset.uord,
        pendingAfterAll: document.querySelectorAll('#chat [data-md-pending]').length,
        headings: document.querySelectorAll('#chat .msg.assistant .body h2').length};
    }""")
    assert lazy["users"] == 1500 and lazy["lastOrd"] == "1500", lazy
    assert 0 < lazy["pendingAfterIdle"] < 3000, lazy
    assert lazy["renderedNearBottom"] > 0, lazy
    assert lazy["pendingAfterAll"] == 0 and lazy["headings"] == 1500, lazy
    print(f"G 长历史惰性渲染:1500 轮插入 {lazy['ms']:.0f} ms,"
          f"离屏待渲染 {lazy['pendingAfterIdle']} 条,全部展开后 {lazy['headings']} 个标题")

    metrics = page.evaluate("""async () => {
      const api = pywebview.api;
      const saved = api.get_session_metrics;
      const render = renderTaskMetrics;
      let calls = 0, renders = 0;
      const pending = [];
      api.get_session_metrics = () => { calls++; return new Promise(r => pending.push(r)); };
      renderTaskMetrics = () => { renders++; };
      try {
        __showSession('a');
        __showSession('d');
        pending[0]({ok:true});
        await Promise.resolve();
        const staleRenders = renders;
        pending[1]({ok:true});
        await Promise.resolve();
        return {calls, renders, staleRenders};
      } finally { api.get_session_metrics = saved; renderTaskMetrics = render; }
    }""")
    assert metrics == {"calls": 2, "renders": 1, "staleRenders": 0}, metrics

    detached = page.evaluate(r"""async () => {
      const oldMermaid = ensureMermaid, oldKatex = ensureKatex;
      const mermaidValue = window.mermaid, katexValue = window.katex;
      let release, work = 0;
      const gate = new Promise(r => release = r);
      ensureMermaid = ensureKatex = () => gate;
      window.mermaid = {run: async () => { work++; }};
      window.katex = {renderToString: () => { work++; return ''; }};
      try {
        chat.innerHTML = '<pre><code class="language-mermaid">graph TD; A-->B</code></pre>'
          + '<span class="math">x^2</span>';
        const jobs = [renderMermaidBlocks(chat), renderMathBlocks(chat)];
        renderHistory([]);
        release(true);
        await Promise.all(jobs);
        return work;
      } finally {
        ensureMermaid = oldMermaid; ensureKatex = oldKatex;
        window.mermaid = mermaidValue; window.katex = katexValue;
      }
    }""")
    assert detached == 0, "detached formula/diagram still rendered"
    print("F 会话切换回归:", report, "并发请求上限 = 1; 连点/搜索/失败重试/后台恢复通过")


def main() -> int:
    from playwright.sync_api import sync_playwright

    url, shutdown = _serve(FRONTEND)
    with sync_playwright() as pw:
        browser = launch(pw)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STUB_JS)
        page.goto(url)
        page.wait_for_selector("#ver", timeout=15000)
        page.wait_for_function("() => !!window.__gcodeEvent")

        def start_answer():
            page.evaluate("() => window.__gcodeEvent({type:'user', text:'帮我算一下'})")

        # ── A. 一发一往返(今天的形状:每个增量一次 evaluate_js)
        start_answer()
        t0 = time.perf_counter()
        for i in range(N):
            page.evaluate("(o) => window.__ev(o)", {"type": "text_delta", "text": PIECE})
        a_wall = (time.perf_counter() - t0) * 1000.0
        page.evaluate("() => window.__ev({type:'finish_stream'})")

        # ── B. 一次往返喂完 600 条(批处理的形状)
        start_answer()
        t0 = time.perf_counter()
        page.evaluate("""([n, piece]) => { for (let i = 0; i < n; i++)
                          window.__ev({type:'text_delta', text: piece}); }""", [N, PIECE])
        b_wall = (time.perf_counter() - t0) * 1000.0
        page.evaluate("() => window.__ev({type:'finish_stream'})")
        page.wait_for_timeout(200)

        # ── C. 三个嫌疑犯各自的单价(在 B 那一轮那份**长回答**上量)
        final_text = page.evaluate(
            "() => { const m = window.__last(); return m ? (m.dataset.raw || '') : ''; }")
        bench = page.evaluate("""([t]) => ({
            renderMd: __bench('renderMd', t, 30),
            toBottom: __bench('toBottom', undefined, 30),
            foldAllTraces: __bench('foldAllTraces', undefined, 30),
        })""", [final_text])

        # ── D. 越答越慢:同一套 renderMd 在不同长度下的单价
        curve = page.evaluate("""([t]) => {
            const lens = [200, 1000, 3000, 6000, 9000];
            return lens.map(n => ({n: n, one: __bench('renderMd', t.slice(0, n), 10).ms / 10}));
        }""", [final_text + PIECE * 200])
        text_len = len(final_text)

        # ── E. 攒批那一半在前端真的接得住:`__gcodeEvents` 必须在场且把整批落到屏上。
        #       缺这只函数的坏法是不报错的 —— push 里 evaluate_js 抛异常被那层
        #       try/except 吞掉,于是流式正文一个字都不出现。
        batched = page.evaluate("""([piece]) => {
            window.__gcodeEvent({type:'user', text:'再问一句'});
            window.__gcodeEvents([{type:'text_delta', text: piece},
                                  {type:'text_delta', text: '收尾那半句'}]);
            window.__gcodeEvent({type:'finish_stream'});
            const m = window.__last();
            return m ? (m.dataset.raw || '') : '';
        }""", [PIECE])
        ok_batch = ("收尾那半句" in batched) and (PIECE in batched)
        print("E 一批事件落到屏上      :" + ("✓" if ok_batch else "✗ __gcodeEvents 没接住"))
        if not ok_batch:
            errors.append("__gcodeEvents 没把整批渲染出来")

        print(f"回答正文长度:{text_len} 字")
        print(f"A 一发一往返 {N} 条      :{a_wall:8.1f} ms  ({a_wall/N:.2f} ms/条)")
        print(f"B 一次往返喂 {N} 条      :{b_wall:8.1f} ms  ({b_wall/N:.2f} ms/条)")
        if a_wall > 0:
            print(f"  → 往返本身的代价      :{a_wall-b_wall:8.1f} ms"
                  f"  (占 A 的 {(a_wall-b_wall)/a_wall*100:.0f}%)")
        print("C 单价(调用一次要多久,长度 %d 字):" % text_len)
        for key in ("renderMd", "toBottom", "foldAllTraces"):
            got = bench.get(key) or {}
            if got.get("missing"):
                print(f"    {key:14s}:这个名字在全局拿不到(不是 function)")
            else:
                print(f"    {key:14s}:{got['ms']/got['reps']:.3f} ms")
        print("D renderMd 单价随长度:")
        base = curve[0]["one"] or 0.001
        for row in curve:
            print(f"    {row['n']:5d} 字:{row['one']:.3f} ms   ({row['one']/base:.1f}×)")
        check_session_switches(page)
        print("页面错误:" + ("无" if not errors else " / ".join(errors[:3])))
        browser.close()
    shutdown()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
