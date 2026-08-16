# Woop Woop

The middle of nowhere, precisely located.

Pick a starting point, a way of travelling and how long you have; get the spot in range
that is furthest from every road, building, railway, power line and runway - and how to
walk in to it.

Live at <https://charlietrenorden.com/woop-woop/>.

Isochrone tools are everywhere and they all draw the same thing: the area you can
reach. None of them rank what is *inside* it. The maps that do measure emptiness
(Germany's points-furthest-from-a-road, Britain's most remote point, Australia's) are
fixed national answers with no start point and no travel budget. This is the
intersection: your location, your transport, your time, and then the emptiest point in
range.

## How it works

    build/extract.py   pull roads, buildings, railways, power lines and runways out of a
                       Geofabrik .osm.pbf, plus coastline and water, over a buffered box
    build/raster.py    burn them into a 100 m grid, take a Euclidean distance transform,
                       mask the ocean, mark the inner box answerable
    build/tiles.py     export the serve box as one 8-bit PNG (1.2 MB for all of SEQ)
    build/icons.py     render the favicon files from favicon.svg
    docs/              the site: everything is measured in the browser from that PNG

Rebuild:

    python build/extract.py data/australia-latest.osm.pbf
    python build/raster.py  data/seq-features.npz 100 data/seq-dist.npz
    python build/tiles.py

## Things that are easy to get wrong

* **Clipping ways to the box truncates them.** Keep whole ways if any vertex is inside.
  Clipping left a two-row gap in the coastline at the southern edge, the ocean flood
  escaped through it, and 98.6% of the region came back as sea.
* **Cells near the edge of the extract have inflated distances**, because the road
  nearest them was never loaded. Hence the 0.6 degree buffer, and answering only inside
  it.
* **Do not dilate the coastline barrier.** It seals narrow tidal channels and welds
  North Stradbroke and Russell Island to the mainland, which lets the app answer with
  somewhere you need a ferry to reach.
* **The naive maximum is usually degenerate** - an island, or open water. Reachability
  is a hard filter, not a display option.

## Status

The emptiness measurement is exact and verified against an independent nearest-neighbour
search (agreement within 40 m, inside one cell). **The travel range is still a
straight-line estimate**; the real version calls a routing engine for an isochrone.
Coverage is South East Queensland only.

Data: [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via
[Geofabrik](https://download.geofabrik.de/).
