# ============================================================
# scheduler.py
# APScheduler — fires scrapers automatically on race weekends
# Runs inside the FastAPI process on Railway
# ============================================================

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, date
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
    """Fired Friday night + Saturday night at 11pm ET."""
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


async def run_results_scrape():
    """Fired Sunday evening at 8pm, 9pm, 10pm ET until results found."""
    # Find the most recently completed or in-progress race
    db = SessionLocal()
    try:
        today = date.today()
        race = (
            db.query(Race)
            .filter(
                Race.race_date <= today,
                Race.status == "scheduled"  # not yet marked completed
            )
            .order_by(Race.race_date.desc())
            .first()
        )
        if not race:
            return
        logger.info(f"Results scraper firing for race_id={race.id}")
        from scrapers.results_scraper import scrape_results
        count = await scrape_results(race.id, db)
        logger.info(f"Results scraper: saved {count} results for race {race.id}")
    except Exception as e:
        logger.error(f"Results scraper failed: {e}")
    finally:
        db.close()


def start_scheduler():
    """Call this once when the FastAPI app starts."""

    # Qualifying: Friday 11pm ET + Saturday 11pm ET
    scheduler.add_job(
        run_qualifying_scrape,
        CronTrigger(day_of_week="fri,sat", hour=23, minute=0, timezone="America/New_York"),
        id="qualifying_scraper",
        replace_existing=True,
    )

    # Results: Sunday 8pm, 9pm, 10pm, 11pm ET (covers all race end times)
    for hour in [20, 21, 22, 23]:
        scheduler.add_job(
            run_results_scrape,
            CronTrigger(day_of_week="sun", hour=hour, minute=0, timezone="America/New_York"),
            id=f"results_scraper_{hour}",
            replace_existing=True,
        )

    scheduler.start()
    logger.info("Scheduler started — qualifying (Fri/Sat 11pm) + results (Sun 8-11pm)")
