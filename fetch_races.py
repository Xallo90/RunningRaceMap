# -*- coding: utf-8 -*-
"""
Fetch running races for Norway (Kondis terminlista, public Firestore backend)
and Sweden (lopplistan.se, server-rendered HTML), geocode locations, and write
web/races.geojson.

Usage:  python fetch_races.py [months_ahead]   (default 12)
"""
import json
import sys
import time
import hashlib
import math
import io
import re
import html as htmllib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
CACHE_FILE = BASE_DIR / "geocode_cache.json"
OUT_FILE = BASE_DIR / "web" / "races.geojson"

USER_AGENT = "race-map-mvp/0.2 (personal project; github.com/Xallo90/RunningRaceMap)"

# ---------------------------------------------------------------- Norway ----

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

TYPE_RANK = {
    "Kommune": 6, "By": 5, "Tettsted": 4, "Bygd": 3,
    "Tettbebyggelse": 3, "Grend": 2, "Bydel": 2,
}

# ---------------------------------------------------------------- Sweden ----

LOPPLISTAN_URL = "https://lopplistan.se/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Regions/areas that aren't towns — approximate representative points
SE_OVERRIDES = {
    "västkusten": (57.70, 11.97),
    "öland": (56.67, 16.63),
    "gotland": (57.53, 18.30),
    "höga kusten": (62.95, 18.27),
    "stockholms skärgård": (59.40, 18.90),
    "österlen": (55.55, 14.20),
    "bohuslän": (58.35, 11.45),
    "dalarna": (60.60, 15.00),
    "sverige": None,       # virtual / nationwide races – skip
    "hela sverige": None,
}

SE_WORD_DISTANCES = {"maraton": 42.195, "marathon": 42.195,
                     "halvmaraton": 21.0975, "halvmarathon": 21.0975}


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
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


def fetch_norway_month(start_iso, end_iso):
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


def no_categories(ev):
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


def no_distances_km(ev):
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


def fetch_norway(start, months):
    """Return normalized race dicts for Norway."""
    events, seen = [], set()
    cur = start
    for _ in range(months):
        nxt = (cur + timedelta(days=32)).replace(day=1)
        s, e = cur.strftime("%Y-%m-%dT00:00:00Z"), nxt.strftime("%Y-%m-%dT00:00:00Z")
        batch = fetch_norway_month(s, e)
        print(f"  NO {s[:10]} → {e[:10]}: {len(batch)} events")
        for ev in batch:
            if ev["_id"] not in seen:
                seen.add(ev["_id"])
                events.append(ev)
        cur = nxt
        time.sleep(0.3)

    races = []
    for ev in events:
        if ev.get("sportType") != "running":
            continue
        addr = ev.get("address") or {}
        if (addr.get("country") or "Norge") not in ("Norge", "Norway"):
            continue
        if not addr.get("town"):
            continue
        url = None
        for u in ev.get("urls") or []:
            if isinstance(u, dict) and u.get("url"):
                url = u["url"]
                break
        races.append({
            "id": ev["_id"],
            "country": "NO",
            "name": ev.get("name"),
            "date": ev.get("date"),
            "town": addr["town"],
            "area": addr.get("area"),
            "distanceSummary": ev.get("distanceSummaryString"),
            "distances_km": no_distances_km(ev),
            "categories": no_categories(ev),
            "url": url,
            "description": (ev.get("description") or "")[:300],
        })
    return races


def geocode_norway(town, area, cache):
    key = f"NO|{town}|{area or ''}"
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
            if (hit.get("skrivemåte") or "").lower() == town.lower():
                score += 3
            if score > best_score:
                best, best_score = [lat, lon], score
    except Exception as e:
        print(f"  NO geocode error for {key}: {e}")
    cache[key] = best
    time.sleep(0.15)
    return best


# ---------------------------------------------------------------- Sweden ----

def se_parse_distances(text):
    """'11.5, 4.5 km' / 'Maraton, 10.0 km' → sorted list of km floats."""
    out = set()
    for word, km in SE_WORD_DISTANCES.items():
        if word in text.lower():
            out.add(km)
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)", text):
        try:
            v = float(m.group(1).replace(",", "."))
            if 0 < v < 1000:
                out.add(round(v, 3))
        except ValueError:
            pass
    return sorted(out)


def fetch_sweden(start, months):
    """Scrape lopplistan.se list pages. Returns normalized race dicts."""
    end_date = (start + timedelta(days=months * 31)).strftime("%Y-%m-%d")
    start_date = start.strftime("%Y-%m-%d")
    races, seen = [], set()
    page, max_page = 1, 1
    while page <= max_page:
        html = http_get(f"{LOPPLISTAN_URL}?page={page}")
        if page == 1:
            nums = [int(n) for n in re.findall(r"\?page=(\d+)", html)]
            max_page = max(nums) if nums else 1
            print(f"  SE lopplistan.se: {max_page} pages")
        blocks = html.split('<div class="race ')[1:]
        found = 0
        for block in blocks:
            block = block[:3000]
            m_date = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})"', block)
            m_name = re.search(
                r'<a class="race__link" href="?([^ >"]+)"?[^>]*>\s*(.*?)\s*</a>',
                block, re.S)
            m_act = re.search(r'race__activity" title="?([^>"]+?)"?\s*>', block)
            m_dist = re.search(r'<div class="race__distance">\s*(.*?)\s*</div>',
                               block, re.S)
            m_loc = re.search(r'<div class="race__location">\s*(.*?)\s*</div>',
                              block, re.S)
            if not (m_date and m_name and m_act):
                continue
            activity = m_act.group(1).strip()
            if activity not in ("Löpning", "Trail"):
                continue
            date = m_date.group(1)
            if not (start_date <= date <= end_date):
                continue
            name = htmllib.unescape(re.sub(r"\s+", " ", m_name.group(2))).strip()
            key = (name.lower(), date)
            if key in seen:
                continue
            seen.add(key)
            href = m_name.group(1).strip()
            url = "https://lopplistan.se" + href if href.startswith("/") else href
            dist_text = htmllib.unescape(m_dist.group(1)).strip() if m_dist else ""
            town = htmllib.unescape(m_loc.group(1)).strip() if m_loc else None
            if not town:
                continue
            races.append({
                "id": "se" + hashlib.md5(f"{name}|{date}".encode()).hexdigest()[:12],
                "country": "SE",
                "name": name,
                "date": date + "T00:00:00Z",
                "town": town,
                "area": None,
                "distanceSummary": dist_text or None,
                "distances_km": se_parse_distances(dist_text),
                "categories": ["trail"] if activity == "Trail" else ["road"],
                "url": url,
                "description": "",
            })
            found += 1
        print(f"  SE page {page}: {found} running/trail races")
        page += 1
        time.sleep(0.6)  # be polite
    return races


def geocode_sweden(town, cache):
    key = f"SE|{town}"
    if cache.get(key) is not None:
        return cache[key]
    # try the full string first, then comma-separated parts (area before venue)
    parts = [p.strip() for p in town.split(",") if p.strip()]
    candidates = [town] + (list(reversed(parts)) if len(parts) > 1 else [])
    best = None
    for cand in candidates:
        low = cand.lower()
        if low in SE_OVERRIDES:
            best = list(SE_OVERRIDES[low]) if SE_OVERRIDES[low] else None
            break
        # partial override match ("Stockholms skärgår" ~ "stockholms skärgård")
        for ov, pos in SE_OVERRIDES.items():
            if pos and (ov.startswith(low) or low.startswith(ov)):
                best = list(pos)
                break
        if best:
            break
        try:
            params = urllib.parse.urlencode({
                "q": cand, "countrycodes": "se", "format": "jsonv2", "limit": 1})
            res = http_json(f"{NOMINATIM_URL}?{params}", timeout=30)
            time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
            if res:
                best = [float(res[0]["lat"]), float(res[0]["lon"])]
                break
        except Exception as e:
            print(f"  SE geocode error for {key} ({cand}): {e}")
    cache[key] = best
    return best


# ----------------------------------------------------------------- common ---

def jitter(event_id, lat, lon):
    """Deterministic small offset so events in the same town don't stack exactly."""
    h = int(hashlib.md5(event_id.encode()).hexdigest()[:8], 16)
    angle = (h % 360) * math.pi / 180
    radius = ((h >> 9) % 100) / 100 * 0.012
    return lat + radius * math.cos(angle), lon + radius * math.sin(angle)


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cache = load_cache()

    print("Fetching Norway (Kondis)…")
    no_races = fetch_norway(start, months)
    print(f"  → {len(no_races)} Norwegian running races")

    print("Fetching Sweden (lopplistan.se)…")
    se_races = fetch_sweden(start, months)
    print(f"  → {len(se_races)} Swedish running/trail races")

    features, skipped = [], 0
    for r in no_races + se_races:
        if r["country"] == "NO":
            pos = geocode_norway(r["town"], r["area"], cache)
        else:
            pos = geocode_sweden(r["town"], cache)
        if not pos:
            print(f"  no geocode: [{r['country']}] {r['town']} – {r['name']}")
            skipped += 1
            continue
        lat, lon = jitter(r["id"], pos[0], pos[1])
        props = {k: v for k, v in r.items()}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": props,
        })

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(
        {"type": "FeatureCollection",
         "generated": start.isoformat(),
         "features": features}, ensure_ascii=False), encoding="utf-8")

    n_no = sum(1 for f in features if f["properties"]["country"] == "NO")
    n_se = sum(1 for f in features if f["properties"]["country"] == "SE")
    print(f"\nWrote {len(features)} races → {OUT_FILE}  (NO: {n_no}, SE: {n_se})")
    print(f"Skipped {skipped} not geocodable")


if __name__ == "__main__":
    main()
