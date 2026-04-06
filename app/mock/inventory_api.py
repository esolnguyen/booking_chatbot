"""Inventory service -- reads crawled data from the SQLite DB.

Returns empty lists when the crawler has not yet populated the DB for the
requested route / city / date. The chatbot handles empty results by suggesting
similar available data.
"""

from datetime import date

from app.mock.seed_data import AIRPORT_TO_CITY, CITY_TO_AIRPORTS
from app.models.option import FlightOption, HotelOption
from app.crawler import db as crawler_db


def _resolve_destination(destination: str) -> tuple[list[str], str]:
    """Resolve a destination string to (airport_codes, city_name)."""
    dest_upper = destination.upper()
    if dest_upper in AIRPORT_TO_CITY:
        city = AIRPORT_TO_CITY[dest_upper]
        return CITY_TO_AIRPORTS.get(city, [dest_upper]), city
    for city, codes in CITY_TO_AIRPORTS.items():
        if city.lower() == destination.lower():
            return codes, city
    return [dest_upper], destination


def get_available_flights(
    origin: str,
    destination: str,
    dep_date: date | None = None,
) -> list[FlightOption]:
    """Return crawled flights for a route, or [] if none have been crawled yet."""
    if dep_date is None:
        return []
    dest_codes, _ = _resolve_destination(destination)
    for code in dest_codes:
        flights = crawler_db.get_fresh_flights(origin, code, dep_date)
        if flights:
            return flights
    return []


def get_available_hotels(
    destination: str,
    checkin: date | None = None,
) -> list[HotelOption]:
    """Return crawled hotels for a city, or [] if none have been crawled yet."""
    if checkin is None:
        return []
    _, city = _resolve_destination(destination)
    return crawler_db.get_fresh_hotels(city, checkin)


def get_available_routes() -> list[dict]:
    """Return a summary of routes/cities that have fresh data in the DB."""
    return crawler_db.get_available_routes()


def get_available_cities() -> list[str]:
    """Return cities that have fresh hotel data in the DB."""
    return crawler_db.get_available_cities()
