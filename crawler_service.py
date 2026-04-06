"""Standalone crawler service entry point.

Usage:
    python crawler_service.py                # run forever (interval from config)
    python crawler_service.py --once         # one crawl then exit
    python crawler_service.py --interval 2  # override interval in minutes
    python crawler_service.py --status      # print recent crawl logs and exit
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on path when run directly
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from app.crawler import db, scheduler


def _print_status() -> None:
    db.init_db()
    logs = db.get_recent_crawl_logs(limit=10)
    if not logs:
        print("No crawl runs recorded yet.")
        return
    print(f"{'ID':>4}  {'Started':^19}  {'Status':^10}  {'Hotels':>6}  {'Flights':>7}  Error")
    print("-" * 80)
    for row in logs:
        err = (row["error"] or "")[:30]
        print(
            f"{row['id']:>4}  {(row['started_at'] or '')[:19]:^19}  "
            f"{(row['status'] or ''):^10}  {row['hotels_found']:>6}  "
            f"{row['flights_found']:>7}  {err}"
        )

    hotels = db.get_all_hotels()
    print(f"\nTotal hotels in DB: {len(hotels)}")
    cities = sorted({h.city for h in hotels})
    for city in cities:
        city_hotels = [h for h in hotels if h.city == city]
        print(f"  {city}: {len(city_hotels)} hotels")


def main() -> None:
    parser = argparse.ArgumentParser(description="Travel booking crawler service")
    parser.add_argument("--once", action="store_true", help="Run one crawl then exit")
    parser.add_argument("--interval", type=int, default=None, help="Crawl interval in minutes")
    parser.add_argument("--status", action="store_true", help="Print recent crawl logs and exit")
    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    scheduler.start(interval_minutes=args.interval, run_once=args.once)


if __name__ == "__main__":
    main()
