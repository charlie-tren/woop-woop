"""Export the serve box as a single PNG the browser can read directly.

One image rather than map tiles. The serve box is ~2000x3100 cells, and a distance
field is smooth, so PNG's filters compress it to a size worth shipping whole - at which
point the client needs no server, no tile logic and no API for the measurement half.
Everything the page does with it is a pixel lookup.

Encoding: two channels of one image, so both fields arrive in a single request.
  R = metres to the nearest CIVILISATION, in STEP units. Zero is reserved for "you
      cannot stand here" (ocean, lakes, outside the answered region), so a value of 0
      and a distance of 0 are never confused - a cell ON a road is 1, not 0.
  G = metres to the nearest ACCESS way (road, track or path) in REACH_STEP units,
      which is finer because the only question asked of it is "is this within a short
      walk", and it is clipped rather than scaled - anything past the ceiling is
      already too far to matter.
"""
import json, sys, numpy as np
from PIL import Image
sys.path.insert(0, "build")
from raster import Grid

STEP = 50        # metres per unit; 255 * 50 = 12.75 km ceiling
REACH_STEP = 25  # metres per unit for the access field; ceiling 6.4 km


def main(src="data/seq-dist.npz", out_png="docs/data/seq.png",
         out_json="docs/data/seq.json"):
    r = np.load(src, allow_pickle=True)
    dist, wet, ans, reach = r["dist"], r["wet"], r["answerable"], r["reach"]
    g = Grid(tuple(r["bbox"]), float(r["cell"]))
    serve = tuple(r["serve"])

    x0, y0 = g.to_px(serve[1], serve[2])
    x1, y1 = g.to_px(serve[3], serve[0])
    x0, y0, x1, y1 = int(np.ceil(x0)), int(np.ceil(y0)), int(x1), int(y1)
    sl = (slice(y0, y1), slice(x0, x1))

    d = dist[sl]
    standable = (~wet & ans)[sl]
    # The ceiling is checked against STANDABLE cells only. Open ocean sits 50 km from
    # the nearest anything and would trip it, but those cells are masked to 0 anyway.
    if standable.any() and d[standable].max() > STEP * 255:
        raise RuntimeError(f"{d[standable].max():.0f} m exceeds the "
                           f"{STEP*255} m encoding ceiling")

    v = np.clip(np.rint(d / STEP), 1, 255).astype(np.uint8)
    v[~standable] = 0
    g = np.clip(np.rint(reach[sl] / REACH_STEP), 0, 255).astype(np.uint8)

    rgb = np.dstack([v, g, np.zeros_like(v)])
    Image.fromarray(rgb, mode="RGB").save(out_png, optimize=True)

    meta = {
        "south": serve[0], "west": serve[1], "north": serve[2], "east": serve[3],
        "width": int(v.shape[1]), "height": int(v.shape[0]),
        "cell_m": float(r["cell"]), "step_m": STEP, "reach_step_m": REACH_STEP,
        "max_m": float(d[standable].max()) if standable.any() else 0.0,
        "note": ("R: 0 = cannot stand there, else metres to civilisation = R * step_m. "
                 "G: metres to the nearest road, track or path = G * reach_step_m."),
    }
    import os
    json.dump(meta, open(out_json, "w"), indent=2)
    kb = os.path.getsize(out_png) / 1024
    print(f"{v.shape[1]}x{v.shape[0]} -> {out_png}  {kb:,.0f} KB "
          f"({kb/ (v.size/1024):.2f} bytes/cell)")
    print(f"  standable {100*(v>0).mean():.0f}%, max {meta['max_m']/1000:.2f} km")


if __name__ == "__main__":
    main(*sys.argv[1:])
