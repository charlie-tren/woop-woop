"""Pull every 'anything' feature out of an OSM extract, clipped to a bbox.

'Anything' is the whole question this project asks, so the definition lives here and
nowhere else. Four families, all geometry rather than optional attributes - which is
why this works where Oldest Near You died: `start_date` is barely mapped, road and
building GEOMETRY is the base layer of OSM.

Output is a .npz of flat float32 lon/lat arrays plus a per-feature offset index, which
is all the rasteriser needs and is ~20x smaller than keeping the tagged objects.
"""
import sys, time, numpy as np, osmium

# A way is "anything" if it matches one of these. Deliberately NOT every tag in OSM:
# a fence or a stream does not make a place less remote, and including them turns the
# answer into noise.
ROAD_VALUES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "track", "motorway_link",
    "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
# Deliberately excluded from roads: path, footway, cycleway, bridleway, steps.
# A walking track through a national park is how you GET to a remote place - counting
# it as civilisation would rule out every answer worth having.

def want(tags):
    hw = tags.get("highway")
    if hw in ROAD_VALUES:
        return "road"
    if "building" in tags:
        return "building"
    rw = tags.get("railway")
    if rw in {"rail", "light_rail", "subway", "tram", "narrow_gauge"}:
        return "rail"
    if tags.get("power") in {"line", "minor_line"}:
        return "power"
    if tags.get("aeroway") in {"runway", "taxiway"}:
        return "aero"
    # Water is NOT "anything" - an empty lake is empty. It is captured separately
    # because you cannot STAND in it, and the maximum over a coastal box is otherwise
    # always somewhere out at sea.
    if tags.get("natural") == "coastline":
        return "coast"
    if (tags.get("natural") == "water" or tags.get("waterway") == "riverbank"
            or tags.get("landuse") == "reservoir"):
        return "water"
    return None


def run(pbf, bbox, out, serve=None):
    south, west, north, east = bbox
    lons, lats, offs, kinds = [], [], [0], []
    counts = {}
    t0 = time.time()
    # Nodes MUST be read for the location cache to fill, so the entity filter goes on
    # the filter chain (after caching) rather than in the FileProcessor constructor,
    # which would skip nodes entirely and leave every way without geometry.
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.EmptyTagFilter()))
    n = 0
    for w in fp:
        n += 1
        if n % 2_000_000 == 0:
            print(f"  {n/1e6:.0f}M ways scanned, {len(kinds)} kept, {time.time()-t0:.0f}s",
                  flush=True)
        kind = want(w.tags)
        if kind is None:
            continue
        # Keep the WHOLE way if any part of it is in the box, rather than clipping to
        # the vertices that fall inside. Clipping truncates every way a vertex short of
        # the boundary, which for the coastline leaves a two-row gap at the southern
        # edge - and the ocean flood escapes through it and marks 98% of the region as
        # sea. Roads suffer the same truncation more quietly.
        xs, ys, inside = [], [], False
        for node in w.nodes:
            if not node.location.valid():
                continue
            lat, lon = node.location.lat, node.location.lon
            xs.append(lon); ys.append(lat)
            if south <= lat <= north and west <= lon <= east:
                inside = True
        if not inside or not xs:
            continue
        lons.extend(xs); lats.extend(ys); offs.append(len(lons))
        kinds.append(kind); counts[kind] = counts.get(kind, 0) + 1

    np.savez_compressed(
        out,
        lon=np.array(lons, dtype=np.float32),
        lat=np.array(lats, dtype=np.float32),
        off=np.array(offs, dtype=np.int64),
        kind=np.array(kinds),
        bbox=np.array(bbox, dtype=np.float64),
        serve=np.array(serve if serve is not None else bbox, dtype=np.float64),
    )
    print(f"\n{n:,} ways scanned in {time.time()-t0:.0f}s")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:9} {v:>9,}")
    print(f"  {'vertices':9} {len(lons):>9,}  ->  {out}")


if __name__ == "__main__":
    # SEQ: Noosa down past the border, west past Toowoomba - plus a BUFFER.
    # The buffer is not cosmetic. A cell near the edge of the extract has an inflated
    # distance, because the road or house that is actually nearest to it sits just
    # outside the box and was never loaded. Every edge would otherwise report a false
    # remote spot. The buffer must exceed the largest distance we are willing to
    # report; 0.6 degrees is ~66 km, comfortably more.
    BUFFER = 0.6
    SERVE = (-28.60, 151.60, -25.80, 153.60)
    BBOX = (SERVE[0] - BUFFER, SERVE[1] - BUFFER,
            SERVE[2] + BUFFER, SERVE[3] + BUFFER)
    run(sys.argv[1] if len(sys.argv) > 1 else "data/australia-latest.osm.pbf",
        BBOX, sys.argv[2] if len(sys.argv) > 2 else "data/seq-features.npz", SERVE)
