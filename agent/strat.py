#!/usr/bin/env python3
"""
Stratosphere watch
==================
Monitors the Arctic polar vortex and looks for Sudden Stratospheric Warming (SSW) signals,
then works out when a stratospheric event could reach the weather in Latvia.

What it measures (all from free Open-Meteo model data, no key needed):
  * Zonal-mean zonal wind at 60 N, 10 hPa and 50 hPa  - the strength of the polar vortex.
    Westerly (positive) is a normal vortex. A switch to easterly (negative) at 10 hPa in
    winter is the textbook definition of a major SSW.
  * Wave 1 and wave 2 amplitude of the geopotential height at 60 N - the planetary waves
    that batter the vortex. Wave 1 dominant = displacement, wave 2 dominant = split.
  * Eddy heat flux (v'T') at 100 hPa, 60 N - how much wave energy is flowing up into
    the stratosphere. Pulses of it precede warmings.
  * 10 hPa temperature at the pole minus the 60 N average - the second SSW criterion
    (the pole becomes warmer than 60 N).
  * Ensemble consensus: ECMWF IFS and AIFS (51 members each) and GFS (31 members),
    giving the share of members that reverse the winds, day by day.

Output: docs/data/strat.json (read by the dashboard).
Standard library only.   Usage: python agent/strat.py [--force]
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snow_agent import http_json, median, mean  # noqa: E402  (shared helpers)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "strat.json"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

LAT = 60
RING_DET = [i * 22.5 for i in range(16)]   # 16 points: resolves waves 1-4 cleanly
RING_ENS = [i * 45.0 for i in range(8)]    # 8 points: exact zonal mean for waves 1-3 (the ones that matter)

DET_MODELS = [
    ("ecmwf_ifs025",         "ECMWF IFS"),
    ("ecmwf_aifs025_single", "ECMWF AIFS"),
    ("gfs_seamless",         "NOAA GFS"),
    ("ukmo_seamless",        "UK Met Office"),
    ("gem_seamless",         "Canada GEM"),
]
ENS_SOURCES = [
    {"id": "ecmwf_ifs025",  "name": "ECMWF IFS ensemble",  "levels": [10, 50], "days": 16, "det": "ecmwf_ifs025"},
    {"id": "ecmwf_aifs025", "name": "ECMWF AIFS ensemble", "levels": [10, 50], "days": 16, "det": "ecmwf_aifs025_single"},
    {"id": "gfs05",         "name": "GFS ensemble",        "levels": [50],     "days": 35, "det": "gfs_seamless"},
]
REFRESH_HOURS = {"det": 5.5, "ens": 11.0}   # models update 4x a day; the heavy ensemble pull runs 2x a day

# --- rules of thumb (documented on the dashboard) ---
SEASON_MONTHS = (11, 12, 1, 2, 3)        # major SSWs are defined for November to March
WEAKENING_DROP = 12.0                    # m/s fall in the median 10 hPa wind that counts as a weakening
HF_STRONG, HF_MODERATE = 30.0, 15.0      # K m/s at 100 hPa, 60 N
LAG_WEEKS = (3, 6)                       # tropospheric lag after a wind reversal
LOWER_STRAT_LAG_DAYS = (7, 14)           # typical 10 hPa -> 50 hPa lag when the forecast cannot show it
MIN_VALID_HOURS = 20
TIME_BUDGET_S = 420        # stop fetching after 7 minutes so a slow API never holds up the snow forecast
T_START = time.time()
DIAG: list[str] = []       # timings and quota cost of every call, saved in strat.json for tuning


def over_budget():
    return time.time() - T_START > TIME_BUDGET_S


def pace():
    """Open-Meteo allows 600 cost units a minute: after a call, wait long enough to stay under that on average."""
    try:
        wait = min(75.0, float(http_json.last_cost or 0) / 10.0 + 2.0)
    except (TypeError, ValueError):
        wait = 5.0
    if not over_budget():
        time.sleep(wait)


def timed(label, fn, *a, **kw):
    t = time.time()
    try:
        return fn(*a, **kw)
    finally:
        DIAG.append(f"{label}: {time.time() - t:.0f}s, cost {http_json.last_cost}")


# ------------------------------------------------------------------ math ----
def uv(ws, wd):
    """Meteorological wind (speed, direction it blows FROM) -> (u east, v north)."""
    r = math.radians(wd)
    return -ws * math.sin(r), -ws * math.cos(r)


def wave_amp(vals, lons, k):
    """Amplitude of zonal wave number k of a ring of values (e.g. geopotential height in m)."""
    n = len(vals)
    c = sum(v * math.cos(math.radians(k * lo)) for v, lo in zip(vals, lons))
    s = sum(v * math.sin(math.radians(k * lo)) for v, lo in zip(vals, lons))
    return 2.0 * math.hypot(c, s) / n


def eddy_flux(v_list, t_list):
    vb, tb = mean(v_list), mean(t_list)
    return mean([(v - vb) * (t - tb) for v, t in zip(v_list, t_list)])


def daily(times, hourly_vals):
    """Hourly list (None = missing) -> {date: daily mean}, needing >= 20 valid hours."""
    acc: dict[str, list[float]] = {}
    for t, v in zip(times, hourly_vals):
        if v is not None:
            acc.setdefault(t[:10], []).append(v)
    return {d: sum(x) / len(x) for d, x in acc.items() if len(x) >= MIN_VALID_HOURS}


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def rnd(x, nd=1):
    return None if x is None else round(x, nd)


def as_list(raw):
    return raw if isinstance(raw, list) else [raw]


# ------------------------------------------------------ deterministic part --
def fetch_det():
    hv = []
    hv += ["wind_speed_10hPa", "wind_direction_10hPa", "temperature_10hPa", "geopotential_height_10hPa",
           "wind_speed_50hPa", "wind_direction_50hPa",
           "wind_speed_100hPa", "wind_direction_100hPa", "temperature_100hPa"]
    ids = ",".join(m for m, _ in DET_MODELS)
    raw = http_json({
        "latitude": ",".join([str(LAT)] * len(RING_DET)), "longitude": ",".join(str(x) for x in RING_DET),
        "hourly": ",".join(hv), "models": ids, "forecast_days": 16, "timezone": "GMT", "wind_speed_unit": "ms",
    }, url=FORECAST_URL, timeout=180)
    print(f"  det ring fetched (cost {http_json.last_cost})", flush=True)
    locs = as_list(raw)
    pole = None
    try:  # polar temperature is optional: the SSW verdict still works without it
        time.sleep(3)
        pole = as_list(http_json({"latitude": 90, "longitude": 0, "hourly": "temperature_10hPa", "models": ids,
                                  "forecast_days": 16, "timezone": "GMT"}, url=FORECAST_URL, timeout=60))[0]
    except Exception as e:
        print(f"  pole temperature unavailable: {e}", file=sys.stderr, flush=True)
    return {"locs": locs, "pole": pole}


def key_for(h, var, lev, model):
    for k in (f"{var}_{lev}hPa_{model}", f"{var}_{lev}hPa"):
        if k in h:
            return h[k]
    return None


def analyse_det_model(model, pack):
    locs = pack["locs"][:len(RING_DET)]
    times = locs[0]["hourly"]["time"]
    n = len(times)
    H = [loc["hourly"] for loc in locs]

    def level_arrays(lev):
        out = {}
        for var in ("wind_speed", "wind_direction", "temperature", "geopotential_height"):
            out[var] = [key_for(h, var, lev, model) for h in H]
        return out

    L = {lev: level_arrays(lev) for lev in (10, 50, 100)}
    series = {k: [None] * n for k in ("u10", "u50", "w1_10", "w2_10", "hf100", "t10_ring")}

    def ok(arrs, i):
        return all(a is not None and a[i] is not None for a in arrs)

    for i in range(n):
        for lev in (10, 50):
            ws, wd = L[lev]["wind_speed"], L[lev]["wind_direction"]
            if ok(ws, i) and ok(wd, i):
                series[f"u{lev}"][i] = mean([uv(ws[j][i], wd[j][i])[0] for j in range(len(H))])
            z = L[lev]["geopotential_height"]
            if lev == 10 and ok(z, i):
                vals = [z[j][i] for j in range(len(H))]
                series["w1_10"][i] = wave_amp(vals, RING_DET, 1)
                series["w2_10"][i] = wave_amp(vals, RING_DET, 2)
        t10 = L[10]["temperature"]
        if ok(t10, i):
            series["t10_ring"][i] = mean([t10[j][i] for j in range(len(H))])
        ws, wd, tt = L[100]["wind_speed"], L[100]["wind_direction"], L[100]["temperature"]
        if ok(ws, i) and ok(wd, i) and ok(tt, i):
            vs = [uv(ws[j][i], wd[j][i])[1] for j in range(len(H))]
            series["hf100"][i] = eddy_flux(vs, [tt[j][i] for j in range(len(H))])

    d = {k: daily(times, v) for k, v in series.items()}
    # polar temperature (optional)
    pole_daily = {}
    if pack.get("pole"):
        arr = key_for(pack["pole"]["hourly"], "temperature", 10, model)
        if arr:
            pole_daily = daily(pack["pole"]["hourly"]["time"], arr)
    dates = sorted(set(times[i][:10] for i in range(n)))
    out = {"dates": dates}
    for k in ("u10", "u50"):
        out[k] = [rnd(d[k].get(x)) for x in dates]
    for k in ("w1_10", "w2_10"):
        out[k] = [rnd(d[k].get(x), 0) for x in dates]
    out["hf100"] = [rnd(d["hf100"].get(x)) for x in dates]
    out["pole_gap"] = [rnd(pole_daily[x] - d["t10_ring"][x]) if x in pole_daily and x in d["t10_ring"] else None for x in dates]
    return out


# ----------------------------------------------------------- ensemble part --
MEMBER_RE = re.compile(r"member(\d+)")


def fetch_ens(src):
    hv = []
    for lev in src["levels"]:
        hv += [f"wind_speed_{lev}hPa", f"wind_direction_{lev}hPa"]
    raw = http_json({
        "latitude": ",".join([str(LAT)] * len(RING_ENS)), "longitude": ",".join(str(x) for x in RING_ENS),
        "hourly": ",".join(hv), "models": src["id"], "forecast_days": src["days"], "timezone": "GMT", "wind_speed_unit": "ms",
    }, url=ENSEMBLE_URL, timeout=240)
    print(f"  {src['name']} fetched (cost {http_json.last_cost})", flush=True)
    return as_list(raw)


def analyse_ens(src, locs, lev):
    times = locs[0]["hourly"]["time"]
    n = len(times)
    H = [loc["hourly"] for loc in locs]
    speed_keys = [k for k in H[0] if k.startswith(f"wind_speed_{lev}hPa")]
    members = {}
    for k in speed_keys:
        m = MEMBER_RE.search(k)
        members[m.group(1) if m else "00"] = k
    daily_u = []
    for mid, ks in members.items():
        kd = ks.replace("wind_speed", "wind_direction", 1)
        if not all(kd in h and ks in h for h in H):
            continue
        hourly_u = [None] * n
        for i in range(n):
            vals = []
            for h in H:
                a, b = h[ks][i], h[kd][i]
                if a is None or b is None:
                    vals = None
                    break
                vals.append(uv(a, b)[0])
            if vals:
                hourly_u[i] = mean(vals)
        daily_u.append(daily(times, hourly_u))
    dates = sorted(set(t[:10] for t in times))
    nmem = len(daily_u)
    if nmem < 5:
        raise RuntimeError(f"only {nmem} usable members")
    qs = {q: [] for q in ("p10", "p25", "p50", "p75", "p90")}
    prob, cum = [], []
    first_rev = []   # per member: index of first reversal day
    for mu in daily_u:
        idx = next((i for i, d in enumerate(dates) if d in mu and mu[d] < 0), None)
        first_rev.append(idx)
    for i, d in enumerate(dates):
        vals = sorted(mu[d] for mu in daily_u if d in mu)
        if len(vals) < max(5, nmem * 0.6):
            for q in qs:
                qs[q].append(None)
            prob.append(None)
            cum.append(None)
            continue
        for q, key in ((0.1, "p10"), (0.25, "p25"), (0.5, "p50"), (0.75, "p75"), (0.9, "p90")):
            qs[key].append(rnd(quantile(vals, q)))
        prob.append(rnd(sum(1 for v in vals if v < 0) / len(vals), 3))
        cum.append(rnd(sum(1 for f in first_rev if f is not None and f <= i) / nmem, 3))
    revs = sorted(f for f in first_rev if f is not None)
    first_med = dates[int(median(revs))] if revs else None
    last_cum = next((c for c in reversed(cum) if c is not None), 0.0)
    cross50 = next((dates[i] for i, c in enumerate(cum) if c is not None and c >= 0.5), None)
    return {"id": src["id"], "name": src["name"], "level": lev, "members": nmem, "dates": dates,
            **qs, "prob_rev": prob, "cum_prob": cum, "p_any": last_cum,
            "first_rev_median": first_med, "cross50": cross50}


# ----------------------------------------------------------------- analysis --
def iso_add(iso, days):
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def fmt(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def med_date(isos):
    ords = sorted(date.fromisoformat(x).toordinal() for x in isos)
    return date.fromordinal(int(median(ords))).isoformat()


def det_summary(m):
    dates = m["dates"]
    u = m["u10"]
    valid = [(i, v) for i, v in enumerate(u) if v is not None]
    if len(valid) < 3:
        return None
    i0, u0 = valid[0]
    imin, umin = min(valid, key=lambda t: t[1])
    rev = next((dates[i] for i, v in valid if v < 0), None)
    u50 = [(i, v) for i, v in enumerate(m["u50"]) if v is not None]
    rev50 = next((dates[i] for i, v in u50 if v < 0), None)
    gaps = [(i, v) for i, v in enumerate(m["pole_gap"]) if v is not None]
    warm = next((dates[i] for i, v in gaps if v > 0), None)
    return {"u_start": u0, "u_min": umin, "min_date": dates[imin], "rev_date": rev,
            "easterly_days": sum(1 for _, v in valid if v < 0), "drop": rnd(u0 - umin),
            "last_date": dates[valid[-1][0]], "days": len(valid), "rev50_date": rev50, "pole_warm_date": warm}


def first_valid(vals):
    return next((v for v in vals if v is not None), None)


def assess(det, ens, today_iso):
    names = det["names"]
    summaries = {m: det_summary(d) for m, d in det["models"].items()}
    summaries = {m: s for m, s in summaries.items() if s and s["days"] >= 5}
    ens10 = [e for e in ens if e["level"] == 10]
    ens50 = [e for e in ens if e["level"] == 50]
    month = int(today_iso[5:7])
    in_season = month in SEASON_MONTHS

    # ---- evidence of a reversal at 10 hPa ----
    evidence = []   # (source, date, kind)
    for m, s in summaries.items():
        if s["rev_date"]:
            evidence.append((names[m], s["rev_date"], "model"))
    for e in ens10:
        if e["p_any"] >= 0.15 and (e["cross50"] or e["first_rev_median"]):
            evidence.append((e["name"], e["cross50"] or e["first_rev_median"], "ensemble"))
    n_det_rev = sum(1 for m, s in summaries.items() if s["rev_date"])
    p_max = max([e["p_any"] for e in ens10], default=0.0)
    drops = []
    for e in ens10:
        p50 = [v for v in e["p50"] if v is not None]
        if p50:
            drops.append(p50[0] - min(p50))
    pole_warm = [s["pole_warm_date"] for s in summaries.values() if s["pole_warm_date"]]
    weakening = max(drops, default=0) >= WEAKENING_DROP

    if p_max >= 0.5 or n_det_rev >= 2:
        status = "ssw_signal"
    elif p_max >= 0.10 or n_det_rev >= 1 or weakening or pole_warm:
        status = "warming_watch"
    else:
        status = "no_signal"
    if not in_season and status != "no_signal":
        status = "warming_watch" if status == "ssw_signal" else status

    cons_date = med_date([d for _, d, _ in evidence]) if evidence else None
    cons_range = [min(d for _, d, _ in evidence), max(d for _, d, _ in evidence)] if evidence else None

    # ---- vortex now ----
    ref = next((m for m in ("ecmwf_ifs025", "gfs_seamless", "ecmwf_aifs025_single") if m in summaries), None)
    now = {"ref_model": names.get(ref) if ref else None}
    if ref:
        d = det["models"][ref]
        now["u10"] = first_valid(d["u10"])
        now["u50"] = first_valid(d["u50"])
        u = [v for v in d["u10"] if v is not None]
        now["u10_end"] = u[-1]
        now["u10_end_date"] = d["dates"][max(i for i, v in enumerate(d["u10"]) if v is not None)]
        k = min(7, len(u) - 1)
        now["u10_change_7d"] = rnd(u[k] - u[0])
    # ---- waves & heat flux ----
    wave = None
    wref = next((m for m in ("ecmwf_ifs025", "gfs_seamless") if m in det["models"]), None)
    if wref:
        d = det["models"][wref]
        w1 = [(i, v) for i, v in enumerate(d["w1_10"]) if v is not None]
        w2 = [(i, v) for i, v in enumerate(d["w2_10"]) if v is not None]
        if w1 and w2:
            p1, p2 = max(w1, key=lambda t: t[1]), max(w2, key=lambda t: t[1])
            wave = {"model": names[wref], "w1_now": w1[0][1], "w2_now": w2[0][1],
                    "w1_peak": p1[1], "w1_peak_date": d["dates"][p1[0]],
                    "w2_peak": p2[1], "w2_peak_date": d["dates"][p2[0]],
                    "dominant": "wave 1 (displacement-type)" if p1[1] >= p2[1] else "wave 2 (split-type)"}
    hf_peaks = []
    for m, d in det["models"].items():
        v = [(i, x) for i, x in enumerate(d["hf100"]) if x is not None]
        if len(v) >= 5:
            i, x = max(v, key=lambda t: t[1])
            hf_peaks.append({"model": names[m], "peak": x, "date": d["dates"][i], "now": v[0][1]})
    heat = None
    if hf_peaks:
        top = max(hf_peaks, key=lambda t: t["peak"])
        heat = {"peaks": hf_peaks, "max_peak": top["peak"], "max_date": top["date"], "max_model": top["model"],
                "level": "strong" if top["peak"] >= HF_STRONG else "moderate" if top["peak"] >= HF_MODERATE else "quiet"}

    # ---- lower stratosphere (50 hPa) evidence ----
    low_dates = [s["rev50_date"] for s in summaries.values() if s["rev50_date"]]
    for e in ens50:
        if e["p_any"] >= 0.15 and (e["cross50"] or e["first_rev_median"]):
            low_dates.append(e["cross50"] or e["first_rev_median"])
    low_date = med_date(low_dates) if low_dates else None

    horizon_end = max([e["dates"][-1] for e in ens10] + [s["last_date"] for s in summaries.values()] or [today_iso])
    long_end = max([e["dates"][-1] for e in ens50] or [horizon_end])

    # ---- propagation timeline ----
    prop = {"lag_weeks": list(LAG_WEEKS), "horizon_end": horizon_end, "long_end": long_end, "milestones": [], "blind_spot": None}
    if cons_date and status != "no_signal":
        lo, hi = cons_range
        lag_lo, lag_hi = LAG_WEEKS[0] * 7, LAG_WEEKS[1] * 7
        prop["event_date"] = cons_date
        prop["milestones"] = [
            {"key": "reversal", "label": "Winds reverse at 10 hPa", "start": lo, "end": hi, "basis": "forecast"},
            {"key": "lower", "label": "Signal reaches the lower stratosphere (50 hPa)",
             "start": low_date if low_date else iso_add(cons_date, LOWER_STRAT_LAG_DAYS[0]),
             "end": low_date if low_date else iso_add(cons_date, LOWER_STRAT_LAG_DAYS[1]),
             "basis": "forecast" if low_date else "typical 1 to 2 weeks"},
            {"key": "tropo", "label": "Tropospheric response: weather patterns shift",
             "start": iso_add(cons_date, lag_lo), "end": iso_add(cons_date, lag_hi), "basis": "typical 3 to 6 weeks"},
        ]
    else:
        prop["blind_spot"] = {"from": horizon_end, "earliest_impact": iso_add(horizon_end, LAG_WEEKS[0] * 7)}

    # ---- plain-language briefing ----
    lines = []
    if now.get("u10") is not None:
        ch = now.get("u10_change_7d")
        trend = "" if ch is None else (f", {'weakening' if ch < -1 else 'strengthening' if ch > 1 else 'steady'} by {abs(ch):.0f} m/s over the next 7 days" if abs(ch) >= 1 else ", steady over the next 7 days")
        low = f" and {now['u50']:.0f} m/s at 50 hPa" if now.get("u50") is not None else ""
        lines.append(f"Vortex now: westerly winds of {now['u10']:.0f} m/s at 10 hPa{low} ({now['ref_model']}){trend}." if now["u10"] >= 0
                     else f"Vortex now: the 10 hPa winds are already easterly ({now['u10']:.0f} m/s) in {now['ref_model']}.")
    if wave:
        lines.append(f"Wave activity: wave 1 is {wave['w1_now']:.0f} m and wave 2 {wave['w2_now']:.0f} m in amplitude at 10 hPa now; "
                     f"the strongest forecast wave is {wave['dominant']}, peaking {fmt(wave['w1_peak_date'] if wave['w1_peak'] >= wave['w2_peak'] else wave['w2_peak_date'])}.")
    if heat:
        word = {"strong": "a strong pulse of wave energy flows up into the stratosphere", "moderate": "a moderate amount of wave energy flows up", "quiet": "little wave energy is flowing up"}[heat["level"]]
        lines.append(f"Heat flux at 100 hPa peaks at {heat['max_peak']:.0f} K m/s on {fmt(heat['max_date'])} ({heat['max_model']}): {word}.")
    if status == "no_signal":
        ens_txt = ", ".join(f"{e['name'].replace(' ensemble', '')} {round(e['p_any'] * 100)}%" for e in ens10)
        few = "no ensemble member does" if p_max < 0.02 else "few ensemble members do"
        lines.append(f"SSW signal: none. No model reverses the 10 hPa winds before {fmt(horizon_end)}"
                     + (f" and {few} ({ens_txt})." if ens10 else "."))
    else:
        ens_txt = "; ".join(f"{e['name']} {round(e['p_any'] * 100)}% of {e['members']} members" for e in ens10)
        lo_d, hi_d = cons_range
        n_src = len(summaries) + len(ens10)
        spread = fmt(lo_d) if lo_d == hi_d else f"between {fmt(lo_d)} and {fmt(hi_d)}"
        lines.append(f"SSW signal: {'strong consensus' if status == 'ssw_signal' else 'early warning'}. "
                     f"{len(evidence)} of {n_src} sources reverse the 10 hPa winds, {spread}."
                     + (f" Share of members that reverse by {fmt(horizon_end)}: {ens_txt}." if ens_txt else ""))
        if pole_warm:
            lines.append(f"The pole is forecast warmer than 60 N at 10 hPa from {fmt(min(pole_warm))}, the second SSW criterion.")
    if prop["milestones"]:
        m = {x["key"]: x for x in prop["milestones"]}
        lines.append(f"Downward propagation: reversal around {fmt(cons_date)}; lower stratosphere {fmt(m['lower']['start'])}"
                     f"{'' if m['lower']['start'] == m['lower']['end'] else ' to ' + fmt(m['lower']['end'])}; "
                     f"tropospheric response most likely {fmt(m['tropo']['start'])} to {fmt(m['tropo']['end'])} (3 to 6 weeks after the reversal).")
    elif prop["blind_spot"]:
        lines.append(f"Blind spot: forecasts only see the stratosphere to {fmt(horizon_end)}. A warming that starts after that would reach "
                     f"Latvia's weather no sooner than {fmt(prop['blind_spot']['earliest_impact'])}.")
    if not in_season:
        lines.append("This is outside the November to March season in which a wind reversal counts as a major SSW.")

    headline = {
        "no_signal": "No sudden warming in sight",
        "warming_watch": "Early warming signal" + (f": winds may reverse around {fmt(cons_date)}" if cons_date else ""),
        "ssw_signal": "Warming signal" + (f": winds likely reverse around {fmt(cons_date)}" if cons_date else ""),
    }[status]
    sub = {
        "no_signal": "The polar vortex stays westerly through the whole forecast, so the stratosphere is not a source of cold for Latvia yet.",
        "warming_watch": "Some models or ensemble members weaken or reverse the vortex. If it verifies, cold can reach Latvia 3 to 6 weeks later.",
        "ssw_signal": "Models agree the vortex breaks down. Cold outbreaks over Latvia become more likely 3 to 6 weeks after the reversal.",
    }[status]
    return {"status": status, "headline": headline, "sub": sub, "season_ok": in_season, "now": now, "wave": wave, "heat": heat,
            "evidence": [{"source": s, "date": d, "kind": k} for s, d, k in evidence], "consensus_date": cons_date,
            "consensus_range": cons_range, "p_max": p_max, "n_det_reversal": n_det_rev, "n_det": len(summaries),
            "pole_warm_date": min(pole_warm) if pole_warm else None, "lower_date": low_date,
            "propagation": prop, "lines": lines,
            "det_summary": {names[m]: s for m, s in summaries.items()}}


# --------------------------------------------------------------------- main --
def age_hours(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
    except Exception:
        return 1e9


def main():
    force = "--force" in sys.argv
    try:
        prev = json.loads(OUT.read_text(encoding="utf8"))
    except Exception:
        prev = {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    errors = []
    parts = {}

    # deterministic models -------------------------------------------------
    pd = prev.get("det")
    if pd and not force and age_hours(pd["fetched"]) < REFRESH_HOURS["det"]:
        parts["det"] = pd
        print("det: fresh enough, reusing")
    else:
        try:
            pack = timed("deterministic ring", fetch_det)
            pace()
            models = {}
            for mid, name in DET_MODELS:
                try:
                    models[mid] = analyse_det_model(mid, pack)
                except Exception as e:
                    errors.append(f"{name}: {e}")
            if not models:
                raise RuntimeError("no model could be analysed")
            parts["det"] = {"fetched": now_iso, "models": models, "names": dict(DET_MODELS)}
            print(f"det: {len(models)} models analysed")
        except Exception as e:
            errors.append(f"deterministic models: {e}")
            if pd:
                parts["det"] = pd
                errors.append("deterministic models: showing the previous run")

    # ensembles --------------------------------------------------------------
    pe = prev.get("ens")
    if pe and not force and age_hours(pe["fetched"]) < REFRESH_HOURS["ens"]:
        parts["ens"] = pe
        print("ens: fresh enough, reusing")
    else:
        results, old = [], {(e["id"], e["level"]): e for e in (pe or {}).get("series", [])}
        for src in ENS_SOURCES:
            try:
                if over_budget():
                    raise RuntimeError("skipped, time budget used up")
                locs = timed(src["name"], fetch_ens, src)
                pace()
                for lev in src["levels"]:
                    results.append(analyse_ens(src, locs, lev))
            except Exception as e:
                errors.append(f"{src['name']}: {e}")
                for lev in src["levels"]:
                    if (src["id"], lev) in old:
                        results.append(old[(src["id"], lev)])
                        errors.append(f"{src['name']}: showing the previous run")
        parts["ens"] = {"fetched": now_iso if results else (pe or {}).get("fetched", now_iso), "series": results}
        print(f"ens: {len(results)} series")

    if "det" not in parts:
        sys.exit("No deterministic data at all - keeping the previous strat.json.")
    ens = parts["ens"]["series"] if "ens" in parts else []
    today = date.today().isoformat()
    first_dates = [m["dates"][0] for m in parts["det"]["models"].values()]
    today = min(first_dates) if first_dates else today
    result = assess(parts["det"], ens, today)

    newest = max(parts["det"]["fetched"], parts["ens"]["fetched"] if "ens" in parts else "")
    out = {"generated_at": newest, "det": parts["det"], "ens": parts["ens"], "assessment": result,
           "rules": {"season": "November to March", "weakening_drop_ms": WEAKENING_DROP, "hf_strong": HF_STRONG,
                     "hf_moderate": HF_MODERATE, "lag_weeks": list(LAG_WEEKS), "lower_strat_lag_days": list(LOWER_STRAT_LAG_DAYS)},
           "diagnostics": DIAG, "errors": errors}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    print(f"wrote strat.json: {result['status']} - {result['headline']}")
    for ln in result["lines"]:
        print("  -", ln)
    if errors:
        print("notes:", *errors, sep="\n  ", file=sys.stderr)


STATUS = ROOT / "docs" / "data" / "strat_status.json"


def write_status(ok, error=None):
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": ok,
                                      "error": error, "diagnostics": DIAG}, ensure_ascii=False), encoding="utf8")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
        write_status(True)
    except SystemExit as e:           # main() uses sys.exit("message") for hard failures
        write_status(False, str(e.code))
        raise
    except Exception as e:
        write_status(False, f"{type(e).__name__}: {e}")
        raise
