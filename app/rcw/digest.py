"""The daily Signal digest, 4 pm Hobart (brief 2026-10-29).

Sent on quiet days too ("0 new"), so silence never means a broken scraper. Source failures go in the digest
rather than separate warnings (the one exception is a full-day outage, see alerts.py). A deal on both sites is
listed once with both links. The database size shows when it passes alerts.db_size_warn_mb.
"""
import logging
from datetime import datetime, timedelta, timezone

from . import db, settings, signal, timefmt
from .sources import SOURCES

log = logging.getLogger(__name__)

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _money(v) -> str:
    return f"${float(v):,.0f}"


def item_lines(r: dict, dups: list[dict] = ()) -> list[str]:
    star = "⭐ " if r["boosted"] else ""
    vehicle = r["vehicle_type"] if r["vehicle_type"] != "other" else (r["vehicle_name"] or "vehicle")
    if r["four_wd"]:
        vehicle = "4WD " + vehicle
    berths = f"{r['berths']} berths" if r["berths"] is not None else "**** berths"
    days = f"{r['days_included']} days" if r["days_included"] is not None else "**** days"
    if r["extra_days"]:
        days += f" (+{r['extra_days']})"
    parts = [f"{star}{r['origin'] or '****'} → {r['destination'] or '****'}", f"{vehicle}, {berths}", days,
             f"from {timefmt.d(r['pickup_from'])}"]
    if r["inclusions"]:
        parts.append(r["inclusions"])
    if r["status"] == "gone":
        parts.append("already gone")
    lines = [" · ".join(parts), f"   {r['url']}"]
    lines += [f"   also on {SOURCES[d['source']].label if d['source'] in SOURCES else d['source']}: {d['url']}"
              for d in dups]
    return lines


def format_digest(day, new: list[dict], dups: dict[int, list[dict]], gone: int, problems: list[str],
                  db_mb: float | None, cfg: dict, dashboard: str) -> str:
    """The message text (pure, so it can be tested and previewed)."""
    starred = sum(1 for r in new if r["boosted"])
    lines = [f"Relocations – {day:%a} {day.day} {day:%b}",
             f"{len(new)} new match{'es' if len(new) != 1 else ''}" + (f" ({starred} ⭐)" if starred else "")
             + f" · {gone} gone"]
    limit = int(cfg["alerts"]["digest_max_listed"])
    if new:
        lines.append("")
        for r in new[:limit]:
            lines += item_lines(r, dups.get(r["id"], []))
        if len(new) > limit:
            lines.append(f"+ {len(new) - limit} more on the dashboard")
    if problems:
        lines += [""] + [f"⚠ {p}" for p in problems]
    if db_mb is not None and db_mb > float(cfg["alerts"]["db_size_warn_mb"]):
        lines += ["", f"⚠ Database is {db_mb:,.0f} MB (warning level {cfg['alerts']['db_size_warn_mb']} MB)"]
    lines += ["", f"Dashboard: {dashboard}"]
    return "\n".join(lines)


def _problems(c, since) -> list[str]:
    out = []
    for name, src in SOURCES.items():
        r = c.execute("""SELECT count(*) FILTER (WHERE ok) AS good, count(*) FILTER (WHERE NOT ok AND skipped IS NULL)
                                AS bad, count(*) FILTER (WHERE skipped IS NOT NULL) AS skipped
                         FROM polls WHERE source = %s AND started_at > %s""", (name, since)).fetchone()
        st = db.source_state(c, name) or {}
        if r["bad"] or r["skipped"] or st.get("failing_since"):
            msg = f"{src.label}: {r['bad']} failed poll{'s' if r['bad'] != 1 else ''}"
            if r["skipped"]:
                msg += f", {r['skipped']} skipped while backing off"
            msg += f", {r['good']} good"
            if st.get("failing_since"):
                msg += f" – failing since {timefmt.dt(st['failing_since'])}"
            if st.get("last_error"):
                msg += f" ({st['last_error'][:120]})"
            out.append(msg)
        elif not r["good"] and not st.get("last_success"):
            out.append(f"{src.label}: no polls yet")
    return out


def build(c, cfg: dict, now) -> dict:
    last = c.execute("SELECT max(created_at) AS t FROM digests WHERE sent OR dry_run").fetchone()["t"] or EPOCH
    new = c.execute("""SELECT * FROM relocations WHERE matched AND digested_at IS NULL AND dup_of IS NULL
                       ORDER BY boosted DESC, pickup_from NULLS LAST, first_seen""").fetchall()
    ids = [r["id"] for r in new]
    dups: dict[int, list[dict]] = {}
    for d in c.execute("SELECT * FROM relocations WHERE dup_of = ANY(%s) ORDER BY source", (ids,)).fetchall():
        dups.setdefault(d["dup_of"], []).append(d)
    gone = c.execute("""SELECT count(*) AS n FROM relocations WHERE matched AND dup_of IS NULL AND gone_at > %s""",
                     (last,)).fetchone()["n"]
    since = last if last > EPOCH else now - timedelta(days=1)
    text = format_digest(timefmt.today(now), new, dups, gone, _problems(c, since), db.db_size_mb(c), cfg,
                         settings.DASHBOARD_URL)
    return {"text": text, "ids": ids + [d["id"] for v in dups.values() for d in v], "new": len(new),
            "starred": sum(1 for r in new if r["boosted"]), "gone": gone}


def preview(cfg: dict, now=None) -> str:
    """The would-be digest, changing nothing."""
    now = now or timefmt.now()
    with db.conn() as c:
        db.reevaluate_open(c, cfg, now, timefmt.today(now))
        return build(c, cfg, now)["text"]


def send(cfg: dict, now=None) -> bool:
    now = now or timefmt.now()
    with db.conn() as c:
        db.reevaluate_open(c, cfg, now, timefmt.today(now))
        db.link_duplicates(c)
        d = build(c, cfg, now)
    ok = signal.send(d["text"], cfg)
    with db.conn() as c:
        if ok:
            c.execute("UPDATE relocations SET digested_at = %s WHERE id = ANY(%s)", (now, d["ids"]))
        c.execute("""INSERT INTO digests (for_date, created_at, dry_run, sent, new_matches, starred, gone, text)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                  (timefmt.today(now), now, signal.is_dry_run(cfg), ok and not signal.is_dry_run(cfg), d["new"],
                   d["starred"], d["gone"], d["text"]))
    log.info("digest: %d new matches (%d starred), %d gone - %s", d["new"], d["starred"], d["gone"],
             "dry run" if signal.is_dry_run(cfg) else ("sent" if ok else "SEND FAILED"))
    return ok


def due(cfg: dict, now) -> bool:
    """Digest time has passed today (until 23:00) and none has gone out yet today."""
    loc = timefmt.local(now)
    hh, mm = map(int, cfg["schedule"]["digest_time"].split(":"))
    if (loc.hour, loc.minute) < (hh, mm) or loc.hour >= 23:
        return False
    with db.conn() as c:
        r = c.execute("SELECT 1 FROM digests WHERE for_date = %s AND (sent OR dry_run)", (loc.date(),)).fetchone()
    return r is None
