#!/usr/bin/env python3
"""Картинка со всеми сценариями: python preview.py [data_dir] [out.png]. Нужен matplotlib."""
import json, sys
from datetime import datetime, timedelta, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as md
import matplotlib.pyplot as plt
from fake_cgm import clean

MSK = timezone(timedelta(hours=3))
data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
out = sys.argv[2] if len(sys.argv) > 2 else "preview.png"
meta = json.load(open(f"{data_dir}/scenarios.json"))
names = list(meta)
rows = -(-len(names) // 4)
fig, axs = plt.subplots(rows, 4, figsize=(19, 3.6 * rows))
for ax in axs.flat[len(names):]:
    ax.axis("off")
for ax, n in zip(axs.flat, names):
    pts = clean(json.load(open(f"{data_dir}/{n}.json")))
    t = [datetime.fromtimestamp(ms / 1000, MSK) for ms, _ in pts]
    ax.axhspan(3.9, 10, color="#2a9d8f", alpha=.1)
    for y, c in [(3.0, "#d62828"), (3.9, "#f4a261"), (10, "#f4a261"), (13.9, "#d62828")]:
        ax.axhline(y, color=c, lw=.8, ls="--")
    ax.plot(t, [v for _, v in pts], ".", color="#264653", ms=3.5)
    ax.set_title(n, fontsize=11)
    ax.set_ylim(2, 16.5)
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(md.DateFormatter("%H:%M", tz=MSK))
fig.suptitle("DiaBetrics — сценарии fake_cgm v2 (ммоль/л, МСК); пунктир: 3.0 / 3.9 / 10.0 / 13.9")
fig.tight_layout()
fig.savefig(out, dpi=100)
print(out)
