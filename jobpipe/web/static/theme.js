// The app's looks, and the feedback moments that go with them.
//
// A look is a stylesheet scoped to html[data-theme="<id>"] (static/themes/<id>.css) plus, here, how it
// celebrates an application and consoles a rejection. The choice is a per-browser preference
// (localStorage "jobpipe-theme"); theme-boot.js applies it before the first paint. app.js calls
// Look.status(...) after a status change, Look.sparkle(...) when a role is starred and Look.fly(...)
// when a role is tracked. Every look works the same; only the dressing and the moments differ.
"use strict";
window.Look = (() => {
  const KEY = "jobpipe-theme";
  const THEMES = [
    { id: "sorbet", name: "Sorbet", blurb: "Candy brights and round shapes. Confetti when you apply.",
      swatches: ["#FFF6F9", "#FF3E7F", "#FF9F1C", "#75CB33", "#00A5E7", "#A682E1"],
      confetti: ["#FF3E7F", "#FF9F1C", "#7ED957", "#38B6FF", "#8A5CFF", "#FFFFFF", "#FFD166"] },
    { id: "classic", name: "Classic", blurb: "The original look: compact and grey, with the résumé's red.",
      swatches: ["#f3f4f6", "#ffffff", "#d3121f", "#1d5fd0", "#0e8a5c", "#7446d0"],
      confetti: ["#d3121f", "#1d5fd0", "#0e8a5c", "#e0a106", "#7446d0", "#ffffff"] },
  ];
  const byId = (id) => THEMES.find((t) => t.id === id);
  const RM = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, root = document) => root.querySelector(s);
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    el.append(...kids.flat().filter((x) => x != null && x !== false).map((x) => (typeof x === "string" ? document.createTextNode(x) : x)));
    return el;
  }

  // ---- the choice -----------------------------------------------------------------------------------
  function current() {
    const t = document.documentElement.dataset.theme;
    return byId(t) ? t : THEMES[0].id;
  }
  function set(id) {
    if (!byId(id)) return;
    document.documentElement.dataset.theme = id;
    try { localStorage.setItem(KEY, id); } catch (_) { /* then it lasts for this page only */ }
    renderPickers();
    hideAll();
  }
  function renderPickers() { document.querySelectorAll(".look-picker").forEach(renderPicker); }
  function renderPicker(box) {
    const cur = current();
    box.replaceChildren(...THEMES.map((t) => h("button", {
      type: "button", class: "look-option", role: "radio", "aria-checked": String(t.id === cur), "data-look": t.id,
      onclick: () => set(t.id) },
      h("span", { class: "look-swatches", "aria-hidden": "true" }, ...t.swatches.map((c) => h("i", { style: `background:${c}` }))),
      h("span", { class: "look-name" }, t.name),
      h("span", { class: "look-blurb" }, t.blurb))));
  }

  // ---- shared pieces: confetti, banner, cat card, sparkle, a flying badge -----------------------------
  let canvas = null, ctx = null, particles = [], raf = null;
  function layer() {
    if (!canvas) {
      canvas = h("canvas", { id: "moment-canvas", "aria-hidden": "true" });
      document.body.append(canvas);
      ctx = canvas.getContext("2d");
      addEventListener("resize", sizeCanvas);
      sizeCanvas();
    }
    return canvas;
  }
  function sizeCanvas() {
    const dpr = devicePixelRatio || 1;
    const inDialog = canvas.parentElement?.id === "setup-dialog";
    const w = inDialog ? canvas.clientWidth : innerWidth;
    const h = inDialog ? canvas.clientHeight : innerHeight;
    canvas.width = Math.max(1, w) * dpr; canvas.height = Math.max(1, h) * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  // A burst of pieces from (x, y): a cannon when spread is narrow, a shower when it is wide.
  function confetti(x, y, colors, { count = 200, duration = 2000, size = 1, spread = 0.9, speed = 1, square = false, root = null } = {}) {
    if (RM()) return;
    layer();
    const parent = root?.isConnected ? root : document.body;
    if (canvas.parentElement !== parent) parent.append(canvas);
    sizeCanvas();
    const t0 = performance.now();
    for (let i = 0; i < count; i++) {
      const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * spread, v = (7 + Math.random() * 9) * speed;
      particles.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: (4 + Math.random() * 5) * size, c: colors[i % colors.length],
                       rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3, shape: square ? 1 : i % 3, born: t0, life: duration * (0.7 + Math.random() * 0.3) });
    }
    if (!raf) raf = requestAnimationFrame(draw);
  }
  function draw(t) {
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    particles = particles.filter((p) => t - p.born < p.life);
    for (const p of particles) {
      const age = (t - p.born) / p.life;
      p.vy += 0.28; p.vx *= 0.985; p.vy *= 0.985; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.globalAlpha = age > 0.75 ? 1 - (age - 0.75) / 0.25 : 1;
      ctx.fillStyle = p.c; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
      if (p.shape === 0) { ctx.beginPath(); ctx.arc(0, 0, p.r * 0.6, 0, Math.PI * 2); ctx.fill(); }
      else if (p.shape === 1) ctx.fillRect(-p.r / 2, -p.r / 4, p.r, p.r / 2);
      else { ctx.beginPath(); ctx.moveTo(0, -p.r / 2); ctx.lineTo(p.r / 2, p.r / 2); ctx.lineTo(-p.r / 2, p.r / 2); ctx.closePath(); ctx.fill(); }
      ctx.restore();
    }
    ctx.globalAlpha = 1;
    if (particles.length) raf = requestAnimationFrame(draw);
    else { raf = null; ctx.clearRect(0, 0, innerWidth, innerHeight); }
  }

  let bannerEl = null, bannerTimer = null;
  function banner(msg, cls = "", ms = 4600) {
    if (!bannerEl) {
      bannerEl = h("div", { id: "moment-banner", class: "moment-banner", role: "status", onclick: hideBanner });
      document.body.append(bannerEl);
    }
    bannerEl.replaceChildren(typeof msg === "string" ? document.createTextNode(msg) : msg);
    bannerEl.className = `moment-banner ${cls}`;
    void bannerEl.offsetWidth;                 // restart the slide when one banner follows another
    bannerEl.classList.add("show");
    clearTimeout(bannerTimer);
    bannerTimer = setTimeout(hideBanner, ms);
  }
  function hideBanner() { bannerEl?.classList.remove("show"); }

  let catEl = null, catTimer = null;
  // The sad fat cat: each look draws its own, in `svg`; the card, the words and the button are shared.
  function catCard({ svg, lead, rest, button = "Open Find jobs", cls = "", ms = 6500 }) {
    if (!catEl) {
      catEl = h("div", { id: "moment-cat", class: "cat-card", role: "status", onclick: hideCat });
      document.body.append(catEl);
    }
    const go = h("button", { type: "button", class: "primary small", onclick: (e) => { e.stopPropagation(); hideCat(); window.showTab?.("find"); } }, button);
    catEl.className = `cat-card ${cls}`;
    catEl.replaceChildren(h("div", { class: "cat-row" },
      h("div", { class: "cat-art", html: svg, "aria-hidden": "true" }),
      h("div", { class: "cat-msg" }, h("p", {}, h("b", {}, lead), rest), go)));
    void catEl.offsetWidth;
    catEl.classList.add("show");
    clearTimeout(catTimer);
    catTimer = setTimeout(hideCat, ms);
  }
  function hideCat() { catEl?.classList.remove("show"); }
  function hideAll() { hideBanner(); hideCat(); }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideAll(); });

  // Seven short rays bursting from a starred star.
  function sparkle(btn, colors) {
    if (!btn || RM()) return;
    const r = btn.getBoundingClientRect();
    const s = h("span", { class: "sparkle", "aria-hidden": "true", style: `left:${r.left + r.width / 2 - 22}px;top:${r.top + r.height / 2 - 22}px` },
      ...Array.from({ length: 7 }, (_, i) => h("i", { style: `--a:${i * 51}deg;background:${(colors || byId(current()).confetti)[i % 5]}` })));
    document.body.append(s);
    setTimeout(() => s.remove(), 700);
  }
  // A small badge flies from one element to another (a tracked role to the Tracker tab).
  function fly(fromEl, toEl, color) {
    if (!fromEl || !toEl || RM() || !fromEl.animate) return;
    const a = fromEl.getBoundingClientRect(), b = toEl.getBoundingClientRect();
    const dot = h("span", { class: "flyer", "aria-hidden": "true", style: `left:${a.left + 24}px;top:${a.top + a.height / 2 - 9}px;background:${color || "currentColor"}` });
    document.body.append(dot);
    dot.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 },
                 { transform: `translate(${b.left + b.width / 2 - (a.left + 24)}px, ${b.top + b.height / 2 - (a.top + a.height / 2)}px) scale(.4)`, opacity: 0.2 }],
                { duration: 520, easing: "cubic-bezier(.4,0,.2,1)" }).onfinish = () => dot.remove();
  }

  // ---- the words --------------------------------------------------------------------------------------
  const who = (job) => job.company || job.title || "them";
  function appliedWords(job, counts) {
    return counts.out <= 1 ? `Sent to ${who(job)}. That's the first one out the door.` : `Sent to ${who(job)}. That's ${counts.out} out the door.`;
  }
  const doneWords = (job) => `Done: ${job.title || "this role"}${job.company ? ` at ${job.company}` : ""}.`;
  const interviewingWords = (job) => `Interviewing at ${who(job)}. Go get them.`;
  const offerWords = (job) => `An offer from ${who(job)}!`;
  function deniedWords(job, counts) {
    return { lead: `${who(job)} passed.`,
             rest: counts.inPlay ? ` The cat is sad too. ${counts.inPlay} still in play.` : " The cat is sad too. Time to find more roles." };
  }

  // ---- the cats ---------------------------------------------------------------------------------------
  const CATS = {
    // Sorbet: a round lilac blob on a pink cushion; its eyes go flat, it blinks once, the tail flicks once.
    sorbet: `<svg class="cat cat-sorbet" viewBox="0 0 140 110">
      <ellipse cx="70" cy="98" rx="64" ry="11" fill="#FFD3E2"/><ellipse cx="70" cy="95" rx="60" ry="8" fill="#FF9FBE"/>
      <g class="tail"><path d="M20 70 C 2 68, 0 92, 24 90" fill="none" stroke="#B4A9CC" stroke-width="9" stroke-linecap="round"/></g>
      <g class="body-blob">
        <ellipse cx="72" cy="66" rx="52" ry="34" fill="#B4A9CC"/><ellipse cx="74" cy="76" rx="30" ry="18" fill="#D6CEE8"/>
        <path d="M44 40 l-4 -12 l12 7z" fill="#B4A9CC"/><path d="M98 40 l4 -12 l-12 7z" fill="#B4A9CC"/>
        <path d="M45 41 l-2 -7 l6 4z" fill="#FF9FBE"/><path d="M97 41 l2 -7 l-6 4z" fill="#FF9FBE"/>
        <g class="eye" style="transform-origin:56px 52px"><ellipse cx="56" cy="52" rx="4.5" ry="5" fill="#2A1B3D"/></g>
        <g class="eye" style="transform-origin:86px 52px"><ellipse cx="86" cy="52" rx="4.5" ry="5" fill="#2A1B3D"/></g>
        <path d="M68 60 q3 3 6 0" fill="none" stroke="#2A1B3D" stroke-width="2" stroke-linecap="round"/>
        <path d="M71 56 l0 3" stroke="#FF9FBE" stroke-width="3" stroke-linecap="round"/>
        <path d="M50 60 l-16 -2 M50 63 l-16 3 M92 60 l16 -2 M92 63 l16 3" stroke="#8E7FA3" stroke-width="1.5" stroke-linecap="round"/>
        <ellipse cx="46" cy="92" rx="10" ry="5" fill="#B4A9CC"/><ellipse cx="98" cy="92" rx="10" ry="5" fill="#B4A9CC"/>
      </g></svg>`,
  };
  CATS.classic = CATS.sorbet;

  // ---- the moments, per look --------------------------------------------------------------------------
  // Each renderer gets { job, counts, anchor, theme }: anchor is where the Applied count sits on screen.
  const standard = {
    applied({ job, counts, anchor, theme }) { confetti(anchor.x, anchor.y, theme.confetti, { count: 220 }); banner(appliedWords(job, counts), "applied"); },
    done({ job, theme }) { confetti(innerWidth / 2, 90, theme.confetti, { count: 90, size: 0.8, spread: 1.4 }); banner(doneWords(job), "done", 3200); },
    interviewing({ job, anchor, theme }) { confetti(anchor.x, anchor.y, theme.confetti, { count: 140 }); banner(interviewingWords(job), "interviewing"); },
    offer({ job, anchor, theme }) {
      confetti(anchor.x, anchor.y, theme.confetti, { count: 260 });
      confetti(innerWidth / 2, 90, theme.confetti, { count: 200, spread: 1.6 });
      banner(offerWords(job), "offer", 6000);
    },
    denied({ job, counts, theme }) { catCard({ svg: CATS[theme.id] || CATS.sorbet, ...deniedWords(job, counts) }); },
  };
  const RENDER = { sorbet: standard, classic: standard };

  // After a status change. Returns true when the look showed something, so app.js can skip its plain toast.
  function status({ job, status, counts }) {
    const kind = { Applied: "applied", Done: "done", Denied: "denied", Interviewing: "interviewing", Offer: "offer" }[status] || null;
    if (!kind || !job) return false;
    const theme = byId(current());
    const r = RENDER[theme.id] || standard;
    const seg = $(`#stages .stage[data-stage="${status}"]`);
    const rect = seg && seg.offsetParent ? seg.getBoundingClientRect() : null;
    const anchor = rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : { x: innerWidth / 2, y: 90 };
    hideAll();
    r[kind]({ job, counts: counts || {}, anchor, theme });
    return true;
  }

  // ---- more looks -------------------------------------------------------------------------------------
  // A look in its own file (static/themes/<id>.js, loaded after this one) registers itself:
  //   Look.register({ id, name, blurb, swatches, confetti, cat: "<svg…>",
  //                   render: { applied(ctx), done(ctx), denied(ctx), interviewing(ctx), offer(ctx) } })
  // Any renderer it leaves out falls back to the standard one; `cat` is the SVG the standard denied moment shows.
  // The picker lists looks in the order they register, Classic last.
  function register(def) {
    if (!def?.id || !/^[a-z]+$/.test(def.id)) throw new Error("A look needs a lowercase id");
    const i = THEMES.findIndex((t) => t.id === def.id);
    const entry = { confetti: THEMES[0].confetti, swatches: [], ...def };
    delete entry.render; delete entry.cat;
    if (i >= 0) THEMES[i] = entry;
    else THEMES.splice(THEMES.findIndex((t) => t.id === "classic"), 0, entry);
    if (def.cat) CATS[def.id] = def.cat;
    RENDER[def.id] = { ...standard, ...(def.render || {}) };
    renderPickers();
  }

  // ---- wiring -----------------------------------------------------------------------------------------
  // The looks' own files register after this one, so the saved choice is checked once they all have.
  document.addEventListener("DOMContentLoaded", () => {
    if (!byId(document.documentElement.dataset.theme)) document.documentElement.dataset.theme = THEMES[0].id;
    renderPickers();
  });
  renderPickers();

  return { THEMES, current, set, register, renderPicker, status, sparkle, fly, confetti, banner, catCard, hideAll, hideCat, hideBanner,
           standard, CATS, RENDER, RM, h, words: { appliedWords, doneWords, deniedWords, interviewingWords, offerWords, who } };
})();
