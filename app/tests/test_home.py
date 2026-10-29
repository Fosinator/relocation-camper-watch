"""Ways home to Hobart: the cost estimate, the ferry wording and the same van on both sites (2026-10-29 data:
Sydney -> Hobart, Cruisin Motorhomes 6-berth listed on Imoova and Transfercar, ferry Geelong -> Devonport)."""
from datetime import date

from rcw import home


def row(**kw):
    base = {"id": 1, "source": "imoova", "origin": "Sydney", "destination": "Hobart", "vehicle_type": "motorhome",
            "vehicle_name": "6 Berth Motorhome", "berths": 6, "days_included": 5, "pickup_from": date(2026, 11, 16),
            "url": "https://www.imoova.com/relocations/australia/sydney-to-hobart/RLC127164",
            "inclusions": "Ferry included Geelong to Devonport 17.11.2026 6:45pm (Recliner)",
            "raw": {"item": {"hire_unit_rate": 100, "booking_fee_amount": 5900}},
            "trip": {"fuel_cost": 380, "fuel_allowance": None}}
    return {**base, **kw}


def test_cost_imoova(cfg):
    c = home.cost(row(), cfg)
    assert c["parts"] == [("hire", 5), ("booking fee", 59), ("fuel", 380)] and c["total"] == 444
    assert c["notes"] == ["plus a ferry seat for 1 more passenger"]
    cfg["home"]["extra_passenger_ferry_aud"] = 150
    assert home.cost(row(), cfg)["total"] == 594


def test_cost_transfercar_with_fuel_allowance(cfg):
    r = row(source="transfercar", raw={"item": {"price_per_free_day": 1}},
            inclusions="Fuel $250 · Ferry included Free ferry for vehicle and driver.",
            trip={"fuel_cost": 304, "fuel_allowance": 250})
    c = home.cost(r, cfg)
    assert c["parts"] == [("hire", 5), ("fuel after the allowance", 54)] and c["total"] == 59
    assert "booking fee not shown by Transfercar" in c["notes"]


def test_ferry_text():
    assert home.ferry_text("Fuel $250 · Ferry included Bass Strait ferry for 1 vehicle") == "Bass Strait ferry for 1 vehicle"
    assert home.ferry_text("Ferry included") == "included"
    assert home.ferry_text("Fuel $300") is None


def test_same_van_on_both_sites_is_one_deal(cfg):
    im = row()
    tc = row(id=2, source="transfercar", vehicle_name="6 Berth Motorhome with Toilet and Shower",
             url="https://www.transfercar.com.au/relocation/Caringbah_(Sydney)/Hobart_Airport/684495")
    other_day = row(id=3, pickup_from=date(2026, 11, 18))
    deals = home.group([im, tc, other_day])
    assert len(deals) == 2 and [a["id"] for a in deals[0]["also"]] == [2]
    for d in deals:
        d["home_cost"], d["ferry"], d["fuel_allowance"] = home.cost(d, cfg), home.ferry_text(d["inclusions"]), None
    lines = home.message_lines(deals[0])
    assert lines[0] == "Sydney → Hobart · 6-berth motorhome · 5 days · pick up 16 Nov"
    assert lines[1] == "⛴ ferry sailing booked Geelong → Devonport 17 Nov 6:45pm"
    assert lines[2] == "About $444 out of pocket (plus a ferry seat for 1 more passenger)"
    assert lines[3].startswith("Imoova: https://") and lines[4].startswith("Transfercar: https://")


def test_ferry_summaries_from_the_live_wording():
    s = home.ferry_summary
    assert s("This relocation includes reimbursement for the ferry ticket for the vehicle and 1 driver in a "
             "recliner seat up to $350. Cabin upgades or additional passengers will NOT be reimbursed.") \
        == "refunded up to $350"
    assert s("Book own ferry. $350 refundable against receipt for vehicle with 1 person in recliner seat") \
        == "book your own, refunded up to $350"
    assert s("Free ferry for vehicle and driver. geelong to devonport 17.11.2026 6:45pm (recliner)") \
        == "sailing booked Geelong → Devonport 17 Nov 6:45pm"
    assert s("Geelong to Devenport 19.11.2026 6:45pm (Recliner)") == "sailing booked 19 Nov 6:45pm"  # their typo
    assert s("Bass Strait ferry included for 1 vehicle + 1 passenger. Additional passengers can be added") \
        == "included for the vehicle + 1 passenger"
    assert s("Ferry spot for the vehicle and driver (ocean recliner) booked by rental operator") \
        == "included for the vehicle + driver"
    assert s(None) == "included for the vehicle + driver"


def test_where_from_cities(cfg):
    cond, params = home.where(cfg, date(2026, 10, 29))
    assert "ferry" in cond and "home_from" not in params
    cfg["home"]["from_cities"] = ["Sydney", "Melbourne"]
    cond, params = home.where(cfg, date(2026, 10, 29))
    assert params["home_from"] == ["sydney", "melbourne"]
