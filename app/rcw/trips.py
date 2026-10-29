"""Trip stats for each match (David 2026-10-29): time vs distance, the driving hours a day it needs, how much
exploring the days and the km cap leave room for, fuel against the fuel allowance, and which sights fit as
detours ("you would exceed those KMs to visit Uluru").

Pace = camper driving hours / included days: comfortable <= 5 h, busy <= 7 h, rushed <= 9 h, beyond that too
tight (all in config.yaml under trip). Spare km = the comfortable hours left over x the route's average speed,
capped by the site's km allowance where it states one.
"""
import logging
import math
import re

from psycopg.types.json import Jsonb

from . import db, geo

log = logging.getLogger(__name__)

PACES = ("comfortable", "busy", "rushed", "too tight")


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


def stats(row: dict, direct: dict, detours: list[tuple[str, dict | None]], cfg: dict) -> dict:
    """Pure: one listing + its routes -> the numbers shown on the card, the trip panel and the digest."""
    t = cfg["trip"]
    factor = float(t["camper_time_factor"])
    km = direct["drive_km"]
    hours = direct["drive_hours"] * factor
    speed = km / hours if hours else 70.0
    days = row.get("days_included")
    extra = row.get("extra_days") or 0
    price = _f(row.get("extra_day_price"))
    cap = row.get("km_allowed")
    per_extra = row.get("km_per_extra_day") or 0
    rate = _f(row.get("excess_km_rate"))
    comfy = float(t["comfortable_hours_per_day"])
    unsealed = geo.unsealed_used(direct, t["unsealed_roads"])

    out = {"route_km": round(km), "drive_hours": round(hours, 1), "ferry": direct.get("ferry_km", 0) > 1,
           "unsealed": unsealed, "cap_km": cap, "comfortable_days": math.ceil(hours / comfy) if hours else 0}
    if cap:
        out["cap_spare_km"] = round(cap - km)
    if days:
        out["hours_per_day"] = round(hours / days, 1)
        out["km_per_day"] = round(km / days)
        out["pace"] = pace(hours / days, t)
        spare = max(0.0, days * comfy - hours) * speed
        out["explore_km"] = round(min(spare, max(0.0, cap - km)) if cap else spare)
        short = max(0, out["comfortable_days"] - days)
        out["days_short"] = short
        if short and extra:
            buy = min(short, extra)
            out["buy_days"] = buy
            out["buy_cost"] = round(buy * price) if price is not None else None
        if extra:
            spare_all = max(0.0, (days + extra) * comfy - hours) * speed
            cap_all = cap + per_extra * extra if cap else None
            out["explore_km_all_extra"] = round(min(spare_all, max(0.0, cap_all - km)) if cap else spare_all)
            out["all_extra_cost"] = round(extra * price) if price is not None else None

    litres = km * float(t["fuel_l_per_100km"].get(row.get("vehicle_type") or "other", 12)) / 100
    out["fuel_cost"] = round(litres * float(t["fuel_price_per_litre"]))
    allowance = fuel_allowance(row.get("inclusions"))
    out["fuel_allowance"] = allowance
    out["fuel_cover_pct"] = min(100, round(100 * allowance / out["fuel_cost"])) if allowance and out["fuel_cost"] else 0

    out["detours"] = []
    for name, via in detours:
        if via is None:
            continue
        extra_km = via["drive_km"] - km
        # checked first: an outback shortcut can even be shorter than the sealed direct route
        new_unsealed = [u for u in geo.unsealed_used(via, t["unsealed_roads"]) if u not in unsealed]
        if new_unsealed:
            out["detours"].append({"name": name, "extra_km": max(0, round(extra_km)), "extra_hours": 0,
                                   "status": "unsealed",
                                   "note": f"the routing found only unsealed roads ({', '.join(new_unsealed)})"})
            continue
        if extra_km < 25:
            out["detours"].append({"name": name, "extra_km": 0, "extra_hours": 0, "status": "on_route",
                                   "note": "on the way"})
            continue
        via_hours = via["drive_hours"] * factor
        d = {"name": name, "extra_km": round(extra_km), "extra_hours": round(via_hours - hours, 1)}
        over = round(via["drive_km"] - cap) if cap and via["drive_km"] > cap else 0
        if over:
            cost = f", about ${over * rate:,.0f} at ${rate:.2f}/km" if rate else ""
            d["status"], d["note"] = "over_cap", f"{over:,} km over the {cap:,} km cap{cost}"
        elif not days:
            d["status"], d["note"] = "fits", "fits the km cap" if cap else "possible"
        elif via_hours <= days * comfy:
            d["status"], d["note"] = "fits", "fits at a comfortable pace"
        elif via_hours <= days * float(t["busy_hours_per_day"]):
            d["status"], d["note"] = "busy", f"fits at a busier pace ({via_hours / days:.1f} h a day)"
        elif extra and via_hours <= (days + extra) * comfy:
            n = math.ceil(via_hours / comfy) - days
            cost = f" (${n * price:,.0f})" if price is not None else ""
            d["status"], d["note"] = "extra_days", f"needs {n} extra day{'s' if n != 1 else ''}{cost}"
        elif extra and via_hours <= (days + extra) * float(t["busy_hours_per_day"]):
            cost = f" (${extra * price:,.0f})" if price is not None else ""
            d["status"], d["note"] = "extra_days", (f"needs all {extra} extra day{'s' if extra != 1 else ''}{cost} "
                                                    f"at a busier pace ({via_hours / (days + extra):.1f} h a day)")
        else:
            n = math.ceil(via_hours / comfy) - days
            d["status"], d["note"] = "no", (f"needs {n} more days, only {extra} extra offered" if extra
                                            else f"needs {n} more days, no extra days offered")
        if over == 0 and cap:
            d["note"] += f" · {cap - round(via['drive_km']):,} km of cap left"
        out["detours"].append(d)
    out["detours"].sort(key=lambda d: d["extra_km"])
    return out


def badge(trip: dict | None) -> str | None:
    """'Comfortable · +480 km to explore' (card and digest)."""
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
            # every listing's own route first, so all cards get a pace badge before any detours are checked
            for r in rows:
                a, b = geo.city_point(r["origin"]), geo.city_point(r["destination"])
                if a and b:
                    router.get([a, b])
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
                    sights = geo.candidate_sights(a, b, cfg["sights"], float(t["max_detour_km"]),
                                                  int(t["detours_per_listing"]))
                    vias = [(s["name"], router.get([a, (s["lat"], s["lon"]), b])) for s in sights]
                    trip = stats(r, direct, vias, cfg)
                    if any(v is None for _, v in vias):
                        trip["detours_pending"] = True
                if trip != r["trip"]:
                    c.execute("UPDATE relocations SET trip = %s WHERE id = %s", (Jsonb(trip), r["id"]))
                done += 1
        finally:
            router.close()
    if router.fetched or waiting:
        log.info("trip stats: %d listings updated, %d waiting for routes, %d routes fetched%s", done, waiting,
                 router.fetched, f" (stopped: {router.stopped})" if router.stopped else "")
    return {"done": done, "waiting": waiting, "fetched": router.fetched}
