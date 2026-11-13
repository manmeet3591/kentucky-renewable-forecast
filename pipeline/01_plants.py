"""Build the solar and wind plant table for LGEE, TVA, PJM and MISO.

Master list: EIA-860M (Sept 2026) operating + retired generators, which carries location,
balancing authority, nameplate and in-service month for plants added up to 2026.
Detail: EIA-860 2025 annual solar (tracking, tilt, azimuth, DC capacity, bifacial) and
wind (hub height, turbine model, design wind speed) schedules, joined on plant + generator.
Monthly generation: EIA-923 2024, 2025 final and 2026 year to date (Schedules 2-5, page 1).

Writes data/proc/generators.parquet (one row per generator, with in-service and retirement
month so fleet capacity can be rebuilt for any hour) and data/proc/plant_monthly.parquet.
"""
import glob
import numpy as np
import pandas as pd

RAW = "data/raw"
PROC = "data/proc"
BAS = ["LGEE", "TVA", "PJM", "MISO"]
# Kentucky and the states that border it: "near Kentucky" on the map and in the plant table.
NEAR = {"KY", "IN", "OH", "WV", "VA", "TN", "MO", "IL"}


def read_860m(sheet):
    d = pd.read_excel(f"{RAW}/eia860m.xlsx", sheet_name=sheet, header=2)
    d = d[pd.to_numeric(d["Plant ID"], errors="coerce").notna()].copy()
    d["Plant ID"] = d["Plant ID"].astype(int)
    d["Generator ID"] = d["Generator ID"].astype(str).str.strip()
    return d


def main():
    op = read_860m("Operating")
    ret = read_860m("Retired")
    op["ret_ym"] = np.nan
    ret = ret[pd.to_numeric(ret["Retirement Year"], errors="coerce") >= 2024].copy()
    ret["ret_ym"] = ret["Retirement Year"].astype(int) * 12 + ret["Retirement Month"].astype(int) - 1
    g = pd.concat([op, ret], ignore_index=True)
    g = g[g["Energy Source Code"].isin(["SUN", "WND"]) & g["Balancing Authority Code"].isin(BAS)]
    g = g[g["Technology"].isin(["Solar Photovoltaic", "Onshore Wind Turbine", "Offshore Wind Turbine"])]
    g = g.rename(columns={"Plant ID": "plant_id", "Generator ID": "gen_id", "Plant Name": "plant",
                          "Plant State": "state", "Balancing Authority Code": "ba",
                          "Nameplate Capacity (MW)": "mw_ac", "DC Net Capacity (MW)": "mw_dc_m",
                          "Latitude": "lat", "Longitude": "lon", "Entity Name": "operator"})
    g["tech"] = np.where(g["Energy Source Code"] == "SUN", "solar", "wind")
    g["on_ym"] = g["Operating Year"].astype(int) * 12 + g["Operating Month"].astype(int) - 1
    for c in ["mw_ac", "mw_dc_m", "lat", "lon"]:
        g[c] = pd.to_numeric(g[c], errors="coerce")
    g = g[g["mw_ac"] >= 1.0].dropna(subset=["lat", "lon"])

    # ---- annual 860 detail ----
    sol = pd.concat([pd.read_excel(f"{RAW}/e860/3_3_Solar_Y2025.xlsx", sheet_name=s, header=1)
                     for s in ["Operable", "Retired and Canceled"]])
    sol = sol[pd.to_numeric(sol["Plant Code"], errors="coerce").notna()]
    sol = pd.DataFrame({
        "plant_id": sol["Plant Code"].astype(int), "gen_id": sol["Generator ID"].astype(str).str.strip(),
        "single_axis": sol["Single-Axis Tracking?"].eq("Y"), "dual_axis": sol["Dual-Axis Tracking?"].eq("Y"),
        "fixed": sol["Fixed Tilt?"].eq("Y"), "bifacial": sol["Bifacial?"].eq("Y"),
        "tilt": pd.to_numeric(sol["Tilt Angle"], errors="coerce"),
        "azimuth": pd.to_numeric(sol["Azimuth Angle"], errors="coerce"),
        "mw_dc": pd.to_numeric(sol["DC Net Capacity (MW)"], errors="coerce"),
    }).drop_duplicates(["plant_id", "gen_id"])
    wnd = pd.concat([pd.read_excel(f"{RAW}/e860/3_2_Wind_Y2025.xlsx", sheet_name=s, header=1)
                     for s in ["Operable", "Retired and Canceled"]])
    wnd = wnd[pd.to_numeric(wnd["Plant Code"], errors="coerce").notna()]
    wnd = pd.DataFrame({
        "plant_id": wnd["Plant Code"].astype(int), "gen_id": wnd["Generator ID"].astype(str).str.strip(),
        "hub_ft": pd.to_numeric(wnd["Turbine Hub Height (Feet)"], errors="coerce"),
        "turbine_mfr": wnd["Predominant Turbine Manufacturer"].astype(str).str.strip(),
        "turbine_model": wnd["Predominant Turbine Model Number"].astype(str).str.strip(),
        "n_turbines": pd.to_numeric(wnd["Number of Turbines"], errors="coerce"),
        "design_ws_mph": pd.to_numeric(wnd["Design Wind Speed (mph)"], errors="coerce"),
        "wind_class": pd.to_numeric(wnd["Wind Quality Class"], errors="coerce"),
    }).drop_duplicates(["plant_id", "gen_id"])
    g = g.merge(sol, on=["plant_id", "gen_id"], how="left").merge(wnd, on=["plant_id", "gen_id"], how="left")

    # tracking: 860 annual flags; plants new in 2026 (only in 860M) default to single-axis,
    # which is what nearly all new utility PV in these regions uses.
    g["detail_from_860"] = g["single_axis"].notna() | g["hub_ft"].notna()
    g["mount"] = np.select([g["dual_axis"].eq(True), g["single_axis"].eq(True), g["fixed"].eq(True)],
                           ["dual", "single_axis", "fixed"], default="single_axis_assumed")
    g.loc[g.tech == "wind", "mount"] = None
    g["mw_dc"] = g["mw_dc"].fillna(g["mw_dc_m"])
    g["dc_ac"] = (g["mw_dc"] / g["mw_ac"]).where(lambda s: s.between(0.9, 1.8))
    g["hub_m"] = (g["hub_ft"] * 0.3048).where(lambda s: s.between(40, 160))
    g["near_ky"] = g["state"].isin(NEAR)
    g["in_ky"] = g["state"].eq("KY")
    keep = ["plant_id", "gen_id", "plant", "operator", "state", "ba", "tech", "lat", "lon", "mw_ac", "mw_dc",
            "dc_ac", "on_ym", "ret_ym", "mount", "bifacial", "tilt", "azimuth", "hub_m", "turbine_mfr",
            "turbine_model", "n_turbines", "design_ws_mph", "wind_class", "near_ky", "in_ky", "detail_from_860"]
    g = g[keep].reset_index(drop=True)
    g.to_parquet(f"{PROC}/generators.parquet")

    # ---- EIA-923 monthly plant generation (SUN / WND rows) ----
    rows = []
    for f in sorted(glob.glob(f"{RAW}/e923_*/EIA923_Schedules_2_3_4_5_M_*.xlsx")):
        d = pd.read_excel(f, sheet_name=0, header=5)
        d.columns = [str(c).replace("\n", " ") for c in d.columns]
        if "Respondent Frequency" not in d:  # year-to-date files: annual respondents are EIA estimates
            d["Respondent Frequency"] = "YTD"
        d = d[d["Reported Fuel Type Code"].isin(["SUN", "WND"])]
        d = d[d["Plant Id"].isin(g.plant_id.unique())]
        months = [c for c in d.columns if c.startswith("Netgen ")]
        m = d.melt(id_vars=["Plant Id", "YEAR", "Reported Fuel Type Code", "Respondent Frequency"],
                   value_vars=months, var_name="m", value_name="mwh")
        m["month"] = m["m"].str.replace("Netgen ", "").map(
            {n: i + 1 for i, n in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                             "August", "September", "October", "November", "December"])})
        m["mwh"] = pd.to_numeric(m["mwh"], errors="coerce")
        rows.append(m)
    mm = pd.concat(rows).dropna(subset=["mwh"])
    mm = mm.groupby(["Plant Id", "YEAR", "month", "Reported Fuel Type Code", "Respondent Frequency"],
                    as_index=False)["mwh"].sum()
    mm.columns = ["plant_id", "year", "month", "fuel", "freq", "mwh"]
    mm = mm[mm.year.isin([2024, 2025, 2026])]
    mm.to_parquet(f"{PROC}/plant_monthly.parquet")

    p = g.groupby(["ba", "tech"]).agg(n=("plant_id", "nunique"), mw=("mw_ac", "sum"))
    print(p)
    print(g[g.near_ky].groupby(["state", "tech"]).agg(n=("plant_id", "nunique"), mw=("mw_ac", "sum")))
    print("923 rows", len(mm), mm.groupby(["year", "freq"]).size())


if __name__ == "__main__":
    main()
