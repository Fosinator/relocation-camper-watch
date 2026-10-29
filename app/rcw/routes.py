"""Routes and route alerts (David 2026-10-29: "start and finish locations as a drop down filter rather than ways
home", "signal alerts optional").

A route = start (or anywhere) -> finish (or anywhere), optionally only listings with the ferry or fuel included,
and either any vehicle or only matches. The Routes page shows the open listings on a route with an estimated
out-of-pocket cost. Any route can be saved as a route alert ("watch"); each new listing on it is sent to Signal
in one message per poll. A new watch starts from a baseline (what's listed already counts as seen), so saving
one never sends a backlog. "Ways home to Hobart" is just the first watch (Anywhere -> Hobart, ferry included,
any vehicle), seeded from config.yaml's home: section.

Cost = hire ($1/day x included days) + the booking fee (Imoova states it) + fuel for the route less the fuel
allowance + a ferry seat for each extra passenger when the ferry is included (the deals cover the vehicle and
driver only) and home.extra_passenger_ferry_aud is set.
"""
import logging
import re
from datetime import date

from . import db, normalise, settings, signal, trips
from .sources import SOURCES

log = logging.getLogger(__name__)


def where(route: dict, today) -> tuple[str, dict]:
    """SQL condition (and parameters) for the open listings on a route."""
    sql = ["status = 'open'", "(pickup_to IS NULL OR pickup_to >= %(today)s)"]
    params = {"today": today}
    if route.get("origin"):
        sql.append("lower(origin) = lower(%(r_origin)s)")
        params["r_origin"] = route["origin"]
    if route.get("destination"):
        sql.append("lower(destination) = lower(%(r_dest)s)")
        params["r_dest"] = route["destination"]
    if route.get("require_ferry"):
        sql.append("inclusions ILIKE '%%ferry%%'")
    if route.get("require_fuel"):
        sql.append("inclusions ILIKE '%%fuel%%'")
    if route.get("matches_only"):
        sql.append("matched")
    return " AND ".join(sql), params


def label(route: dict) -> str:
    """'Anywhere → Hobart · ferry included · any vehicle'."""
    parts = [f"{route.get('origin') or 'Anywhere'} → {route.get('destination') or 'anywhere'}"]
    if route.get("require_ferry"):
        parts.append("ferry included")
    if route.get("require_fuel"):
        parts.append("fuel included")
    parts.append("matches only" if route.get("matches_only") else "any vehicle")
    return " · ".join(parts)


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
    elif row["source"].startswith("transfercar"):
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
    if extra and "ferry" in (row.get("inclusions") or "").lower():
        seat = h.get("extra_passenger_ferry_aud")
        if seat is not None:
            parts.append((f"ferry seat{'s' if extra != 1 else ''} for {extra} more", float(seat) * extra))
        else:
            notes.append(f"plus a ferry seat for {extra} more passenger{'s' if extra != 1 else ''}")
    return {"total": round(sum(a for _, a in parts)), "parts": [(lbl, round(a)) for lbl, a in parts],
            "notes": notes}


def ferry_text(inclusions: str | None) -> str | None:
    for part in (inclusions or "").split(" · "):
        if "ferry" in part.lower():
            return part.replace("Ferry included", "").strip() or "included"
    return None


_SAILING = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})\s*(?:at\s*)?(\d{1,2}(?::\d{2})?\s*[ap]m)?", re.I)
_PORTS = re.compile(r"(geelong|melbourne|devonport)\s+to\s+(geelong|melbourne|devonport)", re.I)
_AMOUNT = re.compile(r"\$\s?(\d[\d,]*)")


def ferry_summary(text: str | None) -> str | None:
    """The operators' ferry wording in a few words: 'sailing booked Geelong → Devonport 17 Nov 6:45pm',
    'book your own, refunded up to $350', 'included for the vehicle + driver'. None without a ferry."""
    if text is None:
        return None
    low = text.lower()
    m = _SAILING.search(text)
    if m:
        try:
            when = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%-d %b")
        except ValueError:
            when = None
        if when:
            ports = _PORTS.search(text)
            leg = f" {ports.group(1).title()} → {ports.group(2).title()}" if ports else ""
            return f"sailing booked{leg} {when}" + (f" {m.group(4).replace(' ', '').lower()}" if m.group(4) else "")
    amount = _AMOUNT.search(text)
    if any(w in low for w in ("reimburs", "refund", "against receipt")):
        own = "book your own, " if "book own" in low or "book your own" in low else ""
        return f"{own}refunded up to ${amount.group(1)}" if amount else f"{own}refunded against receipts"
    if "book own" in low or "book your own" in low:
        return "book your own ferry"
    if "passenger" in low and "1 vehicle" in low:
        return "included for the vehicle + 1 passenger"
    return "included for the vehicle + driver"


def rows(c, cfg: dict, route: dict, today) -> list[dict]:
    cond, params = where(route, today)
    out = c.execute(f"SELECT * FROM relocations WHERE {cond} ORDER BY pickup_from NULLS LAST, origin, source",
                    params).fetchall()
    for r in out:
        r["cost"] = cost(r, cfg)
        r["ferry"] = ferry_text(r["inclusions"])
        r["ferry_short"] = ferry_summary(r["ferry"])
        r["fuel_allowance"] = trips.fuel_allowance(r["inclusions"])
    return out


def _same_deal(a: dict, b: dict) -> bool:
    """The same van on both sites: same route, vehicle type, berths (or unknown) and first pickup day."""
    return (a["source"] != b["source"] and (a["origin"] or "").lower() == (b["origin"] or "").lower()
            and (a["destination"] or "").lower() == (b["destination"] or "").lower()
            and a["vehicle_type"] == b["vehicle_type"] and a["pickup_from"] == b["pickup_from"]
            and (a["berths"] is None or b["berths"] is None or a["berths"] == b["berths"]))


def group(rows_: list[dict]) -> list[dict]:
    """Rows -> one entry per deal, with .also = the same van on the other site."""
    out: list[dict] = []
    for r in rows_:
        twin = next((p for p in out if _same_deal(p, r) and r["source"] not in {x["source"] for x in p["also"]}), None)
        if twin:
            twin["also"].append(r)
        else:
            out.append({**r, "also": []})
    return out


def message_lines(r: dict) -> list[str]:
    src = SOURCES[r["source"]].label if r["source"] in SOURCES else r["source"]
    vehicle = f"{r['berths']}-berth {r['vehicle_type']}" if r["berths"] else (r["vehicle_name"] or r["vehicle_type"])
    head = f"{r['origin'] or '****'} → {r['destination'] or '****'} · {vehicle} · {r['days_included'] or '****'} days"
    if r["pickup_from"]:
        head += f" · pick up {r['pickup_from']:%-d %b}"
    extras = [f"⛴ ferry {r['ferry_short']}"] if r.get("ferry_short") else []
    if r.get("fuel_allowance"):
        extras.append(f"⛽ fuel {normalise.dollars(r['fuel_allowance'], r.get('currency'))}")
    c = r["cost"]
    flag = "🇳🇿 " if r.get("region") == "NZ" else ""
    lines = [flag + head] + ([" · ".join(extras)] if extras else []) + [
        f"About {normalise.dollars(c['total'], r.get('currency'))} out of pocket"
        + (f" ({'; '.join(c['notes'])})" if c["notes"] else ""),
        f"{src}: {r['url']}"]
    lines += [f"{SOURCES[a['source']].label if a['source'] in SOURCES else a['source']}: {a['url']}"
              for a in r["also"]]
    return lines


# --- route alerts ------------------------------------------------------------------------------------------------

WATCH_FIELDS = ("origin", "destination", "require_ferry", "require_fuel", "matches_only")


def watches(c) -> list[dict]:
    return c.execute("SELECT * FROM watches ORDER BY id").fetchall()


def add_watch(c, cfg: dict, route: dict, today, now) -> int:
    """Save a route alert; everything already listed on it counts as seen (no backlog)."""
    w = {k: route.get(k) or (None if k in ("origin", "destination") else False) for k in WATCH_FIELDS}
    existing = c.execute("""SELECT id FROM watches WHERE origin IS NOT DISTINCT FROM %(origin)s
                              AND destination IS NOT DISTINCT FROM %(destination)s AND require_ferry = %(require_ferry)s
                              AND require_fuel = %(require_fuel)s AND matches_only = %(matches_only)s""", w).fetchone()
    if existing:
        c.execute("UPDATE watches SET alert = true WHERE id = %s", (existing["id"],))
        return existing["id"]
    wid = c.execute(f"""INSERT INTO watches ({', '.join(WATCH_FIELDS)}) VALUES ({', '.join(f'%({k})s' for k in WATCH_FIELDS)})
                        RETURNING id""", w).fetchone()["id"]
    cond, params = where(w, today)
    c.execute(f"""INSERT INTO watch_seen (watch_id, relocation_id, seen_at, alerted)
                  SELECT %(wid)s, id, %(now)s, false FROM relocations WHERE {cond} ON CONFLICT DO NOTHING""",
              {**params, "wid": wid, "now": now})
    return wid


def seed_home_watch(c, cfg: dict, today, now) -> None:
    """The first watch is the old 'ways home' (config.yaml home:), created once."""
    h = cfg.get("home") or {}
    if not h.get("city") or c.execute("SELECT 1 FROM watches LIMIT 1").fetchone():
        return
    wid = add_watch(c, cfg, {"destination": h["city"], "require_ferry": h.get("require_ferry", True)}, today, now)
    if not h.get("alert", True):
        c.execute("UPDATE watches SET alert = false WHERE id = %s", (wid,))


def alert_new(cfg: dict, now, today) -> int:
    """For each route alert that's switched on: one Signal message with the listings not seen before."""
    sent = 0
    with db.conn() as c:
        for w in watches(c):
            found = rows(c, cfg, w, today)
            seen = {r["relocation_id"] for r in c.execute("SELECT relocation_id FROM watch_seen WHERE watch_id = %s",
                                                          (w["id"],))}
            new = [r for r in found if r["id"] not in seen]
            if not new:
                continue
            ok = False
            if w["alert"]:
                deals = group(new)
                head = f"🔔 New on {label(w)}" + (f" ({len(deals)})" if len(deals) > 1 else "")
                text = "\n\n".join([head] + ["\n".join(message_lines(d)) for d in deals]
                                   + [f"Dashboard: {settings.DASHBOARD_URL}/routes?watch={w['id']}"])
                ok = signal.send(text, cfg)
                c.execute("INSERT INTO messages (kind, dry_run, sent, text) VALUES ('route', %s, %s, %s)",
                          (signal.is_dry_run(cfg), ok, text))
                sent += len(deals) if ok else 0
                log.info("route alert %s: %d new - %s", label(w), len(new), "sent" if ok else "SEND FAILED")
            if ok or not w["alert"]:  # a switched-off alert still marks them seen, so switching on sends no backlog
                c.cursor().executemany("""INSERT INTO watch_seen (watch_id, relocation_id, seen_at, alerted)
                                 VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                              [(w["id"], r["id"], now, ok) for r in new])
    return sent
