#!/usr/bin/env python3
"""
FireClock - step 1: download NASA FIRMS active-fire detections and aggregate them.

Written with AI assistance (Claude, Anthropic) during NASA Space Apps 2026.

Usage:
    pip install requests pandas
    python fetch_firms.py YOUR_MAP_KEY

Get a free MAP_KEY at https://firms.modaps.eosdis.nasa.gov/api/map_key/
Output goes to ./data/ . Upload the files in ./data/agg/ back to Claude.
Safe to stop and re-run: finished chunks are cached in ./data/raw/.
"""
import io
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import requests

API = "https://firms.modaps.eosdis.nasa.gov/api"
DAY_RANGE = 5               # days per request
MAX_PER_10MIN = 4500        # FIRMS limit is 5000 transactions / 10 min; stay under
WORKERS = 3

REGIONS = {
    # name: (west, south, east, north)
    "nw_india": (73.8, 27.6, 77.6, 32.6),     # Punjab + Haryana (incl. edge of Pakistan Punjab)
    "parana_delta": (-60.9, -34.4, -58.3, -32.4),  # Argentina, generality demo
}

# product family -> (standard-processing source, near-real-time source, first year to try)
FAMILIES = {
    "MODIS": ("MODIS_SP", "MODIS_NRT", 2001),
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT", 2012),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT", 2018),
    "VIIRS_NOAA21": ("VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT", 2023),
}

OUT = "data"
RAW = os.path.join(OUT, "raw")
AGG = os.path.join(OUT, "agg")


class RateLimiter:
    def __init__(self, n, per_s):
        self.n, self.per_s, self.times, self.lock = n, per_s, [], threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                now = time.time()
                self.times = [t for t in self.times if now - t < self.per_s]
                if len(self.times) < self.n:
                    self.times.append(now)
                    return
                sleep = self.per_s - (now - self.times[0]) + 0.05
            time.sleep(max(sleep, 0.05))


limiter = RateLimiter(MAX_PER_10MIN, 600)


KEY = None
_pause_until = 0.0
_pause_lock = threading.Lock()
_first_error = []


def key_status():
    """Ask FIRMS how many transactions this key has used in the current 10-minute window."""
    try:
        j = requests.get(f"https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={KEY}", timeout=30).json()
        return int(j.get("current_transactions", 0)), int(j.get("transaction_limit", 5000))
    except Exception:
        return None


def wait_for_quota():
    """Pause all workers when the key is near its 10-minute limit."""
    global _pause_until
    while True:
        with _pause_lock:
            now = time.time()
            if now < _pause_until:
                wait = _pause_until - now
            else:
                st = key_status()
                if st and st[0] >= st[1] * 0.9:
                    _pause_until = now + 60
                    print(f"  quota {st[0]}/{st[1]} used - pausing 60 s so NASA doesn't block us...", flush=True)
                    wait = 60
                else:
                    return
        time.sleep(wait)


_count = [0]


def get(url, tries=8):
    for i in range(tries):
        limiter.wait()
        _count[0] += 1
        if _count[0] % 25 == 0 or i > 0:
            wait_for_quota()
        try:
            r = requests.get(url, timeout=90)
            txt = r.text
            low = txt[:300].lower()
            if r.status_code == 200 and not ("exceed" in low or "limit" in low and "transaction" in low):
                return txt
            if not _first_error:
                _first_error.append(f"HTTP {r.status_code}: {txt[:200]}")
                print(f"  NASA said: {_first_error[0]}", flush=True)
            # rate limit or server hiccup: wait and retry
            time.sleep(min(30 * (i + 1), 120))
        except requests.RequestException:
            time.sleep(min(15 * (i + 1), 90))
    raise RuntimeError(f"failed after {tries} tries: {url}")


def availability(key):
    txt = get(f"{API}/data_availability/csv/{key}/ALL")
    df = pd.read_csv(io.StringIO(txt))
    df.columns = [c.strip().lower() for c in df.columns]
    out = {}
    for _, r in df.iterrows():
        out[str(r["data_id"]).strip()] = (pd.to_datetime(r["min_date"]).date(),
                                          pd.to_datetime(r["max_date"]).date())
    return out


def plan(avail):
    """Return list of (family, source, start_date) chunks. SP preferred; NRT fills the gap after SP ends."""
    jobs = []
    today = date.today()
    for fam, (sp, nrt, y0) in FAMILIES.items():
        covered_until = None
        if sp in avail:
            a, b = avail[sp]
            d = max(a, date(y0, 1, 1))
            while d <= b:
                jobs.append((fam, sp, d))
                d += timedelta(days=DAY_RANGE)
            covered_until = b
        if nrt in avail:
            a, b = avail[nrt]
            start = a if covered_until is None else max(a, covered_until + timedelta(days=1))
            d = start
            while d <= min(b, today):
                jobs.append((fam, nrt, d))
                d += timedelta(days=DAY_RANGE)
        if sp not in avail and nrt not in avail:
            print(f"  ! {fam}: no source available, skipping")
    return jobs


def fetch_chunk(key, region, bbox, fam, src, d):
    path = os.path.join(RAW, region, src, f"{d.isoformat()}.csv.gz")
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    area = ",".join(str(x) for x in bbox)
    txt = get(f"{API}/area/csv/{key}/{src}/{area}/{DAY_RANGE}/{d.isoformat()}")
    if txt.lstrip().lower().startswith(("invalid", "error")):
        raise RuntimeError(f"{src} {d}: {txt[:200]}")
    df = pd.read_csv(io.StringIO(txt)) if txt.strip() else pd.DataFrame()
    df["family"] = fam
    df["source"] = src
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)
    return path


def load_region(region):
    frames = []
    base = os.path.join(RAW, region)
    for src in sorted(os.listdir(base)):
        for f in sorted(os.listdir(os.path.join(base, src))):
            if f.endswith(".csv.gz"):
                df = pd.read_csv(os.path.join(base, src, f))
                if len(df) and "latitude" in df.columns:
                    frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # NRT and SP can overlap on boundary days: drop exact duplicates
    keep = [c for c in ["latitude", "longitude", "acq_date", "acq_time", "family"] if c in df.columns]
    df = df.drop_duplicates(subset=keep)
    return df


def aggregate(region, df):
    os.makedirs(AGG, exist_ok=True)
    df = df.copy()
    # Drop non-fire heat sources where NASA labels them (standard data has 'type':
    # 0 vegetation fire, 1 volcano, 2 other static land source, 3 offshore). NRT rows lack it.
    if "type" in df.columns:
        t_ = pd.to_numeric(df["type"], errors="coerce")
        before = len(df)
        df = df[t_.isna() | (t_ == 0)]
        print(f"  removed {before - len(df):,} static/volcanic/offshore detections")
    df["acq_date"] = pd.to_datetime(df["acq_date"])
    t = df["acq_time"].astype(int)
    df["utc_hour"] = (t // 100) + (t % 100) / 60.0
    # local solar time from longitude (sun-based, good for orbit-timing analysis)
    df["solar_hour"] = (df["utc_hour"] + df["longitude"] / 15.0) % 24
    df["year"] = df["acq_date"].dt.year
    df["month"] = df["acq_date"].dt.month
    df["cell_lat"] = (df["latitude"] // 0.25) * 0.25
    df["cell_lon"] = (df["longitude"] // 0.25) * 0.25
    conf = df["confidence"].astype(str)
    # MODIS confidence is 0-100; VIIRS is l/n/h. Flag low-confidence for sensitivity tests.
    df["low_conf"] = conf.isin(["l", "low"]) | (pd.to_numeric(conf, errors="coerce") < 30)
    df["frp"] = pd.to_numeric(df.get("frp"), errors="coerce")

    # 1) one row per detection, slimmed (for the clock analysis)
    slim = df[["family", "satellite", "acq_date", "acq_time", "solar_hour", "daynight",
               "latitude", "longitude", "frp", "low_conf"]]
    slim.to_csv(os.path.join(AGG, f"{region}_detections.csv.gz"), index=False, compression="gzip")

    # 2) daily counts per family/satellite/daynight (for calendars and the overpass experiment)
    # pixel size grows away from nadir (scan x track, km): needed to control view-angle bias
    df["pix_area"] = pd.to_numeric(df.get("scan"), errors="coerce") * pd.to_numeric(df.get("track"), errors="coerce")
    daily = (df.groupby(["family", "satellite", "daynight", "acq_date"])
               .agg(n=("frp", "size"), n_hi=("low_conf", lambda s: int((~s).sum())),
                    frp_sum=("frp", "sum"), solar_hour_med=("solar_hour", "median"),
                    pix_area_med=("pix_area", "median"))
               .reset_index())
    daily.to_csv(os.path.join(AGG, f"{region}_daily.csv"), index=False)

    # 3) monthly counts per 0.25 deg cell (for spatial harmonization)
    cm = (df.groupby(["family", "daynight", "year", "month", "cell_lat", "cell_lon"])
            .agg(n=("frp", "size"), frp_sum=("frp", "sum")).reset_index())
    cm.to_csv(os.path.join(AGG, f"{region}_cell_month.csv.gz"), index=False, compression="gzip")
    print(f"  {region}: {len(df):,} detections -> {AGG}/")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    global KEY
    key = sys.argv[1].strip()
    KEY = key
    only = sys.argv[2:]          # optional: region names to run, e.g. nw_india
    print("Checking which FIRMS sources and dates are available...")
    avail = availability(key)
    for k, v in sorted(avail.items()):
        print(f"  {k:22s} {v[0]} -> {v[1]}")
    with open(os.path.join(os.makedirs(OUT, exist_ok=True) or OUT, "availability.csv"), "w") as f:
        f.write("data_id,min_date,max_date\n")
        for k, (a, b) in sorted(avail.items()):
            f.write(f"{k},{a},{b}\n")

    jobs = plan(avail)
    for region, bbox in REGIONS.items():
        if only and region not in only:
            continue
        print(f"\nDownloading {region}: {len(jobs)} requests (cached ones are skipped)")
        done, failed = 0, []
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = {ex.submit(fetch_chunk, key, region, bbox, *j): j for j in jobs}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:
                    failed.append((futs[fut], str(e)[:120]))
                done += 1
                if done % 100 == 0 or done == len(jobs):
                    print(f"  {done}/{len(jobs)}  failed={len(failed)}", flush=True)
        if failed:
            print(f"  {len(failed)} chunks failed (re-run the script to retry). First: {failed[0]}")
        df = load_region(region)
        if len(df):
            aggregate(region, df)

    print("\nDone. Upload everything in data/agg/ plus data/availability.csv to Claude.")


if __name__ == "__main__":
    main()
