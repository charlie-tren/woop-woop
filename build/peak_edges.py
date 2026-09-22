"""Give every peak an exact place on the routing graph: an edge, and a distance along it.

THE PROBLEM. The graph keeps junctions and throws the geometry between them away, so a
peak sitting mid-edge has no node. build/check_peak_nodes.py measures the damage: only
26.6% of peaks have a node within 15 m, the median peak is 590 m from one, and p90 is
3.1 km. Reachability then charges that peak its nearest junction plus a straight-line run
in, which at p90 is 34 minutes of a 60 minute walking budget - so the walking comparison
against openrouteservice measures the approximation, not the speed model.

WHY NOT JUST SPLIT THE EDGE GEOMETRICALLY. Because an edge in graph.npz is a straight
chord between two junctions, while its `length` is the true polyline distance. On any
curved way - which is most of a fire trail - projecting a peak onto the chord gives a
point that is not on the road and an offset that is not the distance travelled. The offset
has to be measured ALONG the polyline, which means reading the .pbf again while the
intermediate vertices still exist.

WHAT THIS WRITES, per peak that lands on a routable way:

    edge_a, edge_b     the junction node indices, matching graph.npz's own indexing
    d_from_a           metres along the way's polyline from edge_a
    d_from_b           metres along it from edge_b (not length - d_from_a when the peak
                       is on a different way than the shortest edge, so it is stored)
    off_m              how far the peak sits off the way. Kept because it is the honest
                       residual: a peak 40 m off a track is genuinely 40 m off it, and a
                       consumer can decide whether to charge it.

Cost is then exact: min(cost[a] + d_from_a / speed, cost[b] + d_from_b / speed), with no
straight-line term at all for a matched peak.

    python build/peak_edges.py            # -> data/au/peak_edges.npz

HOW THE MATCH IS DONE, and why it is not a KD-tree. A tree over 37M segment midpoints is
three quarters of a gigabyte before the tree overhead, on a machine with 32 GB soldered
and no upgrade path. Instead peaks go into a coarse dict keyed by a ~1 km cell - the same
trick build/snap.py already uses - and each segment looks up only the cells it touches.
Almost every segment finds nothing and costs one dict probe, which is what makes a
37M-segment pass affordable in Python.
"""
import json
import sys
import time

import numpy as np
import osmium

sys.path.insert(0, "build")
from graph import CLASS_ID, MPD, SPEEDS, junctions

PBF = "data/australia-latest.osm.pbf"       # same default as build/graph.py
OUT = "data/au/peak_edges.npz"

# A peak further than this from every routable way is not on the network in any useful
# sense, and forcing a match would invent a road. Peaks were snapped onto way geometry by
# build/snap.py, so a genuine match is usually sub-metre; this is loose enough to absorb
# the grid quantisation snap.py deliberately does not undo.
MAX_OFF_M = 120.0

# Cell size for the coarse hash, in degrees. About 1.1 km of latitude, which comfortably
# exceeds MAX_OFF_M so a peak can only be in the cell its segment probes or one adjacent.
CELL = 0.01


def load_peaks(meta_path="docs/data/peaks.json", bin_path="docs/data/peaks.bin"):
    meta = json.load(open(meta_path))
    buf = np.fromfile(bin_path, dtype=np.uint8)
    n, s = meta["count"], meta["coord_scale"]
    return (np.frombuffer(buf, "<i4", n, 0) / s,
            np.frombuffer(buf, "<i4", n, 4 * n) / s)


def build_index(plat, plon):
    """cell -> list of peak ids. A plain dict beats a tree here because it is sparse."""
    idx = {}
    for i, (la, lo) in enumerate(zip(plat, plon)):
        idx.setdefault((int(la // CELL), int(lo // CELL)), []).append(i)
    return {k: np.array(v, dtype=np.int64) for k, v in idx.items()}


class PeakEdges(osmium.SimpleHandler):
    """Walk every routable way, carrying the run from the last junction.

    The state that matters is (last_junction, run): `run` is the true polyline distance
    covered since the last junction, so a peak matched on the current segment gets
    run + (distance along this segment) as its offset from that junction. That is the
    number the chord cannot give.
    """

    def __init__(self, junc, index, plat, plon):
        super().__init__()
        self.junc = junc
        self.index = index
        self.plat = plat
        self.plon = plon
        self.hit = {}                 # peak id -> (a, b, d_from_a, off) best so far
        self.pending = []             # matches on the edge currently being walked
        self.ways = 0
        self.segments = 0

    def _index(self, ref):
        i = np.searchsorted(self.junc, ref)
        return int(i) if i < len(self.junc) and self.junc[i] == ref else -1

    def _match(self, alat, alon, blat, blon, run, klon):
        """Peaks projecting onto this segment, as (peak id, along_m, off_m)."""
        c0 = (int(min(alat, blat) // CELL), int(min(alon, blon) // CELL))
        c1 = (int(max(alat, blat) // CELL), int(max(alon, blon) // CELL))
        cand = []
        for cy in range(c0[0] - 1, c1[0] + 2):
            for cx in range(c0[1] - 1, c1[1] + 2):
                got = self.index.get((cy, cx))
                if got is not None:
                    cand.append(got)
        if not cand:
            return ()
        ids = np.concatenate(cand) if len(cand) > 1 else cand[0]

        ax, ay = alon * klon, alat * MPD
        bx, by = blon * klon, blat * MPD
        px = self.plon[ids] * klon
        py = self.plat[ids] * MPD
        vx, vy = bx - ax, by - ay
        seg2 = vx * vx + vy * vy
        if seg2 <= 0.0:
            return ()
        t = np.clip(((px - ax) * vx + (py - ay) * vy) / seg2, 0.0, 1.0)
        dx = px - (ax + t * vx)
        dy = py - (ay + t * vy)
        off = np.hypot(dx, dy)
        keep = off <= MAX_OFF_M
        if not keep.any():
            return ()
        seg_len = np.sqrt(seg2)
        return zip(ids[keep], run + t[keep] * seg_len, off[keep])

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in SPEEDS:
            return
        self.ways += 1
        cid = CLASS_ID[hw]
        prev_j = -1
        run = 0.0
        plat = plon = None
        klon = None
        for n in w.nodes:
            if not n.location.valid():
                continue
            la, lo = n.location.lat, n.location.lon
            if klon is None:
                klon = MPD * np.cos(np.radians(la))
            if plat is not None:
                seg = np.hypot((la - plat) * MPD, (lo - plon) * klon)
                if prev_j >= 0:
                    for pid, along, off in self._match(plat, plon, la, lo, run, klon):
                        self.pending.append((int(pid), float(along), float(off)))
                run += seg
                self.segments += 1
            j = self._index(n.ref)
            if j >= 0:
                if prev_j >= 0:
                    # The edge prev_j -> j is complete, so its pending peaks can be
                    # resolved: their distance from the FAR junction is only knowable
                    # now that the edge's full length is known.
                    for pid, along, off in self.pending:
                        best = self.hit.get(pid)
                        if best is None or off < best[4]:
                            self.hit[pid] = (prev_j, j, along, run - along, off, cid)
                self.pending = []
                prev_j = j
                run = 0.0
            plat, plon = la, lo
        self.pending = []


def main():
    t0 = time.time()
    print("pass 1: junctions", flush=True)
    junc = junctions(PBF)

    plat, plon = load_peaks()
    print(f"  {len(plat):,} peaks; building the cell index", flush=True)
    index = build_index(plat, plon)
    print(f"  {len(index):,} occupied cells ({time.time() - t0:.0f}s)", flush=True)

    print("pass 2: matching peaks to edges", flush=True)
    h = PeakEdges(junc, index, plat, plon)
    h.apply_file(PBF, locations=True)
    print(f"  {h.ways:,} routable ways, {h.segments:,} segments, "
          f"{len(h.hit):,} peaks matched ({time.time() - t0:.0f}s)", flush=True)

    ids = np.array(sorted(h.hit), dtype=np.int64)
    rows = np.array([h.hit[i] for i in ids], dtype=np.float64)
    np.savez_compressed(
        OUT,
        peak=ids,
        edge_a=rows[:, 0].astype(np.int64),
        edge_b=rows[:, 1].astype(np.int64),
        d_from_a=rows[:, 2].astype(np.float32),
        d_from_b=rows[:, 3].astype(np.float32),
        off_m=rows[:, 4].astype(np.float32),
        cls=rows[:, 5].astype(np.uint8),
    )
    pct = 100.0 * len(ids) / len(plat)
    print(f"wrote {OUT}: {len(ids):,} of {len(plat):,} peaks ({pct:.1f}%)")
    print(f"  off_m  median {np.median(rows[:, 4]):.1f} m   "
          f"p90 {np.percentile(rows[:, 4], 90):.1f} m   max {rows[:, 4].max():.1f} m")
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
