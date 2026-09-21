"""How many peaks the routing graph can actually cost exactly.

A peak with a graph node of its own gets a true shortest-path time. A peak without one is
charged its nearest junction's time plus a straight-line run in, which is an approximation
that grows with the run-in - and on the long unbroken outback segments this app exists to
find, the run-in is kilometres.

    python build/check_peak_nodes.py

WHY THIS IS A SCRIPT AND NOT A NUMBER IN A COMMENT. The figure was recorded as "34% of
peaks have a node", derived from counting the WAY VERTICES that sit within 15 m of a peak
(142,230) against the peak count (415,448). Those are different denominators: one vertex
can be near several peaks and most peaks are near no vertex at all, so the ratio was not
the coverage it was read as. Measured the right way round - for each PEAK, the distance to
the nearest graph node - it is 26.6%, and the median peak is 590 m from a node.

Run it after any change to snap.py, PEAK_SNAP_M, or the extractor.
"""
import json
import sys

import numpy as np
from scipy.spatial import cKDTree

MPD = np.pi * 6371000.0 / 180.0
BANDS = (5, 15, 50, 150, 400, 1000)


def load_peaks(meta_path="docs/data/peaks.json", bin_path="docs/data/peaks.bin"):
    meta = json.load(open(meta_path))
    buf = np.fromfile(bin_path, dtype=np.uint8)
    n, s = meta["count"], meta["coord_scale"]
    return (np.frombuffer(buf, "<i4", n, 0) / s,
            np.frombuffer(buf, "<i4", n, 4 * n) / s)


def main():
    g = np.load("data/au/graph.npz")
    plat, plon = load_peaks()
    klon = MPD * np.cos(np.radians(float(plat.mean())))
    tree = cKDTree(np.column_stack((g["lon"] * klon, g["lat"] * MPD)))
    d, _ = tree.query(np.column_stack((plon * klon, plat * MPD)), workers=-1)

    print(f"{len(plat):,} peaks against {len(g['lat']):,} graph nodes")
    for t in BANDS:
        print(f"  within {t:5} m of a node: {(d <= t).sum():8,}  {(d <= t).mean() * 100:5.1f}%")
    print(f"  median {np.median(d):6.0f} m   p90 {np.percentile(d, 90):6.0f} m   "
          f"max {d.max():7.0f} m")

    # The run-in is charged at the mode's fastest class, so state it as the time it buys
    # on the budget it eats. A peak 3 km out on foot is 36 minutes of a 60 minute answer.
    for mode, kmh, budget in (("foot", 5.5, 60), ("bike", 18.0, 120), ("car", 97.0, 60)):
        p90_min = np.percentile(d, 90) / 1000.0 / kmh * 60.0
        print(f"  {mode:5} p90 run-in is {p90_min:5.1f} min, "
              f"{p90_min / budget * 100:4.0f}% of a {budget} min budget")

    # Falsifiable: this is the claim the calibration rests on, so it fails loudly.
    frac = float((d <= 15).mean())
    print(f"\nexact-cost coverage: {frac * 100:.1f}%")
    if frac < 0.90:
        print("FAIL - most peaks are costed by approximation, so a peak-agreement "
              "comparison against ORS measures the run-in, not the speed model.")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
