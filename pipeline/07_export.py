"""Monthly EIA-923 plant scoring, error breakdowns and dashboard data (docs/data/*.json).

EIA-923 scoring: a BA x tech factor = sum(923 MWh) / sum(day-ahead physics MWh) over the calibration
months is applied to each plant's monthly day-ahead (d1) physics in the test months. Only plant-months
reported by monthly respondents are used (annual respondents' monthly values are EIA allocations), and a
plant's first month in service is skipped (partial month). Baseline: same calendar month one year earlier
for the same plant (plants in service for that whole month only).

Weather types (test period, by day, from the *actual* EIA-930 output):
  solar  clear / partly cloudy / overcast: daily energy / 31-day rolling max of daily energy >= 0.8, 0.5-0.8, < 0.5
  wind   low / moderate / high output: daily mean capacity factor terciles; ramp days: top decile of the
         largest 3-hour change in output that day
"""
import glob
import json
import os
from datetime import date

import numpy as np
import pandas as pd

PROC = "data/proc"
DOCS = "docs/data"
SPLIT = json.load(open("config/split.json"))
NAMES = {"LGEE": "LG&E and KU", "TVA": "Tennessee Valley Authority", "PJM": "PJM Interconnection", "MISO": "MISO"}


def r(x, n=2):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def ym(s):
    y, m = s.split("-")
    return int(y) * 12 + int(m) - 1


def eia923(g):
    pm = pd.read_parquet(f"{PROC}/phys_plant_month.parquet")
    pm = pm[pm.lead == "d1"]
    act = pd.read_parquet(f"{PROC}/plant_monthly.parquet")
    act = act.groupby(["plant_id", "year", "month", "freq"], as_index=False).mwh.sum()
    monthly_resp = set(act[(act.year == 2025) & (act.freq == "M")].plant_id)
    act = act[(act.freq == "M") | ((act.freq == "YTD") & act.plant_id.isin(monthly_resp))]
    pl = g.groupby("plant_id").agg(ba=("ba", "first"), tech=("tech", "first"), first_ym=("on_ym", "min"),
                                   last_on=("on_ym", "max"), mw=("mw_ac", "sum"))
    d = pm.merge(act, on=["plant_id", "year", "month"], suffixes=("_f", "_a")).merge(pl, left_on="plant_id", right_index=True)
    d = d.rename(columns={"tech_x": "tech"}) if "tech_x" in d else d
    d["ym"] = d.year * 12 + d.month - 1
    d = d[(d.ym > d.last_on) & (d.mwh_f > 0) & (d.mwh_a > 0)]  # skip partial first months and units added mid-plant
    c0, c1 = ym(SPLIT["monthly_eia923"]["calibrate"][0]), ym(SPLIT["monthly_eia923"]["calibrate"][1])
    t0, t1 = ym(SPLIT["monthly_eia923"]["test"][0]), ym(SPLIT["monthly_eia923"]["test"][1])
    cal = d[(d.ym >= c0) & (d.ym <= c1)]
    fac = cal.groupby(["ba", "tech"]).apply(lambda x: x.mwh_a.sum() / x.mwh_f.sum(), include_groups=False)
    te = d[(d.ym >= t0) & (d.ym <= t1)].copy()
    te["fac"] = [fac.get((b, t), 1.0) for b, t in zip(te.ba, te.tech)]
    te["corr"] = te.mwh_f * te.fac
    hrs = pd.to_datetime(dict(year=te.year, month=te.month, day=1)).dt.days_in_month * 24
    te["cap_mwh"] = te.mw * hrs.values
    # baseline: same plant, same month last year (scaled by any capacity change is not attempted)
    last = act.set_index(["plant_id", "year", "month"]).mwh
    te["ly"] = [last.get((p, y - 1, m), np.nan) for p, y, m in zip(te.plant_id, te.year, te.month)]
    te.loc[te.ym - 12 <= te.last_on, "ly"] = np.nan

    def summ(x):
        out = {"n_plant_months": int(len(x)), "n_plants": int(x.plant_id.nunique())}
        for k in ["mwh_f", "corr", "ly"]:
            v = x.dropna(subset=[k]) if k == "ly" else x
            ape = (v[k] - v.mwh_a).abs() / v.mwh_a * 100
            out[k] = {"median_ape": r(ape.median(), 1), "cf_mae": r(((v[k] - v.mwh_a).abs() / v.cap_mwh).mean() * 100, 2),
                      "total_bias": r((v[k].sum() / v.mwh_a.sum() - 1) * 100, 1), "n": int(len(v))}
        return out
    summary = {"all": summ(te)}
    for t in ["solar", "wind"]:
        summary[t] = summ(te[te.tech == t])
    near = te.plant_id.isin(g[g.near_ky].plant_id)
    summary["near_ky_solar"] = summ(te[near & (te.tech == "solar")])
    summary["near_ky_wind"] = summ(te[near & (te.tech == "wind")])
    summary["ky"] = summ(te[te.plant_id.isin(g[g.in_ky].plant_id)])
    summary["factors"] = {f"{b}_{t}": r(v, 3) for (b, t), v in fac.items()}
    per_plant = te.groupby("plant_id").agg(a=("mwh_a", "sum"), f=("corr", "sum"), n=("ym", "size"))
    ape = ((te["corr"] - te.mwh_a).abs() / te.mwh_a * 100).groupby(te.plant_id).median()
    per_plant["ape"] = ape
    # monthly series for KY plants (for the plant panel)
    allm = pm.merge(act, on=["plant_id", "year", "month"], how="outer", suffixes=("_f", "_a"))
    allm = allm.merge(pl[["ba", "tech"]], left_on="plant_id", right_index=True, how="left")
    allm = allm.rename(columns={"tech_y": "tech"}) if "tech_y" in allm else allm
    allm["fac"] = [fac.get((b, t), np.nan) for b, t in zip(allm.ba, allm.tech)]
    allm["corr"] = allm.mwh_f * allm.fac
    return summary, per_plant, allm, te


def weather_types(ts):
    out = {}
    for key, s in ts.items():
        tech = key.split("_")[1]
        df = pd.DataFrame({k: s[k] for k in ["actual", "cap", "d1", "d2", "d3", "raw_d1", "clim"] if k in s})
        df.index = pd.to_datetime(df.index)
        day = df.index.floor("D")
        cfd = (df.actual / df.cap).groupby(day).mean()
        if tech == "solar":
            k = cfd / cfd.rolling(31, center=True, min_periods=10).max()
            cls = pd.cut(k, [-1, 0.5, 0.8, 9], labels=["overcast", "partly cloudy", "clear"]).astype(str)
        else:
            cls = pd.qcut(cfd, 3, labels=["low output", "moderate output", "high output"]).astype(str)
            ramp = df.actual.diff(3).abs().groupby(day).max() / df.cap.groupby(day).mean()
            cls = cls.where(ramp < ramp.quantile(0.9), "ramp day")
        season = pd.Series(day.month, index=df.index).map({12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring",
                                                           5: "spring", 6: "summer", 7: "summer", 8: "summer",
                                                           9: "autumn", 10: "autumn", 11: "autumn"})
        df["cls"] = cls.reindex(day).values
        df["season"] = season.values
        if tech == "solar":
            df = df[(df.clim > 0.005 * df.cap) | (df.actual > 0.005 * df.cap)]
        res = {}
        for col in ["cls", "season"]:
            res[col] = {}
            for c, x in df.groupby(col):
                res[col][c] = {"days": int(x.index.floor("D").nunique()),
                               **{l: r((x[l] - x.actual).abs().div(x.cap).mean() * 100, 2) for l in ["d1", "d2", "d3", "raw_d1", "clim"]}}
        out[key] = res
    return out


def lead_diag():
    """GHI error of each lead/model vs HRRR lead day 0 at the Kentucky solar points (daylight hours)."""
    rows = []
    for f in sorted(glob.glob("data/raw/weather/KY_s_*__gfs_seamless__*.json")):
        point, _, start = os.path.basename(f)[:-5].split("__")
        a = pd.DataFrame(json.load(open(f))["hourly"]).set_index("time")
        fg = f.replace("gfs_seamless", "gfs_global")
        if os.path.exists(fg):
            b = pd.DataFrame(json.load(open(fg))["hourly"]).set_index("time")
            a["gfs_d1"] = b["shortwave_radiation_previous_day1"]
        rows.append(a)
    if not rows:
        return {}
    a = pd.concat(rows)
    a = a[a.shortwave_radiation > 20]
    ref = a.shortwave_radiation
    out = {}
    for name, col in [("HRRR d1", "shortwave_radiation_previous_day1"), ("GFS d1", "gfs_d1"),
                      ("GFS d2", "shortwave_radiation_previous_day2"), ("GFS d3", "shortwave_radiation_previous_day3")]:
        if col in a:
            e = (a[col] - ref).dropna()
            out[name] = {"rmse": r(np.sqrt((e ** 2).mean()), 1), "mae": r(e.abs().mean(), 1), "bias": r(e.mean(), 1), "n": int(len(e))}
    return out


def main():
    os.makedirs(DOCS, exist_ok=True)
    g = pd.read_parquet(f"{PROC}/generators.parquet")
    pts = pd.read_csv(f"{PROC}/points.csv")
    sc = json.load(open(f"{PROC}/scores.json"))
    ts = pd.read_pickle(f"{PROC}/test_series.pkl")
    s923, per_plant, allm, te = eia923(g)

    # ---- plants ----
    now = date.today().year * 12 + date.today().month - 1
    plants = []
    for pid, x in g.groupby("plant_id"):
        live = x[(x.on_ym <= now) & ~(x.ret_ym <= now)]
        if live.empty:
            continue
        w = live.mw_ac.values
        p = {"id": int(pid), "name": str(x.plant.iloc[0]), "op": str(x.operator.iloc[0]), "st": x.state.iloc[0],
             "ba": x.ba.iloc[0], "tech": x.tech.iloc[0], "lat": r(np.average(live.lat, weights=w), 4),
             "lon": r(np.average(live.lon, weights=w), 4), "mw": r(live.mw_ac.sum(), 1),
             "on": f"{int(live.on_ym.min()) // 12}-{int(live.on_ym.min()) % 12 + 1:02d}",
             "ky": bool(x.in_ky.iloc[0]), "near": bool(x.near_ky.iloc[0]), "pt": x.point.iloc[0]}
        if p["tech"] == "solar":
            p["mount"] = live.mount.mode().iloc[0]
            dcac = live.dc_ac.dropna()
            p["dcac"] = r(dcac.mean(), 2) if len(dcac) else None
        else:
            p["hub"] = r(live.hub_m.mean(), 0)
            mdl = (live.turbine_mfr.astype(str) + " " + live.turbine_model.astype(str)).replace("nan nan", None).dropna()
            p["turb"] = mdl.mode().iloc[0] if len(mdl) else None
        if pid in per_plant.index:
            q = per_plant.loc[pid]
            p["t923"] = {"a": r(q.a, 0), "f": r(q.f, 0), "n": int(q.n), "ape": r(q.ape, 1)}
        plants.append(p)
    # monthly history for plants near Kentucky (actual 923 vs calibrated day-ahead physics)
    hist = {}
    near_ids = set(g[g.near_ky | g.in_ky].plant_id)
    for pid, x in allm[allm.plant_id.isin(near_ids)].groupby("plant_id"):
        x = x.sort_values(["year", "month"])
        hist[int(pid)] = [[f"{int(y)}-{int(m):02d}", r(a, 0), r(f, 0)] for y, m, a, f in zip(x.year, x.month, x.mwh_a, x["corr"])]
    json.dump({"plants": plants, "hist": hist}, open(f"{DOCS}/plants.json", "w"), separators=(",", ":"))

    # ---- hourly test series (one file per series) ----
    for key, s in ts.items():
        t = pd.to_datetime(s["actual"].index)
        out = {"t0": t[0].strftime("%Y-%m-%dT%H:00Z"), "n": len(t),
               "hours": ((t - t[0]) / pd.Timedelta(hours=1)).astype(int).tolist()}
        for k in ["actual", "d1", "d2", "d3", "raw_d1", "clim", "cap"]:
            v = s[k].reindex(s["actual"].index)
            out[k] = [None if not np.isfinite(x) else int(round(x)) for x in v.values]
        json.dump(out, open(f"{DOCS}/ts_{key}.json", "w"), separators=(",", ":"))

    # ---- capacity by BA x tech (now) and over time ----
    cap_now = g[(g.on_ym <= now) & ~(g.ret_ym <= now)].groupby(["ba", "tech"]).mw_ac.sum()
    meta = {
        "built": date.today().isoformat(), "split": SPLIT, "names": NAMES,
        "series": [{"key": k, "ba": k.split("_")[0], "tech": k.split("_")[1],
                    "cap_now": r(cap_now.get(tuple(k.split("_")), 0), 0)} for k in sc["scores"]],
        "scores": sc["scores"], "chosen": sc["chosen"], "eia923": s923, "weather_types": weather_types(ts),
        "lead_diag": lead_diag(),
        "fleet": {"n_plants": int(g.plant_id.nunique()), "n_points": int(len(pts)),
                  "by": [{"ba": b, "tech": t, "mw": r(v, 0), "n": int(g[(g.ba == b) & (g.tech == t)].plant_id.nunique())}
                         for (b, t), v in cap_now.items()],
                  "ky": {t: r(g[g.in_ky & (g.tech == t)].mw_ac.sum(), 0) for t in ["solar", "wind"]},
                  "near_wind": r(g[g.near_ky & (g.tech == "wind")].mw_ac.sum(), 0)},
        "points": pts[["point", "lat", "lon", "tech", "ba", "mw", "n_gen"]].to_dict("records"),
    }
    json.dump(meta, open(f"{DOCS}/meta.json", "w"), separators=(",", ":"))
    print(json.dumps(s923, indent=1)[:1500])
    print(json.dumps(meta["lead_diag"], indent=1))


if __name__ == "__main__":
    main()
