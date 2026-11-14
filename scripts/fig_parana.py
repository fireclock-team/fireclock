"""FireClock: Paraná Delta figure (naive join vs harmonized). Written with AI assistance (Claude, Anthropic).
Usage: python fig_parana.py out.png [dark]  (reads out/parana_delta/fire_calendar.csv)"""
import sys, pandas as pd, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
out=sys.argv[1]; dark=len(sys.argv)>2
c=pd.read_csv('out/parana_delta/fire_calendar.csv')
a=c.groupby('year')[['value','lo','hi','raw_all_sensors']].sum()
a=a.loc[:2025]
SURF,INK,SEC,GRID=("#120e0c","#f3efe9","#c9c1b6","#2a221e") if dark else ("#ffffff","#1b1512","#5b524a","#e8e2da")
OR,BL="#ff7a3d","#5aa6f5" if dark else "#2f7fd6"
plt.rcParams.update({"font.family":"DejaVu Sans","font.size":15,"axes.edgecolor":GRID,"axes.labelcolor":SEC,"xtick.color":SEC,"ytick.color":SEC,
 "figure.facecolor":SURF,"axes.facecolor":SURF,"text.color":INK,"legend.labelcolor":SEC})
fig,ax=plt.subplots(figsize=(10,4.6))
ax.plot(a.index,a.raw_all_sensors/1000,color=OR,lw=2.4,label="Naive join")
ax.fill_between(a.index,a.lo/1000,a.hi/1000,color=BL,alpha=.25,lw=0)
ax.plot(a.index,a.value/1000,color=BL,lw=2.4,label="FireClock (90% range)")
ax.annotate("2020: naive join's\n\"worst year\"",(2020,a.raw_all_sensors[2020]/1000),xytext=(2013.2,74),color=OR,fontsize=14,arrowprops=dict(arrowstyle="-",color=OR,lw=1))
ax.annotate("2008: smoke over\nBuenos Aires",(2008,a.value[2008]/1000),xytext=(2001.5,70),color=BL,fontsize=14,arrowprops=dict(arrowstyle="-",color=BL,lw=1))
ax.set_ylabel("fire detections per year (thousands)"); ax.set_ylim(0,88)
for s in ["top","right"]: ax.spines[s].set_visible(False)
ax.grid(axis="y",color=GRID); ax.legend(frameon=False,loc="center",bbox_to_anchor=(.55,.62))
fig.text(.99,-.02,"Chart: FireClock code (AI-assisted) · Data: NASA FIRMS",ha="right",fontsize=10,color=SEC)
fig.savefig(out,dpi=200,bbox_inches="tight",facecolor=SURF); print(a.loc[[2008,2020]])
