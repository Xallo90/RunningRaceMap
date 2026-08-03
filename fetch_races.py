# -*- coding: utf-8 -*-
"""
Fetch running races from Kondis terminlista's public Firestore backend,
geocode town names via Kartverket (GeoNorge), and write web/races.geojson.

Usage:  python fetch_races.py [months_ahead]   (default 12)
"""
import json
import sys
import time
import hashlib
import math
import io
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
CACHE_FILE = BASE_DIR / "geocode_cache.json"
OUT_FILE = BASE_DIR / "web" / "races.geojson"

# Public web-app config from terminlista.kondis.no (client-side, not a secret)
API_KEY = "AIzaSyAbf9X_CcYKC-WSAkVijyKc5m3vDgR8slY"
FIRESTORE_URL = (
    "https://firestore.googleapis.com/v1/projects/kondisapp/"
    "databases/(default)/documents:runQuery?key=" + API_KEY
)
GEONORGE_URL = "https://ws.geonorge.no/stedsnavn/v1/navn"

SELECT_FIELDS = [
    "name", "date", "address", "distances", "surfaces", "urls",
    "distanceSummaryString", "sportType", "description", "carouselName",
]

# navneobjekttype preference when several places share a name
TYPE_RANK = {
    "Kommune": 6, "By": 5, "Tettsted": 4, "Bygd": 3,
    "Tettbebyggelse": 3, "Grend": 2, "Bydel": 2,
}


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "race-map-mvp/0.1 (personal project)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fs_decode(v):
    """Decode a Firestore REST typed value to plain Python."""
    if not isinstance(v, dict):
        return v
    for k, val in v.items():
        if k == "stringValue":
            return val
        if k == "integerValue":
            return int(val)
        if k == "doubleValue":
            return float(val)
        if k == "booleanValue":
            return bool(val)
        if k == "timestampValue":
            return val
        if k == "nullValue":
            return None
        if k == "mapValue":
            return {kk: fs_decode(vv) for kk, vv in (val.get("fields") or {}).items()}
        if k == "arrayValue":
            return [fs_decode(x) for x in (val.get("values") or [])]
    return None


def fetch_month(start_iso, end_iso):
    query = {
        "structuredQuery": {
            "from": [{"collectionId": "mainEvents"}],
            "where": {"compositeFilter": {"op": "AND", "filters": [
                {"fieldFilter": {"field": {"fieldPath": "date"},
                                 "op": "GREATER_THAN_OR_EQUAL",
                                 "value": {"timestampValue": start_iso}}},
                {"fieldFilter": {"field": {"fieldPath": "date"},
                                 "op": "LESS_THAN",
                                 "value": {"timestampValue": end_iso}}},
            ]}},
            "orderBy": [{"field": {"fieldPath": "date"}, "direction": "ASCENDING"}],
            "select": {"fields": [{"fieldPath": f} for f in SELECT_FIELDS]},
        }
    }
    rows = http_json(FIRESTORE_URL, query)
    out = []
    for row in rows:
        doc = row.get("document")
        if not doc:
            continue
        fields = {k: fs_decode(v) for k, v in (doc.get("fields") or {}).items()}
        fields["_id"] = doc["name"].rsplit("/", 1)[-1]
        out.append(fields)
    return out


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def geocode(town, area, cache):
    key = f"{town}|{area or ''}"
    if key in cache:
        return cache[key]
    params = urllib.parse.urlencode(
        {"sok": town, "treffPerSide": 25, "side": 1, "fuzzy": "true"})
    best, best_score = None, -1
    try:
        res = http_json(f"{GEONORGE_URL}?{params}", timeout=30)
        for hit in res.get("navn") or []:
            pt = hit.get("representasjonspunkt") or {}
            lat, lon = pt.get("nord"), pt.get("øst")
            if lat is None or lon is None:
                continue
            score = TYPE_RANK.get(hit.get("navneobjekttype"), 0)
            fylker = [f.get("fylkesnavn", "") for f in hit.get("fylker") or []]
            if area and any(area.lower() in f.lower() or f.lower() in area.lower()
                            for f in fylker):
                score += 10
            name_exact = (hit.get("skrivemåte") or "").lower() == town.lower()
            if name_exact:
                score += 3
            if score > best_score:
                best, best_score = [lat, lon], score
    except Exception as e:
        print(f"  geocode error for {key}: {e}")
    cache[key] = best
    time.sleep(0.15)  # be polite to the free API
    return best


def jitter(event_id, lat, lon):
    """Deterministic small offset so events in the same town don't stack exactly."""
    h = int(hashlib.md5(event_id.encode()).hexdigest()[:8], 16)
    angle = (h % 360) * math.pi / 180
    radius = ((h >> 9) % 100) / 100 * 0.012
    return lat + radius * math.cos(angle), lon + radius * math.sin(angle)


def categories(ev):
    surfaces = ev.get("surfaces") or []
    cats = set()
    if "terrain_running" in surfaces:
        cats.add("trail")
    if "asphalt" in surfaces or "gravel" in surfaces:
        cats.add("road")
    for d in ev.get("distances") or []:
        if isinstance(d, dict) and d.get("mountain"):
            cats.add("trail")
    return sorted(cats)


def distances_km(ev):
    out = []
    for d in ev.get("distances") or []:
        if not isinstance(d, dict):
            continue
        raw = d.get("length")
        try:
            meters = float(str(raw).replace(",", "."))
            if 0 < meters < 1_000_000:
                out.append(round(meters / 1000, 3))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cache = load_cache()
    events, seen = [], set()

    cur = start
    for _ in range(months):
        nxt = (cur + timedelta(days=32)).replace(day=1)
        s, e = cur.strftime("%Y-%m-%dT00:00:00Z"), nxt.strftime("%Y-%m-%dT00:00:00Z")
        batch = fetch_month(s, e)
        print(f"{s[:10]} → {e[:10]}: {len(batch)} events")
        for ev in batch:
            if ev["_id"] not in seen:
                seen.add(ev["_id"])
                events.append(ev)
        cur = nxt
        time.sleep(0.3)

    running = [e for e in events if e.get("sportType") == "running"]
    print(f"\nTotal: {len(events)} events, {len(running)} running")

    features, skipped_geo, skipped_foreign = [], 0, 0
    for ev in running:
        addr = ev.get("address") or {}
        town, area = addr.get("town"), addr.get("area")
        country = addr.get("country") or "Norge"
        if country not in ("Norge", "Norway"):
            skipped_foreign += 1
            continue
        if not town:
            skipped_geo += 1
            continue
        pos = geocode(town, area, cache)
        if not pos:
            print(f"  no geocode: {town} ({area}) – {ev.get('name')}")
            skipped_geo += 1
            continue
        lat, lon = jitter(ev["_id"], pos[0], pos[1])
        url = None
        for u in ev.get("urls") or []:
            if isinstance(u, dict) and u.get("url"):
                url = u["url"]
                break
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "id": ev["_id"],
                "name": ev.get("name"),
                "date": ev.get("date"),
                "town": town,
                "area": area,
                "distanceSummary": ev.get("distanceSummaryString"),
                "distances_km": distances_km(ev),
                "categories": categories(ev),
                "surfaces": ev.get("surfaces") or [],
                "url": url,
                "description": (ev.get("description") or "")[:300],
            },
        })

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(
        {"type": "FeatureCollection",
         "generated": start.isoformat(),
         "features": features}, ensure_ascii=False), encoding="utf-8")

    print(f"\nWrote {len(features)} races → {OUT_FILE}")
    print(f"Skipped: {skipped_geo} not geocodable, {skipped_foreign} outside Norway")


if __name__ == "__main__":
    main()
