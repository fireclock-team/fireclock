# FireClock

**A fire calendar that knows what time it is.**
NASA Space Apps Challenge 2026 · Challenge: *Harmonization of MODIS and VIIRS Hot Spots* · Team FireClock

**Live app:** https://fireclock-team.github.io/fireclock/

> Every fire calendar assumes satellites look at the same hour every year. They don't, and neither do the fires.

## Highlights for judges

- **Four breaks, not one.** Besides the 2012 sensor change, the record breaks when Terra/Aqua orbits drift (2020→), when S-NPP stops (Nov 2026) and when burning shifts later in the day (NW India, 2022→). FireClock handles all four.
- **Calibrated only on clean years.** The MODIS→VIIRS exchange rate is learned from 2013–2020, before the orbits drifted, and tested on years the model never saw.
- **A same-day natural experiment.** Two identical VIIRS instruments, same fields, same day, ~50 min apart: only the clock differs. Viewing angle is controlled.
- **A placebo season.** The same test in a season with no reported shift (April–May) checks that the method doesn't invent effects.
- **Honest limits.** A hotspot is a detection, not a fire. What the test can and can't show is stated plainly.

## What it does

FireClock joins 25 years of NASA FIRMS active-fire detections from MODIS (2000→) and VIIRS (2012→) into one consistent burning-activity calendar for any area you draw. It also tells you when the satellite's clock may be hiding fire.

- **Burning calendar**: month × year heatmap, harmonized or raw, any box on a 0.25° grid, CSV download.
- **Current month vs history**: anomaly against the 2003–2022 median, with "above / normal / below" classes and the area's critical period.
- **Year-by-year record**: the naive join (fake jump in 2012) vs the harmonized record, with a 90% range.
- **The satellite clock**: when each satellite actually looks at the area, year by year, plus a same-day test of whether *when* it looks changes *how much* it sees.
- **Can you trust it?**: errors on years the model never saw.

## Why the clock matters

The fire record has four breaks, not one:

1. **Sensor (2012).** VIIRS (375 m) sees several times more hotspots than MODIS (1 km).
2. **Orbit (2020→).** Terra and Aqua no longer hold their orbits: Terra drifts earlier, Aqua later. The "same" MODIS sensor now looks at a different hour every year.
3. **Platform (Nov 2026).** Suomi NPP, the usual VIIRS reference, stopped delivering data on 2 November 2026; the record must bridge to NOAA-20 and NOAA-21.
4. **Behaviour (2022→).** In NW India, studies report crop-residue burning shifting to late afternoon, after the ~1:30 pm satellite pass.

Most harmonization tools fix break 1. FireClock calibrates only on years before the orbits drifted, bridges the VIIRS satellites, and **measures** break 4 with NASA's own data.

## The same-day test

On many days, two VIIRS satellites (identical instruments) pass over the same fields about 50 minutes apart. Same fields, same weather, same season: only the clock differs. We regress the log ratio of their detection counts on the difference in local look time, with pixel size (viewing angle) as a control, separately for 2018–2021 and 2022–now.

### Results for Punjab & Haryana (3,057,777 detections, Nov 2000 – Nov 2026)

**Harmonization.** The naive join shows a fake **6.1× jump** in 2012; FireClock's record shows **1.06×**. On years the model never saw (leave-one-year-out, 2013–2020), annual totals are off by a median **7.3%** (R² = 0.983 on log monthly totals).

**The satellite clock.** Over Punjab in Oct–Nov, Aqua's look time drifted from **13:17** (2020) to **15:32** (2026); Terra's from **10:58** to **9:11**.

**Aqua as an accidental afternoon fire-watcher.** Relative to the fixed ~1:15 pm VIIRS satellites, Aqua's rice-season (Oct–Nov) count rose as it looked later:

| Year | Aqua look time | Rice season (Oct–Nov) | Wheat season (Apr–May, placebo) |
|---|---|---|---|
| 2022 | 13:23 | ×1.10 | ×1.27 |
| 2023 | 13:37 | ×1.33 | ×0.59 |
| 2024 | 14:05 | ×1.60 | ×1.10 |
| 2025 | 14:43 | ×2.26 | ×0.97 |
| 2026 (to 13 Nov) | 15:32 | ×5.44 | ×0.81 |

The wheat season stays flat with the same satellite and the same drift, so the rise is not a sensor or orbit-height artifact: it is fire burning after the main satellites pass.

**Same-day test (supporting).** Within one hour, a later VIIRS look sees **+28.6%** (2018–21) and **+29.6%** (2022–now) more rice-season fire per hour (wheat season, 2022–now: +8.4%). The satellites pass on the rising side of the daily fire cycle. This short-window effect is similar before and after 2022, so this test alone cannot show *when* burning shifted; the published geostationary study (below) reports that it did.

**What it means.** VIIRS-based counts, the ones used to track crop-fire policy, see a timing-sensitive slice of the rice season. Oct 2026 is down sharply against the 2003–2022 median in the harmonized record; FireClock flags that a falling count here can mean fewer fires *or* fires the satellites did not see.


## Method

1. **Data**: all FIRMS detections for the area (MODIS C6.1; VIIRS 375 m S-NPP, NOAA-20, NOAA-21), standard archive then near-real-time. Volcano, offshore and static industrial detections removed where NASA labels them.
2. **One VIIRS scale**: NOAA-20/21 bridged to S-NPP and averaged across satellites, so more satellites never means more fire.
3. **MODIS → VIIRS exchange rate**: per calendar month, learned from 2013–2020 only (both MODIS orbits still controlled), shrunk toward the yearly rate (strength picked by leave-one-year-out), 90% range from a 500× year-block bootstrap plus Poisson noise. Terra-only rate before July 2002.
4. **Calendar**: monthly sums on a 0.25° grid; the browser sums any box you draw.

## Run it yourself

```
pip install requests pandas numpy
python scripts/fetch_firms.py YOUR_FIRMS_MAP_KEY nw_india   # free key: firms.modaps.eosdis.nasa.gov/api/map_key/
python scripts/harmonize.py nw_india
# copy out/nw_india/* to docs/data/nw_india/ and open docs/index.html via any static server
```

## Limits

A hotspot is a satellite detection, not a fire, a burned area or an emissions estimate. Clouds and thick smoke hide fires. The clock test measures a *change in timing*; it does not count how many fires are missed in total.

## Data and references

- NASA FIRMS active fire data: MODIS C6.1 (doi:10.5067/FIRMS/MODIS/MCD14ML), VIIRS 375 m S-NPP / NOAA-20 / NOAA-21 (doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002 and companion products).
- NSIDC, MODIS: ongoing changes in Terra and Aqua orbits. https://nsidc.org/data/modis
- NASA FIRMS / Earthdata notice: Suomi NPP data delivery ends November 2026.
- "Timing the Flames: Geostationary Satellite Detection of Diurnally Shifting Stubble Burning in Northwestern India", *Remote Sensing* 18(10):1506, 2026. https://doi.org/10.3390/rs18101506
- ThePrint (2025), ISRO study on stubble-burning timing.
- Leaflet (BSD-2), OpenStreetMap tiles (ODbL), pandas, NumPy, Requests.

## Use of AI

Code, research support and writing were drafted with **Claude (Anthropic)** and reviewed by the team. The team chose the challenge and the scientific question, made the analysis decisions, ran the data pipeline, and checked the results. No AI-generated images, audio or video are used. Every number comes from NASA data through the open-source scripts here. Files written with AI assistance say so in their header.

## Licence

MIT. See [LICENSE](LICENSE).
