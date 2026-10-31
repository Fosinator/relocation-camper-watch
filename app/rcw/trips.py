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
from datetime import date

from psycopg.types.json import Jsonb

from . import db, geo

log = logging.getLogger(__name__)


def fuel_status(cfg: dict, today, live: dict | None = None) -> dict:
    """The diesel prices in use and how old they are: the hand-set ones (config.yaml, trip.fuel_price_*) and
    the live state prices from FuelCheck (fuelcheck.latest)."""
    t = cfg["trip"]
    try:
        as_of = date.fromisoformat(str(t.get("fuel_price_as_of")))
    except ValueError:
        as_of = None
    age = (today - as_of).days if as_of else None
    return {"prices": t["fuel_price_per_litre"], "as_of": as_of, "age_days": age, "source": t.get("fuel_price_source"),
            "stale": age is None or age > int(t.get("fuel_price_max_age_days", 45)), "live": live or {}}


def fuel_price(row: dict, cfg: dict) -> tuple[float, str]:
    """-> (price per litre in the listing's currency, where it came from). A trip starting in a state with a live
    FuelCheck price uses it; everything else uses the hand-set price for its region."""
    live = cfg["trip"].get("live_fuel") or {}
    state = geo.city_state(row.get("origin")) if (row.get("region") or "AU") == "AU" else None
    if state in live:
        return float(live[state]), f"FuelCheck {state}"
    prices = cfg["trip"]["fuel_price_per_litre"]
    region = row.get("region") or "AU"
    if isinstance(prices, dict):
        return float(prices.get(region, prices.get("AU", 2.10))), "set by hand"
    return float(prices), "set by hand"


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
    price, price_from = fuel_price(row, cfg)
    out["fuel_cost"] = round(litres * price)   # in the listing's own currency
    out["fuel_price"], out["fuel_price_from"] = round(price, 2), price_from
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


def refresh(cfg: dict, http=None, today=None) -> dict:
    """Recompute the trip stats of every open listing (the Routes page shows non-matches too), matches first,
    fetching missing routes within the request budget."""
    from . import fuelcheck, timefmt
    t = cfg["trip"]
    done = waiting = 0
    with db.conn() as c:
        t["live_fuel"] = {s: v["price"] for s, v in fuelcheck.latest(c, timefmt.now()).items()}
        router = geo.Router(c, int(t["routing_requests_per_poll"]), http)
        try:
            rows = c.execute("""SELECT * FROM relocations WHERE status = 'open'
                                ORDER BY matched DESC, boosted DESC, first_seen DESC""").fetchall()
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
