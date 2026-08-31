# openrouteservice — what the free key actually allows

Read off the HeiGIT key dashboard on 19/08/2026, for the free ("Basic") plan.
The key itself lives in a Cloudflare Worker secret and appears nowhere in this repo.

## The number that binds

**Isochrones V2: 500 per day, 20 per minute.**

That is the only endpoint this project needs, and it is the smallest of the daily
quotas alongside Matrix. Every change of start point, travel mode or time slider is one
isochrone call, so a single person fiddling with the controls can spend a hundred in a
few minutes. Two consequences for the client:

* **Cache by (rounded origin, mode, minutes).** Nudging the slider back and forth must
  not re-spend the quota.
* **Debounce the slider**, and only call on release rather than on every input event.
  Twenty per minute is roughly one every three seconds.

## Everything on the plan

| Endpoint | Per day | Per minute |
| --- | --- | --- |
| Directions V2 | 2,000 | 40 |
| Export V2 | 100 | 5 |
| **Isochrones V2** | **500** | **20** |
| Matrix V2 | 500 | 40 |
| Snap V2 | 2,000 | 100 |
| Elevation | 2,000 | 40 |
| Geocoding | 3,000 | 100 |
| Optimization | 500 | 40 |
| POIs | 500 | 60 |

Quotas renew daily. The key does not expire.

## Which host - the notice is ahead of reality

The dashboard says `api.openrouteservice.org` is being retired in favour of
`api.heigit.org`. **Tested on 19/08/2026, and the new host is not serving yet:**
`api.heigit.org` returns an nginx 404 on both `/v2/isochrones/...` and
`/ors/v2/isochrones/...`, while `api.openrouteservice.org` answers 200.

So this builds against the OLD host, and the switch is a thing to re-test rather than
assume. Taking the notice at face value cost a 404 and a confused ten minutes.

Isochrones are a POST to `/v2/isochrones/{profile}` with the key in an `Authorization`
header, `locations` as `[[lon, lat]]` (longitude FIRST), `range` in seconds, and
`range_type: "time"`. Profiles map to the three modes as
`foot-walking`, `cycling-regular`, `driving-car`.

## The one-hour ceiling is DRIVING ONLY

Corrected 31/08/2026. The original note here read "Isochrones stop at one hour" and
generalised a single measurement to all three profiles. That was wrong, and it cost the
project real range: `app.js` carried one `ISO_MAX_MINUTES = 60`, so walking and riding
were dropped to the estimate circle above an hour for months, for no reason at all.

Re-measured against the live Worker from the page's own origin:

| profile | 120 min | 240 min |
| --- | --- | --- |
| `foot-walking` | 200 | 200 |
| `cycling-regular` | 200 | 200 |
| `driving-car` | error 3004, "Maximum possible value is 3600" | - |

openrouteservice's restrictions page gives the actual limits: **foot to 20 hours,
cycling to 5 hours, driving to 1 hour.** Range DISTANCE is capped at 120 km on all
profiles, which time-based ranges do not touch.

So the ceiling belongs per profile, and only driving ever falls back to a circle.
Our own Worker caps at `MAX_SECONDS = 4 * 3600`, which is now the binding constraint
for foot and bike rather than anything upstream.

The lesson is the one already in `feedback-cover-and-verify-breadth`: a limit measured
on ONE input is a fact about that input. Three requests would have caught this, and the
original note was written after testing exactly one.

## Nested ranges are one call

`range` takes an ARRAY - `[900, 1800, 2700, 3600]` returns four nested polygons for a
single request against the quota. The page needs those bands to charge the walked leg
against the time budget, so this is what the Worker should send. It currently builds
`range: [seconds]` from one number and the page fires N parallel requests instead,
which works and caches but spends N times the quota on a cold view. Fold it into the
next deploy.

## Why it goes in a Worker

A key in `app.js` is a key anyone can read and spend, and 500 calls a day is a quota
worth about ten minutes of somebody else's curiosity. The page calls a Cloudflare
Worker; the Worker holds the secret and forwards to HeiGIT.

## What the calibration measured (19/08/2026, 102 real isochrones)

Sampled across Australia, from city to desert, at 30 and 60 minutes for each mode.
Six of 108 calls returned HTTP 500 - openrouteservice cannot always route from a point
on a remote track, which is worth knowing before the exact-check relies on it.

| mode | time | circle the page draws | equal-area radius | furthest reach | shape |
| --- | --- | --- | --- | --- | --- |
| walk | 1 h | 3.6 km | 0.68 km | 5.16 km | 7.5x |
| ride | 1 h | 11.2 km | 4.53 km | 14.65 km | 3.2x |
| drive | 1 h | 49.0 km | 29.54 km | 60.55 km | 2.0x |

**The headline is the last column, not the others.** "Shape" is furthest reach divided
by equal-area radius: a circle scores 1.0, and the real reachable set scores between
2 and 7.5. An hour on foot reaches 5 km along paths and covers 1.5 km2 of ground - a
starfish, not a disc. **No single radius can be right**, and the interesting error is
not the size of the circle but the fact that it IS a circle.

So the calibration deliberately did NOT change the model. Fitting to equal-area, which
was the plan going in, would have shrunk an hour's walk from 3.6 km to 680 m and made
the app far worse while looking like a rigorous improvement. The current guesses sit
between the two measurements and are a defensible middle.

What this does justify is the runtime exact-check: the shape is the whole story, and
only the real polygon has it. One further correction worth folding in whenever the
model is next touched - openrouteservice walks at about 5 km/h, not the 4.5 assumed
here, which is most of why the walking tip beats the assumed radius.

## Water spill: what the isochrone actually does, measured 26/08/2026

The first diagnosis here was wrong in two ways and is corrected below. Both errors came
from the same habit: quoting a number off a mask without asking what the mask can
resolve, and asserting a mechanism without testing it.

**Ferries are not a cause.** The claim was that a ferry puts unreachable land inside the
shape and drags the hull over the bay to connect it. Tested against the Brisbane 60 min
drive by point-in-polygon on every ferry-only island around the city - Dunwich and Point
Lookout on North Stradbroke, Russell, Macleay - and the isochrone contained NONE of
them. The easternmost vertex of the whole shape is on land. `avoid_features: ["ferries"]`
was removed again rather than kept as cheap insurance, because the page hands the user
Google Maps driving directions and those do route over ferries: suppressing them here
would make the reachable set disagree with the directions offered for reaching it.

**Most of the headline water figure was mask resolution, not spill.** The first pass
sampled the polygon against the build's 2 km ocean grid and reported Brisbane 3.2% of
the shape over water, Hobart 2.6%, Sydney 0.4%. Re-measured with a distance transform,
asking how far each "wet" sample sits from the nearest land cell:

| start    | over "ocean" | within 1 cell of land | more than 2 cells offshore | worst |
|----------|--------------|-----------------------|----------------------------|-------|
| Brisbane | 3.2%         | 66%                   | 11%                        | 6.4 km |
| Hobart   | 2.6%         | 72%                   | 5%                         | 4.4 km |
| Sydney   | 0.4%         | 100%                  | 0%                         | 2 km  |

A 2 km grid cannot place a coastline to better than a cell, so anything inside one cell
of land is the grid, not the shape. Sydney's spill is entirely that. **The genuinely
offshore share is about 0.35% of the Brisbane shape and 0.13% of Hobart's**, not 3.2%
and 2.6%.

**What is real is the hull spanning a bay.** Brisbane's offshore samples cluster around
-27.32, 153.13, which is Bramble Bay between Redcliffe and the northern suburbs: two
reachable shores facing each other, with the generalised hull bridging the water between
them. That is what `smoothing: 0` targets, and it is the only part of the original
diagnosis that survived.

The lesson for next time is the one already in `feedback_independent-verification`: the
2 km mask came out of the same build as the peaks, and a figure quoted off it inherits
its resolution. Ask what the instrument can resolve before quoting it to three
significant figures.
