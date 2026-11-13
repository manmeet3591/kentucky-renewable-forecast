"""Fetch archived NWP forecasts at each weather point from the Open-Meteo Previous Runs API.

Feed `gfs_seamless` = NCEP HRRR (3 km) for leads under 48 h and GFS (0.25 deg) beyond, so
  lead day 0 (0-23 h)  HRRR      -> stands in for "observed" weather when diagnosing model error
  lead day 1 (24-47 h) HRRR      -> day-ahead forecast
  lead day 2 (48-71 h) GFS
  lead day 3 (72-95 h) GFS
For Kentucky points we also pull pure GFS at lead day 1 (`gfs_global`) so the day1 -> day2
jump can be split into "longer lead" and "HRRR -> GFS".

The free API limit is per IP and shared with other projects on this machine, so requests are
paced, cached per point and half-year chunk, back off on the minute limit and stop on the daily one.
Solar points: shortwave_radiation (GHI, W/m2, preceding-hour mean) + temperature_2m.
Wind points: wind_speed_80m + temperature_2m.
"""
import json
import os
import sys
import time

import pandas as pd
import requests

PROC = "data/proc"
OUT = "data/raw/weather"
URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
START, END = "2024-07-01", "2026-10-31"
LEADS = ["", "_previous_day1", "_previous_day2", "_previous_day3"]
BASE = {"solar": ["shortwave_radiation", "temperature_2m"], "wind": ["wind_speed_80m", "temperature_2m"]}
PACE_S = 12  # one ~13-unit chunk every 12 s keeps us near 65 weighted calls/min, ~3,900/h


def chunks():
    s = pd.Timestamp(START)
    while s <= pd.Timestamp(END):
        e = min(s + pd.offsets.MonthEnd(6), pd.Timestamp(END))
        yield s.date().isoformat(), e.date().isoformat()
        s = e + pd.Timedelta(days=1)


def get(params):
    for attempt in range(30):
        try:
            r = requests.get(URL, params=params, timeout=120)
        except requests.RequestException as e:
            print("net error", e, flush=True)
            time.sleep(30)
            continue
        if r.status_code == 200:
            return r.json()
        reason = r.json().get("reason", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        if "Daily" in reason:
            sys.exit(f"daily limit reached; rerun later to resume ({reason})")
        if "Hourly" in reason:
            print("hourly limit; sleeping 10 min", flush=True)
            time.sleep(600)
        elif "Minutely" in reason:
            time.sleep(65)
        else:
            print("error", r.status_code, reason, flush=True)
            time.sleep(20)
    raise RuntimeError("gave up")


def main():
    os.makedirs(OUT, exist_ok=True)
    pts = pd.read_csv(f"{PROC}/points.csv")
    jobs = []
    for p in pts.itertuples():
        hourly = [v + l for v in BASE[p.tech] for l in LEADS]
        jobs.append((p.point, "gfs_seamless", hourly, p))
        if p.point.startswith("KY_"):  # pure-GFS comparison only at Kentucky points (budget)
            jobs.append((p.point, "gfs_global", [v + "_previous_day1" for v in BASE[p.tech]], p))
    n = 0
    for s, e in chunks():
        for point, model, hourly, p in jobs:
            f = f"{OUT}/{point}__{model}__{s}.json"
            if os.path.exists(f):
                continue
            d = get({"latitude": p.lat, "longitude": p.lon, "hourly": ",".join(hourly), "models": model,
                     "start_date": s, "end_date": e, "timezone": "GMT", "wind_speed_unit": "ms"})
            json.dump({"hourly": d["hourly"], "elevation": d.get("elevation"), "lat": d.get("latitude"),
                       "lon": d.get("longitude")}, open(f, "w"))
            n += 1
            if n % 20 == 0:
                print(time.strftime("%H:%M"), n, "chunks fetched", s, point, flush=True)
            time.sleep(PACE_S)
    print("done", n, "new chunks")


if __name__ == "__main__":
    main()
