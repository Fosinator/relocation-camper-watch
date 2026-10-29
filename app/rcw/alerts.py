"""The only Signal messages besides the daily digest.

- Outage (David 2026-10-29): "if polling fails for a full 24hr send a signal alert" - one message per outage,
  reset by the next good poll.
- Instant alerts for starred deals: built but OFF (alerts.instant_starred) while gathering data for 9-12 months.
"""
import logging
from datetime import timedelta

from . import db, settings, signal, timefmt
from .sources import SOURCES

log = logging.getLogger(__name__)


def _record(c, kind: str, cfg: dict, sent: bool, text: str) -> None:
    c.execute("INSERT INTO messages (kind, dry_run, sent, text) VALUES (%s, %s, %s, %s)",
              (kind, signal.is_dry_run(cfg), sent, text))


def check_outages(cfg: dict, now) -> int:
    limit = timedelta(hours=float(cfg["alerts"]["outage_hours"]))
    sent = 0
    with db.conn() as c:
        for name, st in db.all_source_states(c).items():
            if not cfg["sources"].get(name, {}).get("enabled", True):
                continue
            since = st["failing_since"]
            if not since or st["outage_alerted_at"] or now - since < limit:
                continue
            label = SOURCES[name].label if name in SOURCES else name
            last_good = timefmt.dt(st["last_success"]) if st["last_success"] else "never"
            text = (f"⚠ Relocation Camper Watch: {label} has had no successful poll for "
                    f"{timefmt.span((now - since).total_seconds())} (last good poll: {last_good}).\n"
                    f"Last error: {st['last_error']}\nDashboard: {settings.DASHBOARD_URL}")
            ok = signal.send(text, cfg)
            _record(c, "outage", cfg, ok, text)
            if ok:
                c.execute("UPDATE source_state SET outage_alerted_at = %s WHERE source = %s", (now, name))
                sent += 1
    return sent


def instant(cfg: dict, now) -> int:
    """One message per new starred match, with its booking link(s). Off unless alerts.instant_starred."""
    if not cfg["alerts"].get("instant_starred"):
        return 0
    from .digest import item_lines  # same wording as the digest

    sent = 0
    with db.conn() as c:
        rows = c.execute("""SELECT * FROM relocations WHERE status = 'open' AND matched AND boosted
                                AND alerted_at IS NULL AND dup_of IS NULL AND first_seen > %s
                            ORDER BY first_seen""", (now - timedelta(days=1),)).fetchall()
        for r in rows:
            dups = c.execute("SELECT * FROM relocations WHERE dup_of = %s", (r["id"],)).fetchall()
            text = "\n".join(["Relocation Camper Watch – new starred deal"] + item_lines(r, dups))
            ok = signal.send(text, cfg)
            _record(c, "instant", cfg, ok, text)
            if ok:
                c.execute("UPDATE relocations SET alerted_at = %s WHERE id = ANY(%s)",
                          (now, [r["id"]] + [x["id"] for x in dups]))
                sent += 1
    return sent
