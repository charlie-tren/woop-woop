"""Render the remoteness surface so it can be judged by eye.

Not a product asset - a check. A distance field that is subtly wrong (a leaked water
mask, a flipped axis, a swathe of unmapped roads) looks obviously wrong on a map and
completely fine in a summary statistic.
"""
import sys, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
sys.path.insert(0, "build")
from raster import Grid


def main(src="data/seq-dist.npz", out="data/preview.png", top=8):
    r = np.load(src, allow_pickle=True)
    dist, wet, ans = r["dist"], r["wet"], r["answerable"]
    g = Grid(tuple(r["bbox"]), float(r["cell"]))
    serve = tuple(r["serve"])
    field = np.where(wet | ~ans, -1, dist)

    picks, f = [], field.copy()
    rr = int(10_000 / g.cell)
    for _ in range(top):
        iy, ix = np.unravel_index(np.argmax(f), f.shape)
        if f[iy, ix] < 0:
            break
        picks.append((ix, iy, float(dist[iy, ix])))
        f[max(0, iy-rr):iy+rr, max(0, ix-rr):ix+rr] = -1

    # Crop to the serve box - the buffer exists to make the maths right, not to look at.
    x0, y0 = g.to_px(serve[1], serve[2])
    x1, y1 = g.to_px(serve[3], serve[0])
    sl = (slice(int(y0), int(y1)), slice(int(x0), int(x1)))

    cmap = LinearSegmentedColormap.from_list("remote", [
        "#12161c", "#1b2735", "#24405a", "#2f6a72", "#5fa06b", "#d6c05a", "#f2efe4"])
    show = np.where(wet, np.nan, dist)[sl] / 1000.0

    fig, ax = plt.subplots(figsize=(9, 12), dpi=110)
    fig.patch.set_facecolor("#0d1117")
    im = ax.imshow(show, cmap=cmap, vmin=0, vmax=7.0, interpolation="nearest")
    ax.set_facecolor("#05070a")

    for n, (ix, iy, d) in enumerate(picks, 1):
        x, y = ix - int(x0), iy - int(y0)
        ax.plot(x, y, "o", ms=11, mfc="none", mec="#ff5c5c", mew=1.8)
        ax.annotate(f"{n}  {d/1000:.1f} km", (x, y), (13, 5),
                    textcoords="offset points", color="#ff9a9a", fontsize=9.5,
                    weight="bold")

    ax.set_title("How far you can get from a road, a building, a railway,\n"
                 "a power line or a runway  -  South East Queensland",
                 color="#e6edf3", fontsize=13, pad=16)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.032, pad=0.02)
    cb.set_label("km to the nearest anything", color="#e6edf3")
    cb.ax.yaxis.set_tick_params(color="#8b949e")
    plt.setp(plt.getp(cb.ax.axes, "yticklabels"), color="#8b949e")
    fig.text(0.5, 0.045, "flat black = ocean and lakes, masked out because you cannot "
             f"stand there  ·  best on land: {picks[0][2]/1000:.2f} km",
             ha="center", color="#8b949e", fontsize=9)
    fig.savefig(out, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"-> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
