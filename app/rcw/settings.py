"""Settings from the environment: secrets and per-install values (app.env, not in git).

Filters, schedule and alert switches live in config.yaml instead, so they can change without a restart.
"""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SIGNAL_URL = os.environ.get("SIGNAL_URL", "")
SIGNAL_FROM = os.environ.get("SIGNAL_FROM", "")
SIGNAL_TO = [n.strip() for n in os.environ.get("SIGNAL_TO", "").split(",") if n.strip()]
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yaml")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://10.1.1.200")
# NSW FuelCheck API (api.nsw.gov.au, free tier): live diesel prices for NSW and Tasmania. Optional.
FUELCHECK_KEY = os.environ.get("FUELCHECK_KEY", "")
FUELCHECK_SECRET = os.environ.get("FUELCHECK_SECRET", "")
# DRY_RUN=1 forces dry-run whatever config.yaml says: digests and alerts are printed to the log, never sent.
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
# SCHEDULER=0 runs the web pages only (no polls, no digest).
SCHEDULER = os.environ.get("SCHEDULER", "1") == "1"
TZ = "Australia/Hobart"
