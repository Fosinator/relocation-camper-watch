# Relocation Camper Watch

Watches **Imoova** and **Transfercar** for motorhome and campervan relocations in Australia, so short $1/day
relocations can be used to try motorhome layouts before buying one. Booking stays manual: no logins, no payments.

- **Dashboard:** http://10.1.1.200 (home network only, no login)
  - **Current:** ways home to Hobart first, then open matches (starred first) with trip stats, and missed
    opportunities from the last 2 days
  - **History:** matches that went more than 2 days ago (nothing is ever deleted)
  - **Stats:** matches per week, top routes, days included, time until gone, Hobart routes, polling health
- **Signal:** one digest a day at 4 pm Hobart time, sent on quiet days too. It also carries site failures and a
  database-size warning. The only other messages:
  - a straight-away alert for each new way home to Hobart
  - one alert if a site has had no good poll for 24 hours
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
| `trips` | recompute the trip stats now (fetching missing routes within the budget) |
| `home` | list the ways home with their cost estimate (changes nothing) |

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
| `filters.exclude_keywords` | `[hitop, poptop]` | a vehicle name containing any of these never matches. Spaces and hyphens are ignored, so `hitop` also catches Hi-Top, Hi Top and HiTop. |
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
| `trip.comfortable_hours_per_day` / `busy_hours_per_day` / `max_hours_per_day` | `5` / `7` / `9` | pace bands |
| `trip.camper_time_factor` | `1.15` | camper hours = routing-service car hours × this |
| `trip.fuel_l_per_100km` / `fuel_price_per_litre` | 15 / 12 / 9 L, `$2.10` | fuel estimate |
| `trip.routing_requests_per_poll` | `60` | requests to the routing service per poll |
| `trip.unsealed_roads` | 16 outback roads | a route using one of these gets a note on its card |

## How matching works

- **Hard filters** (all must pass): vehicle type, 4WD setting, excluded vehicle names (hi-tops and pop-tops),
  berths, origin or destination city, included days and pickup window. A value the site doesn't show counts as
  passed.
- **Open listings are re-checked at every poll** and before every digest, so config edits apply straight away,
  and a listing whose pickup date comes within 60 days becomes a match then.
- **Gone:** missing from 2 successful polls in a row. A gone listing that reappears is open again.
- **Same deal on both sites:** same route and vehicle type, berths equal or unknown, and overlapping pickup
  windows. Both are stored. The digest lists the deal once with both links, and the dashboard shows one card with
  both buttons.

## Trip stats

Every open match gets a **pace badge** ("Comfortable · +222 km to explore") and a **trip panel**. The panel
opens on hover with a mouse, or on tap on a phone. The digest line carries the badge too, and the Current page
can sort by most room to explore.

- **Route:** road km and driving hours from the public OSRM routing service, plus 15% because campers are
  slower than car times. Ferry legs (Spirit of Tasmania) are kept out of the driving numbers. One request per
  route, kept for good in `road_routes`, at most `trip.routing_requests_per_poll` a poll.
- **Pace:** driving hours a day over the included days. Comfortable ≤ 5 h, busy ≤ 7 h, rushed ≤ 9 h, beyond
  that too tight. Also the days a comfortable trip takes, and the extra days to buy (and their cost) if short.
- **Explore:** spare km = (days × 5 h − driving hours) × the route's average speed, or what the site's km
  cap leaves beyond the route, whichever is smaller. The panel says which one limits it, the days or the km cap,
  because a long relocation with a tight cap leaves little room to wander. Caps come from Transfercar
  `kms_allowed` and from Imoova `distance_allowed` plus its km per extra day. Also shown with every extra day
  bought, and with the per-km charge over the cap where the site states one.
- **Fuel:** route km × L/100 km (motorhome 15, campervan 12) × $/L, against the listing's fuel allowance.
- **Unsealed roads:** the routing service happily uses outback tracks (it takes Darwin → Uluru → Perth along
  the Great Central Road), so a route using a road in `trip.unsealed_roads` gets a note instead of being
  trusted.
- A city without a map position shows "no map position for X". Add it to `CITY_COORDS` in `rcw/geo.py`
  (and to `KNOWN_CITIES` in `rcw/normalise.py`).

## Ways home

A relocation into `home.city` (Hobart) with the Bass Strait ferry included is a cheap way home from the
mainland. The hard filters don't apply here: any vehicle counts, including a hi-top or a car. The Current page
lists them first. When the same van is on both sites (same origin, vehicle type, berths and first pickup day),
it's shown once with both links.

**Cost home** = hire ($1 a day) + the booking fee (Imoova states it; Transfercar doesn't) + fuel for the route
less any fuel allowance + a ferry seat for each extra passenger (`home.passengers`), if
`home.extra_passenger_ferry_aud` is set. The ferry deals cover the vehicle and the driver only.

**Signal:** each new way home goes out straight away, in one message per poll. The first run only takes a
baseline, so switching it on doesn't send a backlog. `home.alert: false` turns it off; the digest still carries a
count. Run `python -m rcw home` to print the current list with costs.

| Field | Default | Meaning |
|---|---|---|
| `home.city` | `Hobart` | destination that counts as home |
| `home.from_cities` | `[]` | only from these cities (empty = anywhere) |
| `home.require_ferry` | `true` | only listings that include the ferry |
| `home.alert` | `true` | instant Signal message for each new one |
| `home.passengers` | `2` | for the cost estimate |
| `home.extra_passenger_ferry_aud` | `null` | seat price for each extra passenger |

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
