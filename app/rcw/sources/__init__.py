"""The sites being watched. Add a new adapter here (see README "Adding a third source")."""
from .base import Listing, Source, SourceError
from .imoova import Imoova
from .transfercar import SITE_NZ, Transfercar

SOURCES: dict[str, Source] = {s.name: s for s in (
    Imoova(),
    Transfercar(),
    Transfercar("transfercar_nz", "Transfercar NZ", SITE_NZ, "NZ", "NZD"),   # 2026-10-30
)}

__all__ = ["SOURCES", "Listing", "Source", "SourceError"]
