# Relocation Camper Watch

Watches **Imoova** and **Transfercar** for motorhome and campervan relocations in Australia, so short $1/day
relocations can be used to try motorhome layouts before buying one. Booking stays manual: no logins, no payments.

- **Dashboard:** http://10.1.1.200 (home network only, no login)
  - **Current:** open matches (starred first) and missed opportunities from the last 2 days
  - **History:** matches that went more than 2 days ago (nothing is ever deleted)
  - **Stats:** matches per week, top routes, days included, time until gone, Hobart routes, polling health
- **Signal:** one digest a day at 4 pm Hobart time, sent on quiet days too. It also carries site failures and a
  database-size warning. The only other message is one alert if a site has had no good poll for 24 hours.
- **Polls:** every hour from 6 am to 10 pm Hobart time, plus 0–2 min of random jitter. Each site is polled
  separately with its own back-off.

Built from the brief of 29 Oct 2026, with David's changes the same day: 2+ berths, a missing value passes the
filter (shown as `****`), 4WD campers are a setting, every listing is stored, digest only (no instant alerts)
while gathering data for 9–12 months.

## How it reads the sites

| Site | What it fetches each poll | robots.txt (checked 29 Oct 2026) |
|---|---|---|
| Imoova | `www.imoova.com/relocations/table/australia`: one page that embeds every live listing. Plus each **new** motorhome/campervan's own page, once, for the vehicle description. | `www` allows it. `api.imoova.com` disallows everything, so the API is **not** used. |
| Transfercar | `/api-endpoint/listings.json`: every live listing in one request. Plus `/api-endpoint/listing/<id>.json` once per **new** motorhome/campervan, for the vehicle name, description, bond and operator. | allows everything |

Every request is checked against the site's robots.txt (cached for a day) and spaced at least 2 seconds apart.
A 403 or 429 from a site stops polling it for 24 hours. Other errors back off 1, 2, 4, then 8 hours. A failed
poll never counts towards "gone", and a sudden collapse in the listing count is treated as a broken page.

## Setup (andstar1)

Everything lives under `/mnt/user/appdata/relocation-camper-watch/`:

| Path | What |
|---|---|
| `app/` | code, deployed from GitHub by `scripts/deploy.sh` (mounted read-only) |
| `config/config.yaml` | filters, schedule and alert switches. Seeded on the first deploy, then **edit it on the server**. |
| `config/app.env` | secrets, not in git (see below) |
| `data/` | deploy log and database dumps taken before each deploy (the last 10 are kept) |
| `venv/` | Python packages, rebuilt when `requirements.txt` changes (excluded from Appdata Backup) |

The container `relocation-camper-watch` runs the stock `python:3.12-slim` image from the Unraid template
`unraid/my-relocation-camper-watch.xml`. Its networks are br0 at 10.1.1.200, `v2-net` (database) and `signal-net`
(Signal). The database is `relocation_watch` (login `relocation_watch`) on the v2 Postgres server `exp-v2-db`.
The tests use `relocation_watch_test`.

`config/app.env`:

```
DATABASE_URL=postgresql://relocation_watch:<password>@v2db:5432/relocation_watch
TEST_DATABASE_URL=postgresql://relocation_watch:<password>@v2db:5432/relocation_watch_test
SIGNAL_URL=http://signal-cli-rest-api:8080
SIGNAL_FROM=+61...
SIGNAL_TO=+61...            # comma-separated for more than one
DASHBOARD_URL=http://10.1.1.200
# DRY_RUN=1                 # force dry-run whatever config.yaml says
```

Deploy any pushed branch, tag or commit with `sh scripts/deploy.sh <ref>`. It backs up the database, swaps the
code (the old copy is kept as `app.prev`), restarts, checks `/health` and runs the tests inside the container.
To back out, deploy the previous ref.

### Commands

Run them inside the container: `docker exec -w /app relocation-camper-watch /venv/bin/python -m rcw <command>`

| Command | What |
|---|---|
| `poll all --dry-run` | fetch and filter both sites now and print the normalised records. Touches nothing. |
| `poll imoova` | a real poll now (ignores any back-off) |
| `digest --preview` | print the would-be digest and change nothing |
| `digest --send` | send the digest now (honours dry-run) |
| `outages` | run the 24-hour outage check now |

**Dry-run mode:** set `alerts.dry_run: true` in config.yaml (or `DRY_RUN=1` in app.env). The digest and alerts
then go to the container log (`docker logs relocation-camper-watch`) instead of Signal.

**Simulated outage:** set `sources.<site>.simulate_outage: true`. Every poll of that site then fails. The
dashboard shows a red banner straight away, the next digest lists the failures, and after 24 hours one Signal
alert goes out. Set it back to `false` to recover.

## config.yaml fields

Changes are picked up at the next poll, digest or page load, with no restart, except `schedule.poll_hours`.
If the file has a mistake, the last good settings stay in use and the dashboard shows the error.

| Field | Default | Meaning |
|---|---|---|
| `filters.vehicle_types` | `[motorhome, campervan]` | normalised types that can match (`motorhome`, `campervan`, `other`) |
| `filters.four_wd` | `include` | 4WD campers: `include`, `exclude` or `only`. The dashboard also has a view toggle. |
| `filters.min_berths` | `2` | fewer berths fails; not shown = passes |
| `filters.cities` | 6 capitals | the origin **or** destination must be one of these |
| `filters.min_days` | `4` | included ($1/day or free) days |
| `filters.pickup_within_days` | `60` | the earliest pickup must be within this many days |
| `boosts.keywords` | rear lounge, drop-down bed, island bed, Winnebago, Avida, Iveco, Sprinter | case-insensitive, in the vehicle name and description; a hit = ⭐ |
| `boosts.inclusions` | `[fuel, ferry]` | inclusion words that give a ⭐ |
| `schedule.poll_hours` | `"6-22"` | Hobart hours to poll, on the hour (cron hour format; needs a restart) |
| `schedule.jitter_seconds` | `120` | random delay before each poll |
| `schedule.digest_time` | `"16:00"` | caught up later the same day if the service was down |
| `schedule.gone_after_missed_polls` | `2` | missing from this many successful polls in a row = gone |
| `schedule.history_after_days` | `2` | gone matches stay on Current as "missed" this long |
| `schedule.backoff_max_hours` / `blocked_backoff_hours` | `8` / `24` | error back-off |
| `alerts.dry_run` | `false` | log instead of sending |
| `alerts.instant_starred` | `false` | one Signal message per new starred match, with its booking link(s) |
| `alerts.outage_hours` | `24` | one alert when a site has had no good poll this long |
| `alerts.digest_max_listed` | `10` | deals listed in the digest (the counts always cover everything) |
| `alerts.db_size_warn_mb` | `500` | the digest warns above this size |
| `sources.<site>.enabled` | `true` | switch a site off |
| `sources.<site>.simulate_outage` | `false` | make every poll fail (testing) |
| `sources.<site>.details_per_poll` | `40` | detail requests per poll, for new motorhomes and campervans |

## How matching works

- **Hard filters** (all must pass): vehicle type, 4WD setting, berths, origin or destination city, included
  days and pickup window. A value the site doesn't show counts as passed.
- **Open listings are re-checked at every poll** and before every digest, so config edits apply straight away,
  and a listing whose pickup date comes within 60 days becomes a match then.
- **Gone:** missing from 2 successful polls in a row. A gone listing that reappears is open again.
- **Same deal on both sites:** same route and vehicle type, berths equal or unknown, and overlapping pickup
  windows. Both are stored. The digest lists the deal once with both links, and the dashboard shows one card with
  both buttons.

## Adding a third source

1. Create `app/rcw/sources/<site>.py` with a `Source` subclass. Give it a `name` (used in the database and
   config) and a `label`, and a `fetch(http, cfg, cached)` that returns a list of `Listing` records (see
   `sources/base.py`).
   - Use `http.get(url)`: it honours robots.txt, spaces requests and raises `Blocked` on 403/429.
   - `cached` maps the site's listing ids to the raw payload already stored, so extra detail requests happen once
     per listing. Put whatever you fetch in `raw`.
   - Normalise `vehicle_type` to `motorhome` / `campervan` / `other`, set `four_wd`, and use `normalise.city()`
     for origin and destination. Leave unknown values as `None`.
   - Raise `SourceError` when the page gives nothing usable.
2. Register it in `app/rcw/sources/__init__.py`.
3. Add `sources.<name>: {enabled: true, simulate_outage: false}` to config.yaml (on the server too).
4. Save a sample response under `app/tests/fixtures/` and add parsing tests to `tests/test_sources.py`.
5. Restart, since the poll jobs are created at start-up. Check with `poll <name> --dry-run` first.
