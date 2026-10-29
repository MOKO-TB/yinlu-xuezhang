---
name: interactive-html
description: 做能拖动/调参、曲线和指标实时变化的单页模拟器或仪表盘时用:自包含 HTML/CSS/JS 模板、滑条/预设/Canvas 曲线、沙箱边界与自检清单
---

# 什么时候用

用户要的是"**能拖、能调、图和数字实时跟着变**"的东西,而不是一张固定数据的图:

- 咖啡调豆、投资收益、实验参数、体能分配这类"改输入看输出"的模拟器;
- 带筛选/预设/阈值的仪表盘;
- 课堂上让人自己动手试的参数演示页。

固定数据看趋势用现成的图表卡就够了;这一份技能是给**有交互逻辑**的单页用的。
同一件事不要既写 HTML 又调图表工具,选一种。

# 硬边界(沙箱)

写出来的页面会在**隔离沙箱**里运行,下面这些一律做不到,写到页面里只会坏掉:

- 不能联网:`fetch`、XHR、WebSocket、外链脚本/样式/图片/字体都会被拦;
- 不能 `eval`/`new Function`,不能用 `localStorage`/`cookie`;
- 碰不到 `window.parent`、宿主按钮、桥接口;
- 单文件 ≤ 512 KB;超过 512 KB 会被拒,不截断;
- 别写 `while(true)` 或长时间同步循环;拖动时只做轻量重算,动画用 `requestAnimationFrame`。

所有数据、常数、公式都写在文件里;图表用 Canvas 或 SVG 自己画,不引图表库。
页面按 **700–760px** 宽设计,窄屏自适应。

# 工作流

1. 先确认**输入参数**(2–4 个滑条最好)和**输出指标**(2–4 个数字 + 1 张曲线/柱图);
   参数和指标都要带单位和当前值。
2. 用 `write_file` 写一份单页 `.html`(比如 `charts/咖啡调豆模拟器.html`)。
   一次写完整,不要只给代码片段。
3. 写完用一句话告诉用户:**产物卡上点「运行」就可以拖;默认预览是静态的**。
4. 用户说哪里不对时,用 `read_file` 读回页面再改,别凭记忆重写。

# 最小模板

下面这份可以直接改:三个滑条、三个指标、三个预设、一张 Canvas 曲线。把 `compute()`
和 `series()` 换成你的领域公式,把标题/参数名/单位换掉即可。

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>参数模拟器</title>
<style>
  :root{--bg:#f6f8fb;--card:#fff;--ink:#10202b;--muted:#66788a;--line:#e3e9f0;--accent:#2f9e8f;--accent2:#5b8def}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:18px}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:12px;margin-bottom:14px}
  .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
  .metric{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
  .metric span{display:block;color:var(--muted);font-size:11px}
  .metric b{font-size:20px}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
  .presets{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .presets button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 12px;cursor:pointer}
  .presets button.on{border-color:var(--accent);background:#e8f6f3;color:#1d6f65}
  .row{display:grid;grid-template-columns:120px 1fr 76px;gap:10px;align-items:center;margin:10px 0}
  .row label{font-size:12px;color:var(--muted)}
  .row input[type=range]{width:100%;accent-color:var(--accent)}
  .row output{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
  canvas{width:100%;height:220px;display:block}
  .legend{display:flex;gap:14px;font-size:11px;color:var(--muted);margin-top:6px}
  .dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
  .note{font-size:11px;color:var(--muted);margin-top:8px}
  @media(max-width:560px){.metrics{grid-template-columns:1fr 1fr}.row{grid-template-columns:88px 1fr 64px}}
</style>
</head>
<body>
<div class="wrap">
  <h1 id="title">参数模拟器</h1>
  <div class="sub" id="sub">拖动参数，看曲线和指标一起变。</div>
  <div class="metrics">
    <div class="metric"><span>指标 A</span><b id="m1">—</b></div>
    <div class="metric"><span>指标 B</span><b id="m2">—</b></div>
    <div class="metric"><span>指标 C</span><b id="m3">—</b></div>
  </div>
  <div class="panel">
    <div class="presets" id="presets"></div>
    <div class="row"><label for="p1">参数一</label><input id="p1" type="range" min="0" max="100" value="40"><output id="o1"></output></div>
    <div class="row"><label for="p2">参数二</label><input id="p2" type="range" min="0" max="100" value="60"><output id="o2"></output></div>
    <div class="row"><label for="p3">参数三</label><input id="p3" type="range" min="0" max="100" value="30"><output id="o3"></output></div>
  </div>
  <div class="panel">
    <canvas id="chart" width="720" height="220"></canvas>
    <div class="legend">
      <span><i class="dot" style="background:#2f9e8f"></i>曲线 A</span>
      <span><i class="dot" style="background:#5b8def"></i>曲线 B</span>
    </div>
    <div class="note" id="note"></div>
  </div>
</div>
<script>
(function(){
  "use strict";
  var presets = [
    {name:"默认", p1:40, p2:60, p3:30},
    {name:"方案 A", p1:75, p2:35, p3:55},
    {name:"方案 B", p1:20, p2:80, p3:70}
  ];
  var state = {p1:40, p2:60, p3:30};
  var $ = function(id){ return document.getElementById(id); };
  var canvas = $("chart"), ctx = canvas.getContext("2d");
  function compute(){
    var a = state.p1 / 100, b = state.p2 / 100, c = state.p3 / 100;
    return {
      m1: (a * 100).toFixed(1),
      m2: ((1 - b) * 50 + 20).toFixed(1),
      m3: (a * 40 + b * 30 + c * 30).toFixed(1)
    };
  }
  function series(){
    var out = [];
    for (var i = 0; i <= 40; i++){
      var x = i / 40;
      out.push({
        x: x,
        a: Math.min(1, x * (0.4 + state.p1 / 100)),
        b: Math.max(0, Math.min(1, 1 - Math.exp(-x * (0.5 + state.p2 / 150))))
      });
    }
    return out;
  }
  function draw(){
    var w = canvas.width, h = canvas.height, pad = {l:36,r:12,t:12,b:24};
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle = "#e3e9f0"; ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++){
      var y = pad.t + (h - pad.t - pad.b) * g / 4;
      ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
    }
    var pts = series();
    function line(key, color){
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2.4;
      pts.forEach(function(p,i){
        var x = pad.l + (w - pad.l - pad.r) * p.x;
        var y = h - pad.b - (h - pad.t - pad.b) * p[key];
        if (i) ctx.lineTo(x,y); else ctx.moveTo(x,y);
      });
      ctx.stroke();
    }
    line("a", "#2f9e8f"); line("b", "#5b8def");
  }
  function render(){
    var m = compute();
    $("m1").textContent = m.m1;
    $("m2").textContent = m.m2;
    $("m3").textContent = m.m3;
    $("o1").textContent = state.p1 + " %";
    $("o2").textContent = state.p2 + " %";
    $("o3").textContent = state.p3 + " %";
    draw();
  }
  ["p1","p2","p3"].forEach(function(id){
    $(id).addEventListener("input", function(){
      state[id] = Number(this.value); render();
    });
  });
  presets.forEach(function(p){
    var b = document.createElement("button");
    b.textContent = p.name;
    b.addEventListener("click", function(){
      state.p1 = p.p1; state.p2 = p.p2; state.p3 = p.p3;
      $("p1").value = p.p1; $("p2").value = p.p2; $("p3").value = p.p3;
      Array.prototype.forEach.call(document.querySelectorAll(".presets button"), function(x){ x.classList.remove("on"); });
      b.classList.add("on");
      render();
    });
    $("presets").appendChild(b);
  });
  $("note").textContent = "所有数据都在本文件里；拖动滑条只重算这一页，不联网。";
  render();
})();
</script>
</body>
</html>
```

# 自检清单

交付前逐条过一遍:

- [ ] 单文件;没有 `<script src>`、`<link href>`、外链图片/字体,没有 `fetch`/XHR/WebSocket。
- [ ] 没有 `eval`/`new Function`,没有 `localStorage`/`cookie`,没有 `window.parent`。
- [ ] 2–4 个滑条/下拉/按钮;每个都有标签、单位、当前值;至少一个预设按钮。
- [ ] 2–4 个指标随参数变;曲线/柱图随参数重绘;改一个参数不需要刷新页面。
- [ ] 没有死循环;拖动时只重算必要量;窄屏(约 560px)不横向溢出。
- [ ] 写完后明确告诉用户:**点产物卡上的「运行」**;默认预览是静态的。
- [ ] 没有把同学的隐私数据(姓名/学号/成绩/联系方式)写进页面。
