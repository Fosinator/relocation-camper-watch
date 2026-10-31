"""Signal messages through the homelab's signal-cli-rest-api (POST /v1/send on signal-net, the same path Uptime
Kuma and Experience Radar v2 use), from SIGNAL_FROM to SIGNAL_TO (David's number only, 2026-10-29).

Dry-run prints the message to the log instead. So does the master switch (alerts.signal, "All Signal messages"
on the Alerts page): while it's off, everything is handled exactly as if sent - digests are marked, route alerts
count their listings as seen - so switching it back on never sends a backlog.
A failed send is logged and reported back, never fatal.
"""
import json
import logging
import urllib.request

from . import settings

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(settings.SIGNAL_URL and settings.SIGNAL_FROM and settings.SIGNAL_TO)


def paused(cfg: dict) -> bool:
    return not cfg["alerts"].get("signal", True)


def is_dry_run(cfg: dict) -> bool:
    """Messages are written to the log, not sent: dry-run, or every Signal message switched off."""
    return settings.DRY_RUN or bool(cfg["alerts"].get("dry_run")) or paused(cfg)


def send(text: str, cfg: dict) -> bool:
    """-> True when sent (or printed, in dry-run / while switched off)."""
    if is_dry_run(cfg):
        why = "switched off" if paused(cfg) else "DRY RUN"
        log.info("%s - Signal message not sent:\n%s", why, text)
        print(f"----- {why}: Signal message -----\n{text}\n-----------------------------------", flush=True)
        return True
    if not configured():
        log.warning("Signal is not configured (SIGNAL_URL / SIGNAL_FROM / SIGNAL_TO) - message not sent")
        return False
    body = json.dumps({"message": text, "number": settings.SIGNAL_FROM, "recipients": settings.SIGNAL_TO}).encode()
    req = urllib.request.Request(settings.SIGNAL_URL.rstrip("/") + "/v1/send", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Signal send failed: %s", e)
        return False
