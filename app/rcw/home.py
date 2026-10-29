"""Ways home (David 2026-10-29: "if I could get ferry and fuel it would be a cheap way to get home from sydney").

A way home = an open relocation into home.city (Hobart) with the Bass Strait ferry included, from anywhere (or
from home.from_cities), any vehicle, whose pickup window hasn't passed. The hard filters don't apply: a hi-top
or a car is fine for getting home. They get their own section at the top of the dashboard with an estimated
cost home, and each new one is sent to Signal straight away (one message per poll).

Cost home = hire ($1/day x included days) + the booking fee (Imoova states it) + fuel for the route less the
fuel allowance + a ferry seat for each extra passenger (the deals cover the vehicle and driver only) when
home.extra_passenger_ferry_aud is set.
"""
import logging

from . import db, settings, signal, trips
from .sources import SOURCES

log = logging.getLogger(__name__)


def where(cfg: dict, today) -> tuple[str, dict]:
    """SQL condition (and its parameters) for an open way home."""
    h = cfg["home"]
    sql = ["status = 'open'", "lower(destination) = lower(%(home_city)s)", "(pickup_to IS NULL OR pickup_to >= %(today)s)"]
    params = {"home_city": h["city"], "today": today}
    if h.get("require_ferry", True):
        sql.append("inclusions ILIKE '%%ferry%%'")
    if h.get("from_cities"):
        sql.append("lower(origin) = ANY(%(home_from)s)")
        params["home_from"] = [c.lower() for c in h["from_cities"]]
    return " AND ".join(sql), params


def cost(row: dict, cfg: dict) -> dict:
    """Out-of-pocket estimate -> {'total', 'parts': [(label, amount)], 'notes': [...]}."""
    h = cfg["home"]
    item = (row.get("raw") or {}).get("item") or {}
    parts, notes = [], []
    days = row.get("days_included")
    if row["source"] == "imoova":
        rate = item.get("hire_unit_rate")
        if rate is not None and days:
            parts.append(("hire", rate / 100 * days))
        if item.get("booking_fee_amount"):
            parts.append(("booking fee", item["booking_fee_amount"] / 100))
    elif row["source"] == "transfercar":
        rate = item.get("price_per_free_day")
        if rate is not None and days:
            parts.append(("hire", float(rate) * days))
        notes.append("booking fee not shown by Transfercar")
    trip = row.get("trip") or {}
    if trip.get("fuel_cost") is not None:
        fuel = max(0, trip["fuel_cost"] - (trip.get("fuel_allowance") or 0))
        parts.append(("fuel" + (" after the allowance" if trip.get("fuel_allowance") else ""), fuel))
    else:
        notes.append("fuel not estimated yet")
    extra = max(0, int(h.get("passengers") or 1) - 1)
    if extra:
        seat = h.get("extra_passenger_ferry_aud")
        if seat is not None:
            parts.append((f"ferry seat{'s' if extra != 1 else ''} for {extra} more", float(seat) * extra))
        else:
            notes.append(f"plus a ferry seat for {extra} more passenger{'s' if extra != 1 else ''}")
    return {"total": round(sum(a for _, a in parts)), "parts": [(label, round(a)) for label, a in parts],
            "notes": notes}


def ferry_text(inclusions: str | None) -> str | None:
    for part in (inclusions or "").split(" · "):
        if "ferry" in part.lower():
            return part.replace("Ferry included", "").strip() or "included"
    return None


def open_rows(c, cfg: dict, today) -> list[dict]:
    cond, params = where(cfg, today)
    rows = c.execute(f"SELECT * FROM relocations WHERE {cond} ORDER BY pickup_from NULLS LAST, origin, source",
                     params).fetchall()
    for r in rows:
        r["home_cost"] = cost(r, cfg)
        r["ferry"] = ferry_text(r["inclusions"])
        r["fuel_allowance"] = trips.fuel_allowance(r["inclusions"])
    return rows


def _same_deal(a: dict, b: dict) -> bool:
    """The same van on both sites: same origin, vehicle type, berths (or unknown) and first pickup day."""
    return (a["source"] != b["source"] and (a["origin"] or "").lower() == (b["origin"] or "").lower()
            and a["vehicle_type"] == b["vehicle_type"] and a["pickup_from"] == b["pickup_from"]
            and (a["berths"] is None or b["berths"] is None or a["berths"] == b["berths"]))


def group(rows: list[dict]) -> list[dict]:
    """Rows -> one entry per deal, with .also = the same van on the other site."""
    out: list[dict] = []
    for r in rows:
        twin = next((p for p in out if _same_deal(p, r) and r["source"] not in {x["source"] for x in p["also"]}), None)
        if twin:
            twin["also"].append(r)
        else:
            out.append({**r, "also": []})
    return out


def message_lines(r: dict) -> list[str]:
    label = SOURCES[r["source"]].label if r["source"] in SOURCES else r["source"]
    vehicle = f"{r['berths']}-berth {r['vehicle_type']}" if r["berths"] else (r["vehicle_name"] or r["vehicle_type"])
    fuel = f" · ⛽ fuel ${r['fuel_allowance']:,.0f}" if r["fuel_allowance"] else ""
    ferry = r["ferry"] or "included"
    if len(ferry) > 90:  # some operators write a paragraph; the full text is on the dashboard
        ferry = ferry[:88].rsplit(" ", 1)[0] + "…"
    c = r["home_cost"]
    lines = [f"{r['origin'] or '****'} → {r['destination']} · {vehicle} · {r['days_included'] or '****'} days · "
             f"pick up {r['pickup_from']:%-d %b}" if r["pickup_from"] else f"{r['origin']} → {r['destination']}",
             f"⛴ ferry {ferry}{fuel}",
             f"About ${c['total']:,} out of pocket" + (f" ({'; '.join(c['notes'])})" if c["notes"] else ""),
             f"{label}: {r['url']}"]
    lines += [f"{SOURCES[a['source']].label if a['source'] in SOURCES else a['source']}: {a['url']}"
              for a in r["also"]]
    return lines


def alert_new(cfg: dict, now, today) -> int:
    """One Signal message listing the ways home not alerted yet. The first run only takes a baseline
    (everything listed at the time counts as seen), so switching this on doesn't send a backlog."""
    h = cfg["home"]
    if not h.get("alert"):
        return 0
    with db.conn() as c:
        first_run = c.execute("SELECT 1 FROM messages WHERE kind IN ('home', 'home_baseline') LIMIT 1").fetchone() is None
        rows = [r for r in open_rows(c, cfg, today) if r["home_alerted_at"] is None]
        ids = [r["id"] for r in rows]
        if first_run:
            c.execute("UPDATE relocations SET home_alerted_at = %s WHERE id = ANY(%s)", (now, ids))
            c.execute("INSERT INTO messages (kind, dry_run, sent, text) VALUES ('home_baseline', %s, false, %s)",
                      (signal.is_dry_run(cfg), f"{len(ids)} ways home already listed - not alerted"))
            log.info("ways home: baseline taken, %d already listed (no alert)", len(ids))
            return 0
        if not rows:
            return 0
        deals = group(rows)
        head = f"🏠 New way{'s' if len(deals) != 1 else ''} home to {h['city']}"
        text = "\n\n".join([head] + ["\n".join(message_lines(d)) for d in deals]
                           + [f"Dashboard: {settings.DASHBOARD_URL}"])
        ok = signal.send(text, cfg)
        c.execute("INSERT INTO messages (kind, dry_run, sent, text) VALUES ('home', %s, %s, %s)",
                  (signal.is_dry_run(cfg), ok, text))
        if ok:
            c.execute("UPDATE relocations SET home_alerted_at = %s WHERE id = ANY(%s)", (now, ids))
        log.info("ways home: %d new (%d deals) - %s", len(rows), len(deals), "sent" if ok else "SEND FAILED")
        return len(deals) if ok else 0
