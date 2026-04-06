"""OTA URL builders and public fetch API.

Actual scraping is delegated to playwright_scraper.py.
This module exposes the URL builders (always useful regardless of scraping
outcome) and the synchronous fetch_hotels / fetch_flights wrappers that
the scheduler and inventory_api call.
"""

import asyncio
import logging
import time
from datetime import date
from urllib.parse import quote_plus

from app.models.option import FlightOption, HotelOption

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds
_cache: dict[str, tuple[list, float]] = {}


def _cached(key: str) -> list | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _set_cache(key: str, value: list) -> None:
    _cache[key] = (value, time.time() + _CACHE_TTL)


# ── URL builders ─────────────────────────────────────────────────

def agoda_hotel_search_url(city: str, checkin: date, checkout: date) -> str:
    return (
        f"https://www.agoda.com/search?q={quote_plus(city)}"
        f"&checkIn={checkin.isoformat()}&checkOut={checkout.isoformat()}"
        f"&adults=1&rooms=1&currency=USD"
    )


def agoda_hotel_url(hotel_name: str, city: str, checkin: date, checkout: date) -> str:
    return (
        f"https://www.agoda.com/search?q={quote_plus(hotel_name + ' ' + city)}"
        f"&checkIn={checkin.isoformat()}&checkOut={checkout.isoformat()}"
        f"&adults=1&rooms=1&currency=USD"
    )


def booking_com_hotel_url(hotel_name: str, checkin: date, checkout: date) -> str:
    return (
        f"https://www.booking.com/search.html?ss={quote_plus(hotel_name)}"
        f"&checkin={checkin.isoformat()}&checkout={checkout.isoformat()}"
        f"&group_adults=1&no_rooms=1"
    )


def traveloka_hotel_url(city: str, checkin: date, checkout: date) -> str:
    nights = max(1, (checkout - checkin).days)
    return (
        f"https://www.traveloka.com/en-id/hotel/search"
        f"?spec={quote_plus(city)}"
        f"&checkInDate={checkin.strftime('%d/%m/%Y')}"
        f"&duration={nights}&adult=1"
    )


def traveloka_flight_url(origin: str, destination: str, dep: date) -> str:
    return (
        f"https://www.traveloka.com/en-en/flight/search"
        f"?spec={origin}.{destination}.{dep.strftime('%Y%m%d')}.1.0.0.ECONOMY"
    )


def kayak_flight_url(origin: str, destination: str, dep: date, ret: date) -> str:
    return (
        f"https://www.kayak.com/flights/{origin}-{destination}"
        f"/{dep.isoformat()}/{ret.isoformat()}"
    )


def google_flights_url(origin: str, destination: str, dep: date, ret: date) -> str:
    return (
        f"https://www.google.com/travel/flights/search"
        f"?q=flights+from+{origin}+to+{destination}"
        f"+{dep.isoformat()}+returning+{ret.isoformat()}"
    )


def skyscanner_flight_url(origin: str, destination: str, dep: date, ret: date) -> str:
    return (
        f"https://www.skyscanner.com/transport/flights"
        f"/{origin.lower()}/{destination.lower()}"
        f"/{dep.strftime('%y%m%d')}/{ret.strftime('%y%m%d')}/"
    )


def booking_com_flight_url(
    origin: str, destination: str, dep: date, ret: date
) -> str:
    return (
        f"https://flights.booking.com/flights/{origin}/{destination}/"
        f"?type=ROUNDTRIP&adults=1&cabinClass=ECONOMY"
        f"&depart={dep.isoformat()}&return={ret.isoformat()}&sort=BEST"
    )


def get_hotel_search_urls(city: str, checkin: date, checkout: date) -> dict[str, str]:
    return {
        "Booking.com": (
            f"https://www.booking.com/searchresults.en-us.html"
            f"?ss={quote_plus(city)}"
            f"&checkin={checkin.isoformat()}&checkout={checkout.isoformat()}"
            f"&group_adults=1"
        ),
        "Agoda": agoda_hotel_search_url(city, checkin, checkout),
        "Traveloka": traveloka_hotel_url(city, checkin, checkout),
    }


def get_flight_search_urls(
    origin: str, destination: str, dep: date, ret: date
) -> dict[str, str]:
    return {
        "Traveloka": traveloka_flight_url(origin, destination, dep),
        "Booking.com Flights": booking_com_flight_url(origin, destination, dep, ret),
        "Google Flights": google_flights_url(origin, destination, dep, ret),
        "Kayak": kayak_flight_url(origin, destination, dep, ret),
        "Skyscanner": skyscanner_flight_url(origin, destination, dep, ret),
    }


# ── Public fetch API ─────────────────────────────────────────────

def fetch_hotels(
    city: str,
    checkin: date,
    checkout: date,
    fallback: list[HotelOption],
) -> list[HotelOption]:
    """
    Scrape hotels from Booking.com via Playwright.
    Returns enriched HotelOption list; falls back to URL-enriched fallback on failure.
    """
    from app.crawler.playwright_scraper import scrape_hotels_batch

    cache_key = f"pw_hotel:{city}:{checkin}:{checkout}"
    if (hit := _cached(cache_key)) is not None:
        return hit  # type: ignore[return-value]

    try:
        raw_map = asyncio.run(scrape_hotels_batch([(city, checkin, checkout)]))
        scraped = raw_map.get(city, [])
    except RuntimeError:
        # Already inside an event loop (shouldn't happen in scheduler, but guard)
        scraped = []

    result: list[HotelOption] = []
    seen: set[str] = set()
    city_name = fallback[0].city if fallback else city

    for i, raw in enumerate(scraped[:12]):
        name = raw["name"].strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        result.append(HotelOption(
            id=f"BKG-H-{i+1:03d}",
            name=name,
            city=city_name,
            price_per_night=raw.get("price_per_night") or 0.0,
            rating=raw.get("rating") or 0.0,
            available_rooms=raw.get("available_rooms", 99),
            amenities=[],
            booking_url=raw.get("booking_url", ""),
            source="booking.com",
        ))

    _set_cache(cache_key, result)
    return result


def fetch_flights(
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
    fallback: list[FlightOption],
) -> list[FlightOption]:
    """
    Scrape flights from flights.booking.com via Playwright.
    Returns enriched FlightOption list; returns [] if scraping yields nothing
    (caller handles empty → OTA search links shown in chatbot).
    """
    from app.crawler.playwright_scraper import scrape_flights_batch

    cache_key = f"pw_flight:{origin}:{destination}:{dep_date}:{ret_date}"
    if (hit := _cached(cache_key)) is not None:
        return hit  # type: ignore[return-value]

    booking_url = booking_com_flight_url(origin, destination, dep_date, ret_date)

    try:
        raw_map = asyncio.run(
            scrape_flights_batch([(origin, destination, dep_date, ret_date)])
        )
        key     = f"{origin}-{destination}-{dep_date}"
        scraped = raw_map.get(key, [])
    except RuntimeError:
        scraped = []

    result: list[FlightOption] = []
    for raw in scraped:
        result.append(FlightOption(
            id=raw["id"],
            airline=raw["airline"],
            origin=raw["origin"],
            destination=raw["destination"],
            departure_time=raw.get("departure_time", ""),
            arrival_time=raw.get("arrival_time", ""),
            price=raw.get("price", 0.0),
            stops=raw.get("stops", 0),
            available_seats=raw.get("available_seats", 9),
            cabin_class=raw.get("cabin_class", "economy"),
            booking_url=raw.get("booking_url", booking_url),
            source="booking.com",
        ))

    _set_cache(cache_key, result)
    return result
