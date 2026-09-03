"""Snap every peak onto the way it is supposed to be standing on.

The build guarantees a peak is ON the network only to the resolution of the grid: the
chunk stage keeps cells where an access feature was BURNED (`reach <= 0`), and then
reports the cell CENTRE. A 100 m cell puts the reported coordinate up to about 70 m from
the line that qualified it. Measured on the shipped data: 30 to 60 m off the nearest
path, which at street zoom is plainly beside the track rather than on it, and the page
claims "the spot is ON a track, so you can follow it the whole way".

So this moves each peak to the nearest point on the nearest ACCESS segment, and each
access point to the nearest point on the nearest CIVILISATION segment - the latter being
where the page tells you to leave the car.

`d` is deliberately NOT recomputed. It is a property of the 100 m cell, the move stays
inside that cell, and the verifier's tolerance already covers grid quantisation. Moving
the coordinate without touching the distance is the honest pairing: the number keeps
meaning what it measured, and the point is now somewhere you can stand.

    python build/snap.py          # rewrites data/au/peaks/*.npy and drive/*.npy in place
"""
import json, os, sys, time
import numpy as np
sys.path.insert(0, "build")
from au import WORK, BUFFER, read_chunk
from raster import ANYTHING

# What a peak may be snapped ONTO. Not ACCESS, which includes buildings, railways and
# power lines - none of them somewhere to stand, and the building outlines alone are
# half the segments in an urban chunk. A peak with d > 0 has nothing from ANYTHING in
# its cell, so the feature that qualified it is always a way; d == 0 peaks sit on a
# road. Roads and ways cover both and drop 12M segments per city chunk.
WALKABLE = {"road", "way"}

PEAK_DIR = f"{WORK}/peaks"
DRIVE_DIR = f"{WORK}/drive"
NEAR_M = 400.0          # only segments this close to a peak can win

# A snap further than this is not a refinement, it is a confession.
#
# The move should be sub-cell: the way is burned into the peak's own 100 m cell, so the
# true line is within about 70 m of the cell centre, and 150 m is 1.5 cells of slack.
# Anything beyond that means no walkable way was in the cell at all, and the peak
# qualified on something else in ACCESS - a building outline, a railway, a power line.
# Measured on the Sydney chunk: 2,868 such peaks, and 2,863 of them have d == 0. They
# are degenerate city points that were never standable, so they are DROPPED rather than
# dragged to a road several hundred metres away, which would keep a distance measured
# somewhere the answer no longer is.
MAX_SNAP_M = 150.0
R = 6371000.0


def segments(lon, lat, off, want):
    """Endpoint arrays for the wanted features, joins between features removed."""
    keep = np.ones(len(lon) - 1, bool)
    keep[off[1:-1] - 1] = False
    owner = np.zeros(len(lon) - 1, np.int64)
    owner[off[1:-1] - 1] = 1
    keep &= want[np.cumsum(owner)]
    return (lon[:-1][keep], lat[:-1][keep], lon[1:][keep], lat[1:][keep])


def snap_points(plat, plon, seg, cell_m=NEAR_M):
    """Nearest point on any segment, for every peak. Returns snapped lat/lon."""
    x0, y0, x1, y1 = seg
    if len(x0) == 0:
        return plat.copy(), plon.copy()
    lat0 = float(np.mean(plat))
    mlat = np.pi * R / 180.0
    mlon = mlat * np.cos(np.radians(lat0))

    # Coarse hash so each peak only sees segments in its own neighbourhood. Ways are
    # short, so a midpoint key is enough; this is what keeps it linear instead of
    # peaks x segments.
    mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    kx = lambda a: np.floor(a * mlon / cell_m).astype(np.int64)
    ky = lambda b: np.floor(b * mlat / cell_m).astype(np.int64)
    key = lambda a, b: kx(a) * 4_000_003 + ky(b)
    skey = key(mx, my)

    # PREFILTER, and it is the difference between 46 minutes and one. A chunk holds
    # ~13M walkable segments and ~10k peaks, so barely half a percent of segments can
    # possibly be nearest to anything. Building the sorted index over the whole set and
    # searching it per peak costs far more than discarding it once, vectorised.
    pk = np.unique(np.concatenate([
        (kx(plon) + dx) * 4_000_003 + (ky(plat) + dy)
        for dx in (-1, 0, 1) for dy in (-1, 0, 1)]))
    near = np.isin(skey, pk)
    x0, y0, x1, y1 = x0[near], y0[near], x1[near], y1[near]
    skey = skey[near]
    if len(x0) == 0:
        return plat.copy(), plon.copy()
    order = np.argsort(skey)
    skey_s = skey[order]

    out_lat, out_lon = plat.copy(), plon.copy()
    for i in range(len(plat)):
        pa, po = plat[i], plon[i]
        bx0 = int(np.floor(po * mlon / cell_m)); by0 = int(np.floor(pa * mlat / cell_m))
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                k = (bx0 + dx) * 4_000_003 + (by0 + dy)
                lo = np.searchsorted(skey_s, k, "left")
                hi = np.searchsorted(skey_s, k, "right")
                if hi > lo:
                    cand.append(order[lo:hi])
        if not cand:
            continue
        idx = np.concatenate(cand)
        ax = (x0[idx] - po) * mlon; ay = (y0[idx] - pa) * mlat
        bx = (x1[idx] - po) * mlon; by = (y1[idx] - pa) * mlat
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = np.clip(np.where(L2 > 0, -(ax * dx + ay * dy) / np.maximum(L2, 1e-9), 0.0),
                    0.0, 1.0)
        cx, cy = ax + t * dx, ay + t * dy
        j = int(np.argmin(cx * cx + cy * cy))
        out_lon[i] = po + cx[j] / mlon
        out_lat[i] = pa + cy[j] / mlat
    return out_lat, out_lon


def run(peak_dir, on_set, snap_access):
    cfg = json.load(open(f"{WORK}/chunks.json"))
    files = sorted(f for f in os.listdir(peak_dir) if f.endswith(".npy"))
    t0, moved, total, worst, dropped = time.time(), [], 0, 0.0, 0
    for f in files:
        i = int(f[1:4])
        rows = np.load(f"{peak_dir}/{f}")
        ch = read_chunk(f"{WORK}/c{i:03d}.bin")
        if ch is None or not len(rows):
            continue
        lon, lat, off, kind = ch
        seg = segments(lon, lat, off, np.isin(kind, list(on_set)))
        nlat, nlon = snap_points(rows[:, 0], rows[:, 1], seg)
        d = np.hypot((nlat - rows[:, 0]) * 111195,
                     (nlon - rows[:, 1]) * 111195 * np.cos(np.radians(rows[:, 0])))
        keep = d <= MAX_SNAP_M
        dropped += int((~keep).sum())
        rows, nlat, nlon, d = rows[keep], nlat[keep], nlon[keep], d[keep]
        if not len(rows):
            continue
        rows[:, 0], rows[:, 1] = nlat, nlon
        if snap_access:
            # The drop-off is where you leave the CAR, so it belongs on a road. The
            # access point came from a transform to ANYTHING, which can land it on a
            # building outline - not somewhere Google Maps will navigate to.
            aseg = segments(lon, lat, off, kind == "road")
            alat, alon = snap_points(rows[:, 4], rows[:, 5], aseg)
            rows[:, 4], rows[:, 5] = alat, alon
        else:
            # Drive-only peaks park AT the spot, so the access point has to follow it.
            # Leaving it behind would put the drop-off a cell away from the answer and
            # draw a walk leg that does not exist.
            rows[:, 4], rows[:, 5] = rows[:, 0], rows[:, 1]
        np.save(f"{peak_dir}/{f}", rows)
        moved.append(d); total += len(rows); worst = max(worst, float(d.max()))
        print(f"  chunk {i:3d} {len(rows):6,} peaks, moved {d.mean():5.1f} m mean "
              f"({time.time()-t0:.0f}s)", flush=True)
    allm = np.concatenate(moved) if moved else np.array([0.0])
    print(f"  {total:,} peaks snapped: mean {allm.mean():.1f} m, "
          f"median {np.median(allm):.1f} m, worst {worst:.0f} m")
    print(f"  {dropped:,} dropped - no walkable way within {MAX_SNAP_M:.0f} m, so they "
          f"were never on the network the build claims")


if __name__ == "__main__":
    print("main peaks -> nearest access way (track, path, footway, road)")
    run(PEAK_DIR, WALKABLE, True)
    if os.path.isdir(DRIVE_DIR):
        print("drive peaks -> nearest road")
        run(DRIVE_DIR, {"road"}, False)
