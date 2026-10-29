"""Small helpers that turn each site's wording into the normalised record."""
import html
import re
from datetime import date

# Places the sites use, matched as whole words, so "Caringbah (Sydney)", "Sydney Airport" and
# "Bankstown Sydney Airport" all become Sydney.
KNOWN_CITIES = [
    "Hobart", "Launceston", "Devonport", "Melbourne", "Sydney", "Brisbane", "Adelaide", "Perth", "Darwin",
    "Cairns", "Gold Coast", "Sunshine Coast", "Alice Springs", "Broome", "Townsville", "Canberra", "Newcastle",
    "Mackay", "Ballina", "Byron Bay", "Geelong", "Wollongong", "Port Lincoln", "Exmouth", "Esperance", "Albany",
    "Kununurra", "Rockhampton", "Bundaberg", "Hervey Bay", "Coffs Harbour", "Port Macquarie", "Mildura",
    "Whyalla", "Bairnsdale", "Kalgoorlie",
]
# New Zealand (2026-10-30), e.g. "Harewood (Christchurch)", "Queenstown Airport", "Takanini (Auckland)".
KNOWN_CITIES_NZ = [
    "Auckland", "Wellington", "Christchurch", "Queenstown", "Dunedin", "Invercargill", "Nelson", "Picton",
    "Greymouth", "Hokitika", "Blenheim", "Kaikoura", "Rotorua", "Tauranga", "Napier", "Hamilton",
    "Palmerston North", "New Plymouth", "Whakatane", "Taupo", "Gisborne", "Whangarei", "Te Anau", "Wanaka",
    "Timaru", "Oamaru", "Pokeno", "Kerikeri", "Paihia", "Westport", "Twizel", "Ashburton", "Masterton",
    "Whanganui", "Levin", "Thames",
]


def city(place: str | None, region: str = "AU") -> str | None:
    if not place:
        return None
    s = place.strip()
    m = re.search(r"\(([^)]+)\)", s)
    if m and "airport" not in m.group(1).lower():
        return m.group(1).strip()
    for c in KNOWN_CITIES_NZ if region == "NZ" else KNOWN_CITIES:
        if re.search(rf"\b{re.escape(c)}\b", s, re.I):
            return c
    return re.sub(r"\s*\(.*?\)|\s+(International\s+)?Airport\b.*$", "", s, flags=re.I).strip() or s


def berths(text: str | None) -> int | None:
    m = re.search(r"(\d+)\s*-?\s*berth", text or "", re.I)
    return int(m.group(1)) if m else None


def day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def text_from_html(value: str | None) -> str | None:
    if not value:
        return None
    s = re.sub(r"<[^>]+>", " ", value)
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s or None


def dollars(value, currency: str | None = "AUD") -> str:
    """123.4 -> '$123' (AUD) or 'NZ$123' (NZD); None -> '****'."""
    if value is None:
        return "****"
    return f"{'NZ$' if currency == 'NZD' else '$'}{float(value):,.0f}"


def money(value) -> float | None:
    """'$300', '300', 300 -> 300.0; anything else -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    return float(m.group(0).replace(",", "")) if m else None
