"""Daisy-chaining relocations (David 2026-10-29: "a drop down for start and finish - we may be able to daisy
chain some together").

A next leg fits after a relocation when it starts where that one ends and its pickup window overlaps the days
you could be there: from the first pickup day plus the fewest days the drive can take (at
trip.max_hours_per_day), to the last pickup day plus every included and extra day.
"""
import math
from datetime import date, timedelta


def arrival_window(r: dict, cfg: dict) -> tuple[date, date] | None:
    pf = r.get("pickup_from")
    if not pf:
        return None
    pt = r.get("pickup_to") or pf
    hours = (r.get("trip") or {}).get("drive_hours")
    fastest = max(1, math.ceil(hours / float(cfg["trip"]["max_hours_per_day"]))) if hours else 1
    longest = max(fastest, (r.get("days_included") or fastest) + (r.get("extra_days") or 0))
    return pf + timedelta(days=fastest), pt + timedelta(days=longest)


def fits_after(first: dict, nxt: dict, cfg: dict) -> bool:
    if nxt["id"] == first["id"] or not first.get("destination") or not nxt.get("origin"):
        return False
    if nxt["origin"].lower() != first["destination"].lower():
        return False
    window = arrival_window(first, cfg)
    if window is None or not nxt.get("pickup_from"):
        return True  # dates unknown: can't rule it out
    lo, hi = window
    return (nxt.get("pickup_to") or nxt["pickup_from"]) >= lo and nxt["pickup_from"] <= hi


def onward(first: dict, rows: list[dict], cfg: dict) -> list[dict]:
    return [r for r in rows if fits_after(first, r, cfg)]


REGION_NAMES = {"AU": "Australia", "NZ": "New Zealand"}


def places_by_region(rows: list[dict], key: str) -> list[tuple[str, list[tuple[str, int]]]]:
    """[(region name, [(city, count)])] for dropdowns grouped by country."""
    out = []
    for region, name in REGION_NAMES.items():
        cities = places([r for r in rows if (r.get("region") or "AU") == region], key)
        if cities:
            out.append((name, cities))
    return out


def places(rows: list[dict], key: str) -> list[tuple[str, int]]:
    """[(city, count)] for a dropdown, busiest first."""
    counts: dict[str, int] = {}
    for r in rows:
        if r.get(key):
            counts[r[key]] = counts.get(r[key], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
