"""Trip stats, worked through David's Darwin -> Adelaide example (2026-10-29) with the real road numbers:
3,022 km and 32.4 h by car on the Stuart Highway."""
import json

from conftest import fixture_text

from rcw import geo, trips
from rcw.sources import imoova, transfercar

DIRECT = {"drive_km": 3022, "drive_hours": 32.4, "ferry_km": 0, "roads": ["Stuart Highway"]}


def listing(**kw):
    base = {"vehicle_type": "campervan", "days_included": 8, "extra_days": 3, "extra_day_price": 100,
            "km_allowed": 4500, "km_per_extra_day": None, "excess_km_rate": 0.5, "inclusions": None}
    return {**base, **kw}


def test_eight_days_4500_km_cap_days_are_the_limit(cfg):
    t = trips.stats(listing(), DIRECT, cfg)
    assert t["route_km"] == 3022 and t["drive_hours"] == 37.3       # car hours x 1.15
    assert t["hours_per_day"] == 4.7 and t["pace"] == "comfortable"
    assert t["comfortable_days"] == 8 and t["days_short"] == 0
    assert t["explore_km"] == 222 and t["explore_limit"] == "days"  # (8 x 5 h - 37.3 h) x 81 km/h
    assert t["explore_km_all_extra"] == 1439 and t["explore_limit_all_extra"] == "days"
    assert t["all_extra_cost"] == 300 and t["cap_spare_km"] == 1478
    assert t["fuel_cost"] == 816 and t["fuel_allowance"] is None     # 3,022 km x 10 L/100 x $2.70
    assert trips.badge(t) == "Comfortable · +222 km to explore"


def test_seven_days_3400_km_cap_the_cap_is_the_limit(cfg):
    t = trips.stats(listing(days_included=7, extra_days=2, extra_day_price=75, km_allowed=3400,
                            inclusions="Fuel $300 upon receipts"), DIRECT, cfg)
    assert t["pace"] == "busy" and t["explore_km"] == 0              # 5.3 h a day: no comfortable hours left
    assert t["explore_km_all_extra"] == 378 and t["explore_limit_all_extra"] == "km cap"  # 9 days, but 3,400 km
    assert t["fuel_allowance"] == 300 and t["fuel_cover_pct"] == 37
    assert trips.badge(t) == "Busy"


def test_route_longer_than_the_cap(cfg):
    t = trips.stats(listing(km_allowed=3000), DIRECT, cfg)
    assert t["over_cap_km"] == 22 and t["over_cap_cost"] == 11 and t["explore_km"] == 0
    assert t["explore_limit"] == "km cap"


def test_unknown_days_and_cap(cfg):
    t = trips.stats(listing(days_included=None, km_allowed=None, extra_days=0), DIRECT, cfg)
    assert "pace" not in t and trips.badge(t) is None and t["cap_km"] is None


def test_unsealed_route_is_noted(cfg):
    t = trips.stats(listing(), {**DIRECT, "roads": ["Great Central Road", "Stuart Highway"]}, cfg)
    assert t["unsealed"] == ["Great Central Road"]


def test_route_parsing_unsealed_and_ferry():
    outback = geo.parse_route(json.loads(fixture_text("osrm_darwin_uluru_perth.json")))
    assert geo.unsealed_used(outback, ["Great Central Road", "Tanami Road"]) == ["Great Central Road"]
    assert outback["ferry_km"] == 0 and 3900 < outback["drive_km"] < 4100
    bass = geo.parse_route(json.loads(fixture_text("osrm_melbourne_hobart.json")))
    assert bass["ferry_km"] > 300 and bass["drive_km"] < 450 and bass["ferry_hours"] > 8


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


def test_imoova_extra_days_add_km_to_the_cap(cfg):
    t = trips.stats(listing(km_allowed=3150, km_per_extra_day=250, days_included=7), DIRECT, cfg)
    assert t["explore_limit"] == "km cap" or t["explore_km"] == 0
    assert t["explore_km_all_extra"] == 3150 + 3 * 250 - 3022       # 10 days: the cap (878 km) is the limit
    assert t["explore_limit_all_extra"] == "km cap"
