# ============================================================
# routers/results.py — Race results only
# ============================================================
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Result, Race, Driver, DriverSeason
from schemas import ResultOut

router = APIRouter()


@router.get("/{race_id}", response_model=List[ResultOut])
def get_race_results(race_id: int, db: Session = Depends(get_db)):
    """Get race results with driver info in a single joined query."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    # Single query: join Result → Driver → DriverSeason (for that race's season)
    rows = (
        db.query(Result, Driver, DriverSeason)
        .join(Driver, Result.driver_id == Driver.id)
        .outerjoin(
            DriverSeason,
            (DriverSeason.driver_id == Driver.id) & (DriverSeason.season == race.season)
        )
        .filter(Result.race_id == race_id)
        .order_by(Result.finish_position)
        .all()
    )

    return [
        ResultOut(
            id                  = r.id,
            driver_id           = r.driver_id,
            driver_name         = d.full_name,
            car_number          = ds.car_number if ds else None,
            finish_position     = r.finish_position,
            start_position      = r.start_position,
            laps_completed      = r.laps_completed,
            laps_led            = r.laps_led,
            fastest_lap         = r.fastest_lap,
            green_flag_speed    = r.green_flag_speed,
            dk_salary           = r.dk_salary,
            dk_points           = r.dk_points,
            dk_place_pts        = r.dk_place_pts,
            dk_place_diff_pts   = r.dk_place_diff_pts,
            dk_laps_led_pts     = r.dk_laps_led_pts,
            dk_fast_lap_pts     = r.dk_fast_lap_pts,
            dk_dominator_bonus  = r.dk_dominator_bonus,
            fd_salary           = r.fd_salary,
            fd_points           = r.fd_points,
            status              = r.status,
        )
        for r, d, ds in rows
    ]
