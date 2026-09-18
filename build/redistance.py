"""Recompute each peak's distance from its SNAPPED position, against real geometry.

Why this exists. `d` comes from a distance transform on a 100 m grid, so it is the
distance between CELL CENTRES and carries about +-70 m before anything else happens.
Then `build/snap.py` moves the peak up to 150 m onto real way geometry and leaves `d`
alone - a decision I made and justified with a comment claiming the move "stays inside
that cell", which is false for a 100 m cell. Measured 18/09/2026: a peak shipped as
"100 m from anything" sits 12.2 m from a road and 35 m from a building.

That is not only a wrong LABEL. The winner is chosen by ranking on this field, so a
corrupted d picks the wrong peak, and the city answers were being decided by
quantisation rather than by emptiness.

The search is local because the grid already brackets the answer: the true nearest
feature is within grid_d +- 220 m of the snapped point (70 m of EDT cell-centre error,
150 m of snap). So this walks cells outward from the peak and stops as soon as the next
ring cannot beat what it has - no global search over 25M city segments.

Above RECOMPUTE_MAX_M the correction is not worth the walk: a 220 m error on a 100 km
answer is noise, and those peaks are half a continent from a building, so the ring search
would cross hundreds of empty cells to prove it.

    python build/redistance.py            # rewrites data/au/peaks/*.npy and drive/*.npy
"""
import json, os, sys, time
import numpy as np
from scipy.spatial import cKDTree
sys.path.insert(0, "build")
from au import WORK, BUFFER, read_chunk
from raster import ANYTHING, BUILT

PEAK_DIR = f"{WORK}/peaks"
DRIVE_DIR = f"{WORK}/drive"
# 400 m, not 2 km, and the reason is where the error actually bites.
#
# The absolute error is roughly constant at +-220 m whatever d is, so it only MATTERS
# where d is small: at 12 m it is the entire answer, at 2 km it is 11% and at 15 km it is
# noise. Measured on the Sydney chunk, going to 2 km cost 18 minutes for that chunk alone
# - 23 hours for the continent - because a peak whose best is 2 km has to walk eleven
# rings of cells to prove nothing is nearer. A peak whose best is 12 m proves it in two.
RECOMPUTE_MAX_M = 400.0
STEP_M = 40.0             # points along a long span, for the shortlist
K_NEAREST = 48            # candidate segments per peak
R = 6371000.0


def _points(x0, y0, x1, y1, mlon, mlat, step_m):
    """Every segment's endpoints, plus interpolated points along anything long.

    The tree is built on POINTS but the answer must be a distance to a SEGMENT, so the
    tree is only used to shortlist. Endpoints alone would shortlist badly for a long
    span - a power line crossing close to a peak has both ends far away - so anything
    longer than a step gets points along it. Roads and building walls are tens of metres
    and contribute their two ends, which is most of the set and costs nothing extra.
    """
    dx = (x1 - x0) * mlon
    dy = (y1 - y0) * mlat
    n = np.maximum(2, np.ceil(np.sqrt(dx * dx + dy * dy) / step_m) + 1).astype(np.int64)
    owner = np.repeat(np.arange(len(n)), n)
    starts = np.concatenate(([0], np.cumsum(n)[:-1]))
    k = np.arange(len(owner)) - starts[owner]
    t = k / (n[owner] - 1)
    return owner, x0[owner] + (x1[owner] - x0[owner]) * t,            y0[owner] + (y1[owner] - y0[owner]) * t


def nearest(plat, plon, dist, seg):
    """Exact distance to the nearest segment, for every peak under the cutoff.

    A KD-tree shortlists K candidate segments per peak, then the exact point-to-segment
    distance is computed for all peaks and all candidates in one vectorised pass. The
    first version walked a grid of cells outward from each peak; it was correct to 9 m
    but took 28 ms per peak, because a 1.8 km box in inner Sydney holds several hundred
    thousand building-wall segments and every one of them was measured.
    """
    x0, y0, x1, y1 = seg
    out = dist.copy()
    todo = np.nonzero(dist <= RECOMPUTE_MAX_M)[0]
    if len(x0) == 0 or len(todo) == 0:
        return out
    lat0 = float(np.mean(plat))
    mlat = np.pi * R / 180.0
    mlon = mlat * np.cos(np.radians(lat0))

    owner, px, py = _points(x0, y0, x1, y1, mlon, mlat, STEP_M)
    tree = cKDTree(np.column_stack((px * mlon, py * mlat)))
    q = np.column_stack((plon[todo] * mlon, plat[todo] * mlat))
    k = min(K_NEAREST, len(px))
    _, idx = tree.query(q, k=k, workers=-1)
    if idx.ndim == 1:
        idx = idx[:, None]
    cand = owner[idx]                                    # (n_todo, k) segment indices

    # Exact point-to-segment, every peak against every one of its candidates at once.
    ax = (x0[cand] - plon[todo][:, None]) * mlon
    ay = (y0[cand] - plat[todo][:, None]) * mlat
    bx = (x1[cand] - plon[todo][:, None]) * mlon
    by = (y1[cand] - plat[todo][:, None]) * mlat
    ddx, ddy = bx - ax, by - ay
    L2 = ddx * ddx + ddy * ddy
    t = np.clip(np.where(L2 > 0, -(ax * ddx + ay * ddy) / np.maximum(L2, 1e-9), 0.0),
                0.0, 1.0)
    cx, cy = ax + t * ddx, ay + t * ddy
    out[todo] = np.sqrt(cx * cx + cy * cy).min(axis=1)
    return out


def segments(lon, lat, off, want):
    keep = np.ones(len(lon) - 1, bool)
    keep[off[1:-1] - 1] = False
    owner = np.zeros(len(lon) - 1, np.int64)
    owner[off[1:-1] - 1] = 1
    keep &= want[np.cumsum(owner)]
    return (lon[:-1][keep], lat[:-1][keep], lon[1:][keep], lat[1:][keep])


def run(peak_dir, target):
    cfg = json.load(open(f"{WORK}/chunks.json"))
    files = sorted(f for f in os.listdir(peak_dir) if f.endswith(".npy"))
    t0, moved, n_done = time.time(), [], 0
    for f in files:
        i = int(f[1:4])
        rows = np.load(f"{peak_dir}/{f}")
        ch = read_chunk(f"{WORK}/c{i:03d}.bin")
        if ch is None or not len(rows):
            continue
        lon, lat, off, kind = ch
        seg = segments(lon, lat, off, np.isin(kind, list(target)))
        before = rows[:, 2].copy()
        rows[:, 2] = nearest(rows[:, 0], rows[:, 1], before, seg)
        np.save(f"{peak_dir}/{f}", rows)
        ch_moved = rows[:, 2] - before
        touched = before <= RECOMPUTE_MAX_M
        moved.append(ch_moved[touched]); n_done += int(touched.sum())
        print(f"  chunk {i:3d} {len(rows):6,} peaks, {int(touched.sum()):6,} recomputed, "
              f"mean change {ch_moved[touched].mean() if touched.any() else 0:+7.1f} m "
              f"({time.time()-t0:.0f}s)", flush=True)
    a = np.concatenate(moved) if moved else np.array([0.0])
    print(f"  {n_done:,} distances recomputed: mean {a.mean():+.1f} m, "
          f"median {np.median(a):+.1f} m, "
          f"worst overstatement {a.min():+.0f} m, worst understatement {a.max():+.0f} m")


if __name__ == "__main__":
    print("main peaks -> exact distance to anything (roads, buildings, rail, power, aero)")
    run(PEAK_DIR, ANYTHING)
    if os.path.isdir(DRIVE_DIR):
        print("drive peaks -> exact distance to built things only (roads excluded)")
        run(DRIVE_DIR, BUILT)
