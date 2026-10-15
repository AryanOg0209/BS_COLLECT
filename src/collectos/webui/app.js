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
    data: null, view: "dashboard", asOf: new URLSearchParams(location.search).get("as_of") || "",
    chartMode: "age", calYM: null, calSel: null, filter: "", remindFor: null, trendMetric: "overdue",
  };
  const NAV = [
    ["dashboard", "Dashboard", "dashboard"], ["queue", "Work Queue", "queue"], ["customers", "Customers", "customers"],
    ["promises", "Promises", "promises"], ["reminders", "Reminders", "reminders"], ["trend", "Trend", "trend"], ["settings", "Settings", "settings"],
  ];
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
        ? sel.map((p) => h("li", {}, h("span", { text: p.customer }), h("span", { class: "chip " + (p.state === "broken" ? "broken" : "pending"), text: p.state === "broken" ? "Broken" : "Pending" })))
        : (state.calSel ? [h("li", { class: "sub", text: "No promises on " + dmy(state.calSel) })] : []));
      const legend = h("div", { class: "legend-cal" }, h("span", {}, h("i", { style: { background: "var(--teal)" } }), "Pending"), h("span", {}, h("i", { style: { background: "var(--warn)" } }), "Broken (date passed)"));
      box.replaceChildren(head, grid, list, big ? null : legend);
    };
    render();
    return box;
  }

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
        h("td", {}, h("div", { text: r.customer }), expandable ? reasons : null,
          slim && r.broken_promise_dates.length ? h("span", { class: "chip broken", style: { "margin-top": "4px" }, text: "Broken promise" }) : null),
        h("td", {}, h("span", { class: "chip " + (r.action === "Call" ? "call" : "remind"), text: r.action })),
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
    const cols = slim ? ["Customer", "Action", "Overdue", "Oldest"] : ["#", "Customer", "Action", "Score", "Overdue", "Oldest", "Contact"];
    const right = new Set(slim ? ["Overdue", "Oldest"] : ["Overdue", "Oldest", "Contact"]);
    const head = h("tr", {}, cols.map((t) => h("th", { scope: "col", class: right.has(t) ? "r" : "", text: t })));
    return h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, head), body));
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
          h("tbody", {}, d.waiting.map((w) => h("tr", {}, h("td", { text: w.customer }), h("td", { class: "r num", text: fmt(w.overdue) }), h("td", {}, h("span", { class: "chip muted", text: w.reason })))))))) : null,
      d.review.length ? h("div", { class: "note", style: { "margin-top": "20px" } }, "Promises to review: ", d.review.map((r) => `${r.customer} (${r.reason})`).join("; ")) : null);
    return h("div", {}, viewHead("Work queue", "Everyone to follow up today, with the rules behind each score. Click a row for the reasons."), h("div", { class: "grid" }, card));
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
        h("td", { text: c.customer }), h("td", { class: "r num", text: fmt(c.outstanding) }), h("td", { class: "r num", text: fmt(c.overdue) }),
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
    const rows = d.promises.map((p) => h("tr", {}, h("td", { text: p.customer }), h("td", { class: "num", text: dmy(p.date) }), h("td", { class: "r num", text: p.amount ? fmt(p.amount) : "–" }),
      h("td", {}, h("span", { class: "chip " + (p.state === "broken" ? "broken" : "pending"), text: p.state === "broken" ? "Broken (date passed)" : "Pending" }))));
    const list = d.promises.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Customer", "Promised date", "Amount", "State"].map((t, i) => h("th", { scope: "col", class: i === 2 ? "r" : "", text: t })))), h("tbody", {}, rows)))
      : h("div", { class: "empty", text: "No open promises. When a customer promises a date, record it with `collectos log … --promise-date`." });
    return h("div", {}, viewHead("Promises", "Every open promise to pay. A promise turns 'broken' the day after its date if it isn't marked kept."),
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c5 r-c12", style: { "--i": 0 } }, calendar(d.promises, true)), h("section", { class: "card glass reveal c7 r-c12", style: { "--i": 1 } }, list)));
  }

  function viewReminders(d) {
    const names = d.customers.filter((c) => Number(c.overdue) > 0);
    if (!state.remindFor && names.length) state.remindFor = names[0].customer;
    const out = h("div", {}), list = h("ul", { class: "picklist" });
    const sender = store.get("sender", ""), company = store.get("company", "");
    const load = async () => {
      if (!state.remindFor) { out.replaceChildren(h("div", { class: "empty", text: "Nobody has overdue invoices." })); return; }
      out.replaceChildren(h("div", { class: "empty", text: "Preparing draft…" }));
      try {
        const q = new URLSearchParams({ customer: state.remindFor, sender, company });
        if (state.asOf) q.set("as_of", state.asOf);
        const r = await fetch("/api/draft?" + q); const j = await r.json();
        if (!r.ok) throw new Error(j.error || "failed");
        const block = (title, text) => h("div", { style: { "margin-bottom": "18px" } },
          h("div", { class: "card-head" }, h("h2", { text: title, style: { "font-size": "16px" } }), h("button", { type: "button", class: "btn ghost", onclick: () => copy(text) }, icon("copy"), "Copy")),
          h("div", { class: "draft", text }));
        out.replaceChildren(h("div", { style: { "margin-bottom": "14px" } }, h("span", { class: "chip " + (j.template === "promise_followup" ? "broken" : j.template === "firm" ? "call" : "remind"), text: j.template.replace("_", " ") }),
          h("span", { class: "sub", text: "  Draft only. Read it, then send it yourself. Nothing is sent from here." })),
          block("Email: " + j.subject, j.email), block("WhatsApp", j.whatsapp));
      } catch (e) { out.replaceChildren(h("div", { class: "banner", text: "Could not build the draft: " + e.message })); }
    };
    names.forEach((c) => list.append(h("li", {}, h("button", { type: "button", "aria-pressed": String(c.customer === state.remindFor),
      onclick: (e) => { state.remindFor = c.customer; $$("button", list).forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); load(); } },
      h("span", { text: c.customer }), h("span", { class: "num sub", text: compact(Number(c.overdue)) })))));
    const hint = !sender || !company ? h("div", { class: "note", style: { "margin-bottom": "14px" } }, "Tip: set your name and company in Settings so drafts are signed correctly.") : null;
    load();
    return h("div", {}, viewHead("Reminder drafts", "Fact-only messages built from the invoice data. Nothing is sent automatically."), hint,
      h("div", { class: "two" }, h("section", { class: "card glass reveal", style: { "--i": 0 } }, list), h("section", { class: "card glass reveal", style: { "--i": 1 } }, out)));
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
      h("div", { class: "grid" }, h("section", { class: "card glass reveal c12", style: { "--i": 0 } }, h("div", { class: "card-head" }, h("h2", { text: "Week by week" }), sel), host),
        d.trend.length ? h("section", { class: "card glass reveal c12", style: { "--i": 1 } }, table, h("p", { class: "sub", style: { "margin-top": "14px" }, text: "These numbers describe what the data showed. They do not prove that any change was caused by follow-up work. 'Payments seen' counts only increases in paid amounts that arrived through imports." })) : null));
  }

  function viewSettings(d) {
    const nameIn = h("input", { id: "set-name", value: store.get("sender", ""), placeholder: "Your name" }), coIn = h("input", { id: "set-co", value: store.get("company", ""), placeholder: "Your company" });
    const dateIn = h("input", { id: "set-date", type: "date", value: d.as_of });
    const rm = h("input", { type: "checkbox", id: "set-rm", checked: store.get("reduce", "0") === "1" });
    rm.addEventListener("change", () => { store.set("reduce", rm.checked ? "1" : "0"); applyMotion(); });
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
      h("section", { class: "card glass reveal c6 r-c12", style: { "--i": 1 } }, h("h2", { text: "About your data", style: { "text-align": "left", "margin-bottom": "16px" } }),
        h("div", { class: "note", text: "This page is read-only and is served from this computer only. Nothing you see here is uploaded, and no message is sent to any customer." }),
        h("p", { class: "sub", text: `Database: ${d.db_name}` }),
        h("p", { class: "sub", text: "Figures describe the data supplied. They are not accounting, tax or legal advice and do not predict or guarantee collections." }))));
  }

  /* ---------- shell ---------- */
  const todayISO = () => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; };
  const reportUrl = () => { const q = new URLSearchParams({ company: store.get("company", "") || "CollectOS" }); if (state.asOf) q.set("as_of", state.asOf); return "/report.xlsx?" + q; };
  function copy(text) {
    const done = () => toast("Copied");
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => toast("Select the text and copy it manually"));
    else { const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }); ta.value = text; document.body.append(ta); ta.select(); try { document.execCommand("copy"); done(); } catch { toast("Select the text and copy it manually"); } ta.remove(); }
  }
  const VIEWS = { dashboard: viewDashboard, queue: viewQueue, customers: viewCustomers, promises: viewPromises, reminders: viewReminders, trend: viewTrend, settings: viewSettings };
  function go(id) { location.hash = "#/" + id; }
  function render() {
    const id = (location.hash.replace("#/", "") || "dashboard"); state.view = VIEWS[id] ? id : "dashboard";
    $$("#nav a").forEach((a) => (a.dataset.id === state.view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    const v = $("#view");
    if (!state.data) { v.replaceChildren(h("div", { class: "empty", text: "Loading…" })); return; }
    hideTip();
    v.replaceChildren(VIEWS[state.view](state.data));
    document.title = `${NAV.find((n) => n[0] === state.view)[1]} · CollectOS`;
  }
  function buildNav() {
    const nav = $("#nav");
    nav.replaceChildren(...NAV.map(([id, label, ico]) => h("li", {}, h("a", { href: "#/" + id, "data-id": id, title: label }, icon(ico), h("span", { class: "nav-label", text: label }),
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

  async function load() {
    const q = state.asOf ? "?as_of=" + encodeURIComponent(state.asOf) : "";
    try {
      const r = await fetch("/api/overview" + q); const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      state.data = j; state.calYM = null; $("#banner").replaceChildren(); applyChrome(); render();
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
    const bell = $("#bellBtn"), menu = $("#bellMenu");
    bell.addEventListener("click", (e) => { e.stopPropagation(); const o = menu.classList.toggle("open"); bell.setAttribute("aria-expanded", String(o)); });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) { menu.classList.remove("open"); bell.setAttribute("aria-expanded", "false"); } if (!e.target.closest(".search")) $("#searchResults").classList.remove("open"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { menu.classList.remove("open"); $("#searchResults").classList.remove("open"); hideTip(); } });
    const s = $("#search"), res = $("#searchResults");
    s.addEventListener("input", () => {
      const q = s.value.trim().toLowerCase(); res.replaceChildren();
      if (!q || !state.data) { res.classList.remove("open"); return; }
      const hits = state.data.customers.filter((c) => c.customer.toLowerCase().includes(q)).slice(0, 8);
      res.append(...(hits.length ? hits.map((c) => h("button", { type: "button", role: "option", onclick: () => { state.filter = c.customer; s.value = ""; res.classList.remove("open"); go("customers"); render(); } },
        h("span", { text: c.customer }), h("span", { class: "sub num", text: compact(Number(c.overdue)) + " overdue" }))) : [h("div", { class: "sub", style: { padding: "10px 12px" }, text: "No customers match." })]));
      res.classList.add("open");
    });
    window.addEventListener("hashchange", render);
  }

  buildNav();
  $$("[data-icon]").forEach((s) => s.replaceChildren(icon(s.dataset.icon)));
  applyMotion(); updateAvatar(); wire(); render(); load();
})();
