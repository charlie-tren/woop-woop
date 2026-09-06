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

## Ferries, corrected again 06/09/2026 - they ARE the cause

The section above concluded that ferries were not a cause and removed
`avoid_features`. That conclusion was drawn from Brisbane alone and does not hold. It is
reversed here, with the measurement that reverses it.

Sydney is the city the page opens on, and Sydney has a ferry network across the middle of
the answer. On the LIVE 60 minute **walking** isochrone from Rushcutters Bay:

| point | walk from the origin | inside the isochrone |
|---|---|---|
| Cremorne Point wharf | ~9 km, around the bridge | **yes** |
| Mosman Bay wharf | ~11 km | **yes** |
| Taronga Zoo wharf | ~12 km | **yes** |
| Kirribilli | ~5 km over the bridge | yes, legitimately |
| Watsons Bay wharf | ~11 km | no |

Three wharves that are a two to three hour walk away are inside a one hour walking
isochrone. Only a ferry puts them there. **31.9% of that polygon sits on water**, measured
against the 250 m land mask the page ships - an order of magnitude worse than the Brisbane
figure that produced the wrong conclusion.

`avoid_features: ["ferries"]` is back, for **every** profile rather than just foot. The
reason is not that a ferry is cheating: it is that openrouteservice models a ferry as a
link with a speed and **no timetable**. You board the instant you arrive and never wait,
so a twenty minute headway is invisible to the isochrone. That makes a crossing an
unfunded claim in a way an ordinary road is not, whatever the mode. The earlier argument
for keeping ferries - that the page hands out Google Maps driving directions, which do
route over them - is real but much weaker, and it only ever applied to driving.

A 400 from upstream retries without the option, because the valid `avoid_features` set is
documented per profile and a rejection would otherwise take out a whole mode.

**The methodological point, which is the expensive part.** Brisbane was the wrong city to
generalise from and nothing about the test said so. One city is one sample; a network
feature that does not bind there can dominate somewhere else. Test the case the product
actually opens on, and when a check comes back negative, ask what would have to be true
for it to come back positive before concluding the mechanism is absent.

## The canal, and a metric that nearly sent me the wrong way (06/09/2026)

Charlie: "one minor issue i spotted though with the canal in alexandria". Part of
Alexandria Canal was under the fill while the wide basin beside it was not.

**The filter was not the cause.** The obvious suspect was the 9x9 majority pass, because
it is symmetric: it cannot tell a 3 px ferry dash (thin LAND on water, remove) from a 3 px
canal (thin WATER in land, keep). That is a genuine property of the operator and it looked
like the answer. Two replacements were tried before the picture was looked at:

| operator | canal | side effect |
|---|---|---|
| majority 9x9 | erased in principle | none |
| closing 9x9 | kept | water 3.2% -> 13.9%, noise smeared into holes |
| component area filter | ~same as majority | none |

Then the render was actually inspected, and at z16 **the majority filter keeps the canal
perfectly well**. All three operators score about the same on the metric I was tuning -
"share of raw water kept", 30% - because that number is dominated by **28,818 water
components of one or two pixels**, which are anti-aliasing noise around every pale feature
on the map. The top five components hold 30% of the water pixels and everything else is
speckle. I was optimising a number that barely moved with the thing I cared about, which
is the failure `feedback_fit-the-outcome` describes, in a session that had already been
bitten by it once.

**The real cause is the zoom the fill is rendered at.** It was rendered ONCE, at a zoom
chosen to fit the WHOLE isochrone inside 24 tiles, then stretched as you zoomed in. For a
60 minute walk across Sydney the bbox is 0.095 x 0.081 degrees:

| z | tiles | m/px |
|---|---|---|
| 16 | 324 | 2.0 |
| 15 | 90 | 4.0 |
| 14 | 25 | 7.9 |
| **13** | **9** | **15.9  <- chosen** |

Alexandria Canal is about 20 m wide, so it was roughly ONE pixel in the source tiles. No
filter can preserve what the sampling never captured.

**Fix: render the part of the isochrone that is on screen, at the zoom you are looking
at**, and redraw on moveend, debounced 200 ms. The same 24 tile budget then buys detail
where the eye is, instead of covering country off the edge of the map. Measured in the
browser: 768x512 at 31.7 m/px zoomed out, 1536x768 at 1.0 m/px zoomed in.

The lesson worth keeping is not about canals. **Three plausible fixes were proposed and
two were coded before anyone looked at a rendering of the thing being fixed.** The picture
settled it in one glance and contradicted the metric.

## The water colour test was catching whole land uses (06/09/2026)

Charlie: "why is rookwood cemetery not coloured in when I select ride", "same question
but for the airport", "there are just some random unhighlighted spots".

One cause for all three. The classifier asked whether the SUM of the three channel
differences from water `#aad3df` was within 60, and that treats a colour far off in one
channel the same as one slightly off in all three:

| OSM carto fill | rgb | sum distance | caught? |
|---|---|---|---|
| water `#aad3df` | 170,211,223 | 0 | yes, correctly |
| **cemetery `#aacbaf`** | 170,203,175 | **56** | **yes, wrongly** |
| **airport apron** | 187,187,204 | **60** | **yes, exactly on the line** |
| **grey building** | 212,211,211 | **54** | **yes, wrongly** |

Cemetery green has the SAME red as water and green within 8; the entire miss is 48 points
of blue, which the sum buries. Measured on the real tile: 62,468 pixels of solid cemetery
green over Rookwood, which is why the whole cemetery went unfilled. The neutral greys are
also where most of the pixel speckle came from - 28,818 water components in one Alexandria
window, nearly all one or two pixels.

**Now every channel must be within 20 individually.** Share of pixels classified water,
before and after:

| place | sum <= 60 | per channel <= 20 |
|---|---|---|
| Rookwood Cemetery | 31.14% | **0.48%** |
| Sydney Airport | 26.48% | 12.32% (it has the Cooks River and Botany Bay on it) |
| Centennial Park | 8.56% | 5.30% (the ponds) |
| Sydney Harbour | 79.64% | 77.58% |

20 and not 25: at 25 the airport apron comes back. Cemetery is safe at any per-channel
tolerance below 48, so it is not the binding constraint.

**Known residual.** Tightening also rejects a grey-blue around 184,191,201 that appears
over the harbour where a pattern is drawn on top of the water - the hatched naval area by
Garden Island is the visible case, and it is the grey patch in Charlie's harbour
screenshot. That is a pattern-over-water problem rather than a colour-distance one: the
right fix is to fill land components fully enclosed by water, not to widen the tolerance,
which would let the airport back in. Not built yet.

## What cycling-regular actually assumes (06/09/2026)

Charlie: "the riding one actually takes an hour 17 not an hour". Measured rather than
assumed. Straight-line reach of the openrouteservice cycling bands from Sydney CBD:

| band | max straight-line | implied km/h straight line |
|---|---|---|
| 15 min | 4.48 km | 17.9 |
| 30 min | 8.80 km | 17.6 |
| 45 min | 12.76 km | 17.0 |
| 60 min | 16.78 km | 16.8 |

A straight-line reach implies a HIGHER speed along the road, because roads bend. So
cycling-regular is assuming somewhere north of 20 km/h sustained, with no lights, no
traffic and no penalty for Sydney's hills. A realistic urban door-to-door average is about
15 km/h on the road, which is roughly 11-12 km/h straight line.

Two independent numbers that agree in direction and roughly in size: Charlie's 77 minutes
against a claimed 60 is 1.28x, and the implied-speed gap is about 1.4x.

**Deliberately NOT fitting a correction factor from this.** One route and one
implied-speed estimate is exactly the sample size that produced the ferry mistake earlier
in the same week. The measurement is recorded so a calibration over a dozen real routes is
cheap to do properly; a fudge factor picked from n=1 would be a guess wearing a number.
Logged as its own TODO.

## Calibrated against a second engine (06/09/2026) - and the answer is not a factor

Charlie asked whether there is a more accurate way to know than inferring speed from
isochrone geometry. There is, and it is cheap.

**Method.** Sample points ON THE BOUNDARY of the openrouteservice 60 minute isochrone.
The boundary is where ORS asserts exactly 60 minutes, so no banding and no assumptions
are needed. Then ask a completely different engine how long the same origin-to-point trip
takes. Valhalla's public instance is keyless, is a different codebase with a different
road model, and answers `pedestrian`, `bicycle` and `auto`. 16 points per mode, spread by
bearing around the ring, from Sydney CBD.

**Result: how long a trip ORS calls 60 minutes really takes.**

| mode | n | min | median | mean | max |
|---|---|---|---|---|---|
| foot | 16 | 0.80 | **1.15** | 1.12 | 1.46 |
| bike | 16 | 0.55 | **1.17** | 1.09 | 1.41 |

(car could not be measured in this run: the Worker returned 502 for the 60 minute driving
isochrone. Repeat before drawing any conclusion about driving.)

**Two findings, and the second one matters more.**

1. **It is not a cycling problem.** Walking carries essentially the same bias as riding,
   1.15 against 1.17. The earlier note here framed it as "cycling-regular is optimistic",
   which was the wrong frame: whatever it is, it is not specific to the bike profile.
   Charlie's measured 1.28 sits inside the range and above the median, so his single
   observation was real and slightly worse than typical.

2. **A scalar correction is the WRONG fix, and not for the reason previously given.**
   The earlier note declined to fit a factor because n=1. Now n=32, and the reason to
   decline is much better: the per-route spread, 0.55 to 1.46, is far larger than the
   ~15% median bias. Multiplying every answer by 1.15 would fix the median and make most
   individual answers worse in one direction or the other. There is no single number that
   makes an isochrone tell the truth about a particular trip.

**What the accurate version actually is.** Stop asking the isochrone to be accurate. Use
it for what it is good at - shortlisting candidates cheaply from one call - and then ask a
router for the real duration to the ONE point that wins. That converts a 15% median bias
with a 90-point spread into an exact number for the trip actually being offered.

Valhalla makes this practical in a way ORS does not:

    OPTIONS https://valhalla1.openstreetmap.de/route
    Access-Control-Allow-Origin: *
    Access-Control-Allow-Methods: GET, POST, OPTIONS

**Keyless and CORS-open, so the page can call it directly** - no API key to hide, no
Worker in the path, and therefore no dependence on the Cloudflare deploy that is currently
blocked. One request per answer, not per slider drag.
