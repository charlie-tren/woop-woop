"""Replace the guessed travel speeds with measured ones.

The page draws a circle: radius = speed x time x detour, where all six numbers were my
guesses. This asks openrouteservice for REAL isochrones at sampled points around
Australia and works out what radius actually matches, so the default answer is right
without the page ever calling an API.

Spends the daily quota once. Nothing at runtime.

    python build/calibrate.py probe     # one call, to check the request shape
    python build/calibrate.py run       # the sweep
    python build/calibrate.py fit       # summarise what came back

The key is read from secrets/ors_key.txt and is never printed, logged or committed.
"""
import json, math, os, random, sys, time, urllib.error, urllib.request

# Announced as deprecated in favour of api.heigit.org, but MEASURED on 19/08/2026:
# api.heigit.org returns an nginx 404 on both /v2/isochrones/... and /ors/v2/...,
# while api.openrouteservice.org answers 200. The notice is forward-looking, so stay on
# the old host and re-test before switching.
UPSTREAM = "https://api.openrouteservice.org/v2/isochrones"
PROFILES = {"foot": "foot-walking", "bike": "cycling-regular", "car": "driving-car"}
KEY_FILE = "secrets/ors_key.txt"
OUT = "data/calibration.json"

# The model currently in docs/app.js, which is what this is testing.
GUESS = {"foot": (4.5, 0.80), "bike": (15.0, 0.75), "car": (70.0, 0.70)}

# 20 isochrones a minute is the plan's ceiling, so leave a margin: a 429 in the middle
# of a sweep wastes everything already spent.
GAP_S = 3.5


def key():
    with open(KEY_FILE, encoding="utf-8") as f:
        k = f.read().strip()
    if not k:
        raise SystemExit(f"{KEY_FILE} is empty - paste the key into it and save")
    return k


def isochrone(k, mode, lat, lon, seconds):
    body = json.dumps({
        "locations": [[lon, lat]],          # longitude FIRST
        "range": [seconds],
        "range_type": "time",
        "smoothing": 10,
    }).encode()
    req = urllib.request.Request(
        f"{UPSTREAM}/{PROFILES[mode]}", data=body,
        headers={"Authorization": k, "Content-Type": "application/json",
                 "Accept": "application/geo+json",
                 "User-Agent": "woop-woop-calibration/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def ring_metrics(geo, lat0, lon0):
    """Equal-area radius of the isochrone, and how far it reaches at its furthest.

    The page's model is a circle, so the fair comparison is the circle with the same
    AREA - not the furthest point, which a single motorway would stretch absurdly.
    Both are returned because the gap between them says how circular the truth is.
    """
    coords = geo["features"][0]["geometry"]["coordinates"][0]
    mlat = 111320.0
    mlon = mlat * math.cos(math.radians(lat0))
    pts = [((x - lon0) * mlon, (y - lat0) * mlat) for x, y in coords]
    area = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2.0
    far = max(math.hypot(x, y) for x, y in pts)
    return math.sqrt(area / math.pi), far, area


def sample_points(n, seed=11):
    """Points on land across Australia, taken from the peak list.

    Sampling the peaks rather than random lat/lons guarantees every point is somewhere
    a person could actually stand, and spans city to desert in the same proportion the
    app's own answers do.
    """
    import numpy as np
    m = json.load(open("docs/data/peaks.json"))
    cnt, s = m["count"], m["coord_scale"]
    buf = open("docs/data/peaks.bin", "rb").read()
    lat = np.frombuffer(buf, "<i4", cnt, 0) / s
    lon = np.frombuffer(buf, "<i4", cnt, cnt * 4) / s
    rng = np.random.default_rng(seed)
    idx = rng.choice(cnt, size=n, replace=False)
    return [(float(lat[i]), float(lon[i])) for i in idx]


def probe():
    k = key()
    geo = isochrone(k, "car", -27.4698, 153.0251, 1800)
    r, far, area = ring_metrics(geo, -27.4698, 153.0251)
    print(f"  30 min by car from Brisbane:")
    print(f"    equal-area radius {r/1000:6.1f} km")
    print(f"    furthest reach    {far/1000:6.1f} km")
    print(f"    area              {area/1e6:6.0f} km2")
    kmh, det = GUESS["car"]
    print(f"    the page currently assumes {kmh*det*0.5:.1f} km")


def run(n_points=18):
    k = key()
    pts = sample_points(n_points)
    rows = []
    plan = [(mode, secs) for mode in ("foot", "bike", "car")
            for secs in (1800, 3600)]
    total = len(pts) * len(plan)
    print(f"{total} calls at {GAP_S}s apart, about {total*GAP_S/60:.0f} minutes")
    done = 0
    for lat, lon in pts:
        for mode, secs in plan:
            done += 1
            try:
                geo = isochrone(k, mode, lat, lon, secs)
                r, far, area = ring_metrics(geo, lat, lon)
                rows.append({"mode": mode, "seconds": secs, "lat": lat, "lon": lon,
                             "equal_area_m": r, "furthest_m": far, "area_m2": area})
                print(f"  [{done}/{total}] {mode:4} {secs//60:>2}min "
                      f"{lat:8.3f},{lon:8.3f}  r={r/1000:6.2f} km", flush=True)
            except urllib.error.HTTPError as e:
                print(f"  [{done}/{total}] {mode} HTTP {e.code} - stopping", flush=True)
                if e.code == 429:
                    break
            except Exception as exc:
                print(f"  [{done}/{total}] {type(exc).__name__}", flush=True)
            time.sleep(GAP_S)
    json.dump(rows, open(OUT, "w"), indent=1)
    print(f"  -> {OUT} with {len(rows)} results")


def fit():
    rows = json.load(open(OUT))
    print(f"{len(rows)} samples\n")
    print(f"{'mode':6}{'guess km/h':>12}{'guess x det':>13}{'measured':>11}"
          f"{'ratio':>8}{'spread':>9}")
    out = {}
    for mode in ("foot", "bike", "car"):
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        # Effective speed the circle should use: equal-area radius over time.
        eff = sorted(r["equal_area_m"] / (r["seconds"] / 3600.0) / 1000.0 for r in rs)
        med = eff[len(eff) // 2]
        lo, hi = eff[len(eff) // 10], eff[-max(1, len(eff) // 10)]
        kmh, det = GUESS[mode]
        out[mode] = round(med, 2)
        print(f"{mode:6}{kmh:>12.1f}{kmh*det:>13.1f}{med:>11.1f}"
              f"{med/(kmh*det):>8.2f}{f'{lo:.0f}-{hi:.0f}':>9}")
    print("\nsuggested MODES for docs/app.js (effective km/h, detour folded in):")
    for mode, v in out.items():
        print(f"  {mode:5} kmh: {v}, detour: 1.0")


if __name__ == "__main__":
    {"probe": probe, "run": run, "fit": fit}[sys.argv[1] if len(sys.argv) > 1
                                             else "probe"]()
