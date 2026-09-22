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

A KNOWN APPROXIMATION, and it is now MEASURED rather than flagged: the graph keeps
junctions and throws away the geometry between them, so a peak in the middle of a long
edge has no node of its own. Its cost is taken as its nearest junction plus the
straight-line run in. build/check_peak_nodes.py puts exact-cost coverage at 26.6% and the
median peak 590 m from a node, so the run-in is not an edge case, it is the common case.

What that does to each mode is the thing to read before blaming the speed model, and it
is not uniform: at p90 the run-in is 34 minutes on foot against a 60 minute budget, and
under 2 minutes by car. So the driving comparison below is about the speed model and the
walking one is mostly about the approximation. The header prints both so the table cannot
be read without them.

The fix is a build step giving each peak an edge and an offset along it - tiny data, and
it removes the problem rather than bounding it.
"""
import json
import sys

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

sys.path.insert(0, "build")
from graph import CLASSES, JUNCTION_PENALTY_S, SPEEDS, speed_kmh

ORIGIN = (-33.8688, 151.2093)          # Sydney CBD, the default view
BUDGETS = (15, 30, 45, 60)
KLAT = 111195.0


def local(lat, lon, a, b, cls, length, oneway, mode, radius_m):
    """The mode's sub-graph within a radius, as a cost matrix in seconds."""
    ok = np.array([mode in SPEEDS[c] for c in CLASSES])
    # FITTED speeds, not the raw table. build/fit_speeds.py fitted bucket multipliers and
    # a junction penalty against routed Valhalla times; building the matrix from raw
    # free-flow values meant this script judged a model the app does not use.
    spd = np.array([speed_kmh(c, mode) for c in CLASSES]) * 1000.0 / 3600.0
    klon = KLAT * np.cos(np.radians(ORIGIN[0]))
    d = np.hypot((lat - ORIGIN[0]) * KLAT, (lon - ORIGIN[1]) * klon)
    near = d < radius_m
    em = ok[cls] & near[a] & near[b]
    if not em.any():
        return None, None, None, None
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
    return (csr_matrix((vals, (rows, cols)), shape=(n, n)),
            lat[keep], lon[keep], remap)


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


PEAK_EDGES = None
try:
    _pe = np.load("data/au/peak_edges.npz")
    PEAK_EDGES = {k: _pe[k] for k in _pe.files}
except (FileNotFoundError, OSError):
    pass


def main():
    # Printed first, deliberately. A table of agreement percentages looks like a verdict
    # on the speed model, and for foot it is mostly a verdict on the run-in. Refusing to
    # print the table would be worse - the numbers are still worth seeing - so the caveat
    # goes ABOVE them where it cannot be scrolled past.
    from check_peak_nodes import load_peaks
    gq = np.load("data/au/graph.npz")
    qlat, qlon = load_peaks()
    kq = KLAT * np.cos(np.radians(float(qlat.mean())))
    dq, _ = cKDTree(np.column_stack((gq["lon"] * kq, gq["lat"] * KLAT))).query(
        np.column_stack((qlon * kq, qlat * KLAT)), workers=-1)
    print(f"peak costing: {(dq <= 15).mean() * 100:.1f}% of peaks have a node of their "
          f"own; the rest are charged")
    print(f"  a straight-line run in, median {np.median(dq):.0f} m, p90 "
          f"{np.percentile(dq, 90):.0f} m.")

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
        gm, glat, glon, remap = local(lat, lon, a, b, cls, length, oneway, mode, radius)
        if gm is None:
            print(f"\n=== {mode} === no local graph")
            continue
        gd = np.hypot((glat - ORIGIN[0]) * KLAT, (glon - ORIGIN[1]) * klon)
        src = int(np.argmin(gd))
        secs = dijkstra(gm, indices=src, directed=True)

        tree = cKDTree(np.column_stack((glon * klon, glat * KLAT)))
        pdist, pnode = tree.query(np.column_stack((plon * klon, plat * KLAT)))
        pcost = secs[pnode] + pdist / max(top * 1000 / 3600, 1e-6)

        # EXACT cost for any peak build/peak_edges.py placed on an edge: its time is the
        # better of the two junctions plus the distance ALONG the way, at that way's own
        # class speed. No straight-line term at all. Everything else keeps the run in,
        # and the count of each is printed, because a mixed measurement that does not say
        # how mixed it is reads as exact.
        exact = 0
        if PEAK_EDGES is not None:
            pe = PEAK_EDGES
            la_, lb_ = remap[pe["edge_a"]], remap[pe["edge_b"]]
            # The FITTED speed, matching what the app would use. The raw per-class table
            # is free-flow with no stopping cost, which is what made driving reach 2,811
            # peaks against ORS's 1,108 - a difference in the speed model being read as a
            # difference in geometry.
            v = np.array([speed_kmh(c, mode) for c in CLASSES]) / 3.6
            v = v[pe["cls"]]
            ok = (la_ >= 0) & (lb_ >= 0) & (v > 0)
            ok &= np.isfinite(secs[np.clip(la_, 0, len(secs) - 1)])
            # Matched on COORDINATE: peak_edges is keyed that way because an index into
            # peaks.bin does not survive a re-merge.
            pkey = (pe["lat_e5"].astype(np.int64) * 100_000_000
                    + pe["lon_e5"].astype(np.int64))
            here = (np.rint(plat * 1e5).astype(np.int64) * 100_000_000
                    + np.rint(plon * 1e5).astype(np.int64))
            order = np.argsort(here)
            pos = np.searchsorted(here[order], pkey)
            pos = np.clip(pos, 0, len(order) - 1)
            row = order[pos]
            ok &= here[row] == pkey
            if ok.any():
                ca = secs[la_[ok]] + pe["d_from_a"][ok] / v[ok]
                cb = secs[lb_[ok]] + pe["d_from_b"][ok] / v[ok]
                pcost[row[ok]] = np.minimum(ca, cb)
                exact = int(ok.sum())
        print(f"  exact-cost peaks in range: {exact:,} of {c:,} "
              f"({100.0 * exact / max(c, 1):.1f}%); the rest keep the run in")

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
