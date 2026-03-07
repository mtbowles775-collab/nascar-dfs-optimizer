# ============================================================
# scheduler.py
# APScheduler — fires qualifying scraper on race weekends
# Runs inside the FastAPI process on Railway
#
# Race results + loop data: loaded post-race via Racing Reference
#   browser console script (see Admin tab in frontend).
# Salaries: loaded via DK/FD browser console scripts (Admin tab).
# Qualifying: auto-scraped from NASCAR API (below).
# ============================================================

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import date
import logging
from database import SessionLocal
from models import Race

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def get_current_race_id() -> int | None:
    """Find the race_id for the upcoming/current race weekend."""
    db = SessionLocal()
    try:
        today = date.today()
        race = (
            db.query(Race)
            .filter(Race.race_date >= today, Race.status == "scheduled")
            .order_by(Race.race_date)
            .first()
        )
        return race.id if race else None
    finally:
        db.close()


async def run_qualifying_scrape():
    """Fired hourly on Fri/Sat 11am-11pm ET to catch qualifying whenever it posts."""
    race_id = get_current_race_id()
    if not race_id:
        logger.info("Qualifying scraper: no upcoming race found, skipping")
        return
    logger.info(f"Qualifying scraper firing for race_id={race_id}")
    db = SessionLocal()
    try:
        from scrapers.qualifying_scraper import scrape_qualifying
        count = await scrape_qualifying(race_id, db)
        logger.info(f"Qualifying scraper: saved {count} positions for race {race_id}")
    except Exception as e:
        logger.error(f"Qualifying scraper failed: {e}")
    finally:
        db.close()


def start_scheduler():
    """Call this once when the FastAPI app starts."""

    # ── Qualifying (auto) ──
    # Runs every hour on Friday and Saturday, 11am through 11pm ET
    # Qualifying sessions can happen anytime during the day, so hourly
    # coverage ensures we pick up results promptly after they post.
    scheduler.add_job(
        run_qualifying_scrape,
        CronTrigger(
            day_of_week="fri,sat",
            hour="11-23",
            minute=0,
            timezone="America/New_York",
        ),
        id="qualifying_scraper",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started — "
        "qualifying (Fri/Sat hourly 11am-11pm ET). "
        "Race results: Racing Reference browser script. "
        "Salaries: DK/FD browser scripts."
    )
