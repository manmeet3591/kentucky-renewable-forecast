"""Physics-based solar and wind power from archived forecasts, per generator, summed to
balancing-area totals (hourly) and plant-month totals.

Solar (pvlib): forecast GHI (preceding-hour mean) -> sun position at mid-hour -> Erbs split into
DNI / DHI -> plane-of-array irradiance (Perez) for the plant's mount: single-axis tracker
(N-S axis, +/-55 deg, backtracking, GCR 0.35), fixed tilt (EIA-860 tilt/azimuth, default 25 deg south),
or dual-axis -> SAPM cell temperature (open rack, 1 m/s wind) -> PVWatts DC
(-0.35 %/K, 14 % system losses) -> inverter clipping at AC nameplate (96 % efficiency).
DC/AC ratio from EIA-860 DC capacity, 1.3 where missing.

Wind: forecast 80 m wind -> hub height by power law (alpha 0.14; EIA-860 hub height, 80 m where
missing) -> air-density adjustment from forecast temperature and site elevation -> generic
normalised turbine curve (cut-in 3 m/s, rated at EIA-860 design wind speed, 11.5 m/s default,
cut-out 25 m/s), smoothed with a 1.5 m/s Gaussian to stand in for spread across the farm and within
the hour -> 10 % wake and availability loss.

Each generator counts only from its in-service month (and until retirement). Output:
  data/proc/phys_ba.parquet     time, ba, tech, lead, mw, cap_mw (installed in the model)
  data/proc/phys_plant_month.parquet  plant_id, tech, lead, year, month, mwh
"""
import glob
import json
import sys

import numpy as np
import pandas as pd
import pvlib

# Weather source: per-point JSON files data/raw/weather/<point>__<MODEL>__<start>.json in Open-Meteo layout.
# Default is the archived HRRR/GFS feed; pass e.g. `aircast` to run the same physics on DSOC Aircast SR fields.
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gfs_seamless"
SUFFIX = "" if MODEL == "gfs_seamless" else f"_{MODEL}"
PROC = "data/proc"
WX = "data/raw/weather"
LEADS = {"d0": "", "d1": "_previous_day1", "d2": "_previous_day2", "d3": "_previous_day3"}
GAMMA, LOSS, INV_EFF = -0.0035, 0.14, 0.96


def load_point(point, model=MODEL):
    parts = []
    elev = None
    for f in sorted(glob.glob(f"{WX}/{point}__{model}__*.json")):
        d = json.load(open(f))
        elev = d.get("elevation", elev)
        h = pd.DataFrame(d["hourly"])
        h["time"] = pd.to_datetime(h.time)
        parts.append(h.set_index("time"))
    if not parts:
        return None, None
    return pd.concat(parts).sort_index(), elev or 200.0


def active_mask(times, on_ym, ret_ym):
    ym = times.year * 12 + times.month - 1
    m = ym >= on_ym
    if not np.isnan(ret_ym):
        m &= ym < ret_ym
    return np.asarray(m)


# ---------------- solar ----------------
def solar_unit_dc(times, lat, lon, elev, ghi, tair, mount, tilt, azimuth):
    """DC power per MW_dc (MW/MW) for one mount configuration."""
    tmid = times - pd.Timedelta(minutes=30)
    sp = pvlib.solarposition.get_solarposition(tmid, lat, lon, altitude=elev)
    ghi = np.clip(np.nan_to_num(ghi), 0, 1300)
    erbs = pvlib.irradiance.erbs(ghi, sp.zenith.values, tmid)
    dni, dhi = np.nan_to_num(erbs["dni"]), np.nan_to_num(erbs["dhi"])
    dni_extra = pvlib.irradiance.get_extra_radiation(tmid).values
    if mount.startswith("single_axis"):
        tr = pvlib.tracking.singleaxis(sp.apparent_zenith, sp.azimuth, axis_tilt=0, axis_azimuth=180,
                                       max_angle=55, backtrack=True, gcr=0.35)
        stilt, saz = tr.surface_tilt.fillna(0).values, tr.surface_azimuth.fillna(180).values
    elif mount == "dual":
        stilt, saz = sp.apparent_zenith.clip(0, 90).values, sp.azimuth.values
    else:
        stilt = np.full(len(times), tilt if np.isfinite(tilt) and 0 <= tilt <= 60 else 25.0)
        saz = np.full(len(times), azimuth + 180 if np.isfinite(azimuth) and -90 <= azimuth <= 90 else 180.0)
    poa = pvlib.irradiance.get_total_irradiance(stilt, saz, sp.apparent_zenith.values, sp.azimuth.values,
                                                dni, ghi, dhi, dni_extra=dni_extra, model="perez",
                                                airmass=pvlib.atmosphere.get_relative_airmass(sp.apparent_zenith.values))
    poa_g = np.nan_to_num(np.asarray(poa["poa_global"]))
    p = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_polymer"]
    tcell = pvlib.temperature.sapm_cell(poa_g, np.nan_to_num(tair, nan=15.0), 1.0, **p)
    dc = poa_g / 1000.0 * (1 + GAMMA * (tcell - 25.0)) * (1 - LOSS)
    dc[sp.zenith.values >= 90] = 0.0
    return np.clip(dc, 0, None)


# ---------------- wind ----------------
def wind_curve(v, rated):
    vi, vo = 3.0, 25.0
    p = np.clip((v ** 3 - vi ** 3) / (rated ** 3 - vi ** 3), 0, 1)
    p[v < vi] = 0
    p[v >= vo] = 0
    return p


def wind_unit(ws80, tair, elev, hub, rated):
    """Power per MW nameplate."""
    v = np.nan_to_num(ws80) * (hub / 80.0) ** 0.14
    # air density from temperature and a standard-atmosphere pressure at site elevation
    pres = 101325 * (1 - 2.25577e-5 * (elev + hub)) ** 5.25588
    rho = pres / (287.05 * (np.nan_to_num(tair, nan=15.0) + 273.15))
    v = v * (rho / 1.225) ** (1 / 3)
    # Gaussian smoothing of the curve (sigma 1.5 m/s) by quadrature
    nodes, w = np.polynomial.hermite_e.hermegauss(9)
    w = w / w.sum()
    p = sum(wi * wind_curve(np.clip(v + 1.5 * ni, 0, None), rated) for ni, wi in zip(nodes, w))
    return p * 0.90


def main():
    g = pd.read_parquet(f"{PROC}/generators.parquet")
    ba_rows, pm_rows = [], []
    for point, gp in g.groupby("point"):
        wx, elev = load_point(point)
        if wx is None:
            print("no weather for", point)
            continue
        times = wx.index
        tech = gp.tech.iloc[0]
        lat = np.average(gp.lat, weights=gp.mw_ac)
        lon = np.average(gp.lon, weights=gp.mw_ac)
        for lead, suf in LEADS.items():
            out = np.zeros((len(gp), len(times)))
            if tech == "solar":
                ghi, ta = wx[f"shortwave_radiation{suf}"].values, wx[f"temperature_2m{suf}"].values
                cache = {}
                for i, r in enumerate(gp.itertuples()):
                    key = (r.mount, r.tilt if r.mount == "fixed" else None, r.azimuth if r.mount == "fixed" else None)
                    if key not in cache:
                        cache[key] = solar_unit_dc(times, lat, lon, elev, ghi, ta, r.mount, r.tilt, r.azimuth)
                    dc_ac = r.dc_ac if np.isfinite(r.dc_ac) else 1.3
                    ac = np.minimum(cache[key] * dc_ac * r.mw_ac * INV_EFF, r.mw_ac)
                    out[i] = ac * active_mask(times, r.on_ym, r.ret_ym)
            else:
                ws, ta = wx[f"wind_speed_80m{suf}"].values, wx[f"temperature_2m{suf}"].values
                for i, r in enumerate(gp.itertuples()):
                    hub = r.hub_m if np.isfinite(r.hub_m) else 80.0
                    rated = r.design_ws_mph * 0.44704 if np.isfinite(r.design_ws_mph) and 20 <= r.design_ws_mph <= 36 else 11.5
                    out[i] = wind_unit(ws, ta, elev, hub, rated) * r.mw_ac * active_mask(times, r.on_ym, r.ret_ym)
            # missing forecast hours stay NaN at the BA level
            miss = np.isnan(wx[f"{'shortwave_radiation' if tech == 'solar' else 'wind_speed_80m'}{suf}"].values)
            out[:, miss] = np.nan
            cap = np.array([r.mw_ac * active_mask(times, r.on_ym, r.ret_ym) for r in gp.itertuples()])
            for ba, idx in gp.reset_index(drop=True).groupby("ba").groups.items():
                ba_rows.append(pd.DataFrame({"time": times, "ba": ba, "tech": tech, "lead": lead, "point": point,
                                             "mw": out[idx].sum(0), "cap_mw": cap[idx].sum(0)}))
            pdf = pd.DataFrame(out.T, index=times, columns=gp.plant_id.values).T.groupby(level=0).sum(min_count=1).T
            mon = pdf.resample("MS").agg(lambda s: s.sum() if s.notna().mean() > 0.9 else np.nan)
            mon = mon * (mon.index.days_in_month.values[:, None] * 24 / pdf.resample("MS").count().values.clip(1))
            mon = mon.stack().rename("mwh").reset_index()
            mon.columns = ["month_start", "plant_id", "mwh"]
            mon["tech"], mon["lead"] = tech, lead
            pm_rows.append(mon)
        print(point, tech, len(gp), "gens", flush=True)
    pb = pd.concat(ba_rows)
    pb["gap"] = pb.mw.isna()
    pb = pb.groupby(["time", "ba", "tech", "lead"], as_index=False).agg(mw=("mw", "sum"), cap_mw=("cap_mw", "sum"), gap=("gap", "any"))
    pb.loc[pb.gap, "mw"] = np.nan  # a BA hour is missing if any of its points is missing
    pb = pb.drop(columns="gap")
    pb.to_parquet(f"{PROC}/phys_ba{SUFFIX}.parquet")
    pm = pd.concat(pm_rows)
    pm["year"], pm["month"] = pm.month_start.dt.year, pm.month_start.dt.month
    pm.drop(columns="month_start").to_parquet(f"{PROC}/phys_plant_month{SUFFIX}.parquet")
    print(pb.groupby(["ba", "tech", "lead"]).mw.mean().unstack().round(0))


if __name__ == "__main__":
    main()
