"""Fit the graph's speed model against routed times, rather than against a hull.

The first calibration compared the graph to openrouteservice isochrones and found it wrong
in both directions - driving too fast, walking and riding too slow. It also showed that
the isochrone is the wrong target: its hull demonstrably over-reaches (a 60 minute walk
from the Sydney CBD contains Watsons Bay, an 11 km road trip), so matching it would mean
reproducing its errors.

Routed point-to-point times are a real target. The app already fetches them from the
public Valhalla server for its verify step, one pair at a time and free, so the same
source can supply a training set.

WHAT IS FITTED, and why it is this and not per-class speeds. Twenty-one classes against a
few hundred observations is underdetermined, so the classes are grouped into four buckets
that behave differently in traffic, and each bucket gets one multiplier. A per-junction
penalty is fitted alongside, because the single largest error in the first calibration was
driving with free-flow speeds and no cost for intersections - ORS.md already recorded that
a fitted AVERAGE for driving was 22 km/h against a 100 km/h motorway, and the gap between
those two numbers is almost entirely stopping.

    time = sum over buckets (metres / (speed * multiplier)) + penalty * junctions

Five parameters per mode, fitted by minimising squared RELATIVE error - relative because a
90 second error matters on a 10 minute walk and not on a 4 hour drive.

    python build/fit_speeds.py [n_pairs_per_mode]

Prints the fitted parameters and the before/after error. It does NOT write them into
graph.py: read them, sanity-check them against what a car actually does, and paste them
in deliberately.

Politeness: the Valhalla instance is a community server run by the OSM project. Requests
are serialised with a delay, and the sample is deliberately small.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, "build")
from graph import CLASSES, SPEEDS

VALHALLA = "https://valhalla1.openstreetmap.de/route"
COSTING = {"foot": "pedestrian", "bike": "bicycle", "car": "auto"}
DELAY_S = 1.1                      # one request a second, and not in parallel
KLAT = 111195.0

# Four buckets, because they behave differently once traffic and stopping are in play.
BUCKETS = {
    "motorway": ("motorway", "motorway_link", "trunk", "trunk_link"),
    "arterial": ("primary", "primary_link", "secondary", "secondary_link"),
    "local":    ("tertiary", "tertiary_link", "unclassified", "residential",
                 "living_street", "service"),
    "offroad":  ("pedestrian", "footway", "path", "track", "cycleway", "bridleway",
                 "steps"),
}
BNAMES = list(BUCKETS)
BUCKET_OF = {}
for bi, bn in enumerate(BNAMES):
    for c in BUCKETS[bn]:
        BUCKET_OF[c] = bi


def routed_minutes(mode, a, b):
    q = {"locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
         "costing": COSTING[mode]}
    url = VALHALLA + "?json=" + urllib.parse.quote(json.dumps(q))
    req = urllib.request.Request(url, headers={
        "User-Agent": "woop-woop calibration (charlietrenorden.com)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return d["trip"]["summary"]["time"] / 60.0


def mode_graph(g, mode, centre, radius_m):
    """The mode's sub-graph near a centre, with the per-edge bucket and length kept."""
    lat, lon = g["lat"], g["lon"]
    a, b, cls, length, oneway = g["a"], g["b"], g["cls"], g["length"], g["oneway"]
    ok = np.array([mode in SPEEDS[c] for c in CLASSES])
    base = np.array([SPEEDS[c].get(mode, 0.0) for c in CLASSES]) * 1000.0 / 3600.0
    bucket = np.array([BUCKET_OF[c] for c in CLASSES])
    klon = KLAT * np.cos(np.radians(centre[0]))
    d = np.hypot((lat - centre[0]) * KLAT, (lon - centre[1]) * klon)
    near = d < radius_m
    em = ok[cls] & near[a] & near[b]
    if not em.any():
        return None
    keep = np.zeros(len(lat), bool)
    keep[a[em]] = True
    keep[b[em]] = True
    remap = np.full(len(lat), -1, np.int64)
    remap[keep] = np.arange(int(keep.sum()))
    ai, bi = remap[a[em]], remap[b[em]]
    return {"n": int(keep.sum()), "ai": ai, "bi": bi,
            "len": length[em].astype(np.float64),
            "spd": base[cls[em]], "bkt": bucket[cls[em]],
            "two": ~oneway[em], "lat": lat[keep], "lon": lon[keep]}


def build_matrix(sub):
    """The cost matrix and an edge lookup, built ONCE per mode.

    The first version rebuilt a dict of two million hops inside the per-pair function,
    which made a forty-pair sample take longer than the extraction it was calibrating.
    """
    cost = sub["len"] / np.maximum(sub["spd"], 1e-6)
    two = sub["two"]
    rows = np.concatenate([sub["ai"], sub["bi"][two]])
    cols = np.concatenate([sub["bi"], sub["ai"][two]])
    vals = np.concatenate([cost, cost[two]])
    eid = np.concatenate([np.arange(len(cost)), np.nonzero(two)[0]])
    m = csr_matrix((vals, (rows, cols)), shape=(sub["n"], sub["n"]))
    # Hop -> edge, as one int64 key so the lookup is a sorted search rather than a dict.
    key = rows.astype(np.int64) * sub["n"] + cols.astype(np.int64)
    order = np.argsort(key)
    return m, key[order], eid[order]


def shortest(sub, mat, src, dst):
    """Metres per bucket and junctions crossed, on the graph's own shortest path."""
    m, key_s, eid_s = mat
    secs, pred = dijkstra(m, indices=src, directed=True, return_predecessors=True)
    if not np.isfinite(secs[dst]):
        return None
    metres = np.zeros(len(BNAMES))
    hops = 0
    cur = int(dst)
    while cur != src:
        p = int(pred[cur])
        if p < 0:
            return None
        k = p * sub["n"] + cur
        i = np.searchsorted(key_s, k)
        if i >= len(key_s) or key_s[i] != k:
            return None
        e = int(eid_s[i])
        metres[sub["bkt"][e]] += sub["len"][e]
        hops += 1
        cur = p
    return metres, hops, secs[dst] / 60.0


def sample_pairs(sub, n, min_km, max_km, rng):
    klon = KLAT * np.cos(np.radians(float(sub["lat"].mean())))
    out = []
    tries = 0
    while len(out) < n and tries < n * 40:
        tries += 1
        i = int(rng.integers(sub["n"]))
        d = np.hypot((sub["lat"] - sub["lat"][i]) * KLAT,
                     (sub["lon"] - sub["lon"][i]) * klon)
        ok = np.nonzero((d > min_km * 1000) & (d < max_km * 1000))[0]
        if not len(ok):
            continue
        out.append((i, int(rng.choice(ok))))
    return out


def main(per_mode=40):
    g = np.load("data/au/graph.npz")
    rng = np.random.default_rng(7)
    print(f"buckets: {', '.join(BNAMES)}")
    for mode, centre, radius, lo, hi in (
            ("foot", (-33.8688, 151.2093), 12000, 0.5, 5),
            ("bike", (-33.8688, 151.2093), 30000, 1, 15),
            ("car",  (-33.8688, 151.2093), 60000, 3, 40)):
        sub = mode_graph(g, mode, centre, radius)
        if sub is None:
            print(f"\n=== {mode} === no graph")
            continue
        mat = build_matrix(sub)
        pairs = sample_pairs(sub, per_mode, lo, hi, rng)
        rows, target, raw = [], [], []
        print(f"\n=== {mode} ===  {sub['n']:,} nodes, sampling {len(pairs)} pairs")
        for (i, j) in pairs:
            r = shortest(sub, mat, i, j)
            if r is None:
                continue
            metres, hops, mine = r
            try:
                truth = routed_minutes(mode, (sub["lat"][i], sub["lon"][i]),
                                       (sub["lat"][j], sub["lon"][j]))
            except (urllib.error.URLError, KeyError, TimeoutError):
                time.sleep(DELAY_S)
                continue
            time.sleep(DELAY_S)
            if truth <= 0.5:
                continue
            rows.append(np.concatenate([metres, [hops]]))
            target.append(truth)
            raw.append(mine)
        if len(rows) < 8:
            print(f"  only {len(rows)} usable pairs, not enough to fit")
            continue
        A = np.array(rows)
        y = np.array(target)
        base_spd = np.array([np.median([SPEEDS[c][mode] for c in BUCKETS[bn]
                                        if mode in SPEEDS[c]] or [1])
                             for bn in BNAMES]) * 1000.0 / 3600.0

        def resid(p):
            mult = np.exp(p[:len(BNAMES)])
            pen = np.exp(p[-1])
            secs = (A[:, :len(BNAMES)] / np.maximum(base_spd * mult, 1e-6)).sum(axis=1)
            secs = secs + pen * A[:, -1]
            return (secs / 60.0 - y) / y

        # Multipliers are bounded to a physically defensible band, and the junction
        # penalty to something a car could actually spend at an intersection. Left free,
        # the fit put residential streets at 68 km/h and a bicycle at 18 km/h on a
        # footpath: it was absorbing the difference between MY shortest path and
        # Valhalla's into the speed table, which is not what the speed table means.
        p0 = np.zeros(len(BNAMES) + 1)
        p0[-1] = np.log(2.0)
        lo_b = np.concatenate([np.full(len(BNAMES), np.log(0.55)), [np.log(0.2)]])
        hi_b = np.concatenate([np.full(len(BNAMES), np.log(1.45)), [np.log(25.0)]])
        fit = least_squares(resid, p0, bounds=(lo_b, hi_b), method="trf")
        mult = np.exp(fit.x[:len(BNAMES)])
        pen = float(np.exp(fit.x[-1]))
        # Both sides are the BUCKET model, differing only in the parameters. The first
        # version compared the per-class model against the per-bucket one, so the loss
        # from bucketing 21 classes into 4 was charged to the fit - which is how walking
        # appeared to get WORSE after fitting, 12.3% to 15.5%. It had not.
        before = np.abs(resid(p0))
        after = np.abs(resid(fit.x))
        perclass = np.abs((np.array(raw) - y) / y)
        print(f"  {len(y)} pairs against routed times")
        print(f"  mean |relative error|, bucket model: {before.mean()*100:5.1f}% "
              f"unfitted -> {after.mean()*100:5.1f}% fitted")
        print(f"  (the per-class model, for reference: {perclass.mean()*100:5.1f}%)")
        for bn, m0, mu in zip(BNAMES, base_spd, mult):
            print(f"    {bn:9} base {m0*3.6:5.1f} km/h  x{mu:5.2f}  "
                  f"-> {m0*3.6*mu:5.1f} km/h")
        print(f"    junction penalty: {pen:.1f} s each")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
