"""Hard filters and boosts (brief 2026-10-29, with David's changes the same day: 2+ berths, a missing value
passes, 4WD campers a setting, hi-tops and pop-tops excluded by vehicle name).

A listing matches only if it passes every hard filter. A value the site doesn't show (berths, days, dates...)
counts as passed and shows as **** (David: "assume that filter would have passed"). Boosts never filter
anything out: a hit only adds a star, and starred matches are listed first.
"""
import re
from datetime import date, timedelta


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("-", " ").split())


def _squash(s: str) -> str:
    """Spaces and hyphens removed, so 'hitop' finds Hi-Top, Hi Top, HiTop and Hitop."""
    return re.sub(r"[\s\-]+", "", s.lower())


def evaluate(row: dict, cfg: dict, today: date) -> dict:
    f = cfg["filters"]
    fails = []
    vtype = row.get("vehicle_type")
    if vtype and vtype not in f["vehicle_types"]:
        fails.append(f"vehicle type {vtype}")
    if f["four_wd"] == "exclude" and row.get("four_wd"):
        fails.append("4WD")
    elif f["four_wd"] == "only" and not row.get("four_wd"):
        fails.append("not 4WD")
    name = _squash(row.get("vehicle_name") or "")
    excluded = [k for k in f["exclude_keywords"] if _squash(k) and _squash(k) in name]
    if excluded:
        fails.append(f"excluded: {', '.join(excluded)}")
    if row.get("berths") is not None and row["berths"] < f["min_berths"]:
        fails.append(f"{row['berths']} berths")
    cities = {c.lower() for c in f["cities"]}
    places = {p.lower() for p in (row.get("origin"), row.get("destination")) if p}
    if cities and places and not places & cities:
        fails.append(f"route {row.get('origin')} → {row.get('destination')}")
    if row.get("days_included") is not None and row["days_included"] < f["min_days"]:
        fails.append(f"{row['days_included']} days")
    pf, pt = row.get("pickup_from"), row.get("pickup_to")
    if pf and pf > today + timedelta(days=f["pickup_within_days"]):
        fails.append(f"pickup from {pf:%d %b}")
    if pt and pt < today:
        fails.append("pickup window over")

    text = _norm(" ".join(x for x in (row.get("vehicle_name"), row.get("description")) if x))
    boosts = [k for k in cfg["boosts"]["keywords"] if _norm(k) in text]
    inclusions = _norm(row.get("inclusions") or "")
    boosts += [k for k in cfg["boosts"]["inclusions"] if _norm(k) in inclusions]
    return {
        "matched": not fails,
        "boosted": bool(boosts),
        "fail_reasons": "; ".join(fails) or None,
        "boost_reasons": ", ".join(boosts) or None,
    }
