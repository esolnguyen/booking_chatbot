"""SQLite persistence for crawled hotel and flight data."""

import json
import logging
import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from app.models.option import FlightOption, HotelOption

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "crawler.db"

# Data older than this is considered stale and re-crawled
FRESH_HOURS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hotels (
    id              TEXT NOT NULL,
    city            TEXT NOT NULL,
    name            TEXT NOT NULL,
    price_per_night REAL    DEFAULT 0,
    rating          REAL    DEFAULT 0,
    available_rooms INTEGER DEFAULT 99,
    amenities       TEXT    DEFAULT '[]',
    booking_url     TEXT    DEFAULT '',
    source          TEXT    DEFAULT 'scraped',
    checkin         TEXT,
    checkout        TEXT,
    crawled_at      TEXT NOT NULL,
    PRIMARY KEY (id, city, checkin)
);

CREATE TABLE IF NOT EXISTS flights (
    id              TEXT NOT NULL,
    origin          TEXT NOT NULL,
    destination     TEXT NOT NULL,
    airline         TEXT,
    departure_time  TEXT,
    arrival_time    TEXT,
    price           REAL    DEFAULT 0,
    stops           INTEGER DEFAULT 0,
    available_seats INTEGER DEFAULT 0,
    cabin_class     TEXT    DEFAULT 'economy',
    booking_url     TEXT    DEFAULT '',
    source          TEXT    DEFAULT 'scraped',
    dep_date        TEXT,
    ret_date        TEXT,
    crawled_at      TEXT NOT NULL,
    PRIMARY KEY (id, origin, destination, dep_date)
);

CREATE TABLE IF NOT EXISTS crawl_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT DEFAULT 'running',
    hotels_found  INTEGER DEFAULT 0,
    flights_found INTEGER DEFAULT 0,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_hotels_city_checkin ON hotels(city, checkin);
CREATE INDEX IF NOT EXISTS idx_hotels_crawled      ON hotels(crawled_at);
CREATE INDEX IF NOT EXISTS idx_flights_route_date  ON flights(origin, destination, dep_date);
CREATE INDEX IF NOT EXISTS idx_flights_crawled     ON flights(crawled_at);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    logger.info("DB ready at %s", DB_PATH)


# ── Hotels ───────────────────────────────────────────────────────

def upsert_hotels(
    hotels: list[HotelOption],
    checkin: date | None = None,
    checkout: date | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            h.id, h.city, h.name,
            h.price_per_night, h.rating, h.available_rooms,
            json.dumps(h.amenities), h.booking_url, h.source,
            checkin.isoformat() if checkin else None,
            checkout.isoformat() if checkout else None,
            now,
        )
        for h in hotels
    ]
    with _conn() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO hotels
              (id, city, name, price_per_night, rating, available_rooms,
               amenities, booking_url, source, checkin, checkout, crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_fresh_hotels(
    city: str,
    checkin: date | None = None,
    max_age_hours: int = FRESH_HOURS,
) -> list[HotelOption]:
    """Return hotels crawled within max_age_hours for the given city and checkin date."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with _conn() as conn:
        if checkin:
            rows = conn.execute(
                """
                SELECT * FROM hotels
                WHERE city = ? AND checkin = ? AND crawled_at >= ?
                """,
                (city, checkin.isoformat(), cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hotels WHERE city = ? AND crawled_at >= ?",
                (city, cutoff),
            ).fetchall()
    return [_row_to_hotel(r) for r in rows]


def get_all_hotels(city: str | None = None) -> list[HotelOption]:
    with _conn() as conn:
        if city:
            rows = conn.execute(
                "SELECT * FROM hotels WHERE city = ?", (city,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM hotels").fetchall()
    return [_row_to_hotel(r) for r in rows]


def _row_to_hotel(row: sqlite3.Row) -> HotelOption:
    return HotelOption(
        id=row["id"],
        name=row["name"],
        city=row["city"],
        price_per_night=row["price_per_night"] or 0.0,
        rating=row["rating"] or 0.0,
        available_rooms=row["available_rooms"] or 99,
        amenities=json.loads(row["amenities"] or "[]"),
        booking_url=row["booking_url"] or "",
        source=row["source"] or "db",
    )


# ── Flights ──────────────────────────────────────────────────────

def upsert_flights(
    flights: list[FlightOption],
    dep_date: date | None = None,
    ret_date: date | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            f.id, f.origin, f.destination, f.airline,
            f.departure_time, f.arrival_time, f.price,
            f.stops, f.available_seats, f.cabin_class,
            f.booking_url, f.source,
            dep_date.isoformat() if dep_date else None,
            ret_date.isoformat() if ret_date else None,
            now,
        )
        for f in flights
    ]
    with _conn() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO flights
              (id, origin, destination, airline, departure_time, arrival_time,
               price, stops, available_seats, cabin_class, booking_url, source,
               dep_date, ret_date, crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_fresh_flights(
    origin: str,
    destination: str,
    dep_date: date | None = None,
    max_age_hours: int = FRESH_HOURS,
) -> list[FlightOption]:
    """Return flights crawled within max_age_hours for the given route and date."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    with _conn() as conn:
        if dep_date:
            rows = conn.execute(
                """
                SELECT * FROM flights
                WHERE origin = ? AND destination = ?
                  AND dep_date = ? AND crawled_at >= ?
                """,
                (origin.upper(), destination.upper(), dep_date.isoformat(), cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM flights
                WHERE origin = ? AND destination = ? AND crawled_at >= ?
                """,
                (origin.upper(), destination.upper(), cutoff),
            ).fetchall()
    return [_row_to_flight(r) for r in rows]


def _row_to_flight(row: sqlite3.Row) -> FlightOption:
    return FlightOption(
        id=row["id"],
        airline=row["airline"] or "",
        origin=row["origin"],
        destination=row["destination"],
        departure_time=row["departure_time"] or "",
        arrival_time=row["arrival_time"] or "",
        price=row["price"] or 0.0,
        stops=row["stops"] or 0,
        available_seats=row["available_seats"] or 0,
        cabin_class=row["cabin_class"] or "economy",
        booking_url=row["booking_url"] or "",
        source=row["source"] or "db",
    )


# ── Availability queries ──────────────────────────────────────────

def get_available_routes() -> list[dict]:
    """Return distinct routes that have fresh flight data."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT origin, destination, dep_date
            FROM flights
            WHERE crawled_at >= ?
            ORDER BY origin, destination, dep_date
            """,
            (cutoff,),
        ).fetchall()
    return [{"origin": r["origin"], "destination": r["destination"], "date": r["dep_date"]} for r in rows]


def get_available_cities() -> list[str]:
    """Return distinct cities that have fresh hotel data."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT city FROM hotels WHERE crawled_at >= ?",
            (cutoff,),
        ).fetchall()
    return [r["city"] for r in rows]


# ── Cleanup ──────────────────────────────────────────────────────

def cleanup_old_data(days: int = 7) -> tuple[int, int]:
    """Delete hotel and flight records older than `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as conn:
        h = conn.execute(
            "DELETE FROM hotels WHERE crawled_at < ?", (cutoff,)
        ).rowcount
        f = conn.execute(
            "DELETE FROM flights WHERE crawled_at < ?", (cutoff,)
        ).rowcount
    if h or f:
        logger.info("Cleanup: removed %d hotels, %d flights older than %d days", h, f, days)
    return h, f


# ── Crawl log ────────────────────────────────────────────────────

def start_crawl_log() -> int:
    with _conn() as conn:
        return conn.execute(
            "INSERT INTO crawl_log (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        ).lastrowid


def finish_crawl_log(
    log_id: int,
    status: str,
    hotels_found: int = 0,
    flights_found: int = 0,
    error: str | None = None,
) -> None:
    with _conn() as conn:
        conn.execute(
            """
            UPDATE crawl_log
            SET finished_at=?, status=?, hotels_found=?, flights_found=?, error=?
            WHERE id=?
            """,
            (datetime.now(timezone.utc).isoformat(), status, hotels_found, flights_found, error, log_id),
        )


def get_recent_crawl_logs(limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
