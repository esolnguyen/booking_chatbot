"""Realistic synthetic flight data generator.

Generates plausible flight options per route based on real airlines, realistic
price ranges, and typical flight durations. Used as a fallback when live
scraping yields no results. Prices vary slightly each run to simulate live data.

Booking URLs point to real OTA search pages so users can book actual flights.
"""

import hashlib
import random
from datetime import date, datetime, timedelta

from app.crawler.scraper import (
    traveloka_flight_url,
)
from app.models.option import FlightOption

_ROUTES: dict[tuple[str, str], dict] = {
    ("SGN", "SYD"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
            ("Singapore Airlines","SQ", 1),
            ("Qantas",           "QF", 1),
        ],
        "price_range": (350, 850),
        "duration_h": 9.5,
        "dep_times": ["00:05", "07:30", "10:15", "22:45"],
    },
    ("SYD", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
            ("Singapore Airlines","SQ", 1),
            ("Qantas",           "QF", 1),
        ],
        "price_range": (350, 850),
        "duration_h": 10.0,
        "dep_times": ["01:20", "08:00", "11:30", "19:50"],
    },
    ("SGN", "NRT"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 0),
            ("VietJet",          "VJ", 0),
            ("Japan Airlines",   "JL", 1),
            ("ANA",              "NH", 1),
        ],
        "price_range": (280, 700),
        "duration_h": 5.5,
        "dep_times": ["00:30", "07:00", "12:30", "23:15"],
    },
    ("NRT", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 0),
            ("VietJet",          "VJ", 0),
            ("Japan Airlines",   "JL", 1),
            ("ANA",              "NH", 1),
        ],
        "price_range": (280, 700),
        "duration_h": 6.0,
        "dep_times": ["09:30", "14:00", "17:45", "22:00"],
    },
    # Additional common routes
    ("SGN", "MEL"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
            ("Singapore Airlines","SQ", 1),
        ],
        "price_range": (380, 900),
        "duration_h": 10.0,
        "dep_times": ["00:15", "08:00", "21:30"],
    },
    ("MEL", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
            ("Singapore Airlines","SQ", 1),
        ],
        "price_range": (380, 900),
        "duration_h": 10.5,
        "dep_times": ["02:00", "09:30", "20:00"],
    },
    ("SGN", "BNE"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
        ],
        "price_range": (400, 950),
        "duration_h": 9.0,
        "dep_times": ["01:00", "09:00"],
    },
    ("BNE", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Jetstar",          "JQ", 1),
        ],
        "price_range": (400, 950),
        "duration_h": 9.5,
        "dep_times": ["08:00", "21:00"],
    },
    ("SGN", "HAN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 0),
            ("VietJet",          "VJ", 0),
            ("Bamboo Airways",   "QH", 0),
        ],
        "price_range": (40, 120),
        "duration_h": 2.0,
        "dep_times": ["06:00", "09:30", "14:00", "18:30", "21:00"],
    },
    ("HAN", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 0),
            ("VietJet",          "VJ", 0),
            ("Bamboo Airways",   "QH", 0),
        ],
        "price_range": (40, 120),
        "duration_h": 2.0,
        "dep_times": ["05:30", "08:00", "12:00", "16:30", "20:00"],
    },
    ("SFO", "SYD"): {
        "airlines": [
            ("Qantas",          "QF", 0),
            ("United Airlines", "UA", 1),
            ("Air New Zealand", "NZ", 1),
        ],
        "price_range": (820, 1450),
        "duration_h": 14.5,
        "dep_times": ["10:30", "13:45", "21:00"],
    },
    ("SFO", "SGN"): {
        "airlines": [
            ("Vietnam Airlines", "VN", 1),
            ("Cathay Pacific",   "CX", 1),
            ("Korean Air",       "KE", 1),
            ("Japan Airlines",   "JL", 1),
        ],
        "price_range": (620, 1200),
        "duration_h": 19.5,
        "dep_times": ["08:00", "11:30", "23:45"],
    },
    ("SFO", "NRT"): {
        "airlines": [
            ("Japan Airlines", "JL", 0),
            ("ANA",            "NH", 0),
            ("United Airlines","UA", 0),
        ],
        "price_range": (550, 1100),
        "duration_h": 11.0,
        "dep_times": ["10:00", "13:30", "15:45"],
    },
}


def _price_for_date(base_lo: int, base_hi: int, dep_date: date, seed_str: str) -> float:
    """Deterministic but date-varying price within the range."""
    seed = int(hashlib.md5(f"{seed_str}{dep_date.isoformat()}".encode()).hexdigest(), 16)
    rng = random.Random(seed)
    base = rng.randint(base_lo, base_hi)
    jitter = rng.uniform(-0.05, 0.05)
    return round(base * (1 + jitter), 2)


def _add_hours(dep_time_str: str, hours: float) -> str:
    """Add duration to a HH:MM departure time -> HH:MM arrival (next-day ignored)."""
    try:
        h, m = map(int, dep_time_str.split(":"))
        dt = datetime(2000, 1, 1, h, m) + timedelta(hours=hours)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def generate_flights(
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
) -> list[FlightOption]:
    """Return synthetic FlightOption list for the given route and date."""
    key = (origin.upper(), destination.upper())

    if key not in _ROUTES:
        return []

    cfg = _ROUTES[key]
    airlines = cfg["airlines"]
    lo, hi = cfg["price_range"]
    dur_h = cfg["duration_h"]
    dep_times = cfg["dep_times"]

    o, d = key
    booking_url = traveloka_flight_url(o, d, dep_date)

    options: list[FlightOption] = []
    for i, (airline_name, airline_code, stops) in enumerate(airlines):
        dep_t = dep_times[i % len(dep_times)]
        arr_t = _add_hours(dep_t, dur_h)
        price = _price_for_date(lo, hi, dep_date, f"{o}{d}{airline_code}")

        options.append(FlightOption(
            id=f"GEN-F-{o}{d}-{i+1:02d}",
            airline=airline_name,
            origin=o,
            destination=d,
            departure_time=f"{dep_date.isoformat()}T{dep_t}",
            arrival_time=arr_t,
            price=price,
            stops=stops,
            available_seats=random.randint(3, 9),
            cabin_class="economy",
            booking_url=booking_url,
            source="generated",
        ))

    return options


def supported_routes() -> list[tuple[str, str]]:
    return list(_ROUTES.keys())
