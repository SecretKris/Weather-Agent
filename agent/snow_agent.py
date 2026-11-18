#!/usr/bin/env python3
"""
Latvia Snow Agent
=================
Pulls hourly forecasts from many weather models (Open-Meteo, free, no key),
runs each model through the same simple snowpack + snowmaking simulation,
scores every day for skiing, and measures how much the models agree.

Output (read by the dashboard):
    docs/data/forecast.json   latest analysis
    docs/data/history.json    one line per run, used for "forecast trend"

Standard library only - nothing to install.
Usage:  python agent/snow_agent.py
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.open-meteo.com/v1/forecast"
SEASONAL_API = "https://seasonal-api.open-meteo.com/v1/seasonal"

# ---------------------------------------------------------------- models ---
MODELS = {
    "ecmwf_ifs025":         "ECMWF IFS",
    "ecmwf_aifs025_single": "ECMWF AIFS (AI)",
    "gfs_seamless":         "NOAA GFS",
    "icon_seamless":        "DWD ICON",
    "gem_seamless":         "Canada GEM",
    "meteofrance_seamless": "Météo-France",
    "ukmo_seamless":        "UK Met Office",
    "metno_seamless":       "MET Norway",
    "dmi_seamless":         "DMI HARMONIE",
    "knmi_seamless":        "KNMI HARMONIE",
    "jma_seamless":         "JMA",
}
VARS = ["temperature_2m", "relative_humidity_2m", "precipitation",
        "snowfall", "snow_depth", "wind_gusts_10m"]

# ------------------------------------------------------ simulation knobs ---
SNOW_CM_PER_MM = 0.7      # Open-Meteo: 7 cm snow = 10 mm water equivalent
COMPACT_H      = 0.0015   # snowpack settles ~3.5 % per day
MELT_CM_PER_DEGH = 0.07   # cm of snow melted per degree-hour above 0 C
RAIN_MELT_CM_PER_MM = 0.25
GUN_CM_H       = 0.8      # snow guns: cm of base built per hour at full efficiency
GUN_FULL_AT    = -7.0     # wet-bulb C where guns hit full output
GUN_START_AT   = -2.0     # wet-bulb C where guns start to work
ART_MELT_FACTOR = 0.6     # machine snow is denser, melts slower
ART_CAP_CM     = 60.0

# Resort-specific snowmaking (spots with a "resort" block in config/spots.json).
# Snow output of one medium-class fan gun in m3 of fresh machine snow per hour, by wet-bulb temperature.
# Anchored on manufacturer datasheets (Bachler SnoTek: ~18-35 m3/h at -3 C wet-bulb for mid-size fans,
# 67 m3/h at -5 C and 124 m3/h at -8 C for large ones); "efficiency" in the config then trims it for
# moving hoses, pump limits, wind and downtime.
FAN_GUN_CURVE = [(-2.0, 8.0), (-3.0, 20.0), (-5.0, 38.0), (-7.0, 52.0), (-10.0, 65.0), (-15.0, 70.0)]
NAT_PACK = 0.35           # fresh natural snow packs down to about a third of its depth once groomed
SNOW_PER_WATER = 2.2      # 1 m3 of water makes about 2.2 m3 of machine snow
PLAN_MIN_DAYS = 7         # a model needs this many forecast days to vote on an opening date
HOURS_AT = (-3, -5, -7, -10)

# What "good enough to ski" means, per riding style
PROFILES = {
    "park":    {"label": "Freestyle / park", "min_cm": 20, "full_cm": 45},
    "groomed": {"label": "Groomed runs",     "min_cm": 10, "full_cm": 30},
}
SKIABLE, GREAT, MARGINAL = 50, 75, 30
DAYTIME = range(9, 18)    # 09:00-17:59 local = lift hours
MIN_MODELS = 3            # a day needs at least this many models before it can trigger a verdict


# ------------------------------------------------------------- helpers -----
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def mean(v):
    return sum(v) / len(v) if v else None


def r1(x, nd=1):
    return None if x is None else round(x, nd)


def spread(vals, nd=1):
    """Ensemble summary over models: median / min / max / how many."""
    vals = [x for x in vals if x is not None]
    if not vals:
        return {"med": None, "min": None, "max": None, "n": 0}
    return {"med": r1(median(vals), nd), "min": r1(min(vals), nd),
            "max": r1(max(vals), nd), "n": len(vals)}


def wetbulb(t, rh):
    """Stull (2011) wet-bulb approximation, valid for normal surface conditions."""
    rh = clamp(rh, 1, 100)
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def http_json(params, tries=5, url=API, timeout=25):
    q = urllib.parse.urlencode(params, safe=",")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                f"{url}?{q}",
                headers={"User-Agent": "latvia-snow-agent/1.0 (personal, non-commercial)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                http_json.last_cost = r.headers.get("X-RateLimit-Cost")
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf8", "ignore")[:300]
            last = f"HTTP {e.code}: {body}"
            if e.code == 429 and "Daily" in body:   # quota used up for today: retrying cannot help
                break
            if e.code == 429 and i < tries - 1:     # per-minute limit: wait for the window to reset
                time.sleep(62)
                continue
            if e.code in (500, 502, 503, 504) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            break
        except OSError as e:  # URLError, TLS and socket timeouts are all OSError
            last = str(e)
            if i < tries - 1:
                time.sleep(3 * (i + 1))
                continue
    raise RuntimeError(last or "request failed")


http_json.last_cost = None


# ------------------------------------------------------------ scoring ------
def ski_score(cover_cm, t_day, gust, prof):
    """0-100. Snow cover is 70 pts, comfortable temperature 20, wind 10."""
    if cover_cm is None or t_day is None:
        return None
    base = 70 * clamp((cover_cm - prof["min_cm"]) / (prof["full_cm"] - prof["min_cm"]))
    if t_day <= 0:
        temp = 20 if t_day >= -10 else 20 * clamp(1 - (-10 - t_day) / 10)
    else:
        temp = 20 * clamp(1 - t_day / 3)
    wind = 10 * clamp(1 - max(0, (gust or 0) - 45) / 35)
    s = base + temp + wind
    if cover_cm < prof["min_cm"]:
        s = min(s, 25)
    if t_day > 3:
        s = min(s, 20)
    return round(s)


def verdict(score):
    if score is None:
        return None
    if score >= GREAT:
        return "great"
    if score >= SKIABLE:
        return "skiable"
    if score >= MARGINAL:
        return "marginal"
    return "no"


# --------------------------------------------------------- simulation ------
def derive_hourly(m, n):
    """Snowfall, rain and wet-bulb for one model, filling gaps sensibly."""
    T, RH, P, S = (m["temperature_2m"], m["relative_humidity_2m"],
                   m["precipitation"], m["snowfall"])
    snow, rain, tw = [None] * n, [None] * n, [None] * n
    for i in range(n):
        t = T[i]
        if t is None:
            continue
        p = P[i] or 0.0
        s = S[i]
        if s is None:  # model has no snowfall field: estimate from precip + temperature
            s = p * clamp((1.5 - t) / 2.5) * SNOW_CM_PER_MM
        snow[i] = s
        rain[i] = max(0.0, p - s / SNOW_CM_PER_MM)
        rh = RH[i] if RH[i] is not None else 85.0
        tw[i] = wetbulb(t, rh)
    return snow, rain, tw


def gun_m3h(w):
    """Fresh machine snow per medium fan gun per hour (m3) at wet-bulb w (C)."""
    if w is None or w > FAN_GUN_CURVE[0][0]:
        return 0.0
    for (w1, q1), (w2, q2) in zip(FAN_GUN_CURVE, FAN_GUN_CURVE[1:]):
        if w2 <= w <= w1:
            return q1 + (q2 - q1) * (w - w1) / (w2 - w1)
    return FAN_GUN_CURVE[-1][1]


def generic_gun_cm(w):
    return GUN_CM_H * clamp((GUN_START_AT - w) / (GUN_START_AT - GUN_FULL_AT))


def simulate(T, snow, rain, tw, init_cm, gun_cm=generic_gun_cm):
    """Hour-by-hour snowpack: natural snow + machine snow (if resort runs guns)."""
    n = len(T)
    nat, art = [None] * n, [None] * n
    d, a = init_cm, 0.0
    for i in range(n):
        t = T[i]
        if t is None:
            break
        d += snow[i] or 0.0
        w = tw[i]
        if w is not None and w < GUN_START_AT:
            a += gun_cm(w)
        a = min(a, ART_CAP_CM)
        d *= 1 - COMPACT_H
        a *= 1 - COMPACT_H * 0.5
        if t > 0:
            melt = MELT_CM_PER_DEGH * t + RAIN_MELT_CM_PER_MM * (rain[i] or 0.0)
            d = max(0.0, d - melt)
            a = max(0.0, a - melt * ART_MELT_FACTOR)
        nat[i], art[i] = d, a
    return nat, art


# ------------------------------------------------- resort opening plan ------
def resort_setup(resort):
    """Slope areas and snow volumes from the config; stages are built in the listed order."""
    stages = []
    for sl in resort["slopes"]:
        area = sl["length_m"] * sl["width_m"]
        stages.append({**sl, "area_m2": area,
                       "snow_m3": round(area * sl["depth_cm"] / 100 + sl.get("features_m3", 0))})
    g = resort["guns"]
    rate = lambda w: g["count"] * g.get("efficiency", 1.0) * gun_m3h(w)  # noqa: E731  m3/h for the whole resort
    ids = [st["id"] for st in stages]
    miles = {pid: ids.index(sid) for pid, sid in resort["milestones"].items() if sid in ids}
    open_area = sum(st["area_m2"] for st in stages[:miles.get("groomed", 0) + 1])
    return {"stages": stages, "rate": rate, "miles": miles, "open_area": open_area,
            "depth_cm": stages[0]["depth_cm"]}


def stage_need(stages, upto, nat_cm):
    """m3 of machine snow still needed for stages[0..upto] when nat_cm of natural snow lies on the ground."""
    packed = NAT_PACK * (nat_cm or 0.0)
    return sum(st["area_m2"] * max(0.0, st["depth_cm"] - packed) / 100 + st.get("features_m3", 0)
               for st in stages[:upto + 1])


def run_plan(plan, T, tw, rain, nat, start, n):
    """One weather model: hour by hour snow made (m3), melt losses, and when each milestone is reached."""
    stages, miles = plan["stages"], plan["miles"]
    total_area = sum(st["area_m2"] for st in stages[:max(miles.values()) + 1])
    depth = plan["depth_cm"] / 100
    cap = stage_need(stages, len(stages) - 1, 0) * 1.05   # guns stop once every slope is built (topping up after melt)
    v, done = 0.0, {}
    vol, need = [None] * n, {pid: [None] * n for pid in miles}
    gun_h = 0
    for i in range(start, n):
        t = T[i]
        if t is None:
            break
        if plan["rate"](tw[i]) > 0:
            gun_h += 1
        q = plan["rate"](tw[i]) if v < cap else 0.0
        v = min(cap, v + q)
        covered = min(total_area, v / depth) if depth > 0 else 0.0
        if t > 0:   # machine snow melts slower than natural snow
            loss_cm = (MELT_CM_PER_DEGH * t + RAIN_MELT_CM_PER_MM * (rain[i] or 0.0)) * ART_MELT_FACTOR
            v = max(0.0, v - covered * loss_cm / 100)
        vol[i] = v
        for pid, k in miles.items():
            nd = stage_need(stages, k, nat[i])
            need[pid][i] = nd
            if pid not in done and v >= nd:
                done[pid] = i
    last = max((i for i in range(start, n) if vol[i] is not None), default=None)
    return {"vol": vol, "need": need, "done": done, "last": last, "gun_h": gun_h}


def plan_summary(spot, plan, runs, times, start, n, dates):
    """Turn per-model opening runs into dates, odds and hours still needed."""
    resort = spot["resort"]
    q5 = plan["rate"](-5.0)
    out = {"slopes": [], "guns": resort["guns"], "vertical_m": resort.get("vertical_m"),
           "weekends_first": bool(resort.get("weekends_first")), "milestones": {}, "hours": {}}
    for st in plan["stages"]:
        out["slopes"].append({k: st.get(k) for k in ("id", "name", "length_m", "width_m", "depth_cm", "features_m3",
                                                      "area_m2", "snow_m3", "size")})
    out["rate_m3h"] = {str(w): r1(plan["rate"](w), 0) for w in HOURS_AT}
    labels = {"groomed": "Central run open", "park": "Snowpark ready"}
    for pid, k in plan["miles"].items():
        base = stage_need(plan["stages"], k, 0)
        voters, opened, remaining = [], [], []
        for mid, r in runs.items():
            if r["last"] is None or (r["last"] - start) < PLAN_MIN_DAYS * 24:
                continue
            voters.append(mid)
            if pid in r["done"]:
                d = (date.fromisoformat(times[r["done"][pid]][:10]) + timedelta(days=1)).isoformat()
                opened.append(d)
            else:
                remaining.append(max(0.0, r["need"][pid][r["last"]] - r["vol"][r["last"]]))
        nv = len(voters)
        ms = {"label": labels.get(pid, pid), "stage": plan["stages"][k]["name"], "snow_m3": round(base),
              "water_m3": round(base / SNOW_PER_WATER), "n": nv, "prob": r1(len(opened) / nv, 2) if nv else None,
              "hours_at": {str(w): (round(base / plan["rate"](w)) if plan["rate"](w) > 0 else None) for w in HOURS_AT}}
        ranked = sorted(opened) + [None] * (nv - len(opened))
        mid_ix = (nv + 1) // 2 - 1 if nv else None
        ms["date"] = ranked[mid_ix] if nv and ranked[mid_ix] else None
        ms["earliest"] = min(opened) if opened else None
        ms["latest"] = max(opened) if opened else None
        ms["opened_models"] = len(opened)
        if ms["date"] and out["weekends_first"]:
            d = date.fromisoformat(ms["date"])
            while d.weekday() < 5:
                d += timedelta(days=1)
            ms["weekend"] = d.isoformat()
        if remaining:
            rem = median(remaining)
            ms["remaining_m3"] = round(rem)
            ms["remaining_h5"] = round(rem / q5) if q5 > 0 else None
        out["milestones"][pid] = ms
    # hourly consensus for the chart
    def stat(get):
        med, lo, hi = [], [], []
        for i in range(start, n):
            vals = [get(r, i) for r in runs.values()]
            vals = [x for x in vals if x is not None]
            med.append(round(median(vals)) if vals else None)
            lo.append(round(min(vals)) if vals else None)
            hi.append(round(max(vals)) if vals else None)
        return {"med": med, "lo": lo, "hi": hi}
    out["hourly"] = {"vol": stat(lambda r, i: r["vol"][i]),
                     "need": {pid: stat(lambda r, i, p=pid: r["need"][p][i])["med"] for pid in plan["miles"]}}
    out["gun_hours"] = r1(median([r["gun_h"] for r in runs.values()]) or 0, 0)
    return out


# ------------------------------------------------------------ one spot -----
def analyse_spot(spot, raw):
    h = raw["hourly"]
    times = h["time"]
    N = len(times)
    offset = raw.get("utc_offset_seconds", 0)
    today = (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%d")
    start = next(i for i, t in enumerate(times) if t.startswith(today))

    # dates -> hour indices (forecast days only)
    day_idx: dict[str, list[int]] = {}
    for i in range(start, N):
        day_idx.setdefault(times[i][:10], []).append(i)
    dates = list(day_idx)

    # collect models that actually returned data
    series = {}
    for mid in MODELS:
        t = h.get(f"temperature_2m_{mid}")
        if not t or sum(x is not None for x in t) < 24:
            continue
        series[mid] = {v: (h.get(f"{v}_{mid}") or [None] * N) for v in VARS}

    # seed the snowpack with what the models say is on the ground right now
    seeds = [m["snow_depth"][0] * 100 for m in series.values() if m["snow_depth"][0] is not None]
    init_cm = median(seeds) or 0.0

    plan = resort_setup(spot["resort"]) if spot.get("resort") else None
    gun_cm = generic_gun_cm
    if plan:   # machine snow spread over the slopes that open first
        gun_cm = lambda w: plan["rate"](w) / plan["open_area"] * 100  # noqa: E731

    per_model, runs = {}, {}
    for mid, m in series.items():
        snow, rain, tw = derive_hourly(m, N)
        nat, art = simulate(m["temperature_2m"], snow, rain, tw, init_cm, gun_cm)
        per_model[mid] = {"T": m["temperature_2m"], "G": m["wind_gusts_10m"], "snow": snow,
                          "tw": tw, "nat": nat, "art": art, "native": m["snow_depth"]}
        if plan:
            runs[mid] = run_plan(plan, m["temperature_2m"], tw, rain, nat, start, N)
            per_model[mid]["ready"] = {pid: times[i][:10] for pid, i in runs[mid]["done"].items()}

    # ---- daily metrics per model ----
    def model_day(pm, ix):
        T = [pm["T"][i] for i in ix]
        if sum(x is not None for x in T) < 20:
            return None
        day_ix = [i for i in ix if int(times[i][11:13]) in DAYTIME and pm["T"][i] is not None]
        if len(day_ix) < 6:
            return None
        t_day = mean([pm["T"][i] for i in day_ix])
        gust = max([pm["G"][i] for i in day_ix if pm["G"][i] is not None] or [0])
        nat = mean([pm["nat"][i] for i in day_ix])
        art = mean([pm["art"][i] for i in day_ix])
        tw_ok = [pm["tw"][i] for i in ix if pm["tw"][i] is not None]
        out = {
            "tmean": mean([x for x in T if x is not None]),
            "tmax": max(x for x in T if x is not None),
            "tmin": min(x for x in T if x is not None),
            "tday": t_day,
            "snow": sum(pm["snow"][i] or 0 for i in ix),
            "gust": gust,
            "nat": nat,
            "pot": nat + art,
            "sm_h": sum(1 for x in tw_ok if x <= GUN_START_AT),
            "score": {},
        }
        today_d = times[ix[0]][:10]
        for pid, prof in PROFILES.items():
            s_nat = ski_score(nat, t_day, gust, prof)
            s_pot = ski_score(nat + art, t_day, gust, prof)
            if "ready" in pm:   # resort spot: lifts only run once the slope for this style is built (the day after)
                ready = pm["ready"].get(pid)
                if ready is None or ready >= today_d:
                    s_pot = s_nat
            out["score"][pid] = {"nat": s_nat, "pot": s_pot}
        return out

    md = {mid: [model_day(pm, day_idx[d]) for d in dates] for mid, pm in per_model.items()}

    # ---- ensemble per day ----
    days = []
    for k, d in enumerate(dates):
        rows = [md[mid][k] for mid in md if md[mid][k] is not None]
        if not rows:
            days.append({"date": d, "n": 0})
            continue
        day = {
            "date": d, "n": len(rows),
            "tmean": spread([r["tmean"] for r in rows]),
            "tday": spread([r["tday"] for r in rows]),
            "tmin": spread([r["tmin"] for r in rows]),
            "tmax": spread([r["tmax"] for r in rows]),
            "snow": spread([r["snow"] for r in rows]),
            "snow_prob": r1(sum(1 for r in rows if r["snow"] >= 0.3) / len(rows), 2),
            "gust": spread([r["gust"] for r in rows], 0),
            "nat": spread([r["nat"] for r in rows]),
            "pot": spread([r["pot"] for r in rows]),
            "sm_h": spread([r["sm_h"] for r in rows], 0),
            "ski": {},
        }
        for pid in PROFILES:
            day["ski"][pid] = {}
            for mode in ("nat", "pot"):
                sc = [r["score"][pid][mode] for r in rows]
                med = median(sc)
                day["ski"][pid][mode] = {
                    "score": round(med), "verdict": verdict(med),
                    "prob": r1(sum(1 for s in sc if s >= SKIABLE) / len(sc), 2),
                    "min": min(sc), "max": max(sc),
                }
        # agreement on temperature
        t_spread = day["tday"]["max"] - day["tday"]["min"]
        day["agreement"] = ("high" if t_spread <= 2.5 else "medium" if t_spread <= 5 else "low")
        days.append(day)

    # ---- first skiable window (ensemble) ----
    def first_window(pid, mode):
        for k, day in enumerate(days):
            if not day.get("ski") or day["n"] < MIN_MODELS:
                continue
            s = day["ski"][pid][mode]
            nxt = days[k + 1] if k + 1 < len(days) and days[k + 1].get("ski") and days[k + 1]["n"] >= MIN_MODELS else None
            if s["prob"] >= 0.5 and (nxt is None or nxt["ski"][pid][mode]["prob"] >= 0.4):
                length = 0
                j = k
                while (j < len(days) and days[j].get("ski") and days[j]["n"] >= MIN_MODELS
                       and days[j]["ski"][pid][mode]["prob"] >= 0.5):
                    length += 1
                    j += 1
                return {"date": day["date"], "prob": s["prob"], "n": day["n"], "days": length,
                        "score": s["score"]}
        return None

    first = {pid: {mode: first_window(pid, mode) for mode in ("nat", "pot")} for pid in PROFILES}

    # closest call when nothing qualifies
    def closest(pid, mode):
        cand = [d for d in days if d.get("ski") and d["n"] >= MIN_MODELS]
        if not cand:
            return None
        b = max(cand, key=lambda d: (d["ski"][pid][mode]["score"], d["ski"][pid][mode]["prob"]))
        s = b["ski"][pid][mode]
        return {"date": b["date"], "score": s["score"], "prob": s["prob"], "n": b["n"],
                "cover": b[mode]["med"]}

    closest_call = {pid: {mode: closest(pid, mode) for mode in ("nat", "pot")} for pid in PROFILES}

    # first snowfall and first cold snap (snowmaking weather)
    first_flakes = next(({"date": d["date"], "cm": d["snow"]["med"], "prob": d["snow_prob"], "n": d["n"]}
                         for d in days if d.get("n", 0) >= MIN_MODELS and d["snow_prob"] >= 0.5), None)
    cold_snap = next(({"date": d["date"], "hours": d["sm_h"]["med"], "n": d["n"]}
                      for d in days if d.get("n", 0) >= MIN_MODELS and d["sm_h"]["med"] >= 6), None)
    cold_total = sum(d["sm_h"]["med"] for d in days if d.get("n", 0) >= MIN_MODELS and d["sm_h"]["med"] is not None)

    # ---- per-model first ski day + snow totals ----
    models_out = []
    for mid, rows in md.items():
        valid = [r for r in rows if r]
        item = {"id": mid, "name": MODELS[mid], "horizon_days": len(valid),
                "snow7": r1(sum(r["snow"] for r in valid[:7])),
                "snow14": r1(sum(r["snow"] for r in valid[:14])),
                "tmean": [r1(r["tmean"]) if r else None for r in rows],
                "snow": [r1(r["snow"]) if r else None for r in rows],
                "pot": [r1(r["pot"]) if r else None for r in rows],
                "nat": [r1(r["nat"]) if r else None for r in rows],
                "first": {}}
        for pid in PROFILES:
            item["first"][pid] = {}
            for mode in ("nat", "pot"):
                sc = [r["score"][pid][mode] if r else None for r in rows]
                f = None
                for k, s in enumerate(sc):
                    if s is not None and s >= SKIABLE and (k + 1 >= len(sc) or sc[k + 1] is None or sc[k + 1] >= SKIABLE):
                        f = dates[k]
                        break
                item["first"][pid][mode] = f
        models_out.append(item)

    # ---- hourly consensus for the animation & charts ----
    def hourly_stat(getter, nd=1):
        med, lo, hi, cnt = [], [], [], []
        for i in range(start, N):
            vals = [getter(pm, i) for pm in per_model.values()]
            vals = [v for v in vals if v is not None]
            cnt.append(len(vals))
            if vals:
                med.append(r1(median(vals), nd)); lo.append(r1(min(vals), nd)); hi.append(r1(max(vals), nd))
            else:
                med.append(None); lo.append(None); hi.append(None)
        return {"med": med, "lo": lo, "hi": hi}, cnt

    t_h, n_h = hourly_stat(lambda pm, i: pm["T"][i])
    snow_h, _ = hourly_stat(lambda pm, i: pm["snow"][i], 2)
    nat_h, _ = hourly_stat(lambda pm, i: pm["nat"][i])
    pot_h, _ = hourly_stat(lambda pm, i: (pm["nat"][i] + pm["art"][i]) if pm["nat"][i] is not None else None)
    tw_h, _ = hourly_stat(lambda pm, i: pm["tw"][i])
    gust_h, _ = hourly_stat(lambda pm, i: pm["G"][i], 0)
    native_h, _ = hourly_stat(lambda pm, i: pm["native"][i] * 100 if pm["native"][i] is not None else None)
    hourly = {"time": times[start:], "n": n_h, "temp": t_h, "snow": snow_h, "nat": nat_h,
              "pot": pot_h, "tw": tw_h, "gust": gust_h, "native": native_h}

    opening = plan_summary(spot, plan, runs, times, start, N, dates) if plan else None

    return {
        "opening": opening,
        "id": spot["id"], "name": spot["name"], "region": spot["region"], "kind": spot["kind"],
        "lat": spot["lat"], "lon": spot["lon"], "elevation": spot.get("elevation"),
        "initial_snow_cm": r1(init_cm),
        "n_models": len(series),
        "days": days, "models": models_out, "hourly": hourly,
        "first": first, "closest": closest_call,
        "first_flakes": first_flakes, "cold_snap": cold_snap, "cold_hours_total": round(cold_total),
    }



# ----------------------------------------------- extended outlook (weeks) ---
def fetch_outlook(spot):
    """ECMWF seasonal ensemble (50 members): chance of cold weeks, up to ~13 weeks out.
    Skill is low beyond ~3 weeks - treat as a climate tilt, not a forecast."""
    params = {"latitude": spot["lat"], "longitude": spot["lon"],
              "daily": "temperature_2m_mean,temperature_2m_min,snowfall_sum",
              "forecast_days": 92, "timezone": "Europe/Riga"}
    if spot.get("elevation") is not None:
        params["elevation"] = spot["elevation"]
    raw = http_json(params, url=SEASONAL_API)
    dl = raw["daily"]
    times = dl["time"]

    def members(base):
        keys = [k for k in dl if k.startswith(base + "_member")]
        return [dl[k] for k in keys]

    tm, tn, sf = members("temperature_2m_mean"), members("temperature_2m_min"), members("snowfall_sum")
    weeks = []
    for w in range(len(times) // 7):
        a, b = w * 7, w * 7 + 7
        t_week, tmin_week, snow_week = [], [], []
        for mi in range(len(tm)):
            seg = tm[mi][a:b]
            if len(seg) == 7 and None not in seg:
                t_week.append(mean(seg))
                tmin_week.append(mean(tn[mi][a:b]))
                snow_week.append(sum(x or 0 for x in sf[mi][a:b]))
        if not t_week:
            continue
        n = len(t_week)
        ts = sorted(t_week)
        weeks.append({
            "start": times[a], "n": n,
            "tmean": {"med": r1(median(ts)), "lo": r1(ts[int(0.1 * (n - 1))]), "hi": r1(ts[int(0.9 * (n - 1))])},
            "tmin": r1(median(tmin_week)),
            "snow": r1(median(snow_week)),
            "p_freezing": r1(sum(1 for x in t_week if x <= 0) / n, 2),
            "p_cold": r1(sum(1 for x in t_week if x <= -3) / n, 2),
            "p_snow": r1(sum(1 for x in snow_week if x >= 5) / n, 2),
        })
    first_freezing = next((w for w in weeks if w["p_freezing"] >= 0.5), None)
    first_cold = next((w for w in weeks if w["p_cold"] >= 0.5), None)
    return {"weeks": weeks,
            "first_freezing": first_freezing and {"start": first_freezing["start"], "p": first_freezing["p_freezing"]},
            "first_cold": first_cold and {"start": first_cold["start"], "p": first_cold["p_cold"]}}

# ----------------------------------------------------------- briefing ------
def fmt_date(iso):
    d = date.fromisoformat(iso)
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def pct(p):
    return f"{round(p * 100)}%"


def spot_briefing(s, pid):
    """Plain-language summary for one spot and riding style."""
    prof = PROFILES[pid]["label"]
    pot, nat = s["first"][pid]["pot"], s["first"][pid]["nat"]
    lines = []
    if pot:
        head = f"{prof}: skiable from {fmt_date(pot['date'])} ({pct(pot['prob'])} of {pot['n']} models agree)"
        if pot["days"] > 1:
            head += f", staying good for about {pot['days']} days"
    else:
        cc = s["closest"][pid]["pot"]
        head = f"{prof}: no skiable day in the 15-day forecast"
        if cc and cc["cover"] is not None and cc["cover"] >= 5:
            lines.append(f"Closest call is {fmt_date(cc['date'])} (score {cc['score']}/100, "
                         f"{pct(cc['prob'])} of models skiable, cover about {cc['cover']} cm with snowmaking).")
        else:
            lines.append("No day even comes close: there is no snow on the ground and nothing builds up.")
    op = s.get("opening")
    if op:
        lines.extend(opening_lines(s, op, pid))
    if nat and (not pot or nat["date"] <= pot["date"]):
        lines.append(f"Natural snow alone could be enough from {fmt_date(nat['date'])} ({pct(nat['prob'])} of models).")
    elif not nat:
        peak = max((d["nat"]["med"] or 0 for d in s["days"] if d.get("n")), default=0)
        if peak >= 1:
            lines.append(f"Natural snow alone does not get there (peak natural cover about {peak} cm).")
    if s["first_flakes"]:
        f = s["first_flakes"]
        lines.append(f"First meaningful snowfall: {fmt_date(f['date'])} (about {f['cm']} cm, {pct(f['prob'])} of models).")
    else:
        lines.append("No meaningful snowfall is agreed on by the models in this horizon.")
    if s["cold_snap"]:
        c = s["cold_snap"]
        lines.append(f"Snowmaking weather starts {fmt_date(c['date'])} ({c['hours']} h below the wet-bulb limit that day; "
                     f"{s['cold_hours_total']} h across the horizon).")
    else:
        lines.append("No snowmaking-grade cold (wet-bulb under -2 C) is forecast for long enough to build a base.")
    ol = s.get("outlook")
    if ol and (ol.get("first_freezing") or ol.get("first_cold")):
        parts = []
        if ol.get("first_freezing"):
            parts.append(f"freezing weeks become more likely than not from the week of {fmt_date(ol['first_freezing']['start'])} "
                         f"({pct(ol['first_freezing']['p'])} of 50 members)")
        if ol.get("first_cold"):
            parts.append(f"snowmaking-grade cold (week averaging below -3 C) from the week of {fmt_date(ol['first_cold']['start'])} "
                         f"({pct(ol['first_cold']['p'])})")
        lines.append("Weeks ahead (low confidence): " + ", and ".join(parts) + ".")
    elif ol:
        lines.append("Weeks ahead: the seasonal ensemble does not show a sustained cold week in the next 3 months.")
    return {"headline": head, "lines": lines}


def opening_lines(s, op, pid):
    """Briefing lines for a resort with known slope sizes."""
    ms = op["milestones"]
    g = op["guns"]
    first = ms.get("groomed")
    park = ms.get("park")
    out = []
    if first:
        h = first["hours_at"]
        txt = (f"To open, {s['name']} needs about {first['snow_m3']:,} m³ of machine snow for the {first['stage'].lower()} "
               f"(about {first['water_m3']:,} m³ of water). With {g['count']} {g['type']}s that is roughly {h['-5']} hours of "
               f"snowmaking at −5 °C wet-bulb ({h['-3']} h at −3 °C, {h['-10']} h at −10 °C)")
        if park:
            txt += f"; the snowpark brings the total to {park['snow_m3']:,} m³, about {park['hours_at']['-5']} hours at −5 °C"
        out.append(txt + ".")
    m = ms.get(pid) or first
    if m:
        what = m["label"].lower()
        if m["date"]:
            rng = "" if m["earliest"] == m["latest"] else f", models range {fmt_date(m['earliest'])} to {fmt_date(m['latest'])}"
            wk = f" First weekend day: {fmt_date(m['weekend'])}." if m.get("weekend") and m["weekend"] != m["date"] else ""
            out.append(f"Earliest ski day ({what}): {fmt_date(m['date'])}, the day after the guns finish "
                       f"({m['opened_models']} of {m['n']} models get there{rng}).{wk}")
        else:
            got = f"{m['opened_models']} of {m['n']} models" if m["n"] else "no model"
            more = (f" The rest still lack about {m['remaining_m3']:,} m³ at the end of their forecast, "
                    f"about {m['remaining_h5']} more hours at −5 °C wet-bulb.") if m.get("remaining_m3") is not None else ""
            out.append(f"{m['label']}: not within the forecast - only {got} make enough snow in time.{more}")
    return out


def model_note(s):
    ms = [m for m in s["models"] if m["horizon_days"] >= 7 and m["snow14"] is not None]
    if len(ms) < 3:
        return None
    hi = max(ms, key=lambda m: m["snow14"])
    lo = min(ms, key=lambda m: m["snow14"])
    if hi["snow14"] - lo["snow14"] < 1:
        return "All long-range models agree on little or no snowfall."
    return (f"Most snow-friendly: {hi['name']} ({hi['snow14']} cm over its first {min(14, hi['horizon_days'])} days). "
            f"Least: {lo['name']} ({lo['snow14']} cm).")


# -------------------------------------------------------------- main -------
def main():
    cfg = json.loads((ROOT / "config" / "spots.json").read_text(encoding="utf8"))
    spots_out = []
    errors = []
    try:  # last good run, used if a spot fails this time
        prev = {s["id"]: s for s in json.loads((ROOT / "docs" / "data" / "forecast.json").read_text(encoding="utf8"))["spots"]}
    except Exception:
        prev = {}
    for spot in cfg["spots"]:
        params = {
            "latitude": spot["lat"], "longitude": spot["lon"],
            "hourly": ",".join(VARS), "models": ",".join(MODELS),
            "past_days": 2, "forecast_days": 16, "timezone": "Europe/Riga",
            "wind_speed_unit": "kmh", "temperature_unit": "celsius",
        }
        if spot.get("elevation") is not None:
            params["elevation"] = spot["elevation"]
        try:
            raw = http_json(params)
            res = analyse_spot(spot, raw)
            try:
                res["outlook"] = fetch_outlook(spot)
            except Exception as e:  # outlook is a bonus layer - never fail the run for it
                res["outlook"] = None
                errors.append(f"{spot['name']} outlook: {e}")
            res["briefing"] = {pid: spot_briefing(res, pid) for pid in PROFILES}
            res["model_note"] = model_note(res)
            spots_out.append(res)
            print(f"ok   {spot['name']:<12} {res['n_models']} models", flush=True)
        except Exception as e:  # keep going - one bad spot should not kill the run
            if spot["id"] in prev:  # keep the previous analysis rather than dropping the spot
                old = prev[spot["id"]]
                old["stale"] = True
                spots_out.append(old)
                errors.append(f"{spot['name']}: update failed, showing the previous run ({e})")
            else:
                errors.append(f"{spot['name']}: {e}")
            print(f"FAIL {spot['name']}: {e}", file=sys.stderr, flush=True)
        time.sleep(1.0)

    if not spots_out:
        sys.exit("No spot could be analysed - keeping previous data.")

    # national summary (ski spots only)
    ski = [s for s in spots_out if s["kind"] == "ski"]
    summary = {}
    for pid in PROFILES:
        ranked = sorted([s for s in ski if s["first"][pid]["pot"]],
                        key=lambda s: (s["first"][pid]["pot"]["date"], -s["first"][pid]["pot"]["prob"]))
        if ranked:
            b = ranked[0]
            f = b["first"][pid]["pot"]
            summary[pid] = {"best": b["id"], "date": f["date"], "prob": f["prob"],
                            "text": f"Best bet: {b['name']}, from {fmt_date(f['date'])} ({pct(f['prob'])} of {f['n']} models)."}
        else:
            snowy = [s for s in ski if s["first_flakes"]]
            snowy.sort(key=lambda s: s["first_flakes"]["date"])
            extra = (f" First flakes expected around {fmt_date(snowy[0]['first_flakes']['date'])} at {snowy[0]['name']}."
                     if snowy else " The models do not show meaningful snow yet.")
            summary[pid] = {"best": None, "date": None, "prob": None,
                            "text": "No ski day is in sight within the model horizon." + extra}

    # history & trend
    hist_path = ROOT / "docs" / "data" / "history.json"
    try:
        history = json.loads(hist_path.read_text(encoding="utf8"))
    except Exception:
        history = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {"t": now_iso, "first": {s["id"]: {pid: (s["first"][pid]["pot"] or {}).get("date")
                                                  for pid in PROFILES} for s in spots_out}}
    ref = None
    target = datetime.now(timezone.utc) - timedelta(hours=22)
    for h in reversed(history):
        if datetime.strptime(h["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= target:
            ref = h
            break
    for s in spots_out:
        s["trend"] = {}
        for pid in PROFILES:
            cur = (s["first"][pid]["pot"] or {}).get("date")
            old = ref["first"].get(s["id"], {}).get(pid) if ref else None
            if ref is None:
                s["trend"][pid] = {"state": "new"}
            elif cur and old:
                delta = (date.fromisoformat(cur) - date.fromisoformat(old)).days
                s["trend"][pid] = {"state": "same" if delta == 0 else ("later" if delta > 0 else "earlier"),
                                   "days": abs(delta)}
            elif cur and not old:
                s["trend"][pid] = {"state": "appeared"}
            elif old and not cur:
                s["trend"][pid] = {"state": "vanished"}
            else:
                s["trend"][pid] = {"state": "none"}
    history.append(snapshot)
    history = history[-240:]

    out = {
        "generated_at": now_iso,
        "horizon_days": 16,
        "profiles": {k: v["label"] for k, v in PROFILES.items()},
        "assumptions": {
            "skiable_score": SKIABLE, "great_score": GREAT, "marginal_score": MARGINAL,
            "profiles": PROFILES, "snow_gun_cm_per_hour": GUN_CM_H,
            "snowmaking_wetbulb_start_c": GUN_START_AT, "snowmaking_wetbulb_full_c": GUN_FULL_AT,
            "melt_cm_per_degree_hour": MELT_CM_PER_DEGH,
            "fan_gun_curve_m3h": FAN_GUN_CURVE, "natural_snow_pack": NAT_PACK, "snow_per_water": SNOW_PER_WATER,
        },
        "models": {k: v for k, v in MODELS.items()},
        "summary": summary,
        "spots": spots_out,
        "errors": errors,
    }
    data_dir = ROOT / "docs" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "forecast.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    hist_path.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    print(f"wrote forecast.json ({len(spots_out)} spots, {len(errors)} errors)")
    for pid in PROFILES:
        print(f"  [{pid}] {summary[pid]['text']}")


if __name__ == "__main__":
    main()
