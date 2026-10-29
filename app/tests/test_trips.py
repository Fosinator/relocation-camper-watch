"""Trip stats, worked through David's Darwin -> Adelaide example (2026-10-29) with the real road numbers:
direct 3,022 km / 32.4 h by car, via Uluru 3,549 km / 39.0 h, via Kakadu + Kings Canyon + Uluru +
Flinders Ranges 4,292 km / 49.4 h."""
import json

from conftest import fixture_text

from rcw import geo, trips
from rcw.sources import imoova, transfercar

DIRECT = {"drive_km": 3022, "drive_hours": 32.4, "ferry_km": 0, "roads": ["Stuart Highway"]}
ULURU = {"drive_km": 3549, "drive_hours": 39.0, "ferry_km": 0, "roads": ["Stuart Highway", "Lasseter Highway"]}
LOOP = {"drive_km": 4292, "drive_hours": 49.4, "ferry_km": 0, "roads": ["Stuart Highway"]}


def listing(**kw):
    base = {"vehicle_type": "campervan", "days_included": 8, "extra_days": 3, "extra_day_price": 100,
            "km_allowed": 4500, "km_per_extra_day": None, "excess_km_rate": 0.5, "inclusions": None}
    return {**base, **kw}


def test_listing_a_eight_days_4500_km_cap(cfg):
    t = trips.stats(listing(), DIRECT, [("Uluru", ULURU), ("The loop", LOOP)], cfg)
    assert t["route_km"] == 3022 and t["drive_hours"] == 37.3       # car hours x 1.15
    assert t["hours_per_day"] == 4.7 and t["pace"] == "comfortable"
    assert t["comfortable_days"] == 8 and t["days_short"] == 0
    assert 200 < t["explore_km"] < 240                               # (8 x 5 h - 37.3 h) x 81 km/h
    assert 1400 < t["explore_km_all_extra"] < 1478                   # 11 days, still under the cap
    assert t["cap_spare_km"] == 1478
    assert t["fuel_cost"] == 762 and t["fuel_allowance"] is None     # 3,022 km x 12 L/100 x $2.10
    uluru, loop = t["detours"]
    assert (uluru["name"], uluru["extra_km"], uluru["status"]) == ("Uluru", 527, "busy")
    assert loop["status"] == "extra_days"
    assert loop["note"].startswith("needs all 3 extra days ($300) at a busier pace (5.2 h a day)")
    assert trips.badge(t) == "Comfortable · +222 km to explore"


def test_listing_b_seven_days_3400_km_cap(cfg):
    t = trips.stats(listing(days_included=7, extra_days=2, extra_day_price=75, km_allowed=3400,
                            inclusions="Fuel $300 upon receipts"), DIRECT, [("Uluru", ULURU)], cfg)
    assert t["pace"] == "busy" and t["explore_km"] == 0
    assert t["fuel_allowance"] == 300 and t["fuel_cover_pct"] == 39
    [uluru] = t["detours"]
    assert uluru["status"] == "over_cap"
    assert uluru["note"] == "149 km over the 3,400 km cap, about $74 at $0.50/km"


def test_unknown_days_and_cap(cfg):
    t = trips.stats(listing(days_included=None, km_allowed=None, extra_days=0), DIRECT, [("Uluru", ULURU)], cfg)
    assert "pace" not in t and trips.badge(t) is None
    assert t["detours"][0]["note"] == "possible"


def test_unsealed_detour_is_flagged(cfg):
    via = {"drive_km": 4000, "drive_hours": 57.7, "ferry_km": 0, "roads": ["Great Central Road", "Stuart Highway"]}
    t = trips.stats(listing(), {**DIRECT, "drive_km": 4015}, [("Uluru", via)], cfg)
    assert t["detours"][0]["status"] == "unsealed"


def test_route_parsing_unsealed_and_ferry():
    outback = geo.parse_route(json.loads(fixture_text("osrm_darwin_uluru_perth.json")))
    assert geo.unsealed_used(outback, ["Great Central Road", "Tanami Road"]) == ["Great Central Road"]
    assert outback["ferry_km"] == 0 and 3900 < outback["drive_km"] < 4100
    bass = geo.parse_route(json.loads(fixture_text("osrm_melbourne_hobart.json")))
    assert bass["ferry_km"] > 300 and bass["drive_km"] < 450 and bass["ferry_hours"] > 8


def test_candidate_sights(cfg):
    darwin, adelaide = geo.city_point("Darwin"), geo.city_point("Adelaide")
    names = [s["name"] for s in geo.candidate_sights(darwin, adelaide, cfg["sights"], 1500,
                                                      cfg["trip"]["detours_per_listing"])]
    assert "Uluru" in names and "Karlu Karlu / Devils Marbles" in names
    assert "Margaret River" not in names


def test_km_caps_from_both_sites():
    rel = ('{__typename:"Relocation",id:"1",distance_allowed:3150,excess_distance_rate:55,measurement:"METRIC",'
           'extra_distance_allowed_per_extra_hire_unit:250,vehicle:$R[2]={__typename:"Vehicle",name:"x"}}')
    detail = imoova.detail_from_page(rel)
    assert detail["relocation"]["distance_allowed"] == 3150
    item = {"id": "1", "reference": "RLC1", "vehicle_type": "CAMPER_VAN", "vehicle_name": "x"}
    rec = imoova.normalise_item(item, detail)
    assert (rec.km_allowed, rec.km_per_extra_day, rec.excess_km_rate) == (3150, 250, 0.55)
    tc = json.loads(fixture_text("transfercar_detail.json"))
    assert transfercar.excess_km_rate(tc) == 0.5   # "50c per extra KM over the allowed amount"
    rec = transfercar.normalise_item({"id": 1, "vehicle_type": "Campervan (3 berth)"}, tc)
    assert rec.km_allowed == 3000 and rec.excess_km_rate == 0.5
