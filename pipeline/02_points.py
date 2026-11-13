"""Group generators into weather points so the Open-Meteo request budget stays small.

Kentucky plants keep their own point (plants within ~5 km share one). Everything else is
grouped per balancing area and technology with capacity-weighted k-means on lat/lon; each
point sits at the capacity-weighted centre of its plants. Writes data/proc/points.csv and
adds a `point` column to generators.parquet.
"""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

PROC = "data/proc"
# clusters per (ba, tech) outside Kentucky; LGEE keeps one point per plant
K = {("LGEE", "solar"): None, ("TVA", "solar"): 12, ("TVA", "wind"): 1, ("PJM", "solar"): 20,
     ("PJM", "wind"): 10, ("MISO", "solar"): 20, ("MISO", "wind"): 15}


def main():
    g = pd.read_parquet(f"{PROC}/generators.parquet")
    g["point"] = ""
    ky = g.in_ky | g.ba.eq("LGEE")
    # Kentucky / LGEE: snap plants to a 0.05 deg grid (about 5 km) and use one point per cell
    cell = (g.lat / 0.05).round().astype(int).astype(str) + "_" + (g.lon / 0.05).round().astype(int).astype(str)
    g.loc[ky, "point"] = "KY_" + g.loc[ky, "tech"].str[0] + "_" + cell[ky]
    for (ba, tech), k in K.items():
        m = ~ky & g.ba.eq(ba) & g.tech.eq(tech)
        if not m.any():
            continue
        X = np.c_[g.loc[m, "lat"], g.loc[m, "lon"] * np.cos(np.radians(38))]
        k = min(k or len(X), len(X))
        lab = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X, sample_weight=g.loc[m, "mw_ac"]).labels_
        g.loc[m, "point"] = [f"{ba}_{tech[0]}_{i:02d}" for i in lab]
    pts = g.groupby("point").apply(lambda d: pd.Series({
        "lat": np.average(d.lat, weights=d.mw_ac), "lon": np.average(d.lon, weights=d.mw_ac),
        "tech": d.tech.iloc[0], "ba": d.ba.mode().iloc[0], "mw": d.mw_ac.sum(), "n_gen": len(d),
        "near_ky": bool(d.near_ky.any() or d.ba.isin(["LGEE", "TVA"]).any())}), include_groups=False).reset_index()
    pts["lat"] = pts.lat.round(3)
    pts["lon"] = pts.lon.round(3)
    pts.to_csv(f"{PROC}/points.csv", index=False)
    g.to_parquet(f"{PROC}/generators.parquet")
    print(pts.groupby(["ba", "tech"]).size(), len(pts), "points;", pts.near_ky.sum(), "near KY")


if __name__ == "__main__":
    main()
