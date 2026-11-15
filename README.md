# Kentucky Renewable Forecast

Prototype 1–3 day solar and wind generation forecasts for the balancing areas that serve Kentucky
(LG&E-KU, TVA, PJM, MISO), built for the Disaster Science Operations Center (DSOC).
Physics (pvlib, turbine power curves) runs on archived NOAA HRRR/GFS forecasts, a light statistical
correction is fitted on CPU, and the result is scored against EIA-930 hourly and EIA-923 monthly data
on held-out 2026 periods that were locked before fitting.

Dashboard: `docs/` (static, GitHub Pages ready). Open `docs/index.html` through any static server:

```bash
python3 -m http.server -d docs 8771
```

## Pipeline

Environment: `mamba create -n ky-renew python=3.11 pvlib pandas numpy scipy scikit-learn requests openpyxl pyarrow statsmodels`

| Step | Script | What it does |
|---|---|---|
| 0 | (manual download, see below) | EIA-860 2025, EIA-860M Sept 2026, EIA-923 2024–2026, EIA-930 six-month balance files → `data/raw/` |
| 1 | `pipeline/01_plants.py` | Solar/wind generators ≥ 1 MW in LGEE, TVA, PJM, MISO with 860 detail (tracking, tilt, DC/AC, hub height, turbine); EIA-923 plant-months |
| 2 | `pipeline/02_points.py` | Group generators into weather points (Kentucky plants keep their own; elsewhere capacity-weighted k-means) |
| 3 | `pipeline/03_fetch_weather.py` | Open-Meteo Previous Runs API, `gfs_seamless` (HRRR < 48 h, GFS beyond), leads day 0–3, paced and cached |
| 4 | `pipeline/04_actuals.py` | EIA-930 hourly solar and wind by area |
| 5 | `pipeline/05_physics.py [model]` | Per-generator physics → area-hourly and plant-monthly totals; `model` selects the weather files (e.g. `aircast`) |
| 6 | `pipeline/06_fit_score.py` | Choose correction on validation, refit, score once on test; baselines persistence and climatology |
| 7 | `pipeline/07_export.py` | EIA-923 monthly scoring, weather-type and HRRR-vs-GFS breakdowns, dashboard JSON |

The split is in `config/split.json` and was committed before any fitting.

Raw downloads (EIA blocks some clients; use a browser user agent):
- `https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip`
- `https://www.eia.gov/electricity/data/eia860m/xls/september_generator2026.xlsx` → `data/raw/eia860m.xlsx`
- `https://www.eia.gov/electricity/data/eia923/archive/xls/f923_2024.zip`, `.../eia923/xls/f923_2025.zip`, `.../eia923/xls/f923_2026.zip`
- `https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_<YYYY>_<Jan_Jun|Jul_Dec>.csv`

Open-Meteo's free tier is limited per IP (about 5,000 weighted calls per hour and 10,000 per day). The
fetcher spaces out its requests, caches each point and half-year chunk, and can be re-run to resume.

## Aircast SR hook

`05_physics.py` only needs hourly GHI, 2 m temperature and 80 m wind per point and lead. Write DSOC
Aircast SR (1 km / 1 h) samples to `data/raw/weather/<point>__aircast__<start>.json` using Open-Meteo
variable names, then run `python pipeline/05_physics.py aircast`. See `tacc/` for SLURM templates. No
neural model is trained locally.

## Limits

See the dashboard's *Method & limits* tab. In short: EIA-930 area series do not match the EIA-860
plant list one for one (LGEE most of all), curtailment is not modelled, wind uses a generic turbine
curve, plants outside Kentucky share weather points, and the forecasts are point forecasts only.
