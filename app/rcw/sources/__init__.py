"""The sites being watched. Add a new adapter here (see README "Adding a third source")."""
from .base import Listing, Source, SourceError
from .imoova import Imoova
from .transfercar import Transfercar

SOURCES: dict[str, Source] = {s.name: s for s in (Imoova(), Transfercar())}

__all__ = ["SOURCES", "Listing", "Source", "SourceError"]
