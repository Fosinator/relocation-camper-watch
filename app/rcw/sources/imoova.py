"""Imoova (imoova.com), the largest Australian relocation site, and New Zealand (2026-10-30).

api.imoova.com's robots.txt disallows everything, so this reads the public pages, which robots.txt allows
(checked 2026-10-29): the table view embeds every live listing worldwide in one page (~270: ~213 Australian, ~110 NZ),
and each listing's own page adds the vehicle description, brand and features, and the km cap. That detail page
is fetched once per new motorhome / campervan, at most details_per_poll a poll.
"""
import logging

from .. import normalise, seroval
from ..http import Blocked, Disallowed
from .base import Listing, Source, SourceError

log = logging.getLogger(__name__)

SITE = "https://www.imoova.com"
TABLE_URL = SITE + "/relocations/table/australia"
TYPES = {
    "MOTOR_HOME": ("motorhome", False),
    "CAMPER_VAN": ("campervan", False),
    "FOUR_WHEEL_DRIVE_CAMPER": ("campervan", True),
    "FOUR_WHEEL_DRIVE": ("other", True),
}


REGION_SLUGS = {"AU": "australia", "NZ": "new-zealand"}


def listing_url(item: dict) -> str:
    country = REGION_SLUGS.get(item.get("departure_city_region") or "AU", "australia")
    return (f"{SITE}/relocations/{country}/{item.get('departure_city_slug')}-to-"
            f"{item.get('delivery_city_slug')}/{item.get('reference')}")


def live_items(page: str, regions=("AU",)) -> list[dict]:
    """Relocations in the chosen regions (AU, NZ) that still have a vehicle to book."""
    return [o for o in seroval.objects(page, "RelocationTableItem")
            if o.get("departure_city_region") in regions and o.get("type", "RELOCATION") == "RELOCATION"
            and (o.get("count") is None or o.get("count") > 0)]


RELOCATION_FIELDS = ("distance_allowed", "excess_distance_rate", "extra_distance_allowed_per_extra_hire_unit",
                     "measurement")


def vehicle_from_page(page: str) -> dict:
    vehicles = seroval.objects(page, "Vehicle")
    return vehicles[0] if vehicles else {}


def detail_from_page(page: str) -> dict:
    """The listing page's vehicle, plus the km cap from its relocation record (checked 2026-10-29:
    distance_allowed 3150, excess_distance_rate 55 = cents per km, +250 km per extra day)."""
    relocations = seroval.objects(page, "Relocation")
    rel = relocations[0] if relocations else {}
    return {"vehicle": vehicle_from_page(page), "relocation": {k: rel.get(k) for k in RELOCATION_FIELDS}}


def normalise_item(item: dict, detail: dict | None) -> Listing:
    v = (detail or {}).get("vehicle") or {}
    vtype, four_wd = TYPES.get(item.get("vehicle_type") or "", ("other", False))
    name = item.get("vehicle_name") or v.get("name")
    make = " ".join(x for x in (v.get("brand"), v.get("model")) if x)
    vehicle_name = f"{name} ({make})" if make and name and make.lower() not in name.lower() else (name or make or None)

    inclusions = []
    if item.get("has_fuel"):
        amount = item.get("fuel_amount")
        inclusions.append(" ".join(x for x in ("Fuel", f"${amount / 100:,.0f}" if amount else "",
                                               item.get("fuel_description") or "") if x))
    if item.get("has_ferry"):
        inclusions.append(" ".join(x for x in ("Ferry included", item.get("ferry_description") or "") if x))

    features = [label for key, label in (("has_shower_and_toilet", "shower and toilet"), ("has_kitchen", "kitchen"),
                                         ("is_self_contained", "self-contained")) if v.get(key)]
    description = " ".join(x for x in (normalise.text_from_html(v.get("description")),
                                       f"Features: {', '.join(features)}." if features else None) if x) or None

    region = item.get("departure_city_region") or "AU"
    currency = item.get("currency") or ("NZD" if region == "NZ" else "AUD")
    extra = item.get("extra_units")
    rate = item.get("extra_hire_unit_rate")
    rel = (detail or {}).get("relocation") or {}
    to_km = 1.609 if rel.get("measurement") == "IMPERIAL" else 1.0
    cap = rel.get("distance_allowed")
    per_day = rel.get("extra_distance_allowed_per_extra_hire_unit")
    excess = rel.get("excess_distance_rate")
    return Listing(
        source="imoova",
        listing_id=str(item["id"]),
        url=listing_url(item),
        vehicle_name=vehicle_name,
        vehicle_type=vtype,
        four_wd=four_wd,
        berths=(item.get("vehicle_sleeps") or v.get("sleeps") or normalise.berths(name)
                or normalise.berths(v.get("name"))),
        origin=normalise.city(item.get("departure_city_name"), region),
        destination=normalise.city(item.get("delivery_city_name"), region),
        pickup_from=normalise.day(item.get("available_from_date")),
        pickup_to=normalise.day(item.get("latest_departure_date") or item.get("available_to_date")),
        days_included=item.get("relocation_units"),
        extra_days=extra,
        extra_day_price=rate / 100 if extra and rate is not None else None,
        inclusions=" · ".join(inclusions) or None,
        description=description,
        km_allowed=round(cap * to_km) if cap else None,
        km_per_extra_day=round(per_day * to_km) if per_day else None,
        excess_km_rate=excess / 100 / to_km if excess else None,
        region=region,
        currency=currency,
        raw={"item": item, "detail": detail},
    )


class Imoova(Source):
    name = "imoova"
    label = "Imoova"
    regions = frozenset({"AU", "NZ"})

    def fetch(self, http, cfg, cached):
        regions = tuple(r for r in cfg["regions"] if r in self.regions)
        items = live_items(http.get(TABLE_URL).text, regions)
        if not items:
            raise SourceError(f"no {'/'.join(regions)} listings in the table page - has the page layout changed?")
        budget = int(cfg["sources"]["imoova"].get("details_per_poll", 40))
        out = []
        for item in items:
            detail = (cached.get(str(item["id"])) or {}).get("detail")
            # details saved before 2026-10-29 lack the km cap ("relocation"): fetch those again too
            if ((detail is None or "relocation" not in detail) and budget > 0
                    and TYPES.get(item.get("vehicle_type") or "", ("other",))[0] != "other"):
                budget -= 1
                try:
                    detail = detail_from_page(http.get(listing_url(item)).text)
                except (Blocked, Disallowed):
                    raise
                except Exception as e:  # noqa: BLE001 - one bad detail page must not fail the poll
                    log.info("imoova detail %s skipped: %s", item.get("reference"), e)
            out.append(normalise_item(item, detail))
        return out
