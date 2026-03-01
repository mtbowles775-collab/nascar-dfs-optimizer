# ============================================================
# scheduler.py
# APScheduler — fires scrapers automatically
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


async def run_live_feed_scrape():
    """
    Poll the NASCAR live feed for race results + loop data.
    Safe to call any time — if no race is happening, it skips instantly.
    """
    db = SessionLocal()
    try:
        from scrapers.live_feed_scraper import scrape_live_feed
        result = await scrape_live_feed(db)

        if result.get("error"):
            logger.warning(f"Live feed scraper: {result['error']}")
        elif result.get("skipped"):
            logger.debug(f"Live feed scraper skipped: {result['reason']}")
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

    # ── Live Feed: every day, every 30 min from 12pm-midnight ET ──
    # Covers all race windows: Fri night, Sat, Sun, Mon rain delays
    # If no race is happening, the scraper skips instantly (near-zero cost)
    for hour in range(12, 24):
        for minute in [0, 30]:
            scheduler.add_job(
                run_live_feed_scrape,
                CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone="America/New_York",
                ),
                id=f"live_feed_{hour}_{minute}",
                replace_existing=True,
            )

    # ── Qualifying: Fri + Sat 11pm ET ──
    scheduler.add_job(
        run_qualifying_scrape,
        CronTrigger(day_of_week="fri,sat", hour=23, minute=0, timezone="America/New_York"),
        id="qualifying_scraper",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started — "
        "live feed (daily 12pm-midnight ET every 30min) + "
        "qualifying (Fri/Sat 11pm ET)"
    )
