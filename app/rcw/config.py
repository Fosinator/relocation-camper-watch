"""config.yaml: filters, boosts, schedule and alert switches (brief: "all filters live in one config file").

Read again whenever it changes on disk, so a filter edit takes effect at the next poll without a restart.
Anything missing from the file falls back to DEFAULTS. A file that fails to load keeps the last good settings,
and the error shows on the dashboard instead of stopping the polls.
"""
import copy
import logging
import os
import threading

import yaml

from . import settings

log = logging.getLogger(__name__)

DEFAULTS = {
    "filters": {
        "vehicle_types": ["motorhome", "campervan"],
        "four_wd": "include",            # include | exclude | only  (David 2026-10-29: keep 4WD campers filterable)
        "min_berths": 2,                 # brief said 4; David: "2 berths are OK"
        "cities": ["Hobart", "Melbourne", "Sydney", "Brisbane", "Adelaide", "Perth"],
        "min_days": 4,
        "pickup_within_days": 60,
        "exclude_keywords": ["hitop", "poptop"],  # David 2026-10-29: hi-tops and pop-tops "probably aren't interested"
    },
    "boosts": {
        "keywords": ["rear lounge", "drop-down bed", "island bed", "Winnebago", "Avida", "Iveco", "Sprinter"],
        "inclusions": ["fuel", "ferry"],
    },
    "schedule": {
        "poll_hours": "6-22",
        "jitter_seconds": 120,
        "digest_time": "16:00",
        "gone_after_missed_polls": 2,
        "history_after_days": 2,
        "backoff_max_hours": 8,
        "blocked_backoff_hours": 24,
    },
    "alerts": {
        "dry_run": False,
        "instant_starred": False,
        "outage_hours": 24,
        "digest_max_listed": 10,
        "db_size_warn_mb": 500,
    },
    "sources": {
        "imoova": {"enabled": True, "simulate_outage": False, "details_per_poll": 40},
        "transfercar": {"enabled": True, "simulate_outage": False, "details_per_poll": 40},
    },
    # Trip stats (David 2026-10-29: fuel allowance, time vs distance, required daily hours, room to explore;
    # "Perth to Sydney ... comfort would be more like 10 days" = about 400 km / 5 h a day).
    "trip": {
        "comfortable_hours_per_day": 5,
        "busy_hours_per_day": 7,
        "max_hours_per_day": 9,
        "camper_time_factor": 1.15,      # campers are slower than the routing service's car times
        "fuel_l_per_100km": {"motorhome": 15, "campervan": 12, "other": 9},
        "fuel_price_per_litre": 2.10,
        "max_detour_km": 1500,
        "detours_per_listing": 10,
        "routing_requests_per_poll": 60,
        # the routing service will use outback tracks relocation vehicles aren't allowed on
        "unsealed_roads": ["Great Central Road", "Tjukaruru Road", "Outback Way", "Gunbarrel Highway",
                           "Tanami Road", "Gibb River Road", "Plenty Highway", "Donohue Highway",
                           "Sandover Highway", "Buchanan Highway", "Oodnadatta Track", "Birdsville Track",
                           "Strzelecki Track", "Mereenie Loop", "Ernest Giles Road", "Old Telegraph Track"],
    },
    # Places worth a detour; reachable on sealed roads. Add your own: {name, lat, lon}. "via" = points the detour
    # must pass on the way in and (reversed) out, where the routing service would otherwise take an unsealed
    # shortcut: Uluru and Kings Canyon go via Erldunda on the Stuart Highway, not the Ernest Giles Road.
    "sights": [
        {"name": "Kakadu (Jabiru)", "lat": -12.6705, "lon": 132.8361},
        {"name": "Litchfield (Wangi Falls)", "lat": -13.1650, "lon": 130.6800},
        {"name": "Katherine Gorge", "lat": -14.3170, "lon": 132.4200},
        {"name": "Karlu Karlu / Devils Marbles", "lat": -20.5650, "lon": 134.2610},
        {"name": "Kings Canyon", "lat": -24.2500, "lon": 131.5650,
         "via": [[-25.2047, 133.1990], [-25.2856, 131.9665]]},   # Erldunda, Lasseter Highway -> Luritja Road
        {"name": "Uluru", "lat": -25.3444, "lon": 131.0369, "via": [[-25.2047, 133.1990]]},   # Erldunda
        {"name": "Coober Pedy", "lat": -29.0135, "lon": 134.7544},
        {"name": "Flinders Ranges (Wilpena Pound)", "lat": -31.5250, "lon": 138.6020},
        {"name": "Barossa Valley", "lat": -34.5230, "lon": 138.9590},
        {"name": "Head of Bight", "lat": -31.4750, "lon": 131.1500},
        {"name": "Esperance (Lucky Bay)", "lat": -33.9930, "lon": 122.2310},
        {"name": "Wave Rock", "lat": -32.4430, "lon": 118.8970},
        {"name": "Margaret River", "lat": -33.9550, "lon": 115.0750},
        {"name": "Pinnacles (Nambung)", "lat": -30.6050, "lon": 115.1570},
        {"name": "Kalbarri", "lat": -27.7110, "lon": 114.1650},
        {"name": "Monkey Mia", "lat": -25.7930, "lon": 113.7180},
        {"name": "Ningaloo (Coral Bay)", "lat": -23.1420, "lon": 113.7700},
        {"name": "Karijini (Dales Gorge)", "lat": -22.4720, "lon": 118.5560},
        {"name": "Broome (Cable Beach)", "lat": -17.9330, "lon": 122.2100},
        {"name": "Great Ocean Road (Twelve Apostles)", "lat": -38.6650, "lon": 143.1050},
        {"name": "Grampians (Halls Gap)", "lat": -37.1370, "lon": 142.5190},
        {"name": "Wilsons Promontory", "lat": -39.0300, "lon": 146.3200},
        {"name": "Snowy Mountains (Thredbo)", "lat": -36.5040, "lon": 148.3060},
        {"name": "Blue Mountains (Katoomba)", "lat": -33.7120, "lon": 150.3110},
        {"name": "Hunter Valley", "lat": -32.7870, "lon": 151.2900},
        {"name": "Jervis Bay", "lat": -35.0390, "lon": 150.6720},
        {"name": "Byron Bay", "lat": -28.6474, "lon": 153.6020},
        {"name": "Carnarvon Gorge", "lat": -25.0550, "lon": 148.2350},
        {"name": "Airlie Beach (Whitsundays)", "lat": -20.2680, "lon": 148.7180},
        {"name": "Mossman Gorge (Daintree)", "lat": -16.4700, "lon": 145.3300},
        {"name": "Cradle Mountain", "lat": -41.6380, "lon": 145.9490},
        {"name": "Freycinet (Coles Bay)", "lat": -42.1220, "lon": 148.2880},
        {"name": "Port Arthur", "lat": -43.1460, "lon": 147.8530},
        {"name": "Bay of Fires", "lat": -41.2500, "lon": 148.3100},
    ],
}

FOUR_WD_MODES = ("include", "exclude", "only")

_lock = threading.Lock()
_state = {"mtime": None, "cfg": None, "error": None}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _check(cfg: dict) -> dict:
    f = cfg["filters"]
    if f["four_wd"] not in FOUR_WD_MODES:
        raise ValueError(f"filters.four_wd must be one of {', '.join(FOUR_WD_MODES)} (got {f['four_wd']!r})")
    f["vehicle_types"] = [str(t).lower() for t in f["vehicle_types"]]
    f["exclude_keywords"] = [str(k) for k in (f["exclude_keywords"] or [])]
    for key in ("min_berths", "min_days", "pickup_within_days"):
        f[key] = int(f[key])
    cfg["sights"] = [{"name": str(s["name"]), "lat": float(s["lat"]), "lon": float(s["lon"]),
                      "via": [(float(p[0]), float(p[1])) for p in (s.get("via") or [])]}
                     for s in (cfg.get("sights") or [])]
    hh, mm = str(cfg["schedule"]["digest_time"]).split(":")
    cfg["schedule"]["digest_time"] = f"{int(hh):02d}:{int(mm):02d}"
    return cfg


def load(path: str | None = None) -> dict:
    """The current settings (a fresh copy, safe to modify)."""
    path = path or settings.CONFIG_PATH
    with _lock:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if _state["cfg"] is None or mtime != _state["mtime"]:
            try:
                raw = {}
                if mtime is not None:
                    with open(path, encoding="utf-8") as fh:
                        raw = yaml.safe_load(fh) or {}
                _state["cfg"] = _check(_merge(DEFAULTS, raw))
                _state["error"] = None
                if _state["mtime"] is not None:
                    log.info("config.yaml changed - new settings in use")
            except Exception as e:  # noqa: BLE001
                _state["error"] = f"config.yaml could not be read, using the last good settings: {e}"
                log.error(_state["error"])
                if _state["cfg"] is None:
                    _state["cfg"] = _check(copy.deepcopy(DEFAULTS))
            _state["mtime"] = mtime
        return copy.deepcopy(_state["cfg"])


def error() -> str | None:
    return _state["error"]


def defaults() -> dict:
    return _check(copy.deepcopy(DEFAULTS))


def hours(spec: str) -> set[int]:
    """'6-22' or '6,8,10-12' -> the hours it covers (same format as the cron hour field)."""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out
