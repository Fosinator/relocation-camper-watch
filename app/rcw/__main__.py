"""Command line (inside the container: docker exec -w /app relocation-camper-watch /venv/bin/python -m rcw ...).

  migrate                       create / update the tables
  poll [SOURCE|all] [--dry-run] poll now; --dry-run fetches and filters but prints instead of touching the database
  digest --preview              print the would-be digest, change nothing
  digest --send                 send the digest now (honours dry-run in config.yaml / DRY_RUN=1)
  outages                       run the 24-hour outage check now
  trips                         recompute the trip stats now (fetching missing routes within the budget)
  routes                        list the route alerts and what is on each (changes nothing)
"""
import argparse
import logging
import sys

from . import config, db, digest, filters, poller, timefmt
from .http import Polite
from .sources import SOURCES


def _dry_poll(name: str, cfg: dict) -> None:
    http = Polite()
    try:
        listings = SOURCES[name].fetch(http, cfg, {})
    finally:
        http.close()
    today = timefmt.today()
    rows = [(listing, filters.evaluate(listing.row(), cfg, today)) for listing in listings]
    matched = [x for x in rows if x[1]["matched"]]
    print(f"\n{SOURCES[name].label}: {len(listings)} listings, {len(matched)} match, {http.requests} requests")
    for listing, ev in sorted(rows, key=lambda x: (not x[1]["matched"], not x[1]["boosted"])):
        mark = "⭐" if ev["matched"] and ev["boosted"] else ("✓ " if ev["matched"] else "  ")
        b = listing.berths if listing.berths is not None else "****"
        print(f"{mark} {listing.origin} → {listing.destination} | {listing.vehicle_type}{' 4WD' if listing.four_wd else ''}"
              f" {b} berths | {listing.days_included} days | {timefmt.d(listing.pickup_from)}–{timefmt.d(listing.pickup_to)}"
              f" | {listing.vehicle_name} | {ev['fail_reasons'] or ev['boost_reasons'] or ''}\n     {listing.url}")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="rcw")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    pp = sub.add_parser("poll")
    pp.add_argument("source", nargs="?", default="all", choices=["all", *SOURCES])
    pp.add_argument("--dry-run", action="store_true")
    pd = sub.add_parser("digest")
    g = pd.add_mutually_exclusive_group(required=True)
    g.add_argument("--preview", action="store_true")
    g.add_argument("--send", action="store_true")
    sub.add_parser("outages")
    sub.add_parser("trips")
    sub.add_parser("routes")
    a = p.parse_args(argv)
    cfg = config.load()

    if a.cmd == "migrate":
        from . import routes
        with db.conn() as c:
            db.migrate(c)
            routes.seed_home_watch(c, cfg, timefmt.today(), timefmt.now())
        print("schema up to date")
    elif a.cmd == "poll":
        for name in (SOURCES if a.source == "all" else [a.source]):
            if a.dry_run:
                _dry_poll(name, cfg)
            else:
                print(poller.poll(name, cfg=cfg, force=True))
    elif a.cmd == "digest":
        if a.preview:
            print(digest.preview(cfg))
        else:
            return 0 if digest.send(cfg) else 1
    elif a.cmd == "outages":
        from . import alerts
        print(f"{alerts.check_outages(cfg, timefmt.now())} outage alert(s) sent")
    elif a.cmd == "trips":
        from . import trips
        print(trips.refresh(cfg))
    elif a.cmd == "routes":
        from . import routes
        with db.conn() as c:
            for w in routes.watches(c):
                deals = routes.group(routes.rows(c, cfg, w, timefmt.today()))
                print(f"== {routes.label(w)} - alert {'on' if w['alert'] else 'off'} - {len(deals)} listed\n")
                for d in deals:
                    print("\n".join(routes.message_lines(d)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
