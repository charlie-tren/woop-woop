"""Extract a routable graph from the .osm.pbf, so reachability can be computed HERE.

The point is to delete a dependency. Today the reachable set comes from openrouteservice
at RUNTIME: a visitor who moves the start point or the slider spends four calls against a
500-a-day quota, and what comes back is a generalised hull that spans water and
over-reaches - a 60 minute WALK from the CBD contains Watsons Bay, an 11 km road trip.

A graph shipped as static tiles has no quota, no key and no Worker. It also has no hull,
which is the part that matters: reachability stops being "is this point inside a polygon"
and becomes "is this peak's node within the time budget". Every peak sits ON the network
by construction (au_build.py keeps reach == 0), so that substitution is exact rather than
an approximation of an approximation.

Two passes over the extract:

  1. count how many ways reference each node. A node referenced by two or more ways is a
     junction, and so is every way's first and last node. The counting collects refs into
     one int64 array and calls np.unique - a Python dict of eight million ids costs
     several hundred MB, and an array of fifteen million costs 120.
  2. walk each way and emit one edge per junction-to-junction run, carrying the summed
     length and the class. The vertices in between are geometry, not topology, and they
     are what makes the raw data too heavy to ship: a 50 km patch of Sydney holds 5.15M
     of them, about 93 MB.

    python build/graph.py [extract.osm.pbf]

Writes data/au/graph.npz and prints what shipping it would cost, including the worst
single tile - which is the number that decides whether this design is viable at all.

THE SPEED MODEL BELOW IS A FIRST CUT and must be calibrated before it replaces anything.
ORS.md records 102 real isochrones with their measured reach, and the Worker's edge cache
still holds real ones for several origins, so there is a ready-made comparison set. Do
not ship this as the reachability engine on the strength of it looking reasonable.
"""
import os
import sys
import time

import numpy as np
import osmium

# Per-class speeds in km/h. A mode missing from a class may not use that class at all -
# which is why motorway has no foot entry and footway has no car entry. Deliberately
# coarse: this decides "can I be there within T", not a turn-by-turn ETA.
SPEEDS = {
    "motorway":       {"car": 100},
    "motorway_link":  {"car": 80},
    "trunk":          {"car": 90},
    "trunk_link":     {"car": 70},
    "primary":        {"car": 70, "bike": 15, "foot": 5},
    "primary_link":   {"car": 60, "bike": 15, "foot": 5},
    "secondary":      {"car": 60, "bike": 15, "foot": 5},
    "secondary_link": {"car": 55, "bike": 15, "foot": 5},
    "tertiary":       {"car": 50, "bike": 16, "foot": 5},
    "tertiary_link":  {"car": 45, "bike": 16, "foot": 5},
    "unclassified":   {"car": 50, "bike": 16, "foot": 5},
    "residential":    {"car": 40, "bike": 16, "foot": 5},
    "living_street":  {"car": 15, "bike": 14, "foot": 5},
    "service":        {"car": 20, "bike": 14, "foot": 5},
    "pedestrian":     {"bike": 8,  "foot": 5},
    "footway":        {"bike": 6,  "foot": 5},
    "path":           {"bike": 10, "foot": 4.5},
    "track":          {"car": 25, "bike": 10, "foot": 4.5},
    "cycleway":       {"bike": 18, "foot": 5},
    "bridleway":      {"bike": 8,  "foot": 4.5},
    "steps":          {"bike": 2,  "foot": 2},
}
# FITTED against routed times, refit 21/09/2026. build/fit_speeds.py, 47-49 pairs per
# mode
# around Sydney, against the public Valhalla server - the same engine the page already
# uses to verify its answers, so fitting to it makes the app self-consistent.
#
# Mean absolute relative error after fitting: foot 11.6%, bike 8.0%, car 6.3%. Foot is
# the one mode where the per-class table was already better than the fit (9.9%), because
# a pedestrian does 5 km/h on everything and bucketing can only lose information.
#
# The multiplier applies to every class in its bucket. A bucket left at 1.00 is one where
# the fit hit its bound, and a parameter at a bound is not an estimate - it means the fit
# wanted to keep going and something else is wrong. Both cases here are offroad, which
# few sampled routes used, so it is unidentified rather than measured and stays at base.
#
# What this corrected: walking was NOT too slow. The first calibration said the graph
# reached half as many peaks on foot as ORS did, and I read that as a speed problem. The
# fit puts walking multipliers at 1.02, 0.97 and 1.14 - 5 km/h was already 5 km/h. The
# gap was the calibration script charging each peak a straight-line run in from its
# nearest junction, because only 34% of peaks have a node of their own.
FITTED = {
    "foot": {"motorway": 1.00, "arterial": 1.02, "local": 0.96,
             "cycleish": 0.91, "footonly": 1.19},
    "bike": {"motorway": 1.00, "arterial": 1.15, "local": 1.10,
             "cycleish": 1.00, "footonly": 1.00},
    "car":  {"motorway": 1.15, "arterial": 1.10, "local": 0.84,
             "cycleish": 1.00, "footonly": 1.00},
}

# Seconds spent at each junction crossed. Free-flow speeds with no stopping cost was the
# single largest error in the first calibration; ORS.md had already recorded a fitted
# AVERAGE of 22 km/h for driving against free-flow figures three to four times that, and
# most of that gap is stopping rather than cruising slower.
JUNCTION_PENALTY_S = {"foot": 0.2, "bike": 0.2, "car": 2.8}

BUCKET = {
    "motorway": ("motorway", "motorway_link", "trunk", "trunk_link"),
    "arterial": ("primary", "primary_link", "secondary", "secondary_link"),
    "local":    ("tertiary", "tertiary_link", "unclassified", "residential",
                 "living_street", "service"),
    # Split out of one "offroad" bucket on 21/09/2026. Lumped together, the median was
    # 8 km/h, so a bicycle on a CYCLEWAY was as slow as one on steps - and the bucket's
    # multiplier hit its bound for bike and car, meaning the fit wanted it faster and
    # could not get there. A parameter at a bound is not an estimate.
    "cycleish": ("cycleway", "path", "track"),
    "footonly": ("pedestrian", "footway", "bridleway", "steps"),
}
BUCKET_OF = {c: b for b, cs in BUCKET.items() for c in cs}


def _bucket_median(bucket, mode):
    vals = [SPEEDS[c][mode] for c in BUCKET[bucket] if mode in SPEEDS[c]]
    return float(np.median(vals)) if vals else 0.0


def speed_kmh(cls_name, mode):
    """The fitted speed for a class, or 0 if the mode may not use it.

    The BUCKET MEDIAN times the multiplier, not the class's own base times the
    multiplier. The multipliers were fitted against bucket medians, so applying them to
    per-class values does not reproduce the model that was measured - it produced 109 km/h
    on a motorway, above both the fitted 92 and the speed limit.

    The cost is granularity: every class in a bucket now shares a speed, so residential
    and tertiary are the same. The fit says that is affordable - for driving the bucket
    model beat the per-class one outright, 10.4% against 40.1% before fitting, because
    bucket medians sit closer to real speeds than free-flow class values do.
    """
    if mode not in SPEEDS[cls_name]:
        return 0.0
    b = BUCKET_OF[cls_name]
    return _bucket_median(b, mode) * FITTED[mode][b]


CLASSES = sorted(SPEEDS)
CLASS_ID = {c: i for i, c in enumerate(CLASSES)}
R = 6371000.0
MPD = np.pi * R / 180.0          # metres per degree of latitude


# How close a way vertex has to be to a peak to BE that peak's node.
#
# Every peak was snapped onto real way geometry by build/snap.py, so it sits on a vertex
# or within a metre or two of one. Marking those vertices as junctions gives each peak a
# node of its own, which is what makes reachability exact: without it a peak in the middle
# of a long edge has no node and is charged to its nearest junction plus a straight-line
# run in. On a fire trail that is the whole answer. Measured on the first calibration,
# walking agreed with ORS on 50% of peaks and this was a large part of why.
PEAK_SNAP_M = 15.0


class RefCounter(osmium.SimpleHandler):
    """Pass one: node refs on routable ways, their positions, and the endpoints.

    Positions are collected here rather than in a third pass, so the peak test can be one
    batched query over every vertex instead of 37 million single ones. Costs about 600 MB
    of arrays, which is the cheaper end of that trade.
    """

    def __init__(self):
        super().__init__()
        self.refs = []
        self.lats = []
        self.lons = []
        self.ends = []
        self.ways = 0

    def way(self, w):
        if w.tags.get("highway") not in SPEEDS:
            return
        ids, la, lo = [], [], []
        for n in w.nodes:
            if not n.location.valid():
                continue
            ids.append(n.ref)
            la.append(n.location.lat)
            lo.append(n.location.lon)
        if len(ids) < 2:
            return
        self.ways += 1
        self.refs.append(np.array(ids, dtype=np.int64))
        self.lats.append(np.array(la, dtype=np.float32))
        self.lons.append(np.array(lo, dtype=np.float32))
        self.ends.append(ids[0])
        self.ends.append(ids[-1])


def junctions(pbf):
    t0 = time.time()
    h = RefCounter()
    h.apply_file(pbf, locations=True)
    refs = np.concatenate(h.refs)
    vlat = np.concatenate(h.lats)
    vlon = np.concatenate(h.lons)
    print(f"  pass 1: {h.ways:,} routable ways, {len(refs):,} node refs "
          f"({time.time() - t0:.0f}s)", flush=True)
    uniq, counts = np.unique(refs, return_counts=True)
    shared = uniq[counts >= 2]
    ends = np.unique(np.array(h.ends, dtype=np.int64))

    # Every peak gets its own node, found with one batched nearest-neighbour query.
    from scipy.spatial import cKDTree
    import json
    meta = json.load(open("docs/data/peaks.json"))
    pbuf = np.fromfile("docs/data/peaks.bin", dtype=np.uint8)
    pn, ps = meta["count"], meta["coord_scale"]
    plat = np.frombuffer(pbuf, "<i4", pn, 0) / ps
    plon = np.frombuffer(pbuf, "<i4", pn, 4 * pn) / ps
    klon = MPD * np.cos(np.radians(float(plat.mean())))
    tree = cKDTree(np.column_stack((plon * klon, plat * MPD)))
    d, _ = tree.query(np.column_stack((vlon.astype(np.float64) * klon,
                                       vlat.astype(np.float64) * MPD)), workers=-1)
    onpeak = np.unique(refs[d <= PEAK_SNAP_M])

    junc = np.union1d(np.union1d(shared, ends), onpeak)
    print(f"  {len(uniq):,} distinct nodes; {len(shared):,} used by 2+ ways, "
          f"{len(ends):,} endpoints, {len(onpeak):,} on peaks "
          f"-> {len(junc):,} junctions ({100 * len(junc) / len(uniq):.0f}% of nodes)")
    return junc


class EdgeBuilder(osmium.SimpleHandler):
    """Pass two: one edge per junction-to-junction run.

    Junction identity is a binary search into the sorted id array, and coordinates are
    written into a preallocated slot rather than a dict - both to keep this inside a
    couple of hundred MB rather than the better part of a gigabyte.
    """

    def __init__(self, junc):
        super().__init__()
        self.junc = junc
        self.lat = np.zeros(len(junc), dtype=np.float64)
        self.lon = np.zeros(len(junc), dtype=np.float64)
        self.have = np.zeros(len(junc), dtype=bool)
        self.a, self.b, self.length, self.cls, self.oneway = [], [], [], [], []

    def _index(self, ref):
        i = np.searchsorted(self.junc, ref)
        if i < len(self.junc) and self.junc[i] == ref:
            return int(i)
        return -1

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in SPEEDS:
            return
        ow = w.tags.get("oneway", "no") in ("yes", "true", "1", "-1")
        cid = CLASS_ID[hw]
        prev = -1
        run = 0.0
        plat = plon = None
        for n in w.nodes:
            if not n.location.valid():
                continue
            la, lo = n.location.lat, n.location.lon
            if plat is not None:
                dy = (la - plat) * MPD
                dx = (lo - plon) * MPD * np.cos(np.radians(la))
                run += float(np.hypot(dx, dy))
            plat, plon = la, lo
            j = self._index(n.ref)
            if j < 0:
                continue
            if not self.have[j]:
                self.lat[j] = la
                self.lon[j] = lo
                self.have[j] = True
            if prev >= 0 and run > 0.0:
                self.a.append(prev)
                self.b.append(j)
                self.length.append(run)
                self.cls.append(cid)
                self.oneway.append(ow)
            prev = j
            run = 0.0


def main(pbf="data/australia-latest.osm.pbf"):
    junc = junctions(pbf)
    t0 = time.time()
    eb = EdgeBuilder(junc)
    eb.apply_file(pbf, locations=True)
    a = np.array(eb.a, dtype=np.uint32)
    b = np.array(eb.b, dtype=np.uint32)
    length = np.array(eb.length, dtype=np.float32)
    cls = np.array(eb.cls, dtype=np.uint8)
    oneway = np.array(eb.oneway, dtype=bool)
    print(f"  pass 2: {len(a):,} edges, {int(eb.have.sum()):,} junctions located "
          f"({time.time() - t0:.0f}s)", flush=True)

    os.makedirs("data/au", exist_ok=True)
    np.savez_compressed("data/au/graph.npz", lat=eb.lat, lon=eb.lon, have=eb.have,
                        a=a, b=b, length=length, cls=cls, oneway=oneway,
                        classes=np.array(CLASSES))

    n, m = int(eb.have.sum()), len(a)
    # What SHIPPING it would cost: a node is two int32 of hundred-thousandths, an edge is
    # two uint32 endpoints, a uint16 of decametres and a byte carrying class and oneway.
    per_node, per_edge = 8, 11
    raw = n * per_node + m * per_edge
    print(f"\n  graph: {n:,} nodes, {m:,} edges ({m / max(n, 1):.2f} edges per node)")
    print(f"  whole continent, shippable form: {raw / 1e6:.1f} MB raw")
    print(f"  -> data/au/graph.npz {os.path.getsize('data/au/graph.npz') / 1e6:.1f} MB")

    # The number that decides the design is the WORST tile, not the average: nobody
    # loads the average, and Sydney is where the visitors are.
    lat = eb.lat[eb.have]
    lon = eb.lon[eb.have]
    print()
    for deg in (0.125, 0.25, 0.5, 1.0):
        key = (np.floor(lon / deg).astype(np.int64) * 1_000_003
               + np.floor(lat / deg).astype(np.int64))
        _, cnt = np.unique(key, return_counts=True)
        e_per_n = m / max(n, 1)
        worst = int(cnt.max())
        wb = worst * per_node + worst * e_per_n * per_edge
        med = int(np.median(cnt))
        mb = med * per_node + med * e_per_n * per_edge
        km = deg * 111.0
        print(f"  {deg:5.3f}deg (~{km:3.0f} km) tiles: {len(cnt):6,} non-empty, "
              f"worst {worst:8,} nodes = {wb / 1e6:5.1f} MB, "
              f"median {med:6,} = {mb / 1e6:4.2f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
