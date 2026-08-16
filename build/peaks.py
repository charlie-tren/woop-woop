"""Ship the PEAKS, not the field.

The whole tiling problem came from an assumption I never examined: that the browser
needs the distance surface. It does not. The answer to "the furthest point in this
region" is always a LOCAL MAXIMUM of the surface, so the only cells that can ever be an
answer are the peaks - and there are a few thousand of them, not a few billion.

Australia at 100 m is 1.6 billion cells. Australia's peaks, spaced a kilometre apart and
filtered to somewhere you can actually reach, is a list small enough to send as one
file. The rest of the raster is scaffolding that only the build needs.

Each peak carries everything the query needs, precomputed:
  lat, lon        where it is
  d               metres to the nearest civilisation - the number being maximised
  off             metres off the nearest track, so the walk in can be described
  alat, alon      the access point: nearest built ground, i.e. where you leave the car
  c               land-component id, so "can I get there without a boat" is an integer
                  comparison instead of a flood fill over a raster we no longer ship
"""
import json, sys, numpy as np
from scipy import ndimage
sys.path.insert(0, "build")
from raster import Grid

MAX_BUSH_M = 300     # how far off a track a peak may sit and still count
SPACING_M = 1000     # non-maximum suppression radius; one answer per kilometre
# Low on purpose. A 300 m floor looked reasonable and made "walk for an hour from the
# middle of Brisbane" return NOTHING, which reads as broken rather than as an honest
# answer. Better to say "200 m from anything" and let the number speak.
MIN_DIST_M = 100


def main(src="data/seq-dist.npz", out="docs/data/peaks.json"):
    r = np.load(src, allow_pickle=True)
    dist, reach, wet, ans = r["dist"], r["reach"], r["wet"], r["answerable"]
    occ, cell = r["occ"], float(r["cell"])
    g = Grid(tuple(r["bbox"]), cell)

    # Standable, in the answered box, and close enough to a way in to be reachable.
    ok = (~wet) & ans & (reach <= MAX_BUSH_M) & (dist >= MIN_DIST_M)
    print(f"  {ok.sum():,} candidate cells of {dist.size:,}")

    # Land components, so reachability is decided without shipping a raster. Islands
    # get their own id and simply never match a mainland origin.
    comp, ncomp = ndimage.label((~wet) & ans)
    print(f"  {ncomp} land components")

    # A peak is a cell that is the maximum within SPACING_M of itself. maximum_filter
    # does this in one pass; ties on a plateau are broken by taking the first, which is
    # why the equality test is followed by suppression rather than used alone.
    k = int(round(SPACING_M / cell)) | 1
    field = np.where(ok, dist, -1)
    local_max = ndimage.maximum_filter(field, size=k, mode="constant", cval=-1)
    cand = ok & (field >= local_max)
    ys, xs = np.nonzero(cand)
    print(f"  {len(ys):,} raw peaks")

    # Nearest built ground for every cell, computed once for all peaks rather than
    # searched per peak.
    _, (ay, ax) = ndimage.distance_transform_edt(~occ, return_indices=True)

    order = np.argsort(-dist[ys, xs])
    ys, xs = ys[order], xs[order]

    # Suppression on the sorted list: keep a peak only if no better one is already
    # within SPACING_M. Done on a coarse boolean grid so it stays linear.
    step = max(1, int(round(SPACING_M / cell)))
    taken = np.zeros((dist.shape[0] // step + 2, dist.shape[1] // step + 2), bool)
    peaks = []
    for y, x in zip(ys, xs):
        gy, gx = y // step, x // step
        if taken[gy:gy + 2, gx:gx + 2].any() and taken[gy, gx]:
            continue
        if taken[gy, gx]:
            continue
        taken[gy, gx] = True
        lon, lat = g.to_lonlat(x + 0.5, y + 0.5)
        alon, alat = g.to_lonlat(int(ax[y, x]) + 0.5, int(ay[y, x]) + 0.5)
        peaks.append((float(lat), float(lon), float(dist[y, x]), float(reach[y, x]),
                      float(alat), float(alon), int(comp[y, x])))
    peaks.sort(key=lambda p: -p[2])
    print(f"  {len(peaks):,} peaks after suppression; best {peaks[0][2]/1000:.2f} km")

    # Packed binary, in structure-of-arrays order. JSON of 25k objects spends most of
    # its bytes repeating the key names; this is 22 bytes per peak and parses with one
    # typed-array view per field.
    #
    # Coordinates are int32 in hundred-thousandths of a degree (about 1 m), not
    # float32: float32 carries ~7 significant digits and a longitude of 153.12345 needs
    # 8, so a float32 file would silently round every point in eastern Australia to
    # ~10 m and lose the metre the int keeps.
    n = len(peaks)
    lat = np.array([p[0] for p in peaks]) * 1e5
    lon = np.array([p[1] for p in peaks]) * 1e5
    d = np.clip([p[2] for p in peaks], 0, 65535)
    off = np.clip([p[3] for p in peaks], 0, 65535)
    alat = np.array([p[4] for p in peaks]) * 1e5
    alon = np.array([p[5] for p in peaks]) * 1e5
    c = np.clip([p[6] for p in peaks], 0, 65535)

    blob = b"".join([
        np.rint(lat).astype("<i4").tobytes(), np.rint(lon).astype("<i4").tobytes(),
        np.rint(d).astype("<u2").tobytes(), np.rint(off).astype("<u2").tobytes(),
        np.rint(alat).astype("<i4").tobytes(), np.rint(alon).astype("<i4").tobytes(),
        np.rint(c).astype("<u2").tobytes(),
    ])
    binpath = out.replace(".json", ".bin")
    open(binpath, "wb").write(blob)

    # The origin's land component still has to be looked up from somewhere. A component
    # raster at 2 km is 400x smaller than the distance field and is huge flat regions,
    # so it packs to almost nothing.
    cstep = max(1, int(round(2000 / cell)))
    small = np.clip(comp[::cstep, ::cstep], 0, 65535).astype("<u2")
    comppath = out.replace(".json", "-comp.bin")
    open(comppath, "wb").write(small.tobytes())

    meta = {
        "serve": [float(v) for v in r["serve"]],
        "max_bush_m": MAX_BUSH_M, "spacing_m": SPACING_M,
        "count": n, "coord_scale": 1e5,
        "fields": ["lat:i4", "lon:i4", "d:u2", "off:u2", "alat:i4", "alon:i4", "c:u2"],
        "comp": {"width": int(small.shape[1]), "height": int(small.shape[0]),
                 "north": float(r["bbox"][2]), "west": float(r["bbox"][1]),
                 "south": float(r["bbox"][0]), "east": float(r["bbox"][3])},
        "max_m": float(peaks[0][2]),
    }
    json.dump(meta, open(out, "w"), indent=1)
    import os
    for f in (out, binpath, comppath):
        print(f"  -> {f}  {os.path.getsize(f)/1024:,.0f} KB")


if __name__ == "__main__":
    main(*sys.argv[1:])
