# ============================================================
# routers/admin.py — Admin endpoints for data management
# Trigger scrapers, manage data, health checks
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Result, LoopData, Qualifying
from datetime import date
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/scrape/live-feed")
async def trigger_live_feed(db: Session = Depends(get_db)):
    """
    Manually trigger the live feed scraper.
    Safe to call multiple times — upserts all data.
    Returns summary of what was scraped.
    """
    from scrapers.live_feed_scraper import scrape_live_feed
    result = await scrape_live_feed(db)
    return result


@router.post("/scrape/qualifying/{race_id}")
async def trigger_qualifying(race_id: int, db: Session = Depends(get_db)):
    """Manually trigger qualifying scraper for a specific race."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")

    from scrapers.qualifying_scraper import scrape_qualifying
    count = await scrape_qualifying(race_id, db)
    return {"race_id": race_id, "qualifying_saved": count}


@router.post("/scrape/salaries")
async def trigger_salary_scrape(db: Session = Depends(get_db)):
    """
    Manually trigger DraftKings salary scraper.
    Finds the next scheduled Cup race and loads DK salaries.
    Safe to call multiple times — upserts all data.
    Salaries typically post Tuesday–Wednesday race week.
    """
    from scrapers.salary_scraper import scrape_dk_salaries
    result = await scrape_dk_salaries(db)
    return result


@router.get("/data-status")
def data_status(db: Session = Depends(get_db)):
    """Overview of data completeness — useful for monitoring."""
    from sqlalchemy import func
    from models import Driver, Track, Salary, Simulation

    total_drivers   = db.query(func.count(Driver.id)).scalar()
    active_drivers  = db.query(func.count(Driver.id)).filter(Driver.active == True).scalar()
    total_tracks    = db.query(func.count(Track.id)).scalar()
    total_races     = db.query(func.count(Race.id)).scalar()
    total_results   = db.query(func.count(Result.id)).scalar()
    total_loop      = db.query(func.count(LoopData.id)).scalar()
    total_qual      = db.query(func.count(Qualifying.id)).scalar()
    total_salaries  = db.query(func.count(Salary.id)).scalar()
    total_sims      = db.query(func.count(Simulation.id)).scalar()

    # Current season stats
    current_year = date.today().year
    season_races = db.query(func.count(Race.id)).filter(Race.season == current_year).scalar()
    completed    = db.query(func.count(Race.id)).filter(
        Race.season == current_year, Race.status == "completed"
    ).scalar()
    scheduled    = db.query(func.count(Race.id)).filter(
        Race.season == current_year, Race.status == "scheduled"
    ).scalar()

    return {
        "totals": {
            "drivers":        total_drivers,
            "active_drivers": active_drivers,
            "tracks":         total_tracks,
            "races":          total_races,
            "results":        total_results,
            "loop_data":      total_loop,
            "qualifying":     total_qual,
            "salaries":       total_salaries,
            "simulations":    total_sims,
        },
        "current_season": {
            "year":         current_year,
            "total_races":  season_races,
            "completed":    completed,
            "scheduled":    scheduled,
        },
    }


@router.get("/next-race")
def next_race_info(db: Session = Depends(get_db)):
    """Quick check: what's the next race and is data ready?"""
    from models import Salary
    from sqlalchemy import func

    race = (
        db.query(Race)
        .filter(Race.race_date >= date.today(), Race.status == "scheduled")
        .order_by(Race.race_date)
        .first()
    )
    if not race:
        return {"message": "No upcoming races found"}

    qual_count = db.query(func.count(Qualifying.id)).filter(
        Qualifying.race_id == race.id
    ).scalar()

    salary_count = db.query(func.count(Salary.id)).filter(
        Salary.race_id == race.id
    ).scalar()

    return {
        "race_id":      race.id,
        "race_name":    race.race_name,
        "race_date":    str(race.race_date),
        "race_number":  race.race_number,
        "qualifying":   qual_count,
        "salaries":     salary_count,
        "ready":        qual_count > 0 and salary_count > 0,
    }
