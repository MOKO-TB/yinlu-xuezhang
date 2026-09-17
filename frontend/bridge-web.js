/* 引路学长 · 网页版桥接 shim
 *
 * 由 web_server 在返回 index.html 时注入到 <head> 最前面,页面本身零改动。
 * 它做三件事:
 *   1. 伪造 window.pywebview.api —— 把每次方法调用变成 POST /api/<方法名>;
 *   2. 开一条 SSE(GET /api/events),把服务端推来的事件转给 window.__gcodeEvent;
 *   3. 藏掉只属于所有者的控件(密钥/沙箱/撤销/附件/权限模式)。
 *
 * 位置参数 → 具名参数:服务端门面收的是关键字参数,所以这里按表映射。
 */
(function () {
  "use strict";

  // [方法名, ...位置参数名]
  var METHODS = [
    ["get_info"],
    ["get_suggestions"],
    ["send_task", "text", "attachments"],
    ["stop_task"],
    ["new_task"],
    ["compact_now"],
    ["edit_resend", "ordinal", "new_text", "expect"],
    ["confirm_reply", "choice"],
    ["refresh_sessions"],
    ["resume_session", "index"],
    ["delete_session", "index"],
    ["get_asset", "rel_path"],
    ["undo"],
    ["pick_attachments"],
    ["set_sandbox", "enabled"],
    ["set_permission_mode", "mode"],
    ["consolidate_memory", "scope"],
    ["get_providers"],
    ["save_providers", "providers_json", "active_id"],
    ["set_provider", "provider_id"],
  ];

  function post(name, args) {
    return fetch("/api/" + name, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(args || {}),
    }).then(function (resp) {
      if (!resp.ok) throw new Error(name + " 请求失败(HTTP " + resp.status + ")");
      return resp.json();
    });
  }

  var api = {};
  METHODS.forEach(function (entry) {
    var name = entry[0], params = entry.slice(1);
    api[name] = function () {
      var args = {};
      for (var i = 0; i < params.length; i++) {
        if (i < arguments.length && arguments[i] !== undefined) args[params[i]] = arguments[i];
      }
      return post(name, args);
    };
  });

  // 打开链接交给浏览器自己:服务端执行 webbrowser.open 会开在服务器那台机器上
  api.open_url = function (url) {
    url = String(url || "");
    if (/^https?:\/\//i.test(url)) window.open(url, "_blank", "noopener");
    return Promise.resolve({ ok: true });
  };

  window.pywebview = { api: api };

  /* ---------------- 事件流 ---------------- */
  var pending = [];
  function deliver(ev) {
    if (typeof window.__gcodeEvent === "function") {
      try { window.__gcodeEvent(ev); } catch (e) { console.error("[bridge-web] 事件处理失败", e, ev); }
      return;
    }
    pending.push(ev);                       // 页面脚本还没解析到时先存着
    if (pending.length > 500) pending.shift();
  }
  function flush() {
    while (pending.length && typeof window.__gcodeEvent === "function") {
      var ev = pending.shift();
      try { window.__gcodeEvent(ev); } catch (e) { /* 忽略单条事件错误 */ }
    }
  }

  var source = null;
  function connect() {
    if (source) return;
    // 先握手拿到访客 cookie,再开流(顺序反了会各拿一个访客身份)
    post("hello", {}).then(function () {
      source = new EventSource("/api/events");
      source.onopen = function () {
        flush();
        // 首屏可能在 SSE 建好之前就请求过会话列表,这里补一次
        try { api.refresh_sessions(); } catch (e) {}
      };
      source.onmessage = function (e) {
        var ev;
        try { ev = JSON.parse(e.data); } catch (err) { return; }
        if (ev && ev.type === "__closed__") { source.close(); source = null; return; }
        deliver(ev);
      };
      source.onerror = function () {
        // EventSource 自带重连;连不上就等它自己重试
      };
    }).catch(function (e) {
      console.error("[bridge-web] 事件流建立失败", e);
    });
  }
  connect();

  /* ---------------- 隐藏所有者专属控件 ---------------- */
  var css = [
    "#btnMemory", "#btnSandbox", "#btnUndo", "#btnAttach", "#perm", "#btnSkills",
  ].join(",") + "{display:none !important}";
  function injectCss() {
    var style = document.createElement("style");
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
  }
  if (document.head) injectCss();
  else document.addEventListener("DOMContentLoaded", injectCss);

  /* ---------------- 兼容 index.html 的初始化分支 ----------------
   * 页面脚本会检查 window.pywebview 是否就绪:已就绪就直接 boot(),
   * 否则等 pywebviewready。这里把两种路径都铺好。
   */
  window.addEventListener("DOMContentLoaded", function () {
    try { window.dispatchEvent(new Event("pywebviewready")); } catch (e) {}
  });
})();
