"""How long does a trip to the EDGE of an openrouteservice isochrone really take?

The edge is where ORS asserts exactly 60 minutes, so it needs no banding and no
assumptions: ask a second, independent engine how long the same origin-to-point trip
takes, and the ratio is the bias.

Valhalla's public instance is keyless and is a different engine with a different road
model, so it is a real second opinion rather than a restatement of the first.
"""
import json, math, os, time, urllib.request, urllib.error, urllib.parse

SCR = r"C:\Users\charl\AppData\Local\Temp\claude\C--Users-charl-Documents\1826aa3d-c6d9-4614-a7b1-00253333be2c\scratchpad"
WORKER = "https://woop-woop-iso.charlie-tren.workers.dev"
VALHALLA = "https://valhalla1.openstreetmap.de/route"
UA = "woop-woop-calibration/1.0 (charlietrenorden.com)"
ORIGIN = (-33.8688, 151.2093)          # Sydney CBD
MINUTES = 60
N_POINTS = 16
MODES = [("foot", "pedestrian"), ("bike", "bicycle"), ("car", "auto")]


def isochrone(mode, mins):
    f = os.path.join(SCR, "cal_%s_%d.json" % (mode, mins))
    if not os.path.exists(f):
        body = json.dumps({"mode": mode, "lat": ORIGIN[0], "lon": ORIGIN[1],
                           "seconds": mins * 60}).encode()
        req = urllib.request.Request(WORKER, data=body, headers={
            "Origin": "https://charlietrenorden.com",
            "Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            open(f, "wb").write(r.read())
        time.sleep(1)
    return json.load(open(f))


def valhalla(costing, lat, lon, tries=3):
    q = {"locations": [{"lat": ORIGIN[0], "lon": ORIGIN[1]}, {"lat": lat, "lon": lon}],
         "costing": costing}
    url = VALHALLA + "?json=" + urllib.parse.quote(json.dumps(q))
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            return d["trip"]["summary"]["time"] / 60.0, d["trip"]["summary"]["length"]
        except Exception as e:
            if a == tries - 1:
                return None, None
            time.sleep(2 + 2 * a)
    return None, None


print("Origin: Sydney CBD %.4f, %.4f   ORS isochrone edge = %d min by construction\n"
      % (ORIGIN[0], ORIGIN[1], MINUTES))

summary = []
for mode, costing in MODES:
    try:
        geo = isochrone(mode, MINUTES)
    except urllib.error.HTTPError as e:
        print("  %s: isochrone refused (%s)" % (mode, e.code))
        continue
    ring = geo["features"][0]["geometry"]["coordinates"][0]
    # evenly spaced by index around the ring, which spreads by bearing
    step = max(1, len(ring) // N_POINTS)
    pts = [ring[i * step] for i in range(N_POINTS) if i * step < len(ring)]
    print("%s  (ORS %s, Valhalla %s)  %d edge points" %
          (mode.upper(), mode, costing, len(pts)))
    ratios = []
    for lon, lat in pts:
        mins, km = valhalla(costing, lat, lon)
        time.sleep(1.1)
        if mins is None:
            print("    %.4f, %.4f   valhalla: no route" % (lat, lon))
            continue
        ratios.append(mins / MINUTES)
        print("    %.4f, %.4f   %6.1f km   valhalla %6.1f min   ratio %.2f"
              % (lat, lon, km, mins, mins / MINUTES))
    if ratios:
        ratios.sort()
        med = ratios[len(ratios) // 2]
        summary.append((mode, len(ratios), min(ratios), med, max(ratios),
                        sum(ratios) / len(ratios)))
        print("    -> n=%d  median ratio %.2f  mean %.2f  range %.2f to %.2f\n"
              % (len(ratios), med, sum(ratios) / len(ratios), min(ratios), max(ratios)))

print("\nSUMMARY: how long a trip ORS calls 60 minutes really takes")
print("  mode   n   min   median   mean   max")
for mode, n, lo, med, hi, mean in summary:
    print("  %-5s %3d  %.2f   %.2f    %.2f   %.2f" % (mode, n, lo, med, mean, hi))
print("\n  ratio 1.00 = the two engines agree. Above 1.00 = ORS is optimistic.")
json.dump([{"mode": m, "n": n, "min": lo, "median": med, "mean": mean, "max": hi}
           for m, n, lo, med, hi, mean in summary],
          open(os.path.join(SCR, "calibration.json"), "w"), indent=1)
