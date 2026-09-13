/* 校园地图面板 —— 手绘底图 + 标注框,纯 SVG/原生 DOM,零外部依赖。
 *
 * 数据来源:后端 get_info().campus_map(源自线下交付包 frontend/campus-index.json)。
 * 坐标一律基于源图 2559×1279,底图是原图直接复制,一像素没改。
 * 指令来源:window.__gcodeEvent 里 type="map" 的事件,由 map_* 工具推送:
 *   op=focus  框出建筑(可多栋)+ 出单栋标注图;op=list 打开地点列表;
 *   op=notice 显示提示(非建筑/未命中);op=pin 钉图钉;op=clear 清除。
 *
 * 视角用 SVG viewBox(cx/cy/k)实现,底图与标注框、图钉都在同一套源图坐标里,
 * 所以"按鼠标位置缩放"只是线性映射,不用跟 preserveAspectRatio 的留白打架。
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const COLOR = { orange: "#F97316", gold: "#D9A215", teal: "#12946F",
                  red: "#E5484D", blue: "#3B82F6" };

  let data = null;                  // 后端下发的 {base_map,size,items,not_buildings,...}
  let byNo = new Map();
  let svg = null, gBase = null, gHi = null, gPin = null;
  const view = { cx: 1280, cy: 640, k: 1 };
  let boxes = [];                   // 当前框出的条目
  let boxColor = "orange";
  let pins = [];

  const $ = (id) => document.getElementById(id);
  const node = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in (attrs || {})) n.setAttribute(k, attrs[k]);
    return n;
  };
  const num = (v) => (typeof v === "number" ? v : parseFloat(v) || 0);
  const W = () => (data && data.size ? num(data.size.width) : 2559);
  const H = () => (data && data.size ? num(data.size.height) : 1279);

  /* 与后端 campus.normalize() 同一套规则:剥掉提问词、去标点、栋/座/楼等价、拉丁转小写。
     前端搜索框要在本地匹配俗称,规则不一致就会出现"问了能查到、手输搜不到"。 */
  const ASK_WORDS = ["怎么走", "怎么去", "怎么到", "怎么过去", "咋走", "如何去", "怎样走",
                     "去哪走", "在哪里", "在哪儿", "在什么地方", "在哪个位置", "在哪个方位",
                     "在哪", "位置", "带我去", "我要去", "想去", "远不远", "多远",
                     "离得远吗", "怎么找"];
  function normalize(text) {
    let s = String(text || "").toLowerCase();
    ASK_WORDS.forEach((w) => { s = s.split(w).join(""); });
    s = s.replace(/[\s,，。、·．.\-—_/\\|?？!！:：;；()（）\[\]【】“”"'’‘]+/g, "");
    return s.replace(/[栋座幛楼]/g, "");
  }

  /* ---------------------------------------------------------------- 视角 */
  function rect() { return svg ? svg.getBoundingClientRect() : { width: 0, height: 0 }; }

  function applyView() {
    if (!svg) return;
    const r = rect();
    if (!r.width || !r.height) return;
    const vh = H() / view.k;
    const vw = vh * (r.width / r.height);
    view.cx = Math.max(-400, Math.min(W() + 400, view.cx));
    view.cy = Math.max(-400, Math.min(H() + 400, view.cy));
    svg.setAttribute("viewBox",
      `${(view.cx - vw / 2).toFixed(1)} ${(view.cy - vh / 2).toFixed(1)} ` +
      `${vw.toFixed(1)} ${vh.toFixed(1)}`);
  }

  /* 把可见区域对准一批图形对象;pad=1 表示刚好铺满 */
  function fitTo(bbox, pad) {
    if (!bbox) return;
    const [x0, y0, x1, y1] = bbox;
    const bw = Math.max(80, x1 - x0), bh = Math.max(80, y1 - y0);
    const r = rect();
    const aspect = (r.width || 1) / (r.height || 1);
    const padRatio = pad || 2.2;      // 建筑框很小,留足周边上下文
    const vhNeed = Math.max(bh * padRatio, (bw * padRatio) / aspect);
    view.k = Math.max(0.5, Math.min(6, H() / vhNeed));
    view.cx = (x0 + x1) / 2;
    view.cy = (y0 + y1) / 2;
    applyView();
  }

  function fitAll() { fitTo([0, 0, W(), H()], 1.02); }

  function bboxOfItems(items) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    (items || []).forEach((it) => {
      const b = it.bbox || [];
      if (b.length !== 4) return;
      x0 = Math.min(x0, num(b[0])); y0 = Math.min(y0, num(b[1]));
      x1 = Math.max(x1, num(b[2])); y1 = Math.max(y1, num(b[3]));
    });
    return isFinite(x0) ? [x0, y0, x1, y1] : null;
  }

  /* ---------------------------------------------------------------- 绘制 */
  function drawBase() {
    gBase.textContent = "";
    gBase.appendChild(node("image", {
      href: data.base_map, x: 0, y: 0, width: W(), height: H(),
      preserveAspectRatio: "none", class: "cm-map-img",
    }));
  }

  function drawBoxes() {
    gHi.textContent = "";
    boxes.forEach((it) => {
      const b = it.bbox || [];
      if (b.length !== 4) return;
      const [x0, y0, x1, y1] = b.map(num);
      const g = node("g", { class: "cm-bg-g", "data-no": it.no });
      g.appendChild(node("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0,
                                   rx: 8, class: "cm-box",
                                   stroke: COLOR[boxColor] || COLOR.orange }));
      const label = String(it.name || "");
      const size = 40;
      const t = node("text", { x: (x0 + x1) / 2, y: y0 - 14, class: "cm-box-label",
                               "font-size": size, stroke: COLOR[boxColor] || COLOR.orange });
      t.textContent = label;
      g.appendChild(t);
      const title = node("title");
      title.textContent = `${label}(${it.category || ""} · ${it.area || ""})`;
      g.appendChild(title);
      g.addEventListener("click", (ev) => { ev.stopPropagation(); focusItem(it); });
      gHi.appendChild(g);
    });

    gPin.textContent = "";
    pins.forEach((p) => {
      const g = node("g", { class: "cm-pin-g" });
      g.appendChild(node("path", {
        d: `M ${p.x} ${p.y} c -22 -34 -38 -51 -38 -73 a 38 38 0 1 1 76 0 c 0 22 -16 39 -38 73 z`,
        fill: COLOR[p.color] || COLOR.red, class: "cm-pin",
      }));
      g.appendChild(node("circle", { cx: p.x, cy: p.y - 73, r: 14, class: "cm-pin-dot" }));
      if (p.label) {
        const t = node("text", { x: p.x, y: p.y + 34, class: "cm-pin-label",
                                 "font-size": 38 });
        t.textContent = p.label;
        g.appendChild(t);
      }
      gPin.appendChild(g);
    });
  }

  /* ---------------------------------------------------------- 面板与卡片 */
  function isOpen() { return $("mapPanel").classList.contains("open"); }

  function openPanel(focusSearch) {
    $("mapPanel").classList.add("open");
    $("mapPanel").setAttribute("aria-hidden", "false");
    setTimeout(applyView, 260);      // 等滑入动画结束再算尺寸
    if (focusSearch) setTimeout(() => $("mapSearch").focus(), 300);
  }
  function closePanel() {
    $("mapPanel").classList.remove("open");
    $("mapPanel").setAttribute("aria-hidden", "true");
  }
  function togglePanel(focusSearch) {
    isOpen() ? closePanel() : openPanel(focusSearch);
  }

  /* ------------------------------------------------------------ 本地搜索
     不消耗模型调用:面板里直接输入"图书馆""E栋""食堂"就能定位。
     命中多个就列出来让用户自己挑(与后端的"不猜"口径一致)。 */
  function searchItems(query) {
    const q = normalize(query);
    if (!q) return [];
    const scored = [];
    (data.items || []).forEach((it) => {
      const names = [it.name, ...(it.aliases || [])].map(normalize);
      const cat = normalize(it.category);
      // 只按「名称/别名/类别」匹配:区域描述(area)里带别的楼名,
      // 拿它做匹配会让搜"图书馆"把"图书馆东侧的学思苑F"也算进来。
      let rank = 0;
      if (names.some((n) => n === q)) rank = 3;
      else if (cat === q) rank = 2;
      else if (names.some((n) => n.startsWith(q))) rank = 2;
      else if (names.some((n) => n.includes(q))) rank = 1;
      else if (cat.includes(q)) rank = 1;
      if (rank) scored.push({ it, rank });
    });
    return scored.sort((a, b) => b.rank - a.rank || a.it.no - b.it.no).map((s) => s.it);
  }

  function renderSearchResults(query) {
    const q = String(query || "").trim();
    if (!q) {
      $("mapList").style.display = "none";
      $("mapSearchInfo").textContent = "";
      return;
    }
    const hits = searchItems(q);
    if (!hits.length) {
      $("mapList").style.display = "none";
      $("mapSearchInfo").textContent = `没搜到「${q}」。可试:楼名(图书馆)、俗称(E栋、s5)、类别(食堂)`;
      return;
    }
    $("mapSearchInfo").textContent = hits.length === 1
      ? `命中 1 处:${hits[0].name}(回车或点列表定位)`
      : `命中 ${hits.length} 处,点一行定位`;
    renderList({ title: `搜索:${q}`, items: hits });
  }

  function runSearch() {
    const q = $("mapSearch").value;
    const hits = searchItems(q);
    if (!hits.length) return;
    boxes = hits.length === 1 ? [hits[0]] : hits;
    boxColor = "orange";
    drawBoxes();
    const bb = bboxOfItems(boxes);
    if (bb) fitTo(bb);
    $("mapSub").textContent = hits.length === 1
      ? `${hits[0].name} · ${hits[0].category || ""} · ${hits[0].area || ""}`
      : `${hits.length} 处:${hits.map((h) => h.name).join("、")}`;
    renderCard(hits.length === 1 ? [hits[0]] : hits.slice(0, 6), hits.length > 1);
    renderNotice(null);
  }

  function renderNotice(ev) {
    const bar = $("mapNotice");
    if (!ev) { bar.style.display = "none"; return; }
    bar.className = "map-notice " + (ev.level === "warn" ? "warn" : "info");
    bar.textContent = (ev.title ? ev.title + ":" : "") + (ev.text || "");
    bar.style.display = "block";
  }

  /* 卡片缩略图:用裁切窗把标注图对到目标框附近。
     交付图是"整图 + 一个红框",直接缩到卡片尺寸红框就看不清了;
     这里不改图片像素,只是把 display 视口移到框上(点开仍是完整标注图)。 */
  function cropFig(it, boxW, boxH) {
    const b = (it.bbox || []).map(num);
    const fig = document.createElement("figure");
    fig.className = "mc-fig";
    const win = document.createElement("div");
    win.className = "mc-crop";
    win.style.width = boxW + "px";
    win.style.height = boxH + "px";
    const img = document.createElement("img");
    img.src = it.image;
    img.alt = it.name || "";
    img.loading = "lazy";
    if (b.length === 4) {
      const bw = Math.max(1, b[2] - b[0]), bh = Math.max(1, b[3] - b[1]);
      // 1.8 倍留白:目标框约占窗口 55%,四周还能看到邻近建筑当参照
      const scale = Math.min(boxW / (bw * 1.8), boxH / (bh * 1.8));
      img.style.width = (W() * scale) + "px";
      img.style.height = (H() * scale) + "px";
      img.style.left = (boxW / 2 - ((b[0] + b[2]) / 2) * scale) + "px";
      img.style.top = (boxH / 2 - ((b[1] + b[3]) / 2) * scale) + "px";
    }
    win.appendChild(img);
    win.addEventListener("click", () => lightbox(it));
    const cap = document.createElement("figcaption");
    cap.textContent = `${it.name}｜${it.category || ""}`;
    fig.appendChild(win);
    fig.appendChild(cap);
    return fig;
  }

  /* 单栋标注图卡片:一张大图(点开看完整标注图);多张时排一行并提示需澄清 */
  function renderCard(items, ask) {
    const card = $("mapCard");
    const list = items || [];
    if (!list.length) { card.style.display = "none"; return; }
    card.innerHTML = "";
    if (ask) {
      const tip = document.createElement("div");
      tip.className = "mc-ask";
      tip.textContent = "你说的可能指这几栋,请确认是哪一栋";
      card.appendChild(tip);
    }
    const strip = document.createElement("div");
    strip.className = "mc-strip";
    const wide = list.length === 1 ? 320 : 186;
    const high = list.length === 1 ? 150 : 96;
    list.forEach((it) => strip.appendChild(cropFig(it, wide, high)));
    card.appendChild(strip);

    const first = list[0];
    const meta = document.createElement("div");
    meta.className = "mc-meta";
    meta.innerHTML = `<b></b><span class="mc-area"></span>`;
    meta.querySelector("b").textContent = list.length > 1
      ? `${list.length} 处候选` : (first.name || "");
    meta.querySelector(".mc-area").textContent = first.area || "";
    card.appendChild(meta);

    // 邻近建筑做成可点的:从"图书馆"顺手逛到"学思苑F/体育综合馆"
    if (list.length === 1 && (first.nearby || []).length) {
      const near = document.createElement("div");
      near.className = "mc-near";
      near.appendChild(document.createTextNode("附近还有:"));
      (first.nearby || []).forEach((name) => {
        const b = document.createElement("button");
        b.className = "mc-link";
        b.textContent = name;
        b.title = `跳到 ${name}`;
        b.addEventListener("click", () => {
          const hits = searchItems(name);
          if (hits.length) { focusItem(hits[0]); renderSearchResults($("mapSearch").value); }
        });
        near.appendChild(b);
      });
      card.appendChild(near);
    }

    // 复制一句能直接发群里/发给同学的位置说明
    const acts = document.createElement("div");
    acts.className = "mc-acts";
    const copy = document.createElement("button");
    copy.className = "mc-copy";
    copy.textContent = "⧉ 复制位置说明";
    copy.addEventListener("click", async () => {
      const text = list.length === 1
        ? `${first.name}(${first.category || ""}):${first.area || ""}` +
          `;邻近:${(first.nearby || []).join("、") || "—"}(见校园地图框选)`
        : `${list.map((i) => i.name).join("、")} —— 「${$("mapSearch").value}」在地图上可能指这几处,需确认`;
      const ok = await copyText(text);
      copy.textContent = ok ? "✓ 已复制" : "复制失败";
      setTimeout(() => { copy.textContent = "⧉ 复制位置说明"; }, 1600);
    });
    acts.appendChild(copy);
    card.appendChild(acts);

    const hint = document.createElement("div");
    hint.className = "mc-hint";
    hint.textContent = "点标注图看完整大图(整张手绘图 + 该栋红框)";
    card.appendChild(hint);
    card.style.display = "block";
  }

  /* 复制:与聊天区同一套兜底(navigator.clipboard 失败时用 execCommand) */
  async function copyText(t) {
    try { await navigator.clipboard.writeText(t); return true; }
    catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        return ok;
      } catch (_) { return false; }
    }
  }

  function renderList(ev) {
    const box = $("mapList");
    const items = ev.items || [];
    box.innerHTML = `<div class="ml-head"></div>`;
    const head = box.querySelector(".ml-head");
    head.textContent = `${ev.title || "地图标注"}(${items.length})`;
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "ml-row";
      row.innerHTML = `<span class="ml-n"></span><span class="ml-z"></span>
                       <span class="ml-i"></span><button class="ml-v" title="看这栋的标注图">看图</button>`;
      row.querySelector(".ml-n").textContent = it.name || "";
      row.querySelector(".ml-z").textContent = it.category || "";
      row.querySelector(".ml-i").textContent = it.area || "";
      row.addEventListener("click", () => focusItem(it));
      row.querySelector(".ml-v").addEventListener("click", (e) => {
        e.stopPropagation();
        lightbox(it);
      });
      box.appendChild(row);
    });
    box.style.display = "block";
  }

  function focusItem(it) {
    boxes = [it];
    boxColor = "orange";
    drawBoxes();
    const bb = bboxOfItems([it]);
    if (bb) fitTo(bb);
    $("mapSub").textContent = `${it.name} · ${it.category || ""} · ${it.area || ""}`;
    renderCard([it], false);
    renderNotice(null);
  }

  /* ------------------------------------------------------------ 灯箱 */
  function lightbox(it) {
    $("mapLightImg").src = it.image;
    $("mapLightImg").alt = it.name || "";
    $("mapLightCap").textContent =
      `${it.name}（No.${it.no}）· ${it.category || ""} · ${it.area || ""}`;
    $("mapLightbox").classList.add("show");
  }
  function closeLightbox() { $("mapLightbox").classList.remove("show"); }

  /* ------------------------------------------------------------ 对外接口 */
  function apply(ev) {
    if (!ev || !svg) return;
    openPanel();
    if (ev.op === "focus") {
      $("mapSearch").value = "";
      $("mapSearchInfo").textContent = "";
      activeCat = "";
      renderLegend();
      boxes = ev.items || [];
      boxColor = ev.color || "orange";
      drawBoxes();
      const bb = bboxOfItems(boxes);
      if (bb) fitTo(bb);
      const names = boxes.map((b) => b.name).join("、");
      $("mapSub").textContent = boxes.length > 1
        ? `${boxes.length} 处:${names}` : (boxes[0] ? `${boxes[0].name} · ${boxes[0].area || ""}` : "");
      renderCard(boxes, !!ev.ask);
      renderNotice(null);
    } else if (ev.op === "list") {
      renderList(ev);
    } else if (ev.op === "notice") {
      renderNotice(ev);
      $("mapSub").textContent = ev.title || "";
    } else if (ev.op === "pin") {
      pins.push({ x: num(ev.x), y: num(ev.y), label: ev.label, color: ev.color });
      drawBoxes();
      const px = num(ev.x), py = num(ev.y);
      fitTo([px - 320, py - 320, px + 320, py + 320]);
      $("mapSub").textContent = ev.label || "";
    } else if (ev.op === "clear") {
      const scope = ev.scope || "all";
      if (scope === "all" || scope === "highlight") {
        boxes = []; activeCat = "";
        renderCard([]); renderNotice(null); renderLegend();
        $("mapSearch").value = ""; $("mapSearchInfo").textContent = "";
      }
      if (scope === "all" || scope === "pin") pins = [];
      if (scope === "all" || scope === "list") $("mapList").style.display = "none";
      drawBoxes();
    }
  }

  function setData(d) {
    data = d || null;
    if (!data || !(data.items || []).length) return;
    byNo = new Map((data.items || []).map((it) => [it.no, it]));
    const stage = $("mapStage");
    stage.textContent = "";
    svg = node("svg", { class: "cm-svg" });
    gBase = node("g", {}); gHi = node("g", {}); gPin = node("g", {});
    svg.appendChild(gBase); svg.appendChild(gHi); svg.appendChild(gPin);
    stage.appendChild(svg);
    drawBase();
    drawBoxes();
    fitAll();
    renderLegend();
    bindSearch();
    bindGestures();
  }

  /* 搜索框:输入即出候选列表,回车定位第一个命中 */
  function bindSearch() {
    const box = $("mapSearch");
    box.addEventListener("input", () => renderSearchResults(box.value));
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); runSearch(); }
      if (ev.key === "Escape") { box.value = ""; renderSearchResults(""); ev.stopPropagation(); }
    });
    $("mapSearchClear").addEventListener("click", () => {
      box.value = "";
      renderSearchResults("");
      box.focus();
    });
  }

  /* 图例即筛选:点「食堂 2」只看食堂,再点一次取消 —— 用户自己探索,不用问模型 */
  let activeCat = "";
  function renderLegend() {
    const box = $("mapLegend");
    box.innerHTML = "";
    const cats = new Map();
    (data.items || []).forEach((it) => {
      const c = it.category || "其他";
      cats.set(c, (cats.get(c) || 0) + 1);
    });
    cats.forEach((n, name) => {
      const b = document.createElement("button");
      b.className = "lg-chip" + (activeCat === name ? " on" : "");
      b.textContent = `${name} ${n}`;
      b.title = activeCat === name ? "取消筛选" : `只看「${name}」`;
      b.addEventListener("click", () => {
        activeCat = activeCat === name ? "" : name;
        if (activeCat) {
          const hits = (data.items || []).filter((it) => (it.category || "其他") === activeCat);
          boxes = hits;
          boxColor = "teal";
          drawBoxes();
          const bb = bboxOfItems(hits);
          if (bb) fitTo(bb);
          $("mapSub").textContent = `只看「${activeCat}」:${hits.length} 处`;
          renderCard(hits.slice(0, 6), hits.length > 1);
          renderList({ title: activeCat, items: hits });
        } else {
          boxes = [];
          drawBoxes();
          $("mapList").style.display = "none";
          renderCard([], false);
          $("mapSub").textContent = "全览";
          fitAll();
        }
        renderLegend();
      });
      box.appendChild(b);
    });
    if (activeCat) {
      const reset = document.createElement("button");
      reset.className = "lg-chip lg-reset";
      reset.textContent = "取消筛选";
      reset.addEventListener("click", () => { activeCat = ""; boxes = []; drawBoxes();
        $("mapList").style.display = "none"; renderCard([], false); fitAll(); renderLegend(); });
      box.appendChild(reset);
    }
    const note = document.createElement("span");
    note.className = "lg-note";
    note.textContent = "位置图 · 非导航";
    note.title = data.source || "";
    box.appendChild(note);
  }

  /* -------------------------------------------------------- 缩放与拖动 */
  let dragging = null;
  function bindGestures() {
    svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = rect();
      const vb = (svg.getAttribute("viewBox") || "").split(/\s+/).map(Number);
      if (vb.length !== 4 || !r.width) return;
      const ux = vb[0] + ((ev.clientX - r.left) / r.width) * vb[2];
      const uy = vb[1] + ((ev.clientY - r.top) / r.height) * vb[3];
      const factor = ev.deltaY < 0 ? 1.18 : 1 / 1.18;
      const k2 = Math.max(0.5, Math.min(8, view.k * factor));
      const vh2 = H() / k2, vw2 = vh2 * (r.width / r.height);
      const vx2 = ux - ((ev.clientX - r.left) / r.width) * vw2;
      const vy2 = uy - ((ev.clientY - r.top) / r.height) * vh2;
      view.k = k2;
      view.cx = vx2 + vw2 / 2;
      view.cy = vy2 + vh2 / 2;
      applyView();
    }, { passive: false });

    svg.addEventListener("mousedown", (ev) => {
      const r = rect();
      const vb = (svg.getAttribute("viewBox") || "").split(/\s+/).map(Number);
      dragging = { x: ev.clientX, y: ev.clientY, r, vb };
      svg.style.cursor = "grabbing";
    });
    window.addEventListener("mousemove", (ev) => {
      if (!dragging) return;
      const dx = (ev.clientX - dragging.x) / dragging.r.width * dragging.vb[2];
      const dy = (ev.clientY - dragging.y) / dragging.r.height * dragging.vb[3];
      view.cx = dragging.vb[0] + dragging.vb[2] / 2 - dx;
      view.cy = dragging.vb[1] + dragging.vb[3] / 2 - dy;
      applyView();
    });
    window.addEventListener("mouseup", () => {
      if (dragging) { dragging = null; if (svg) svg.style.cursor = "grab"; }
    });
    svg.addEventListener("dblclick", () => { view.k = Math.min(8, view.k * 1.6); applyView(); });
  }

  window.CampusMap = {
    setData, apply, open: openPanel, close: closePanel, toggle: togglePanel,
    isOpen, fitAll, closeLightbox,
    zoom: (f) => { view.k = Math.max(0.5, Math.min(8, view.k * f)); applyView(); },
  };
})();
