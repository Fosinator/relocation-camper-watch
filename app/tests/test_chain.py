"""Daisy-chaining: Sydney -> Hobart (picks up 16-20 Nov, 5 days, 16.5 h of driving) then a leg out of Hobart."""
from datetime import date

from rcw import chain

FIRST = {"id": 1, "origin": "Sydney", "destination": "Hobart", "pickup_from": date(2026, 11, 16),
         "pickup_to": date(2026, 11, 20), "days_included": 5, "extra_days": 1, "trip": {"drive_hours": 16.5}}


def leg(**kw):
    return {**{"id": 2, "origin": "Hobart", "destination": "Melbourne", "pickup_from": date(2026, 11, 22),
               "pickup_to": date(2026, 11, 30)}, **kw}


def test_arrival_window(cfg):
    # fastest: 16.5 h at 9 h a day = 2 days after the first pickup day; latest: last pickup day + 5 + 1 days
    assert chain.arrival_window(FIRST, cfg) == (date(2026, 11, 18), date(2026, 11, 26))


def test_which_legs_fit(cfg):
    assert chain.fits_after(FIRST, leg(), cfg)
    assert chain.fits_after(FIRST, leg(origin="hobart"), cfg)                     # case doesn't matter
    assert not chain.fits_after(FIRST, leg(origin="Launceston"), cfg)             # starts somewhere else
    assert not chain.fits_after(FIRST, leg(pickup_from=date(2026, 11, 27), pickup_to=date(2026, 12, 3)), cfg)
    assert not chain.fits_after(FIRST, leg(pickup_from=date(2026, 11, 10), pickup_to=date(2026, 11, 17)), cfg)
    assert chain.fits_after(FIRST, leg(pickup_from=None, pickup_to=None), cfg)    # dates unknown: still shown
    assert not chain.fits_after(FIRST, FIRST, cfg)


def test_onward_and_places(cfg):
    rows = [FIRST, leg(), leg(id=3, origin="Perth")]
    assert [r["id"] for r in chain.onward(FIRST, rows, cfg)] == [2]
    assert chain.places(rows, "origin") == [("Hobart", 1), ("Perth", 1), ("Sydney", 1)]
