"""Standalone crawler scheduler.

Each crawl cycle fetches live data from Booking.com / Traveloka via Playwright
and persists results to SQLite. Old records (> 7 days) are pruned after each run.

Routes are configured as "ORIGIN-DESTINATION" pairs in settings.crawl_route_list.
Hotel cities are derived from the unique destinations in those routes.

Usage (via crawler_service.py):
    python crawler_service.py              # run forever, interval from config
    python crawler_service.py --once       # single run then exit
    python crawler_service.py --interval 2 # override interval (minutes)
"""

import asyncio
import logging
import time
from datetime import date, timedelta

import schedule

from app.config import settings
from app.crawler import db
from app.crawler.flight_generator import generate_flights
from app.crawler.playwright_scraper import scrape_flights_batch, scrape_hotels_batch
from app.crawler.scraper import booking_com_flight_url, booking_com_hotel_url
from app.mock.seed_data import AIRPORT_TO_CITY
from app.models.option import FlightOption, HotelOption

logger = logging.getLogger(__name__)


def _parse_routes() -> list[tuple[str, str]]:
    """Parse crawl_routes config into (origin, destination) tuples."""
    routes = []
    for r in settings.crawl_route_list:
        parts = r.strip().split("-")
        if len(parts) == 2:
            routes.append((parts[0].strip().upper(), parts[1].strip().upper()))
        else:
            logger.warning("Invalid route format: %s (expected ORIGIN-DEST)", r)
    return routes


def _destination_cities(routes: list[tuple[str, str]]) -> list[str]:
    """Derive unique hotel cities from destination airport codes."""
    cities = set()
    for _, dest in routes:
        city = AIRPORT_TO_CITY.get(dest, dest)
        cities.add(city)
    return sorted(cities)


def _build_hotel_options(
    raw_list: list[dict],
    city: str,
    checkin: date,
    checkout: date,
) -> list[HotelOption]:
    """Convert raw scraped dicts to HotelOption list for a specific date pair."""
    options: list[HotelOption] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_list[:12]):
        name = raw.get("name", "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        booking_url = raw.get("booking_url") or booking_com_hotel_url(name, checkin, checkout)
        options.append(HotelOption(
            id=f"BKG-H-{i+1:03d}",
            name=name,
            city=city,
            price_per_night=raw.get("price_per_night") or 0.0,
            rating=raw.get("rating") or 0.0,
            available_rooms=raw.get("available_rooms", 99),
            amenities=[],
            booking_url=booking_url,
            source="booking.com",
        ))
    return options


def _build_flight_options(
    raw_list: list[dict],
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
) -> list[FlightOption]:
    """Convert raw scraped dicts to FlightOption list."""
    fallback_url = booking_com_flight_url(origin, destination, dep_date, ret_date)
    options: list[FlightOption] = []
    for raw in raw_list:
        options.append(FlightOption(
            id=raw["id"],
            airline=raw.get("airline", "Unknown"),
            origin=raw.get("origin", origin.upper()),
            destination=raw.get("destination", destination.upper()),
            departure_time=raw.get("departure_time", ""),
            arrival_time=raw.get("arrival_time", ""),
            price=raw.get("price", 0.0),
            stops=raw.get("stops", 0),
            available_seats=raw.get("available_seats", 9),
            cabin_class=raw.get("cabin_class", "economy"),
            booking_url=raw.get("booking_url") or fallback_url,
            source="booking.com",
        ))
    return options


async def _run_crawl_async(departure_dates: list[date]) -> tuple[int, int]:
    """Run all hotel and flight scraping in parallel using shared browser instances."""
    stay = settings.crawl_stay_duration_days
    routes = _parse_routes()
    cities = _destination_cities(routes)

    # -- Hotels --
    first_dep = departure_dates[0]
    first_ret = first_dep + timedelta(days=stay)
    hotel_tasks = [(city, first_dep, first_ret) for city in cities]

    hotels_total = 0
    logger.info("Hotel batch: %d cities via 1 Playwright browser", len(hotel_tasks))
    try:
        hotel_map = await scrape_hotels_batch(hotel_tasks)
        for city in cities:
            raw_list = hotel_map.get(city, [])
            if not raw_list:
                logger.debug("  No hotel data for %s", city)
                continue
            for dep_date in departure_dates:
                checkout = dep_date + timedelta(days=stay)
                options = _build_hotel_options(raw_list, city, dep_date, checkout)
                if options:
                    count = db.upsert_hotels(options, dep_date, checkout)
                    hotels_total += count
            logger.info(
                "  Hotels  %-20s : %d options x %d dates saved",
                city, len(_build_hotel_options(raw_list, city, first_dep, first_ret)),
                len(departure_dates),
            )
    except Exception as exc:
        logger.error("Hotel batch failed: %s", exc, exc_info=True)

    # -- Flights --
    flight_tasks: list[tuple[str, str, date, date]] = [
        (orig, dest, dep_date, dep_date + timedelta(days=stay))
        for dep_date in departure_dates
        for orig, dest in routes
    ]

    flights_total = 0
    logger.info("Flight batch: %d route x date combinations", len(flight_tasks))
    scraped_map: dict = {}
    try:
        scraped_map = await scrape_flights_batch(flight_tasks)
    except Exception as exc:
        logger.warning("Flight scrape failed, falling back to generator: %s", exc)

    for orig, dest, dep_date, ret_date in flight_tasks:
        key = f"{orig}-{dest}-{dep_date}"
        raw_list = scraped_map.get(key, [])

        if raw_list:
            options = _build_flight_options(raw_list, orig, dest, dep_date, ret_date)
        else:
            options = generate_flights(orig, dest, dep_date, ret_date)
            if options:
                logger.debug(
                    "  Flights %s->%s %s : generated %d (no live data)",
                    orig, dest, dep_date, len(options),
                )

        if options:
            count = db.upsert_flights(options, dep_date, ret_date)
            flights_total += count
            logger.info(
                "  Flights %s->%-6s %s : %d saved (source: %s)",
                orig, dest, dep_date, count, options[0].source,
            )

    return hotels_total, flights_total


def run_crawl() -> dict:
    """One full crawl cycle -- all routes x next 7 departure dates."""
    today = date.today()
    departure_dates = [today + timedelta(days=d) for d in range(1, 8)]
    routes = _parse_routes()

    logger.info(
        "=== Crawl started | dates %s to %s | %d routes ===",
        departure_dates[0], departure_dates[-1], len(routes),
    )

    log_id = db.start_crawl_log()
    hotels_total = 0
    flights_total = 0
    error_msg = None

    try:
        hotels_total, flights_total = asyncio.run(_run_crawl_async(departure_dates))
        db.cleanup_old_data(days=7)
        db.finish_crawl_log(log_id, "success", hotels_total, flights_total)
        logger.info(
            "=== Crawl finished: %d hotels, %d flights saved ===",
            hotels_total, flights_total,
        )
    except Exception as exc:
        error_msg = str(exc)
        db.finish_crawl_log(log_id, "error", hotels_total, flights_total, error_msg)
        logger.error("=== Crawl failed: %s ===", error_msg)

    return {
        "hotels": hotels_total,
        "flights": flights_total,
        "dates": [d.isoformat() for d in departure_dates],
        "error": error_msg,
    }


def start(interval_minutes: int | None = None, run_once: bool = False) -> None:
    """Initialise DB, run one crawl immediately, then loop on schedule."""
    db.init_db()
    interval = interval_minutes or settings.crawl_interval_minutes
    routes = _parse_routes()
    logger.info(
        "Crawler starting | interval=%d min | %d routes",
        interval, len(routes),
    )

    run_crawl()

    if run_once:
        logger.info("--once flag set, exiting.")
        return

    schedule.every(interval).minutes.do(run_crawl)
    logger.info("Scheduler active. Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(15)
    except KeyboardInterrupt:
        logger.info("Crawler stopped.")
