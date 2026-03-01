# ============================================================
# routers/admin.py — Scraper triggers + bulk operations
# ============================================================
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Result, Qualifying, Salary, LoopData, Driver

router = APIRouter()


@router.post("/scrape/qualifying/{race_id}")
async def trigger_qual_scrape(race_id: int, db: Session = Depends(get_db)):
    """Manually trigger a qualifying scrape for a specific race."""
    from scrapers.qualifying_scraper import scrape_qualifying
    try:
        result = await scrape_qualifying(race_id, db)
        return {"status": "success", "scraped": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrape/results/{race_id}")
async def trigger_results_scrape(race_id: int, db: Session = Depends(get_db)):
    """Manually trigger a results scrape + DK/FD point calculation."""
    from scrapers.results_scraper import scrape_results
    try:
        result = await scrape_results(race_id, db)
        return {"status": "success", "scraped": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrape/live-feed")
async def trigger_live_feed_scrape(db: Session = Depends(get_db)):
    """Manually trigger a live feed scrape for the current race."""
    from scrapers.live_feed_scraper import scrape_live_feed
    try:
        result = await scrape_live_feed(db)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/loop-data-file")
async def import_loop_data_file(request: Request, db: Session = Depends(get_db)):
    """
    Import loop data from Racing-Reference JSON upload.
    curl -X POST -H "Content-Type: application/json" -d @racing_reference_loop_data.json URL
    """
    body = await request.body()
    races_data = json.loads(body)

    imported = 0
    skipped = 0
    no_race = 0
    no_driver = 0

    for race_entry in races_data:
        year = race_entry["year"]
        race_num = race_entry["race_number"]

        # Find matching race
        race = (
            db.query(Race)
            .filter(Race.season == year, Race.race_number == race_num, Race.series == "cup")
            .first()
        )
        if not race:
            no_race += len(race_entry["drivers"])
            continue

        # Update race metadata if missing
        if race_entry.get("cautions") and not race.caution_segments:
            race.caution_segments = race_entry["cautions"]
        if race_entry.get("caution_laps") and not race.caution_laps:
            race.caution_laps = race_entry["caution_laps"]
        if race_entry.get("lead_changes") and not race.lead_changes:
            race.lead_changes = race_entry["lead_changes"]

        for d in race_entry["drivers"]:
            driver_name = d["driver"]
            parts = driver_name.split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""

            # Find driver — try exact match, then fuzzy
            driver = (
                db.query(Driver)
                .filter(Driver.first_name == first, Driver.last_name == last)
                .first()
            )
            if not driver:
                # Try case-insensitive
                driver = (
                    db.query(Driver)
                    .filter(
                        Driver.first_name.ilike(first),
                        Driver.last_name.ilike(last),
                    )
                    .first()
                )
            if not driver:
                no_driver += 1
                continue

            # Check if loop_data already exists
            existing = (
                db.query(LoopData)
                .filter(LoopData.race_id == race.id, LoopData.driver_id == driver.id)
                .first()
            )
            if existing:
                skipped += 1
                continue

            total_laps = d.get("total_laps", 1) or 1
            loop = LoopData(
                race_id=race.id,
                driver_id=driver.id,
                green_flag_passes=d["green_flag_passes"],
                green_flag_passed=d["green_flag_times_passed"],
                quality_passes=d["quality_passes"],
                avg_running_position=d["avg_pos"],
                passing_differential=d["pass_diff"],
                laps_in_top15=d["top15_laps"],
                pct_laps_in_top15=d["pct_top15_laps"],
                fastest_lap_pct=round(d["fastest_laps"] / total_laps * 100, 2) if total_laps else 0,
                driver_rating=d["driver_rating"],
            )
            db.add(loop)

            # Also backfill driver_rating on results table
            result = (
                db.query(Result)
                .filter(Result.race_id == race.id, Result.driver_id == driver.id)
                .first()
            )
            if result and not result.driver_rating:
                result.driver_rating = d["driver_rating"]

            imported += 1

    db.commit()

    return {
        "status": "success",
        "imported": imported,
        "skipped_existing": skipped,
        "no_race_match": no_race,
        "no_driver_match": no_driver,
        "total_loop_data": db.query(LoopData).count(),
    }


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """Quick health check on data completeness."""
    return {
        "races":        db.query(Race).count(),
        "results":      db.query(Result).count(),
        "qualifying":   db.query(Qualifying).count(),
        "salaries":     db.query(Salary).count(),
        "loop_data":    db.query(LoopData).count(),
    }
