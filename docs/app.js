/* Snow watch Latvia - dashboard. Reads data/forecast.json written by agent/snow_agent.py */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const DATA_URL = "data/forecast.json";
  const REFRESH_MS = 10 * 60 * 1000;
  const STALE_H = 9;

  const state = { data: null, spot: "gaizinkalns", profile: "park", machine: true, hour: 0, playing: false };
  let scene = null, charts = {}, playRaf = null;

  // ---------- small helpers ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem("snow." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("snow." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const mode = () => (state.machine ? "pot" : "nat");
  const spot = () => state.data.spots.find((s) => s.id === state.spot) || state.data.spots[0];
  const dt = (iso) => new Date(iso + "T12:00:00Z");
  const fmtDate = (iso) => dt(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const fmtDM = (iso) => dt(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const fmtDow = (iso) => dt(iso).toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
  const pct = (p) => Math.round(p * 100) + "%";
  const T = (v, d = 1) => (v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d));
  const N1 = (v, d = 1) => (v == null ? "–" : (+v).toFixed(d));
  const VERDICT = { great: "Great", skiable: "Skiable", marginal: "Marginal", no: "Not yet" };
  const profile = () => state.data.assumptions.profiles[state.profile];
  const hourly = () => spot().hourly;
  const dayOfHour = (h) => spot().days.findIndex((d) => d.date === hourly().time[h].slice(0, 10));
  const dayAt = (h) => spot().days[dayOfHour(h)];
  const MIN_MODELS = 3;

  function verdictOf(day) {
    if (!day || !day.ski || day.n < MIN_MODELS) return null;
    return day.ski[state.profile][mode()];
  }

  function nowHourIndex(times) {
    try {
      const f = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Riga", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hour12: false }).format(new Date());
      const [d, hh] = f.split(" ");
      const i = times.indexOf(`${d}T${hh.slice(0, 2)}:00`);
      return i >= 0 ? i : 0;
    } catch (e) { return 0; }
  }

  // ---------- load ----------
  async function load(first) {
    try {
      const r = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      const changed = !state.data || state.data.generated_at !== d.generated_at;
      state.data = d;
      if (first) {
        state.spot = store.get("spot", state.spot);
        state.profile = store.get("profile", state.profile);
        state.machine = store.get("machine", state.machine);
        if (!d.spots.some((s) => s.id === state.spot)) state.spot = d.spots[0].id;
        state.hour = nowHourIndex(spot().hourly.time);
      }
      if (changed || first) renderAll();
      renderStatus();
    } catch (e) {
      if (!state.data) $("#app").innerHTML = `<div class="err wrap"><b>The forecast data could not be loaded.</b><br>${String(e.message || e)}. If you just deployed the site, run the "Update snow forecast" workflow once so data/forecast.json exists.</div>`;
      else renderStatus(true);
    }
  }

  function renderStatus(failed) {
    const el = $("#status");
    if (!state.data) return;
    const ageH = (Date.now() - new Date(state.data.generated_at).getTime()) / 3600000;
    const rel = ageH < 1 ? Math.max(1, Math.round(ageH * 60)) + " min ago" : ageH < 48 ? Math.round(ageH) + " h ago" : Math.round(ageH / 24) + " days ago";
    el.className = "status" + (ageH > STALE_H ? " stale" : "");
    el.innerHTML = `Forecast updated <b>${rel}</b>${ageH > STALE_H ? " (the hourly job may have paused)" : ""}${failed ? ". Refresh failed, showing saved data." : ""} <button class="linkbtn" id="refresh">Refresh</button>`;
    $("#refresh").onclick = () => load(false);
  }

  // ---------- render ----------
  function renderAll() {
    renderControls(); renderHero(); renderPlates(); renderDays(); renderCharts(); renderModels(); renderOutlook(); renderBrief(); renderHow(); renderTable(); renderFooter();
    syncHour();
  }

  function renderControls() {
    document.querySelectorAll("[data-profile]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.profile === state.profile));
    $("#machine").checked = state.machine;
  }

  function bestElsewhere() {
    const list = state.data.spots.filter((s) => s.kind === "ski" && s.first[state.profile][mode()]);
    list.sort((a, b) => a.first[state.profile][mode()].date.localeCompare(b.first[state.profile][mode()].date) || b.first[state.profile][mode()].prob - a.first[state.profile][mode()].prob);
    return list[0] || null;
  }

  function renderHero() {
    const s = spot(), f = s.first[state.profile][mode()], prof = profile().label.toLowerCase();
    let h1, p;
    if (f) {
      h1 = `Ski from ${fmtDate(f.date)}`;
      p = `${s.name}: ${pct(f.prob)} of ${f.n} models agree that ${prof} conditions arrive${f.days > 1 ? ` and hold for about ${f.days} days` : ""}.`;
    } else {
      h1 = "No ski day in sight";
      const cold = s.cold_snap ? `Snowmaking cold first appears on ${fmtDate(s.cold_snap.date)}.` : "No snowmaking-grade cold shows up in the 15-day models.";
      const o = s.outlook || {};
      const ol = o.first_freezing || o.first_cold
        ? ` Looking weeks ahead (low confidence): ${o.first_freezing ? `freezing weeks turn likely from ${fmtDM(o.first_freezing.start)}` : ""}${o.first_freezing && o.first_cold ? ", " : ""}${o.first_cold ? `snowmaking-grade cold from ${fmtDM(o.first_cold.start)}` : ""}.` : "";
      p = `${s.name} will not build enough snow for ${prof} in the next 15 days. ${cold}${ol}`;
    }
    const b = bestElsewhere();
    if (b && b.id !== s.id && (!f || b.first[state.profile][mode()].date < f.date)) p += ` Sooner elsewhere: ${b.name}, from ${fmtDate(b.first[state.profile][mode()].date)}.`;
    $("#h1").textContent = h1;
    $("#vp").textContent = p;
    const tr = s.trend && s.trend[state.profile];
    const tEl = $("#trend");
    const msg = !tr ? "" : { new: "Tracking has just started, so day-to-day change appears tomorrow.", same: "Same answer as yesterday.", none: "", appeared: "A ski day newly appeared in the forecast since yesterday.", vanished: "The ski day in yesterday's forecast has disappeared." }[tr.state]
      || (tr.state === "earlier" ? `Moved ${tr.days} day${tr.days > 1 ? "s" : ""} earlier since yesterday.` : `Moved ${tr.days} day${tr.days > 1 ? "s" : ""} later since yesterday.`);
    tEl.textContent = msg || "";
    tEl.className = "trend " + (tr && (tr.state === "earlier" || tr.state === "appeared") ? "earlier" : tr && (tr.state === "later" || tr.state === "vanished") ? "later" : "");

    // scrubber range
    const n = s.hourly.time.length;
    const rng = $("#range");
    rng.max = n - 1;
    if (state.hour > n - 1) state.hour = 0;
    if (!scene && window.HillScene) scene = new HillScene($("#scene"));
    $("#play").disabled = !!(scene && scene.reduced);
    if (scene && scene.reduced) $("#play").title = "Animation is off because your system asks for reduced motion";
  }

  function renderPlates() {
    const ul = $("#plates");
    ul.innerHTML = "";
    const order = [...state.data.spots.filter((s) => s.kind === "ski"), ...state.data.spots.filter((s) => s.kind !== "ski")];
    for (const s of order) {
      const f = s.first[state.profile][mode()];
      const cells = s.days.map((d, i) => {
        const v = d.n >= MIN_MODELS && d.ski ? d.ski[state.profile][mode()].verdict : null;
        const fill = v === "great" ? "var(--v-great)" : v === "skiable" ? "var(--v-skiable)" : v === "marginal" ? "var(--v-marginal)" : "none";
        return `<rect x="${i * 10}" y="0" width="8.5" height="8" rx="1" style="fill:${fill};stroke:${fill === "none" ? "var(--line)" : "none"}" />`;
      }).join("");
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.className = "plate";
      b.setAttribute("aria-pressed", s.id === state.spot);
      b.innerHTML = `<span class="nm">${s.name}</span><span class="rg">${s.region}${s.elevation != null && s.kind === "ski" ? ", " + s.elevation + " m" : ""}</span>
        <span class="first">${f ? "From " + fmtDate(f.date) : s.kind === "ski" ? "No ski day yet" : "Reference"}</span>
        <svg viewBox="0 0 ${s.days.length * 10 - 1.5} 8" preserveAspectRatio="none" aria-hidden="true">${cells}</svg>`;
      b.onclick = () => { state.spot = s.id; store.set("spot", s.id); renderAll(); };
      li.appendChild(b); ul.appendChild(li);
    }
  }

  function renderDays() {
    const s = spot(), ul = $("#days");
    ul.innerHTML = "";
    s.days.forEach((d, i) => {
      const v = verdictOf(d);
      const li = document.createElement("li");
      const b = document.createElement("button");
      const thin = d.n < MIN_MODELS;
      b.className = "day" + (v && v.verdict !== "no" ? " v-" + v.verdict : "") + (thin ? " thin" : "");
      b.setAttribute("aria-label", `${fmtDate(d.date)}: ${thin ? "too few models" : VERDICT[v.verdict]}, ${T(d.tday && d.tday.med)} degrees by day, ${d.n} models`);
      const dots = Array.from({ length: 11 }, (_, k) => `<s class="${k < d.n ? "" : "off"}"></s>`).join("");
      const snow = d.snow && d.snow.med >= 0.1 ? `snow ${N1(d.snow.med)} cm` : "";
      const cover = d[mode()] && d[mode()].med >= 0.5 ? `${N1(d[mode()].med, 0)} cm cover` : "";
      b.innerHTML = `<span class="dw">${fmtDow(d.date)}</span><span class="dd">${fmtDM(d.date)}</span>
        <span class="tt">${d.tday ? T(d.tday.med, 0) + "°" : "–"}</span>
        <span class="vv">${thin ? "few models" : v.verdict === "no" ? "Not yet" : VERDICT[v.verdict]}</span>
        <span class="sn">${cover || snow || "&nbsp;"}</span>
        <span class="nn" title="${d.n} of 11 models reporting">${dots}</span>`;
      b.dataset.i = i;
      b.onclick = () => { const h = hourly().time.findIndex((t) => t.startsWith(d.date + "T12")); setHour(h >= 0 ? h : i * 24, true); };
      li.appendChild(b); ul.appendChild(li);
    });
  }

  // ---------- charts ----------
  const niceTicks = (lo, hi, count) => {
    const raw = (hi - lo) / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  };
  const segs = (arr) => { const out = []; let cur = []; arr.forEach((v, i) => { if (v == null) { if (cur.length) out.push(cur); cur = []; } else cur.push([i, v]); }); if (cur.length) out.push(cur); return out; };

  function chart(host, cfg) {
    const W = Math.max(280, host.clientWidth || 600), H = cfg.h || 250;
    const m = { l: 46, r: cfg.rightPad || 10, t: 10, b: 28 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, n = cfg.n;
    const x = (i) => m.l + (i / (n - 1)) * iw, y = (v) => m.t + ih - ((v - cfg.y0) / (cfg.y1 - cfg.y0)) * ih;
    const line = (arr) => segs(arr).map((sg) => "M" + sg.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L")).join("");
    const band = (lo, hi) => segs(hi.map((v, i) => (v == null || lo[i] == null ? null : v))).map((sg) =>
      "M" + sg.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("L") + "L" + [...sg].reverse().map(([i]) => `${x(i).toFixed(1)},${y(lo[i]).toFixed(1)}`).join("L") + "Z").join("");
    let g = "";
    for (const v of cfg.yTicks) g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1" opacity=".7"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${cfg.yFmt(v)}</text>`;
    const times = hourly().time;
    for (let i = 0; i < n; i++) if (times[i].endsWith("T00:00") && ((i / 24) | 0) % 2 === 0) g += `<line x1="${x(i)}" x2="${x(i)}" y1="${m.t + ih}" y2="${m.t + ih + 4}" stroke="var(--ink-3)"/><text x="${x(i)}" y="${H - 8}" text-anchor="middle">${fmtDM(times[i].slice(0, 10))}</text>`;
    const body = cfg.draw({ x, y, line, band, m, iw, ih, W, H });
    const cid = "c" + Math.random().toString(36).slice(2, 8);
    const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${cfg.label}"><clipPath id="${cid}"><rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}"/></clipPath>${g}<g clip-path="url(#${cid})">${body}</g><line class="mk" y1="${m.t}" y2="${m.t + ih}" stroke="var(--ink)" stroke-width="1.5" opacity="0"/><rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" style="touch-action:pan-y"/></svg><div class="tip"></div>`;
    host.innerHTML = svg;
    const mk = host.querySelector(".mk"), tip = host.querySelector(".tip"), hit = host.querySelector(".hit");
    const at = (ev) => { const r = hit.getBoundingClientRect(); return Math.max(0, Math.min(n - 1, Math.round(((ev.clientX - r.left) / r.width) * (n - 1)))); };
    const move = (ev) => {
      const i = at(ev); setHour(i, true);
      tip.innerHTML = cfg.tip(i); tip.style.opacity = 1;
      const px = (x(i) / W) * host.clientWidth, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(host.clientWidth - tw, px + 14 > host.clientWidth - tw ? px - tw - 14 : px + 14)) + "px";
      tip.style.top = "8px";
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => (tip.style.opacity = 0));
    return { set(i) { mk.setAttribute("x1", x(i)); mk.setAttribute("x2", x(i)); mk.setAttribute("opacity", 1); } };
  }

  const tipRow = (k, v) => `<span><em>${k}</em><b style="font:600 .9rem var(--font)">${v}</b></span>`;
  function tipHtml(i) {
    const H = hourly(), t = H.time[i], day = dayAt(i);
    const hh = t.slice(11, 16);
    let s = `<b>${fmtDate(t.slice(0, 10))}, ${hh}</b>`;
    s += tipRow("Air", `${T(H.temp.med[i])} °C <span style="display:inline;color:var(--ink-3);font-weight:400"> (${T(H.temp.lo[i], 0)} to ${T(H.temp.hi[i], 0)})</span>`);
    s += tipRow("Wet-bulb", `${T(H.tw.med[i])} °C`);
    s += tipRow("Natural cover", `${N1(H.nat.med[i])} cm`);
    if (state.machine) s += tipRow("With machine snow", `${N1(H.pot.med[i])} cm`);
    s += tipRow("Snowfall", H.snow.med[i] >= 0.05 ? `${N1(H.snow.med[i], 2)} cm/h` : "none");
    s += tipRow("Models", `${H.n[i]} of 11`);
    return s;
  }

  function renderCharts() {
    const s = spot(), H = s.hourly, n = H.time.length, prof = profile();
    const maxHi = Math.max(0, ...(state.machine ? H.pot.hi : H.nat.hi).filter((v) => v != null));
    const y1 = Math.max(prof.min_cm * 1.7, maxHi * 1.15, 12);
    const yt = niceTicks(0, y1, 4);
    charts.cover = chart($("#chart-cover"), {
      n, y0: 0, y1, yTicks: yt, yFmt: (v) => v + " cm", label: `Snow cover on the slope at ${s.name}, next 15 days, with the range across models`, rightPad: 10,
      tip: tipHtml,
      draw: ({ x, y, line, band, m, iw }) => {
        let o = `<path d="${band(H.nat.lo, H.nat.hi)}" fill="var(--nat)" opacity=".22"/>`;
        if (state.machine) o += `<path d="${band(H.pot.lo, H.pot.hi)}" fill="var(--art)" opacity=".2"/>`;
        o += `<line x1="${m.l}" x2="${m.l + iw}" y1="${y(prof.min_cm)}" y2="${y(prof.min_cm)}" stroke="var(--ink-2)" stroke-dasharray="5 4" stroke-width="1.5"/>`;
        o += `<text class="lbl" x="${m.l + 6}" y="${y(prof.min_cm) - 6}">${prof.min_cm} cm: the least ${prof.label.toLowerCase()} needs</text>`;
        o += `<path d="${line(H.nat.med)}" fill="none" stroke="var(--nat)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
        if (state.machine) o += `<path d="${line(H.pot.med)}" fill="none" stroke="var(--art)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
        if (maxHi < 1) o += `<text x="${m.l + iw / 2}" y="${y(prof.min_cm * 0.45)}" text-anchor="middle" class="lbl">No model builds any snow cover here in the next 15 days</text>`;
        return o;
      },
    });
    $("#leg-cover").innerHTML = `<span><i style="border-color:var(--nat)"></i>Natural snow (median of models)</span>${state.machine ? `<span><i style="border-color:var(--art)"></i>Natural plus machine snow</span>` : ""}<span><i class="band" style="background:var(--nat)"></i>Spread across models (natural)</span>${state.machine ? `<span><i class="band" style="background:var(--art)"></i>Spread across models (with machine snow)</span>` : ""}<span><i class="dash" style="border-color:var(--ink-2)"></i>Minimum for ${prof.label.toLowerCase()}</span>`;

    const tmin = Math.min(-6, ...H.temp.lo.filter((v) => v != null), ...H.tw.med.filter((v) => v != null)) - 1;
    const tmax = Math.max(4, ...H.temp.hi.filter((v) => v != null)) + 1;
    const tt = niceTicks(tmin, tmax, 5);
    charts.temp = chart($("#chart-temp"), {
      n, y0: tmin, y1: tmax, yTicks: tt, yFmt: (v) => T(v, 0) + "°", label: `Air temperature and wet-bulb temperature at ${s.name}, next 15 days`, rightPad: 10,
      tip: tipHtml,
      draw: ({ x, y, line, band, m, ih }) => {
        let o = "", run = null;
        const flush = (end) => { if (run != null) { o += `<rect x="${x(run)}" y="${m.t}" width="${Math.max(1, x(end) - x(run))}" height="${ih}" fill="var(--art)" opacity=".22"/>`; run = null; } };
        H.tw.med.forEach((v, i) => { if (v != null && v <= -2) { if (run == null) run = i; } else flush(i); });
        flush(n - 1);
        o += `<path d="${band(H.temp.lo, H.temp.hi)}" fill="var(--nat)" opacity=".22"/>`;
        o += `<line x1="${m.l}" x2="${x(n - 1)}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink-2)" stroke-width="1.5" stroke-dasharray="5 4"/><text class="lbl" x="${m.l + 6}" y="${y(0) - 6}">Freezing</text>`;
        o += `<path d="${line(H.tw.med)}" fill="none" stroke="var(--wet)" stroke-width="1.8" stroke-linejoin="round"/>`;
        o += `<path d="${line(H.temp.med)}" fill="none" stroke="var(--nat)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
        return o;
      },
    });
    $("#leg-temp").innerHTML = `<span><i style="border-color:var(--nat)"></i>Air temperature (median)</span><span><i style="border-color:var(--wet)"></i>Wet-bulb (what snow guns care about)</span><span><i class="band" style="background:var(--nat)"></i>Spread across models</span><span><i class="win" style="background:var(--art)"></i>Wet-bulb at or below −2 °C: snow guns can run</span>`;
    $("#h-cover").textContent = `Snow on the slope at ${s.name}`;
    $("#h-temp").textContent = `Temperature and snowmaking weather at ${s.name}`;
  }

  // ---------- models ----------
  function renderModels() {
    const s = spot(), host = $("#models");
    const rows = s.models.map((m) => {
      const t = m.tmean.filter((v) => v != null);
      return { m, lo: Math.min(...t), hi: Math.max(...t) };
    }).sort((a, b) => a.lo - b.lo);
    const lo = Math.floor(Math.min(-3, ...rows.map((r) => r.lo)) / 2) * 2 - 1, hi = Math.ceil(Math.max(10, ...rows.map((r) => r.hi)) / 2) * 2 + 1;
    const pos = (v) => ((v - lo) / (hi - lo)) * 100;
    const ticks = niceTicks(lo, hi, 5);
    const first = (m) => { const f = m.first[state.profile][mode()]; return f ? fmtDM(f) : "none"; };
    host.innerHTML = `<div class="mrow head"><span>Model</span><span>Daily mean air temperature, coldest to warmest day (filled dot = coldest)</span><span class="num">Snow 14 d</span><span class="num">First ski day</span></div>`
      + rows.map(({ m, lo: a, hi: b }) => `<div class="mrow"><span class="nm">${m.name}<small>${m.horizon_days} days of forecast</small></span>
        <span class="track" role="img" aria-label="${m.name}: daily mean from ${T(a)} to ${T(b)} degrees"><span class="zero" style="left:${pos(0)}%"></span><span class="rng" style="left:${pos(a)}%;width:${Math.max(0.5, pos(b) - pos(a))}%"></span><span class="dot warm" style="left:${pos(b)}%"></span><span class="dot" style="left:${pos(a)}%"></span></span>
        <span class="num">${N1(m.snow14)} cm</span><span class="num">${first(m)}</span></div>`).join("")
      + `<div class="mrow head" style="border-top:0"><span></span><span class="axis">${ticks.map((v) => `<span style="left:${pos(v)}%">${T(v, 0)}°</span>`).join("")}</span><span></span><span></span></div>`;
    $("#model-note").textContent = s.model_note || "";
  }

  // ---------- outlook ----------
  function renderOutlook() {
    const s = spot(), host = $("#outlook"), ol = s.outlook;
    if (!ol || !ol.weeks.length) { host.innerHTML = `<p class="dayhint">The weeks-ahead outlook was not available in the latest run.</p>`; return; }
    const W = Math.max(300, host.clientWidth || 700), H = 250, m = { l: 46, r: 8, t: 12, b: 50 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, k = ol.weeks.length, band = iw / k, bw = Math.min(30, band * 0.62);
    const y = (p) => m.t + ih - p * ih;
    let g = [0, 0.25, 0.5, 0.75, 1].map((p) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(p)}" y2="${y(p)}" stroke="var(--line)" opacity=".7"/><text x="${m.l - 8}" y="${y(p) + 4}" text-anchor="end">${p * 100}%</text>`).join("");
    const bar = (cx, w, p, col, op) => { const h = Math.max(0, p * ih), r = Math.min(4, w / 2, h); return h < 0.5 ? "" : `<path d="M${cx - w / 2},${m.t + ih} V${y(p) + r} Q${cx - w / 2},${y(p)} ${cx - w / 2 + r},${y(p)} H${cx + w / 2 - r} Q${cx + w / 2},${y(p)} ${cx + w / 2},${y(p) + r} V${m.t + ih} Z" fill="${col}" opacity="${op}"/>`; };
    ol.weeks.forEach((w, i) => {
      const cx = m.l + band * (i + 0.5), short = i < 2;
      g += bar(cx, bw, w.p_freezing, "var(--nat)", short ? 0.35 : 1);
      g += bar(cx, bw * 0.4, w.p_cold, "var(--art)", short ? 0.5 : 1);
      g += `<text x="${cx}" y="${H - 30}" text-anchor="middle">${fmtDM(w.start)}</text><text class="lbl" x="${cx}" y="${H - 14}" text-anchor="middle">${T(w.tmean.med, 0)}°</text>`;
    });
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Chance that each of the next 13 weeks averages below freezing, from the ECMWF seasonal ensemble">${g}<text x="${m.l - 8}" y="${H - 14}" text-anchor="end" class="lbl">Median</text><rect class="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih + 40}" fill="transparent"/></svg><div class="tip"></div>`;
    const tip = host.querySelector(".tip"), hit = host.querySelector(".hit");
    const mv = (ev) => {
      const r = hit.getBoundingClientRect(), i = Math.max(0, Math.min(k - 1, Math.floor(((ev.clientX - r.left) / r.width) * k))), w = ol.weeks[i];
      tip.innerHTML = `<b>Week of ${fmtDate(w.start)}</b>${tipRow("Median week", T(w.tmean.med) + " °C")}${tipRow("Likely range", `${T(w.tmean.lo, 0)} to ${T(w.tmean.hi, 0)} °C`)}${tipRow("Below freezing", pct(w.p_freezing))}${tipRow("Below −3 °C", pct(w.p_cold))}${tipRow("Snowfall (median)", N1(w.snow) + " cm")}`;
      tip.style.opacity = 1;
      const px = ((m.l + band * (i + 0.5)) / W) * host.clientWidth;
      tip.style.left = Math.max(0, Math.min(host.clientWidth - tip.offsetWidth, px - tip.offsetWidth / 2)) + "px"; tip.style.top = "6px";
    };
    hit.addEventListener("pointermove", mv); hit.addEventListener("pointerdown", mv); hit.addEventListener("pointerleave", () => (tip.style.opacity = 0));
  }

  function renderBrief() {
    const s = spot(), b = s.briefing[state.profile];
    $("#brief").innerHTML = `<p class="hd">${b.headline}</p><ul>${b.lines.map((l) => `<li>${l}</li>`).join("")}</ul>${s.model_note ? `<p>${s.model_note}</p>` : ""}<p class="dayhint">The agent assumes the resort runs its snow guns whenever the weather allows. Switch off "Count machine snow" at the top to see natural snow only.</p>`;
  }

  function renderHow() {
    const a = state.data.assumptions, p = a.profiles;
    $("#how").innerHTML = `<p><b>Models.</b> The agent asks ${Object.keys(state.data.models).length} weather models for the same hour-by-hour forecast at each ski hill (ECMWF IFS and its AI sibling AIFS, NOAA GFS, DWD ICON, Canada GEM, Météo-France, UK Met Office, MET Norway, DMI and KNMI HARMONIE, JMA). Short-range models stop after 2 to 10 days, so each day shows how many are still reporting. Days with fewer than 3 models never trigger a verdict.</p>
      <p><b>Snow on the ground.</b> Every model's snowfall, rain and temperature go through the same simple snowpack: fresh snow adds up, settles about 3.5% a day, and melts ${a.melt_cm_per_degree_hour} cm per degree-hour above freezing (rain melts it faster).</p>
      <p><b>Machine snow.</b> Guns start working when the wet-bulb temperature drops below ${a.snowmaking_wetbulb_start_c} °C and reach full output at ${a.snowmaking_wetbulb_full_c} °C, building up to ${a.snow_gun_cm_per_hour} cm an hour. This is a "could the resort do it" estimate, not a promise that they will.</p>
      <p><b>Score.</b> Each day gets 0 to 100: up to 70 points for snow cover, 20 for comfortable daytime temperature (about −10 to 0 °C), 10 for wind below 45 km/h gusts. Skiable is ${a.skiable_score} or more, great is ${a.great_score}. ${p.park.label} needs ${p.park.min_cm} cm to score at all and ${p.park.full_cm} cm for full marks; ${p.groomed.label.toLowerCase()} needs ${p.groomed.min_cm} and ${p.groomed.full_cm} cm.</p>
      <p><b>"X% of models".</b> The share of reporting models whose own simulation scores that day as skiable. A first ski day needs at least half of them, and the next day cannot collapse.</p>
      <p><b>Weeks ahead.</b> ECMWF's 50-member seasonal ensemble. Beyond about three weeks it has little skill for individual weeks, so read it as "how often does a cold week happen across 50 plausible winters".</p>`;
  }

  function renderTable() {
    const s = spot(), host = $("#tbl");
    host.innerHTML = `<table><thead><tr><th>Date</th><th>Models</th><th>Day temp</th><th>Snowfall cm</th><th>Natural cm</th><th>With machine cm</th><th>Gun hours</th><th>Gust km/h</th><th>Score</th><th>Verdict</th></tr></thead><tbody>${s.days.map((d) => {
      if (!d.n) return `<tr><td>${fmtDate(d.date)}</td><td colspan="9">no data</td></tr>`;
      const v = d.ski[state.profile][mode()];
      return `<tr><td>${fmtDate(d.date)}</td><td>${d.n}</td><td>${T(d.tday.med)} (${T(d.tday.min, 0)} to ${T(d.tday.max, 0)})</td><td>${N1(d.snow.med)}</td><td>${N1(d.nat.med)}</td><td>${N1(d.pot.med)}</td><td>${N1(d.sm_h.med, 0)}</td><td>${N1(d.gust.med, 0)}</td><td>${v.score}</td><td>${d.n < MIN_MODELS ? "few models" : VERDICT[v.verdict]}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function renderFooter() {
    const errs = state.data.errors || [];
    $("#err").textContent = errs.length ? `Some data was missing in the latest run: ${errs.join("; ")}` : "";
  }

  // ---------- scrubbing ----------
  function setHour(h, fromUser) {
    state.hour = Math.max(0, Math.min(hourly().time.length - 1, Math.round(h)));
    if (fromUser && state.playing) togglePlay(false);
    syncHour();
  }

  function syncHour() {
    if (!state.data) return;
    const H = hourly(), h = state.hour, t = H.time[h], day = dayAt(h), v = verdictOf(day);
    $("#range").value = h;
    Object.values(charts).forEach((c) => c && c.set(h));
    const nat = H.nat.med[h] || 0, pot = H.pot.med[h] || 0, art = state.machine ? Math.max(0, pot - nat) : 0;
    const hr = +t.slice(11, 13), rate = H.snow.med[h] || 0;
    const running = !!v && (v.verdict === "skiable" || v.verdict === "great") && hr >= 9 && hr < 18;
    if (scene) scene.set({ nat, art, rate, temp: H.temp.med[h] ?? 5, gust: H.gust.med[h] ?? 10, hour: hr + 0.5, running });
    const ro = $("#readout");
    ro.className = "readout " + (v ? (v.verdict === "great" ? "great" : v.verdict === "skiable" ? "ok" : v.verdict === "marginal" ? "marginal" : "") : "");
    ro.innerHTML = `<span class="when">${fmtDate(t.slice(0, 10))}, ${t.slice(11, 16)}</span><span class="big">${T(H.temp.med[h])} °C</span>
      <span class="small">Snow cover ${N1(nat + art, nat + art < 10 ? 1 : 0)} cm${art > 0.2 ? ` (${N1(art)} cm machine)` : ""}</span>
      <span class="small">${rate >= 0.05 ? `Snowing ${N1(rate, 2)} cm/h` : "No snowfall"}</span>
      <span class="small"><b>${!v ? "Too few models" : v.verdict === "no" ? "Not skiable yet" : VERDICT[v.verdict] + " for " + profile().label.toLowerCase()}</b>, ${H.n[h]} of 11 models</span>`;
    $("#hint").textContent = `${fmtDow(t.slice(0, 10))} ${fmtDM(t.slice(0, 10))}`;
    document.querySelectorAll(".day").forEach((b) => (+b.dataset.i === dayOfHour(h) ? b.setAttribute("aria-current", "true") : b.removeAttribute("aria-current")));
  }

  function togglePlay(on) {
    state.playing = on === undefined ? !state.playing : on;
    $("#play").setAttribute("aria-label", state.playing ? "Pause the forecast time-lapse" : "Play the forecast as a time-lapse");
    $("#play").innerHTML = state.playing
      ? `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="4" width="5" height="16"/><rect x="14" y="4" width="5" height="16"/></svg>`
      : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>`;
    cancelAnimationFrame(playRaf);
    if (!state.playing) return;
    if (state.hour >= hourly().time.length - 2) state.hour = 0;
    let last = performance.now(), acc = state.hour;
    const step = (now) => {
      acc += ((now - last) / 1000) * 22; last = now;                         // 22 forecast hours per second
      if (acc >= hourly().time.length - 1) { acc = hourly().time.length - 1; state.hour = acc; syncHour(); togglePlay(false); return; }
      state.hour = Math.floor(acc); syncHour();
      playRaf = requestAnimationFrame(step);
    };
    playRaf = requestAnimationFrame(step);
  }

  // ---------- wire up ----------
  function init() {
    document.querySelectorAll("[data-profile]").forEach((b) => (b.onclick = () => { state.profile = b.dataset.profile; store.set("profile", state.profile); renderAll(); }));
    $("#machine").onchange = (e) => { state.machine = e.target.checked; store.set("machine", state.machine); renderAll(); };
    $("#range").addEventListener("input", (e) => setHour(+e.target.value, true));
    $("#play").onclick = () => togglePlay();
    let rz; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (state.data) { renderCharts(); renderOutlook(); syncHour(); } }, 150); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(false); });
    setInterval(() => load(false), REFRESH_MS);
    setInterval(() => state.data && renderStatus(), 60000);
    load(true);
  }
  init();
})();
