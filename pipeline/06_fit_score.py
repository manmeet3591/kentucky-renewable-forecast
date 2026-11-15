"""Fit light statistical corrections on CPU and score on the locked held-out periods.

Hourly (EIA-930 by balancing area), per series (BA x tech) and lead:
  raw      physics only
  scale    y = b * p                         (one factor: fleet derates, curtailment, reporting gaps)
  ridge    y = ridge(p, p^2, p x hour and season harmonics)   (diurnal / seasonal shape errors)
  gbm      small gradient-boosted trees on (p, hour, day of year), depth 3
  X+roll   any of the above times a rolling 30-day level factor (actual / forecast) using only actuals
           available at issue time; tracks fleet changes and EIA-930 reporting changes the plant list misses
Everything is in capacity-normalised units (MW / installed MW in the model), so fleet growth
between 2024 and 2026 is carried by the plant list, not the correction.
The form with the lowest validation RMSE (fit on train only) is refit on train+valid and scored once on test.
Baselines: same-hour persistence k days earlier (k = lead) and hour x month climatology.

Monthly (EIA-923, plant level): a BA x tech factor = sum(923) / sum(day-ahead physics) over the calibration
months, applied to each plant's monthly day-ahead physics in the test months.
Writes data/proc/scores.json and the dashboard data in docs/data/.
"""
import json
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

warnings.filterwarnings("ignore")
PROC = "data/proc"
DOCS = "docs/data"
SPLIT = json.load(open("config/split.json"))
H = SPLIT["hourly_eia930"]
SERIES = [("LGEE", "solar"), ("TVA", "solar"), ("PJM", "solar"), ("MISO", "solar"), ("PJM", "wind"), ("MISO", "wind")]
LEADS = ["d0", "d1", "d2", "d3"]


FORMS = ["raw", "scale", "ridge", "gbm", "scale+roll", "ridge+roll", "gbm+roll"]


def roll_ratio(time, y, f, k, days=30):
    """Level recalibration from the trailing 30 days of actuals that were known at issue time:
    ratio of summed actual to summed forecast, using days that ended at least k+1 days before."""
    dd = pd.DataFrame({"date": time.dt.floor("D").values, "y": y, "f": f}).dropna()
    daily = dd.groupby("date")[["y", "f"]].sum()
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
    r = (daily.y.rolling(days, min_periods=10).sum() / daily.f.rolling(days, min_periods=10).sum()).shift(k + 1)
    r = r.clip(0.2, 3.0).fillna(1.0)
    return r.reindex(time.dt.floor("D").values).fillna(1.0).values


def period(t, name):
    a, b = H[name]
    return (t >= pd.Timestamp(a)) & (t < pd.Timestamp(b) + pd.Timedelta(days=1))


def feats(df, form, tech):
    p = df.p.values
    if form == "gbm":
        return np.c_[p, df.time.dt.hour, df.time.dt.dayofyear]
    h = 2 * np.pi * df.time.dt.hour.values / 24
    d = 2 * np.pi * df.time.dt.dayofyear.values / 365.25
    cols = [p, p ** 2, p * np.sin(h), p * np.cos(h), p * np.sin(2 * h), p * np.cos(2 * h), p * np.sin(d), p * np.cos(d)]
    if tech == "wind":
        cols += [p ** 3, np.ones_like(p)]
    return np.c_[tuple(cols)]


def fit(df, form, tech):
    if form == "raw":
        return None
    if form == "scale":
        return float((df.p * df.y).sum() / (df.p ** 2).sum())
    X = feats(df, form, tech)
    if form == "ridge":
        return Ridge(alpha=1e-3, fit_intercept=False).fit(X, df.y)
    return HistGradientBoostingRegressor(max_depth=3, max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                         random_state=0).fit(X, df.y)


def predict(m, df, form, tech):
    if form == "raw":
        out = df.p.values.copy()
    elif form == "scale":
        out = m * df.p.values
    else:
        out = m.predict(feats(df, form, tech))
    out = np.clip(out, 0, 1)
    if tech == "solar":
        out[df.p.values <= 0] = 0.0
    return out


def metrics(y, f, cap):
    """nMAE / nRMSE / bias in % of installed capacity; MAE in MW."""
    e = f - y
    return {"nmae": float(np.mean(np.abs(e) / cap) * 100), "nrmse": float(np.sqrt(np.mean((e / cap) ** 2)) * 100),
            "bias": float(np.mean(e / cap) * 100), "mae_mw": float(np.mean(np.abs(e))),
            "mean_actual_mw": float(np.mean(y)), "n": int(len(y))}


def main():
    phys = pd.read_parquet(f"{PROC}/phys_ba.parquet")
    act = pd.read_parquet(f"{PROC}/eia930.parquet")
    act["time"] = pd.to_datetime(act.time)
    scores, ts_out, wx_out, chosen = {}, {}, {}, {}
    for ba, tech in SERIES:
        key = f"{ba}_{tech}"
        y = act[act.ba == ba].set_index("time")[tech].rename("y_mw")
        sp = phys[(phys.ba == ba) & (phys.tech == tech)].pivot(index="time", columns="lead", values="mw")
        cap = phys[(phys.ba == ba) & (phys.tech == tech) & (phys.lead == "d1")].set_index("time").cap_mw
        df = pd.concat([sp, cap.rename("cap"), y], axis=1).dropna(subset=["y_mw", "cap", "d1"]).reset_index()
        df = df[df.cap > 0]
        # QC: drop hours where reported output is outside [-2 %, 110 %] of modelled capacity
        df = df[(df.y_mw > -0.02 * df.cap) & (df.y_mw < 1.1 * df.cap)]
        df["y"] = df.y_mw / df.cap
        tr, va, te = period(df.time, "train"), period(df.time, "valid"), period(df.time, "test")
        # daylight mask for solar scoring: sun up somewhere in the fleet per day-0 physics or actual > 0.5 %
        day = (df.d0.fillna(0) > 0) | (df.y > 0.005) if tech == "solar" else pd.Series(True, index=df.index)

        # climatology (hour x month mean of normalised actual) and persistence
        def clim(fit_mask):
            c = df[fit_mask].groupby([df.time.dt.month, df.time.dt.hour]).y.mean()
            return np.array([c.get((m, h), np.nan) for m, h in zip(df.time.dt.month, df.time.dt.hour)])
        yser = df.set_index("time").y_mw
        sc = {"valid": {}, "test": {}}
        best_by_lead = {}
        for lead in LEADS:
            d = df.assign(p=df[lead] / df.cap).dropna(subset=["p"])
            trm, vam, tem = tr.loc[d.index], va.loc[d.index], te.loc[d.index]
            dm = day.loc[d.index].values
            k = int(lead[1]) if lead != "d0" else 1

            def run(form, fit_mask):
                base = form.split("+")[0]
                f = predict(fit(d[fit_mask], base, tech), d, base, tech) * d.cap.values
                if form.endswith("+roll"):
                    f = f * roll_ratio(d.time, d.y_mw.values, f, k)
                return f

            def score(f, mask):
                mk = mask.values & dm
                return metrics(d.y_mw.values[mk], f[mk], d.cap.values[mk])
            # choose form on valid (fit on train)
            res = {form: score(run(form, trm), vam) for form in FORMS}
            sc["valid"][lead] = res
            best = min(FORMS[1:], key=lambda f: res[f]["nrmse"])
            best_by_lead[lead] = best
            # refit on train+valid, score test once
            tv = trm | vam
            f_raw, f_best = run("raw", tv), run(best, tv)
            pers = yser.reindex(d.time - pd.Timedelta(hours=24 * k)).values
            clim_all = pd.Series(clim(tr | va), index=df.index).loc[d.index].values * d.cap.values
            ok = tem & np.isfinite(pers) & np.isfinite(clim_all)  # every method scored on the same hours
            r = {"raw": score(f_raw, ok), "corrected": score(f_best, ok),
                 "persistence": score(pers, ok), "climatology": score(clim_all, ok)}
            r["corrected"]["form"] = best
            for ref in ["persistence", "climatology"]:
                r["corrected"][f"skill_vs_{ref}"] = float(1 - r["corrected"]["nrmse"] / r[ref]["nrmse"])
            sc["test"][lead] = r
            tt = d.time[tem].values
            ts = ts_out.setdefault(key, {})
            ts[lead] = pd.Series(f_best[tem.values], index=tt)
            if lead == "d1":
                ts["raw_d1"] = pd.Series(f_raw[tem.values], index=tt)
                ts["actual"] = pd.Series(d.y_mw.values[tem.values], index=tt)
                ts["cap"] = pd.Series(d.cap.values[tem.values], index=tt)
                ts["clim"] = pd.Series(clim_all[tem.values], index=tt)
        chosen[key] = best_by_lead
        scores[key] = sc
        print(key, {l: (best_by_lead[l], round(sc["test"][l]["corrected"]["nrmse"], 2),
                        round(sc["test"][l]["raw"]["nrmse"], 2), round(sc["test"][l]["persistence"]["nrmse"], 2),
                        round(sc["test"][l]["climatology"]["nrmse"], 2)) for l in LEADS}, flush=True)
    json.dump({"scores": scores, "chosen": chosen}, open(f"{PROC}/scores.json", "w"), indent=1)
    # time series and weather-type breakdowns are exported by 07_export.py from this pickle
    pd.to_pickle(ts_out, f"{PROC}/test_series.pkl")


if __name__ == "__main__":
    main()
