from datetime import date, timedelta

from rcw.filters import evaluate

TODAY = date(2026, 10, 29)


def row(**kw):
    base = {"vehicle_type": "motorhome", "four_wd": False, "berths": 4, "origin": "Melbourne", "destination": "Cairns",
            "days_included": 6, "pickup_from": TODAY, "pickup_to": TODAY + timedelta(days=10),
            "vehicle_name": "4 Berth Motorhome", "description": None, "inclusions": None}
    return {**base, **kw}


def test_a_plain_match(cfg):
    ev = evaluate(row(), cfg, TODAY)
    assert ev["matched"] and not ev["boosted"] and ev["fail_reasons"] is None


def test_each_hard_filter(cfg):
    assert "vehicle type other" in evaluate(row(vehicle_type="other"), cfg, TODAY)["fail_reasons"]
    assert evaluate(row(berths=1), cfg, TODAY)["fail_reasons"] == "1 berths"
    assert evaluate(row(berths=2), cfg, TODAY)["matched"]  # David: 2 berths are OK
    assert not evaluate(row(origin="Darwin", destination="Cairns"), cfg, TODAY)["matched"]
    assert evaluate(row(origin="Darwin", destination="Hobart"), cfg, TODAY)["matched"]  # destination counts too
    assert not evaluate(row(days_included=3), cfg, TODAY)["matched"]
    assert not evaluate(row(pickup_from=TODAY + timedelta(days=61)), cfg, TODAY)["matched"]
    assert evaluate(row(pickup_from=TODAY + timedelta(days=60)), cfg, TODAY)["matched"]
    assert not evaluate(row(pickup_from=TODAY - timedelta(days=9), pickup_to=TODAY - timedelta(days=1)), cfg, TODAY)["matched"]


def test_missing_values_pass(cfg):
    ev = evaluate(row(berths=None, days_included=None, pickup_from=None, pickup_to=None, origin=None,
                      destination=None), cfg, TODAY)
    assert ev["matched"]


def test_four_wd_setting(cfg):
    camper4 = row(vehicle_type="campervan", four_wd=True)
    assert evaluate(camper4, cfg, TODAY)["matched"]
    cfg["filters"]["four_wd"] = "exclude"
    assert evaluate(camper4, cfg, TODAY)["fail_reasons"] == "4WD"
    assert evaluate(row(), cfg, TODAY)["matched"]
    cfg["filters"]["four_wd"] = "only"
    assert evaluate(camper4, cfg, TODAY)["matched"]
    assert evaluate(row(), cfg, TODAY)["fail_reasons"] == "not 4WD"


def test_excluded_vehicle_names(cfg):
    for name in ["4 Berth-Hitop", "2 Berth Hi-Top", "4 Berth HiTop – Campervan (4 berth)",
                 "Oc Active Poptop 4 Auto Select", "Pop Top Camper"]:
        ev = evaluate(row(vehicle_type="campervan", vehicle_name=name), cfg, TODAY)
        assert not ev["matched"] and ev["fail_reasons"].startswith("excluded: "), name
    # only the name counts: a description mentioning a pop-top doesn't exclude a motorhome
    assert evaluate(row(description="roomier than a pop-top"), cfg, TODAY)["matched"]
    cfg["filters"]["exclude_keywords"] = []
    assert evaluate(row(vehicle_name="2 Berth Hi-Top"), cfg, TODAY)["matched"]


def test_boosts(cfg):
    ev = evaluate(row(vehicle_name="Avida Esperance", description="Rear lounge, drop down bed"), cfg, TODAY)
    assert ev["boosted"] and ev["boost_reasons"] == "rear lounge, drop-down bed, Avida"
    assert evaluate(row(inclusions="Fuel $300 · Ferry included"), cfg, TODAY)["boost_reasons"] == "fuel, ferry"
    # a boost never rescues a listing that fails a hard filter
    ev = evaluate(row(vehicle_name="Winnebago", berths=1), cfg, TODAY)
    assert ev["boosted"] and not ev["matched"]
