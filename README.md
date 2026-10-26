# Snow watch Latvia

An agent that studies 11 weather models for Latvian ski hills, works out when each hill becomes skiable, and shows it on an animated dashboard. It costs nothing to host and refreshes itself every 3 hours.

```
GitHub Actions (every 3 h)                GitHub Pages (free)
  agent/snow_agent.py  ──►  docs/data/forecast.json  ──►  docs/index.html (the dashboard)
  asks Open-Meteo for 11 models              committed to the repo,           reads the JSON,
  + a 50-member seasonal ensemble            so history is kept               animates the hill
```

## What the agent does each run

1. Pulls hourly forecasts for each spot in `config/spots.json` from 11 models: ECMWF IFS, ECMWF AIFS (AI), NOAA GFS, DWD ICON, Canada GEM, Météo-France, UK Met Office, MET Norway, DMI HARMONIE, KNMI HARMONIE and JMA.
2. Runs every model through the same simple snowpack: natural snow builds, settles and melts, and a "snow guns" layer builds machine snow whenever the wet-bulb temperature allows.
3. Scores every day 0 to 100 for your riding style (freestyle / park needs a deeper base than groomed runs) and counts how many models agree.
4. Finds the first skiable window, how it compares with yesterday, and what the 13-week seasonal ensemble says about cold weeks.
5. Writes `docs/data/forecast.json` and a short plain-language briefing per spot.

## Put it online for free (about 5 minutes)

1. Create a new GitHub repository (public is simplest) and upload everything in this folder, including the hidden `.github` folder.
2. In the repository go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Open the **Actions** tab, pick **Update snow forecast**, and press **Run workflow**. (It also runs by itself on every push and every 3 hours.)
4. After about a minute your site is live at `https://<your-user>.github.io/<repo-name>/`.

Cost: nothing. A run takes about a minute, which is far inside GitHub's free allowance. Open-Meteo's free tier allows 10,000 calls a day for non-commercial use, and this uses about 130.

If the repository sees no activity for 60 days GitHub pauses scheduled workflows. The data commits count as activity, but if the dashboard ever shows "the hourly job may have paused", press **Run workflow** again.

## Run it on your own computer

```
python agent/snow_agent.py        # needs Python 3.9+, nothing to install
python -m http.server -d docs     # then open http://localhost:8000
```

## Make it yours

- **Ski hills:** edit `config/spots.json`. Coordinates are approximate, and `elevation` (metres, mid-slope) makes temperatures match the hill rather than the valley.
- **What counts as skiable:** the `PROFILES`, `SKIABLE` and snow-gun constants at the top of `agent/snow_agent.py`. The defaults assume freestyle / park needs 20 cm to score at all and 45 cm for full marks.
- **Look and feel:** `docs/style.css` (colours are variables at the top) and `docs/scene.js` (the animated hill).

## Honest limits

- Models are not weather stations. Latvian hills are small, and a 9 to 25 km model grid cannot see them. Treat dates as "when the weather allows", then check the resort's own page for lift status.
- The machine-snow layer assumes the resort runs its guns whenever it is cold enough. Switch "Count machine snow" off for natural snow only.
- Beyond about 10 days models disagree a lot, which is why every verdict shows how many models agree. The weeks-ahead chart has little skill past three weeks.

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
