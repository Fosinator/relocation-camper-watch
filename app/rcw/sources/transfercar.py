"""Transfercar (transfercar.com.au), the second big Australian relocation site.

The site's own JSON (robots.txt allows everything, checked 2026-10-29): one list request returns every live
listing (~175), and a detail request adds the vehicle name and description, bond and operator. The detail is
fetched once per new motorhome / campervan, at most details_per_poll a poll.
"""
import logging
import re
from urllib.parse import quote

from .. import normalise
from ..http import Blocked, Disallowed
from .base import Listing, Source, SourceError

log = logging.getLogger(__name__)

SITE = "https://www.transfercar.com.au"
LIST_URL = SITE + "/api-endpoint/listings.json?limit=500"
DETAIL_URL = SITE + "/api-endpoint/listing/{id}.json"


def vehicle_kind(text: str | None) -> tuple[str, bool]:
    """'4x4 Campervan (2 berth)' -> ('campervan', True); 'SUV (7 seats)' -> ('other', False)."""
    low = (text or "").lower()
    four_wd = bool(re.search(r"4x4|4wd", low))
    if "motorhome" in low or "motor home" in low:
        return "motorhome", four_wd
    if "camper" in low:
        return "campervan", four_wd
    return "other", four_wd


def listing_url(item: dict) -> str:
    def part(s):
        return quote((s or "").replace(" ", "_"), safe="()_")
    return f"{SITE}/relocation/{part(item.get('pickup_location'))}/{part(item.get('dropoff_location'))}/{item['id']}"


def _inclusion(inc: dict) -> str | None:
    kind = (inc.get("type") or "").strip().lower()
    desc = (inc.get("description") or "").strip()
    if kind in ("petrol", "diesel", "fuel"):
        return "Fuel " + (f"${desc}" if desc.isdigit() else desc)
    if "ferry" in kind:
        return ("Ferry included " + desc).strip()
    return f"{kind.title()} {desc}".strip() or None


def normalise_item(item: dict, detail: dict | None) -> Listing:
    d = detail or {}
    dv = d.get("vehicle_type") if isinstance(d.get("vehicle_type"), dict) else {}
    type_text = item.get("vehicle_type") if isinstance(item.get("vehicle_type"), str) else dv.get("type")
    vtype, four_wd = vehicle_kind(type_text)
    name = dv.get("name")
    vehicle_name = f"{name} – {type_text}" if name and type_text else (name or type_text)
    incs = [s for s in (_inclusion(i) for i in (item.get("inclusions") or d.get("inclusions") or [])) if s]
    paid = item.get("paid_days")
    bond = ((d.get("policies") or {}).get("insurance_policy") or {}).get("bond")
    return Listing(
        source="transfercar",
        listing_id=str(item["id"]),
        url=listing_url(item),
        vehicle_name=vehicle_name,
        vehicle_type=vtype,
        four_wd=four_wd,
        berths=normalise.berths(type_text),
        origin=normalise.city(item.get("pickup_location")),
        destination=normalise.city(item.get("dropoff_location")),
        pickup_from=normalise.day(item.get("pickup_date")),
        pickup_to=normalise.day(item.get("dropoff_date")),   # the site shows "From <pickup_date> To <dropoff_date>"
        days_included=item.get("free_days"),
        extra_days=paid,
        extra_day_price=normalise.money(item.get("price_per_paid_day")) if paid else None,
        inclusions=" · ".join(incs) or None,
        bond=normalise.money(bond),
        operator=(d.get("operator") or {}).get("company"),
        description=dv.get("description"),
        raw={"item": item, "detail": detail},
    )


class Transfercar(Source):
    name = "transfercar"
    label = "Transfercar"

    def fetch(self, http, cfg, cached):
        data = http.get(LIST_URL).json()
        items = [i for i in data.get("listings") or []
                 if not i.get("is_ghost_listing") and i.get("status_name", "listed") == "listed"]
        if not items:
            raise SourceError("the listings feed came back empty")
        if (data.get("count") or 0) > len(data.get("listings") or []):
            log.warning("transfercar: got %d of %d listings - raise the limit in LIST_URL",
                        len(data["listings"]), data["count"])
        budget = int(cfg["sources"]["transfercar"].get("details_per_poll", 40))
        out = []
        for item in items:
            detail = (cached.get(str(item["id"])) or {}).get("detail")
            if detail is None and vehicle_kind(item.get("vehicle_type"))[0] != "other" and budget > 0:
                budget -= 1
                try:
                    detail = http.get(DETAIL_URL.format(id=item["id"])).json()
                    detail.pop("pickup_location", None)   # opening hours and maps: not needed, keeps raw small
                    detail.pop("dropoff_location", None)
                except (Blocked, Disallowed):
                    raise
                except Exception as e:  # noqa: BLE001 - one bad detail must not fail the poll
                    log.info("transfercar detail %s skipped: %s", item["id"], e)
            out.append(normalise_item(item, detail))
        return out
