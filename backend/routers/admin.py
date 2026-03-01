# ============================================================
# routers/admin.py — Scraper triggers + bulk operations
# ============================================================
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Result, Qualifying, Salary, LoopData

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
