// Minimal SVG charts: line chart with crosshair + tooltip, grouped bar chart with per-bar tooltip.
// Colours come from CSS custom properties, so light/dark follow the page tokens.
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function niceTicks(lo, hi, n = 5) {
    const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function swatch(s) {
    return s.dash ? `<i class="sw ln dash" style="border-color:${s.color}"></i>` : `<i class="sw ln" style="border-color:${s.color}"></i>`;
  }
  function frame(host, o) {
    host.innerHTML = "";
    if (o.title) host.insertAdjacentHTML("beforeend", `<p class="ttl">${esc(o.title)}</p>`);
    if (o.caption) host.insertAdjacentHTML("beforeend", `<p class="cap">${o.caption}</p>`);
    if (o.legend && o.legend.length > 1)
      host.insertAdjacentHTML("beforeend", `<div class="lg">${o.legend.map(s => `<span>${s.sw || swatch(s)}${esc(s.name)}</span>`).join("")}</div>`);
    const tip = document.createElement("div");
    tip.className = "tip";
    host.appendChild(tip);
    return tip;
  }

  // o: {title, caption, x:[Date|number], series:[{name,values,color,dash}], height, yFmt, xFmt, tipX, onClick, yMax}
  function line(host, o) {
    const tip = frame(host, { ...o, legend: o.series });
    const W = Math.max(host.clientWidth, 320), H = o.height || 260, m = { l: 52, r: 12, t: 8, b: 26 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.title || "chart" }, host);
    const n = o.x.length;
    let hi = o.yMax ?? 0, lo = 0;
    o.series.forEach(s => s.values.forEach(v => { if (v != null) { hi = Math.max(hi, v); lo = Math.min(lo, v); } }));
    if (o.yMax) hi = Math.max(hi, o.yMax);
    const ticks = niceTicks(lo, hi * 1.05);
    const y0 = ticks[0], y1 = Math.max(ticks[ticks.length - 1], hi);
    const X = i => m.l + (W - m.l - m.r) * (n > 1 ? i / (n - 1) : 0.5);
    const Y = v => m.t + (H - m.t - m.b) * (1 - (v - y0) / (y1 - y0 || 1));
    ticks.forEach(t => {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: t === 0 ? "axisl" : "gridl" }, svg);
      el("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = (o.yFmt || String)(t);
    });
    const xt = o.xTicks ? o.xTicks(o.x) : [0, Math.floor(n / 2), n - 1];
    xt.forEach(i => {
      const t = el("text", { x: X(i), y: H - 8, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle", class: "tick" }, svg);
      t.textContent = (o.xFmt || String)(o.x[i]);
    });
    o.series.forEach(s => {
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1);
        pen = true;
      });
      el("path", { d, class: "ln" + (s.dash ? " dash" : ""), style: `stroke:${s.color}` }, svg);
    });
    const xh = el("line", { y1: m.t, y2: H - m.b, class: "xh", visibility: "hidden" }, svg);
    const dots = o.series.map(s => el("circle", { r: 4, class: "dot", style: `fill:${s.color}`, visibility: "hidden" }, svg));
    const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", style: o.onClick ? "cursor:pointer" : "" }, svg);
    const idx = ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (W / r.width);
      return Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (n - 1))));
    };
    hit.addEventListener("mousemove", ev => {
      const i = idx(ev);
      xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
      o.series.forEach((s, k) => {
        const v = s.values[i];
        dots[k].setAttribute("visibility", v == null ? "hidden" : "visible");
        if (v != null) { dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v)); }
      });
      tip.innerHTML = `<b>${esc((o.tipX || o.xFmt || String)(o.x[i]))}</b>` + o.series.map(s =>
        `<div class="r"><span>${swatch(s)}${esc(s.name)}</span><span>${s.values[i] == null ? "–" : (o.tipFmt || o.yFmt || String)(s.values[i])}</span></div>`).join("");
      tip.style.display = "block";
      const hr = host.getBoundingClientRect(), r = svg.getBoundingClientRect();
      const px = (X(i) / W) * r.width + r.left - hr.left;
      tip.style.left = (px > hr.width * 0.6 ? px - tip.offsetWidth - 12 : px + 12) + "px";
      tip.style.top = (r.top - hr.top + 10) + "px";
    });
    hit.addEventListener("mouseleave", () => { tip.style.display = "none"; xh.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); });
    if (o.onClick) hit.addEventListener("click", ev => o.onClick(idx(ev)));
  }

  // o: {title, caption, cats:[str], groups:[{name,values,color}], yFmt, height}
  function bars(host, o) {
    const tip = frame(host, { ...o, legend: o.groups.map(g => ({ ...g, sw: `<i class="sw sq" style="background:${g.color};border-radius:2px"></i>` })) });
    const W = Math.max(host.clientWidth, 320), H = o.height || 240, m = { l: 44, r: 8, t: 8, b: 30 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.title || "chart" }, host);
    let hi = 0;
    o.groups.forEach(g => g.values.forEach(v => { if (v != null) hi = Math.max(hi, v); }));
    const ticks = niceTicks(0, hi * 1.05), y1 = Math.max(ticks[ticks.length - 1], hi);
    const Y = v => m.t + (H - m.t - m.b) * (1 - v / (y1 || 1));
    ticks.forEach(t => {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: t === 0 ? "axisl" : "gridl" }, svg);
      el("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = (o.yFmt || String)(t);
    });
    const cw = (W - m.l - m.r) / o.cats.length, gap = 2;
    const bw = Math.min(28, (cw * 0.7 - gap * (o.groups.length - 1)) / o.groups.length);
    o.cats.forEach((c, ci) => {
      const gx = m.l + ci * cw + (cw - (bw * o.groups.length + gap * (o.groups.length - 1))) / 2;
      el("text", { x: m.l + ci * cw + cw / 2, y: H - 10, "text-anchor": "middle", class: "tick" }, svg).textContent = c;
      o.groups.forEach((g, gi) => {
        const v = g.values[ci];
        if (v == null) return;
        const x = gx + gi * (bw + gap), y = Y(v), h = Y(0) - y, rr = Math.min(4, bw / 2, h);
        // rounded data-end, square at the baseline
        const d = `M${x},${Y(0)} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + bw - rr} Q${x + bw},${y} ${x + bw},${y + rr} V${Y(0)} Z`;
        const p = el("path", { d, style: `fill:${g.color}`, class: "bar" }, svg);
        const hitr = el("rect", { x: x - gap / 2, y: m.t, width: bw + gap, height: H - m.t - m.b, fill: "transparent" }, svg);
        const show = ev => {
          const extra = o.tipExtra ? o.tipExtra(ci, gi) : "";
          tip.innerHTML = `<b>${esc(c)} · ${esc(g.name)}</b><div class="r"><span>${esc(o.metricName || "value")}</span><span>${(o.yFmt || String)(v)}</span></div>${extra}`;
          tip.style.display = "block";
          const hr = host.getBoundingClientRect(), r = svg.getBoundingClientRect();
          const px = ((x + bw / 2) / W) * r.width + r.left - hr.left;
          tip.style.left = (px > hr.width * 0.6 ? px - tip.offsetWidth - 10 : px + 10) + "px";
          tip.style.top = ((y / H) * r.height + r.top - hr.top - 10) + "px";
          p.setAttribute("opacity", 0.85);
        };
        hitr.addEventListener("mousemove", show);
        hitr.addEventListener("mouseleave", () => { tip.style.display = "none"; p.removeAttribute("opacity"); });
      });
    });
  }

  window.Charts = { line, bars };
})();
