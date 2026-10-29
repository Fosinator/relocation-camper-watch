"""The normalised record every source adapter returns, and the adapter interface.

To add a site: subclass Source, return Listing records from fetch(), and register it in sources/__init__.py
(see README "Adding a third source").
"""
from dataclasses import asdict, dataclass, field
from datetime import date


class SourceError(Exception):
    """The site answered, but not with anything we can use (layout change, empty list...)."""


@dataclass
class Listing:
    source: str
    listing_id: str
    url: str
    vehicle_name: str | None = None
    vehicle_type: str = "other"          # motorhome | campervan | other
    four_wd: bool = False
    berths: int | None = None            # None = not shown (passes the filter, shows as ****)
    origin: str | None = None            # city
    destination: str | None = None
    pickup_from: date | None = None
    pickup_to: date | None = None
    days_included: int | None = None     # the $1/day (or free) days
    extra_days: int | None = None
    extra_day_price: float | None = None  # AUD
    inclusions: str | None = None        # "Fuel $300 · Ferry included"
    bond: float | None = None            # AUD
    operator: str | None = None
    description: str | None = None       # vehicle text, searched by the keyword boosts
    raw: dict = field(default_factory=dict)

    def row(self) -> dict:
        return asdict(self)


class Source:
    name = ""
    label = ""

    def fetch(self, http, cfg: dict, cached: dict[str, dict]) -> list[Listing]:
        """Every live Australian listing on the site.

        cached = {listing_id: stored raw payload} for listings already in the database, so details that need an
        extra request are fetched once per listing, not on every poll.
        """
        raise NotImplementedError
