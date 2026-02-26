# ============================================================
# routers/drivers.py
# ============================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_
from typing import List, Optional
from database import get_db
from models import Driver, DriverSeason, Result, Race, Track, TrackType
from schemas import DriverOut, DriverDetailOut

router = APIRouter()

@router.get("", response_model=List[DriverOut])
def list_drivers(
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    q = db.query(Driver)
    if active_only:
        q = q.filter(Driver.active == True)
    return q.order_by(Driver.last_name).all()


@router.get("/{driver_id}")
def get_driver(driver_id: int, db: Session = Depends(get_db)):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


@router.get("/{driver_id}/history")
def get_driver_history(
    driver_id: int,
    track_type: Optional[str] = None,
    season_from: int = 2015,
    season_to: int = 2025,
    platform: str = "draftkings",
    db: Session = Depends(get_db)
):
    """Historical race results for a driver, filterable by track type and season."""
    from models import Qualifying
    q = (
        db.query(Result, Race, Track, TrackType)
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            Result.driver_id == driver_id,
            Race.season >= season_from,
            Race.season <= season_to,
        )
    )
    if track_type:
        q = q.filter(TrackType.name == track_type)

    rows = q.order_by(Race.race_date.desc()).all()

    return [
        {
            "race_id":          race.id,
            "season":           race.season,
            "race_name":        race.race_name,
            "race_date":        str(race.race_date),
            "track_name":       track.name,
            "track_type":       track_type_obj.name,
            "finish_position":  result.finish_position,
            "start_position":   result.start_position,
            "laps_led":         result.laps_led,
            "fastest_lap":      result.fastest_lap,
            "green_flag_speed": float(result.green_flag_speed) if result.green_flag_speed else None,
            "dk_points":        float(result.dk_points) if result.dk_points else None,
            "dk_salary":        result.dk_salary,
            "fd_points":        float(result.fd_points) if result.fd_points else None,
            "fd_salary":        result.fd_salary,
            "status":           result.status,
        }
        for result, race, track, track_type_obj in rows
    ]


@router.get("/{driver_id}/track-type-averages")
def get_track_type_averages(driver_id: int, db: Session = Depends(get_db)):
    """Career averages broken down by FRCS Pro track type."""
    rows = (
        db.query(
            TrackType.name,
            func.count(Result.id).label("races"),
            func.avg(Result.finish_position).label("avg_finish"),
            func.avg(Result.laps_led).label("avg_laps_led"),
            func.avg(Result.dk_points).label("avg_dk_pts"),
            func.avg(Result.fd_points).label("avg_fd_pts"),
            func.avg(Result.green_flag_speed).label("avg_gf_speed"),
            func.sum(func.cast(Result.fastest_lap, db.bind.dialect.name == 'postgresql' and 'integer' or 'integer')).label("fast_laps"),
        )
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(Result.driver_id == driver_id)
        .group_by(TrackType.name)
        .all()
    )
    return [
        {
            "track_type":       r.name,
            "races":            r.races,
            "avg_finish":       round(float(r.avg_finish), 2) if r.avg_finish else None,
            "avg_laps_led":     round(float(r.avg_laps_led), 2) if r.avg_laps_led else None,
            "avg_dk_pts":       round(float(r.avg_dk_pts), 2) if r.avg_dk_pts else None,
            "avg_fd_pts":       round(float(r.avg_fd_pts), 2) if r.avg_fd_pts else None,
            "avg_gf_speed":     round(float(r.avg_gf_speed), 3) if r.avg_gf_speed else None,
        }
        for r in rows
    ]
