"""On/off switches set on the Alerts page (David 2026-10-29: "signal alerts optional"). They're kept in the
database and override the same settings in config.yaml, so they survive restarts and deploys.
"""
import logging
import time

from psycopg.types.json import Jsonb

from . import db, settings

log = logging.getLogger(__name__)

# switch -> (config section, key, label on the Alerts page)
SWITCHES = {
    # the master switch (David 2026-10-31: "I need to be able to switch off the signal messages")
    "signal": ("alerts", "signal", "All Signal messages"),
    "digest": ("alerts", "digest", "Daily digest at the digest time"),
    "outage": ("alerts", "outage", "Alert when a site has had no good poll for a day"),
    "instant_starred": ("alerts", "instant_starred", "Instant alert for each new starred match"),
}
_cache = {"at": 0.0, "values": {}}
TTL = 10


def load(force: bool = False) -> dict:
    if not settings.DATABASE_URL:
        return {}
    if force or time.monotonic() - _cache["at"] > TTL:
        try:
            with db.conn() as c:
                _cache["values"] = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM prefs")}
        except Exception as e:  # noqa: BLE001 - e.g. before the first migration: config.yaml alone applies
            log.debug("prefs unavailable: %s", e)
            _cache["values"] = {}
        _cache["at"] = time.monotonic()
    return _cache["values"]


def apply(cfg: dict, values: dict | None = None) -> dict:
    for key, value in (load() if values is None else values).items():
        if key in SWITCHES:
            section, name, _ = SWITCHES[key]
            cfg[section][name] = bool(value)
    return cfg


def set_switch(key: str, on: bool) -> None:
    if key not in SWITCHES:
        raise KeyError(key)
    with db.conn() as c:
        c.execute("""INSERT INTO prefs (key, value) VALUES (%s, %s)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (key, Jsonb(on)))
    load(force=True)
