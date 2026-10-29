"""Trip stats for each match (David 2026-10-29): time vs distance, the driving hours a day it needs, how much
exploring the days and the km cap leave room for, and fuel against the fuel allowance. The km cap matters as
much as the days: a long relocation with a tight cap leaves little room to wander ("you would exceed those
KMs to visit Uluru" - an example of how the limits affect the value of a rental, not a request for detours).

Pace = camper driving hours / included days: comfortable <= 5 h, busy <= 7 h, rushed <= 9 h, beyond that too
tight (all in config.yaml under trip). Spare km = the comfortable hours left over x the route's average speed,
or what the km cap leaves beyond the route, whichever is smaller; explore_limit says which one it was.
"""
import logging
import math
import re

from psycopg.types.json import Jsonb

from . import db, geo

log = logging.getLogger(__name__)


def fuel_allowance(inclusions: str | None) -> float | None:
    m = re.search(r"Fuel \$([\d,]+(?:\.\d+)?)", inclusions or "")
    return float(m.group(1).replace(",", "")) if m else None


def pace(hours_per_day: float, t: dict) -> str:
    if hours_per_day <= t["comfortable_hours_per_day"]:
        return "comfortable"
    if hours_per_day <= t["busy_hours_per_day"]:
        return "busy"
    if hours_per_day <= t["max_hours_per_day"]:
        return "rushed"
    return "too tight"


def _f(v):
    return float(v) if v is not None else None


def _explore(days: int, cap: int | None, km: float, hours: float, speed: float, comfy: float) -> tuple[int, str]:
    """-> (spare km, what limits it: 'days' or 'km cap')."""
    by_days = max(0.0, days * comfy - hours) * speed
    if cap and cap - km < by_days:
        return round(max(0.0, cap - km)), "km cap"
    return round(by_days), "days"


def stats(row: dict, direct: dict, cfg: dict) -> dict:
    """Pure: one listing + its road route -> the numbers shown on the card, the trip panel and the digest."""
    t = cfg["trip"]
    km = direct["drive_km"]
    hours = direct["drive_hours"] * float(t["camper_time_factor"])
    speed = km / hours if hours else 70.0
    days = row.get("days_included")
    extra = row.get("extra_days") or 0
    price = _f(row.get("extra_day_price"))
    cap = row.get("km_allowed")
    per_extra = row.get("km_per_extra_day") or 0
    rate = _f(row.get("excess_km_rate"))
    comfy = float(t["comfortable_hours_per_day"])

    out = {"route_km": round(km), "drive_hours": round(hours, 1), "ferry": direct.get("ferry_km", 0) > 1,
           "unsealed": geo.unsealed_used(direct, t["unsealed_roads"]), "cap_km": cap,
           "comfortable_days": math.ceil(hours / comfy) if hours else 0}
    if cap:
        out["cap_spare_km"] = round(cap - km)
        if km > cap:
            out["over_cap_km"] = round(km - cap)
            out["over_cap_cost"] = round((km - cap) * rate) if rate else None
    if days:
        out["hours_per_day"] = round(hours / days, 1)
        out["km_per_day"] = round(km / days)
        out["pace"] = pace(hours / days, t)
        out["explore_km"], out["explore_limit"] = _explore(days, cap, km, hours, speed, comfy)
        short = max(0, out["comfortable_days"] - days)
        out["days_short"] = short
        if short and extra:
            buy = min(short, extra)
            out["buy_days"] = buy
            out["buy_cost"] = round(buy * price) if price is not None else None
        if extra:
            cap_all = cap + per_extra * extra if cap else None
            out["explore_km_all_extra"], out["explore_limit_all_extra"] = _explore(
                days + extra, cap_all, km, hours, speed, comfy)
            out["all_extra_cost"] = round(extra * price) if price is not None else None

    litres = km * float(t["fuel_l_per_100km"].get(row.get("vehicle_type") or "other", 12)) / 100
    out["fuel_cost"] = round(litres * float(t["fuel_price_per_litre"]))
    allowance = fuel_allowance(row.get("inclusions"))
    out["fuel_allowance"] = allowance
    out["fuel_cover_pct"] = min(100, round(100 * allowance / out["fuel_cost"])) if allowance and out["fuel_cost"] else 0
    return out


def badge(trip: dict | None) -> str | None:
    """'Comfortable · +222 km to explore' (card and digest)."""
    if not trip or "pace" not in trip:
        return None
    text = trip["pace"].capitalize()
    if trip.get("explore_km"):
        text += f" · +{trip['explore_km']:,} km to explore"
    return text


def refresh(cfg: dict, http=None) -> dict:
    """Recompute the trip stats of every open match, fetching missing routes within the request budget."""
    t = cfg["trip"]
    done = waiting = 0
    with db.conn() as c:
        router = geo.Router(c, int(t["routing_requests_per_poll"]), http)
        try:
            rows = c.execute("""SELECT * FROM relocations WHERE status = 'open' AND matched
                                ORDER BY boosted DESC, first_seen DESC""").fetchall()
            for r in rows:
                a, b = geo.city_point(r["origin"]), geo.city_point(r["destination"])
                if not a or not b:
                    missing = r["origin"] if not a else r["destination"]
                    trip = {"error": f"no map position for {missing or '****'}"}
                else:
                    direct = router.get([a, b])
                    if direct is None:
                        waiting += 1
                        continue
                    trip = stats(r, direct, cfg)
                if trip != r["trip"]:
                    c.execute("UPDATE relocations SET trip = %s WHERE id = %s", (Jsonb(trip), r["id"]))
                done += 1
        finally:
            router.close()
    if router.fetched or waiting:
        log.info("trip stats: %d listings updated, %d waiting for routes, %d routes fetched%s", done, waiting,
                 router.fetched, f" (stopped: {router.stopped})" if router.stopped else "")
    return {"done": done, "waiting": waiting, "fetched": router.fetched}
