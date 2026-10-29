"""New Zealand (David 2026-10-30: "Add New Zealand"): Imoova's NZ listings from the same page, Transfercar's
NZ site, NZ city names, NZD prices, and the region switch."""
from datetime import date

from conftest import fixture_text

from rcw import filters, normalise, trips
from rcw.sources import SOURCES, imoova, transfercar

NZ_ITEM = {"id": "126381", "reference": "RLC126381", "currency": "NZD", "booking_fee_amount": 9900, "count": 1,
           "type": "RELOCATION", "available_from_date": "2026-10-23", "latest_departure_date": "2026-10-29",
           "hire_unit_type": "NIGHT", "hire_unit_rate": 100, "extra_hire_unit_rate": 22000, "relocation_units": 7,
           "extra_units": 3, "departure_city_name": "Auckland", "departure_city_slug": "auckland",
           "departure_city_region": "NZ", "delivery_city_name": "Queenstown", "delivery_city_slug": "queenstown",
           "vehicle_name": "Oc Comfort Family 6 Auto Select", "vehicle_type": "MOTOR_HOME", "vehicle_sleeps": 6,
           "has_ferry": True, "ferry_description": "Interislander for the vehicle and driver"}


def test_imoova_nz_listing():
    rec = imoova.normalise_item(NZ_ITEM, None)
    assert (rec.region, rec.currency) == ("NZ", "NZD")
    assert rec.url == "https://www.imoova.com/relocations/new-zealand/auckland-to-queenstown/RLC126381"
    assert (rec.origin, rec.destination, rec.berths, rec.extra_day_price) == ("Auckland", "Queenstown", 6, 220)
    assert "Ferry included" in rec.inclusions


def test_imoova_region_choice():
    page = fixture_text("imoova_table.html")
    au = imoova.live_items(page, ("AU",))
    both = imoova.live_items(page, ("AU", "NZ"))
    assert all(i["departure_city_region"] == "AU" for i in au) and len(both) > len(au)
    assert {i["departure_city_region"] for i in both} == {"AU", "NZ"}


def test_transfercar_nz_source():
    nz = SOURCES["transfercar_nz"]
    assert (nz.label, nz.site, nz.regions) == ("Transfercar NZ", "https://www.transfercar.co.nz", frozenset({"NZ"}))
    item = {"id": 679791, "vehicle_type": "Motorhome (4 berth)", "pickup_location": "Harewood (Christchurch)",
            "dropoff_location": "Pokeno", "pickup_date": "2026-10-30T13:46:31Z", "dropoff_date": "2026-10-31T00:00:00Z",
            "free_days": 4, "paid_days": 3, "price_per_paid_day": 150,
            "inclusions": [{"type": "ferry", "description": "Free ferry for the vehicle and the driver "}]}
    rec = transfercar.normalise_item(item, None, nz.name, nz.site, nz.region, nz.currency)
    assert rec.source == "transfercar_nz" and (rec.region, rec.currency) == ("NZ", "NZD")
    assert rec.url == "https://www.transfercar.co.nz/relocation/Harewood_(Christchurch)/Pokeno/679791"
    assert (rec.origin, rec.destination, rec.vehicle_type, rec.berths) == ("Christchurch", "Pokeno", "motorhome", 4)


def test_nz_city_names_and_money():
    assert normalise.city("Queenstown Airport", "NZ") == "Queenstown"
    assert normalise.city("Takanini (Auckland)", "NZ") == "Auckland"
    assert normalise.city("Palmerston North", "NZ") == "Palmerston North"
    assert normalise.city("Hamilton Island Airport") == "Hamilton Island"   # Australia: not NZ's Hamilton
    assert normalise.dollars(123.4, "NZD") == "NZ$123" and normalise.dollars(1234, "AUD") == "$1,234"
    assert normalise.dollars(None) == "****"


def test_nz_matching_and_fuel(cfg):
    row = {"vehicle_type": "motorhome", "berths": 6, "origin": "Auckland", "destination": "Queenstown",
           "days_included": 7, "pickup_from": date(2026, 11, 2), "pickup_to": date(2026, 11, 5), "region": "NZ"}
    assert filters.evaluate(row, cfg, date(2026, 10, 30))["matched"]          # the AU city list doesn't apply
    cfg["filters"]["cities_nz"] = ["Christchurch"]
    assert not filters.evaluate(row, cfg, date(2026, 10, 30))["matched"]
    direct = {"drive_km": 1000, "drive_hours": 12, "ferry_km": 92, "roads": []}
    nz = trips.stats({**row, "region": "NZ"}, direct, cfg)
    au = trips.stats({**row, "region": "AU"}, direct, cfg)
    assert nz["fuel_cost"] == 390 and au["fuel_cost"] == 315 and nz["ferry"]  # 150 L at NZ$2.60 vs $2.10


def test_old_single_fuel_price_still_works():
    from rcw import config
    cfg = config._check(config._merge(config.DEFAULTS, {"trip": {"fuel_price_per_litre": 2.30}}))
    assert cfg["trip"]["fuel_price_per_litre"] == {"AU": 2.30, "NZ": 2.60}
