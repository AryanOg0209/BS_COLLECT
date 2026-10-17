/* CollectOS website behaviour. No libraries, no network requests. Everything here is enhancement: with scripts off, or with
   "reduce motion" on, the page is complete and readable. No scroll-jacking: scrolling is never intercepted. */
(() => {
  "use strict";
  const root = document.documentElement;
  root.classList.add("js");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

  /* ---------- mobile menu ---------- */
  const btn = $("#menuBtn"), nav = $("#nav");
  if (btn && nav) {
    const set = (open) => { btn.setAttribute("aria-expanded", String(open)); nav.dataset.open = String(open); };
    set(false);
    btn.addEventListener("click", () => set(btn.getAttribute("aria-expanded") !== "true"));
    nav.addEventListener("click", (e) => { if (e.target.closest("a")) set(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") { set(false); btn.focus(); } });
  }

  /* ---------- reveal on scroll ---------- */
  const reveals = $$(".reveal");
  if ("IntersectionObserver" in window && !reduce.matches) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    reveals.forEach((el, i) => { el.style.transitionDelay = `${(i % 4) * 70}ms`; io.observe(el); });
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }

  /* ---------- hero scene: gentle pointer tilt; pauses when offscreen ---------- */
  const stage = $("#stage"), scene = $("#scene");
  if (stage && scene) {
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(([en]) => stage.classList.toggle("paused", !en.isIntersecting), { threshold: 0 }).observe(stage);
    }
    if (!reduce.matches && fine.matches) {
      let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
      const tick = () => {
        cx += (tx - cx) * 0.12; cy += (ty - cy) * 0.12;
        scene.style.setProperty("--rx", `${cy.toFixed(2)}deg`);
        scene.style.setProperty("--ry", `${cx.toFixed(2)}deg`);
        raf = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.02 ? requestAnimationFrame(tick) : 0;
      };
      const kick = () => { if (!raf) raf = requestAnimationFrame(tick); };
      window.addEventListener("pointermove", (e) => {
        const r = stage.getBoundingClientRect();
        if (r.bottom < 0 || r.top > innerHeight) return;
        tx = clamp(((e.clientX - (r.left + r.width / 2)) / innerWidth) * 26, -9, 9);
        ty = clamp(((e.clientY - (r.top + r.height / 2)) / innerHeight) * -20, -7, 7);
        kick();
      }, { passive: true });
    }
  }

  /* ---------- card tilt (hover with a mouse only) ---------- */
  if (!reduce.matches && fine.matches) {
    $$(".tilt").forEach((el) => {
      el.addEventListener("pointermove", (e) => {
        const r = el.getBoundingClientRect();
        el.style.setProperty("--ty", `${(((e.clientX - r.left) / r.width) - 0.5) * 8}deg`);
        el.style.setProperty("--tx", `${(((e.clientY - r.top) / r.height) - 0.5) * -8}deg`);
      });
      el.addEventListener("pointerleave", () => { el.style.setProperty("--tx", "0deg"); el.style.setProperty("--ty", "0deg"); });
    });
  }

  /* ---------- screens viewer: keyboard-operable tabs, optional frame tilt ---------- */
  const tabs = $$('[role="tab"]');
  if (tabs.length) {
    const show = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => show(t, false));
      t.addEventListener("keydown", (e) => {
        const keys = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
        if (e.key in keys) { e.preventDefault(); show(tabs[(keys[e.key] + tabs.length) % tabs.length], true); }
      });
    });
    const wrap = $("#frameWrap");
    if (wrap && !reduce.matches && fine.matches) {
      const frame = $(".frame", wrap);
      wrap.addEventListener("pointermove", (e) => {
        const r = wrap.getBoundingClientRect();
        frame.style.setProperty("--fy", `${(((e.clientX - r.left) / r.width) - 0.5) * 9}deg`);
        frame.style.setProperty("--fx", `${5 + (((e.clientY - r.top) / r.height) - 0.5) * -6}deg`);
      });
      wrap.addEventListener("pointerleave", () => { frame.style.setProperty("--fy", "0deg"); frame.style.setProperty("--fx", "5deg"); });
    }
  }
})();
