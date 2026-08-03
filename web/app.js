/* Løpskart MVP – Leaflet + Kondis terminlista data (races.geojson) */

const COLORS = { trail: "#2e7d32", road: "#1565c0", both: "#6a1b9a", unknown: "#757575" };

const map = L.map("map", { zoomControl: true }).setView([63.0, 15.0], 5);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Løpsdata: <a href="https://terminlista.kondis.no">Kondis</a> + <a href="https://lopplistan.se">Lopplistan</a>',
}).addTo(map);

const cluster = L.markerClusterGroup({
  showCoverageOnHover: false,
  maxClusterRadius: 45,
});
map.addLayer(cluster);

let allRaces = [];        // [{feature, marker, latlng}]
let userPos = null;       // L.LatLng
let userMarker = null;

const state = {
  country: "all",
  surface: "all",
  distMin: 0,
  distMax: 999,
  dateFrom: null,
  dateTo: null,
};

/* ---------- helpers ---------- */

function colorFor(cats) {
  if (cats.includes("trail") && cats.includes("road")) return COLORS.both;
  if (cats.includes("trail")) return COLORS.trail;
  if (cats.includes("road")) return COLORS.road;
  return COLORS.unknown;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("nb-NO", {
    weekday: "short", day: "numeric", month: "short", year: "numeric",
  });
}

function fmtKm(kms) {
  if (!kms || !kms.length) return null;
  return kms.map(k => (k % 1 ? k.toFixed(1).replace(".", ",") : k) + " km").join(", ");
}

function haversineKm(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLon = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function surfaceLabel(cats) {
  if (cats.includes("trail") && cats.includes("road")) return "Terreng + vei";
  if (cats.includes("trail")) return "Terreng";
  if (cats.includes("road")) return "Vei";
  return "Ukjent underlag";
}

function popupHtml(p) {
  const col = colorFor(p.categories);
  return `
    <h3>${p.name}</h3>
    <div>📅 ${fmtDate(p.date)}</div>
    <div>📍 ${p.country === "SE" ? "🇸🇪" : "🇳🇴"} ${p.town}${p.area ? ", " + p.area : ""}</div>
    ${p.distanceSummary ? `<div>📏 ${p.distanceSummary}</div>` : ""}
    <div class="tags"><span class="tag" style="background:${col}">${surfaceLabel(p.categories)}</span></div>
    ${p.description ? `<div style="color:#555">${p.description.slice(0, 160)}${p.description.length > 160 ? "…" : ""}</div>` : ""}
    ${p.url ? `<div style="margin-top:4px"><a href="${p.url}" target="_blank" rel="noopener">Nettside →</a></div>` : ""}
  `;
}

/* ---------- filtering ---------- */

function matches(p) {
  if (state.country !== "all" && p.country !== state.country) return false;
  if (state.surface !== "all" && !p.categories.includes(state.surface)) return false;

  const fullRange = state.distMin <= 0 && state.distMax >= 999;
  if (!fullRange) {
    const kms = p.distances_km || [];
    if (!kms.length) return false;
    if (!kms.some(k => k >= state.distMin && k <= state.distMax)) return false;
  }

  const d = p.date.slice(0, 10);
  if (state.dateFrom && d < state.dateFrom) return false;
  if (state.dateTo && d > state.dateTo) return false;
  return true;
}

function applyFilters() {
  const visible = allRaces.filter(r => matches(r.feature.properties));

  cluster.clearLayers();
  cluster.addLayers(visible.map(r => r.marker));

  if (userPos) {
    visible.forEach(r => { r.away = haversineKm(userPos, r.latlng); });
    visible.sort((a, b) => a.away - b.away);
  } else {
    visible.sort((a, b) => a.feature.properties.date.localeCompare(b.feature.properties.date));
  }

  document.getElementById("count").textContent =
    `${visible.length} løp vises` + (userPos ? " · sortert etter avstand" : " · sortert etter dato");

  const list = document.getElementById("raceList");
  list.innerHTML = "";
  visible.slice(0, 300).forEach(r => {
    const p = r.feature.properties;
    const el = document.createElement("div");
    el.className = "race-item";
    el.innerHTML = `
      <div class="name">
        <span class="badge" style="background:${colorFor(p.categories)}"></span>
        <span>${p.name}</span>
        ${r.away != null ? `<span class="away">${r.away < 10 ? r.away.toFixed(1) : Math.round(r.away)} km</span>` : ""}
      </div>
      <div class="meta">${p.country === "SE" ? "🇸🇪" : "🇳🇴"} ${fmtDate(p.date)} · ${p.town}${p.distanceSummary ? " · " + p.distanceSummary : ""}</div>
    `;
    el.addEventListener("click", () => {
      cluster.zoomToShowLayer(r.marker, () => r.marker.openPopup());
    });
    list.appendChild(el);
  });
}

/* ---------- UI wiring ---------- */

function wireSeg(segId, stateKey) {
  document.querySelectorAll(`#${segId} button`).forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll(`#${segId} button`).forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      state[stateKey] = b.dataset.val;
      applyFilters();
    });
  });
}
wireSeg("surfaceSeg", "surface");
wireSeg("countrySeg", "country");

document.querySelectorAll("#distChips button").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll("#distChips button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    state.distMin = parseFloat(b.dataset.min);
    state.distMax = parseFloat(b.dataset.max);
    document.getElementById("distMin").value = state.distMin > 0 ? state.distMin : "";
    document.getElementById("distMax").value = state.distMax < 999 ? state.distMax : "";
    applyFilters();
  });
});

["distMin", "distMax"].forEach(id => {
  document.getElementById(id).addEventListener("change", e => {
    const v = parseFloat(e.target.value);
    if (id === "distMin") state.distMin = isNaN(v) ? 0 : v;
    else state.distMax = isNaN(v) ? 999 : v;
    document.querySelectorAll("#distChips button").forEach(x => x.classList.remove("active"));
    applyFilters();
  });
});

["dateFrom", "dateTo"].forEach(id => {
  document.getElementById(id).addEventListener("change", e => {
    state[id] = e.target.value || null;
    applyFilters();
  });
});

function setUserPos(latlng, label) {
  userPos = latlng;
  if (userMarker) map.removeLayer(userMarker);
  userMarker = L.marker(latlng, {
    icon: L.divIcon({ className: "", html: '<div class="user-dot"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }),
    title: label || "Din posisjon",
    zIndexOffset: 1000,
  }).addTo(map);
  map.setView(latlng, 9);
  applyFilters();
}

document.getElementById("locateBtn").addEventListener("click", () => {
  if (!navigator.geolocation) { alert("Nettleseren støtter ikke posisjon."); return; }
  navigator.geolocation.getCurrentPosition(
    pos => setUserPos(L.latLng(pos.coords.latitude, pos.coords.longitude)),
    err => alert("Fikk ikke posisjon: " + err.message),
    { enableHighAccuracy: false, timeout: 10000 }
  );
});

async function searchPlace() {
  const q = document.getElementById("placeSearch").value.trim();
  if (!q) return;
  try {
    const res = await fetch(`https://ws.geonorge.no/stedsnavn/v1/navn?sok=${encodeURIComponent(q)}&treffPerSide=15&fuzzy=true`);
    const data = await res.json();
    const rank = { Kommune: 3, By: 2, Tettsted: 1 };
    const hits = (data.navn || []).filter(h => h.representasjonspunkt)
      .sort((a, b) => (rank[b.navneobjekttype] || 0) - (rank[a.navneobjekttype] || 0));
    if (hits.length) {
      const pt = hits[0].representasjonspunkt;
      setUserPos(L.latLng(pt.nord, pt.øst), hits[0].skrivemåte);
      return;
    }
    // not found in Norway – try Sweden via Nominatim
    const seRes = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&countrycodes=se&format=jsonv2&limit=1`);
    const seData = await seRes.json();
    if (!seData.length) { alert("Fant ikke stedet."); return; }
    setUserPos(L.latLng(+seData[0].lat, +seData[0].lon), seData[0].name);
  } catch (e) {
    alert("Stedssøk feilet: " + e.message);
  }
}
document.getElementById("searchBtn").addEventListener("click", searchPlace);
document.getElementById("placeSearch").addEventListener("keydown", e => {
  if (e.key === "Enter") searchPlace();
});

/* ---------- load data ---------- */

fetch("races.geojson")
  .then(r => r.json())
  .then(geo => {
    allRaces = geo.features.map(f => {
      const [lon, lat] = f.geometry.coordinates;
      const latlng = L.latLng(lat, lon);
      const marker = L.circleMarker(latlng, {
        radius: 7,
        color: "#fff",
        weight: 1.5,
        fillColor: colorFor(f.properties.categories),
        fillOpacity: 0.92,
      }).bindPopup(popupHtml(f.properties), { maxWidth: 280 });
      return { feature: f, marker, latlng, away: null };
    });

    // default date window: everything loaded (12 months)
    const dates = geo.features.map(f => f.properties.date.slice(0, 10)).sort();
    document.getElementById("dateFrom").value = dates[0];
    document.getElementById("dateTo").value = dates[dates.length - 1];
    state.dateFrom = dates[0];
    state.dateTo = dates[dates.length - 1];

    applyFilters();
  })
  .catch(e => {
    document.getElementById("count").textContent = "Kunne ikke laste races.geojson: " + e.message;
  });
