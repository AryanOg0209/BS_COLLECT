/* CollectOS dashboard. Read-only. All text from the server is untrusted (it comes from imported
   spreadsheets), so it is only ever inserted with textContent / createTextNode, never as HTML. */
(() => {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
  const store = {
    get(k, d) { try { const v = localStorage.getItem("collectos." + k); return v === null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("collectos." + k, v); } catch { /* storage may be blocked */ } },
  };

  /* ---------- tiny DOM helper (builds nodes directly; never parses HTML from strings) ---------- */
  function h(tag, props = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style") for (const [sk, sv] of Object.entries(v)) el.style.setProperty(sk, sv);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    append(el, kids);
    return el;
  }
  function append(el, kids) {
    for (const k of kids.flat()) {
      if (k === null || k === undefined || k === false) continue;
      el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  /* ---------- icons ---------- */
  const ICONS = {
    dashboard: ["M3 3h7v9H3z", "M14 3h7v5h-7z", "M14 12h7v9h-7z", "M3 16h7v5H3z"],
    queue: ["M9 6h11", "M9 12h11", "M9 18h11", "M3.5 6l1 1 2-2", "M3.5 12l1 1 2-2", "M3.5 18l1 1 2-2"],
    customers: ["M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6", "M17 14.2c2.6.2 4.5 2 4.5 4.8", "M9 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z", "M17.5 6.5a2.5 2.5 0 1 1 0 5"],
    promises: ["M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M3 10h18", "M8 3v4", "M16 3v4", "M9 15l2 2 4-4"],
    reminders: ["M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M3 7l9 6 9-6"],
    trend: ["M5 20V11", "M12 20V4", "M19 20v-7", "M3 20h18"],
    settings: ["M4 6h9", "M17 6h3", "M4 12h3", "M11 12h9", "M4 18h11", "M19 18h1", "M15 4v4h2V4z", "M9 10v4h2v-4z", "M17 16v4h2v-4z"],
    search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-4-4"],
    bell: ["M6 9a6 6 0 1 1 12 0c0 6 2 7.5 2 7.5H4S6 15 6 9z", "M10 20a2 2 0 0 0 4 0"],
    calendar: ["M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M3 10h18", "M8 3v4", "M16 3v4"],
    "chevron-left": ["M15 6l-6 6 6 6"],
    "chevron-right": ["M9 6l6 6-6 6"],
    download: ["M12 4v11", "M7 11l5 5 5-5", "M5 20h14"],
    copy: ["M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1z", "M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"],
    alert: ["M12 4l9 16H3z", "M12 10v4", "M12 17.5v.5"],
    phone: ["M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v3a2 2 0 0 1-2 2A15 15 0 0 1 3 6a2 2 0 0 1 2-2z"],
    plus: ["M12 5v14", "M5 12h14"],
    disputes: ["M4 20l16-16", "M4 4h8v8H4z", "M12 12h8v8h-8z"],
    credit: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", "M9 12l2 2 4-4"],
    import: ["M12 15V4", "M7 9l5-5 5 5", "M5 20h14"],
    payments: ["M3 8h18v10H3z", "M3 12h18", "M7 16h3"],
    activity: ["M12 7v5l3 2", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"],
    print: ["M7 9V4h10v5", "M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2", "M7 14h10v6H7z"],
    chat: ["M4 5h16v11H9l-5 4z"],
    edit: ["M4 20h4L19 9l-4-4L4 16z", "M13 7l4 4"],
    check: ["M5 12l5 5 9-10"],
    link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
    insights: ["M12 3a6 6 0 0 0-3.5 10.9V17h7v-3.1A6 6 0 0 0 12 3z", "M9.5 21h5"],
    cheque: ["M3 6h18v12H3z", "M7 10h5", "M7 14h3", "M15 14h2"],
    tds: ["M6 3h9l4 4v14H6z", "M14 3v5h5", "M9.5 17l5-5", "M9.5 12h.01", "M14.5 17h.01"],
    money: ["M3 7h18v10H3z", "M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z", "M6 10v4", "M18 10v4"],
  };
  function icon(name, cls = "ico") {
    const s = svgEl("svg", { viewBox: "0 0 24 24", class: cls, fill: "none", stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    for (const d of ICONS[name] || []) s.append(svgEl("path", { d }));
    return s;
  }

  /* ---------- formatting ---------- */
  const inr2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  const fmt = (s) => (s === null || s === undefined ? "—" : "₹" + inr2.format(Number(s)));
  const fmt0 = (n) => "₹" + inr0.format(Math.round(n));
  function compact(n) {
    const a = Math.abs(n), sign = n < 0 ? "-" : "";
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
    if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(1)}k`;
    return `${sign}₹${Math.round(a)}`;
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function dmy(iso) { const [y, m, d] = iso.split("-").map(Number); return `${String(d).padStart(2, "0")}-${MONTHS[m - 1]}-${y}`; }
  function dm(iso) { const [, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]}`; }
  const pct = (s) => (s === null ? "—" : `${Math.round(Number(s) * 100)}%`);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  const reduceMotion = () => document.documentElement.classList.contains("reduce-motion") || matchMedia("(prefers-reduced-motion: reduce)").matches;
  function countUp(el, target, render) {
    if (reduceMotion() || !Number.isFinite(target)) { el.textContent = render(target); return; }
    const t0 = performance.now(), dur = 1100;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      el.textContent = render(target * e);
      if (p < 1) requestAnimationFrame(step); else el.textContent = render(target);
    };
    requestAnimationFrame(step);
  }

  /* ---------- state ---------- */
  const state = {
    data: null, session: { token: "", writable: false }, view: "dashboard", asOf: new URLSearchParams(location.search).get("as_of") || "",
    chartMode: "age", calYM: null, calSel: null, filter: "", remindFor: null, trendMetric: "overdue",
  };
  // Sidebar order. Screens registered by features.js slot in by id; ids not listed here are reachable by link only.
  const NAV_ORDER = ["dashboard", "queue", "customers", "promises", "cashflow", "shipcheck", "insights", "cheques", "tds", "disputes", "credit", "reminders", "import", "payments", "trend", "activity", "settings"];
  const NAV = [
    ["dashboard", "Dashboard", "dashboard"], ["queue", "Work Queue", "queue"], ["customers", "Customers", "customers"],
    ["promises", "Promises", "promises"], ["reminders", "Reminders", "reminders"], ["trend", "Trend", "trend"], ["settings", "Settings", "settings"],
  ];
  const PARENT = { customer: "customers", callsheet: "queue" };   // which sidebar item stays lit on a detail screen
  const BAR_TOP = ["#7ee6cb", "#a98ae8", "#c455e0", "#e03bd9", "#ff4fa3", "#8a8a99"];
  const BAR_BOT = ["#2f9c85", "#2f9c85", "#2f9c85", "#2f9c85", "#2f9c85", "#4a4a57"];
  const DONUT = ["#5ad1b0", "#8e63e0", "#b13bd6", "#e03bd9", "#ff4fa3", "#6f6f7d"];

  /* ---------- tooltip / toast ---------- */
  const tip = $("#tooltip");
  function showTip(e, title, body) {
    tip.replaceChildren(h("b", { text: title }), h("span", { text: body }));
    tip.classList.add("show");
    const pad = 16, w = tip.offsetWidth, hgt = tip.offsetHeight;
    tip.style.left = Math.min(innerWidth - w - 8, e.clientX + pad) + "px";
    tip.style.top = Math.min(innerHeight - hgt - 8, e.clientY + pad) + "px";
  }
  const hideTip = () => tip.classList.remove("show");
  function withTip(el, title, body) {
    el.addEventListener("pointermove", (e) => showTip(e, title, body));
    el.addEventListener("pointerleave", hideTip);
    el.addEventListener("focus", () => { const r = el.getBoundingClientRect(); showTip({ clientX: r.left + r.width / 2, clientY: r.top }, title, body); });
    el.addEventListener("blur", hideTip);
  }
  let toastTimer;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
  }

  /* ---------- 3D tilt + specular highlight ---------- */
  function tilt(el, max = 5) {
    el.classList.add("tilt");
    el.addEventListener("pointermove", (e) => {
      if (reduceMotion()) return;
      const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      el.classList.add("is-moving");
      el.style.setProperty("--ry", ((x - 0.5) * 2 * max).toFixed(2) + "deg");
      el.style.setProperty("--rx", ((0.5 - y) * 2 * max).toFixed(2) + "deg");
    });
    el.addEventListener("pointerleave", () => { el.classList.remove("is-moving"); el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg"); });
  }
  document.addEventListener("pointermove", (e) => {
    const g = e.target.closest && e.target.closest(".glass");
    if (!g) return;
    const r = g.getBoundingClientRect();
    g.style.setProperty("--mx", e.clientX - r.left + "px"); g.style.setProperty("--my", e.clientY - r.top + "px");
  }, { passive: true });

  /* ---------- 3D bar chart: every bar is a real cuboid ---------- */
  function niceScale(v) {
    if (v <= 0) return { step: 1, count: 1, max: 1 };
    const p = Math.pow(10, Math.floor(Math.log10(v)) - 1);
    for (const m of [1, 2, 2.5, 5, 10, 20, 25, 50, 100]) {
      const step = m * p, count = Math.ceil(v / step - 1e-9);
      if (count <= 5) return { step, count: Math.max(count, 2), max: step * Math.max(count, 2) };
    }
    return { step: v, count: 1, max: v };
  }
  function tickLabel(n) {
    if (n === 0) return "0";
    const trim = (x, d) => String(+x.toFixed(d));
    if (n >= 1e7) return `₹${trim(n / 1e7, 2)} Cr`;
    if (n >= 1e5) return `₹${trim(n / 1e5, 2)} L`;
    if (n >= 1e3) return `₹${trim(n / 1e3, 1)}k`;
    return `₹${trim(n, 0)}`;
  }
  function chart3d(items, label) {
    const wrap = h("div", { class: "chart3d", role: "img", "aria-label": label });
    const stage = h("div", { class: "stage" }), wall = h("div", { class: "wall" }), floor = h("div", { class: "floor" }), slots = h("div", { class: "slots" });
    stage.append(wall, floor, slots); wrap.append(stage);
    const scale = niceScale(Math.max(...items.map((i) => i.value), 0)), max = scale.max;
    for (let k = 0; k <= scale.count; k++) {
      wall.append(h("div", { class: "tick", style: { bottom: (k / scale.count) * 100 + "%" } }, h("span", { text: tickLabel(scale.step * k) })));
    }
    const bars = items.map((it, i) => {
      const body = h("div", { class: "bar-body" },
        h("div", { class: "face f-back" }), h("div", { class: "face f-left" }), h("div", { class: "face f-right" }),
        h("div", { class: "face f-top" }), h("div", { class: "face f-front" }));
      const bar = h("div", { class: "bar", tabindex: "0", role: "img", "aria-label": `${it.label}: ${it.exact}`, style: { "--i": i, "--c1": it.top, "--c2": it.bot } },
        body, h("div", { class: "bar-val", text: compact(it.value) }), h("div", { class: "bar-lbl", text: it.label }));
      withTip(bar, it.label, it.tip);
      slots.append(bar); return bar;
    });
    const layout = () => {
      const W = slots.clientWidth, H = stage.clientHeight;
      if (!W || !H) return;
      const slot = W / Math.max(items.length, 1), bw = Math.max(18, Math.min(58, slot * 0.52)), depth = Math.round(bw * 0.78);
      floor.style.height = depth * 2.2 + "px";
      wall.style.transform = `translateZ(${-depth * 1.1}px)`;
      floor.style.transform = `translateZ(${-depth * 1.1}px) rotateX(90deg)`;
      bars.forEach((b, i) => {
        const hgt = Math.max(items[i].value > 0 ? 6 : 2, (items[i].value / max) * H);
        b.style.left = i * slot + (slot - bw) / 2 + "px"; b.style.width = bw + "px"; b.style.height = hgt + "px";
        b.style.setProperty("--w", bw + "px"); b.style.setProperty("--d", depth + "px");
      });
    };
    new ResizeObserver(layout).observe(stage);
    requestAnimationFrame(layout);
    wrap.addEventListener("pointermove", (e) => {
      if (reduceMotion()) return;
      const r = wrap.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      stage.classList.add("is-moving");
      stage.style.transform = `rotateX(${(-13 - y * 8).toFixed(2)}deg) rotateY(${(-20 + x * 16).toFixed(2)}deg)`;
    });
    wrap.addEventListener("pointerleave", () => { stage.classList.remove("is-moving"); stage.style.transform = ""; });
    // a plain table for screen readers, since the 3D scene itself cannot be navigated meaningfully
    const table = h("table", { class: "sr-only" }, h("caption", { text: label }),
      h("tbody", {}, items.map((it) => h("tr", {}, h("th", { scope: "row", text: it.label }), h("td", { text: it.exact })))));
    return h("div", {}, wrap, table);
  }

  /* ---------- 3D donut: stacked layers give it real thickness ---------- */
  function donut3d(items, centerTop, centerBottom) {
    const R = 64, C = 2 * Math.PI * R, total = items.reduce((a, b) => a + b.value, 0);
    const layers = 8;
    const wrap = h("div", { class: "donut-wrap" });
    const d3 = h("div", { class: "donut3d", role: "img", "aria-label": `${centerBottom}: ` + items.map((i) => `${i.label} ${pct(total ? String(i.value / total) : null)}`).join(", ") });
    const arcs = [];
    for (let k = layers - 1; k >= 0; k--) {
      const svg = svgEl("svg", { viewBox: "0 0 190 190", class: "layer", "aria-hidden": "true" });
      svg.style.setProperty("--k", k);
      if (k === 0) svg.dataset.top = "";
      let cum = 0;
      items.forEach((it, i) => {
        if (!it.value || !total) return;
        const len = Math.max(0, (it.value / total) * C - 3);
        const c = svgEl("circle", { cx: 95, cy: 95, r: R, stroke: it.color, "stroke-dasharray": `${len} ${C}`, "stroke-dashoffset": -cum, transform: "rotate(-90 95 95)", class: "seg-arc draw" });
        c.style.setProperty("--i", i);
        if (k === 0) {
          c.addEventListener("pointermove", (e) => { showTip(e, it.label, it.tip); highlight(i, true); });
          c.addEventListener("pointerleave", () => { hideTip(); highlight(i, false); });
          arcs[i] = c;
        }
        svg.append(c);
        cum += (it.value / total) * C;
      });
      d3.append(svg);
    }
    const center = h("div", { class: "donut-center" }, h("strong", { class: "num", text: centerTop }), h("span", { text: centerBottom }));
    wrap.append(d3, center);
    const legend = h("ul", { class: "legend" }, items.map((it, i) =>
      h("li", { onpointerenter: () => highlight(i, true), onpointerleave: () => highlight(i, false) },
        h("i", { style: { background: it.color } }), h("span", {}, it.label, " ", h("b", { class: "num", text: total ? pct(String(it.value / total)) : "—" })))));
    function highlight(i, on) { if (arcs[i]) arcs[i].classList.toggle("hot", on); }
    d3.addEventListener("pointermove", (e) => {
      if (reduceMotion()) return;
      const r = wrap.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      d3.classList.add("is-moving");
      d3.style.transform = `rotateX(${(52 - y * 14).toFixed(1)}deg) rotateZ(${(-18 + x * 24).toFixed(1)}deg)`;
    });
    d3.addEventListener("pointerleave", () => { d3.classList.remove("is-moving"); d3.style.transform = ""; });
    return h("div", {}, wrap, legend);
  }

  /* ---------- calendar of promised dates ---------- */
  function calendar(promises, big = false) {
    const asOf = state.data.as_of, [ay, am] = asOf.split("-").map(Number);
    if (!state.calYM) state.calYM = { y: ay, m: am - 1 };
    const byDate = {};
    promises.forEach((p) => (byDate[p.date] = byDate[p.date] || []).push(p));
    const box = h("div", { class: "cal" });
    const render = () => {
      const { y, m } = state.calYM;
      const first = new Date(y, m, 1), startDow = (first.getDay() + 6) % 7, days = new Date(y, m + 1, 0).getDate();
      const head = h("div", { class: "cal-head" },
        h("button", { type: "button", "aria-label": "Previous month", onclick: () => { state.calYM = m === 0 ? { y: y - 1, m: 11 } : { y, m: m - 1 }; render(); } }, icon("chevron-left")),
        h("div", { class: "cal-title", text: `${MONTHS[m].toLowerCase()} ${y}` }),
        h("button", { type: "button", "aria-label": "Next month", onclick: () => { state.calYM = m === 11 ? { y: y + 1, m: 0 } : { y, m: m + 1 }; render(); } }, icon("chevron-right")));
      const grid = h("div", { class: "cal-grid" }, ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((d) => h("div", { class: "cal-dow", text: d })));
      for (let i = 0; i < startDow; i++) grid.append(h("div"));
      for (let d = 1; d <= days; d++) {
        const iso = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`, ps = byDate[iso] || [];
        const dots = ps.length ? h("span", { class: "dots" }, ps.slice(0, 3).map((p) => h("i", { class: p.state === "broken" ? "b" : "" }))) : null;
        const label = `${dmy(iso)}${ps.length ? ": " + plural(ps.length, "promise") : ""}`;
        grid.append(h("button", { type: "button", class: "cal-day" + (iso === asOf ? " today" : "") + (iso === state.calSel ? " sel" : ""), "aria-label": label, "aria-pressed": String(iso === state.calSel),
          onclick: () => { state.calSel = state.calSel === iso ? null : iso; render(); } }, String(d), dots));
      }
      const sel = state.calSel && byDate[state.calSel];
      const list = h("ul", { class: "cal-list" }, sel
        ? sel.map((p) => h("li", {}, custLink(p.customer), h("span", { class: "chip " + (p.state === "broken" ? "broken" : "pending"), text: p.state === "broken" ? "Broken" : "Pending" })))
        : (state.calSel ? [h("li", { class: "sub", text: "No promises on " + dmy(state.calSel) })] : []));
      const legend = h("div", { class: "legend-cal" }, h("span", {}, h("i", { style: { background: "var(--teal)" } }), "Pending"), h("span", {}, h("i", { style: { background: "var(--warn)" } }), "Broken (date passed)"));
      box.replaceChildren(...[head, grid, list, big ? null : legend].filter(Boolean));
    };
    render();
    return box;
  }

  const custHref = (name) => "#/customer/" + encodeURIComponent(name);
  const custLink = (name) => h("a", { class: "plainlink", href: custHref(name), text: name, onclick: (e) => e.stopPropagation() });

  /* ---------- tables ---------- */
  function queueTable(rows, { limit, expandable = true, compact: slim = false } = {}) {
    const shown = limit ? rows.slice(0, limit) : rows;
    if (!shown.length) return h("div", { class: "empty", text: "Nobody to chase today. Everyone is either current, waiting on a promise, or was contacted recently." });
    const maxScore = Math.max(...rows.map((r) => r.score), 1);
    const body = h("tbody");
    shown.forEach((r, i) => {
      const tr = h("tr", { class: expandable ? "row-btn" : "" });
      const reasons = h("ul", { class: "reasons" }, r.reasons.map((x) => h("li", { text: x })));
      const cells = [
        slim ? null : h("td", { class: "num", text: String(i + 1) }),
        h("td", {}, h("div", {}, custLink(r.customer)), expandable ? reasons : null,
          expandable ? h("div", { class: "rowactions" },
            writable() ? h("button", { type: "button", class: "btn small ghost", onclick: (e) => { e.stopPropagation(); openFollowup(r.customer); } }, icon("phone"), "Log follow-up") : null,
            h("button", { type: "button", class: "btn small", onclick: (e) => { e.stopPropagation(); CO.openSend && CO.openSend(r.customer); } }, icon("reminders"), "Send reminder"),
            h("a", { class: "btn small ghost", href: custHref(r.customer), onclick: (e) => e.stopPropagation() }, icon("customers"), "Open customer")) : null,
          slim && r.broken_promise_dates.length ? h("span", { class: "chip broken", style: { "margin-top": "4px" }, text: "Broken promise" }) : null),
        h("td", {}, h("span", { class: "chip " + (r.action === "Call" ? "call" : "remind"), text: r.action })),
        slim ? null : h("td", {}, h("span", { class: "chip stagechip s" + r.stage, title: r.stage_why, text: `${r.stage} · ${r.stage_label}` })),
        slim ? null : h("td", {}, h("span", { class: "scorebar" }, h("i", { style: { "--w": (r.score / maxScore) * 100 + "%" } })), h("span", { class: "num", text: String(r.score) })),
        h("td", { class: "r num", text: slim ? compact(Number(r.overdue)) : fmt(r.overdue), title: r.overdue }),
        h("td", { class: "r num", text: r.oldest_days + "d" }),
        slim ? null : h("td", { class: "r" }, r.broken_promise_dates.length ? h("span", { class: "chip broken", text: "Broken promise" }) : h("span", { class: "sub", text: r.days_since_contact === null ? "Never contacted" : r.days_since_contact + "d ago" })),
      ];
      tr.append(...cells.filter(Boolean));
      if (expandable) {
        tr.setAttribute("tabindex", "0"); tr.setAttribute("aria-expanded", "false");
        tr.addEventListener("click", () => { tr.classList.toggle("open"); tr.setAttribute("aria-expanded", String(tr.classList.contains("open"))); });
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tr.click(); } });
      }
      body.append(tr);
    });
    const cols = slim ? ["Customer", "Action", "Overdue", "Oldest"] : ["#", "Customer", "Action", "Stage", "Score", "Overdue", "Oldest", "Contact"];
    const right = new Set(slim ? ["Overdue", "Oldest"] : ["Overdue", "Oldest", "Contact"]);
    const head = h("tr", {}, cols.map((t) => h("th", { scope: "col", class: right.has(t) ? "r" : "", text: t })));
    return h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, head), body));
  }

  /* ---------- actions: guarded writes through a modal ---------- */
  const writable = () => state.session.writable;
  const actor = () => store.get("sender", "") || "dashboard";
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CollectOS-Token": state.session.token }, body: JSON.stringify(body) });
    let j = {}; try { j = await r.json(); } catch { /* non-JSON error body */ }
    if (!r.ok) throw new Error(j.error || `request failed (${r.status})`);
    return j;
  }
  const dlg = $("#modal");
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  function openModal({ title, intro, fields, submit, onSubmit, danger = false, wide = false, quiet = false }) {
    dlg.classList.toggle("wide", wide);
    const err = h("div", { class: "form-error", role: "alert", hidden: true });
    const go = h("button", { type: "submit", class: "btn" + (danger ? " bad" : ""), text: submit });
    const form = h("form", { novalidate: true }, h("h2", { id: "modalTitle", text: title }), intro ? h("p", { class: "sub", text: intro }) : null, fields, err,
      h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Cancel", onclick: () => dlg.close() }), go));
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); err.hidden = true; go.disabled = true;
      try { const msg = await onSubmit(new FormData(form)); dlg.close(); toast(msg || "Saved"); await load(quiet); }
      catch (ex) { err.textContent = ex.message; err.hidden = false; go.disabled = false; }
    });
    dlg.replaceChildren(h("div", { class: "modal-inner" }, form));
    dlg.showModal();
    const first = $("input:not([type=hidden]), select, textarea", form); if (first && !first.value) first.focus();
  }
  const field = (label, control, hint) => h("div", { class: "field" }, h("label", { for: control.id, text: label }), control, hint ? h("span", { class: "hint", text: hint }) : null);

  function openFollowup(customer = "", prefill = {}) {
    if (!writable()) return;
    const names = (state.data ? state.data.customers : []).map((c) => c.customer);
    const cust = h("input", { id: "f-cust", name: "customer", list: "f-cust-list", value: customer, placeholder: "Start typing a customer name", autocomplete: "off", required: true });
    const channel = h("select", { id: "f-chan", name: "channel" }, ...["call", "whatsapp", "email", "visit", "other"].map((c) => h("option", { value: c, text: c[0].toUpperCase() + c.slice(1), selected: c === prefill.channel })));
    const outcome = h("input", { id: "f-out", name: "outcome", placeholder: "e.g. Said he will pay on Friday", required: true, value: prefill.outcome || "" });
    const note = h("textarea", { id: "f-note", name: "note", placeholder: "Optional details" }); note.value = prefill.note || "";
    const promised = h("input", { type: "checkbox", id: "f-prom" });
    const date = h("input", { type: "date", id: "f-date", name: "promise_date", min: todayISO() });
    const amount = h("input", { id: "f-amt", name: "promise_amount", placeholder: "Optional, e.g. 50,000", inputmode: "decimal" });
    const box = h("div", { class: "promise-box", hidden: true }, field("Promised date", date), field("Promised amount (₹)", amount));
    promised.addEventListener("change", () => { box.hidden = !promised.checked; if (promised.checked) date.focus(); });
    openModal({
      title: "Log a follow-up", intro: "Record what happened on this contact. If the customer promised a date, note it so it can be tracked.",
      fields: [h("datalist", { id: "f-cust-list" }, names.map((n) => h("option", { value: n }))), field("Customer", cust),
        CO.extra.followupAssist ? CO.extra.followupAssist({ cust, channel, outcome, note, promised, date, amount, box }) : null,
        field("How did you contact them?", channel), field("What happened?", outcome), field("Notes", note),
        h("label", { class: "checkrow" }, promised, h("span", { text: "They promised to pay by a date" })), box],
      submit: "Save follow-up",
      onSubmit: async () => {
        if (promised.checked && !date.value) throw new Error("Choose the date they promised to pay by.");
        const body = { customer: cust.value, channel: channel.value, outcome: outcome.value, note: note.value, actor: actor() };
        if (promised.checked) { body.promise_date = date.value; if (amount.value.trim()) body.promise_amount = amount.value; }
        const j = await post("/api/followup", body);
        return j.promise_id ? `Follow-up saved. Promise recorded for ${dmy(date.value)}.` : "Follow-up saved.";
      },
    });
  }
  function openResolve(p, status) {
    if (!writable()) return;
    const note = h("textarea", { id: "r-note", name: "note", placeholder: "Optional, e.g. NEFT received on 14th" });
    const word = { kept: "kept", broken: "broken", cancelled: "cancelled" }[status];
    openModal({
      title: `Mark promise as ${word}?`, danger: status !== "kept",
      intro: `${p.customer} promised to pay by ${dmy(p.date)}${p.amount ? " (" + fmt(p.amount) + ")" : ""}. This closes the promise and cannot be undone; the change is recorded in the audit log.`,
      fields: [field("Note", note)], submit: `Mark ${word}`,
      onSubmit: async () => { await post("/api/promise/resolve", { id: p.id, status, note: note.value, actor: actor() }); return `Promise marked ${word}.`; },
    });
  }
  function openSnapshot() {
    if (!writable()) return;
    const d = state.data;
    openModal({
      title: "Take a snapshot", intro: `Record today's measures as of ${dmy(d.as_of)} so they can be compared week by week. Snapshots are permanent; taking another for the same date supersedes it without deleting history.`,
      fields: [], submit: "Take snapshot",
      onSubmit: async () => { const j = await post("/api/snapshot", { as_of: d.as_of, actor: actor() }); return `Snapshot saved. Payments seen since the last one: ${fmt(j.payments_observed)}.`; },
    });
  }

  /* ---------- views ---------- */
  function emptyState() {
    return h("div", { class: "card glass reveal c12", style: { "--i": 0 } }, h("div", { class: "empty" }, h("div", {},
      h("h2", { text: "No invoices yet" }),
      h("p", { class: "sub", text: "Import an outstanding-invoices export to see your receivables here:" }),
      h("p", { class: "num", text: "collectos import export.xlsx --db your.db" }))));
  }

  function viewDashboard(d) {
    const k = d.kpi, root = h("div");
    root.append(viewHead("Receivables overview", `As of ${dmy(d.as_of)} · ${d.db_name}`));
    if (!k.open_invoices) { root.append(h("div", { class: "grid" }, emptyState())); return root; }
    const grid = h("div", { class: "grid" });
    const kpi = (i, cls, label, ico, hint, renderTarget, extraCls = "") => {
      const v = h("div", { class: "value num " + extraCls }), t = h("div", { class: "kpi glass reveal c3 " + cls, style: { "--i": i } },
        h("div", { class: "label" }, icon(ico), label), v, h("div", { class: "hint", text: hint }));
      tilt(t, 6); countUp(v, renderTarget.n, renderTarget.f); return t;
    };
    grid.append(
      kpi(0, "", "Overdue", "alert", `${pct(k.overdue_share)} of outstanding · ${plural(k.overdue_invoices, "invoice")}`, { n: Number(k.overdue), f: fmt0 }),
      kpi(1, "", "Total outstanding", "money", `${plural(k.open_invoices, "open invoice")} · ${plural(k.customers_overdue, "customer")} overdue`, { n: Number(k.total_outstanding), f: fmt0 }),
      kpi(2, "", "To contact today", "phone", `${k.waiting} waiting · ${d.review.length} promise${d.review.length === 1 ? "" : "s"} to review`, { n: k.to_contact_today, f: (n) => String(Math.round(n)) }, "plain"),
      kpi(3, k.broken_promises ? "alert" : "", "Broken promises", "promises", k.broken_promises ? "Promised date passed, not marked kept" : "None right now", { n: k.broken_promises, f: (n) => String(Math.round(n)) }, k.broken_promises ? "" : "plain"),
    );

    if (CO.extra.dashboardTop) grid.append(...CO.extra.dashboardTop(d));

    const chartHost = h("div");
    const drawChart = () => {
      chartHost.replaceChildren(state.chartMode === "age" ? ageChart(d) : trendChart(d, "overdue", false));
    };
    const toggle = h("div", { class: "seg", role: "group", "aria-label": "Chart view" },
      ...[["age", "By age"], ["trend", "Weekly trend"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(state.chartMode === id), text: t,
        onclick: (e) => { state.chartMode = id; $$("button", e.currentTarget.parentElement).forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); drawChart(); } })));
    const chartCard = h("section", { class: "card glass reveal c8 r-c8", style: { "--i": 4 } },
      h("div", { class: "card-head" }, h("h2", { text: "Where the overdue money sits" }), toggle), chartHost);
    drawChart();
    const calCard = h("section", { class: "card glass reveal c4 r-c4", style: { "--i": 5 } }, h("h2", { text: "Promised payments" }), calendar(d.promises));
    tilt(calCard, 2.5);

    const dItems = d.ageing.filter((a) => Number(a.amount) > 0 || a.bucket !== "No due date").map((a, i) => ({ label: a.bucket === "Current" ? "Not due" : a.bucket, value: Number(a.amount), color: DONUT[d.ageing.indexOf(a)], tip: `${fmt(a.amount)} · ${plural(a.count, "invoice")}` }));
    const donutCard = h("section", { class: "card glass fill reveal c3 r-c4", style: { "--i": 6 } }, h("h2", { text: "Outstanding by age" }),
      h("div", { class: "grow" }, donut3d(dItems, compact(Number(k.total_outstanding)), "outstanding")));
    tilt(donutCard, 3);
    const queueCard = h("section", { class: "card glass reveal c6 r-c8", style: { "--i": 7 } },
      h("div", { class: "card-head" }, h("h2", { text: "Today's queue" }), h("button", { type: "button", class: "linkbtn", text: "View all", onclick: () => go("queue") })),
      queueTable(d.queue, { limit: 6, expandable: false, compact: true }));
    const top = d.customers.filter((c) => Number(c.overdue) > 0).slice(0, 4), topMax = Math.max(...top.map((c) => Number(c.overdue)), 1);
    const moneyCard = h("section", { class: "card glass money reveal c3 r-c12", style: { "--i": 8 } },
      h("h2", { text: "Overdue now" }),
      h("div", { class: "big" }, h("strong", { class: "num", text: fmt0(Number(k.overdue)) }), h("span", { text: `${plural(k.customers_overdue, "customer")} · ${plural(k.overdue_invoices, "invoice")}` })),
      top.length ? h("div", {}, h("div", { class: "eyebrow", text: "Biggest exposures" }), h("ul", { class: "expo" }, top.map((c) => h("li", { title: fmt(c.overdue) },
        h("span", { text: c.customer }), h("span", { class: "num", text: compact(Number(c.overdue)) }),
        h("span", { class: "scorebar" }, h("i", { style: { "--w": (Number(c.overdue) / topMax) * 100 + "%" } })))))) : null,
      h("a", { class: "btn", href: reportUrl(), download: "" }, icon("download"), "Download owner report"),
      h("button", { type: "button", class: "linkbtn", style: { "margin-top": "12px", "align-self": "center" }, text: "Open reminder drafts", onclick: () => go("reminders") }));
    tilt(moneyCard, 4);
    grid.append(chartCard, calCard, donutCard, queueCard, moneyCard);
    root.append(grid);
    return root;
  }

  function ageChart(d) {
    const items = d.ageing.filter((a) => a.bucket !== "No due date" || a.count > 0).map((a, i) => ({
      label: a.bucket === "Current" ? "Not due" : a.bucket, value: Number(a.amount), exact: fmt(a.amount),
      tip: `${fmt(a.amount)} across ${plural(a.count, "invoice")}`, top: BAR_TOP[d.ageing.indexOf(a)], bot: BAR_BOT[d.ageing.indexOf(a)],
    }));
    return chart3d(items, "Outstanding amount by days overdue");
  }
  const TREND_METRICS = { overdue: ["Overdue", "overdue"], over60: ["Over 60 days", "over60"], over90: ["Over 90 days", "over90"], payments: ["Payments seen in data", "payments_observed"] };
  function trendChart(d, metric) {
    if (!d.trend.length) return h("div", { class: "empty", text: "No snapshots yet. Run `collectos snapshot` after your first import to record the baseline, then weekly." });
    const [name, key] = TREND_METRICS[metric];
    const items = d.trend.map((s, i) => ({ label: dm(s.as_of), value: Number(s[key]), exact: fmt(s[key]), tip: `${name}: ${fmt(s[key])} on ${dmy(s.as_of)}`,
      top: BAR_TOP[Math.min(i, 4)], bot: BAR_BOT[0] }));
    return chart3d(items, `${name} by week`);
  }

  function viewHead(title, sub, extra) {
    return h("div", { class: "view-head" }, h("div", {}, h("h1", { text: title }), h("p", { text: sub })), extra);
  }

  function viewQueue(d) {
    const card = h("section", { class: "card glass reveal c12", style: { "--i": 0 } },
      queueTable(d.queue, { expandable: true }),
      d.waiting.length ? h("div", {}, h("h2", { text: "Waiting (not chasing today)", style: { "text-align": "left", margin: "26px 0 8px" } }),
        h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Customer", "Overdue", "Why"].map((t, i) => h("th", { scope: "col", class: i === 1 ? "r" : "", text: t })))),
          h("tbody", {}, d.waiting.map((w) => h("tr", {}, h("td", {}, custLink(w.customer)), h("td", { class: "r num", text: fmt(w.overdue) }), h("td", {}, h("span", { class: "chip muted", text: w.reason })))))))) : null,
      d.review.length ? h("div", { class: "note", style: { "margin-top": "20px" } }, "Promises to review: ", d.review.map((r) => `${r.customer} (${r.reason})`).join("; ")) : null);
    return h("div", {}, viewHead("Work queue", "Everyone to follow up today, with the rules behind each score. Click a row for the reasons.",
      h("a", { class: "btn small ghost", href: "#/callsheet" }, icon("print"), "Call sheet")), h("div", { class: "grid" }, card));
  }

  function viewCustomers(d) {
    const buckets = ["Current", "1-30", "31-60", "61-90", "91+", "No due date"];
    const maxes = buckets.map((b) => Math.max(...d.customers.map((c) => Number(c.buckets[b] || 0)), 1));
    const input = h("input", { type: "search", placeholder: "Filter customers…", "aria-label": "Filter customers", value: state.filter, style: { "max-width": "320px", height: "44px", "border-radius": "12px", border: "1px solid var(--line)", background: "rgba(0,0,0,.28)", padding: "0 14px" } });
    const body = h("tbody");
    const paint = () => {
      const q = state.filter.trim().toLowerCase();
      const rows = d.customers.filter((c) => !q || c.customer.toLowerCase().includes(q));
      body.replaceChildren(...rows.map((c) => h("tr", {},
        h("td", {}, custLink(c.customer), c.do_not_contact ? h("span", { class: "chip muted", style: { "margin-left": "8px" }, text: "Do not contact" }) : null), h("td", { class: "r num", text: fmt(c.outstanding) }), h("td", { class: "r num", text: fmt(c.overdue) }),
        ...buckets.map((b, i) => { const v = Number(c.buckets[b] || 0); return h("td", { class: "r" }, v ? h("span", { class: "heat num", text: compact(v), title: fmt(String(v)), style: { "--a": (0.08 + 0.5 * (v / maxes[i])).toFixed(2) } }) : h("span", { class: "sub", text: "–" })); }),
        h("td", { class: "r num", text: String(c.open_invoices) }), h("td", { class: "r num", text: Number(c.overdue) > 0 ? c.oldest_days + "d" : "–" }))));
      if (!rows.length) body.append(h("tr", {}, h("td", { colspan: "11", class: "empty", text: "No customers match." })));
    };
    input.addEventListener("input", () => { state.filter = input.value; paint(); });
    paint();
    const head = h("tr", {}, ["Customer", "Outstanding", "Overdue", ...buckets.map((b) => (b === "Current" ? "Not due" : b)), "Invoices", "Oldest"].map((t, i) => h("th", { scope: "col", class: i ? "r" : "", text: t })));
    return h("div", {}, viewHead("Customers", "Open balances by age. Darker cells hold more money.", input),
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c12", style: { "--i": 0 } }, h("div", { class: "tablewrap" }, h("table", { class: "customers" }, h("thead", {}, head), body)))));
  }

  function viewPromises(d) {
    const rows = d.promises.map((p) => h("tr", {}, h("td", {}, custLink(p.customer)), h("td", { class: "num", text: dmy(p.date) }), h("td", { class: "r num", text: p.amount ? fmt(p.amount) : "–" }),
      h("td", {}, h("span", { class: "chip " + (p.state === "broken" ? "broken" : "pending"), text: p.state === "broken" ? "Broken" : "Pending", title: p.state === "broken" ? "The promised date has passed and it was not marked kept" : "The promised date has not arrived yet" })),
      writable() ? h("td", { class: "r" }, h("div", { class: "cellactions" },
        h("button", { type: "button", class: "btn small good", "aria-label": `Mark ${p.customer} promise kept`, text: "Kept", onclick: () => openResolve(p, "kept") }),
        h("button", { type: "button", class: "btn small bad", "aria-label": `Mark ${p.customer} promise broken`, text: "Broken", onclick: () => openResolve(p, "broken") }),
        h("button", { type: "button", class: "btn small ghost", "aria-label": `Cancel ${p.customer} promise`, text: "Cancel", onclick: () => openResolve(p, "cancelled") }))) : null));
    const heads = ["Customer", "Promised date", "Amount", "State", ...(writable() ? [""] : [])];
    const list = d.promises.length ? h("div", { class: "tablewrap" }, h("table", { class: "promises" }, h("thead", {}, h("tr", {}, heads.map((t, i) => h("th", { scope: "col", class: i === 2 || i === 4 ? "r" : "", text: t || "Actions" })))), h("tbody", {}, rows)))
      : h("div", { class: "empty", text: writable() ? "No open promises. When a customer promises a date, use “Log follow-up” and tick “They promised to pay by a date”." : "No open promises." });
    return h("div", {}, viewHead("Promises", "Every open promise to pay. A promise turns 'broken' the day after its date if it isn't marked kept."),
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c4 r-c12", style: { "--i": 0 } }, calendar(d.promises, true)), h("section", { class: "card glass reveal c8 r-c12", style: { "--i": 1 } }, list)));
  }

  function viewTrend(d) {
    const host = h("div"), draw = () => host.replaceChildren(trendChart(d, state.trendMetric));
    const sel = h("select", { class: "pillselect", "aria-label": "Measure", onchange: (e) => { state.trendMetric = e.target.value; draw(); } },
      ...Object.entries(TREND_METRICS).map(([id, [t]]) => h("option", { value: id, text: t, selected: id === state.trendMetric })));
    draw();
    const rows = d.trend.map((s) => h("tr", {}, h("td", { class: "num", text: dmy(s.as_of) }), h("td", { class: "r num", text: fmt(s.total_outstanding) }), h("td", { class: "r num", text: fmt(s.overdue) }),
      h("td", { class: "r num", text: fmt(s.over60) }), h("td", { class: "r num", text: fmt(s.over90) }), h("td", { class: "r num", text: String(s.followups_7d) }), h("td", { class: "r num", text: String(s.broken_promises) }), h("td", { class: "r num", text: fmt(s.payments_observed) })));
    const table = d.trend.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["As of", "Outstanding", "Overdue", "Over 60d", "Over 90d", "Follow-ups (7d)", "Broken", "Payments seen"].map((t, i) => h("th", { scope: "col", class: i ? "r" : "", text: t })))), h("tbody", {}, rows))) : null;
    return h("div", {}, viewHead("Trend", "Weekly snapshots, recorded the same way every time."),
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c12", style: { "--i": 0 } }, h("div", { class: "card-head" }, h("h2", { text: "Week by week" }), h("div", { style: { display: "flex", gap: "10px", "align-items": "center" } }, writable() ? h("button", { type: "button", class: "btn small", onclick: openSnapshot }, icon("trend"), "Take snapshot now") : null, sel)), host),
        d.trend.length ? h("section", { class: "card glass reveal c12", style: { "--i": 1 } }, table, h("p", { class: "sub", style: { "margin-top": "14px" }, text: "These numbers describe what the data showed. They do not prove that any change was caused by follow-up work. 'Payments seen' counts only increases in paid amounts that arrived through imports." })) : null));
  }

  function viewSettings(d) {
    const nameIn = h("input", { id: "set-name", value: store.get("sender", ""), placeholder: "Your name" }), coIn = h("input", { id: "set-co", value: store.get("company", ""), placeholder: "Your company" });
    const dateIn = h("input", { id: "set-date", type: "date", value: d.as_of });
    const rm = h("input", { type: "checkbox", id: "set-rm", checked: store.get("reduce", "0") === "1" });
    rm.addEventListener("change", () => { store.set("reduce", rm.checked ? "1" : "0"); applyMotion(); });
    const msme = h("input", { type: "checkbox", id: "set-msme", checked: d.settings.msme_supplier });
    msme.addEventListener("change", async () => {
      try { await post("/api/setting", { key: "msme_supplier", value: msme.checked ? "1" : "0", actor: actor() }); toast("Saved"); await load(); }
      catch (e) { msme.checked = !msme.checked; toast(e.message); }
    });
    const lang = h("select", { id: "set-lang", onchange: (e) => { store.set("lang", e.target.value); toast("Saved"); } },
      h("option", { value: "en", text: "English", selected: store.get("lang", "en") === "en" }), h("option", { value: "hinglish", text: "Hinglish (Hindi in Roman letters)", selected: store.get("lang", "en") === "hinglish" }));
    return h("div", {}, viewHead("Settings", "Saved in this browser only."),
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c6 r-c12", style: { "--i": 0 } }, h("h2", { text: "Drafts and date", style: { "text-align": "left", "margin-bottom": "16px" } }),
        h("div", { class: "field" }, h("label", { for: "set-name", text: "Your name (signs reminder drafts)" }), nameIn),
        h("div", { class: "field" }, h("label", { for: "set-co", text: "Your company" }), coIn),
        h("div", { class: "field" }, h("label", { for: "set-date", text: "Show the position as of" }), dateIn),
        h("button", { type: "button", class: "btn", text: "Save", onclick: () => {
          store.set("sender", nameIn.value.trim()); store.set("company", coIn.value.trim()); state.asOf = dateIn.value && dateIn.value !== todayISO() ? dateIn.value : "";
          updateAvatar(); toast("Saved"); load();
        } }),
        h("div", { class: "field", style: { "margin-top": "22px" } }, h("label", { for: "set-rm" }, rm, " Reduce motion (turns off 3D movement and animation)"))),
      h("section", { class: "card glass reveal c6 r-c12", style: { "--i": 1 } }, h("h2", { text: "Your data", style: { "text-align": "left", "margin-bottom": "16px" } }),
        h("div", { class: "note", text: state.session.writable
          ? "This page is served from this computer only. The only changes it can make are the ones you save (imports, follow-ups, promise outcomes, snapshots, notes); they go into your local database and audit log. Nothing is uploaded, and no message is ever sent to a customer."
          : "This dashboard is running in read-only mode and is served from this computer only. Nothing is uploaded, and no message is sent to any customer." }),
        h("p", { class: "sub", text: `Database: ${d.db_name}` }),
        h("div", { style: { display: "flex", gap: "10px", "flex-wrap": "wrap", margin: "14px 0" } },
          h("a", { class: "btn small", href: "/backup.db", download: "" }, icon("download"), "Download a backup copy"),
          h("button", { type: "button", class: "btn small ghost", onclick: () => go("activity") }, icon("activity"), "See everything that changed")),
        h("p", { class: "sub", text: "A backup is the whole database as one file. It contains your customers' details, so keep it as private as the original exports." }),
        writable() ? h("div", { class: "field", style: { "margin-top": "22px" } }, h("label", { for: "set-msme" }, msme, " I am a registered micro or small enterprise (Udyam). Mark invoices older than 45 days."),
          h("span", { class: "hint", text: d.settings.msme_caution })) : null,
        h("div", { class: "field", style: { "margin-top": "14px" } }, h("label", { for: "set-lang", text: "Default language for reminder drafts" }), lang),
        h("p", { class: "sub", text: "Figures describe the data supplied. They are not accounting, tax or legal advice and do not predict or guarantee collections." })),
      CO.extra.settingsAi ? CO.extra.settingsAi(d) : null,
      CO.extra.settingsMore ? CO.extra.settingsMore(d) : null));
  }

  /* ---------- shell ---------- */
  const todayISO = () => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; };
  const reportUrl = () => { const q = new URLSearchParams({ company: store.get("company", "") || "CollectOS" }); if (state.asOf) q.set("as_of", state.asOf); return "/report.xlsx?" + q; };
  function copy(text) {
    const done = () => toast("Copied");
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => toast("Select the text and copy it manually"));
    else { const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }); ta.value = text; document.body.append(ta); ta.select(); try { document.execCommand("copy"); done(); } catch { toast("Select the text and copy it manually"); } ta.remove(); }
  }
  const VIEWS = { dashboard: viewDashboard, queue: viewQueue, customers: viewCustomers, promises: viewPromises, trend: viewTrend, settings: viewSettings };
  const TITLES = {};
  function registerView(id, label, ico, fn) {
    VIEWS[id] = fn; TITLES[id] = label;
    if (ico && !NAV.some((n) => n[0] === id)) NAV.push([id, label, ico]);
  }
  function go(id) { location.hash = "#/" + id; }
  function route() {
    const [id, ...rest] = (location.hash.replace("#/", "") || "dashboard").split("/");
    return { id: VIEWS[id] ? id : "dashboard", param: rest.length ? decodeURIComponent(rest.join("/")) : "" };
  }
  function render() {
    const { id, param } = route(); state.view = id;
    const lit = PARENT[id] || id;
    $$("#nav a").forEach((a) => (a.dataset.id === lit ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    const v = $("#view");
    if (!state.data) { v.replaceChildren(h("div", { class: "empty", text: "Loading…" })); return; }
    hideTip();
    const node = VIEWS[state.view](state.data, param);
    v.replaceChildren(node);
    if (node && typeof node.then === "function") node.then((n) => { if (route().id === id && n) v.replaceChildren(n); });
    document.title = `${(NAV.find((n) => n[0] === state.view) || [])[1] || TITLES[state.view] || "CollectOS"} · CollectOS`;
    window.scrollTo({ top: 0 });
  }
  function buildNav() {
    const nav = $("#nav");
    const ordered = [...NAV].sort((a, b) => NAV_ORDER.indexOf(a[0]) - NAV_ORDER.indexOf(b[0]));
    nav.replaceChildren(...ordered.map(([id, label, ico]) => h("li", {}, h("a", { href: "#/" + id, "data-id": id, title: label }, icon(ico), h("span", { class: "nav-label", text: label }),
      id === "queue" ? h("span", { class: "badge", id: "queueBadge", hidden: true }) : null))));
  }
  function applyChrome() {
    const d = state.data; if (!d) return;
    $("#asofLabel").textContent = dmy(d.as_of);
    const qb = $("#queueBadge"); if (qb) { qb.hidden = !d.kpi.to_contact_today; qb.textContent = d.kpi.to_contact_today; }
    const bc = $("#bellCount"), n = d.kpi.broken_promises;
    bc.hidden = !n; bc.textContent = n; $("#bellBtn").classList.toggle("ring", n > 0);
    const menu = $("#bellMenu");
    menu.replaceChildren(h("h4", { text: "Alerts" }), h("ul", {},
      d.promises.filter((p) => p.state === "broken").slice(0, 6).map((p) => h("li", {}, h("b", { text: p.customer }), h("small", { text: `Promised ${dmy(p.date)}; not marked kept` }))),
      d.kpi.to_contact_today ? h("li", {}, h("b", { text: `${plural(d.kpi.to_contact_today, "customer")} to contact today` }), h("small", { text: "See the Work Queue" })) : null,
      !n && !d.kpi.to_contact_today ? h("li", { text: "Nothing needs attention." }) : null));
  }
  function updateAvatar() { const n = store.get("sender", ""); $("#avatar").textContent = (n[0] || "Y").toUpperCase(); $("#userName").textContent = n || "You"; }
  function applyMotion() { document.documentElement.classList.toggle("reduce-motion", store.get("reduce", "0") === "1"); }

  // quiet=true refreshes the data and the header/sidebar badges but leaves the current screen as it is
  // (used by multi-step flows such as the import wizard, which must not be rebuilt mid-way).
  async function load(quiet = false) {
    const q = state.asOf ? "?as_of=" + encodeURIComponent(state.asOf) : "";
    try {
      const r = await fetch("/api/overview" + q); const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      state.data = j; state.calYM = null; $("#banner").replaceChildren(); applyChrome(); if (!quiet) render();
    } catch (e) {
      $("#banner").replaceChildren(h("div", { class: "banner", role: "alert", text: "Could not load data: " + e.message }));
      if (!state.data) $("#view").replaceChildren();
    }
  }

  function wire() {
    const app = $("#app"), cb = $("#collapse");
    const collapsed = store.get("collapsed", "0") === "1"; app.dataset.collapsed = String(collapsed); cb.setAttribute("aria-pressed", String(collapsed));
    cb.addEventListener("click", () => { const c = app.dataset.collapsed !== "true"; app.dataset.collapsed = String(c); cb.setAttribute("aria-pressed", String(c)); cb.setAttribute("aria-label", c ? "Expand sidebar" : "Collapse sidebar"); store.set("collapsed", c ? "1" : "0"); });
    $("#asofBtn").addEventListener("click", () => go("settings"));
    $("#logBtn").addEventListener("click", () => openFollowup());
    const bell = $("#bellBtn"), menu = $("#bellMenu");
    bell.addEventListener("click", (e) => { e.stopPropagation(); const o = menu.classList.toggle("open"); bell.setAttribute("aria-expanded", String(o)); });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) { menu.classList.remove("open"); bell.setAttribute("aria-expanded", "false"); } if (!e.target.closest(".search")) $("#searchResults").classList.remove("open"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { menu.classList.remove("open"); $("#searchResults").classList.remove("open"); hideTip(); } });
    const s = $("#search"), res = $("#searchResults");
    s.addEventListener("input", () => {
      const q = s.value.trim().toLowerCase(); res.replaceChildren();
      if (!q || !state.data) { res.classList.remove("open"); return; }
      const hits = state.data.customers.filter((c) => c.customer.toLowerCase().includes(q)).slice(0, 8);
      res.append(...(hits.length ? hits.map((c) => h("button", { type: "button", role: "option", onclick: () => { s.value = ""; res.classList.remove("open"); go("customer/" + encodeURIComponent(c.customer)); } },
        h("span", { text: c.customer }), h("span", { class: "sub num", text: compact(Number(c.overdue)) + " overdue" }))) : [h("div", { class: "sub", style: { padding: "10px 12px" }, text: "No customers match." })]));
      res.classList.add("open");
    });
    window.addEventListener("hashchange", render);
  }

  async function startSession() {
    try { const r = await fetch("/api/session"); if (r.ok) state.session = await r.json(); } catch { /* stay read-only if the session cannot be read */ }
    $("#logBtn").hidden = !state.session.writable;
    $("#sideNote").replaceChildren(h("b", { text: state.session.writable ? "Local only." : "Read-only." }),
      state.session.writable ? " Changes you save stay in the database on this computer. Nothing is sent anywhere." : " Served from this computer. Nothing is sent anywhere.");
    if (state.data) render();
  }

  async function getJSON(path, params = {}) {
    const q = new URLSearchParams(params);
    if (state.asOf && !q.has("as_of")) q.set("as_of", state.asOf);
    const r = await fetch(path + (q.toString() ? "?" + q : "")); let j = {};
    try { j = await r.json(); } catch { /* non-JSON error body */ }
    if (!r.ok) throw new Error(j.error || `request failed (${r.status})`);
    return j;
  }
  // raw-file upload (imports and bank statements); the file name travels in a header, never in the URL path
  async function upload(path, file, params = {}) {
    const q = new URLSearchParams(params); const r = await fetch(path + (q.toString() ? "?" + q : ""), {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream", "X-CollectOS-Token": state.session.token, "X-Filename": encodeURIComponent(file.name || "upload") } });
    let j = {}; try { j = await r.json(); } catch { /* non-JSON error body */ }
    if (!r.ok) throw new Error(j.error || `upload failed (${r.status})`);
    return j;
  }

  const CO = window.CO = {
    h, $, $$, icon, svgEl, fmt, fmt0, compact, dmy, dm, pct, plural, state, store, toast, openModal, field, post, upload, getJSON, load, go, render,
    writable, actor, reduceMotion, tilt, withTip, showTip, hideTip, chart3d, donut3d, queueTable, calendar, viewHead, custLink, custHref, copy,
    todayISO, countUp, openFollowup, openResolve, openSnapshot, registerView, dlg, extra: {}, BAR_TOP, BAR_BOT, DONUT, NAV_ORDER,
  };

  document.addEventListener("DOMContentLoaded", () => {
    buildNav();
    $$("[data-icon]").forEach((s) => s.replaceChildren(icon(s.dataset.icon)));
    applyMotion(); updateAvatar(); wire(); render(); startSession(); load();
  });
})();
