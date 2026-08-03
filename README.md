# 🏃 Løpskart – Running Race Map for Norway & Sweden

An interactive map of running races in Norway and Sweden. Browse upcoming races
on a Google Maps-style interface, filter by country, surface (trail/road),
distance and date, and sort by how far they are from you.

**Data:** 🇳🇴 [Kondis terminlista](https://terminlista.kondis.no) (public Firestore API) ·
🇸🇪 [Lopplistan](https://lopplistan.se) (HTML, robots.txt allows crawling) ·
**Geocoding:** [Kartverket / GeoNorge](https://ws.geonorge.no) (NO) +
[Nominatim](https://nominatim.org) (SE) ·
**Map:** [Leaflet](https://leafletjs.com) + OpenStreetMap

## Features

- 📍 Clustered map of all upcoming running races (next 12 months)
- 🌍 Country filter: Norway, Sweden or both
- 🥾 Surface filter: trail (`terrain_running`), road (`asphalt`/`gravel`), or both
- 📏 Distance filter: preset chips (≤5 km … ultra) or a custom min–max range,
  matched against exact race distances in meters
- 📅 Date range filter
- 🧭 "Find me" (browser geolocation) or free-text place search — the race list
  re-sorts by distance from you
- Popups with date, distances, surface, description and race website link

## Run it locally

```bash
python -m http.server 8123 --directory web
```

Then open http://localhost:8123.

## Refresh the data

```bash
python fetch_races.py 12   # months ahead, default 12
```

The script pulls Norwegian events from Kondis's public Firestore backend
(`sportType == running`), scrapes Swedish "Löpning"/"Trail" races from
lopplistan.se's server-rendered list pages, geocodes each unique location once
(GeoNorge for Norway, Nominatim for Sweden — cached in `geocode_cache.json`),
and writes a merged `web/races.geojson`.

## How it works

- **No backend.** The whole app is static files; the data is one GeoJSON file
  (~800 features). Host it anywhere (GitHub Pages works out of the box).
- Races only carry town + county, so pins sit at town centers with a small
  deterministic jitter so same-town races don't stack exactly. Marker
  clustering handles the rest.
- Colors: 🟢 trail · 🔵 road · 🟣 both · ⚪ unknown surface.

## Credits & fair use

Race data belongs to [Kondis](https://www.kondis.no) and
[Lopplistan](https://lopplistan.se). This is a personal, non-commercial project
reading the same public API Kondis's own site uses and crawling Lopplistan
within its robots.txt (which allows all). The fetch script throttles and
caches, and Nominatim geocoding respects the 1 req/s usage policy. If this
ever becomes more than a hobby project, ask both sites first.
