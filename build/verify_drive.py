"""Check the drive-only answers against an independent nearest-feature search.

The shipped number comes from a Euclidean distance transform on a 100 m grid, per
chunk, with a 0.6 degree buffer. Two things could be wrong with that and neither would
look wrong: the grid could be measuring to the wrong thing, and - the real worry - a
chunk's buffer is about 66 km while the best answers are over 150 km, so the nearest
built feature could sit OUTSIDE the data the chunk ever loaded and the distance would
come back inflated.

So this recomputes from the raw bucketed vertices, point-to-SEGMENT rather than
point-to-vertex (power line spans are kilometres long, and vertex-only would overstate
every one), gathering every chunk within a radius far beyond the answer.

    python build/verify_drive.py [n]
"""
import json, sys, numpy as np
sys.path.insert(0, "build")
from au import WORK, BUFFER, read_chunk
from raster import BUILT, ANYTHING

SEARCH_KM = 300.0
R = 6371000.0


def seg_dist_m(plat, plon, lat, lon, off, kind, want):
    """Minimum distance from a point to any segment of the wanted features."""
    kx = np.cos(np.radians(plat))
    x = (lon - plon) * kx * np.pi * R / 180.0
    y = (lat - plat) * np.pi * R / 180.0
    keep = np.ones(len(x) - 1, bool)
    keep[off[1:-1] - 1] = False
    owner = np.zeros(len(x) - 1, np.int64)
    owner[off[1:-1] - 1] = 1
    keep &= want[np.cumsum(owner)]
    if not keep.any():
        return np.inf
    x0, y0, x1, y1 = x[:-1][keep], y[:-1][keep], x[1:][keep], y[1:][keep]
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    t = np.where(L2 > 0, -(x0 * dx + y0 * dy) / np.maximum(L2, 1e-9), 0.0)
    t = np.clip(t, 0.0, 1.0)
    cx, cy = x0 + t * dx, y0 + t * dy
    return float(np.sqrt(cx * cx + cy * cy).min())


def main(n=10, which="drive"):
    meta = json.load(open("docs/data/peaks.json"))
    if which == "drive":
        d, want_set, path = meta["drive"], BUILT, "docs/data/peaks-drive.bin"
    else:
        d = {"count": meta["count"], "measured_to": sorted(ANYTHING)}
        want_set, path = ANYTHING, "docs/data/peaks.bin"
    cfg = json.load(open(f"{WORK}/chunks.json"))
    buf = np.fromfile(path, dtype=np.uint8)
    c = d["count"]
    lat = np.frombuffer(buf, "<i4", c, 0) / meta["coord_scale"]
    lon = np.frombuffer(buf, "<i4", c, 4 * c) / meta["coord_scale"]
    dist = np.frombuffer(buf, "<u2", c, 8 * c).astype(float) * meta["dist_scale_m"]

    print(f"{d['count']:,} {which} peaks, measured to {', '.join(d['measured_to'])}")
    print(f"checking the top {n} against a {SEARCH_KM:.0f} km independent search\n")
    print(f"  {'lat':>9} {'lon':>10} {'shipped':>10} {'independent':>12} "
          f"{'diff':>9}")
    worst_signed_low = 0.0   # most negative diff = worst overstatement
    worst_high = 0.0         # most positive diff = worst understatement
    for i in range(n):
        plat, plon = float(lat[i]), float(lon[i])
        best = np.inf
        for j, box in enumerate(cfg["boxes"]):
            # Does this chunk's loaded extent come within the search radius?
            s, w, nn, e = box[0] - BUFFER, box[1] - BUFFER, box[2] + BUFFER, box[3] + BUFFER
            dlat = max(s - plat, plat - nn, 0.0) * 111.32
            dlon = max(w - plon, plon - e, 0.0) * 111.32 * np.cos(np.radians(plat))
            if (dlat * dlat + dlon * dlon) ** 0.5 > SEARCH_KM:
                continue
            ch = read_chunk(f"{WORK}/c{j:03d}.bin")
            if ch is None:
                continue
            clon, clat, coff, ckind = ch
            best = min(best, seg_dist_m(plat, plon, clat, clon, coff, ckind,
                                        np.isin(ckind, list(want_set))))
        diff = best - dist[i]
        worst_signed_low = min(worst_signed_low, diff)
        worst_high = max(worst_high, diff)
        print(f"  {plat:9.4f} {plon:10.4f} {dist[i]/1000:9.2f}k {best/1000:11.2f}k "
              f"{diff:+8.0f}m")
    # The criterion is ASYMMETRIC, and deliberately so.
    #
    # The page claims "X from anything". Claiming MORE emptiness than exists is the
    # product lying, and it is the failure that shipped: the headline read 176.4 km
    # against a true 137.5. Claiming LESS is caution, and it is what the correction
    # produces, because the continental grid marks a whole 2 km cell as occupied and
    # min() then takes the pessimistic value. So overstatement fails and
    # understatement is reported but passes.
    #
    # GRID_M is what either grid can explain on its own: 2 km cells, so a diagonal
    # of 2.8 km, plus the 100 m field under it.
    GRID_M = 3000.0
    over = -min(0.0, worst_signed_low)   # how far any answer OVERstates, in metres
    print("")
    if over > GRID_M:
        print("FAIL - overstates by %.0f m, beyond the %.0f m the grids explain"
              % (over, GRID_M))
    elif over > 0:
        print("PASS - worst overstatement %.0f m, inside the %.0f m grid tolerance"
              % (over, GRID_M))
    else:
        print("PASS - no answer overstates its emptiness")
    print("     worst understatement %.0f m (conservative, by design)" % worst_high)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10,
         sys.argv[2] if len(sys.argv) > 2 else "drive")
