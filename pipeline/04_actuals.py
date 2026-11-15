"""EIA-930 hourly solar and wind net generation for LGEE, TVA, PJM and MISO.

EIA split solar and wind into "with" / "without integrated battery storage" from mid-2024;
we sum the two. EIA's "(Adjusted)" columns (anomalies cleaned) are used where present.
Time index is UTC hour-ending, which matches Open-Meteo's preceding-hour-mean convention.
Writes data/proc/eia930.parquet with columns time, ba, solar, wind (MW).
"""
import glob
import pandas as pd

BAS = ["LGEE", "TVA", "PJM", "MISO"]


def fuel_cols(cols, fuel):
    raw = [c for c in cols if c.startswith(f"Net Generation (MW) from {fuel}") and not c.endswith(")")]
    adj = [c for c in cols if c.startswith(f"Net Generation (MW) from {fuel}") and c.endswith("(Adjusted)")]
    return adj or raw


def main():
    out = []
    for f in sorted(glob.glob("data/raw/EIA930_BALANCE_*.csv")):
        d = pd.read_csv(f, thousands=",", low_memory=False)
        d = d[d["Balancing Authority"].isin(BAS)]
        t = pd.to_datetime(d["UTC Time at End of Hour"], format="mixed")
        r = pd.DataFrame({"time": t.values, "ba": d["Balancing Authority"].values})
        for fuel in ["Solar", "Wind"]:
            cs = fuel_cols(list(d.columns), fuel)
            v = d[cs].apply(pd.to_numeric, errors="coerce")
            r[fuel.lower()] = v.sum(axis=1, min_count=1).values
            print(f.split("/")[-1], fuel, cs)
        out.append(r)
    a = pd.concat(out).drop_duplicates(["time", "ba"]).sort_values(["ba", "time"]).reset_index(drop=True)
    a.to_parquet("data/proc/eia930.parquet")
    a["m"] = a.time.dt.to_period("Q")
    print(a.groupby(["ba", "m"])[["solar", "wind"]].mean().round(0).unstack(0).to_string())


if __name__ == "__main__":
    main()
