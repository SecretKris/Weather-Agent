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

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(res, indent=1), encoding="utf8")
print("probe written")
