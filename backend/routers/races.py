# ============================================================
# routers/races.py
# ============================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from database import get_db
from models import Race, Track, TrackType
from schemas import RaceOut
from datetime import date

router = APIRouter()

@router.get("", response_model=List[RaceOut])
def list_races(
    season: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    q = db.query(Race).options(joinedload(Race.track).joinedload(Track.track_type))
    if season:
        q = q.filter(Race.season == season)
    if status:
        q = q.filter(Race.status == status)
    return q.order_by(Race.season, Race.race_number).all()


@router.get("/upcoming", response_model=List[RaceOut])
def upcoming_races(db: Session = Depends(get_db)):
    return (
        db.query(Race)
        .options(joinedload(Race.track).joinedload(Track.track_type))
        .filter(Race.race_date >= date.today(), Race.status == "scheduled")
        .order_by(Race.race_date)
        .limit(5)
        .all()
    )


@router.get("/next", response_model=RaceOut)
def next_race(db: Session = Depends(get_db)):
    race = (
        db.query(Race)
        .options(joinedload(Race.track).joinedload(Track.track_type))
        .filter(Race.race_date >= date.today(), Race.status == "scheduled")
        .order_by(Race.race_date)
        .first()
    )
    if not race:
        raise HTTPException(status_code=404, detail="No upcoming races found")
    return race


@router.get("/{race_id}", response_model=RaceOut)
def get_race(race_id: int, db: Session = Depends(get_db)):
    race = (
        db.query(Race)
        .options(joinedload(Race.track).joinedload(Track.track_type))
        .filter(Race.id == race_id)
        .first()
    )
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return race
