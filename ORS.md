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
