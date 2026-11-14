import os
"""FireClock figures for README and slides. Written with AI assistance (Claude, Anthropic)."""
import json, sys, os, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
out = sys.argv[2] if len(sys.argv) > 2 else "img"; os.makedirs(out, exist_ok=True)
od = sys.argv[1]
S = json.load(open(f"{od}/summary.json")); cal = pd.DataFrame(json.load(open(f"{od}/calendar.json")))
DARK = os.environ.get("THEME") == "dark"
if DARK:
    INK, SEC, MUT, GRID, SURF = "#f3efe9", "#c9c1b6", "#8f877d", "#2a2320", "#120e0c"
    BLUE, ORANGE, AQUA, YEL, MAG = "#5aa6f5", "#ff7a3d", "#2fd393", "#ffbf3c", "#f28fb5"
else:
    INK, SEC, MUT, GRID, SURF = "#0b0b0b", "#52514e", "#7a7974", "#ecebe7", "#fcfcfb"
    BLUE, ORANGE, AQUA, YEL, MAG = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
RAMP_L = ["#fdeee6","#fbd3bf","#f6ad8a","#ef8456","#eb6834","#c9531f","#a3421a","#7c3214","#5a240e"]
RAMP = ["#2a1a12","#4a2414","#6e2f15","#933c17","#b84b1a","#d95d22","#ef7a35","#ff9b57","#ffc08a"] if DARK else RAMP_L
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "axes.edgecolor": GRID, "axes.labelcolor": SEC,
                     "xtick.color": MUT, "ytick.color": MUT, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "text.color": INK, "legend.labelcolor": SEC})
if DARK:
    plt.rcParams.update({"font.size": 15})
MARK = "Chart: FireClock code (AI-assisted) · Data: NASA FIRMS"
def T(ax, *a, **k):
    if os.environ.get("NOTITLE"):
        return
    ax.set_title(*a, **k)
def finish(fig, name):
    fig.text(0.995, -0.04, MARK, ha="right", va="bottom", fontsize=8.5, color=MUT)
    fig.savefig(f"{out}/{name}", dpi=200, bbox_inches="tight", facecolor=SURF, transparent=os.environ.get("THEME")=="dark"); plt.close(fig)
hm = lambda h: f"{int(h)%12 or 12}:{round(h%1*60):02d}"

# 1 probe
P = S["clock"]["aqua_probe"]
fig, ax = plt.subplots(figsize=(9, 5))
for k, c, lab in [("oct_nov", ORANGE, "Oct–Nov · rice-residue season"), ("apr_may", BLUE, "Apr–May · wheat season (placebo)")]:
    d = pd.DataFrame(P[k]); d = d[(d.year >= 2013) & d.aqua_hour.notna()].sort_values("aqua_hour")
    if DARK:
        for w, a in [(14, .06), (9, .10), (5, .18)]:
            ax.plot(d.aqua_hour, d.multiplier, color=c, lw=w, alpha=a, zorder=2, solid_capstyle="round")
    ax.plot(d.aqua_hour, d.multiplier, color=c, lw=3 if DARK else 2.2, zorder=3)
    ax.scatter(d.aqua_hour, d.multiplier, s=46, color=c, edgecolor=SURF, linewidth=2, zorder=4, label=lab)
    if k == "oct_nov":
        for _, r in d[d.year >= 2023].iterrows():
            ax.annotate(f"{int(r.year)}\n{hm(r.aqua_hour)} pm", (r.aqua_hour, r.multiplier), xytext=(8, -4), textcoords="offset points", fontsize=10, color=SEC)
ax.axhline(1, color=MUT, ls=(0, (4, 3)), lw=1)
ax.text(ax.get_xlim()[1], 1.12, "×1 = Aqua at ~1:15 pm (2013–2020)", ha="right", fontsize=10, color=MUT)
ax.set_xlabel("Aqua's local look time over Punjab & Haryana")
ax.set_ylabel("fire seen, relative to VIIRS (~1:15 pm)")
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{int(v)}:{int(round(v%1*60)):02d}"))
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"×{v:g}"))
ax.grid(axis="y", color=GRID); ax.legend(frameon=False, loc="upper left")
T(ax, "As Aqua's orbit drifted later, it saw ever more rice-season fire", loc="left", fontsize=14, color=INK, pad=12)
finish(fig, "fig_probe.png")

# 2 seam
by = cal.groupby("year").agg(harm=("value", "sum"), lo=("lo", "sum"), hi=("hi", "sum"), raw=("raw_all_sensors", "sum"), n=("month", "size"))
by = by[by.n == 12]
fig, ax = plt.subplots(figsize=(9, 4.6))
ax.fill_between(by.index, by.lo, by.hi, color=BLUE, alpha=.15, lw=0)
ax.plot(by.index, by.raw, color=ORANGE, lw=2.2, label="Naive join")
ax.plot(by.index, by.harm, color=BLUE, lw=2.2, label="FireClock (90% range)")
ax.axvline(2012, color=MUT, ls=(0, (4, 3)), lw=1); ax.text(2012.2, ax.get_ylim()[1]*.12, "VIIRS arrives", color=MUT, fontsize=10)
j = S["fake_2012_jump_ratio"]
T(ax, f"Fake 2012 jump: {j['raw_naive_join']:.1f}× in the naive join → {j['harmonized']:.2f}× after harmonization", loc="left", fontsize=14, color=INK, pad=12)
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
ax.set_ylabel("fire detections per year"); ax.grid(axis="y", color=GRID); ax.legend(frameon=False, loc="upper left")
finish(fig, "fig_seam.png")

# 3 drift
ph = S["clock"]["pass_solar_hour_by_year"]
fig, ax = plt.subplots(figsize=(9, 4.6))
names = {"Terra": ("Terra · MODIS", ORANGE), "Aqua": ("Aqua · MODIS", BLUE), "SNPP": ("S-NPP · VIIRS", AQUA), "NOAA20": ("NOAA-20 · VIIRS", YEL), "NOAA21": ("NOAA-21 · VIIRS", MAG)}
ends = []
for s, (lab, c) in names.items():
    if s not in ph: continue
    yy = sorted(ph[s], key=int); xs = [int(y) for y in yy]; vs = [ph[s][y] for y in yy]
    ax.plot(xs, vs, color=c, lw=2.2, label=lab); ends.append([xs[-1] + .3, vs[-1], lab, c])
ends.sort(key=lambda e: e[1])
for i in range(1, len(ends)):
    if ends[i][1] - ends[i-1][1] < .22: ends[i][1] = ends[i-1][1] + .22
for x, y, lab, c in ends: ax.text(x, y, lab, va="center", fontsize=10, color=SEC)
ax.set_xlim(right=2031); ax.set_xticks([2000, 2005, 2010, 2015, 2020, 2026])
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{int(v)}:{int(round(v%1*60)):02d}"))
ax.set_ylabel("local look time, Oct–Nov daytime pass"); ax.grid(axis="y", color=GRID)
T(ax, "Terra and Aqua no longer look at the same hour: measured over Punjab", loc="left", fontsize=14, color=INK, pad=12)
finish(fig, "fig_drift.png")

# 4 calendar
pv = cal.pivot(index="year", columns="month", values="value")
vals = np.sort(pv.values[np.isfinite(pv.values) & (pv.values > 0)])
qs = [vals[int(len(vals)*i/9)] for i in range(1, 9)]
cmap = ListedColormap(RAMP); norm = BoundaryNorm([0] + qs + [vals.max()+1], 9)
fig, ax = plt.subplots(figsize=(8, 8.5))
ax.imshow(pv.values, cmap=cmap, norm=norm, aspect="auto")
ax.set_xticks(range(12)); ax.set_xticklabels(list("JFMAMJJASOND"))
ax.set_yticks(range(len(pv.index))); ax.set_yticklabels(pv.index, fontsize=9)
ax.axhline(list(pv.index).index(2012) - .5, color=INK, ls=(0, (3, 3)), lw=1)
for s_ in ax.spines.values(): s_.set_visible(False)
ax.tick_params(length=0)
T(ax, "Harmonized burning calendar · Punjab & Haryana, 2000–2026", loc="left", fontsize=14, color=INK, pad=12)
finish(fig, "fig_calendar.png")
print("ok")
