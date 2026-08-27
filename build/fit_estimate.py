"""Tune the estimate against the answer, not against a geometric proxy.

The first calibration fitted the circle to the isochrone's equal-area radius and the
result was useless - 680 m for an hour's walk - because area is not what the app asks
of the polygon. What it asks is "which peak wins", so that is what this fits.

For each sampled origin it takes the exact answer (the best peak inside the real
isochrone) and then, for a range of candidate radii, the estimate's answer. The best
multiplier is the one whose answer agrees most often - and, where it disagrees, errs
towards under-promising rather than sending someone somewhere they cannot reach.

    python build/fit_estimate.py fetch    # sweep, saving the polygons this time
    python build/fit_estimate.py fit      # evaluate multipliers against the answers
"""
import json, math, os, sys, time, urllib.error, urllib.request
import numpy as np

UPSTREAM = "https://api.openrouteservice.org/v2/isochrones"
PROFILES = {"foot": "foot-walking", "bike": "cycling-regular", "car": "driving-car"}
KEY_FILE = "secrets/ors_key.txt"
OUT = "data/isochrones.json"
GAP_S = 3.5

# The BASELINE the multiplier sweep is expressed against, not what the app ships.
# It was app.js's model when this was written; the fit has since been run over 84
# isochrones and app.js now carries the RESULT - 1.8, 7.3 and 22.0 km/h at detour
# 1.0, chosen to under-promise. Do not "correct" these to match app.js: the reported
# multiplier is relative to this baseline, and moving it silently rescales every
# number the fit prints. The effective speed is always kmh * det * mult, which is
# what to compare against app.js.
GUESS = {"foot": (4.5, 0.80), "bike": (15.0, 0.75), "car": (70.0, 0.70)}
# openrouteservice refuses anything longer, measured (error 3004).
MAX_SECONDS = 3600


def key():
    k = open(KEY_FILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit(f"{KEY_FILE} is empty")
    return k


def load_peaks():
    m = json.load(open("docs/data/peaks.json"))
    n, s = m["count"], m["coord_scale"]
    ds = m.get("dist_scale_m", 1)
    buf = open("docs/data/peaks.bin", "rb").read()
    o = 0

    def take(dt, cnt):
        nonlocal o
        a = np.frombuffer(buf, dt, cnt, o)
        o += cnt * np.dtype(dt).itemsize
        return a
    lat = take("<i4", n) / s
    lon = take("<i4", n) / s
    d = take("<u2", n).astype(np.int64) * ds
    take("<u2", n); take("<i4", n); take("<i4", n)
    comp = take("<u2", n)
    return lat, lon, d, comp


def isochrone(k, mode, lat, lon, seconds):
    body = json.dumps({"locations": [[lon, lat]], "range": [seconds],
                       "range_type": "time", "smoothing": 10}).encode()
    req = urllib.request.Request(
        f"{UPSTREAM}/{PROFILES[mode]}", data=body,
        headers={"Authorization": k, "Content-Type": "application/json",
                 "Accept": "application/geo+json",
                 "User-Agent": "woop-woop-fit/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def fetch(n_points=16):
    k = key()
    lat, lon, d, comp = load_peaks()
    rng = np.random.default_rng(29)
    idx = rng.choice(len(lat), size=n_points, replace=False)
    pts = [(float(lat[i]), float(lon[i])) for i in idx]

    plan = [(m, s) for m in ("foot", "bike", "car") for s in (1800, MAX_SECONDS)]
    rows, total, done = [], len(pts) * len(plan), 0
    print(f"{total} calls, about {total*GAP_S/60:.0f} minutes")
    for la, lo in pts:
        for mode, secs in plan:
            done += 1
            try:
                geo = isochrone(k, mode, la, lo, secs)
                rings = geo["features"][0]["geometry"]["coordinates"]
                rows.append({"mode": mode, "seconds": secs, "lat": la, "lon": lo,
                             "rings": rings})
                print(f"  [{done}/{total}] {mode:4} {secs//60:>2}min ok", flush=True)
            except urllib.error.HTTPError as e:
                print(f"  [{done}/{total}] {mode} HTTP {e.code}", flush=True)
                if e.code == 429:
                    json.dump(rows, open(OUT, "w"))
                    raise SystemExit("daily quota reached - saved what we have")
            except Exception as exc:
                print(f"  [{done}/{total}] {type(exc).__name__}", flush=True)
            time.sleep(GAP_S)
    json.dump(rows, open(OUT, "w"))
    print(f"  -> {OUT} with {len(rows)} isochrones")


def in_ring(plat, plon, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > plat) != (yj > plat) and \
                plon < (xj - xi) * (plat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def best_in_polygon(rings, lat, lon, d, comp, want):
    """The app's own query, run against the real polygon."""
    ring = np.array(rings[0])
    s, n = ring[:, 1].min(), ring[:, 1].max()
    w, e = ring[:, 0].min(), ring[:, 0].max()
    box = (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e) & (comp == want)
    cand = np.nonzero(box)[0]
    cand = cand[np.argsort(-d[cand])]
    for i in cand:
        if in_ring(lat[i], lon[i], rings[0]):
            if not any(in_ring(lat[i], lon[i], r) for r in rings[1:]):
                return int(i)
    return None


def best_in_circle(radius_m, olat, olon, lat, lon, d, comp, want):
    mlat = 111320.0
    mlon = mlat * math.cos(math.radians(olat))
    dx = (lon - olon) * mlon
    dy = (lat - olat) * mlat
    ok = (dx * dx + dy * dy <= radius_m * radius_m) & (comp == want)
    if not ok.any():
        return None
    idx = np.nonzero(ok)[0]
    return int(idx[np.argmax(d[idx])])


def fit():
    rows = json.load(open(OUT))
    lat, lon, d, comp = load_peaks()
    # Component of each origin: nearest peak's component is a good enough proxy here.
    print(f"{len(rows)} isochrones\n")
    mults = [round(x, 2) for x in np.arange(0.30, 1.35, 0.05)]

    for mode in ("foot", "bike", "car"):
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        kmh, det = GUESS[mode]
        truth, origins = [], []
        for r in rs:
            mlat = 111320.0
            dd = np.hypot((lat - r["lat"]) * mlat,
                          (lon - r["lon"]) * mlat * math.cos(math.radians(r["lat"])))
            want = int(comp[int(np.argmin(dd))])
            truth.append(best_in_polygon(r["rings"], lat, lon, d, comp, want))
            origins.append(want)

        print(f"{mode}:")
        print(f"  {'mult':>6}{'km/h':>8}{'same answer':>13}{'over-promise':>14}"
              f"{'median error':>14}")
        best = None
        for mu in mults:
            same = over = 0
            errs = []
            for r, t, want in zip(rs, truth, origins):
                if t is None:
                    continue
                radius = kmh * 1000 * det * mu * (r["seconds"] / 3600)
                g = best_in_circle(radius, r["lat"], r["lon"], lat, lon, d, comp, want)
                if g is None:
                    continue
                if g == t:
                    same += 1
                else:
                    errs.append(abs(float(d[g]) - float(d[t])))
                    # The estimate claimed a better spot than is actually reachable.
                    if d[g] > d[t]:
                        over += 1
            n = sum(1 for t in truth if t is not None)
            med = np.median(errs) if errs else 0
            print(f"  {mu:>6.2f}{kmh*det*mu:>8.1f}{f'{same}/{n}':>13}"
                  f"{f'{over}/{n}':>14}{med:>13.0f}m")
            score = (same, -over)
            if best is None or score > best[0]:
                best = (score, mu)
        print(f"  -> best multiplier {best[1]}  "
              f"(effective {kmh*det*best[1]:.1f} km/h)\n")


if __name__ == "__main__":
    {"fetch": fetch, "fit": fit}[sys.argv[1] if len(sys.argv) > 1 else "fit"]()
