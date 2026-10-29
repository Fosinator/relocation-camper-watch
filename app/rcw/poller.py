"""One poll of one source: fetch, store, mark missing listings, back off on errors.

Each source is polled on its own, so a failing site never stops the other. A failed poll never counts as
"missing", so an outage can't mark everything gone. On errors the source backs off exponentially (1, 2, 4, 8 h);
if the site refuses us (403 / 429) it backs off for a day.
"""
import logging
from datetime import timedelta

from . import alerts, config, db, routes, timefmt, trips
from .http import Blocked, Disallowed, Polite
from .sources import SOURCES, SourceError

log = logging.getLogger(__name__)


def _sane(source: str, listings: list, state: dict | None) -> None:
    """A sudden collapse in the count is far more likely a broken page than every deal vanishing at once."""
    before = (state or {}).get("last_count") or 0
    if before >= 20 and len(listings) < before * 0.25:
        raise SourceError(f"only {len(listings)} listings (last good poll had {before}) - treating as a broken page")


def poll(name: str, *, cfg: dict | None = None, src=None, http=None, force: bool = False, now=None) -> dict:
    cfg = cfg or config.load()
    src = src or SOURCES[name]
    scfg = cfg["sources"].get(name, {})
    now = now or timefmt.now()
    today = timefmt.today(now)
    result = {"source": name, "started_at": now, "ok": False}
    if not scfg.get("enabled", True):
        return {**result, "skipped": "disabled in config.yaml"}

    with db.conn() as c:
        state = db.source_state(c, name)
        if not force and state and state["next_attempt_at"] and now < state["next_attempt_at"]:
            result["skipped"] = f"backing off until {timefmt.dt(state['next_attempt_at'])}"
            db.log_poll(c, **result)
            log.info("poll %s: skipped, %s", name, result["skipped"])
            return result
        cached = db.cached_raw(c, name)

    own_http = http is None
    http = http or Polite()
    try:
        if scfg.get("simulate_outage"):
            raise SourceError("simulated outage (sources.%s.simulate_outage in config.yaml)" % name)
        listings = src.fetch(http, cfg, cached)
        _sane(name, listings, state)
    except Exception as e:  # noqa: BLE001 - any failure is recorded, never raised
        blocked = isinstance(e, (Blocked, Disallowed))
        failures = ((state or {}).get("consecutive_failures") or 0) + 1
        hours = (cfg["schedule"]["blocked_backoff_hours"] if blocked
                 else min(2 ** (failures - 1), cfg["schedule"]["backoff_max_hours"]))
        error = f"{type(e).__name__}: {e}"
        with db.conn() as c:
            db.source_failed(c, name, now, error, timedelta(hours=hours) - timedelta(minutes=5))
            db.log_poll(c, **result, error=error, requests=getattr(http, "requests", None))
        log.warning("poll %s: FAILED (%s) - next try in about %s h", name, error, hours)
        alerts.check_outages(cfg, now)
        return {**result, "error": error}
    finally:
        if own_http:
            http.close()

    with db.conn() as c:
        stats = db.store(c, name, listings, cfg, now, today)
        db.reevaluate_open(c, cfg, now, today)
        db.link_duplicates(c)
        db.source_ok(c, name, now, len(listings))
    try:
        trips.refresh(cfg, today=today)
    except Exception:  # noqa: BLE001 - trip stats are extra; a routing problem must not fail the poll
        log.exception("trip stats failed")
    sent = alerts.instant(cfg, now)
    try:
        sent += routes.alert_new(cfg, now, today)
    except Exception:  # noqa: BLE001
        log.exception("route alerts failed")
    result.update(ok=True, listings_found=len(listings), new_listings=stats["new"], new_matches=stats["new_matches"],
                  gone=stats["gone"], alerts_sent=sent, requests=getattr(http, "requests", None))
    with db.conn() as c:
        db.log_poll(c, **result)
    log.info("poll %s: %d listings, %d new, %d new matches, %d gone, %d alerts sent, %s requests",
             name, len(listings), stats["new"], stats["new_matches"], stats["gone"], sent, result["requests"])
    return result
