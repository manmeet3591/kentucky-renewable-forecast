(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (v, d = 0) => v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => v == null || !isFinite(v) ? "–" : fmt(v, d) + " %";
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const LEADN = { d0: "Day 0", d1: "Day 1", d2: "Day 2", d3: "Day 3" };
  const FORMN = { raw: "physics only", scale: "single scale factor", ridge: "ridge on diurnal / seasonal shape", gbm: "small boosted trees",
    "scale+roll": "rolling 30-day level", "ridge+roll": "ridge + rolling 30-day level", "gbm+roll": "boosted trees + rolling 30-day level" };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let M, P, map, layer, ptLayer;
  const S = { tech: { solar: true, wind: true }, ba: { LGEE: true, TVA: true, PJM: true, MISO: true }, area: "all",
    key: null, lead: "d1", raw: false, clim: false, week: null, skKey: null, metric: "nrmse" };
  const tsCache = {};
  const label = k => { const [b, t] = k.split("_"); return `${b} ${t}`; };

  // ---------------- tabs ----------------
  document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
    ["plants", "fc", "skill", "method"].forEach(v => $("#v-" + v).classList.toggle("hidden", v !== b.dataset.view));
    if (b.dataset.view === "plants") map.invalidateSize();
    if (b.dataset.view === "fc") drawFc();
    if (b.dataset.view === "skill") drawSkill();
  }));

  Promise.all(["data/meta.json", "data/plants.json"].map(u => fetch(u).then(r => r.json()))).then(([m, p]) => {
    M = m; P = p;
    document.querySelectorAll(".built").forEach(e => e.textContent = M.built);
    document.querySelectorAll(".testp").forEach(e => e.textContent = M.split.hourly_eia930.test.join(" to "));
    S.key = S.skKey = "PJM_solar";
    initPlants();
    initFc();
    initSkill();
    methodText();
    let t;
    addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { if (!$("#v-fc").classList.contains("hidden")) drawFc(); if (!$("#v-skill").classList.contains("hidden")) drawSkill(); }, 150); });
  });

  // ---------------- plants ----------------
  function visible(p) {
    if (!S.tech[p.tech] || !S.ba[p.ba]) return false;
    if (S.area === "ky") return p.ky;
    if (S.area === "near") return p.near || p.ky;
    return true;
  }
  function initPlants() {
    map = L.map("map", { preferCanvas: true, zoomSnap: 0.25 }).setView([38.5, -87.5], 5.5);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const esri = s => `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${s}/MapServer/tile/{z}/{y}/{x}`;
    L.tileLayer(esri(dark ? "World_Dark_Gray_Base" : "World_Light_Gray_Base"), {
      attribution: "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors", maxZoom: 16 }).addTo(map);
    map.createPane("labels").style.zIndex = 450;
    map.getPane("labels").style.pointerEvents = "none";
    L.tileLayer(esri(dark ? "World_Dark_Gray_Reference" : "World_Light_Gray_Reference"), { pane: "labels", maxZoom: 16 }).addTo(map);
    layer = L.layerGroup().addTo(map);
    ptLayer = L.layerGroup();
    const bas = ["LGEE", "TVA", "PJM", "MISO"];
    $("#fBa").innerHTML = bas.map(b => `<label class="chk"><input type="checkbox" data-ba="${b}" checked> ${b} <span class="n" id="nb-${b}"></span></label>`).join("") +
      `<label class="chk" style="margin-top:8px"><input type="checkbox" id="fPts"> Weather points (${M.fleet.n_points}) <span class="n">+</span></label>`;
    $("#fBa").querySelectorAll("[data-ba]").forEach(i => i.addEventListener("change", () => { S.ba[i.dataset.ba] = i.checked; drawPlants(); }));
    $("#fPts").addEventListener("change", e => e.target.checked ? ptLayer.addTo(map) : ptLayer.remove());
    $("#fSolar").addEventListener("change", e => { S.tech.solar = e.target.checked; drawPlants(); });
    $("#fWind").addEventListener("change", e => { S.tech.wind = e.target.checked; drawPlants(); });
    $("#fArea").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      $("#fArea").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      S.area = b.dataset.a; drawPlants();
      if (S.area === "ky") map.fitBounds([[36.4, -89.6], [39.2, -81.9]]);
      else if (S.area === "near") map.fitBounds([[34.8, -92], [42.2, -77]]);
      else map.setView([38.5, -87.5], 5.5);
    }));
    $("#plantList").innerHTML = P.plants.map(p => `<option value="${esc(p.name)}">`).join("");
    $("#search").addEventListener("change", e => {
      const p = P.plants.find(q => q.name === e.target.value);
      if (p) { map.setView([p.lat, p.lon], 9); showPlant(p); }
    });
    M.points.forEach(q => L.marker([q.lat, q.lon], { icon: L.divIcon({ className: "", html: `<div style="font:700 14px/1 system-ui;color:${css("--ink")}">+</div>`, iconSize: [10, 10], iconAnchor: [5, 7] }) })
      .bindTooltip(`<b>Weather point ${esc(q.point)}</b><span class="m">${esc(q.ba)} ${q.tech} · ${q.n_gen} generators · ${fmt(q.mw)} MW</span>`, { className: "tt" }).addTo(ptLayer));
    // capacity table
    const by = {};
    M.fleet.by.forEach(r => { by[r.ba] = by[r.ba] || {}; by[r.ba][r.tech] = r; });
    $("#capTbl").innerHTML = `<tr><th>Area</th><th>Solar MW</th><th>Wind MW</th></tr>` + bas.map(b =>
      `<tr><td>${b}</td><td>${fmt(by[b]?.solar?.mw)}</td><td>${fmt(by[b]?.wind?.mw)}</td></tr>`).join("");
    $("#legend").innerHTML = `<div class="t">Plants (circle area ∝ MW)</div>
      <div class="row"><span class="sw solar"></span>Solar PV</div><div class="row"><span class="sw wind"></span>Wind</div>
      <div class="row" style="color:var(--muted)">EIA-860M, operating Sept 2026, ≥ 1 MW</div>`;
    drawPlants();
  }
  function drawPlants() {
    layer.clearLayers();
    const col = { solar: css("--solar"), wind: css("--wind") }, ring = css("--surface");
    const vis = P.plants.filter(visible).sort((a, b) => b.mw - a.mw);
    vis.forEach(p => {
      L.circleMarker([p.lat, p.lon], { radius: Math.max(2.5, Math.sqrt(p.mw) * 0.55), color: ring, weight: 1, fillColor: col[p.tech], fillOpacity: 0.8 })
        .bindTooltip(`<b>${esc(p.name)}</b><span class="m">${p.tech === "solar" ? "Solar" : "Wind"} · ${fmt(p.mw, 0)} MW · ${p.st} · ${p.ba}</span>`, { className: "tt" })
        .on("click", () => showPlant(p)).addTo(layer);
    });
    const cnt = t => P.plants.filter(p => p.tech === t && visible({ ...p, tech: p.tech })).length;
    $("#nSolar").textContent = fmt(cnt("solar"));
    $("#nWind").textContent = fmt(cnt("wind"));
    ["LGEE", "TVA", "PJM", "MISO"].forEach(b => $("#nb-" + b).textContent = fmt(P.plants.filter(p => p.ba === b && S.tech[p.tech] && (S.area === "all" || (S.area === "ky" ? p.ky : p.near || p.ky))).length));
    const mw = t => vis.filter(p => p.tech === t).reduce((a, p) => a + p.mw, 0);
    $("#pKpis").innerHTML = `<div class="kpi"><b>${fmt(mw("solar") / 1000, 1)} GW</b><span>solar shown (${fmt(vis.filter(p => p.tech === "solar").length)} plants)</span></div>
      <div class="kpi"><b>${fmt(mw("wind") / 1000, 1)} GW</b><span>wind shown (${fmt(vis.filter(p => p.tech === "wind").length)} plants)</span></div>
      <div class="kpi wide"><span>Kentucky itself: <b style="display:inline;font-size:14px">${fmt(M.fleet.ky.solar)} MW solar, ${fmt(M.fleet.ky.wind)} MW wind</b>. Wind within Kentucky's neighbouring states: ${fmt(M.fleet.near_wind)} MW.</span></div>`;
  }
  function showPlant(p) {
    const h = P.hist[p.id];
    const t = p.t923;
    $("#plant").innerHTML = `<div class="card"><h3>${esc(p.name)}</h3><div class="score">${p.tech === "solar" ? "Solar PV" : "Wind"} · ${fmt(p.mw, 1)} MW · ${esc(p.st)} · ${esc(p.ba)}</div>
      <div class="facts">
        <div><span>Operator</span><b>${esc(p.op)}</b></div>
        <div><span>In service</span><b>${p.on}</b></div>
        ${p.tech === "solar" ? `<div><span>Mount</span><b>${esc((p.mount || "").replace("single_axis_assumed", "single-axis (assumed, 2026 plant)").replace("single_axis", "single-axis tracker").replace("fixed", "fixed tilt"))}</b></div>
          <div><span>DC / AC ratio</span><b>${p.dcac ?? "1.3 (assumed)"}</b></div>` :
          `<div><span>Hub height</span><b>${p.hub ? fmt(p.hub) + " m" : "80 m (assumed)"}</b></div><div><span>Turbine</span><b>${esc(p.turb || "–")}</b></div>`}
        <div><span>Weather point</span><b>${esc(p.pt)}</b></div>
        <div><span>EIA plant code</span><b>${p.id}</b></div>
        ${t ? `<div class="h">EIA-923 test months (${t.n})</div><div><span>Reported</span><b>${fmt(t.a)} MWh</b></div><div><span>Calibrated day-1 model</span><b>${fmt(t.f)} MWh</b></div><div><span>Median monthly error</span><b>${pct(t.ape)}</b></div>` : ""}
      </div><div class="chart" id="chPlant"></div></div>`;
    if (h && h.length) {
      Charts.line($("#chPlant"), {
        title: "Monthly output, MWh", height: 170,
        caption: "EIA-923 reported vs day-1 forecast summed over the month (physics × area calibration factor).",
        x: h.map(r => r[0]), series: [{ name: "EIA-923", values: h.map(r => r[1]), color: "var(--s-act)" },
          { name: "Day-1 model", values: h.map(r => r[2]), color: "var(--s-fc)" }],
        yFmt: v => v >= 1000 ? fmt(v / 1000, 0) + "k" : fmt(v), tipFmt: v => fmt(v) + " MWh",
        xFmt: s => { const [y, m] = s.split("-"); return MON[+m - 1] + " " + y; },
        xTicks: x => [0, Math.floor(x.length / 2), x.length - 1] });
    } else $("#chPlant").innerHTML = `<p class="empty">Monthly chart shown for plants in Kentucky and neighbouring states.</p>`;
  }

  // ---------------- forecast vs actual ----------------
  function seriesButtons(host, cur, onPick) {
    host.innerHTML = M.series.map(s => `<button data-k="${s.key}" class="${s.key === cur ? "on" : ""}"><span>${s.ba} · ${s.tech}</span><span class="n">${fmt(s.cap_now / 1000, 1)} GW</span></button>`).join("");
    host.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      host.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); onPick(b.dataset.k);
    }));
  }
  function initFc() {
    seriesButtons($("#fcSeries"), S.key, k => { S.key = k; S.week = null; drawFc(); });
    $("#fcLead").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      $("#fcLead").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); S.lead = b.dataset.l; drawFc();
    }));
    $("#showRaw").addEventListener("change", e => { S.raw = e.target.checked; drawFc(); });
    $("#showClim").addEventListener("change", e => { S.clim = e.target.checked; drawFc(); });
  }
  function getTs(k) {
    if (tsCache[k]) return Promise.resolve(tsCache[k]);
    return fetch(`data/ts_${k}.json`).then(r => r.json()).then(d => {
      // expand onto a full hourly axis (QC-dropped hours become gaps)
      const t0 = Date.parse(d.t0), N = d.hours[d.hours.length - 1] + 1, full = { t0, N };
      ["actual", "d1", "d2", "d3", "raw_d1", "clim", "cap"].forEach(c => {
        const a = new Array(N).fill(null); d.hours.forEach((h, i) => a[h] = d[c][i]); full[c] = a;
      });
      return (tsCache[k] = full);
    });
  }
  const dstr = ms => { const d = new Date(ms); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`; };
  function drawFc() {
    if (!M || $("#v-fc").classList.contains("hidden")) return;
    const k = S.key, sc = M.scores[k].test[S.lead], meta = M.series.find(s => s.key === k);
    getTs(k).then(d => {
      // daily means (MW) over the whole test period
      const days = Math.ceil(d.N / 24), x = [], A = [], F = [], R = [], C = [];
      const fc = d[S.lead];
      for (let i = 0; i < days; i++) {
        let a = 0, f = 0, r = 0, c = 0, n = 0;
        for (let h = i * 24; h < Math.min(d.N, i * 24 + 24); h++) if (d.actual[h] != null && fc[h] != null) { a += d.actual[h]; f += fc[h]; r += d.raw_d1[h] ?? 0; c += d.clim[h] ?? 0; n++; }
        x.push(d.t0 + i * 864e5);
        const ok = n >= 18;
        A.push(ok ? a / n : null); F.push(ok ? f / n : null); R.push(ok ? r / n : null); C.push(ok ? c / n : null);
      }
      const ser = [{ name: "EIA-930 actual", values: A, color: "var(--s-act)" }, { name: `${LEADN[S.lead]} forecast`, values: F, color: "var(--s-fc)" }];
      if (S.raw) ser.push({ name: "Day 1 physics only", values: R, color: "var(--s-raw)" });
      if (S.clim) ser.push({ name: "Climatology", values: C, color: "var(--s-ref)", dash: true });
      const mfmt = v => v >= 1000 ? fmt(v / 1000, 1) + " GW" : fmt(v) + " MW";
      Charts.line($("#chDaily"), {
        title: `${meta.ba} ${meta.tech}: daily mean output, test period`, height: 220,
        caption: "Daily average MW. Click a day to show that week hour by hour below.",
        x, series: ser, yFmt: mfmt, xFmt: dstr,
        xTicks: x => { const out = []; for (let i = 0; i < x.length; i += Math.ceil(x.length / 6)) out.push(i); return out; },
        onClick: i => { S.week = Math.max(0, i - 3); drawWeek(d); } });
      if (S.week == null) {
        // default: the week with the largest day-1 error
        let best = 0, bi = 0;
        for (let i = 0; i + 7 <= days; i += 1) { let e = 0; for (let j = i; j < i + 7; j++) if (A[j] != null) e += Math.abs(F[j] - A[j]); if (e > best) { best = e; bi = i; } }
        S.week = bi;
      }
      drawWeek(d);
      fcCard(k, sc, meta);
    });
  }
  function drawWeek(d) {
    const s0 = S.week * 24, n = Math.min(7 * 24, d.N - s0), x = [];
    for (let i = 0; i < n; i++) x.push(d.t0 + (s0 + i) * 36e5);
    const sl = a => a.slice(s0, s0 + n);
    const ser = [{ name: "EIA-930 actual", values: sl(d.actual), color: "var(--s-act)" }, { name: `${LEADN[S.lead]} forecast`, values: sl(d[S.lead]), color: "var(--s-fc)" }];
    if (S.raw) ser.push({ name: "Day 1 physics only", values: sl(d.raw_d1), color: "var(--s-raw)" });
    if (S.clim) ser.push({ name: "Climatology", values: sl(d.clim), color: "var(--s-ref)", dash: true });
    const host = $("#chWeek");
    const mfmt = v => v >= 1000 ? fmt(v / 1000, 1) + " GW" : fmt(v) + " MW";
    const hfmt = ms => { const t = new Date(ms); return `${MON[t.getUTCMonth()]} ${t.getUTCDate()} ${String(t.getUTCHours()).padStart(2, "0")}:00 UTC`; };
    Charts.line(host, { title: `Hourly, week of ${dstr(x[0])}`, height: 260,
      caption: "Hour-ending UTC. Gaps are hours missing from EIA-930 or the archived forecast.",
      x, series: ser, yFmt: mfmt, xFmt: dstr, tipX: hfmt,
      xTicks: x => [0, 48, 96, 144].filter(i => i < x.length) });
    const nav = document.createElement("div");
    nav.className = "nav";
    nav.innerHTML = `<button id="wPrev">← Week</button><button id="wNext">Week →</button><span class="hint" style="margin:0">Starting ${dstr(x[0])}</span>`;
    host.insertBefore(nav, host.querySelector("svg"));
    nav.querySelector("#wPrev").onclick = () => { S.week = Math.max(0, S.week - 7); drawWeek(d); };
    nav.querySelector("#wNext").onclick = () => { S.week = Math.min(Math.floor(d.N / 24) - 7, S.week + 7); drawWeek(d); };
  }
  function fcCard(k, sc, meta) {
    const c = sc.corrected, sk = v => v == null ? "–" : (v >= 0 ? "+" : "") + fmt(v * 100, 0) + " %";
    const note = {
      LGEE_solar: "LGEE's EIA-930 solar series does not track the EIA-860 plant list (it reports more than the listed plants in early 2025 and less than Bluebird Solar alone reports to EIA-923 in 2026). Scores here measure agreement with what LGEE reports, not plant skill.",
      TVA_solar: "TVA's EIA-930 solar includes plants under contract to TVA; the EIA-860M list assigns some of them to other areas, so a level factor absorbs the difference.",
      MISO_wind: "MISO wind spans Manitoba to Louisiana; most of it is far from Kentucky, which has no wind of its own.",
      PJM_wind: "Most PJM wind is in Illinois, Indiana, Ohio, Pennsylvania and West Virginia.",
    }[k];
    $("#fcCard").innerHTML = `<div class="kpis">
      <div class="kpi"><b>${pct(c.nrmse)}</b><span>${LEADN[S.lead]} nRMSE (corrected)</span></div>
      <div class="kpi"><b>${pct(sc.raw.nrmse)}</b><span>physics only</span></div>
      <div class="kpi"><b>${sk(c.skill_vs_persistence)}</b><span>skill vs persistence</span></div>
      <div class="kpi"><b>${sk(c.skill_vs_climatology)}</b><span>skill vs climatology</span></div></div>
      <div class="facts">
        <div><span>Correction chosen on validation</span><b>${esc(FORMN[c.form])}</b></div>
        <div><span>nMAE corrected / physics</span><b>${pct(c.nmae)} / ${pct(sc.raw.nmae)}</b></div>
        <div><span>Bias corrected / physics</span><b>${pct(c.bias)} / ${pct(sc.raw.bias)}</b></div>
        <div><span>MAE</span><b>${fmt(c.mae_mw)} MW</b></div>
        <div><span>Mean actual (scored hours)</span><b>${fmt(c.mean_actual_mw)} MW</b></div>
        <div><span>Installed in model, now</span><b>${fmt(meta.cap_now)} MW</b></div>
        <div><span>Scored hours</span><b>${fmt(c.n)}</b></div>
      </div>
      <p class="hint">Errors in % of installed capacity. Skill = 1 − nRMSE / nRMSE of the baseline; positive is better. Persistence = same hour ${S.lead === "d1" ? "1 day" : S.lead === "d2" ? "2 days" : "3 days"} earlier. ${meta.tech === "solar" ? "Daylight hours only." : ""}</p>
      ${note ? `<div class="callout">${note}</div>` : ""}`;
  }

  // ---------------- skill ----------------
  function initSkill() {
    seriesButtons($("#skSeries"), S.skKey, k => { S.skKey = k; drawSkill(); });
    $("#skMetric").querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      $("#skMetric").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); S.metric = b.dataset.m; drawSkill();
    }));
    const L = M.lead_diag || {};
    $("#leadDiag").innerHTML = Object.keys(L).length ? `<div class="tablewrap"><table><tr><th>Forecast</th><th>RMSE</th><th>Bias</th></tr>${Object.entries(L).map(([k, v]) => `<tr><td>${k}</td><td>${fmt(v.rmse)} W/m²</td><td>${fmt(v.bias)} W/m²</td></tr>`).join("")}</table></div>
      <p class="hint">Hourly GHI at the ${M.points.filter(p => p.point.startsWith("KY_")).length} Kentucky solar points, daylight hours, against HRRR day 0 as the reference. Same lead (day 1), different model: HRRR vs GFS. The gap between them is the part of the day-1 → day-2 jump that comes from switching models rather than looking further ahead.</p>` : `<p class="empty">Not available.</p>`;
  }
  function drawSkill() {
    if (!M || $("#v-skill").classList.contains("hidden")) return;
    const k = S.skKey, t = M.scores[k].test, met = S.metric, mN = { nrmse: "nRMSE", nmae: "nMAE", bias: "Bias" }[met];
    const leads = ["d0", "d1", "d2", "d3"];
    const v = f => leads.map(l => t[l]?.[f]?.[met] ?? null);
    Charts.line($("#chLead"), {
      title: `${label(k)}: ${mN} by lead time, test period`, height: 230,
      caption: "Day 0 uses HRRR's same-day forecast as a stand-in for observed weather, so it shows the error left in the power model itself. Persistence and climatology are reference baselines.",
      x: leads, series: [{ name: "Corrected forecast", values: v("corrected"), color: "var(--s-fc)" }, { name: "Physics only", values: v("raw"), color: "var(--s-raw)" },
        { name: "Persistence", values: v("persistence"), color: "var(--s-ref)", dash: true }, { name: "Climatology", values: v("climatology"), color: "var(--s-act)", dash: true }],
      yFmt: x => fmt(x, met === "bias" ? 1 : 0) + " %", tipFmt: x => fmt(x, 1) + " %", xFmt: l => LEADN[l] + (l === "d0" ? " (diag.)" : ""), xTicks: x => [0, 1, 2, 3] });
    const W = M.weather_types[k], cls = Object.keys(W.cls), order = ["clear", "partly cloudy", "overcast", "low output", "moderate output", "high output", "ramp day"];
    cls.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    const seas = ["winter", "spring", "summer", "autumn"].filter(s => W.season[s]);
    const cats = [...cls, ...seas], src = c => W.cls[c] || W.season[c];
    Charts.bars($("#chWx"), {
      title: `${label(k)}: nMAE by weather type and season, test period`, height: 230, metricName: "nMAE",
      caption: k.endsWith("solar") ? "Day classes from the reported output itself: clear ≥ 80 % of the 31-day best day, overcast < 50 %. Daylight hours." : "Day classes from reported output: terciles of daily capacity factor; ramp days = top 10 % of the largest 3-hour swing.",
      cats, groups: ["d1", "d2", "d3"].map((l, i) => ({ name: LEADN[l], values: cats.map(c => src(c)[l]), color: `var(--l${i + 1})` })),
      yFmt: x => fmt(x, 1) + " %", tipExtra: ci => `<div class="r"><span>days</span><span>${src(cats[ci]).days}</span></div><div class="r"><span>physics only, day 1</span><span>${fmt(src(cats[ci]).raw_d1, 1)} %</span></div>` });
    // table
    const rows = [];
    M.series.forEach(s => ["d1", "d2", "d3"].forEach(l => {
      const r = M.scores[s.key].test[l]; if (!r) return;
      const c = r.corrected, skl = x => `<span class="${x >= 0 ? "good" : "bad"}">${(x >= 0 ? "+" : "") + fmt(x * 100, 0)} %</span>`;
      rows.push(`<tr class="${s.key === k ? "sel" : ""}"><td>${label(s.key)}</td><td>${LEADN[l]}</td><td>${fmt(c.nrmse, 1)}</td><td>${fmt(r.raw.nrmse, 1)}</td><td>${fmt(r.persistence.nrmse, 1)}</td><td>${fmt(r.climatology.nrmse, 1)}</td><td>${skl(c.skill_vs_persistence)}</td><td>${skl(c.skill_vs_climatology)}</td><td>${fmt(c.bias, 1)}</td><td style="text-align:left">${esc(FORMN[c.form])}</td></tr>`);
    }));
    $("#skTbl").innerHTML = `<tr><th>Series</th><th>Lead</th><th>nRMSE %</th><th>Physics %</th><th>Persist. %</th><th>Clim. %</th><th>Skill vs persist.</th><th>Skill vs clim.</th><th>Bias %</th><th style="text-align:left">Correction</th></tr>` + rows.join("");
    const E = M.eia923, er = (n, x) => x ? `<tr><td>${n}</td><td>${fmt(x.n_plants)}</td><td>${fmt(x.n_plant_months)}</td><td>${pct(x.mwh_f.median_ape)}</td><td>${pct(x.corr.median_ape)}</td><td>${pct(x.ly.median_ape)} <span class="neg">(${x.ly.n})</span></td><td>${pct(x.corr.cf_mae, 2)}</td><td>${pct(x.corr.total_bias)}</td></tr>` : "";
    $("#t923").innerHTML = `<tr><th>Plants</th><th>Plants</th><th>Plant-months</th><th>Physics median |err|</th><th>Calibrated median |err|</th><th>Same month last year</th><th>Calibrated MAE (% cap.)</th><th>Calibrated total bias</th></tr>` +
      er("All", E.all) + er("Solar", E.solar) + er("Wind", E.wind) + er("Solar, KY + neighbours", E.near_ky_solar) + er("Wind, KY's neighbours", E.near_ky_wind) + er("Kentucky plants", E.ky);
    $("#t923note").textContent = `Test months ${M.split.monthly_eia923.test.join(" to ")} (EIA-923 2026 year to date), monthly respondents only, partial first months dropped. Day-1 forecasts are summed over each month and multiplied by an area × technology factor fitted on ${M.split.monthly_eia923.calibrate.join(" to ")}. "Same month last year" uses the plant's own 2025 value where it ran all of that month (count in brackets).`;
    const c1 = t.d1.corrected;
    $("#skCard").innerHTML = `<div class="kpis"><div class="kpi"><b>${pct(c1.nrmse)}</b><span>day-1 nRMSE</span></div><div class="kpi"><b>${pct(t.d3.corrected.nrmse)}</b><span>day-3 nRMSE</span></div>
      <div class="kpi"><b>${pct(t.d0.corrected.nrmse)}</b><span>day-0 (power model floor)</span></div><div class="kpi"><b>${pct(t.d1.persistence.nrmse)}</b><span>day-1 persistence</span></div></div>
      <p class="hint">Correction per lead, chosen on ${M.split.hourly_eia930.valid.join(" to ")} after fitting on ${M.split.hourly_eia930.train.join(" to ")}, then refit on both and scored once on ${M.split.hourly_eia930.test.join(" to ")}.</p>`;
  }

  // ---------------- method ----------------
  function methodText() {
    const sp = M.split.hourly_eia930, f = M.fleet;
    $("#methodText").innerHTML = `
    <h2>What this shows</h2>
    <p>A prototype forecast of utility-scale <b>solar and wind output 1–3 days ahead</b> for the balancing areas that serve Kentucky: <b>LG&amp;E-KU (LGEE)</b>, <b>TVA</b>, <b>PJM</b> and <b>MISO</b>. It covers ${fmt(f.n_plants)} plants. The forecasts are scored honestly against what the areas reported to EIA, on 2026 data that was held out before any fitting. Kentucky itself has ${fmt(f.ky.solar)} MW of utility-scale solar and <b>no utility-scale wind</b>. The wind forecasts therefore cover the PJM and MISO fleets, of which ${fmt(f.near_wind)} MW is in Kentucky's neighbouring states.</p>
    <h3>1. Plants</h3>
    <ul>
      <li><b>EIA-860M</b> (September 2026 monthly generator inventory): every operating or recently retired solar PV and wind generator ≥ 1 MW whose balancing area is LGEE, TVA, PJM or MISO, with location, nameplate MW and in-service month. Each generator counts from its in-service month, so fleet growth (MISO and PJM solar roughly doubled over 2024–26) is in the plant list and not left for the correction to absorb.</li>
      <li><b>EIA-860 2025</b> (annual) adds the details: tracking (single-axis, fixed, dual), tilt and azimuth, DC capacity (DC/AC ratio), hub height, turbine make and model, and design wind speed. Plants new in 2026 appear only in 860M; they are assumed to be single-axis trackers with a DC/AC ratio of 1.3 and 80 m hubs.</li>
      <li>To keep the weather download small, generators share <b>${f.n_points} weather points</b>. Kentucky and LG&amp;E-KU plants each keep their own point (plants within ~5 km share one). Elsewhere, capacity-weighted k-means on location per area and technology picks the points.</li>
    </ul>
    <h3>2. Physics</h3>
    <ul>
      <li><b>Solar (pvlib)</b>: forecast GHI (hour mean) → sun position at mid-hour → Erbs split into direct and diffuse → Perez plane-of-array irradiance for the plant's mount (single-axis tracker ±55° with backtracking, GCR 0.35; fixed tilt at the EIA tilt and azimuth, 25° south by default; dual-axis) → SAPM cell temperature (open rack) → PVWatts DC (−0.35 %/K, 14 % losses) → inverter clipping at AC nameplate.</li>
      <li><b>Wind</b>: forecast 80 m wind → hub height by power law (α = 0.14) → air-density adjustment from forecast temperature and site elevation → a generic turbine curve (cut-in 3 m/s, rated at the EIA design wind speed or 11.5 m/s, cut-out 25 m/s), smoothed with a 1.5 m/s Gaussian to stand in for spread across the farm → 10 % wake and availability loss.</li>
    </ul>
    <h3>3. Weather forecasts</h3>
    <ul>
      <li>Archived NOAA forecasts from the <a href="https://open-meteo.com/en/docs/previous-runs-api">Open-Meteo Previous Runs API</a>, model feed <code>gfs_seamless</code>. We checked that it is <b>HRRR (3 km) for leads under 48 h and GFS (0.25°) beyond</b>. "Day 1" is the forecast made 24–47 h before the hour (HRRR), "Day 2" 48–71 h and "Day 3" 72–95 h (both GFS). "Day 0" (0–23 h, HRRR) stands in for observed weather to show how much error comes from the power model itself.</li>
      <li>At the Kentucky points we also pulled pure GFS at day 1. This separates the effect of the HRRR → GFS switch from the effect of a longer lead (see <i>Error &amp; skill</i>).</li>
    </ul>
    <h3>4. Correction and scoring</h3>
    <ul>
      <li>Target: <b>EIA-930 hourly net generation</b> from solar and wind by balancing area (EIA's "adjusted" values, with and without integrated storage summed). Scored series: LGEE solar, TVA solar, PJM solar and wind, MISO solar and wind. LGEE has no wind; TVA reports essentially zero wind to EIA-930.</li>
      <li>The split was <b>locked in <code>config/split.json</code> and committed before any fitting</b>: train ${sp.train.join(" to ")}, validation ${sp.valid.join(" to ")}, test ${sp.test.join(" to ")}.</li>
      <li>Candidate corrections, all in capacity-normalised units and fitted per series and lead on CPU: a single scale factor; ridge regression on diurnal and seasonal shape; small gradient-boosted trees (depth 3) on (physics, hour, day of year); and each of these times a <b>rolling 30-day level factor</b>. That factor is the ratio of reported to forecast output over the last 30 days that were known at issue time, and it tracks reporting and fleet changes the plant list misses. The best form on validation (fit on train only) is refit on train + validation and scored once on test.</li>
      <li>Baselines: same-hour <b>persistence</b> 1, 2 or 3 days earlier, and hour × month <b>climatology</b> of capacity factor (from train + validation) times current capacity. Errors are in % of installed capacity, and solar is scored on daylight hours.</li>
      <li><b>EIA-923</b> monthly plant totals: an area × technology factor fitted on ${M.split.monthly_eia923.calibrate.join(" to ")} scales each plant's month of day-1 forecasts. These are scored on ${M.split.monthly_eia923.test.join(" to ")} for plants that report monthly.</li>
    </ul>
    <h3>Hook for Aircast SR (1 km / 1 h)</h3>
    <p>The physics step only needs hourly GHI, 2 m temperature and 80 m (or hub-height) wind at each weather point. To run it on DSOC's Aircast SR fields, write them in the same per-point JSON layout (<code>data/raw/weather/&lt;point&gt;__aircast__&lt;start&gt;.json</code>, with variables named as in Open-Meteo) and run <code>python pipeline/05_physics.py aircast</code>. At 1 km, each Kentucky plant can get its own pixel instead of a shared point, and the same scoring and baselines apply unchanged. <code>tacc/</code> holds SLURM templates for anything neural (e.g. learning the correction jointly with Aircast SR, or a fleet-level probabilistic model); nothing here was trained locally.</p>
    <h3>Limitations</h3>
    <ul>
      <li><b>EIA-930 is not the same fleet as EIA-860.</b> Areas report the solar and wind they dispatch or contract, which does not match the 860 balancing-area field plant for plant. LGEE is the clearest case: its reported solar does not follow its listed plants. The rolling level factor follows these drifts, but it also hides step changes for up to a month.</li>
      <li><b>Curtailment and outages</b> are not modelled. MISO and PJM wind curtailment shows up as over-forecast on windy nights; plant outages show up as bias.</li>
      <li><b>Weather points</b>: outside Kentucky, plants share points tens of km wide, which smooths local cloud. This matters less for area totals than for single plants.</li>
      <li><b>Generic turbine curve</b>: not per-model manufacturer curves; there are no wake models, icing or high-wind hysteresis, and shear is fixed rather than varying with stability, which is a known day/night error for 80–120 m hubs.</li>
      <li><b>No behind-the-meter solar</b>: EIA-930 does not include it either, but it reduces demand, which matters for the load and adequacy work.</li>
      <li><b>Point forecasts only</b>: no uncertainty bands yet. The error-by-weather-type breakdown is a first step towards conditional intervals.</li>
      <li>Persistence is optimistic for day 1 in operations, because day-ahead forecasts are issued before the previous day's full actuals are in.</li>
      <li>Prototype for research and screening; not an operational forecast.</li>
    </ul>
    <h3>Data sources</h3>
    <ul>
      <li>U.S. EIA: <a href="https://www.eia.gov/electricity/data/eia860/">EIA-860</a> (2025), <a href="https://www.eia.gov/electricity/data/eia860m/">EIA-860M</a> (Sept 2026), <a href="https://www.eia.gov/electricity/data/eia923/">EIA-923</a> (2024, 2025 final, 2026 year to date), <a href="https://www.eia.gov/electricity/gridmonitor/">EIA-930</a> six-month balance files (public domain).</li>
      <li>NOAA NCEP HRRR and GFS forecasts via <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0).</li>
      <li><a href="https://pvlib-python.readthedocs.io/">pvlib-python</a>; basemap © Esri, OpenStreetMap contributors.</li>
    </ul>
    <p class="hint">Code and pipeline: see the repository README. Built ${M.built}.</p>`;
  }
})();
