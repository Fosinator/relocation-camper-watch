"""Against a real, throw-away Postgres database (TEST_DATABASE_URL; deploy.sh points it at relocation_watch_test).

Covers the acceptance criteria that need the database: re-polling creates no duplicates, gone after two missed
polls, filter edits apply at the next poll, an outage shows up and alerts once, dry-run digests, and a deal on
both sites listed once with both links.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from rcw import alerts, db, digest, poller, settings
from rcw.sources import Listing

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

T0 = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)  # 14:00 in Hobart


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", URL)
    monkeypatch.setattr(settings, "DRY_RUN", True)
    with db.conn() as c:
        c.execute("""DROP TABLE IF EXISTS relocations, polls, source_state, digests, messages, road_routes, watches,
                     watch_seen, prefs, fuel_prices CASCADE""")
    from rcw import prefs
    prefs._cache.update(at=0.0, values={})
    with db.conn() as c:
        db.migrate(c)


class Fake:
    def __init__(self, name, listings):
        self.name, self.label, self.listings, self.fail = name, name.title(), listings, None

    def fetch(self, http, cfg, cached):
        if self.fail:
            raise self.fail
        return list(self.listings)


class NoHttp:
    requests = 0

    def close(self):
        pass


def listing(source, lid, **kw):
    base = dict(url=f"https://{source}.test/{lid}", vehicle_name="4 Berth Motorhome", vehicle_type="motorhome",
                berths=4, origin="Melbourne", destination="Hobart", pickup_from=date(2026, 11, 2),
                pickup_to=date(2026, 11, 20), days_included=6)
    return Listing(source=source, listing_id=lid, **{**base, **kw})


def run(src, cfg, now, **kw):
    return poller.poll(src.name, cfg=cfg, src=src, http=NoHttp(), now=now, **kw)


def rows(where="TRUE"):
    with db.conn() as c:
        return c.execute(f"SELECT * FROM relocations WHERE {where} ORDER BY id").fetchall()


def test_repolling_creates_no_duplicates(cfg):
    src = Fake("imoova", [listing("imoova", "1"), listing("imoova", "2", berths=1)])
    first = run(src, cfg, T0)
    second = run(src, cfg, T0 + timedelta(hours=1))
    assert (first["new_listings"], first["new_matches"]) == (2, 1)
    assert (second["new_listings"], second["new_matches"]) == (0, 0)
    assert len(rows()) == 2
    assert [r["matched"] for r in rows()] == [True, False]


def test_gone_after_two_missed_polls_and_failures_dont_count(cfg):
    src = Fake("imoova", [listing("imoova", "1"), listing("imoova", "2")])
    run(src, cfg, T0)
    src.listings = [listing("imoova", "1")]
    run(src, cfg, T0 + timedelta(hours=1))
    assert rows("listing_id = '2'")[0]["status"] == "open"
    src.fail = RuntimeError("site down")
    run(src, cfg, T0 + timedelta(hours=2), force=True)
    assert rows("listing_id = '2'")[0]["missed_polls"] == 1  # the failed poll didn't count
    src.fail = None
    run(src, cfg, T0 + timedelta(hours=3), force=True)
    gone = rows("listing_id = '2'")[0]
    assert gone["status"] == "gone" and gone["gone_at"] == T0 + timedelta(hours=3)
    src.listings = [listing("imoova", "1"), listing("imoova", "2")]  # back again
    run(src, cfg, T0 + timedelta(hours=4))
    assert rows("listing_id = '2'")[0]["status"] == "open"


def test_filter_change_applies_at_next_poll(cfg):
    src = Fake("imoova", [listing("imoova", "1")])
    run(src, cfg, T0)
    cfg["filters"]["min_days"] = 10
    run(src, cfg, T0 + timedelta(hours=1))
    r = rows()[0]
    assert not r["matched"] and r["fail_reasons"] == "6 days"


def test_backoff_then_outage_alert_once(cfg):
    src = Fake("transfercar", [listing("transfercar", "1")])
    cfg["sources"]["transfercar"]["simulate_outage"] = True
    out = run(src, cfg, T0)
    assert "simulated outage" in out["error"]
    assert run(src, cfg, T0 + timedelta(minutes=30))["skipped"].startswith("backing off")
    with db.conn() as c:
        st = db.source_state(c, "transfercar")
    assert st["failing_since"] == T0 and st["consecutive_failures"] == 1
    assert alerts.check_outages(cfg, T0 + timedelta(hours=23)) == 0
    assert alerts.check_outages(cfg, T0 + timedelta(hours=25)) == 1
    assert alerts.check_outages(cfg, T0 + timedelta(hours=26)) == 0  # one message per outage
    text = digest.preview(cfg, T0 + timedelta(hours=26))
    assert "⚠ Transfercar:" in text and "failing since" in text and "simulated outage" in text
    cfg["sources"]["transfercar"]["simulate_outage"] = False
    run(src, cfg, T0 + timedelta(hours=27), force=True)
    with db.conn() as c:
        st = db.source_state(c, "transfercar")
    assert st["failing_since"] is None and st["outage_alerted_at"] is None


def test_trip_stats_refresh_caches_routes(cfg):
    import json

    from conftest import fixture_text

    from rcw import trips

    class FakeRouting:
        calls = 0

        def get(self, url):
            FakeRouting.calls += 1
            data = json.loads(fixture_text("osrm_darwin_uluru_perth.json"))
            return type("R", (), {"json": lambda self: data})()

        def close(self):
            pass

    src = Fake("imoova", [listing("imoova", "1", origin="Darwin", destination="Adelaide", km_allowed=4500)])
    run(src, cfg, T0)  # routing budget 0 in tests: no stats yet
    assert rows()[0]["trip"] is None
    cfg["trip"]["routing_requests_per_poll"] = 50
    first = trips.refresh(cfg, http=FakeRouting())
    t = rows()[0]["trip"]
    assert first["done"] == 1 and 3900 < t["route_km"] < 4100 and t["pace"] and t["cap_km"] == 4500
    assert FakeRouting.calls == 1  # one route per listing
    calls = FakeRouting.calls
    trips.refresh(cfg, http=FakeRouting())
    assert FakeRouting.calls == calls  # every route came from road_routes the second time


def test_every_page_renders_with_trip_stats(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from rcw import config, trips, web

    monkeypatch.setattr(config, "load", lambda path=None: cfg)
    src = Fake("transfercar", [listing("transfercar", "1", origin="Darwin", destination="Adelaide",
                                       km_allowed=3400, excess_km_rate=0.5, extra_days=4, extra_day_price=75)])
    run(src, cfg, T0)
    direct = {"drive_km": 3022, "drive_hours": 32.4, "ferry_km": 0, "roads": ["Stuart Highway"]}
    with db.conn() as c:
        c.execute("UPDATE relocations SET trip = %s", (db.Jsonb(trips.stats(rows()[0], direct, cfg)),))
    client = TestClient(web.app)  # no "with": the scheduler stays off
    for path in ["/", "/?sort=explore", "/?sort=soonest&fourwd=only", "/?all=1", "/?from=Darwin&to=Adelaide",
                 f"/?after={rows()[0]['id']}", "/?from=Nowhere", "/?sort=cost", "/?sort=easy", "/?sort=fuel",
                 "/?sort=newest", "/home", "/routes", "/routes?from=Darwin&to=Adelaide&sort=easy",
                 "/routes?ferry=1&fuel=1&matches=1", "/alerts", "/?region=NZ", "/?region=AU&from=Darwin",
                 "/history", "/history?all=1", "/stats", "/stats?all=1"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    page = client.get("/").text
    assert "trip stats" in page and "3,400 km: 378 km beyond the route" in page and "$0.50/km" in page
    assert "limited by the days" in page and "limited by the km cap" in page
    assert '<option value="Darwin">Darwin (1)</option>' in page
    assert "Nothing listed on Anywhere → Hobart · ferry included" in client.get("/routes?to=Hobart&ferry=1").text
    chained = client.get(f"/?after={rows()[0]['id']}").text
    assert "Your chain so far" in chained and "Darwin → Adelaide</a>" in chained and "clear the chain" in chained
    assert "↪ nothing leaves Adelaide right now" in page          # every card has an onward line, even with none


def test_chaining_keeps_the_trail_and_offers_late_legs(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from rcw import config, web
    monkeypatch.setattr(config, "load", lambda path=None: cfg)
    src = Fake("imoova", [
        listing("imoova", "1", origin="Sydney", destination="Hobart", pickup_from=date(2026, 11, 2),
                pickup_to=date(2026, 11, 5)),
        listing("imoova", "2", origin="Hobart", destination="Melbourne", pickup_from=date(2026, 11, 6),
                pickup_to=date(2026, 11, 12)),
        listing("imoova", "3", origin="Melbourne", destination="Sydney", pickup_from=date(2026, 12, 20),
                pickup_to=date(2026, 12, 22))])                                          # leaves Melbourne too late
    run(src, cfg, T0)
    ids = {r["listing_id"]: r["id"] for r in rows()}
    client = TestClient(web.app)
    page = client.get("/").text
    assert "↪ 1 onward leg from Hobart" in page and "↪ 1 leaves Melbourne, none in time" in page
    two = client.get(f"/?after={ids['1']},{ids['2']}").text                             # Sydney -> Hobart -> Melbourne
    assert "2 legs" in two and "Sydney → Hobart</a>" in two and "Hobart → Melbourne</a>" in two
    assert "back a leg" in two and f"after={ids['1']}#matches" in two                # back = the first leg only


def test_route_alerts_baseline_new_ones_and_switching_off(cfg, capsys, monkeypatch):
    from rcw import routes
    cfg["alerts"]["dry_run"] = True
    ferry = "Ferry included Free ferry for vehicle and driver"
    src = Fake("imoova", [listing("imoova", "1", origin="Sydney", destination="Hobart", inclusions=ferry),
                          listing("imoova", "2", origin="Sydney", destination="Hobart")])   # no ferry: not on the route
    run(src, cfg, T0)
    with db.conn() as c:
        routes.seed_home_watch(c, cfg, T0.date(), T0)   # Anywhere -> Hobart with the ferry: listing 1 is the baseline
    run(src, cfg, T0 + timedelta(hours=1))
    assert "New on" not in capsys.readouterr().out
    src.listings.append(listing("imoova", "3", origin="Melbourne", destination="Hobart", berths=1,
                                vehicle_type="other", inclusions="Fuel $250 · " + ferry))  # any vehicle counts
    run(src, cfg, T0 + timedelta(hours=2))
    out = capsys.readouterr().out
    assert "🔔 New on Anywhere → Hobart · ferry included · any vehicle" in out
    assert "Melbourne → Hobart" in out and "Sydney" not in out and "⛽ fuel $250" in out
    run(src, cfg, T0 + timedelta(hours=3))  # already sent: nothing more
    assert "New on" not in capsys.readouterr().out
    assert "🔔 Anywhere → Hobart · ferry included · any vehicle: 2 listed" in digest.preview(cfg, T0 + timedelta(hours=3))
    # switched off: new ones are only marked seen, so switching back on sends no backlog
    with db.conn() as c:
        c.execute("UPDATE watches SET alert = false")
    src.listings.append(listing("imoova", "4", origin="Adelaide", destination="Hobart", inclusions=ferry))
    run(src, cfg, T0 + timedelta(hours=4))
    with db.conn() as c:
        c.execute("UPDATE watches SET alert = true")
    run(src, cfg, T0 + timedelta(hours=5))
    assert "New on" not in capsys.readouterr().out

    from fastapi.testclient import TestClient

    from rcw import config, web
    monkeypatch.setattr(config, "load", lambda path=None: cfg)
    monkeypatch.setattr(web.timefmt, "today", lambda ts=None: T0.date())
    client = TestClient(web.app)
    page = client.get("/routes").text                     # no query: the first saved route
    assert all(f'<div class="route">{o} → Hobart</div>' in page for o in ("Sydney", "Melbourne", "Adelaide"))
    assert "⛽ $250 fuel" in page and "included for the vehicle + driver" in page and "🔔 Alerts on" in page
    sydney = client.get("/routes?from=Sydney&to=Hobart").text  # any vehicle, ferry or not
    assert sydney.count('<div class="route">Sydney → Hobart</div>') == 2 and "Melbourne → Hobart</div>" not in sydney
    assert "Alert me about this route" in sydney
    r = client.post("/routes/watch?from=Sydney&to=Hobart")
    assert r.status_code == 200 and "watch=" in str(r.url) and "🔔 Alerts on" in r.text
    alerts_page = client.get("/alerts").text
    assert "Sydney → Hobart · any vehicle" in alerts_page and "Anywhere → Hobart · ferry included" in alerts_page
    wid = int(str(r.url).split("watch=")[1])
    client.post(f"/alerts/route/{wid}?on=0")
    with db.conn() as c:
        assert c.execute("SELECT alert FROM watches WHERE id = %s", (wid,)).fetchone()["alert"] is False
    client.post(f"/alerts/route/{wid}?delete=1")
    with db.conn() as c:
        assert c.execute("SELECT 1 FROM watches WHERE id = %s", (wid,)).fetchone() is None
    assert client.get("/home").url.path == "/routes"


def test_route_alert_ignores_extra_copies_of_a_deal(cfg, capsys):
    from rcw import routes
    cfg["alerts"]["dry_run"] = True
    ferry = "Ferry included Free ferry for vehicle and driver"
    copy = dict(origin="Adelaide", destination="Hobart", inclusions=ferry, pickup_from=date(2026, 11, 16),
                vehicle_type="campervan", berths=2)
    src = Fake("transfercar", [listing("transfercar", "1", **copy)])
    run(src, cfg, T0)
    with db.conn() as c:
        routes.seed_home_watch(c, cfg, T0.date(), T0)          # listing 1 = baseline
    src.listings += [listing("transfercar", "2", **copy), listing("transfercar", "3", **copy)]  # more of the same
    run(src, cfg, T0 + timedelta(hours=1))
    assert "New on" not in capsys.readouterr().out             # copies of a deal already seen: quiet
    src.listings += [listing("transfercar", n, **{**copy, "pickup_from": date(2026, 11, 27)}) for n in ("4", "5")]
    run(src, cfg, T0 + timedelta(hours=2))
    out = capsys.readouterr().out
    assert out.count("Adelaide → Hobart") == 1 and "×2 available" in out and "pick up 27 Nov" in out
    with db.conn() as c:
        assert c.execute("SELECT count(*) AS n FROM watch_seen").fetchone()["n"] == 5


def test_master_switch_stops_everything(cfg, capsys):
    from fastapi.testclient import TestClient

    from rcw import config, prefs, signal, web
    prefs.set_switch("signal", False)
    off = prefs.apply(cfg)
    assert signal.paused(off) and signal.is_dry_run(off)
    assert signal.send("hello", off) is True and "switched off: Signal message" in capsys.readouterr().out
    client = TestClient(web.app)
    page = client.get("/stats").text
    assert "Signal messages are switched off" in page and "🔕 Signal off" in page
    r = client.post("/alerts/switch?key=signal&on=1&back=/stats")
    assert r.url.path == "/stats" and "🔔 Signal on" in r.text
    assert prefs.apply(config.defaults())["alerts"]["signal"] is True
    assert client.post("/alerts/switch?key=signal&on=1&back=//evil.example").url.path == "/alerts"


def test_alert_switches_override_config(cfg):
    from rcw import alerts, prefs
    prefs.set_switch("digest", False)
    prefs.set_switch("outage", False)
    switched = prefs.apply(cfg)
    assert switched["alerts"]["digest"] is False and switched["alerts"]["outage"] is False
    assert digest.due(switched, T0 + timedelta(hours=6)) is False     # 09:00 UTC = 8 pm in Hobart
    assert alerts.check_outages(switched, T0) == 0
    prefs.set_switch("digest", True)
    assert prefs.apply(cfg)["alerts"]["digest"] is True


def test_digest_dry_run_and_duplicates(cfg, capsys):
    cfg["alerts"]["dry_run"] = True
    im = Fake("imoova", [listing("imoova", "1", inclusions="Ferry included")])
    tc = Fake("transfercar", [listing("transfercar", "A", berths=None)])
    run(im, cfg, T0)
    run(tc, cfg, T0 + timedelta(minutes=5))
    assert digest.send(cfg, T0 + timedelta(hours=3))
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "1 new match (1 ⭐) · 0 gone" in out
    assert "https://imoova.test/1" in out and "also on Transfercar: https://transfercar.test/A" in out
    assert all(r["digested_at"] for r in rows())
    assert "0 new matches" in digest.preview(cfg, T0 + timedelta(days=1))
