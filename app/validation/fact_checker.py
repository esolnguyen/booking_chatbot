"""Deterministic fact checker -- validates response claims against inventory data.

Extracts prices, airline names, hotel names, and times from the LLM response
and cross-checks them against the actual flight/hotel records that were passed
as grounding context.
"""

import re

from app.models.option import FlightOption, HotelOption


def check_prices(
    response: str,
    flights: list[FlightOption],
    hotels: list[HotelOption],
) -> tuple[float, list[str]]:
    """Check that every dollar amount in the response matches a known value.

    Returns (score, issues) where score is 1.0 if all prices match, decreasing
    by 0.2 per mismatch.
    """
    price_matches = re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", response)
    if not price_matches:
        return 1.0, []

    known_prices: set[float] = set()
    for f in flights:
        known_prices.add(f.price)
    for h in hotels:
        known_prices.add(h.price_per_night)
    # Also accept totals for multi-night stays (2-14 nights)
    for h in hotels:
        for nights in range(2, 15):
            known_prices.add(round(h.price_per_night * nights, 2))
    # Accept flight+hotel combos
    for f in flights:
        for h in hotels:
            for nights in range(1, 15):
                known_prices.add(round(f.price + h.price_per_night * nights, 2))

    issues = []
    for match in price_matches:
        val = float(match.replace(",", ""))
        # Allow $1 tolerance for rounding
        if not any(abs(val - kp) <= 1.0 for kp in known_prices):
            issues.append(f"Price ${val} not found in inventory data")

    penalty = min(len(issues) * 0.2, 1.0)
    return max(0.0, 1.0 - penalty), issues


def check_airlines(
    response: str,
    flights: list[FlightOption],
) -> tuple[float, list[str]]:
    """Check that airline names mentioned in the response exist in the data."""
    if not flights:
        return 1.0, []

    known_airlines = {f.airline.lower() for f in flights}

    # Common airline names to look for in text
    airline_pattern = re.compile(
        r"(?:Qantas|United(?: Airlines)?|Air New Zealand|Vietnam Airlines|"
        r"Cathay Pacific|Korean Air|Japan Airlines|Singapore Airlines|"
        r"VietJet|Bamboo Airways|Emirates|ANA|Delta|American|Southwest|"
        r"JetBlue|Alaska|Spirit|Frontier|Hawaiian|Lufthansa|Air France|"
        r"KLM|Thai Airways|Malaysia Airlines|British Airways)",
        re.IGNORECASE,
    )
    mentioned = airline_pattern.findall(response)
    if not mentioned:
        return 1.0, []

    issues = []
    for name in mentioned:
        # Normalize: "United" should match "United Airlines"
        name_lower = name.lower()
        matched = any(
            name_lower in airline or airline in name_lower
            for airline in known_airlines
        )
        if not matched:
            issues.append(f"Airline '{name}' not found in flight data")

    penalty = min(len(issues) * 0.25, 1.0)
    return max(0.0, 1.0 - penalty), issues


def check_hotel_names(
    response: str,
    hotels: list[HotelOption],
) -> tuple[float, list[str]]:
    """Check that hotel names mentioned in the response exist in the data."""
    if not hotels:
        return 1.0, []

    known_names = {h.name.lower() for h in hotels}

    issues = []
    # Look for patterns like "[Book ...]" or hotel names that appear in the data
    # Check each known hotel name -- if the response mentions something similar
    # to a hotel name but gets it wrong, flag it.
    # Also check for any "Hotel X" / "X Hotel" / "X Hostel" patterns
    hotel_pattern = re.compile(
        r"(?:(?:Hotel|Hostel|Inn|House|Homestay|Backpackers?|Lodge|Motel|Resort)"
        r"\s+[\w\s&'-]+|[\w\s&'-]+\s+"
        r"(?:Hotel|Hostel|Inn|House|Homestay|Backpackers?|Lodge|Motel|Resort))",
        re.IGNORECASE,
    )
    mentioned = hotel_pattern.findall(response)
    if not mentioned:
        return 1.0, []

    for name in mentioned:
        name_clean = name.strip().lower()
        if len(name_clean) < 5:
            continue
        # Fuzzy match: check if any known hotel name contains or is contained
        matched = any(
            name_clean in known or known in name_clean
            for known in known_names
        )
        if not matched:
            issues.append(f"Hotel '{name.strip()}' not found in hotel data")

    penalty = min(len(issues) * 0.25, 1.0)
    return max(0.0, 1.0 - penalty), issues


def check_times(
    response: str,
    flights: list[FlightOption],
) -> tuple[float, list[str]]:
    """Check that departure/arrival times in the response match flight data."""
    if not flights:
        return 1.0, []

    # Extract times from response (HH:MM format, with optional AM/PM)
    time_pattern = re.compile(r"\b(\d{1,2}:\d{2})\s*(?:AM|PM)?\b", re.IGNORECASE)
    mentioned_times = time_pattern.findall(response)
    if not mentioned_times:
        return 1.0, []

    known_times: set[str] = set()
    for f in flights:
        for t_str in (f.departure_time, f.arrival_time):
            if not t_str:
                continue
            # Handle "2026-04-10T08:00" or plain "08:00"
            if "T" in t_str:
                t_str = t_str.split("T")[1]
            t_short = t_str[:5]
            if len(t_short) == 5:
                known_times.add(t_short)

    issues = []
    for t in mentioned_times:
        # Normalize to HH:MM
        t_norm = t.zfill(5) if len(t) == 4 else t
        if t_norm not in known_times:
            issues.append(f"Time {t} not found in flight schedules")

    penalty = min(len(issues) * 0.15, 1.0)
    return max(0.0, 1.0 - penalty), issues


def run_deterministic_checks(
    response: str,
    flights: list[FlightOption],
    hotels: list[HotelOption],
) -> tuple[float, list[str]]:
    """Run all deterministic checks and return (score, all_issues).

    Score is weighted:
      - prices:  35% (most important -- wrong price is misleading)
      - airlines: 25%
      - hotels:  20%
      - times:   20%
    """
    price_score, price_issues = check_prices(response, flights, hotels)
    airline_score, airline_issues = check_airlines(response, flights)
    hotel_score, hotel_issues = check_hotel_names(response, hotels)
    time_score, time_issues = check_times(response, flights)

    weighted = (
        0.35 * price_score
        + 0.25 * airline_score
        + 0.20 * hotel_score
        + 0.20 * time_score
    )

    all_issues = price_issues + airline_issues + hotel_issues + time_issues
    return round(weighted, 3), all_issues
