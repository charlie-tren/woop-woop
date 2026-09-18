"""Compare shipped-graph reachability against the openrouteservice isochrone.

Sizing said a client-side graph is POSSIBLE. This asks whether it is RIGHT, which is the
only question that decides anything. The comparison set is the thing it would replace:
real ORS isochrones, pulled from the Worker's 24 h edge cache so it costs no quota.

Two measurements, the second mattering far more than the first:

  peak agreement   which PEAKS each method calls reachable. This is what the app actually
                   does with an isochrone, so a disagreement here is a disagreement about
                   the product, not about geometry.
  the answer       the single peak each method would name. The only number a visitor sees,
                   and the one that has to match.

    python build/calibrate_graph.py

A KNOWN APPROXIMATION, flagged because it moves the result: the graph keeps junctions and
throws away the geometry between them, so a peak in the middle of a long edge has no node
of its own. Its cost is taken as its nearest junction plus the straight-line run in. On a
fire trail kilometres long that is wrong, and the fix is a build step giving each peak an
edge and an offset along it - tiny data, and it removes the problem rather than bounding
it. Read any disagreement below with that in mind before blaming the speed model.
"""
import json
import sys

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

sys.path.insert(0, "build")
from graph import CLASSES, SPEEDS

ORIGIN = (-33.8688, 151.2093)          # Sydney CBD, the default view
BUDGETS = (15, 30, 45, 60)
KLAT = 111195.0


def local(lat, lon, a, b, cls, length, oneway, mode, radius_m):
    """The mode's sub-graph within a radius, as a cost matrix in seconds."""
    ok = np.array([mode in SPEEDS[c] for c in CLASSES])
    spd = np.array([SPEEDS[c].get(mode, 0.0) for c in CLASSES]) * 1000.0 / 3600.0
    klon = KLAT * np.cos(np.radians(ORIGIN[0]))
    d = np.hypot((lat - ORIGIN[0]) * KLAT, (lon - ORIGIN[1]) * klon)
    near = d < radius_m
    em = ok[cls] & near[a] & near[b]
    if not em.any():
        return None, None, None
    keep = np.zeros(len(lat), bool)
    keep[a[em]] = True
    keep[b[em]] = True
    remap = np.full(len(lat), -1, np.int64)
    remap[keep] = np.arange(int(keep.sum()))
    cost = length[em] / np.maximum(spd[cls[em]], 1e-6)
    ai, bi = remap[a[em]], remap[b[em]]
    two = ~oneway[em]
    rows = np.concatenate([ai, bi[two]])
    cols = np.concatenate([bi, ai[two]])
    vals = np.concatenate([cost, cost[two]])
    n = int(keep.sum())
    return csr_matrix((vals, (rows, cols)), shape=(n, n)), lat[keep], lon[keep]


def inside_ring(plat, plon, ring):
    """Ray casting, vectorised over points. Ring is [lon, lat] pairs."""
    r = np.asarray(ring)
    x, y = r[:, 0], r[:, 1]
    xj, yj = np.roll(x, 1), np.roll(y, 1)
    inside = np.zeros(len(plat), bool)
    for i in range(len(x)):
        cond = (y[i] > plat) != (yj[i] > plat)
        if not cond.any():
            continue
        xint = (xj[i] - x[i]) * (plat - y[i]) / (yj[i] - y[i]) + x[i]
        inside ^= cond & (plon < xint)
    return inside


def main():
    g = np.load("data/au/graph.npz")
    lat, lon = g["lat"], g["lon"]
    a, b, cls = g["a"], g["b"], g["cls"]
    length, oneway = g["length"], g["oneway"]

    iso = json.load(open("data/iso_cache.json"))
    meta = json.load(open("docs/data/peaks.json"))
    buf = np.fromfile("docs/data/peaks.bin", dtype=np.uint8)
    c, s = meta["count"], meta["coord_scale"]
    plat = np.frombuffer(buf, "<i4", c, 0) / s
    plon = np.frombuffer(buf, "<i4", c, 4 * c) / s
    pd = np.frombuffer(buf, "<u2", c, 8 * c).astype(float) * meta["dist_scale_m"]

    klon = KLAT * np.cos(np.radians(ORIGIN[0]))
    for mode in ("foot", "bike", "car"):
        top = max(SPEEDS[k].get(mode, 0) for k in CLASSES)
        radius = top * 1000 / 3600 * max(BUDGETS) * 60 * 1.2
        gm, glat, glon = local(lat, lon, a, b, cls, length, oneway, mode, radius)
        if gm is None:
            print(f"\n=== {mode} === no local graph")
            continue
        gd = np.hypot((glat - ORIGIN[0]) * KLAT, (glon - ORIGIN[1]) * klon)
        src = int(np.argmin(gd))
        secs = dijkstra(gm, indices=src, directed=True)

        tree = cKDTree(np.column_stack((glon * klon, glat * KLAT)))
        pdist, pnode = tree.query(np.column_stack((plon * klon, plat * KLAT)))
        pcost = secs[pnode] + pdist / max(top * 1000 / 3600, 1e-6)

        print(f"\n=== {mode} ===  graph: {gm.shape[0]:,} nodes within {radius/1000:.0f} km")
        print(f"  {'budget':>8} {'ORS':>8} {'graph':>8} {'both':>8} {'agree':>7}"
              f"  {'ORS answer':>11} {'graph answer':>13}")
        for mins in BUDGETS:
            key = f"{mode}|{mins}"
            if key not in iso:
                continue
            ring = iso[key][0]
            r = np.asarray(ring)
            box = (plat >= r[:, 1].min()) & (plat <= r[:, 1].max()) & \
                  (plon >= r[:, 0].min()) & (plon <= r[:, 0].max())
            ors = np.zeros(c, bool)
            if box.any():
                ors[box] = inside_ring(plat[box], plon[box], ring)
            gr = pcost <= mins * 60
            both = int((ors & gr).sum())
            union = int((ors | gr).sum())
            ao = pd[ors].max() if ors.any() else 0.0
            ag = pd[gr].max() if gr.any() else 0.0
            print(f"  {mins:6} min {int(ors.sum()):8,} {int(gr.sum()):8,} {both:8,} "
                  f"{100 * both / max(union, 1):6.0f}% {ao:10.0f} m {ag:12.0f} m")


if __name__ == "__main__":
    main()
