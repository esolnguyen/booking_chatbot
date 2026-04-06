"""Airport and city mappings used by the crawler and inventory API."""

# Airport code -> city name
AIRPORT_TO_CITY = {
    "NRT": "Tokyo",      "HND": "Tokyo",
    "LHR": "London",     "LGW": "London",
    "SIN": "Singapore",
    "JFK": "New York",   "LGA": "New York",   "EWR": "New York",
    "BKK": "Bangkok",
    "SYD": "Sydney",
    "MEL": "Melbourne",  "AVV": "Melbourne",
    "BNE": "Brisbane",
    "SGN": "Ho Chi Minh City",
    "HAN": "Hanoi",
    "SFO": "San Francisco", "OAK": "San Francisco",
    "ICN": "Seoul",      "GMP": "Seoul",
    "KIX": "Osaka",
    "DAD": "Da Nang",
    "PQC": "Phu Quoc",
}

CITY_TO_AIRPORTS: dict[str, list[str]] = {}
for _code, _city in AIRPORT_TO_CITY.items():
    CITY_TO_AIRPORTS.setdefault(_city, []).append(_code)

# Common city name aliases -> canonical city name
CITY_ALIASES = {
    "saigon": "Ho Chi Minh City",
    "hcmc": "Ho Chi Minh City",
    "ho chi minh": "Ho Chi Minh City",
    "hochiminh": "Ho Chi Minh City",
    "hanoi": "Hanoi",
    "ha noi": "Hanoi",
    "da nang": "Da Nang",
    "danang": "Da Nang",
    "phu quoc": "Phu Quoc",
    "phuquoc": "Phu Quoc",
}
