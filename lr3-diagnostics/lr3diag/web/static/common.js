// Shared dashboard code: theme, units, live stream, formatting, trend chart.
const LR3 = (() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- theme & units ------------------------------------------------------
  const prefs = JSON.parse(localStorage.getItem("lr3prefs") || '{"units":"imperial"}');
  const savePrefs = () => localStorage.setItem("lr3prefs", JSON.stringify(prefs));
  if (prefs.theme) document.documentElement.dataset.theme = prefs.theme;

  function header(active) {
    const pages = [["/", "Home"], ["/diagnostics", "Diagnostics"], ["/maintenance", "Maintenance"], ["/offroad", "Off-road"], ["/manual", "Manual"]];
    const h = document.createElement("header");
    h.className = "top";
    h.innerHTML = `<h1>LR3 Diagnostics</h1><nav>${pages.map(([u, n]) => `<a href="${u}" class="${u === active ? "on" : ""}">${n}</a>`).join("")}</nav>
      <span class="spacer"></span>
      <div class="controls">
        <label>Units <select id="units"><option value="imperial">°F · mph · in</option><option value="metric">°C · km/h · mm</option></select></label>
        <button id="theme" title="Toggle light/dark">Light/Dark</button>
      </div>`;
    document.body.prepend(h);
    $("#units").value = prefs.units;
    $("#units").onchange = (e) => { prefs.units = e.target.value; savePrefs(); location.reload(); };
    $("#theme").onclick = () => {
      const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
      prefs.theme = dark ? "light" : "dark"; savePrefs();
      document.documentElement.dataset.theme = prefs.theme;
    };
  }

  // Convert a metric channel value for display. Returns [value, unit].
  function convert(unit, v) {
    if (v == null || typeof v !== "number") return [v, unit];
    if (prefs.units === "imperial") {
      if (unit === "°C") return [v * 9 / 5 + 32, "°F"];
      if (unit === "km/h") return [v * 0.621371, "mph"];
      if (unit === "mm") return [v / 25.4, "in"];
    }
    return [v, unit];
  }
  function fmt(unit, v, digits) {
    const [x, u] = convert(unit, v);
    if (x == null) return ["—", u];
    if (typeof x !== "number") return [String(x), u];
    const d = digits ?? (u === "V" || u === "in" ? 1 : Math.abs(x) >= 100 ? 0 : 1);
    return [x.toFixed(d), u];
  }

  // ---- status thresholds (metric) ------------------------------------------
  // Each returns [state, label]. States map to the reserved status colours and are always shown with a text label.
  const STATUS = {
    coolant: (v) => v >= 110 ? ["critical", "Overheating"] : v >= 105 ? ["warning", "Hot"] : v < 70 ? ["none", "Warming up"] : ["good", "Normal"],
    trans_temp: (v) => v >= 125 ? ["critical", "Too hot"] : v >= 110 ? ["warning", "Hot"] : ["good", "Normal"],
    voltage: (v, r) => (r.rpm ?? 0) < 400 ? (v < 12.2 ? ["warning", "Low (engine off)"] : ["none", "Engine off"])
      : v < 12.6 ? ["critical", "Not charging"] : v < 13.2 ? ["warning", "Low"] : v > 15 ? ["warning", "High"] : ["good", "Charging"],
    trim: (v) => Math.abs(v) > 20 ? ["critical", v > 0 ? "Very lean" : "Very rich"] : Math.abs(v) > 10 ? ["warning", v > 0 ? "Lean" : "Rich"] : ["good", "Normal"],
  };
  const statusHTML = ([state, label]) => `<span class="status ${state}"><span class="dot" aria-hidden="true"></span>${esc(label)}</span>`;

  // ---- live stream ----------------------------------------------------------
  // onRows(rowsAppended, allRows). Keeps up to `keep` rows in memory.
  function live(onRows, { keep = 1200, onStatus } = {}) {
    const rows = [];
    let es;
    const connect = () => {
      es = new EventSource("/api/stream");
      es.addEventListener("history", (e) => { rows.length = 0; rows.push(...JSON.parse(e.data)); onRows(rows.slice(), rows); onStatus?.(""); });
      es.onmessage = (e) => { const r = JSON.parse(e.data); rows.push(r); if (rows.length > keep) rows.shift(); onRows([r], rows); onStatus?.(""); };
      es.addEventListener("status", (e) => onStatus?.(JSON.parse(e.data).error || ""));
      es.onerror = () => onStatus?.("Lost connection to the dashboard server. Retrying…");
    };
    connect();
    return rows;
  }

  // ---- trend chart -------------------------------------------------------------
  // series: [{key, label, value?: (row) => number}] ; unit is the metric unit.
  class TrendChart {
    constructor(el, { title, unit, series, window = 120, min, max, refs = [] }) {
      Object.assign(this, { el, unit, series, window, min, max, refs });
      const colors = ["var(--series-1)", "var(--series-2)"];
      series.forEach((s, i) => (s.color = colors[i]));
      el.classList.add("card", "chart");
      const [, du] = convert(unit, 0);
      el.innerHTML = `<h2>${esc(title)} <span style="text-transform:none;font-weight:400">(${esc(du)})</span></h2>
        ${series.length > 1 ? `<div class="legend">${series.map((s) => `<span><span class="sw" style="background:${s.color}"></span>${esc(s.label)}</span>`).join("")}</div>` : ""}
        <svg role="img" aria-label="${esc(title)} trend"></svg><div class="tip"></div>`;
      this.svg = $("svg", el); this.tip = $(".tip", el);
      this.svg.addEventListener("pointermove", (e) => this.hover(e));
      this.svg.addEventListener("pointerleave", () => { this.hoverX = null; this.tip.style.display = "none"; this.render(); });
      this.rows = [];
    }
    val(s, r) { const v = s.value ? s.value(r) : r[s.key]; return typeof v === "number" ? convert(this.unit, v)[0] : null; }
    update(rows) { this.rows = rows; this.render(); }
    render() {
      const W = this.svg.clientWidth || 400, H = 170, L = 40, R = 48, T = 6, B = 20;
      const rows = this.rows; if (!rows.length) return;
      const tEnd = rows[rows.length - 1].t, tStart = tEnd - this.window;
      const vis = rows.filter((r) => r.t >= tStart);
      const vals = vis.flatMap((r) => this.series.map((s) => this.val(s, r))).filter((v) => v != null);
      if (!vals.length) { this.svg.innerHTML = `<text x="${L}" y="40" class="axis-label">No data for this channel from the current source</text>`; return; }
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (this.min != null) lo = Math.min(lo, convert(this.unit, this.min)[0]);
      if (this.max != null) hi = Math.max(hi, convert(this.unit, this.max)[0]);
      if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
      const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      const x = (t) => L + ((t - tStart) / this.window) * (W - L - R);
      const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
      const ticks = niceTicks(lo, hi, 4);
      let g = ticks.map((v) => `<line class="gridline" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-label" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${+v.toFixed(2)}</text>`).join("");
      for (const s of [0, 0.5, 1]) {
        const secs = Math.round(this.window * (1 - s));
        g += `<text class="axis-label" x="${L + s * (W - L - R)}" y="${H - 4}" text-anchor="${s === 0 ? "start" : s === 1 ? "end" : "middle"}">${secs ? "−" + mmss(secs) : "now"}</text>`;
      }
      for (const ref of this.refs) { const rv = convert(this.unit, ref)[0]; if (rv > lo && rv < hi) g += `<line class="ref" x1="${L}" x2="${W - R}" y1="${y(rv)}" y2="${y(rv)}"/>`; }
      const ends = [];
      for (const s of this.series) {
        const pts = vis.map((r) => [r.t, this.val(s, r)]).filter((p) => p[1] != null);
        if (!pts.length) continue;
        g += `<polyline class="line" stroke="${s.color}" points="${pts.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"/>`;
        const last = pts[pts.length - 1];
        ends.push({ y: y(last[1]), s });
      }
      // Direct end labels (avoid overlap when two series end close together).
      ends.sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].y = ends[i - 1].y + 12;
      if (this.series.length > 1) for (const e of ends) g += `<text class="end-label" x="${W - R + 6}" y="${e.y + 4}">${esc(e.s.short || e.s.label)}</text>`;
      if (this.hoverX != null) {
        const tt = tStart + ((this.hoverX - L) / (W - L - R)) * this.window;
        let best = vis[0]; for (const r of vis) if (Math.abs(r.t - tt) < Math.abs(best.t - tt)) best = r;
        const cx = x(best.t);
        g += `<line class="crosshair" x1="${cx}" x2="${cx}" y1="${T}" y2="${H - B}"/>`;
        for (const s of this.series) { const v = this.val(s, best); if (v != null) g += `<circle cx="${cx}" cy="${y(v)}" r="4" fill="${s.color}" stroke="var(--surface-1)" stroke-width="2"/>`; }
        const [, du] = convert(this.unit, 0);
        this.tip.innerHTML = `<div class="muted">−${mmss(Math.max(0, Math.round(tEnd - best.t)))}</div>` + this.series.map((s) => {
          const v = this.val(s, best); return `<div><span class="sw" style="background:${s.color}"></span>${esc(s.label)}: <b>${v == null ? "—" : v.toFixed(1)}</b> ${esc(du)}</div>`;
        }).join("");
        this.tip.style.display = "block";
        const left = Math.min(cx + 12, W - this.tip.offsetWidth - 4);
        this.tip.style.left = left + "px"; this.tip.style.top = "30px";
      }
      this.svg.innerHTML = g;
    }
    hover(e) { const b = this.svg.getBoundingClientRect(); this.hoverX = e.clientX - b.left; this.render(); }
  }
  function niceTicks(lo, hi, n) {
    const span = hi - lo, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || mag * 10;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
    return out;
  }
  const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  async function api(path, opts) {
    const r = await fetch(path, opts && { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }

  return { $, esc, prefs, header, convert, fmt, STATUS, statusHTML, live, TrendChart, api };
})();
