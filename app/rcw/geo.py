"""Map positions and road routes for the trip stats (2026-10-29).

Road distances and driving times come from the public OSRM server (the one Experience Radar already uses):
one request per route, kept for good in road_routes, at most trip.routing_requests_per_poll a poll.

Two things the routing service gets wrong for relocations:
- It happily uses outback tracks (Darwin -> Uluru -> Perth goes along the Great Central Road), which relocation
  vehicles aren't allowed on. Every road name on a route is saved, and routes using a road in
  trip.unsealed_roads are flagged rather than trusted.
- Ferry legs (Spirit of Tasmania) count as distance. They're kept apart from the driving km and hours.
"""
import logging
import math

from . import normalise
from .http import Blocked, Disallowed, Polite

log = logging.getLogger(__name__)

OSRM_ROUTE = "https://router.project-osrm.org/route/v1/driving/"

# (lat, lon) of the city centre for every city name normalise.city() produces
CITY_COORDS = {
    "Hobart": (-42.8821, 147.3272), "Launceston": (-41.4332, 147.1441), "Devonport": (-41.1800, 146.3500),
    "Melbourne": (-37.8136, 144.9631), "Sydney": (-33.8688, 151.2093), "Brisbane": (-27.4698, 153.0251),
    "Adelaide": (-34.9285, 138.6007), "Perth": (-31.9523, 115.8613), "Darwin": (-12.4634, 130.8456),
    "Cairns": (-16.9186, 145.7781), "Gold Coast": (-28.0167, 153.4000), "Sunshine Coast": (-26.6500, 153.0667),
    "Alice Springs": (-23.6980, 133.8807), "Broome": (-17.9614, 122.2359), "Townsville": (-19.2590, 146.8169),
    "Canberra": (-35.2809, 149.1300), "Newcastle": (-32.9283, 151.7817), "Mackay": (-21.1411, 149.1861),
    "Ballina": (-28.8667, 153.5667), "Byron Bay": (-28.6474, 153.6020), "Geelong": (-38.1499, 144.3617),
    "Wollongong": (-34.4278, 150.8931), "Port Lincoln": (-34.7263, 135.8742), "Exmouth": (-21.9300, 114.1300),
    "Esperance": (-33.8613, 121.8914), "Albany": (-35.0228, 117.8814), "Kununurra": (-15.7736, 128.7386),
    "Rockhampton": (-23.3781, 150.5136), "Bundaberg": (-24.8661, 152.3489), "Hervey Bay": (-25.2882, 152.8531),
    "Coffs Harbour": (-30.2963, 153.1135), "Port Macquarie": (-31.4308, 152.9089), "Mildura": (-34.2080, 142.1246),
    "Whyalla": (-33.0333, 137.5833), "Bairnsdale": (-37.8227, 147.6100), "Kalgoorlie": (-30.7489, 121.4658),
    "Whitsunday Coast": (-20.4017, 148.5815),
}
assert set(normalise.KNOWN_CITIES) <= set(CITY_COORDS), set(normalise.KNOWN_CITIES) - set(CITY_COORDS)


def city_point(name: str | None) -> tuple[float, float] | None:
    if not name:
        return None
    for city, point in CITY_COORDS.items():
        if city.lower() == name.lower():
            return point
    return None


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line km (only used to pick which sights are worth asking the routing service about)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def candidate_sights(a, b, sights: list[dict], max_km: float, limit: int) -> list[dict]:
    """Sights whose rough detour (straight lines x 1.25 for roads) is under max_km, nearest first.
    Sights within 30 km of either end are part of the trip, not a detour."""
    out = []
    for s in sights:
        p = (s["lat"], s["lon"])
        if km_between(a, p) < 30 or km_between(p, b) < 30:
            continue
        est = (km_between(a, p) + km_between(p, b) - km_between(a, b)) * 1.25
        if est <= max_km:
            out.append((est, s))
    return [s for _, s in sorted(out, key=lambda x: x[0])[:limit]]


def route_key(points) -> str:
    return ";".join(f"{lon:.4f},{lat:.4f}" for lat, lon in points)


def parse_route(data: dict) -> dict:
    """OSRM route response (steps=true) -> driving and ferry km / hours, and every road used."""
    route = data["routes"][0]
    drive_m = drive_s = ferry_m = ferry_s = 0.0
    roads = set()
    for leg in route["legs"]:
        for step in leg["steps"]:
            if step.get("mode") == "ferry":
                ferry_m += step["distance"]
                ferry_s += step["duration"]
                continue
            drive_m += step["distance"]
            drive_s += step["duration"]
            for label in (step.get("name"), step.get("ref")):
                if label:
                    roads.add(label)
    return {"drive_km": drive_m / 1000, "drive_hours": drive_s / 3600, "ferry_km": ferry_m / 1000,
            "ferry_hours": ferry_s / 3600, "roads": sorted(roads)}


def unsealed_used(route: dict, unsealed: list[str]) -> list[str]:
    roads = " | ".join(route.get("roads") or []).lower()
    return [u for u in unsealed if u.lower() in roads]


class Router:
    """Routes from the cache, or from the routing service while the request budget lasts."""

    def __init__(self, c, budget: int, http=None):
        self.c = c
        self.budget = budget
        self.http = http
        self.fetched = 0
        self.stopped = None
        self.cache = {r["key"]: r for r in c.execute("SELECT * FROM road_routes")}

    def get(self, points) -> dict | None:
        key = route_key(points)
        row = self.cache.get(key)
        if row is not None:
            return None if row["error"] else {k: (float(row[k]) if k != "roads" else row[k])
                                              for k in ("drive_km", "drive_hours", "ferry_km", "ferry_hours", "roads")}
        if self.budget <= 0 or self.stopped:
            return None
        self.budget -= 1
        if self.http is None:
            self.http = Polite(delay=1.1)
        try:
            data = self.http.get(OSRM_ROUTE + key + "?overview=false&steps=true").json()
        except (Blocked, Disallowed) as e:
            self.stopped = str(e)
            log.warning("routing stopped: %s", e)
            return None
        except Exception as e:  # noqa: BLE001 - try again next poll
            log.info("routing %s failed: %s", key, e)
            return None
        self.fetched += 1
        if data.get("code") != "Ok" or not data.get("routes"):
            row = {"key": key, "error": data.get("code") or "no route"}
            self.c.execute("INSERT INTO road_routes (key, error) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                           (key, row["error"]))
            self.cache[key] = row
            return None
        r = parse_route(data)
        self.c.execute("""INSERT INTO road_routes (key, drive_km, drive_hours, ferry_km, ferry_hours, roads)
                          VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING""",
                       (key, r["drive_km"], r["drive_hours"], r["ferry_km"], r["ferry_hours"], r["roads"]))
        self.cache[key] = {**r, "key": key, "error": None}
        return r

    def close(self):
        if self.http is not None and hasattr(self.http, "close"):
            self.http.close()
