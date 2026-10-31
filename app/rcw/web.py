"""The dashboard (http://10.1.1.200, LAN only, no login).

Current: open matches with From/To dropdowns, onward legs for chaining, and missed opportunities from the last two
days. Routes: any start -> finish (any vehicle), with the cost and a button to save it as a route alert.
History: matches that went more than two days ago (kept forever). Stats: the brief's numbers.
Alerts: every Signal message can be switched on or off (David 2026-10-29: "signal alerts optional").
"""
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import chain, config, db, normalise, prefs, routes, scheduler, settings, signal, stats, timefmt, trips
from .sources import SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

HERE = Path(__file__).parent
VERSION_FILE = HERE.parent / "VERSION"
VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "dev"


@asynccontextmanager
async def lifespan(app):
    if settings.SCHEDULER and settings.DATABASE_URL:
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(dt=timefmt.dt, d=timefmt.d, ago=timefmt.ago, span=timefmt.span,
                             money=normalise.dollars,
                             km=lambda v: f"{float(v):,.0f} km" if v is not None else "****",
                             stars=lambda v: "****" if v is None else v)
templates.env.globals.update(version=VERSION, source_label=lambda s: SOURCES[s].label if s in SOURCES else s,
                             trip_badge=trips.badge, today=timefmt.today,
                             qs=lambda base, **kw: "?" + urlencode({k: v for k, v in {**base, **kw}.items()
                                                                    if v not in (None, "")}))
SORTS = {"starred": "Starred first", "cost": "Cheapest", "soonest": "Soonest pickup", "newest": "Newest",
         "days": "Most days", "explore": "Most room to explore", "easy": "Least driving a day",
         "fuel": "Biggest fuel allowance"}
_SORT_KEYS = {
    "starred": lambda r: (not r["boosted"], r["pickup_from"] or date.max),
    "cost": lambda r: r["cost"]["total"] if r.get("cost") else 10 ** 9,
    "soonest": lambda r: r["pickup_from"] or date.max,
    "newest": lambda r: -r["first_seen"].timestamp(),
    "days": lambda r: -((r["days_included"] or 0) + (r["extra_days"] or 0)),
    "explore": lambda r: -((r["trip"] or {}).get("explore_km", -1)),
    "easy": lambda r: (r["trip"] or {}).get("hours_per_day", 99),
    "fuel": lambda r: -(trips.fuel_allowance(r["inclusions"]) or 0),
}


def sort_rows(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=_SORT_KEYS.get(key, _SORT_KEYS["starred"]))


def _flag(v) -> bool:
    return str(v).lower() in ("1", "true", "on", "yes")


def _sources(c, cfg) -> list[dict]:
    states = db.all_source_states(c)
    out = []
    for name, src in SOURCES.items():
        st = states.get(name) or {}
        scfg = cfg["sources"].get(name, {})
        out.append({"name": name, "label": src.label, "enabled": scfg.get("enabled", True),
                    "simulated": scfg.get("simulate_outage", False), "last_success": st.get("last_success"),
                    "last_count": st.get("last_count"), "failing_since": st.get("failing_since"),
                    "last_error": st.get("last_error"), "next_attempt_at": st.get("next_attempt_at"),
                    "ok": bool(st.get("last_success")) and not st.get("failing_since")})
    return out


def _group(rows: list[dict]) -> list[dict]:
    """Rows -> primaries, each with .dups (the same deal on the other site)."""
    by_id = {r["id"]: {**r, "dups": []} for r in rows}
    out = []
    for r in by_id.values():
        if r["dup_of"] and r["dup_of"] in by_id:
            by_id[r["dup_of"]]["dups"].append(r)
        else:
            out.append(r)
    return out


def _page(request: Request, template: str, cfg: dict, c, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, template, {
        "cfg": cfg, "sources": _sources(c, cfg), "config_error": config.error(),
        "dry_run": settings.DRY_RUN or bool(cfg["alerts"].get("dry_run")), "signal_paused": signal.paused(cfg),
        "here": request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        "fuel": trips.fuel_status(cfg, timefmt.today()),
        "next_poll": scheduler.next_poll(), "dashboard": settings.DASHBOARD_URL, **ctx})


@app.get("/", response_class=HTMLResponse)
def current(request: Request, fourwd: str = "", all: int = 0, sort: str = "starred",
            start: str = Query("", alias="from"), to: str = "", after: str = "", region: str = ""):
    cfg = config.load()
    region = region.upper() if region.upper() in chain.REGION_NAMES else ""
    view = fourwd if fourwd in config.FOUR_WD_MODES else "include"
    sort = sort if sort in SORTS else "starred"
    keep_days = int(cfg["schedule"]["history_after_days"])
    with db.conn() as c:
        all_matches = _group(c.execute("""SELECT * FROM relocations WHERE status = 'open' AND matched
                                          ORDER BY boosted DESC, pickup_from NULLS LAST, first_seen DESC""").fetchall())
        all_matches = [r for r in all_matches if not region or r["region"] == region]
        # daisy-chain: after=<id>[,<id>...] is the chain so far; the legs shown start where its last leg ends,
        # when you'd be there. Every card can extend the chain.
        trail_ids = [int(x) for x in after.split(",") if x.strip().isdigit()][:10]
        by_id = {r["id"]: r for r in c.execute("SELECT * FROM relocations WHERE id = ANY(%s)", (trail_ids,))}
        trail = [by_id[i] for i in trail_ids if i in by_id]
        for leg in trail:
            leg["cost"] = routes.cost(leg, cfg)
        first = trail[-1] if trail else None
        if first:
            start, window = first["destination"] or "", chain.arrival_window(first, cfg)
        else:
            window = None
        base = {"fourwd": view, "sort": sort, "region": region or None}
        chain_ids = [leg["id"] for leg in trail]
        for r in all_matches:
            r["onward"] = len(chain.onward(r, all_matches, cfg))
            r["leaving"] = sum(1 for x in all_matches if x["id"] != r["id"] and r["destination"]
                               and (x["origin"] or "").lower() == r["destination"].lower())
            r["onward_href"] = "/?" + urlencode({k: v for k, v in {
                **base, "after": ",".join(str(i) for i in chain_ids + [r["id"]])}.items() if v is not None})
            r["leaving_href"] = "/?" + urlencode({k: v for k, v in {**base, "from": r["destination"]}.items()
                                                   if v is not None})
            r["cost"] = routes.cost(r, cfg)
        from_places = chain.places_by_region(all_matches, "origin")
        to_places = chain.places_by_region(all_matches, "destination")

        def place_ok(r):
            return ((not region or r["region"] == region) and (not start or (r["origin"] or "").lower() == start.lower())
                    and (not to or (r["destination"] or "").lower() == to.lower()))
        matches = [r for r in all_matches if place_ok(r) and (not first or chain.fits_after(first, r, cfg))]
        missed = _group(c.execute("""SELECT * FROM relocations WHERE status = 'gone' AND matched AND gone_at > %s
                                     ORDER BY gone_at DESC""",
                                  (timefmt.now() - timedelta(days=keep_days),)).fetchall())
        others = c.execute("""SELECT * FROM relocations WHERE status = 'open' AND NOT matched
                              ORDER BY vehicle_type, origin, destination""").fetchall() if all else []
        open_total = c.execute("SELECT count(*) AS n FROM relocations WHERE status = 'open'").fetchone()["n"]

        def view_ok(r):
            return view == "include" or (view == "only") == bool(r["four_wd"])
        matches = sort_rows([r for r in matches if view_ok(r)], sort)
        missed = [r for r in missed if view_ok(r) and place_ok(r)]
        others = [r for r in others if place_ok(r)]
        keep = {"fourwd": view, "sort": sort, "all": 1 if all else None, "from": start or None, "to": to or None,
                "after": ",".join(str(i) for i in chain_ids) or None, "region": region or None}
        back_href = "/?" + urlencode({k: v for k, v in {**base, "after": ",".join(str(i) for i in chain_ids[:-1])
                                                         or None}.items() if v is not None})
        totals = {"days": sum((leg["days_included"] or 0) for leg in trail),
                  "costs": {}}  # per currency: a chain can't mix AUD and NZD sensibly, but show both if it does
        for leg in trail:
            cur = leg.get("currency") or "AUD"
            totals["costs"][cur] = totals["costs"].get(cur, 0) + leg["cost"]["total"]
        return _page(request, "current.html", cfg, c, page="current", matches=matches, missed=missed,
                     others=others, show_all=bool(all), fourwd=view, open_total=open_total, keep_days=keep_days,
                     sort=sort, sorts=SORTS, start=start, to=to, from_places=from_places,
                     to_places=to_places, first=first, window=window, keep=keep, region=region, trail=trail,
                     back_href=back_href, totals=totals,
                     region_links=_region_links(cfg, view, sort, all), filtered=bool(start or to or first))


def _region_links(cfg: dict, view: str, sort: str, show_all) -> list[tuple[str, str, str]]:
    """[(code, label, href)] for the Region chips; switching region clears From/To."""
    base = {"fourwd": view, "sort": sort, "all": 1 if show_all else None}
    links = [("", "All", "/?" + urlencode({k: v for k, v in base.items() if v is not None}))]
    for code in cfg["regions"]:
        flag = {"AU": "🇦🇺", "NZ": "🇳🇿"}.get(code, "")
        links.append((code, f"{flag} {chain.REGION_NAMES.get(code, code)}",
                      "/?" + urlencode({k: v for k, v in {**base, "region": code}.items() if v is not None})))
    return links


ROUTE_SORTS = {k: SORTS[k] for k in ("cost", "soonest", "newest", "days", "easy", "explore", "fuel")}


@app.get("/routes", response_class=HTMLResponse)
def routes_page(request: Request, start: str = Query("", alias="from"), to: str = "", ferry: str = "",
                fuel: str = "", matches: str = "", sort: str = "cost", watch: int | None = None):
    """Start and finish dropdowns over every open listing (any vehicle unless 'matches only'). With no query at
    all it opens on the first route alert (Anywhere -> Hobart with the ferry, the old 'ways home')."""
    cfg = config.load()
    sort = sort if sort in ROUTE_SORTS else "cost"
    today = timefmt.today()
    with db.conn() as c:
        saved = routes.watches(c)
        chosen = next((w for w in saved if w["id"] == watch), None) if watch else None
        if chosen is None and not request.query_params and saved:
            chosen = saved[0]
        if chosen:
            route = {k: chosen[k] for k in routes.WATCH_FIELDS}
        else:
            route = {"origin": start or None, "destination": to or None, "require_ferry": _flag(ferry),
                     "require_fuel": _flag(fuel), "matches_only": _flag(matches)}
        everything = c.execute("SELECT origin, destination, region FROM relocations WHERE status = 'open'").fetchall()
        deals = sort_rows(routes.group(routes.rows(c, cfg, route, today)), sort)
        this_watch = next((w for w in saved if all(w[k] == (route[k] if route[k] is not None else None)
                                                   for k in routes.WATCH_FIELDS)), None)
        params = {"from": route["origin"], "to": route["destination"], "ferry": 1 if route["require_ferry"] else None,
                  "fuel": 1 if route["require_fuel"] else None, "matches": 1 if route["matches_only"] else None}
        return _page(request, "routes.html", cfg, c, page="routes", deals=deals, route=route,
                     route_label=routes.label(route), params=params, sort=sort, sorts=ROUTE_SORTS,
                     from_places=chain.places_by_region(everything, "origin"),
                     to_places=chain.places_by_region(everything, "destination"),
                     this_watch=this_watch, saved=saved,
                     cheapest=min(deals, key=lambda d: d["cost"]["total"]) if deals else None,
                     soonest=min((d["pickup_from"] for d in deals if d["pickup_from"]), default=None))


@app.get("/home")
def old_home_page():
    return RedirectResponse("/routes", status_code=301)


@app.post("/routes/watch")
def add_route_alert(start: str = Query("", alias="from"), to: str = "", ferry: str = "", fuel: str = "",
                    matches: str = ""):
    cfg = config.load()
    route = {"origin": start or None, "destination": to or None, "require_ferry": _flag(ferry),
             "require_fuel": _flag(fuel), "matches_only": _flag(matches)}
    with db.conn() as c:
        wid = routes.add_watch(c, cfg, route, timefmt.today(), timefmt.now())
    return RedirectResponse(f"/routes?watch={wid}", status_code=303)


@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    cfg = config.load()
    today = timefmt.today()
    with db.conn() as c:
        saved = routes.watches(c)
        for w in saved:
            w["label"] = routes.label(w)
            w["count"] = len(routes.group(routes.rows(c, cfg, w, today)))
        recent = c.execute("""SELECT * FROM messages WHERE kind <> 'home_baseline'
                              ORDER BY created_at DESC LIMIT 8""").fetchall()
        last_digest = c.execute("SELECT * FROM digests ORDER BY created_at DESC LIMIT 1").fetchone()
        return _page(request, "alerts.html", cfg, c, page="alerts", saved=saved, switches=prefs.SWITCHES,
                     recent=recent, last_digest=last_digest, signal_ready=signal.configured())


@app.post("/alerts/switch")
def set_alert_switch(key: str, on: str, back: str = "/alerts"):
    prefs.set_switch(key, _flag(on))
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/alerts", status_code=303)


@app.post("/alerts/route/{wid}")
def set_route_alert(wid: int, on: str = "", delete: str = ""):
    with db.conn() as c:
        if _flag(delete):
            c.execute("DELETE FROM watches WHERE id = %s", (wid,))
        else:
            c.execute("UPDATE watches SET alert = %s WHERE id = %s", (_flag(on), wid))
    return RedirectResponse("/alerts", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, page: int = 1, all: int = 0):
    cfg = config.load()
    per_page = 100
    page = max(page, 1)
    cutoff = timefmt.now() - timedelta(days=int(cfg["schedule"]["history_after_days"]))
    scope = "TRUE" if all else "matched"
    with db.conn() as c:
        total = c.execute(f"SELECT count(*) AS n FROM relocations WHERE status = 'gone' AND {scope} AND gone_at <= %s",
                          (cutoff,)).fetchone()["n"]
        rows = c.execute(f"""SELECT * FROM relocations WHERE status = 'gone' AND {scope} AND gone_at <= %s
                             ORDER BY gone_at DESC LIMIT %s OFFSET %s""",
                         (cutoff, per_page, (page - 1) * per_page)).fetchall()
        return _page(request, "history.html", cfg, c, page="history", rows=rows, total=total, page_no=page,
                     pages=max(1, -(-total // per_page)), show_all=bool(all))


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, all: int = 0):
    cfg = config.load()
    with db.conn() as c:
        return _page(request, "stats.html", cfg, c, page="stats", s=stats.collect(c, bool(all)), show_all=bool(all))


@app.get("/health")
def health():
    try:
        with db.conn() as c:
            c.execute("SELECT 1")
        return {"ok": True, "version": VERSION}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "version": VERSION, "error": str(e)}, status_code=503)
