"""config.yaml: filters, boosts, schedule and alert switches (brief: "all filters live in one config file").

Read again whenever it changes on disk, so a filter edit takes effect at the next poll without a restart.
Anything missing from the file falls back to DEFAULTS. A file that fails to load keeps the last good settings,
and the error shows on the dashboard instead of stopping the polls.
"""
import copy
import logging
import os
import threading

import yaml

from . import settings

log = logging.getLogger(__name__)

DEFAULTS = {
    "filters": {
        "vehicle_types": ["motorhome", "campervan"],
        "four_wd": "include",            # include | exclude | only  (David 2026-10-29: keep 4WD campers filterable)
        "min_berths": 2,                 # brief said 4; David: "2 berths are OK"
        "cities": ["Hobart", "Melbourne", "Sydney", "Brisbane", "Adelaide", "Perth"],
        "min_days": 4,
        "pickup_within_days": 60,
    },
    "boosts": {
        "keywords": ["rear lounge", "drop-down bed", "island bed", "Winnebago", "Avida", "Iveco", "Sprinter"],
        "inclusions": ["fuel", "ferry"],
    },
    "schedule": {
        "poll_hours": "6-22",
        "jitter_seconds": 120,
        "digest_time": "16:00",
        "gone_after_missed_polls": 2,
        "history_after_days": 2,
        "backoff_max_hours": 8,
        "blocked_backoff_hours": 24,
    },
    "alerts": {
        "dry_run": False,
        "instant_starred": False,
        "outage_hours": 24,
        "digest_max_listed": 10,
        "db_size_warn_mb": 500,
    },
    "sources": {
        "imoova": {"enabled": True, "simulate_outage": False, "details_per_poll": 40},
        "transfercar": {"enabled": True, "simulate_outage": False, "details_per_poll": 40},
    },
}

FOUR_WD_MODES = ("include", "exclude", "only")

_lock = threading.Lock()
_state = {"mtime": None, "cfg": None, "error": None}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _check(cfg: dict) -> dict:
    f = cfg["filters"]
    if f["four_wd"] not in FOUR_WD_MODES:
        raise ValueError(f"filters.four_wd must be one of {', '.join(FOUR_WD_MODES)} (got {f['four_wd']!r})")
    f["vehicle_types"] = [str(t).lower() for t in f["vehicle_types"]]
    for key in ("min_berths", "min_days", "pickup_within_days"):
        f[key] = int(f[key])
    hh, mm = str(cfg["schedule"]["digest_time"]).split(":")
    cfg["schedule"]["digest_time"] = f"{int(hh):02d}:{int(mm):02d}"
    return cfg


def load(path: str | None = None) -> dict:
    """The current settings (a fresh copy, safe to modify)."""
    path = path or settings.CONFIG_PATH
    with _lock:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if _state["cfg"] is None or mtime != _state["mtime"]:
            try:
                raw = {}
                if mtime is not None:
                    with open(path, encoding="utf-8") as fh:
                        raw = yaml.safe_load(fh) or {}
                _state["cfg"] = _check(_merge(DEFAULTS, raw))
                _state["error"] = None
                if _state["mtime"] is not None:
                    log.info("config.yaml changed - new settings in use")
            except Exception as e:  # noqa: BLE001
                _state["error"] = f"config.yaml could not be read, using the last good settings: {e}"
                log.error(_state["error"])
                if _state["cfg"] is None:
                    _state["cfg"] = _check(copy.deepcopy(DEFAULTS))
            _state["mtime"] = mtime
        return copy.deepcopy(_state["cfg"])


def error() -> str | None:
    return _state["error"]


def defaults() -> dict:
    return _check(copy.deepcopy(DEFAULTS))


def hours(spec: str) -> set[int]:
    """'6-22' or '6,8,10-12' -> the hours it covers (same format as the cron hour field)."""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out
