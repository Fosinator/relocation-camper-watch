import json
from datetime import date

from conftest import fixture_text

from rcw import normalise, seroval
from rcw.http import USER_AGENT, robots_parser
from rcw.sources import imoova, transfercar


# --- the embedded-data reader -------------------------------------------------------------------------------------

def test_seroval_reads_flat_fields_and_skips_nested():
    text = ('x={__typename:"T",a:"q\\"uote",b:!0,c:!1,d:null,e:12,f:-1.5,g:$R[3]={__typename:"N",z:1},'
            'h:[1,{y:2}],i:$R[4],j:new Date("2026-10-29"),k:"\\x3Cp>hi\\x3C/p>",l:"end"}')
    [o] = seroval.objects(text, "T")
    assert o == {"__typename": "T", "a": 'q"uote', "b": True, "c": False, "d": None, "e": 12, "f": -1.5,
                 "j": None, "k": "<p>hi</p>", "l": "end"}


def test_imoova_table_fixture_parses():
    page = fixture_text("imoova_table.html")
    assert len(seroval.objects(page, "RelocationTableItem")) == 80
    au = imoova.live_items(page)
    assert len(au) >= 40
    assert all(i["departure_city_region"] == "AU" for i in au)


def test_imoova_normalises_a_motorhome():
    au = imoova.live_items(fixture_text("imoova_table.html"))
    item = next(i for i in au if i["vehicle_type"] == "MOTOR_HOME" and "Berth" in (i["vehicle_name"] or ""))
    rec = imoova.normalise_item(item, None)
    assert rec.source == "imoova" and rec.listing_id == item["id"]
    assert rec.vehicle_type == "motorhome" and not rec.four_wd
    assert rec.berths == normalise.berths(item["vehicle_name"]) and rec.berths >= 2
    assert rec.url == (f"https://www.imoova.com/relocations/australia/{item['departure_city_slug']}-to-"
                       f"{item['delivery_city_slug']}/{item['reference']}")
    assert rec.pickup_from == date.fromisoformat(item["available_from_date"])
    assert rec.days_included == item["relocation_units"]


def test_imoova_types_inclusions_and_detail():
    item = {"id": "1", "reference": "RLC1", "type": "RELOCATION", "count": 2, "vehicle_type": "FOUR_WHEEL_DRIVE_CAMPER",
            "vehicle_name": "Hi-Top 4WD", "vehicle_sleeps": None, "departure_city_name": "Darwin",
            "departure_city_slug": "darwin", "delivery_city_name": "Perth", "delivery_city_slug": "perth",
            "available_from_date": "2026-11-01", "available_to_date": "2026-11-20", "latest_departure_date": "2026-11-10",
            "relocation_units": 8, "extra_units": 3, "extra_hire_unit_rate": 9500, "has_fuel": True,
            "fuel_amount": 30000, "fuel_description": None, "has_ferry": False}
    detail = {"vehicle": imoova.vehicle_from_page(fixture_text("imoova_detail.html"))}
    rec = imoova.normalise_item(item, detail)
    assert rec.vehicle_type == "campervan" and rec.four_wd
    assert rec.berths == 6  # the fixture's vehicle is a "6 Berth Motorhome"
    assert rec.pickup_to == date(2026, 11, 10)
    assert rec.extra_days == 3 and rec.extra_day_price == 95
    assert rec.inclusions == "Fuel $300"
    assert "chock-full" in rec.description and "<p" not in rec.description
    assert "shower and toilet" in rec.description
    assert imoova.normalise_item({**item, "vehicle_type": "SUV"}, None).vehicle_type == "other"
    assert imoova.live_items('{__typename:"RelocationTableItem",id:"9",count:0,departure_city_region:"AU"}') == []


# --- Transfercar -----------------------------------------------------------------------------------------------

def test_transfercar_vehicle_kinds():
    assert transfercar.vehicle_kind("Motorhome (6 berth)") == ("motorhome", False)
    assert transfercar.vehicle_kind("4x4 Campervan (2 berth)") == ("campervan", True)
    assert transfercar.vehicle_kind("Campervan Compact (2 berth)") == ("campervan", False)
    assert transfercar.vehicle_kind("Vehicle varies") == ("other", False)
    assert transfercar.vehicle_kind("SUV (7 seats)") == ("other", False)


def test_transfercar_list_fixture_normalises():
    items = json.loads(fixture_text("transfercar_list.json"))["listings"]
    recs = [transfercar.normalise_item(i, None) for i in items]
    assert len(recs) == len(items) > 100
    assert {r.vehicle_type for r in recs} >= {"motorhome", "campervan", "other"}
    assert all(r.url.startswith("https://www.transfercar.com.au/relocation/") for r in recs)
    assert all(r.pickup_from for r in recs)


def test_transfercar_item_with_detail():
    detail = json.loads(fixture_text("transfercar_detail.json"))
    item = {"id": 683279, "vehicle_type": "Campervan (3 berth)", "pickup_location": "Caringbah (Sydney)",
            "dropoff_location": "Hobart Airport", "pickup_date": "2026-10-30T00:00:00Z",
            "dropoff_date": "2026-11-03T00:00:00Z", "free_days": 10, "paid_days": 10, "price_per_paid_day": 125,
            "inclusions": [{"type": "petrol", "description": "250"}, {"type": "ferry", "description": ""}]}
    rec = transfercar.normalise_item(item, detail)
    assert rec.url == "https://www.transfercar.com.au/relocation/Caringbah_(Sydney)/Hobart_Airport/683279"
    assert (rec.origin, rec.destination) == ("Sydney", "Hobart")
    assert rec.vehicle_name == "HiTop Camper – Campervan (3 berth)"
    assert rec.berths == 3 and rec.bond == 3500 and rec.operator == "Travellers Autobarn AU"
    assert rec.pickup_from == date(2026, 10, 30) and rec.pickup_to == date(2026, 11, 3)
    assert rec.extra_day_price == 125
    assert rec.inclusions == "Fuel $250 · Ferry included"
    assert "double bed" in rec.description


def test_city_names():
    assert normalise.city("Bankstown Sydney Airport") == "Sydney"
    assert normalise.city("Coolangatta Gold Coast Airport") == "Gold Coast"
    assert normalise.city("Parafield Gardens (Adelaide)") == "Adelaide"
    assert normalise.city("Proserpine (Whitsunday Coast) Airport") == "Whitsunday Coast"
    assert normalise.city("Port Lincoln Airport") == "Port Lincoln"
    assert normalise.city("Somewhere Airport") == "Somewhere"
    assert normalise.city(None) is None


# --- robots.txt --------------------------------------------------------------------------------------------------

def test_robots_rules_as_checked_2026_10_29():
    api = robots_parser(["User-agent: *", "Disallow: /"])
    assert not api.can_fetch(USER_AGENT, "https://api.imoova.com/graphql?RelocationList")
    www = robots_parser(["User-agent: *", "Allow: /", "Disallow: /bookings/*", "Disallow: /sign-in?*"])
    assert www.can_fetch(USER_AGENT, imoova.TABLE_URL)
    assert www.can_fetch(USER_AGENT, "https://www.imoova.com/relocations/australia/darwin-to-cairns/RLC128631")
