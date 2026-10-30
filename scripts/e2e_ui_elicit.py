# -*- coding: utf-8 -*-
"""MCP elicitation 表单卡的真浏览器验证(v0.9.191)。

断言的是"界面上真能用":卡片按后端给的字段摆出来(string/integer/enum/boolean
各按类型渲染)、提交把**字符串原样**交给 `elicit_reply`(类型转换在后端)、
校验不过把错误清单贴回卡上且卡不下、拒绝走 `{decline:true}`、超时/收起不留
悬挂状态由后端那半负责(这里只验界面半边)。

桩的边界(哪一段是故意不走真路的,写在文件头 —— 别当覆盖率漏洞补全):
`pywebview.api` 整只被换掉,`elicit_reply` 按脚本回(真后端的编译/校验/握手
由 tests/test_elicitation.py 的 Bridge 级测试负责);boot() 依赖的真实 payload
一概 {ok:true},boot 半路抛错被自己的 catch 接住不影响事件路由 —— 这一支
不验 boot 完整性(e2e_ui_init.py 那支管)。

跑法(项目根目录):
    PYTHONIOENCODING=utf-8 .venv\\Scripts\\python.exe scripts\\e2e_ui_elicit.py
"""
from __future__ import annotations

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

STUB = r"""
window.__elicitCalls = [];
window.__elicitReplies = [];
const _get = (k) => {
  if (k === "elicit_reply") {
    return (id, payload) => {
      window.__elicitCalls.push([id, payload]);
      const r = (window.__elicitReplies && window.__elicitReplies.length)
        ? window.__elicitReplies.shift() : { ok: true };
      return Promise.resolve(r);
    };
  }
  return () => Promise.resolve({ ok: true });
};
window.pywebview = { api: new Proxy({}, { get: (t, k) => _get(k) }) };
"""


def serve():
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(FRONTEND), **k)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


FIELDS = [
    {"name": "path", "label": "文件路径", "type": "string", "required": True,
     "max_len": 100, "enum": None, "minimum": None, "maximum": None,
     "default": None, "description": "要写哪个文件"},
    {"name": "times", "label": "次数", "type": "integer", "required": False,
     "enum": None, "max_len": None, "minimum": 1, "maximum": 5,
     "default": 2, "description": ""},
    {"name": "mode", "label": "模式", "type": "string", "required": False,
     "enum": ["fast", "slow"], "max_len": None, "minimum": None,
     "maximum": None, "default": None, "description": ""},
    {"name": "loud", "label": "大声", "type": "boolean", "required": False,
     "enum": None, "max_len": None, "minimum": None, "maximum": None,
     "default": None, "description": ""},
]


def main() -> int:
    srv = serve()
    port = srv.server_address[1]
    from playwright.sync_api import sync_playwright
    from e2e_launch import launch
    fails: list[str] = []

    def check(name: str, cond: bool):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    with sync_playwright() as p:
        b = launch(p)
        page = b.new_page()
        page.add_init_script(STUB)
        page.goto("http://127.0.0.1:%d/index.html" % port)
        page.wait_for_function("() => typeof window.__gcodeEvent === 'function'")
        page.wait_for_timeout(200)

        page.evaluate("(f) => window.__gcodeEvent({type:'elicit_request',"
                      "elicit_id:'e1', message:'请确认写入参数', fields:f})", FIELDS)
        check("卡片弹出", page.evaluate(
            "() => document.querySelector('#elicitOverlay').classList.contains('show')"))
        check("说明文案上卡", page.evaluate(
            "() => document.querySelector('#elicitMsg').textContent === '请确认写入参数'"))
        check("四个字段按类型渲染", page.evaluate("""() => {
            const rows = document.querySelectorAll('#elicitFields [data-name]');
            if (rows.length !== 4) return false;
            const by = {};
            rows.forEach(r => by[r.dataset.name] = r);
            if (by.path.tagName !== 'INPUT' || by.path.maxLength !== 100) return false;
            if (by.times.tagName !== 'INPUT' || by.times.type !== 'number') return false;
            if (by.times.min !== '1' || by.times.max !== '5') return false;
            if (by.mode.tagName !== 'SELECT') return false;
            const opts = [...by.mode.options].map(o => o.value);
            if (JSON.stringify(opts) !== JSON.stringify(['', 'fast', 'slow'])) return false;
            if (by.loud.tagName !== 'SELECT') return false;
            return [...by.loud.options].some(o => o.textContent === '是');
        }"""))
        check("必填星号标出", page.evaluate(
            "() => document.querySelector('#elicitFields label').textContent.startsWith('* ')"))

        # 提交:值必须是**字符串原样**,类型转换在后端
        page.evaluate("""() => {
            const by = {};
            document.querySelectorAll('#elicitFields [data-name]').forEach(r => by[r.dataset.name] = r);
            by.path.value = 'a/b.txt'; by.times.value = '3';
            by.mode.value = 'slow'; by.loud.value = 'yes';
        }""")
        page.click("#btnElicitOk")
        page.wait_for_timeout(150)
        calls = page.evaluate("() => window.__elicitCalls")
        check("提交把字符串原样交给 elicit_reply", json.dumps(calls) == json.dumps(
            [["e1", {"values": {"path": "a/b.txt", "times": "3",
                                "mode": "slow", "loud": "yes"}}]]))
        check("提交成功后卡片收起", page.evaluate(
            "() => !document.querySelector('#elicitOverlay').classList.contains('show')"))

        # 校验不过:错误清单贴回卡上,卡不下;改对之后再交就收
        page.evaluate("(f) => window.__gcodeEvent({type:'elicit_request',"
                      "elicit_id:'e2', message:'再来一次', fields:f})", FIELDS)
        page.evaluate("() => window.__elicitReplies.push("
                      "{ok:false, errors:['「文件路径」必填']})")
        page.click("#btnElicitOk")
        page.wait_for_timeout(150)
        check("校验错误贴回卡上且卡不下", page.evaluate("""() => {
            const err = document.querySelector('#elicitErr');
            return err.style.display === 'block'
                && err.textContent.indexOf('必填') >= 0
                && document.querySelector('#elicitOverlay').classList.contains('show');
        }"""))
        page.evaluate("() => window.__elicitReplies.push({ok:true})")
        page.evaluate("""() => { const by = {};
            document.querySelectorAll('#elicitFields [data-name]').forEach(r => by[r.dataset.name] = r);
            by.path.value = 'a/b.txt'; }""")
        page.click("#btnElicitOk")
        page.wait_for_timeout(150)
        check("改对之后提交收卡", page.evaluate(
            "() => !document.querySelector('#elicitOverlay').classList.contains('show')"))

        # 拒绝
        page.evaluate("(f) => window.__gcodeEvent({type:'elicit_request',"
                      "elicit_id:'e3', message:'第三次', fields:f})", FIELDS)
        page.click("#btnElicitNo")
        page.wait_for_timeout(150)
        last = page.evaluate("() => window.__elicitCalls[window.__elicitCalls.length - 1]")
        check("拒绝走 {decline:true}", json.dumps(last) == json.dumps(
            ["e3", {"decline": True}]))
        check("拒绝后卡片收起", page.evaluate(
            "() => !document.querySelector('#elicitOverlay').classList.contains('show')"))
        b.close()
    srv.shutdown()
    print("PASS" if not fails else "FAIL: %s" % fails)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
