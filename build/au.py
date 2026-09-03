"""Build the peak list for a whole continent, one chunk at a time.

Australia at 100 m is 1.6 billion cells, which no distance transform is going to hold.
But nothing ever needs the whole surface at once: the peaks of a chunk are the peaks of
the continent restricted to that chunk, so the field can be built, mined and thrown away
one box at a time. Only the peak lists are kept, and they concatenate.

Three passes, in this order:

  bucket   ONE stream over the .osm.pbf, writing each way into every chunk whose
           BUFFERED box it touches. One pass, not one per chunk - scanning 11 million
           ways ninety-nine times would take a working day.
  coarse   a single 2 km water/land-component grid for the whole continent, so
           "same landmass" is one consistent id everywhere. Per-chunk components would
           renumber at every border and an island in one chunk would match the mainland
           in the next.
  chunks   per chunk: rasterise at 100 m, two distance transforms, mine the peaks.

Chunks overlap by BUFFER on every side so a cell near a border still measures against
the roads just outside it; peaks are then kept only if they fall in the chunk's own
box, which is what stops the seams double-counting.
"""
import json, os, struct, sys, time
import numpy as np
from scipy import ndimage
sys.path.insert(0, "build")
from extract import want
from raster import Grid, burn, ANYTHING, ACCESS, water_mask
import osmium

AUS = (-44.5, 112.0, -9.0, 154.5)     # south, west, north, east
STEP = 4.0                            # chunk size in degrees
BUFFER = 0.6                          # overlap, must exceed the largest distance shown
CELL = 100.0                          # metres
COARSE = 2000.0                       # metres, for the continent-wide component grid
WORK = "data/au"


def chunks(area=AUS, step=STEP):
    s, w, n, e = area
    out = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            out.append((round(lat, 3), round(lon, 3),
                        round(min(lat + step, n), 3), round(min(lon + step, e), 3)))
            lon += step
        lat += step
    return out


# --------------------------------------------------------------------- bucketing
class Bucketer:
    """Append ways to per-chunk binary files, flushing so memory stays flat."""

    FLUSH = 400_000   # vertices held per chunk before writing out

    def __init__(self, boxes, workdir):
        self.boxes = boxes
        self.dir = workdir
        os.makedirs(workdir, exist_ok=True)
        self.buf = [[] for _ in boxes]     # list of (kind, lons, lats)
        self.held = [0] * len(boxes)
        self.total = 0

    def add(self, kind, lons, lats):
        lo_lat, hi_lat = min(lats), max(lats)
        lo_lon, hi_lon = min(lons), max(lons)
        for i, (s, w, n, e) in enumerate(self.boxes):
            if (hi_lat < s - BUFFER or lo_lat > n + BUFFER
                    or hi_lon < w - BUFFER or lo_lon > e + BUFFER):
                continue
            self.buf[i].append((kind, lons, lats))
            self.held[i] += len(lons)
            if self.held[i] >= self.FLUSH:
                self.flush(i)
        self.total += len(lons)

    def flush(self, i):
        if not self.buf[i]:
            return
        with open(f"{self.dir}/c{i:03d}.bin", "ab") as f:
            for kind, lons, lats in self.buf[i]:
                f.write(struct.pack("<BI", KINDS[kind], len(lons)))
                f.write(np.asarray(lons, dtype="<f8").tobytes())
                f.write(np.asarray(lats, dtype="<f8").tobytes())
        self.buf[i] = []
        self.held[i] = 0

    def close(self):
        for i in range(len(self.boxes)):
            self.flush(i)


KINDS = {k: i for i, k in enumerate(
    ["road", "way", "building", "rail", "power", "aero", "water", "coast"])}
UNKIND = {v: k for k, v in KINDS.items()}


def read_chunk(path):
    """Rebuild the flat lon/lat/off/kind arrays a Grid burn expects."""
    lons, lats, offs, kinds = [], [], [0], []
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        blob = f.read()
    p, N = 0, len(blob)
    while p < N:
        k, cnt = struct.unpack_from("<BI", blob, p); p += 5
        lo = np.frombuffer(blob, "<f8", cnt, p); p += cnt * 8
        la = np.frombuffer(blob, "<f8", cnt, p); p += cnt * 8
        lons.append(lo); lats.append(la)
        offs.append(offs[-1] + cnt); kinds.append(UNKIND[k])
    if not kinds:
        return None
    return (np.concatenate(lons).astype(np.float32),
            np.concatenate(lats).astype(np.float32),
            np.array(offs, dtype=np.int64), np.array(kinds))


def water_relation_ways(pbf):
    """Way ids belonging to a water MULTIPOLYGON.

    Big lakes are relations in OSM, not closed ways - Lake Argyle, Lake Way, Lake
    Samsonvale, 33,769 of them in Australia alone - and a pass that reads only ways
    never sees any of them. The consequence is not subtle: the emptiest point in a
    region is very often the middle of its largest lake, so the answer was landing on
    open water.

    Tagging the MEMBER ways as water is enough. Each member is an open line, but their
    union closes the ring, so the existing fill turns them into a filled lake without
    any polygon assembly.
    """
    t0 = time.time()
    ids = set()
    fp = (osmium.FileProcessor(pbf)
          .with_filter(osmium.filter.EntityFilter(osmium.osm.RELATION))
          .with_filter(osmium.filter.EmptyTagFilter()))
    n = 0
    for r in fp:
        t = r.tags
        if (t.get("natural") in ("water", "wetland")
                or t.get("landuse") == "reservoir"
                or t.get("waterway") == "riverbank"):
            n += 1
            for m in r.members:
                if m.type == "w":
                    ids.add(m.ref)
    print(f"  {n:,} water relations -> {len(ids):,} member ways "
          f"({time.time()-t0:.0f}s)", flush=True)
    return ids


def bucket(pbf, boxes):
    b = Bucketer(boxes, WORK)
    water_ways = water_relation_ways(pbf)
    t0 = time.time()
    # NO EmptyTagFilter here, and that is the whole point.
    #
    # It was here as a speed filter, and it silently defeated water_relation_ways()
    # sitting right above it. A multipolygon's rings are UNTAGGED ways - the tags live
    # on the relation - so the filter threw away precisely the ways the water pass had
    # just gone looking for. Measured on Sydney Harbour, which is a natural=water /
    # water=harbour relation: 41 of its 60 member ways carry no tags at all, and the
    # other 19 carry only source/attribution metadata. Its outline therefore never
    # assembled, binary_fill_holes had no closed ring to fill, and the harbour came out
    # as standable LAND - in the same connected region as the whole inland continent,
    # since the coastline crosses the harbour mouth rather than tracing its shoreline.
    #
    # Cost of dropping it is stream time; want() rejects an untagged way immediately.
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY)))
    n = 0
    for w in fp:
        n += 1
        if n % 2_000_000 == 0:
            print(f"  {n/1e6:.0f}M ways, {b.total/1e6:.0f}M vertices kept, "
                  f"{time.time()-t0:.0f}s", flush=True)
        kind = want(w.tags)
        if kind is None and w.id in water_ways:
            kind = "water"          # a member of a lake multipolygon
        if kind is None:
            continue
        lons, lats = [], []
        for node in w.nodes:
            if node.location.valid():
                lons.append(node.location.lon); lats.append(node.location.lat)
        if lons:
            b.add(kind, lons, lats)
    b.close()
    print(f"  {n:,} ways scanned, {b.total:,} vertices bucketed "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    boxes = chunks()
    print(f"{len(boxes)} chunks of {STEP} degrees over {AUS}")
    bucket(sys.argv[1] if len(sys.argv) > 1 else "data/australia-latest.osm.pbf", boxes)
    json.dump({"boxes": boxes, "buffer": BUFFER, "cell": CELL, "coarse": COARSE},
              open(f"{WORK}/chunks.json", "w"), indent=1)
    sizes = sorted((os.path.getsize(f"{WORK}/c{i:03d}.bin") / 1e6
                    for i in range(len(boxes))
                    if os.path.exists(f"{WORK}/c{i:03d}.bin")), reverse=True)
    print(f"  {len(sizes)} non-empty chunks, largest {sizes[0]:.0f} MB, "
          f"total {sum(sizes):.0f} MB")
