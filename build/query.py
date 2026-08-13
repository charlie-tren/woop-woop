"""Answer the actual question: given where you are and how far you'll go, where is the
emptiest point you can reach - and how do you get to it?

Two things this returns that a plain maximum does not:

1. **The access point.** The distance transform's nearest-feature index tells us which
   road cell is closest to the winner, so "drive here, then walk 3.2 km west" comes out
   of the same computation as the answer itself. Without it the result is a pin in a
   swamp with no way in.
2. **A reachable mask.** The naive maximum over a region is routinely degenerate - the
   German prior art's headline answer was a road-free island, which is why they had to
   add a second marker for somewhere you could actually stand. Reachability is a hard
   filter here, not a display option.
"""
import numpy as np
from scipy import ndimage


def solve(dist, grid, reachable=None, exclude_water=None, top_n=5):
    """Return the best reachable cells, furthest-from-anything first."""
    field = dist.copy()
    if reachable is not None:
        field[~reachable] = -1
    if exclude_water is not None:
        field[exclude_water] = -1

    flat = np.argpartition(field.ravel(), -top_n)[-top_n:]
    flat = flat[np.argsort(-field.ravel()[flat])]
    out = []
    for f in flat:
        iy, ix = np.unravel_index(f, field.shape)
        if field[iy, ix] < 0:
            continue
        lon, lat = grid.to_lonlat(ix, iy)
        out.append({"lat": float(lat), "lon": float(lon),
                    "dist_m": float(dist[iy, ix]), "px": (int(ix), int(iy))})
    return out


def access_points(occ, grid, targets):
    """For each target cell, the nearest occupied (road/building) cell - the way in.

    Computed with a single distance transform over the whole grid rather than a search
    per target, because we want this for every candidate and the transform is the same
    cost for one as for all of them.
    """
    _, (iy, ix) = ndimage.distance_transform_edt(~occ, return_indices=True)
    for t in targets:
        x, y = t["px"]
        ay, ax = int(iy[y, x]), int(ix[y, x])
        alon, alat = grid.to_lonlat(ax, ay)
        t["access"] = {"lat": float(alat), "lon": float(alon)}
    return targets
