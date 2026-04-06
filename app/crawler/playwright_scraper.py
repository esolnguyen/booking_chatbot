"""Playwright-based scraper for Booking.com hotels and flights.

Strategy
--------
Hotels  – booking.com/searchresults uses stable data-testid attributes that
          survive most UI rebuilds.  We scrape one page per city (not per
          date) since hotel listings don't change day-to-day; only the booking
          URL carries the selected dates.

Flights – flights.booking.com is a React SPA that issues XHR/fetch calls to
          load results.  Primary strategy: intercept those API responses and
          parse the JSON.  Fallback: scan rendered HTML for price/time patterns.

Anti-detection
--------------
- Hides navigator.webdriver
- Realistic user-agent, viewport, locale, timezone
- Disables AutomationControlled blink feature
- Small human-like waits between actions
- Cookie-consent banners dismissed automatically
"""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    Response,
    async_playwright,
)

from app.crawler.scraper import booking_com_hotel_url, traveloka_flight_url

logger = logging.getLogger(__name__)

# ── Tuning ────────────────────────────────────────────────────────
_CONCURRENCY   = 3          # parallel pages per browser
_PAGE_TIMEOUT  = 35_000     # ms – page navigation timeout
_HOTEL_WAIT    = 12_000     # ms – wait for hotel cards
_FLIGHT_WAIT   = 18_000     # ms – flights take longer to load
_KNOWN_AIRLINES = [
    "Delta", "United", "American", "Southwest", "JetBlue", "Alaska",
    "Spirit", "Frontier", "Hawaiian", "Vietnam Airlines", "VietJet",
    "Bamboo Airways", "Singapore Airlines", "Qantas", "Emirates",
    "Cathay Pacific", "Korean Air", "ANA", "Japan Airlines", "British Airways",
    "Lufthansa", "Air France", "KLM", "Thai Airways", "Malaysia Airlines",
]

_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-US','en'] });
    Object.defineProperty(navigator, 'plugins',    { get: () => [
        { name:'Chrome PDF Plugin', filename:'internal-pdf-viewer',
          description:'Portable Document Format' }
    ]});
    window.chrome = { runtime:{}, loadTimes:()=>{}, csi:()=>{}, app:{} };
    const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origQuery(p);
"""

def _parse_price(txt: str) -> float:
    """Parse a price string handling US (1,234.56) and EU (1.234,56) formats."""
    txt = txt.replace("\xa0", "").replace("\u202f", "").strip()
    m = re.search(r"\d[\d,. ]*", txt)
    if not m:
        return 0.0
    raw = m.group().replace(" ", "").strip(".,")
    commas, dots = raw.count(","), raw.count(".")
    if commas and dots:
        if raw.rindex(",") > raw.rindex("."):   # EU: 1.234,56
            raw = raw.replace(".", "").replace(",", ".")
        else:                                    # US: 1,234.56
            raw = raw.replace(",", "")
    elif commas:
        # comma is thousands sep if exactly 3 digits follow the last comma
        raw = raw.replace(",", "") if re.search(r",\d{3}$", raw) else raw.replace(",", ".")
    elif dots:
        # dot is thousands sep if exactly 3 digits follow the last dot
        if re.search(r"\.\d{3}$", raw):
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--window-size=1920,1080",
]


# ── Browser helpers ───────────────────────────────────────────────

async def _new_browser(playwright, headless: bool = True):
    browser = await playwright.chromium.launch(
        headless=headless, args=_LAUNCH_ARGS
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
    )
    await context.add_init_script(_STEALTH_JS)
    return browser, context


async def _dismiss_consent(page: Page) -> None:
    for sel in [
        "#onetrust-accept-btn-handler",
        'button[id*="cookie"][id*="accept"]',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
        '[aria-label*="accept cookies" i]',
    ]:
        try:
            await page.click(sel, timeout=2_500)
            await page.wait_for_timeout(500)
            return
        except Exception:
            pass


# ── Hotel scraping ────────────────────────────────────────────────

async def _scrape_hotel_page(
    context: BrowserContext, city: str, checkin: date, checkout: date
) -> list[dict]:
    url = (
        f"https://www.booking.com/searchresults.en-us.html"
        f"?ss={city}&checkin={checkin.isoformat()}&checkout={checkout.isoformat()}"
        f"&group_adults=1&no_rooms=1&order=price&selected_currency=USD"
    )
    page = await context.new_page()
    hotels: list[dict] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT)
        await _dismiss_consent(page)

        # Wait for hotel cards to appear
        loaded = False
        for sel in [
            '[data-testid="property-card"]',
            '[data-testid="property-card-container"]',
            ".sr_property_block",
        ]:
            try:
                await page.wait_for_selector(sel, timeout=_HOTEL_WAIT)
                loaded = True
                break
            except Exception:
                pass

        if not loaded:
            logger.warning("Hotels: no cards rendered for %s (WAF or slow load)", city)
            return []

        await page.wait_for_timeout(1_000)  # small settle delay

        cards = await page.query_selector_all('[data-testid="property-card"]')
        if not cards:
            cards = await page.query_selector_all('[data-testid="property-card-container"]')

        logger.info("  Hotels  %-20s → %d cards found", city, len(cards))

        for i, card in enumerate(cards[:15]):
            try:
                h = await _parse_hotel_card(card, city, checkin, checkout)
                if h:
                    hotels.append(h)
            except Exception as exc:
                logger.debug("Hotel card %d parse error: %s", i, exc)

    except Exception as exc:
        logger.warning("Hotel page error (%s): %s", city, exc)
    finally:
        await page.close()
    return hotels


async def _parse_hotel_card(
    card, city: str, checkin: date, checkout: date
) -> dict | None:
    # Name
    name_el = (
        await card.query_selector('[data-testid="title"]')
        or await card.query_selector("h3")
        or await card.query_selector("h2")
    )
    if not name_el:
        return None
    name = (await name_el.inner_text()).strip()
    if not name:
        return None

    # Price per night
    price = 0.0
    for sel in [
        '[data-testid="price-and-discounted-price"]',
        '[data-testid="price"]',
        '[class*="price" i]',
    ]:
        el = await card.query_selector(sel)
        if el:
            txt = await el.inner_text()
            price = _parse_price(txt)
            if price > 0:
                break

    # Rating (Booking.com uses a 1–10 scale → normalise to 5)
    rating = 0.0
    for sel in ['[data-testid="review-score"]', '[aria-label*="Scored" i]']:
        el = await card.query_selector(sel)
        if el:
            txt = await el.inner_text()
            m = re.search(r"\d+[.,]\d+", txt.replace(",", "."))
            if m:
                score = float(m.group())
                rating = round(score / 2.0, 1) if score > 5 else round(score, 1)
                break

    # Booking URL
    href = ""
    for sel in ['[data-testid="title-link"]', 'a[href*="hotel"]', "a[href]"]:
        el = await card.query_selector(sel)
        if el:
            href = (await el.get_attribute("href")) or ""
            if href:
                break
    if href and not href.startswith("http"):
        href = "https://www.booking.com" + href
    if not href:
        href = booking_com_hotel_url(name, checkin, checkout)

    return {
        "name": name,
        "price_per_night": price,
        "rating": rating,
        "available_rooms": 99,
        "booking_url": href,
        "source": "booking.com",
    }


# ── Flight scraping ───────────────────────────────────────────────

async def _scrape_flight_page(
    context: BrowserContext,
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
) -> list[dict]:
    url = (
        f"https://flights.booking.com/flights/{origin}/{destination}/"
        f"?type=ROUNDTRIP&adults=1&cabinClass=ECONOMY"
        f"&depart={dep_date.isoformat()}&return={ret_date.isoformat()}&sort=BEST"
    )
    intercepted: list[dict] = []
    page = await context.new_page()

    async def _on_response(resp: Response) -> None:
        try:
            if resp.status != 200:
                return
            if "json" not in resp.headers.get("content-type", ""):
                return
            url_lower = resp.url.lower()
            if not any(k in url_lower for k in ("search", "result", "itinerary", "flight", "offer")):
                return
            body = await resp.json()
            parsed = _extract_flights_from_json(body, origin, destination, url)
            if parsed:
                intercepted.extend(parsed)
        except Exception:
            pass

    page.on("response", _on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT)
        await _dismiss_consent(page)
        # Flights aggregate from multiple sources — give them time
        await page.wait_for_timeout(_FLIGHT_WAIT)

        if intercepted:
            logger.info(
                "  Flights %s→%-12s %s → %d via API intercept",
                origin, destination, dep_date, len(intercepted),
            )
            return intercepted[:10]

        # HTML fallback
        flights = await _parse_flight_html(page, origin, destination, dep_date, url)
        if flights:
            logger.info(
                "  Flights %s→%-12s %s → %d via HTML",
                origin, destination, dep_date, len(flights),
            )
        else:
            logger.debug("  Flights %s→%s %s → no results", origin, destination, dep_date)
        return flights

    except Exception as exc:
        logger.warning("Flight page error (%s→%s): %s", origin, destination, exc)
        return []
    finally:
        await page.close()


def _extract_flights_from_json(
    body: Any, origin: str, destination: str, page_url: str
) -> list[dict]:
    """Recursively search a JSON blob for objects that look like flight offers."""
    found: list[dict] = []

    def _walk(node, depth: int = 0) -> None:
        if depth > 10 or len(found) >= 10:
            return
        if isinstance(node, list):
            for item in node:
                _walk(item, depth + 1)
        elif isinstance(node, dict):
            keys = set(node)
            has_price    = bool(keys & {"price","totalPrice","total","amount","fare","cost"})
            has_airline  = bool(keys & {"airline","carrier","airlineName","carrierName","validatingCarrier","marketingCarrier"})
            if has_price and has_airline:
                flight = _normalise_flight_node(node, origin, destination, page_url, len(found))
                if flight:
                    found.append(flight)
                    return
            for v in node.values():
                _walk(v, depth + 1)

    try:
        _walk(body)
    except Exception:
        pass
    return found


def _normalise_flight_node(
    node: dict, origin: str, destination: str, page_url: str, idx: int
) -> dict | None:
    airline_val = (
        node.get("airline") or node.get("carrier") or
        node.get("airlineName") or node.get("carrierName") or
        node.get("validatingCarrier") or node.get("marketingCarrier") or ""
    )
    airline = str(airline_val).strip() or "Unknown"

    price_raw = (
        node.get("price") or node.get("totalPrice") or node.get("total") or
        node.get("amount") or node.get("fare") or node.get("cost") or 0
    )
    if isinstance(price_raw, dict):
        price_raw = price_raw.get("amount") or price_raw.get("total") or price_raw.get("value") or 0
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = 0.0

    stops = int(node.get("stops", node.get("numStops", node.get("connectionCount", 1))))
    dep_time = str(node.get("departureTime") or node.get("departure") or node.get("depart") or "")[:16]
    arr_time = str(node.get("arrivalTime") or node.get("arrival") or node.get("arrive") or "")[:16]
    cabin    = str(node.get("cabinClass") or node.get("cabin") or "economy").lower()

    if price <= 0 and not airline:
        return None

    return {
        "id": f"BKG-F-{idx+1:03d}",
        "airline": airline,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "price": price,
        "stops": stops,
        "available_seats": 9,
        "cabin_class": cabin,
        "booking_url": page_url,
        "source": "booking.com",
    }


async def _parse_flight_html(
    page: Page, origin: str, destination: str, dep_date: date, page_url: str
) -> list[dict]:
    """Last-resort: pull flight data from rendered HTML."""
    cards: list = []
    for sel in [
        '[data-testid="flight-card"]',
        '[data-testid="result-card"]',
        '[class*="FlightCard" i]',
        '[class*="ItineraryCard" i]',
        '[class*="ResultCard" i]',
        'li[class*="result" i]',
    ]:
        cards = await page.query_selector_all(sel)
        if cards:
            break

    flights = []
    for i, card in enumerate(cards[:10]):
        try:
            text = await card.inner_text()
            price_m = re.search(r"\$\s*([\d,]+)", text)
            price   = float(price_m.group(1).replace(",", "")) if price_m else 0.0
            times   = re.findall(r"\b([0-2]?\d:[0-5]\d\s*(?:[AP]M)?)\b", text, re.I)
            dep_t   = times[0] if len(times) > 0 else ""
            arr_t   = times[1] if len(times) > 1 else ""
            stops   = 0 if re.search(r"direct|nonstop|non.stop", text, re.I) else 1
            airline = next(
                (a for a in _KNOWN_AIRLINES if a.lower() in text.lower()), "Unknown"
            )
            link_el = await card.query_selector("a[href]")
            href    = (await link_el.get_attribute("href") or "") if link_el else ""
            if href and not href.startswith("http"):
                href = "https://flights.booking.com" + href
            if price > 0 or airline != "Unknown":
                flights.append({
                    "id": f"BKG-F-{i+1:03d}",
                    "airline": airline,
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "departure_time": dep_t,
                    "arrival_time": arr_t,
                    "price": price,
                    "stops": stops,
                    "available_seats": 9,
                    "cabin_class": "economy",
                    "booking_url": href or page_url,
                    "source": "booking.com",
                })
        except Exception as exc:
            logger.debug("Flight HTML card %d: %s", i, exc)
    return flights


# ── Traveloka flight scraping ─────────────────────────────────────

_IDR_TO_USD = 16_200  # approximate conversion rate


def _idr_to_usd(price: float) -> float:
    """Convert IDR to USD if price looks like IDR (> 50 000)."""
    return round(price / _IDR_TO_USD, 2) if price > 50_000 else price


def _extract_traveloka_flights(
    body: Any, origin: str, destination: str, page_url: str
) -> list[dict]:
    """Recursively walk a Traveloka API response for flight offers."""
    found: list[dict] = []

    def _walk(node, depth: int = 0) -> None:
        if depth > 12 or len(found) >= 10:
            return
        if isinstance(node, list):
            for item in node:
                _walk(item, depth + 1)
        elif isinstance(node, dict):
            keys = set(node)
            has_price   = bool(keys & {"totalFare", "amount", "price", "fare",
                                       "totalAmount", "displayedPrice", "sellingPrice"})
            has_airline = bool(keys & {"airline", "airlineCode", "operatingAirline",
                                       "carrierName", "marketingAirline", "airlineName",
                                       "carrier", "flightCode"})
            if has_price and has_airline:
                f = _normalise_traveloka_node(node, origin, destination, page_url, len(found))
                if f:
                    found.append(f)
                    return
            for v in node.values():
                _walk(v, depth + 1)

    try:
        _walk(body)
    except Exception:
        pass
    return found


def _normalise_traveloka_node(
    node: dict, origin: str, destination: str, page_url: str, idx: int
) -> dict | None:
    airline = (
        node.get("airlineName") or node.get("carrierName") or
        node.get("operatingAirline") or node.get("marketingAirline") or
        node.get("airline") or node.get("carrier") or ""
    )
    airline = str(airline).strip() or "Unknown"

    # Price — Traveloka uses nested fare objects
    price_raw = (
        node.get("totalFare") or node.get("sellingPrice") or
        node.get("displayedPrice") or node.get("totalAmount") or
        node.get("amount") or node.get("price") or node.get("fare") or 0
    )
    if isinstance(price_raw, dict):
        currency = str(price_raw.get("currency", "")).upper()
        price_raw = price_raw.get("amount") or price_raw.get("value") or price_raw.get("total") or 0
    else:
        currency = ""

    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = 0.0

    if currency == "IDR" or (not currency and price > 50_000):
        price = _idr_to_usd(price)

    dep_time = str(
        node.get("departureTime") or node.get("std") or node.get("departure") or
        node.get("departureDatetime") or ""
    )[:16]
    arr_time = str(
        node.get("arrivalTime") or node.get("sta") or node.get("arrival") or
        node.get("arrivalDatetime") or ""
    )[:16]

    stops = 0
    for k in ("numberOfTransit", "transitCount", "stops", "numStops", "stopCount"):
        if k in node:
            try:
                stops = int(node[k])
                break
            except (TypeError, ValueError):
                pass

    cabin = str(node.get("cabinClass") or node.get("seatClass") or "economy").lower()

    if price <= 0 and airline == "Unknown":
        return None

    return {
        "id": f"TVK-F-{idx+1:03d}",
        "airline": airline,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "price": price,
        "stops": stops,
        "available_seats": 9,
        "cabin_class": cabin,
        "booking_url": page_url,
        "source": "traveloka",
    }


async def _scrape_traveloka_flight_page(
    context: BrowserContext,
    origin: str,
    destination: str,
    dep_date: date,
    ret_date: date,
) -> list[dict]:
    url = traveloka_flight_url(origin, destination, dep_date)
    intercepted: list[dict] = []
    page = await context.new_page()

    async def _on_response(resp: Response) -> None:
        try:
            if resp.status != 200:
                return
            if "json" not in resp.headers.get("content-type", ""):
                return
            url_lower = resp.url.lower()
            if not any(k in url_lower for k in
                       ("flight", "search", "fare", "itinerary", "avail", "offer")):
                return
            body = await resp.json()
            parsed = _extract_traveloka_flights(body, origin, destination, url)
            if parsed:
                intercepted.extend(parsed)
        except Exception:
            pass

    page.on("response", _on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT)
        await _dismiss_consent(page)
        await page.wait_for_timeout(_FLIGHT_WAIT)

        if intercepted:
            logger.info(
                "  Traveloka %s→%-10s %s → %d via API",
                origin, destination, dep_date, len(intercepted),
            )
            return intercepted[:10]

        # HTML fallback — Traveloka's class names are hashed so rely on text patterns
        flights = await _parse_traveloka_flight_html(page, origin, destination, dep_date, url)
        if flights:
            logger.info(
                "  Traveloka %s→%-10s %s → %d via HTML",
                origin, destination, dep_date, len(flights),
            )
        else:
            logger.debug("  Traveloka %s→%s %s → no results", origin, destination, dep_date)
        return flights

    except Exception as exc:
        logger.warning("Traveloka flight error (%s→%s): %s", origin, destination, exc)
        return []
    finally:
        await page.close()


async def _parse_traveloka_flight_html(
    page: Page, origin: str, destination: str, dep_date: date, page_url: str
) -> list[dict]:
    """Parse Traveloka flight results from rendered HTML using text patterns."""
    # Traveloka uses hashed CSS classes — target by role/structure instead
    cards: list = []
    for sel in [
        '[data-testid*="flight"]',
        '[data-testid*="result"]',
        '[class*="FlightCard"]',
        '[class*="ResultItem"]',
        '[class*="flight-item"]',
        'li[class]',           # each result is typically an <li>
    ]:
        cards = await page.query_selector_all(sel)
        if len(cards) >= 2:    # at least 2 to be real results
            break

    flights = []
    for i, card in enumerate(cards[:10]):
        try:
            text = await card.inner_text()
            if len(text.strip()) < 20:
                continue

            # Price — look for IDR or $ amounts
            idr_m = re.search(r"IDR\s*([\d,.]+)|Rp\.?\s*([\d,.]+)", text, re.I)
            usd_m = re.search(r"\$\s*([\d,]+)", text)
            price = 0.0
            if idr_m:
                raw = (idr_m.group(1) or idr_m.group(2) or "").replace(",", "").replace(".", "")
                price = _idr_to_usd(float(raw)) if raw else 0.0
            elif usd_m:
                price = float(usd_m.group(1).replace(",", ""))

            # Times
            times = re.findall(r"\b([0-2]?\d:[0-5]\d)\b", text)
            dep_t = times[0] if len(times) > 0 else ""
            arr_t = times[1] if len(times) > 1 else ""

            stops = 0 if re.search(r"direct|nonstop|non.stop|langsung", text, re.I) else 1
            airline = next(
                (a for a in _KNOWN_AIRLINES if a.lower() in text.lower()), "Unknown"
            )

            link_el = await card.query_selector("a[href]")
            href = (await link_el.get_attribute("href") or "") if link_el else ""
            if href and not href.startswith("http"):
                href = "https://www.traveloka.com" + href

            if price > 0 or airline != "Unknown":
                flights.append({
                    "id": f"TVK-F-{i+1:03d}",
                    "airline": airline,
                    "origin": origin.upper(),
                    "destination": destination.upper(),
                    "departure_time": dep_t,
                    "arrival_time": arr_t,
                    "price": price,
                    "stops": stops,
                    "available_seats": 9,
                    "cabin_class": "economy",
                    "booking_url": href or page_url,
                    "source": "traveloka",
                })
        except Exception as exc:
            logger.debug("Traveloka HTML card %d: %s", i, exc)
    return flights


# ── Batch entry points ────────────────────────────────────────────

async def scrape_hotels_batch(
    tasks: list[tuple[str, date, date]],   # [(city, checkin, checkout), ...]
    headless: bool = True,
) -> dict[str, list[dict]]:
    """Scrape hotels for multiple cities sharing one browser instance."""
    sem     = asyncio.Semaphore(_CONCURRENCY)
    results: dict[str, list[dict]] = {}

    async with async_playwright() as p:
        browser, context = await _new_browser(p, headless=headless)
        try:
            async def _one(city: str, checkin: date, checkout: date) -> None:
                async with sem:
                    results[city] = await _scrape_hotel_page(context, city, checkin, checkout)

            await asyncio.gather(
                *[_one(c, ci, co) for c, ci, co in tasks],
                return_exceptions=True,
            )
        finally:
            await browser.close()
    return results


async def scrape_flights_batch(
    tasks: list[tuple[str, str, date, date]],  # [(origin, dest, dep, ret), ...]
    headless: bool = True,
) -> dict[str, list[dict]]:
    """Scrape flights using Traveloka (primary) with Booking.com as fallback."""
    sem     = asyncio.Semaphore(_CONCURRENCY)
    results: dict[str, list[dict]] = {}

    async with async_playwright() as p:
        browser, context = await _new_browser(p, headless=headless)
        try:
            async def _one(origin: str, dest: str, dep: date, ret: date) -> None:
                async with sem:
                    key = f"{origin}-{dest}-{dep}"
                    flights = await _scrape_traveloka_flight_page(
                        context, origin, dest, dep, ret
                    )
                    if not flights:
                        logger.debug(
                            "Traveloka empty for %s→%s %s, trying Booking.com",
                            origin, dest, dep,
                        )
                        flights = await _scrape_flight_page(
                            context, origin, dest, dep, ret
                        )
                    results[key] = flights

            await asyncio.gather(
                *[_one(*t) for t in tasks],
                return_exceptions=True,
            )
        finally:
            await browser.close()
    return results
