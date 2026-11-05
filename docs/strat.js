/* Stratosphere watch - dashboard section. Reads data/strat.json written by agent/strat.py. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const DATA_URL = "data/strat.json";
  // identity colours, validated for the dark surface (same order as the validator run)
  const COL = { gfs_seamless: "#3987e5", ecmwf_ifs025: "#d95926", ecmwf_aifs025_single: "#199e70", ukmo_seamless: "#c98500", gem_seamless: "#d55181" };
  const ENS_COL = { ecmwf_ifs025: COL.ecmwf_ifs025, ecmwf_aifs025: COL.ecmwf_aifs025_single, gfs05: COL.gfs_seamless };
  const ORDER = ["gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025_single", "ukmo_seamless", "gem_seamless"];
  const state = { data: null, level: 10, wave: 1 };

  const dt = (iso) => new Date(iso + "T12:00:00Z");
  const fmtDate = (iso) => dt(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const fmtDM = (iso) => dt(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const addDays = (iso, n) => new Date(dt(iso).getTime() + n * 86400000).toISOString().slice(0, 10);
  const diffDays = (a, b) => Math.round((dt(b) - dt(a)) / 86400000);
  const num = (v, d = 0) => (v == null ? "–" : (+v).toFixed(d).replace("-", "−"));
  const pct = (p) => (p == null ? "–" : Math.round(p * 100) + "%");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const niceTicks = (lo, hi, count) => {
    const raw = (hi - lo) / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  };
  const segs = (arr) => { const out = []; let cur = []; arr.forEach((v, i) => { if (v == null) { if (cur.length) out.push(cur); cur = []; } else cur.push([i, v]); }); if (cur.length) out.push(cur); return out; };

  // ---------- load ----------
  async function load() {
    try {
      const r = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      if (!d.assessment) throw new Error("no assessment");
      state.data = d;
      $("#strat").hidden = false;
      render();
    } catch (e) {
      // the section stays hidden when the stratosphere data has not been produced yet
      if (!state.data) $("#strat").hidden = true;
    }
  }

  // ---------- generic chart ----------
  function lineChart(host, cfg) {
    const W = Math.max(280, host.clientWidth || 600), H = cfg.h || 270;
    const wide = W >= 640;
    const m = { l: 48, r: cfg.labels && wide ? 118 : 12, t: 12, b: 30 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, n = cfg.dates.length;
    const x = (i) => m.l + (n > 1 ? (i / (n - 1)) * iw : 0), y = (v) => m.t + ih - ((v - cfg.y0) / (cfg.y1 - cfg.y0)) * ih;
    const line = (arr) => segs(arr).map((sg) => "M" + sg.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L")).join("");
    const bandPath = (lo, hi) => segs(hi.map((v, i) => (v == null || lo[i] == null ? null : v))).map((sg) =>
      "M" + sg.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L") + "L" + [...sg].reverse().map(([i]) => `${x(i).toFixed(1)},${y(lo[i]).toFixed(1)}`).join("L") + "Z").join("");
    let g = "";
    for (const v of cfg.yTicks) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" opacity=".7"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${cfg.yFmt(v)}</text>`;
    const step = n > 28 ? 7 : n > 16 ? 4 : 2;
    cfg.dates.forEach((d, i) => { if (i % step === 0) g += `<line x1="${x(i)}" x2="${x(i)}" y1="${m.t + ih}" y2="${m.t + ih + 4}" stroke="var(--ink-3)"/><text x="${x(i)}" y="${H - 9}" text-anchor="middle">${fmtDM(d)}</text>`; });
    let body = "";
    for (const b of cfg.bands || []) body += `<path d="${bandPath(b.lo, b.hi)}" fill="${b.color}" opacity="${b.op || 0.18}"/>`;
    for (const h of cfg.hlines || []) {
      body += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(h.y)}" y2="${y(h.y)}" stroke="${h.color || "var(--ink-2)"}" stroke-width="1.5" stroke-dasharray="${h.solid ? "0" : "5 4"}"/>`;
      body += `<text class="lbl" x="${m.l + 6}" y="${y(h.y) + (h.below ? 16 : -6)}">${esc(h.label)}</text>`;
    }
    for (const s of cfg.series) body += `<path d="${line(s.vals)}" fill="none" stroke="${s.color}" stroke-width="${s.w || 2}" stroke-dasharray="${s.dash || "0"}" stroke-linejoin="round" stroke-linecap="round"/>`;
    for (const mk of cfg.markers || []) { const i = cfg.dates.indexOf(mk.date); if (i >= 0) body += `<line x1="${x(i)}" x2="${x(i)}" y1="${m.t}" y2="${m.t + ih}" stroke="${mk.color || "var(--ink-2)"}" stroke-dasharray="2 4" stroke-width="1.5"/>`; }
    // direct labels at the line ends, nudged apart
    let labels = "";
    if (cfg.labels && wide) {
      const ends = cfg.series.map((s) => { let li = -1; s.vals.forEach((v, i) => { if (v != null) li = i; }); return li < 0 ? null : { s, i: li, y: y(s.vals[li]) }; }).filter(Boolean).sort((a, b) => a.y - b.y);
      for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
      for (const e of ends) labels += `<text x="${Math.min(x(e.i) + 7, W - m.r + 6)}" y="${e.y + 4}" fill="${e.s.color}" style="fill:var(--ink)" font-weight="600"><tspan fill="${e.s.color}">●</tspan> ${esc(e.s.short || e.s.label)}</text>`;
    }
    const cid = "s" + Math.random().toString(36).slice(2, 8);
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.aria)}"><clipPath id="${cid}"><rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}"/></clipPath>${g}<g clip-path="url(#${cid})">${body}</g>${labels}<line class="mk" y1="${m.t}" y2="${m.t + ih}" stroke="var(--ink)" stroke-width="1.5" opacity="0"/><rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" style="touch-action:pan-y"/></svg><div class="tip"></div>`;
    const mk = host.querySelector(".mk"), tip = host.querySelector(".tip"), hit = host.querySelector(".hit");
    const at = (ev) => { const r = hit.getBoundingClientRect(); return Math.max(0, Math.min(n - 1, Math.round(((ev.clientX - r.left) / r.width) * (n - 1)))); };
    const move = (ev) => {
      const i = at(ev);
      mk.setAttribute("x1", x(i)); mk.setAttribute("x2", x(i)); mk.setAttribute("opacity", 1);
      let html = `<b>${fmtDate(cfg.dates[i])}</b>`;
      for (const s of cfg.series) if (s.vals[i] != null) html += `<span><em><i class="dot" style="background:${s.color}"></i>${esc(s.label)}</em><b style="font:600 .9rem var(--font)">${cfg.fmtVal(s.vals[i])}</b></span>`;
      for (const b of cfg.bands || []) if (b.lo[i] != null && b.label) html += `<span><em><i class="dot band" style="background:${b.color}"></i>${esc(b.label)}</em><b style="font:600 .9rem var(--font)">${cfg.fmtVal(b.lo[i])} to ${cfg.fmtVal(b.hi[i])}</b></span>`;
      tip.innerHTML = html; tip.style.opacity = 1;
      const px = (x(i) / W) * host.clientWidth, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(host.clientWidth - tw, px + 14 > host.clientWidth - tw ? px - tw - 14 : px + 14)) + "px";
      tip.style.top = "8px";
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => { tip.style.opacity = 0; mk.setAttribute("opacity", 0); });
  }

  const unionDates = (lists) => {
    const all = lists.flat();
    if (!all.length) return [];
    const lo = all.reduce((a, b) => (a < b ? a : b)), hi = all.reduce((a, b) => (a > b ? a : b));
    return Array.from({ length: diffDays(lo, hi) + 1 }, (_, i) => addDays(lo, i));
  };
  const align = (dates, src, vals) => { const idx = new Map(src.map((d, i) => [d, i])); return dates.map((d) => (idx.has(d) && vals[idx.get(d)] != null ? vals[idx.get(d)] : null)); };

  // ---------- render ----------
  function render() {
    const d = state.data, a = d.assessment;
    renderVerdict(a); renderFacts(a);
    document.querySelectorAll("[data-slevel]").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.slevel === state.level));
    document.querySelectorAll("[data-swave]").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.swave === state.wave));
    renderWind(); renderProb(); renderWaves(); renderHeat();
    $("#s-leg-models").innerHTML = modelChips(ORDER.filter((id) => state.data.det.models[id])); renderTimeline(a); renderBrief(a); renderHow(d);
    renderHero(a);
  }

  function renderHero(a) {
    const el = $("#s-hero");
    if (!el) return;
    el.hidden = false;
    el.className = "shero " + a.status;
    el.innerHTML = `Stratosphere: <a href="#strat">${esc(a.headline.toLowerCase())}</a>`;
  }

  function renderVerdict(a) {
    $("#s-verdict").className = "sverdict " + a.status;
    $("#s-verdict").innerHTML = `<p class="sv-h">${esc(a.headline)}</p><p class="sv-p">${esc(a.sub)}</p>`;
  }

  function renderFacts(a) {
    const n = a.now || {}, w = a.wave, h = a.heat, d = state.data;
    const ens10 = d.ens.series.filter((e) => e.level === 10);
    const ch = n.u10_change_7d;
    const facts = [
      ["Wind at 10 hPa, 60°N", n.u10 != null ? `${num(n.u10)} m/s` : "–", n.u10 != null ? (n.u10 >= 0 ? "westerly" : "easterly") + (ch == null ? "" : Math.abs(ch) < 1 ? ", steady over 7 days" : `, ${ch < 0 ? "down" : "up"} ${num(Math.abs(ch))} m/s in 7 days`) : ""],
      ["Wind at 50 hPa, 60°N", n.u50 != null ? `${num(n.u50)} m/s` : "–", n.u50 != null ? (n.u50 >= 0 ? "westerly" : "easterly") : ""],
      ["Planetary waves at 10 hPa", w ? `${num(w.w1_now)} m · ${num(w.w2_now)} m` : "–", w ? `wave 1 · wave 2 now. Peak is ${w.dominant}` : ""],
      ["Heat flux at 100 hPa", h ? `${num(h.max_peak)} K m/s` : "–", h ? `peak on ${fmtDM(h.max_date)}: ${h.level}` : ""],
      ["Polar warming", a.pole_warm_date ? fmtDM(a.pole_warm_date) : "not forecast", a.pole_warm_date ? "pole warmer than 60°N at 10 hPa" : "pole stays colder than 60°N"],
      ["Members that reverse the wind", ens10.length ? ens10.map((e) => pct(e.p_any)).join(" · ") : "–", ens10.length ? ens10.map((e) => e.name.replace(" ensemble", "")).join(" · ") : "no ensemble data"],
    ];
    $("#s-facts").innerHTML = facts.map(([k, v, s]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd><dd class="sub">${esc(s)}</dd></div>`).join("");
  }

  const SHORT = { ecmwf_ifs025: "ECMWF IFS", ecmwf_aifs025_single: "ECMWF AIFS", gfs_seamless: "NOAA GFS", ukmo_seamless: "UK Met Office", gem_seamless: "Canada GEM" };
  const modelChips = (ids) => ids.map((id) => `<span><i style="border-color:${COL[id]}"></i>${esc(SHORT[id] || id)}</span>`).join("");

  function levelSeries(level) {
    const d = state.data, key = level === 10 ? "u10" : "u50";
    const dets = ORDER.filter((id) => d.det.models[id]).map((id) => ({ id, label: d.det.names[id], short: d.det.names[id].replace("ECMWF ", "").replace("NOAA ", "").replace("UK Met Office", "UKMO").replace("Canada ", ""), color: COL[id], dates: d.det.models[id].dates, vals: d.det.models[id][key] }));
    const enss = d.ens.series.filter((e) => e.level === level);
    return { dets, enss };
  }

  function renderWind() {
    const lv = state.level, { dets, enss } = levelSeries(lv);
    const dates = unionDates([...dets.filter((s) => s.vals.some((v) => v != null)).map((s) => s.dates), ...enss.map((e) => e.dates)]);
    if (!dates.length) { $("#s-wind").innerHTML = `<p class="dayhint">No wind data in the latest run.</p>`; return; }
    const series = dets.map((s) => ({ label: s.label, short: s.short, color: s.color, vals: align(dates, s.dates, s.vals), w: 2 })).filter((s) => s.vals.some((v) => v != null));
    const bands = enss.map((e) => ({ lo: align(dates, e.dates, e.p10), hi: align(dates, e.dates, e.p90), color: ENS_COL[e.id], op: 0.2, label: e.name + " (10 to 90%)" }));
    enss.forEach((e) => series.push({ label: e.name + " median", short: e.name.replace("ECMWF ", "").replace(" ensemble", " ens."), color: ENS_COL[e.id], vals: align(dates, e.dates, e.p50), w: 1.6, dash: "6 4" }));
    const all = [...series.flatMap((s) => s.vals), ...bands.flatMap((b) => [...b.lo, ...b.hi])].filter((v) => v != null);
    const lo = Math.min(-8, ...all) - 3, hi = Math.max(10, ...all) + 3;
    const yt = niceTicks(lo, hi, 5);
    lineChart($("#s-wind"), { dates, series, bands, h: 300, labels: true, y0: Math.min(yt[0], lo), y1: Math.max(hi, yt[yt.length - 1]), yTicks: yt, yFmt: (v) => num(v), fmtVal: (v) => num(v, 1) + " m/s",
      hlines: [{ y: 0, label: "Easterly below this line: vortex reversed", color: "#ffb36b", solid: true, below: true }],
      aria: `Zonal-mean zonal wind at 60 degrees north, ${lv} hectopascals, from five models and ensembles` });
    $("#s-leg-wind").innerHTML = modelChips(dets.filter((s) => s.vals.some((v) => v != null)).map((s) => s.id)) + (enss.length ? `<span><i class="dash" style="border-color:var(--ink-2)"></i>Dashed: ensemble median</span><span><i class="band" style="background:var(--ink-2)"></i>Band: ensemble 10 to 90% range</span>` : "");
    $("#s-h-wind").textContent = lv === 10 ? "Vortex wind: 10 hPa (about 31 km)" : "Vortex wind: 50 hPa (about 20 km)";
    $("#s-p-wind").textContent = lv === 10
      ? "Average westerly wind around the 60°N circle, in metres per second. A normal winter vortex is westerly. A major sudden warming is defined as this wind going below zero."
      : "The lower stratosphere is where a warming has to arrive before it can touch the weather. The GFS ensemble reaches 35 days here, far beyond the other models.";
  }

  function renderProb() {
    const enss = state.data.ens.series.filter((e) => e.level === state.level);
    if (!enss.length) { $("#s-prob").innerHTML = `<p class="dayhint">No ensemble data in the latest run.</p>`; $("#s-leg-prob").innerHTML = ""; return; }
    const dates = unionDates(enss.map((e) => e.dates));
    const series = enss.map((e) => ({ label: `${e.name} (${e.members})`, short: e.name.replace("ECMWF ", "").replace(" ensemble", " ens."), color: ENS_COL[e.id], vals: align(dates, e.dates, e.cum_prob), w: 2.2 }));
    lineChart($("#s-prob"), { dates, series, h: 220, labels: true, y0: 0, y1: 1.02, yTicks: [0, 0.25, 0.5, 0.75, 1], yFmt: (v) => Math.round(v * 100) + "%", fmtVal: (v) => pct(v),
      hlines: [{ y: 0.5, label: "More likely than not" }], aria: "Share of ensemble members whose wind has reversed by each date" });
    $("#s-leg-prob").innerHTML = enss.map((e) => `<span><i style="border-color:${ENS_COL[e.id]}"></i>${esc(e.name)}: ${e.members} members, ${pct(e.p_any)} reverse</span>`).join("");
  }

  function renderWaves() {
    const d = state.data, k = state.wave, key = `w${k}_10`;
    const dets = ORDER.filter((id) => d.det.models[id] && d.det.models[id][key]);
    const dates = unionDates(dets.map((id) => d.det.models[id].dates));
    const series = dets.map((id) => ({ label: d.det.names[id], short: d.det.names[id].replace("ECMWF ", "").replace("NOAA ", "").replace("UK Met Office", "UKMO").replace("Canada ", ""), color: COL[id], vals: align(dates, d.det.models[id].dates, d.det.models[id][key]), w: 2 })).filter((s) => s.vals.some((v) => v != null));
    if (!series.length) { $("#s-wave").innerHTML = `<p class="dayhint">No wave data in the latest run.</p>`; return; }
    const all = series.flatMap((s) => s.vals).filter((v) => v != null);
    const yt = niceTicks(0, Math.max(...all) * 1.15, 4);
    lineChart($("#s-wave"), { dates, series, h: 250, labels: true, y0: 0, y1: Math.max(Math.max(...all) * 1.08, yt[yt.length - 1]), yTicks: yt, yFmt: (v) => v + " m", fmtVal: (v) => num(v) + " m", aria: `Wave ${k} amplitude at 10 hectopascals at 60 degrees north` });
    $("#s-h-wave").textContent = `Wave ${k} amplitude at 10 hPa`;
  }

  function renderHeat() {
    const d = state.data;
    const dets = ORDER.filter((id) => d.det.models[id] && d.det.models[id].hf100);
    const dates = unionDates(dets.map((id) => d.det.models[id].dates));
    const series = dets.map((id) => ({ label: d.det.names[id], short: d.det.names[id].replace("ECMWF ", "").replace("NOAA ", "").replace("UK Met Office", "UKMO").replace("Canada ", ""), color: COL[id], vals: align(dates, d.det.models[id].dates, d.det.models[id].hf100), w: 2 })).filter((s) => s.vals.some((v) => v != null));
    if (!series.length) { $("#s-heat").innerHTML = `<p class="dayhint">No heat flux data in the latest run.</p>`; return; }
    const all = series.flatMap((s) => s.vals).filter((v) => v != null);
    const lo = Math.min(0, ...all) - 2, hi = Math.max(d.rules.hf_strong + 5, ...all) + 3;
    const yt = niceTicks(lo, hi, 4);
    lineChart($("#s-heat"), { dates, series, h: 250, labels: true, y0: Math.min(yt[0], lo), y1: Math.max(hi, yt[yt.length - 1]), yTicks: yt, yFmt: (v) => num(v), fmtVal: (v) => num(v, 1) + " K m/s",
      hlines: [{ y: d.rules.hf_strong, label: `${d.rules.hf_strong} K m/s: a strong pulse` }], aria: "Eddy heat flux at 100 hectopascals, 60 degrees north" });
  }

  // ---------- propagation timeline ----------
  function renderTimeline(a) {
    const host = $("#s-timeline"), p = a.propagation, d = state.data;
    const today = d.det.models[Object.keys(d.det.models)[0]].dates[0];
    const W = Math.max(300, host.clientWidth || 700), compact = W < 560;
    const left = compact ? 12 : 168, right = 14, iw = W - left - right;
    const span = Math.max(95, diffDays(today, p.milestones.length ? p.milestones[2].end : p.horizon_end) + 10);
    const X = (iso) => left + (Math.max(0, diffDays(today, iso)) / span) * iw;
    const rowH = compact ? 44 : 34, top = 30;
    const rows = [];
    rows.push({ label: "Forecasts can see", bars: [{ s: today, e: p.horizon_end, c: "var(--nat)", op: 1, t: `to ${fmtDM(p.horizon_end)}` }, ...(p.long_end > p.horizon_end ? [{ s: p.horizon_end, e: p.long_end, c: "var(--nat)", op: 0.35, t: `GFS ensemble, 50 hPa, to ${fmtDM(p.long_end)}` }] : [])] });
    if (p.milestones.length) {
      const ms = Object.fromEntries(p.milestones.map((m) => [m.key, m]));
      rows.push({ label: "Winds reverse at 10 hPa", bars: [{ s: ms.reversal.start, e: ms.reversal.end, c: "var(--art)", op: 1, t: ms.reversal.start === ms.reversal.end ? fmtDM(ms.reversal.start) : `${fmtDM(ms.reversal.start)} to ${fmtDM(ms.reversal.end)}`, min: 8 }] });
      rows.push({ label: "Reaches 50 hPa", bars: [{ s: ms.lower.start, e: ms.lower.end, c: "var(--wet)", op: 1, t: ms.lower.start === ms.lower.end ? `${fmtDM(ms.lower.start)} (${ms.lower.basis})` : `${fmtDM(ms.lower.start)} to ${fmtDM(ms.lower.end)} (${ms.lower.basis})`, min: 8 }] });
      rows.push({ label: "Weather pattern shifts", bars: [{ s: ms.tropo.start, e: ms.tropo.end, c: "var(--v-great)", op: 1, t: `${fmtDM(ms.tropo.start)} to ${fmtDM(ms.tropo.end)}`, min: 8 }] });
    } else if (p.blind_spot) {
      rows.push({ label: "Blind spot", bars: [{ s: p.blind_spot.from, e: p.blind_spot.earliest_impact, c: "var(--ink-3)", op: 0.5, t: `A warming after ${fmtDM(p.blind_spot.from)} cannot show up here yet`, hatch: true }] });
      rows.push({ label: "Earliest weather impact", bars: [{ s: p.blind_spot.earliest_impact, e: addDays(p.blind_spot.earliest_impact, 21), c: "var(--v-great)", op: 0.55, t: `from ${fmtDM(p.blind_spot.earliest_impact)}`, min: 8 }] });
    }
    const H = top + rows.length * rowH + 28;
    let g = "";
    // month ticks
    for (let i = 0; i <= span; i += 7) {
      const iso = addDays(today, i), xx = X(iso);
      g += `<line x1="${xx}" x2="${xx}" y1="${top - 6}" y2="${H - 24}" stroke="var(--line)" opacity="${i % 28 === 0 ? 0.9 : 0.45}"/>`;
      if (i % 14 === 0 || !compact) g += `<text x="${xx}" y="${H - 8}" text-anchor="middle">${fmtDM(iso)}</text>`;
    }
    g += `<line x1="${X(today)}" x2="${X(today)}" y1="${top - 12}" y2="${H - 24}" stroke="var(--ink)" stroke-width="2"/><text class="lbl" x="${X(today) + 5}" y="${top - 14}">Today</text>`;
    rows.forEach((r, ri) => {
      const yy = top + ri * rowH + (compact ? 20 : 4);
      g += compact ? `<text class="lbl" x="${left}" y="${top + ri * rowH + 10}">${esc(r.label)}</text>` : `<text class="lbl" x="${left - 12}" y="${yy + 15}" text-anchor="end">${esc(r.label)}</text>`;
      for (const b of r.bars) {
        const x0 = X(b.s), x1 = Math.max(X(b.e), x0 + (b.min || 3)), bw = x1 - x0;
        g += `<rect x="${x0}" y="${yy}" width="${bw}" height="20" rx="4" fill="${b.c}" opacity="${b.op}"${b.hatch ? ' stroke="var(--ink-3)" stroke-dasharray="4 3" fill-opacity=".25"' : ""}/>`;
        const fits = bw > b.t.length * 6.4 + 12, tx = fits ? x0 + 8 : (x1 + 8 + b.t.length * 6.4 < W - right ? x1 + 8 : x0 - 8), anchor = fits || tx > x0 ? "start" : "end";
        g += `<text x="${tx}" y="${yy + 14}" text-anchor="${anchor}" style="fill:${fits && b.op === 1 && b.c !== "var(--ink-3)" ? "#06121a" : "var(--ink)"}" font-weight="600">${esc(b.t)}</text>`;
      }
    });
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Timeline from today to about three months ahead, showing what the forecasts see and when a stratospheric warming would reach the weather">${g}</svg>`;
  }

  function renderBrief(a) {
    $("#s-brief").innerHTML = `<ul>${a.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`;
    const errs = state.data.errors || [];
    $("#s-err").textContent = errs.length ? `Some stratosphere data was missing in the latest run: ${errs.join("; ")}` : "";
  }

  function renderHow(d) {
    const r = d.rules;
    $("#s-how").innerHTML = `<p><b>What is measured.</b> Winds, temperatures and geopotential height on a ring of 24 points around 60°N (12 points for the ensembles). Zonal-mean wind is the average westerly component around the ring. Wave 1 and wave 2 are the strengths of the one-lobed and two-lobed patterns in the geopotential height. Heat flux is the correlation of north-south wind and temperature wiggles at 100 hPa, which measures how much wave energy is being pushed up into the stratosphere.</p>
      <p><b>What counts as a reversal.</b> The daily-mean zonal wind at 60°N, 10 hPa falling below zero. For a major sudden warming that must happen between ${esc(r.season)}, and the pole must also become warmer than 60°N. The agent checks both and reports the date.</p>
      <p><b>Consensus.</b> Five deterministic models (ECMWF IFS and AIFS, GFS, UK Met Office, Canada GEM) and three ensembles (ECMWF IFS and AIFS with 51 members each, GFS with 31). A "warming signal" needs at least 50% of an ensemble's members, or two deterministic models, to reverse the wind. A "watch" needs 10% of members, or one model, or a median wind drop of ${r.weakening_drop_ms} m/s, or a warm pole.</p>
      <p><b>Timeline.</b> From the consensus reversal date the tropospheric response is placed ${r.lag_weeks[0]} to ${r.lag_weeks[1]} weeks later, and the lower stratosphere (50 hPa) ${r.lower_strat_lag_days[0]} to ${r.lower_strat_lag_days[1]} days later unless a model shows it directly. These lags are typical, not guaranteed: some warmings never reach the surface, and the surface response is a shift in odds (colder, more blocked weather over northern Europe), not a forecast for a particular day.</p>
      <p><b>Heat flux.</b> Above ${r.hf_strong} K m/s counts as a strong pulse and above ${r.hf_moderate} K m/s as moderate. These are the agent's own rules of thumb for 60°N at 100 hPa.</p>
      <p><b>Limits.</b> The models only see about 16 days ahead (35 days for the lower-stratosphere GFS ensemble). A warming that starts after that cannot be seen yet. Data for 10 hPa from ECMWF, AIFS and GFS is model output on a 0.25° grid (0.5° for the GFS ensemble).</p>`;
  }

  function init() {
    document.querySelectorAll("[data-slevel]").forEach((b) => (b.onclick = () => { state.level = +b.dataset.slevel; if (state.data) render(); }));
    document.querySelectorAll("[data-swave]").forEach((b) => (b.onclick = () => { state.wave = +b.dataset.swave; if (state.data) render(); }));
    let rz; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => state.data && render(), 150); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
    setInterval(load, 10 * 60 * 1000);
    load();
  }
  init();
})();
