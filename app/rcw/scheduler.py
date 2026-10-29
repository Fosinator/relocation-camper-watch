"""When things run (Hobart time): each source hourly within schedule.poll_hours plus 0-2 min of random jitter,
and an hourly check that sends the digest once it's due and looks for full-day outages.

The poll hours are read when the service starts, so changing schedule.poll_hours needs a restart; everything
else in config.yaml is picked up as it changes. After a restart a missed poll or digest is caught up.
"""
import logging
import random
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import alerts, config, db, digest, poller, timefmt
from .sources import SOURCES

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def scheduled_poll(name: str) -> None:
    cfg = config.load()
    time.sleep(random.uniform(0, float(cfg["schedule"]["jitter_seconds"])))
    try:
        poller.poll(name)
    except Exception:  # noqa: BLE001 - e.g. the database is down; the next hour tries again
        log.exception("poll %s crashed", name)


def hourly() -> None:
    cfg = config.load()
    now = timefmt.now()
    try:
        if digest.due(cfg, now):
            digest.send(cfg, now)
        alerts.check_outages(cfg, now)
    except Exception:  # noqa: BLE001
        log.exception("hourly job crashed")


def catch_up() -> None:
    """After a (re)start: poll any source not polled in the last hour (inside poll hours), then the digest."""
    time.sleep(20)
    cfg = config.load()
    now = timefmt.now()
    if timefmt.local(now).hour in config.hours(cfg["schedule"]["poll_hours"]):
        for name in SOURCES:
            try:
                with db.conn() as c:
                    last = db.last_poll_at(c, name)
                if last is None or (now - last).total_seconds() > 3600:
                    log.info("catch-up poll for %s (last poll %s)", name, timefmt.ago(last))
                    poller.poll(name)
            except Exception:  # noqa: BLE001
                log.exception("catch-up poll %s crashed", name)
    hourly()


def start() -> BackgroundScheduler:
    global _scheduler
    cfg = config.load()
    s = BackgroundScheduler(timezone=timefmt.TZ,
                            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900})
    for name in SOURCES:
        s.add_job(scheduled_poll, CronTrigger(hour=cfg["schedule"]["poll_hours"], minute=0, timezone=timefmt.TZ),
                  args=[name], id=f"poll-{name}", name=f"poll {name}")
    s.add_job(hourly, CronTrigger(minute=0, second=30, timezone=timefmt.TZ), id="hourly", name="digest + outages")
    s.start()
    threading.Thread(target=catch_up, daemon=True, name="catch-up").start()
    _scheduler = s
    log.info("scheduler started: polls at hours %s, digest at %s", cfg["schedule"]["poll_hours"],
             cfg["schedule"]["digest_time"])
    return s


def next_poll():
    if not _scheduler:
        return None
    times = [j.next_run_time for j in _scheduler.get_jobs() if j.id.startswith("poll-") and j.next_run_time]
    return min(times) if times else None


def stop() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)
