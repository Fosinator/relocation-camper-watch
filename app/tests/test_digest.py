from datetime import date

from rcw.digest import format_digest


def r(id, **kw):
    base = {"id": id, "source": "imoova", "boosted": False, "vehicle_type": "motorhome", "vehicle_name": "x",
            "four_wd": False, "berths": 4, "days_included": 6, "extra_days": 2, "origin": "Melbourne",
            "destination": "Hobart", "pickup_from": date(2026, 11, 3), "inclusions": None, "status": "open",
            "url": f"https://example.test/{id}"}
    return {**base, **kw}


DAY = date(2026, 10, 29)


def test_quiet_day_still_says_zero(cfg):
    text = format_digest(DAY, [], {}, 0, [], 3.0, cfg, "http://10.1.1.200")
    assert text.splitlines()[:2] == ["Relocations – Thu 29 Oct", "0 new matches · 0 gone"]
    assert text.endswith("Dashboard: http://10.1.1.200")


def test_counts_stars_duplicates_and_missing_values(cfg):
    new = [r(1, boosted=True, inclusions="Ferry included"), r(2, berths=None, status="gone")]
    dups = {1: [r(9, source="transfercar", url="https://tc.test/9")]}
    text = format_digest(DAY, new, dups, 5, [], 3.0, cfg, "http://10.1.1.200")
    lines = text.splitlines()
    assert lines[1] == "2 new matches (1 ⭐) · 5 gone"
    assert lines[3] == "⭐ Melbourne → Hobart · motorhome, 4 berths · 6 days (+2) · from 3 Nov · Ferry included"
    assert lines[4] == "   https://example.test/1"
    assert lines[5] == "   also on Transfercar: https://tc.test/9"
    assert "**** berths" in lines[6] and "already gone" in lines[6]


def test_failures_db_size_and_overflow(cfg):
    cfg["alerts"]["digest_max_listed"] = 1
    text = format_digest(DAY, [r(1), r(2), r(3)], {}, 0, ["Transfercar: 3 failed polls"], 612.0, cfg, "http://x")
    assert "+ 2 more on the dashboard" in text
    assert "⚠ Transfercar: 3 failed polls" in text
    assert "⚠ Database is 612 MB (warning level 500 MB)" in text
