"""NSW FuelCheck (David 2026-10-31): median diesel per state, state price by where a trip starts."""
from datetime import datetime

from rcw import fuelcheck, settings, timefmt, trips

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timefmt.TZ)
SAMPLE = {"stations": [], "prices": [
    {"stationcode": "1", "state": "NSW", "fueltype": "DL", "price": 205.9, "lastupdated": "31/10/2026 07:10:00"},
    {"stationcode": "2", "state": "NSW", "fueltype": "DL", "price": 211.9, "lastupdated": "30/10/2026 18:00:00"},
    {"stationcode": "3", "state": "NSW", "fueltype": "DL", "price": 199.9, "lastupdated": "31/10/2026 06:00:00"},
    {"stationcode": "4", "state": "NSW", "fueltype": "U91", "price": 189.9, "lastupdated": "31/10/2026 06:00:00"},
    {"stationcode": "5", "state": "NSW", "fueltype": "DL", "price": 1.99, "lastupdated": "31/10/2026 06:00:00"},
    {"stationcode": "6", "state": "NSW", "fueltype": "DL", "price": 150.0, "lastupdated": "01/09/2026 06:00:00"},
    {"stationcode": "7", "state": "TAS", "fueltype": "DL", "price": 229.9, "lastupdated": "31/10/2026 08:00:00"},
    {"stationcode": "8", "state": "TAS", "fueltype": "DL", "price": 239.9, "lastupdated": "31/10/2026 08:00:00"},
]}


def test_medians_skip_other_fuels_typos_and_old_prices():
    m = fuelcheck.medians(SAMPLE, NOW)
    assert m == {"NSW": {"price": 2.059, "stations": 3}, "TAS": {"price": 2.349, "stations": 2}}


def test_fetch_logs_in_then_asks_for_both_states(monkeypatch):
    monkeypatch.setattr(settings, "FUELCHECK_KEY", "key")
    monkeypatch.setattr(settings, "FUELCHECK_SECRET", "secret")
    calls = []

    class Http:
        def get(self, url, headers):
            calls.append((url, headers))
            body = {"access_token": "tok"} if "accesstoken" in url else SAMPLE
            return type("R", (), {"json": lambda self: body})()

    assert fuelcheck.fetch(Http(), NOW)["TAS"]["price"] == 2.349
    (token_url, token_headers), (prices_url, headers) = calls
    assert "grant_type=client_credentials" in token_url and token_headers["Authorization"] == "Basic a2V5OnNlY3JldA=="
    assert prices_url.endswith("/FuelPriceCheck/v2/fuel/prices?states=NSW|TAS")
    assert headers["Authorization"] == "Bearer tok" and headers["apikey"] == "key" and headers["transactionid"]


def test_trip_uses_the_start_state_price(cfg):
    cfg["trip"]["live_fuel"] = {"NSW": 2.06, "TAS": 2.35}
    assert trips.fuel_price({"origin": "Sydney", "region": "AU"}, cfg) == (2.06, "FuelCheck NSW")
    assert trips.fuel_price({"origin": "Hobart", "region": "AU"}, cfg) == (2.35, "FuelCheck TAS")
    assert trips.fuel_price({"origin": "Darwin", "region": "AU"}, cfg) == (2.70, "set by hand")
    assert trips.fuel_price({"origin": "Auckland", "region": "NZ"}, cfg) == (2.85, "set by hand")
    direct = {"drive_km": 1000, "drive_hours": 12, "ferry_km": 0, "roads": []}
    t = trips.stats({"origin": "Sydney", "region": "AU", "vehicle_type": "campervan", "days_included": 5}, direct, cfg)
    assert t["fuel_cost"] == 206 and t["fuel_price_from"] == "FuelCheck NSW"   # 100 L x $2.06


def test_without_a_key_nothing_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FUELCHECK_KEY", "")
    monkeypatch.setattr(fuelcheck, "ENV_FILE", str(tmp_path / "missing.env"))
    assert not fuelcheck.configured() and fuelcheck.update(NOW) is None


def test_key_read_straight_from_app_env(monkeypatch, tmp_path):
    env = tmp_path / "app.env"
    env.write_text("DATABASE_URL=x\n# FUELCHECK_KEY=old\nFUELCHECK_KEY=abc\nFUELCHECK_SECRET=\"def\"\n")
    monkeypatch.setattr(settings, "FUELCHECK_KEY", "")
    monkeypatch.setattr(fuelcheck, "ENV_FILE", str(env))
    assert fuelcheck.credentials() == ("abc", "def") and fuelcheck.configured()
