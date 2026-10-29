"""The dashboard (http://10.1.1.200, LAN only, no login: public data, no decisions - David 2026-10-29).

Current: open matches (starred first) and missed opportunities from the last two days.
History: matches that went more than two days ago (kept forever). Stats: the brief's numbers.
"""
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import config, db, home, scheduler, settings, signal, stats, timefmt, trips
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
                             money=lambda v: f"${float(v):,.0f}" if v is not None else "****",
                             km=lambda v: f"{float(v):,.0f} km" if v is not None else "****",
                             stars=lambda v: "****" if v is None else v)
templates.env.globals.update(version=VERSION, source_label=lambda s: SOURCES[s].label if s in SOURCES else s,
                             trip_badge=trips.badge)
SORTS = {"starred": "Starred first", "explore": "Most room to explore", "soonest": "Soonest pickup"}


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
        "cfg": cfg, "sources": _sources(c, cfg), "config_error": config.error(), "dry_run": signal.is_dry_run(cfg),
        "next_poll": scheduler.next_poll(), "dashboard": settings.DASHBOARD_URL, **ctx})


@app.get("/", response_class=HTMLResponse)
def current(request: Request, fourwd: str = "", all: int = 0, sort: str = "starred"):
    cfg = config.load()
    view = fourwd if fourwd in config.FOUR_WD_MODES else "include"
    sort = sort if sort in SORTS else "starred"
    keep_days = int(cfg["schedule"]["history_after_days"])
    with db.conn() as c:
        matches = _group(c.execute("""SELECT * FROM relocations WHERE status = 'open' AND matched
                                      ORDER BY boosted DESC, pickup_from NULLS LAST, first_seen DESC""").fetchall())
        missed = _group(c.execute("""SELECT * FROM relocations WHERE status = 'gone' AND matched AND gone_at > %s
                                     ORDER BY gone_at DESC""",
                                  (timefmt.now() - timedelta(days=keep_days),)).fetchall())
        others = c.execute("""SELECT * FROM relocations WHERE status = 'open' AND NOT matched
                              ORDER BY vehicle_type, origin, destination""").fetchall() if all else []
        open_total = c.execute("SELECT count(*) AS n FROM relocations WHERE status = 'open'").fetchone()["n"]
        ways_home = home.group(home.open_rows(c, cfg, timefmt.today()))

        def view_ok(r):
            return view == "include" or (view == "only") == bool(r["four_wd"])
        matches = [r for r in matches if view_ok(r)]
        missed = [r for r in missed if view_ok(r)]
        if sort == "explore":
            matches.sort(key=lambda r: -((r["trip"] or {}).get("explore_km", -1)))
        elif sort == "soonest":
            matches.sort(key=lambda r: (r["pickup_from"] is None, r["pickup_from"]))
        return _page(request, "current.html", cfg, c, page="current", matches=matches, missed=missed,
                     others=others, show_all=bool(all), fourwd=view, open_total=open_total, keep_days=keep_days,
                     sort=sort, sorts=SORTS, ways_home=ways_home)


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
