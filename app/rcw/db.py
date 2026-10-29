"""Postgres: the relocation_watch database on the v2 server (exp-v2-db, reached over v2-net).

One connection per unit of work: volumes are tiny (a few hundred rows an hour).
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import filters, settings

LISTING_FIELDS = ["url", "vehicle_name", "vehicle_type", "four_wd", "berths", "origin", "destination", "pickup_from",
                  "pickup_to", "days_included", "extra_days", "extra_day_price", "inclusions", "bond", "operator",
                  "description", "km_allowed", "km_per_extra_day", "excess_km_rate"]
EVAL_FIELDS = ["matched", "boosted", "fail_reasons", "boost_reasons"]


def conn(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or settings.DATABASE_URL, row_factory=dict_row, connect_timeout=10)


def migrate(c) -> None:
    c.execute((Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8"))


# --- listings ---------------------------------------------------------------------------------------------------

def cached_raw(c, source: str) -> dict[str, dict]:
    """{listing_id: raw} for this source's open listings, so adapters can reuse details already fetched."""
    rows = c.execute("SELECT listing_id, raw FROM relocations WHERE source = %s AND status = 'open'", (source,))
    return {r["listing_id"]: r["raw"] for r in rows}


_UPSERT = f"""
INSERT INTO relocations (source, listing_id, {", ".join(LISTING_FIELDS + EVAL_FIELDS)}, first_seen, last_seen,
                         matched_at, raw)
VALUES (%(source)s, %(listing_id)s, {", ".join(f"%({k})s" for k in LISTING_FIELDS + EVAL_FIELDS)}, %(now)s, %(now)s,
        CASE WHEN %(matched)s THEN %(now)s::timestamptz END, %(raw)s)
ON CONFLICT (source, listing_id) DO UPDATE SET
    {", ".join(f"{k} = EXCLUDED.{k}" for k in LISTING_FIELDS + EVAL_FIELDS)},
    last_seen = EXCLUDED.last_seen, missed_polls = 0, status = 'open', gone_at = NULL,
    matched_at = COALESCE(relocations.matched_at, EXCLUDED.matched_at), raw = EXCLUDED.raw
RETURNING (xmax = 0) AS inserted, matched_at IS NOT DISTINCT FROM %(now)s AS newly_matched
"""


def store(c, source: str, listings: list, cfg: dict, now: datetime, today: date) -> dict:
    """Upsert one successful poll's listings, then count missing ones -> {'new', 'new_matches', 'gone'}."""
    new = new_matches = 0
    for listing in listings:
        row = listing.row()
        row.update(filters.evaluate(row, cfg, today), now=now, raw=Jsonb(row["raw"]))
        r = c.execute(_UPSERT, row).fetchone()
        new += r["inserted"]
        new_matches += r["newly_matched"]
    gone_rows = c.execute(
        """UPDATE relocations SET missed_polls = missed_polls + 1,
               status = CASE WHEN missed_polls + 1 >= %(n)s THEN 'gone' ELSE status END,
               gone_at = CASE WHEN missed_polls + 1 >= %(n)s THEN %(now)s ELSE gone_at END
           WHERE source = %(s)s AND status = 'open' AND NOT (listing_id = ANY(%(seen)s))
           RETURNING status""",
        {"n": int(cfg["schedule"]["gone_after_missed_polls"]), "now": now, "s": source,
         "seen": [listing.listing_id for listing in listings]}).fetchall()
    return {"new": new, "new_matches": new_matches, "gone": sum(r["status"] == "gone" for r in gone_rows)}


def reevaluate_open(c, cfg: dict, now: datetime, today: date) -> int:
    """Re-check every open listing against the current filters (config edits, and the pickup window moving
    with the date). -> how many changed."""
    changed = 0
    for row in c.execute("SELECT * FROM relocations WHERE status = 'open'").fetchall():
        ev = filters.evaluate(row, cfg, today)
        if any(row[k] != ev[k] for k in EVAL_FIELDS):
            c.execute(f"""UPDATE relocations SET {", ".join(f"{k} = %({k})s" for k in EVAL_FIELDS)},
                              matched_at = COALESCE(matched_at, CASE WHEN %(matched)s THEN %(now)s::timestamptz END)
                          WHERE id = %(id)s""", {**ev, "now": now, "id": row["id"]})
            changed += 1
    return changed


def _overlap(a: dict, b: dict) -> bool:
    lo = date.min
    hi = date.max
    return (a["pickup_from"] or lo) <= (b["pickup_to"] or hi) and (b["pickup_from"] or lo) <= (a["pickup_to"] or hi)


def link_duplicates(c) -> None:
    """The same deal on both sites (David: "log both and send 1 signal message with 2 URLs"). A loose match:
    same route, vehicle type and berths (a missing value matches anything) and overlapping pickup windows."""
    rows = c.execute("""SELECT id, source, origin, destination, vehicle_type, berths, pickup_from, pickup_to, dup_of
                        FROM relocations WHERE status = 'open' AND matched ORDER BY first_seen, id""").fetchall()
    primaries: list[dict] = []
    want: dict[int, int | None] = {}
    for r in rows:
        key = ((r["origin"] or "").lower(), (r["destination"] or "").lower(), r["vehicle_type"])
        match = None
        for p in primaries:
            if (p["key"] == key and p["row"]["source"] != r["source"] and r["source"] not in p["linked"]
                    and (p["row"]["berths"] is None or r["berths"] is None or p["row"]["berths"] == r["berths"])
                    and _overlap(p["row"], r)):
                match = p
                break
        if match:
            match["linked"].add(r["source"])
            want[r["id"]] = match["row"]["id"]
        else:
            primaries.append({"key": key, "row": r, "linked": set()})
            want[r["id"]] = None
    # only open matches are re-linked; gone ones keep their last link so History still shows both sites
    for r in rows:
        if r["dup_of"] != want[r["id"]]:
            c.execute("UPDATE relocations SET dup_of = %s WHERE id = %s", (want[r["id"]], r["id"]))


# --- sources and polls ------------------------------------------------------------------------------------------

def source_state(c, source: str) -> dict | None:
    return c.execute("SELECT * FROM source_state WHERE source = %s", (source,)).fetchone()


def all_source_states(c) -> dict[str, dict]:
    return {r["source"]: r for r in c.execute("SELECT * FROM source_state")}


def source_ok(c, source: str, now: datetime, count: int) -> None:
    c.execute("""INSERT INTO source_state (source, last_success, last_count) VALUES (%(s)s, %(now)s, %(n)s)
                 ON CONFLICT (source) DO UPDATE SET last_success = %(now)s, last_count = %(n)s, failing_since = NULL,
                     consecutive_failures = 0, next_attempt_at = NULL, outage_alerted_at = NULL""",
              {"s": source, "now": now, "n": count})


def source_failed(c, source: str, now: datetime, error: str, backoff: timedelta) -> dict:
    return c.execute(
        """INSERT INTO source_state (source, last_failure, failing_since, consecutive_failures, next_attempt_at,
                                     last_error)
           VALUES (%(s)s, %(now)s, %(now)s, 1, %(next)s, %(err)s)
           ON CONFLICT (source) DO UPDATE SET last_failure = %(now)s,
               failing_since = COALESCE(source_state.failing_since, %(now)s),
               consecutive_failures = source_state.consecutive_failures + 1, next_attempt_at = %(next)s,
               last_error = %(err)s
           RETURNING *""", {"s": source, "now": now, "next": now + backoff, "err": error[:500]}).fetchone()


def log_poll(c, **kw) -> None:
    cols = ["source", "started_at", "ok", "skipped", "listings_found", "new_listings", "new_matches", "gone",
            "alerts_sent", "requests", "error"]
    c.execute(f"INSERT INTO polls ({', '.join(cols)}) VALUES ({', '.join(f'%({k})s' for k in cols)})",
              {k: kw.get(k) for k in cols})


def last_poll_at(c, source: str) -> datetime | None:
    r = c.execute("SELECT max(started_at) AS t FROM polls WHERE source = %s AND skipped IS NULL", (source,)).fetchone()
    return r["t"]


def db_size_mb(c) -> float:
    return c.execute("SELECT pg_database_size(current_database()) / 1048576.0 AS mb").fetchone()["mb"]
