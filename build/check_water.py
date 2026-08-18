"""Prove no shipped peak sits in water, using a different code path from the build.

The build decides water by burning way outlines into a grid and filling them. If that
logic is wrong, checking it with itself proves nothing. So this uses osmium's own
multipolygon ASSEMBLER to construct real water polygons, and shapely to test each peak
against them. Two independent implementations agreeing is evidence; one agreeing with
itself is not.

    python build/check_water.py [extract.osm.pbf] [peaks.json]          # report
    python build/check_water.py [extract.osm.pbf] [peaks.json] --drop   # and remove

Raster fills leave a residue no matter how they are seeded: a salt lake whose outline
does not close cleanly at the coarse resolution stays "land" in the grid. Assembled
polygons have no such failure mode, so --drop uses them as the final sieve and the
guarantee becomes structural rather than statistical.
"""
import json, sys, time
import numpy as np
import osmium
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

# Split deliberately. STRICT is what the build claims to exclude - open water. MARSH
# is wetland, which the build does NOT exclude and arguably should not: a claypan or a
# salt flat is walkable, and Lake Eyre is dry most of the year. Reporting them together
# would blame the build for a definition it never made.
STRICT = {("natural", "water"), ("landuse", "reservoir"), ("waterway", "riverbank")}
MARSH = {("natural", "wetland")}


def classify(tags):
    pairs = {(k, v) for k, v in tags}
    if pairs & STRICT:
        return "strict"
    if pairs & MARSH:
        return "marsh"
    return None


def load_peaks(path):
    m = json.load(open(path))
    n, s = m["count"], m["coord_scale"]
    buf = open(path.replace(".json", ".bin"), "rb").read()
    o = 0

    def take(dt, cnt):
        nonlocal o
        a = np.frombuffer(buf, dt, cnt, o)
        o += cnt * np.dtype(dt).itemsize
        return a
    lat = take("<i4", n) / s
    lon = take("<i4", n) / s
    d = take("<u2", n).astype(np.int64) * m.get("dist_scale_m", 1)
    return m, lat, lon, d


def main(pbf="data/australia-latest.osm.pbf", peaks="docs/data/peaks.json"):
    m, lat, lon, d = load_peaks(peaks)
    print(f"{len(lat):,} shipped peaks, best {d.max()/1000:.1f} km")

    t0 = time.time()
    polys = []
    # NO EmptyTagFilter here. With one in the chain this loop assembled ZERO water
    # polygons and the check reported a clean 0.00% - a test that cannot fail. The
    # extract really holds 6.4M areas, 309,595 of them water.
    # GeoInterfaceFilter is what ATTACHES __geo_interface__ to the objects. Without it
    # every area raises AttributeError, and with a bare `except: continue` around the
    # conversion that looked exactly like "there is no water in Australia".
    fp = (osmium.FileProcessor(pbf).with_areas()
          .with_filter(osmium.filter.GeoInterfaceFilter()))
    errs = 0
    groups = {"strict": [], "marsh": []}
    for o in fp:
        if type(o).__name__ != "Area":
            continue
        cls = classify(o.tags)
        if cls is None:
            continue
        try:
            groups[cls].append(shape(o.__geo_interface__["geometry"]))
        except Exception as exc:
            errs += 1
            if errs <= 3:
                print(f"    skipped an area: {type(exc).__name__}: {exc}")
    if errs:
        print(f"    {errs:,} areas could not be converted")
    polys = groups["strict"] + groups["marsh"]
    print(f"  assembled {len(polys):,} water polygons in {time.time()-t0:.0f}s")
    if len(polys) < 1000:
        raise SystemExit("too few water polygons assembled - the check would pass "
                         "vacuously, which is worse than not running it")

    pts = [Point(x, y) for x, y in zip(lon, lat)]
    drop = np.zeros(len(lat), bool)
    for name, ps in (("open water", groups["strict"]),
                     ("wetland", groups["marsh"])):
        if not ps:
            print(f"  {name}: no polygons")
            continue
        hits = STRtree(ps).query(pts, predicate="within")
        bad = np.unique(hits[0]) if hits.size else np.array([], dtype=int)
        drop[bad] = True
        print("")
        print(f"  peaks inside {name}: {len(bad):,} "
              f"({100*len(bad)/len(lat):.2f}%)")
        for i in bad[np.argsort(-d[bad])][:6]:
            print(f"    {d[i]/1000:7.1f} km  {lat[i]:.4f}, {lon[i]:.4f}")

    if "--drop" not in sys.argv:
        return 1 if drop.any() else 0

    keep = ~drop
    print("")
    print(f"  dropping {drop.sum():,}, keeping {keep.sum():,}")
    n = int(keep.sum())
    buf = open(peaks.replace(".json", ".bin"), "rb").read()
    o, total = 0, len(lat)
    fields = []
    for dt in ("<i4", "<i4", "<u2", "<u2", "<i4", "<i4", "<u2"):
        a = np.frombuffer(buf, dt, total, o)
        o += total * np.dtype(dt).itemsize
        fields.append(a[keep].astype(dt))
    open(peaks.replace(".json", ".bin"), "wb").write(
        b"".join(f.tobytes() for f in fields))
    m["count"] = n
    m["water_sieve"] = "assembled polygons, osmium areas + shapely"
    json.dump(m, open(peaks, "w"), indent=1)
    print(f"  -> {peaks} and .bin rewritten with {n:,} peaks")
    return 0


if __name__ == "__main__":
    sys.exit(main(*[a for a in sys.argv[1:] if not a.startswith("--")]))
