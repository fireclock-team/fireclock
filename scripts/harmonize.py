#!/usr/bin/env python3
"""
FireClock - step 2: harmonize MODIS + VIIRS into one fire record, validate it,
and test whether the satellites' observation clock biases the counts.

Written with AI assistance (Claude, Anthropic) during NASA Space Apps 2026.

Input : data/agg/{region}_daily.csv   (from fetch_firms.py)
Output: out/{region}/*.json, *.csv    (consumed by the web app)

Scale: "VIIRS-equivalent detections" = what ONE VIIRS satellite (S-NPP scale)
would have counted. VIIRS is the future of the record (MODIS ends 2027).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

CLEAN_YEARS = list(range(2013, 2021))   # S-NPP full years AND Terra/Aqua orbits still controlled
DRIFT_YEARS = list(range(2022, 2027))   # Aqua drifting later, Terra earlier
BASELINE = (2003, 2022)                 # "normal" for anomaly
RNG = np.random.default_rng(42)


# ---------------------------------------------------------------- loading
def sat_code(row):
    f, s = row["family"], str(row["satellite"]).strip().lower()
    if f == "MODIS":
        return "Terra" if s.startswith("t") else "Aqua"
    return {"VIIRS_SNPP": "SNPP", "VIIRS_NOAA20": "NOAA20", "VIIRS_NOAA21": "NOAA21"}[f]


def load(region, agg="data/agg"):
    d = pd.read_csv(os.path.join(agg, f"{region}_daily.csv"), parse_dates=["acq_date"])
    d["sat"] = d.apply(sat_code, axis=1)
    d["year"] = d.acq_date.dt.year
    d["month"] = d.acq_date.dt.month
    return d


def monthly(d, daynight=None):
    x = d if daynight is None else d[d.daynight == daynight]
    m = x.groupby(["sat", "year", "month"]).n.sum().unstack("sat").fillna(0.0)
    # months a satellite was not flying must be NaN, not 0
    first = x.groupby("sat").acq_date.min()
    last = x.groupby("sat").acq_date.max()
    idx = pd.to_datetime([f"{y}-{mo:02d}-15" for y, mo in m.index])
    for s in m.columns:
        m.loc[(idx < first[s] - pd.Timedelta(days=15)) | (idx > last[s] + pd.Timedelta(days=15)), s] = np.nan
    return m


# ---------------------------------------------------------- ratio model
def fit_ratio(m, src, ref, years, tau):
    """Per-calendar-month exchange rate ref/src, shrunk toward the pooled rate by tau (in detections)."""
    sub = m.loc[m.index.get_level_values(0).isin(years), [src, ref]].dropna()
    pooled = sub[ref].sum() / max(sub[src].sum(), 1e-9)
    g = sub.groupby(level=1).sum()
    k = {}
    for mo in range(1, 13):
        s_ = g[src].get(mo, 0.0)
        r_ = g[ref].get(mo, 0.0)
        k[mo] = (r_ + tau * pooled) / (s_ + tau)
    return k, pooled


def predict(m, src, k):
    mo = m.index.get_level_values(1)
    return m[src].values * np.array([k[x] for x in mo])


def loyo(m, src, ref, years, tau):
    """Leave-one-year-out: predict ref from src for each held-out year."""
    rows = []
    for y in years:
        k, _ = fit_ratio(m, src, ref, [v for v in years if v != y], tau)
        sub = m.loc[[y]].dropna(subset=[src, ref])
        if not len(sub):
            continue
        p = predict(sub, src, k)
        rows.append(pd.DataFrame({"year": y, "month": sub.index.get_level_values(1),
                                  "obs": sub[ref].values, "pred": p}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def scores(df):
    if df.empty:
        return {}
    o, p = df.obs.values, df.pred.values
    season = df.groupby("year")[["obs", "pred"]].sum()
    big = o >= np.percentile(o, 50)  # judge on months that matter
    return {
        "months": int(len(df)),
        "monthly_median_abs_pct_err_busy_months": float(np.median(np.abs(p[big] - o[big]) / np.maximum(o[big], 1)) * 100),
        "annual_median_abs_pct_err": float(np.median(np.abs(season.pred - season.obs) / np.maximum(season.obs, 1)) * 100),
        "r2_log_monthly": float(1 - np.sum((np.log1p(p) - np.log1p(o)) ** 2) / np.sum((np.log1p(o) - np.log1p(o).mean()) ** 2)),
    }


def choose_tau(m, src, ref, years):
    best = None
    for tau in [0, 5, 20, 50, 100, 200, 500, 1000, 5000]:
        df = loyo(m, src, ref, years, tau)
        if df.empty:
            continue
        err = np.mean((np.log1p(df.pred) - np.log1p(df.obs)) ** 2)
        if best is None or err < best[1]:
            best = (tau, err)
    return best[0] if best else 100


def bootstrap_k(m, src, ref, years, tau, B=500):
    ks = []
    for _ in range(B):
        ys = list(RNG.choice(years, size=len(years), replace=True))
        sub = pd.concat([m.loc[[y]] for y in ys if y in m.index.get_level_values(0)])
        if sub.empty:
            continue
        sub.index = pd.MultiIndex.from_arrays([np.arange(len(sub)) // 12 + 1, sub.index.get_level_values(1)])
        k, _ = fit_ratio(sub, src, ref, list(sub.index.get_level_values(0).unique()), tau)
        ks.append([k[i] for i in range(1, 13)])
    return np.array(ks)  # B x 12


# ------------------------------------------------------------ harmonize
def harmonize(d):
    m = monthly(d)
    out = {"method": {}, "validation": {}}
    have = set(m.columns)

    # 1) VIIRS reference series on S-NPP scale; bridge NOAA-20/21 (S-NPP ended Nov 2026)
    bridges = {}
    for s in ["NOAA20", "NOAA21"]:
        if s in have and "SNPP" in have:
            ov = m[[s, "SNPP"]].dropna()
            ov = ov[(ov[s] > 0) | (ov["SNPP"] > 0)]
            if len(ov) >= 6:
                bridges[s] = float(ov["SNPP"].sum() / max(ov[s].sum(), 1e-9))
    out["method"]["viirs_bridge_to_snpp_scale"] = bridges
    v_cols = [c for c in ["SNPP", "NOAA20", "NOAA21"] if c in have]
    scaled = pd.DataFrame({c: m[c] * (bridges.get(c, 1.0)) for c in v_cols})
    viirs = scaled.mean(axis=1, skipna=True)        # average of available VIIRS, single-satellite scale

    # 2) MODIS -> VIIRS exchange rate, calibrated ONLY on clean years
    m2 = m.copy()
    m2["VIIRS"] = viirs
    if "Terra" in have or "Aqua" in have:
        m2["MODIS"] = m2[[c for c in ["Terra", "Aqua"] if c in have]].sum(axis=1, min_count=1)
        have = have | {"MODIS"}
    res = {}
    for src in ["Aqua", "Terra", "MODIS"]:
        if src not in have:
            continue
        tau = choose_tau(m2, src, "VIIRS", CLEAN_YEARS)
        k, pooled = fit_ratio(m2, src, "VIIRS", CLEAN_YEARS, tau)
        kb = bootstrap_k(m2, src, "VIIRS", CLEAN_YEARS, tau)
        cv = loyo(m2, src, "VIIRS", CLEAN_YEARS, tau)
        # naive approach most tools use: calibrate on ALL overlap years incl. drift years
        all_years = sorted(set(m2.dropna(subset=[src, "VIIRS"]).index.get_level_values(0)))
        k_naive, pooled_naive = fit_ratio(m2, src, "VIIRS", all_years, tau)
        res[src] = dict(tau=tau, k=k, pooled=pooled, kb=kb, cv=cv, k_naive=k_naive, pooled_naive=pooled_naive)
        out["method"][f"{src}_tau"] = tau
        out["method"][f"{src}_rate_by_month"] = {str(i): round(k[i], 3) for i in k}
        out["method"][f"{src}_rate_pooled_clean"] = round(pooled, 3)
        out["method"][f"{src}_rate_pooled_naive_all_years"] = round(pooled_naive, 3)
        out["validation"][f"{src}_to_VIIRS_leave_one_year_out_{CLEAN_YEARS[0]}_{CLEAN_YEARS[-1]}"] = scores(cv)

    # 3) exchange rate by season-year: shows the orbit-drift break
    yr = m2.groupby(level=0).sum(min_count=1)
    drift = {}
    for src in [x for x in res if x != "MODIS"]:
        r = (yr["VIIRS"] / yr[src]).replace([np.inf], np.nan)
        drift[src] = {int(y): (None if pd.isna(v) else round(float(v), 3)) for y, v in r.items() if y >= 2012}
    out["exchange_rate_by_year"] = drift

    # 4) assemble one record: VIIRS where it exists, else Aqua*k, else Terra*k
    idx = m2.index
    rec = pd.DataFrame(index=idx)
    rec["value"] = np.nan
    rec["lo"] = np.nan
    rec["hi"] = np.nan
    rec["source"] = ""
    vmask = rec.index.get_level_values(0) >= 2012
    vmask &= m2["VIIRS"].notna().values
    rec.loc[vmask, "value"] = m2.loc[vmask, "VIIRS"]
    rec.loc[vmask, "lo"] = m2.loc[vmask, "VIIRS"] - 1.96 * np.sqrt(m2.loc[vmask, "VIIRS"])
    rec.loc[vmask, "hi"] = m2.loc[vmask, "VIIRS"] + 1.96 * np.sqrt(m2.loc[vmask, "VIIRS"])
    rec.loc[vmask, "source"] = "VIIRS (observed)"
    for src in ["Aqua", "Terra"]:
        if src not in res:
            continue
        need = rec["value"].isna() & m2[src].notna()
        if not need.any():
            continue
        mo = rec.index[need].get_level_values(1)
        base = m2.loc[need, src].values
        k = np.array([res[src]["k"][x] for x in mo])
        kb = res[src]["kb"][:, mo - 1] if len(res[src]["kb"]) else np.tile(k, (2, 1))
        sims = kb * RNG.poisson(np.maximum(base, 0)[None, :].repeat(len(kb), 0))
        rec.loc[need, "value"] = base * k
        rec.loc[need, "lo"] = np.percentile(sims, 5, axis=0)
        rec.loc[need, "hi"] = np.percentile(sims, 95, axis=0)
        rec.loc[need, "source"] = f"{src} MODIS x exchange rate"
    rec["lo"] = rec["lo"].clip(lower=0)
    rec = rec.dropna(subset=["value"]).reset_index()
    rec.columns = ["year", "month", "value", "lo", "hi", "source"]

    # raw (un-harmonized) comparison: the "fake jump" a naive join shows
    raw = m.sum(axis=1, min_count=1).reset_index()
    raw.columns = ["year", "month", "raw_all_sensors"]
    rec = rec.merge(raw, on=["year", "month"], how="left")

    # anomaly vs baseline median per calendar month
    base = rec[(rec.year >= BASELINE[0]) & (rec.year <= BASELINE[1])].groupby("month").value
    med = base.median()
    q25 = base.quantile(0.25)
    q75 = base.quantile(0.75)
    rec["baseline_median"] = rec.month.map(med)
    rec["anomaly_pct"] = (rec.value - rec.baseline_median) / rec.baseline_median.replace(0, np.nan) * 100
    rec["class"] = np.where(rec.value > rec.month.map(q75), "above normal",
                            np.where(rec.value < rec.month.map(q25), "below normal", "normal"))

    # the fake 2012 jump, before vs after harmonization
    def jump(col):
        a = rec[(rec.year >= 2009) & (rec.year <= 2011)][col].sum() / 3
        b = rec[(rec.year >= 2013) & (rec.year <= 2015)][col].sum() / 3
        return round(float(b / a), 2) if a else None
    out["fake_2012_jump_ratio"] = {"raw_naive_join": jump("raw_all_sensors"), "harmonized": jump("value")}
    return rec, out, res, m2


# --------------------------------------------------------- clock analysis
def clock(d, season_months=(10, 11)):
    """Is the count biased by WHEN the satellite looks? Within-day design: same day,
    same weather, same fields, two VIIRS satellites ~50 min apart."""
    x = d[(d.daynight == "D") & d.month.isin(season_months)]
    out = {}

    # pass time (local solar) per satellite per year
    pt = x.groupby(["sat", "year"]).apply(
        lambda g: np.average(g.solar_hour_med, weights=g.n), include_groups=False).unstack("sat")
    out["pass_solar_hour_by_year"] = {s: {int(y): round(float(v), 2) for y, v in pt[s].dropna().items()}
                                      for s in pt.columns}

    # within-day pairs
    piv_n = x.pivot_table(index="acq_date", columns="sat", values="n", aggfunc="sum")
    piv_t = x.pivot_table(index="acq_date", columns="sat", values="solar_hour_med", aggfunc="median")
    has_pix = "pix_area_med" in x.columns and x["pix_area_med"].notna().any()
    piv_p = x.pivot_table(index="acq_date", columns="sat", values="pix_area_med", aggfunc="median") if has_pix else None
    pairs = []
    for a, b in [("SNPP", "NOAA20"), ("SNPP", "NOAA21"), ("NOAA20", "NOAA21"), ("SNPP", "Aqua")]:
        if a not in piv_n or b not in piv_n:
            continue
        df = pd.DataFrame({"na": piv_n[a], "nb": piv_n[b], "ta": piv_t[a], "tb": piv_t[b]})
        if has_pix and a in piv_p and b in piv_p:
            df["dpix"] = np.log(piv_p[b] / piv_p[a])      # bigger pixels off-nadir -> fewer detections
        else:
            df["dpix"] = 0.0
        df = df.dropna()
        df = df[(df.na >= 5) & (df.nb >= 5)]
        if len(df) < 20:
            continue
        df["dt_h"] = df.tb - df.ta                     # hours later that b looked
        df["lr"] = np.log(df.nb / df.na)               # log ratio of counts
        df["year"] = df.index.year
        df["pair"] = f"{b} vs {a}"
        pairs.append(df.reset_index())
    if not pairs:
        return out
    P = pd.concat(pairs, ignore_index=True)

    # slope: % change in detections per hour later the satellite looks, by period
    def slope(g):
        if len(g) < 15 or g.dt_h.std() < 0.05:
            return None
        X = np.c_[np.ones(len(g)), g.dt_h.values]
        if g.dpix.std() > 1e-6 and abs(np.corrcoef(g.dpix, g.dt_h)[0, 1]) < 0.9:
            X = np.c_[X, g.dpix.values]                  # control for viewing angle
        beta, *_ = np.linalg.lstsq(X, g.lr.values, rcond=None)
        # bootstrap CI over days
        bs = []
        for _ in range(400):
            i = RNG.integers(0, len(g), len(g))
            bb, *_ = np.linalg.lstsq(X[i], g.lr.values[i], rcond=None)
            bs.append(bb[1])
        to_pct = lambda b: (np.exp(b) - 1) * 100
        return {"n_days": int(len(g)), "pct_more_per_hour_later": round(float(to_pct(beta[1])), 1),
                "ci90": [round(float(to_pct(np.percentile(bs, 5))), 1), round(float(to_pct(np.percentile(bs, 95))), 1)]}

    P["period"] = np.where(P.year <= 2021, "2018-2021", "2022-now")
    res = {}
    for (pair, per), g in P.groupby(["pair", "period"]):
        s = slope(g)
        if s:
            res.setdefault(pair, {})[per] = s
    # headline: VIIRS vs VIIRS only (identical instrument, only the clock differs), pair fixed effects
    V = P[~P.pair.str.contains("Aqua")].copy()
    pooled = {}
    for per, g in V.groupby("period"):
        g = g.copy()
        for col in ["lr", "dt_h", "dpix"]:
            g[col] = g[col] - g.groupby("pair")[col].transform("mean")
        s = slope(g)
        if s:
            pooled[per] = s
    out["later_look_effect"] = res
    out["later_look_effect_pooled"] = pooled
    out["controls"] = "same day + same area (weather, season, fields fixed); pixel size (viewing angle) included as a covariate"
    out["_pairs"] = P[["acq_date", "pair", "dt_h", "lr", "year"]].assign(
        acq_date=lambda z: z.acq_date.dt.strftime("%Y-%m-%d")).round(4).to_dict("records")
    return out


# ------------------------------------------------- Aqua as an accidental probe
def aqua_probe(d, ph):
    """Aqua's orbit drifted later (2022->). Its daytime count relative to the fixed ~1:15 pm VIIRS
    pass, by year, traces how much more fire burns later in the afternoon. A season with no
    afternoon burning (placebo) must stay flat: that also rules out orbit-altitude / pixel artifacts."""
    out = {}
    base_hour = np.mean([ph["Aqua"][str(y)] for y in range(2013, 2021) if str(y) in ph.get("Aqua", {})]) if "Aqua" in ph else None
    for name, months in [("oct_nov", (10, 11)), ("apr_may", (4, 5))]:
        x = d[(d.daynight == "D") & d.month.isin(months)]
        g = x.groupby(["year", "sat"]).n.sum().unstack()
        vc = [c for c in ["SNPP", "NOAA20", "NOAA21"] if c in g.columns]
        if "Aqua" not in g.columns or not vc:
            continue
        r = g["Aqua"] / g[vc].mean(axis=1, skipna=True)
        base = r.loc[2013:2020].mean()
        rows = []
        for y in r.index:
            if y < 2012 or pd.isna(r[y]):
                continue
            # season-specific Aqua look time if available
            xa = x[(x.sat == "Aqua") & (x.year == y)]
            hr = float(np.average(xa.solar_hour_med, weights=xa.n)) if len(xa) else None
            rows.append({"year": int(y), "aqua_hour": None if hr is None else round(hr, 2),
                         "multiplier": round(float(r[y] / base), 3)})
        out[name] = rows
    out["baseline_years"] = "2013-2020"
    out["base_aqua_hour"] = None if base_hour is None else round(float(base_hour), 2)
    return out


# ------------------------------------------------------------- cell export
def export_cells(region, res, m2, rec, agg="data/agg"):
    """Per 0.25-degree cell monthly series so the web app can sum any user-drawn box."""
    p = os.path.join(agg, f"{region}_cell_month.csv.gz")
    if not os.path.exists(p):
        return None
    c = pd.read_csv(p)
    c["ym"] = c.year * 12 + (c.month - 1)
    ym0, ym1 = int(c.ym.min()), int(c.ym.max())
    months = list(range(ym0, ym1 + 1))
    cells = c[["cell_lat", "cell_lon"]].drop_duplicates().sort_values(["cell_lat", "cell_lon"]).values.tolist()
    cidx = {(a, b): i for i, (a, b) in enumerate(cells)}
    c["ci"] = [cidx[(a, b)] for a, b in zip(c.cell_lat, c.cell_lon)]
    c["mi"] = c.ym - ym0
    nC, nM = len(cells), len(months)

    def grid(fam):
        g = np.zeros((nC, nM))
        sub = c[c.family == fam].groupby(["ci", "mi"]).n.sum()
        for (ci, mi), v in sub.items():
            g[ci, mi] = v
        return g

    modis = grid("MODIS")
    bridges = {"VIIRS_SNPP": 1.0, "VIIRS_NOAA20": None, "VIIRS_NOAA21": None}
    # which VIIRS satellites were flying each month (region level), and their bridge to S-NPP scale
    flying = {}
    for fam, sat in [("VIIRS_SNPP", "SNPP"), ("VIIRS_NOAA20", "NOAA20"), ("VIIRS_NOAA21", "NOAA21")]:
        if sat in m2.columns:
            ok = m2[sat].notna()
            flying[fam] = set((y * 12 + mo - 1) for (y, mo), v in ok.items() if v)
    br = {"VIIRS_SNPP": 1.0}
    for fam, sat in [("VIIRS_NOAA20", "NOAA20"), ("VIIRS_NOAA21", "NOAA21")]:
        ov = m2[[sat, "SNPP"]].dropna() if sat in m2.columns and "SNPP" in m2.columns else pd.DataFrame()
        br[fam] = float(ov["SNPP"].sum() / max(ov[sat].sum(), 1e-9)) if len(ov) >= 6 else 1.0
    vsum = np.zeros((nC, nM)); vcnt = np.zeros(nM)
    for fam in flying:
        g = grid(fam) * br[fam]
        for mi, ym in enumerate(months):
            if ym in flying[fam]:
                vsum[:, mi] += g[:, mi]; vcnt[mi] += 1
    harm = np.full((nC, nM), np.nan)
    src = []
    for mi, ym in enumerate(months):
        y, mo = ym // 12, ym % 12 + 1
        if y >= 2012 and vcnt[mi] > 0:
            harm[:, mi] = vsum[:, mi] / vcnt[mi]; src.append("V")
        else:
            key = "Terra" if (y < 2002 or (y == 2002 and mo < 7)) else "MODIS"
            if key in res:
                harm[:, mi] = modis[:, mi] * res[key]["k"][mo]; src.append("M")
            else:
                src.append("-")
    raw = modis + sum(grid(f) for f in flying)   # the naive "just add everything" join
    band = {}
    for _, r in rec.iterrows():
        if r.value > 0:
            band[int(r.year * 12 + r.month - 1 - ym0)] = [round(r.lo / r.value, 3), round(r.hi / r.value, 3)]
    out = {
        "region": region, "start": [ym0 // 12, ym0 % 12 + 1], "n_months": nM,
        "cells": cells, "cell_size": 0.25, "source": "".join(src),
        "harm": np.nan_to_num(np.round(harm, 1)).tolist(),
        "raw": raw.astype(int).tolist(),
        "band": band,
    }
    return out


# ------------------------------------------------------------------ main
def run(region, agg="data/agg", outdir="out"):
    d = load(region, agg)
    rec, summary, res, m2 = harmonize(d)
    clk = clock(d)
    # placebo: a season with no reported timing shift should show no change
    plc = clock(d, season_months=(4, 5))
    clk["placebo_apr_may"] = {k: plc.get(k) for k in ["later_look_effect_pooled"]}
    clk["aqua_probe"] = aqua_probe(d, clk.get("pass_solar_hour_by_year", {}))
    od = os.path.join(outdir, region)
    os.makedirs(od, exist_ok=True)
    rec.round(2).to_csv(os.path.join(od, "fire_calendar.csv"), index=False)
    pairs = clk.pop("_pairs", [])
    summary["clock"] = clk
    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (float, np.floating)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return o
    with open(os.path.join(od, "summary.json"), "w") as f:
        json.dump(clean(summary), f, indent=1, allow_nan=False)
    with open(os.path.join(od, "calendar.json"), "w") as f:
        json.dump(rec.round(2).replace({np.nan: None}).to_dict("records"), f)
    with open(os.path.join(od, "clock_pairs.json"), "w") as f:
        json.dump(pairs, f)
    # monthly per-sensor table for the "seam" chart
    m2.reset_index().round(2).to_csv(os.path.join(od, "monthly_by_sensor.csv"), index=False)
    cells = export_cells(region, res, m2, rec, agg)
    if cells:
        with open(os.path.join(od, "cells.json"), "w") as f:
            json.dump(cells, f, separators=(",", ":"))
    print(json.dumps({k: summary[k] for k in ["fake_2012_jump_ratio", "validation"]}, indent=1))
    print("Oct-Nov:", json.dumps(summary["clock"].get("later_look_effect_pooled", {})))
    print("Apr-May placebo:", json.dumps(summary["clock"].get("placebo_apr_may", {})))
    return rec, summary


if __name__ == "__main__":
    regions = sys.argv[1:] or ["nw_india", "parana_delta"]
    for r in regions:
        if os.path.exists(f"data/agg/{r}_daily.csv"):
            print(f"=== {r}")
            run(r)
