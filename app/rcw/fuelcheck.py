"""Live diesel prices for NSW and Tasmania from the NSW Government's FuelCheck API (David 2026-10-31,
https://api.nsw.gov.au/Product/Index/22).

Needs David's own free API key and secret in app.env (FUELCHECK_KEY / FUELCHECK_SECRET); without them nothing
runs and the hand-set prices in config.yaml are used. Once a day: an OAuth token (Basic key:secret), then every
current price for NSW and Tasmania (v2 /fuel/prices?states=NSW|TAS); the median diesel (DL) price per state is
stored in fuel_prices. About 60 calls a month against the free tier's 2,500.
A trip starting in NSW or Tasmania then uses that state's price; everywhere else keeps the hand-set price.
"""
import base64
import logging
import statistics
import uuid
from datetime import datetime, timedelta, timezone

from . import db, settings, timefmt
from .http import Polite

log = logging.getLogger(__name__)

API = "https://api.onegov.nsw.gov.au"
TOKEN_URL = API + "/oauth/client_credential/accesstoken?grant_type=client_credentials"
PRICES_URL = API + "/FuelPriceCheck/v2/fuel/prices?states=NSW|TAS"
FUEL_TYPE = "DL"                      # diesel
MAX_PRICE_AGE = timedelta(days=14)    # ignore stations that haven't updated their price for a fortnight
USE_FOR = timedelta(days=3)           # a fetched price is used for trips for this long


ENV_FILE = "/config/app.env"


def credentials() -> tuple[str, str]:
    """The key and secret from the environment, or straight from app.env: Docker only reads --env-file when the
    container is created, so this lets David add the key without recreating the container."""
    key, secret = settings.FUELCHECK_KEY, settings.FUELCHECK_SECRET
    if key and secret:
        return key, secret
    found = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                name, sep, value = line.strip().partition("=")
                if sep and name in ("FUELCHECK_KEY", "FUELCHECK_SECRET"):
                    found[name] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found.get("FUELCHECK_KEY", ""), found.get("FUELCHECK_SECRET", "")


def configured() -> bool:
    return all(credentials())


def medians(data: dict, now: datetime) -> dict[str, dict]:
    """The API's prices -> {state: {'price': AUD per litre, 'stations': n}} for current diesel prices."""
    by_state: dict[str, list[float]] = {}
    for p in data.get("prices") or []:
        if p.get("fueltype") != FUEL_TYPE or p.get("price") is None:
            continue
        cents = float(p["price"])
        if not 100 <= cents <= 500:   # typos happen: 1.99 or 1999 instead of 199.9
            continue
        try:
            updated = datetime.strptime(p.get("lastupdated") or "", "%d/%m/%Y %H:%M:%S").replace(tzinfo=timefmt.TZ)
        except ValueError:
            updated = None
        if updated and now - updated > MAX_PRICE_AGE:
            continue
        by_state.setdefault((p.get("state") or "NSW").upper(), []).append(cents)
    return {s: {"price": round(statistics.median(v) / 100, 3), "stations": len(v)} for s, v in by_state.items() if v}


def fetch(http=None, now: datetime | None = None) -> dict[str, dict]:
    now = now or timefmt.now()
    own = http is None
    http = http or Polite(delay=1.0)
    try:
        key, secret = credentials()
        basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
        token = http.get(TOKEN_URL, headers={"Authorization": f"Basic {basic}"}).json()["access_token"]
        data = http.get(PRICES_URL, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8",
            "apikey": key, "transactionid": str(uuid.uuid4()),
            "requesttimestamp": datetime.now(timezone.utc).strftime("%d/%m/%Y %I:%M:%S %p")}).json()
    finally:
        if own:
            http.close()
    return medians(data, now)


def update(now: datetime | None = None, http=None, force: bool = False) -> dict | None:
    """Fetch and store today's medians, once a day (or now with force). -> what was stored, or None."""
    now = now or timefmt.now()
    if not configured():
        return None
    with db.conn() as c:
        last = c.execute("SELECT max(fetched_at) AS t FROM fuel_prices WHERE source = 'FuelCheck'").fetchone()["t"]
    if not force and last and timefmt.today(last) == timefmt.today(now):
        return None
    try:
        found = fetch(http, now)
    except Exception as e:  # noqa: BLE001 - the hand-set prices carry on
        log.warning("FuelCheck failed: %s", e)
        return None
    with db.conn() as c:
        for state, v in found.items():
            c.execute("""INSERT INTO fuel_prices (state, price, stations, source, fetched_at)
                         VALUES (%s, %s, %s, 'FuelCheck', %s)""", (state, v["price"], v["stations"], now))
    log.info("FuelCheck diesel: %s", ", ".join(f"{s} A${v['price']:.2f} ({v['stations']} stations)"
                                             for s, v in sorted(found.items())))
    return found


def latest(c, now: datetime) -> dict[str, dict]:
    """{state: {'price', 'stations', 'fetched_at'}} for prices fetched recently enough to use."""
    rows = c.execute("""SELECT DISTINCT ON (state) state, price, stations, fetched_at FROM fuel_prices
                        WHERE source = 'FuelCheck' AND fetched_at > %s ORDER BY state, fetched_at DESC""",
                     (now - USE_FOR,)).fetchall()
    return {r["state"]: {"price": float(r["price"]), "stations": r["stations"], "fetched_at": r["fetched_at"]}
            for r in rows}
