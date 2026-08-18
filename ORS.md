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

## Use api.heigit.org, not api.openrouteservice.org

The dashboard carries a deprecation notice: **`api.openrouteservice.org` is being
retired in favour of `api.heigit.org`.** Nearly every tutorial and SDK example still
uses the old host, so this is worth writing down - build against the new one.

Isochrones are a POST to `/v2/isochrones/{profile}` with the key in an `Authorization`
header, `locations` as `[[lon, lat]]` (longitude FIRST), `range` in seconds, and
`range_type: "time"`. Profiles map to the three modes as
`foot-walking`, `cycling-regular`, `driving-car`.

## Why it goes in a Worker

A key in `app.js` is a key anyone can read and spend, and 500 calls a day is a quota
worth about ten minutes of somebody else's curiosity. The page calls a Cloudflare
Worker; the Worker holds the secret and forwards to HeiGIT.
