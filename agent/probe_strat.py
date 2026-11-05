#!/usr/bin/env python3
"""Temporary probe: which models/levels does Open-Meteo really serve for the stratosphere?
Writes probe/result.json. Deleted once the stratosphere module is built."""
import json, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "probe" / "result.json"

def get(url, params, tries=3):
    q = urllib.parse.urlencode(params, safe=",")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "latvia-snow-agent/1.0 (personal, non-commercial)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf8','ignore')[:200]}"
            if e.code == 429: time.sleep(20 * (i + 1)); continue
            break
        except OSError as e:
            last = str(e); time.sleep(4)
    return None, last

res = {"forecast": {}, "ensemble": {}, "notes": []}
LEVELS = [10, 20, 30, 50, 70, 100]
MODELS = ["ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless", "gfs025", "icon_seamless", "gem_seamless",
          "ukmo_seamless", "jma_seamless", "meteofrance_seamless"]
for m in MODELS:
    vars_ = [f"{v}_{l}hPa" for l in LEVELS for v in ("wind_speed", "wind_direction", "temperature", "geopotential_height")]
    d, err = get("https://api.open-meteo.com/v1/forecast", {"latitude": 60, "longitude": 0, "hourly": ",".join(vars_),
                 "models": m, "forecast_days": 16, "timezone": "GMT"})
    if err or d.get("error"):
        res["forecast"][m] = {"error": err or d.get("reason")}
    else:
        h = d["hourly"]; per = {}
        for l in LEVELS:
            cnt = {}
            for v in ("wind_speed", "wind_direction", "temperature", "geopotential_height"):
                arr = h.get(f"{v}_{l}hPa_{m}") or h.get(f"{v}_{l}hPa") or []
                cnt[v] = sum(x is not None for x in arr)
            per[str(l)] = cnt
        res["forecast"][m] = {"hours_total": len(h["time"]), "nonnull": per}
    time.sleep(3)

ENS = ["ecmwf_ifs025", "ecmwf_aifs025", "gfs025", "gfs05", "icon_global", "gem_global", "ukmo_global_ensemble_20km"]
for m in ENS:
    d, err = get("https://ensemble-api.open-meteo.com/v1/ensemble", {"latitude": 60, "longitude": 0,
                 "hourly": "wind_speed_50hPa,wind_speed_10hPa,wind_speed_30hPa", "models": m, "forecast_days": 16, "timezone": "GMT"})
    if err or d.get("error"):
        res["ensemble"][m] = {"error": err or d.get("reason")}
    else:
        h = d["hourly"]; info = {}
        for key in ("wind_speed_50hPa", "wind_speed_30hPa", "wind_speed_10hPa"):
            members = [k for k in h if k.startswith(key)]
            nn = {k: sum(x is not None for x in h[k]) for k in members[:3]}
            info[key] = {"series": len(members), "sample_nonnull": nn}
        info["hours_total"] = len(h["time"])
        res["ensemble"][m] = info
    time.sleep(3)

# --- pole + multi-location + cost ---
def get_h(url, params):
    q = urllib.parse.urlencode(params, safe=",")
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "latvia-snow-agent/1.0 (personal, non-commercial)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r), dict(r.headers)
res["extra"] = {}
for name, lat in (("pole90", 90), ("pole89.75", 89.75), ("lat80", 80)):
    try:
        d, hd = get_h("https://api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": 0, "hourly": "temperature_10hPa,geopotential_height_10hPa",
                      "models": "gfs_seamless", "forecast_days": 16, "timezone": "GMT"})
        arr = d["hourly"].get("temperature_10hPa_gfs_seamless") or d["hourly"].get("temperature_10hPa") or []
        res["extra"][name] = {"nonnull": sum(x is not None for x in arr), "first": arr[:3], "cost": hd.get("X-RateLimit-Cost") or hd.get("x-ratelimit-cost")}
    except Exception as e:
        res["extra"][name] = {"error": str(e)[:200]}
    time.sleep(3)
try:
    lons = ",".join(str(x) for x in range(0, 360, 15))
    d, hd = get_h("https://api.open-meteo.com/v1/forecast", {"latitude": ",".join(["60"] * 24), "longitude": lons,
                  "hourly": "wind_speed_10hPa,wind_direction_10hPa,temperature_10hPa,geopotential_height_10hPa,wind_speed_50hPa,wind_direction_50hPa,temperature_50hPa,geopotential_height_50hPa,wind_speed_100hPa,wind_direction_100hPa,temperature_100hPa,geopotential_height_100hPa",
                  "models": "gfs_seamless", "forecast_days": 16, "timezone": "GMT"})
    res["extra"]["ring24_gfs"] = {"type": type(d).__name__, "n": len(d) if isinstance(d, list) else 1,
        "keys": sorted(d[0]["hourly"].keys())[:14] if isinstance(d, list) else sorted(d["hourly"].keys())[:14],
        "cost": hd.get("X-RateLimit-Cost") or hd.get("x-ratelimit-cost"),
        "lon_first_last": [d[0]["longitude"], d[-1]["longitude"], d[0]["latitude"]] if isinstance(d, list) else None}
except Exception as e:
    res["extra"]["ring24_gfs"] = {"error": str(e)[:300]}
time.sleep(3)
try:
    d, hd = get_h("https://api.open-meteo.com/v1/forecast", {"latitude": ",".join(["60"] * 24), "longitude": lons,
                  "hourly": "wind_speed_50hPa,wind_direction_50hPa,temperature_50hPa,geopotential_height_50hPa,wind_speed_100hPa,wind_direction_100hPa,temperature_100hPa",
                  "models": "ecmwf_ifs025,ecmwf_aifs025_single,gfs_seamless", "forecast_days": 16, "timezone": "GMT"})
    res["extra"]["ring24_3models"] = {"n": len(d) if isinstance(d, list) else 1, "cost": hd.get("X-RateLimit-Cost") or hd.get("x-ratelimit-cost"),
        "keys": sorted(d[0]["hourly"].keys())[:25]}
except Exception as e:
    res["extra"]["ring24_3models"] = {"error": str(e)[:300]}

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(res, indent=1), encoding="utf8")
print("probe written")
