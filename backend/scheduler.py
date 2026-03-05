# ============================================================
# scheduler.py
# APScheduler — fires scrapers automatically on race weekends
# Runs inside the FastAPI process on Railway
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


async def run_live_feed_scrape():
    """
    Poll the NASCAR live feed for race results + loop data.
    Called multiple times on race day — safe to call repeatedly (upserts).
    Only processes data when run_type=3 (race) and race is in progress or complete.
    """
    db = SessionLocal()
    try:
        from scrapers.live_feed_scraper import scrape_live_feed
        result = await scrape_live_feed(db)

        if result.get("error"):
            logger.warning(f"Live feed scraper: {result['error']}")
        elif result.get("skipped"):
            logger.info(f"Live feed scraper skipped: {result['reason']}")
        else:
            logger.info(
                f"Live feed scraper: {result['race_name']} at {result['track_name']} "
                f"({result['laps']}) — {result['results_saved']} results, "
                f"{result['loop_data_saved']} loop data records"
            )
            if result.get("is_complete"):
                logger.info("Race is COMPLETE — final results captured ✅")
    except Exception as e:
        logger.error(f"Live feed scraper failed: {e}", exc_info=True)
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


def start_scheduler():
    """Call this once when the FastAPI app starts."""

    # ── Race results via Live Feed ──
    # Sunday: poll every 30 min from 2pm-midnight ET
    for hour in range(14, 24):
        for minute in [0, 30]:
            scheduler.add_job(
                run_live_feed_scrape,
                CronTrigger(
                    day_of_week="sun",
                    hour=hour,
                    minute=minute,
                    timezone="America/New_York",
                ),
                id=f"live_feed_{hour}_{minute}",
                replace_existing=True,
            )

    # Saturday races (road courses, some Xfinity/Trucks)
    for hour in range(12, 20):
        scheduler.add_job(
            run_live_feed_scrape,
            CronTrigger(
                day_of_week="sat",
                hour=hour,
                minute=0,
                timezone="America/New_York",
            ),
            id=f"live_feed_sat_{hour}",
            replace_existing=True,
        )

    # ── Qualifying ──
    scheduler.add_job(
        run_qualifying_scrape,
        CronTrigger(day_of_week="fri,sat", hour=23, minute=0, timezone="America/New_York"),
        id="qualifying_scraper",
        replace_existing=True,
    )

    # NOTE: DK salary loading is done via the SalaryLoader browser component.
    # DK blocks Railway's IP from their API — salaries must be fetched
    # client-side. See src/components/SalaryLoader.jsx in the frontend.

    scheduler.start()
    logger.info(
        "Scheduler started — "
        "live feed (Sat 12-7pm, Sun 2pm-midnight ET every 30min) + "
        "qualifying (Fri/Sat 11pm ET)"
    )
