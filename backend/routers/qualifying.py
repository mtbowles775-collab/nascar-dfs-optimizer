# ============================================================
# routers/qualifying.py — Qualifying positions + times
# ============================================================
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Qualifying, Race, Driver, DriverSeason
from schemas import QualifyingOut, QualifyingIn
from datetime import datetime

router = APIRouter()


@router.get("/{race_id}", response_model=List[QualifyingOut])
def get_qualifying(race_id: int, db: Session = Depends(get_db)):
    """Get qualifying results — single query with JOINs."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    rows = (
        db.query(Qualifying, Driver, DriverSeason)
        .join(Driver, Qualifying.driver_id == Driver.id)
        .outerjoin(
            DriverSeason,
            (DriverSeason.driver_id == Driver.id) &
            (DriverSeason.season == race.season)
        )
        .filter(Qualifying.race_id == race_id)
        .order_by(Qualifying.start_position)
        .all()
    )

    return [
        QualifyingOut(
            id              = q.id,
            driver_id       = q.driver_id,
            driver_name     = d.full_name,
            car_number      = ds.car_number if ds else None,
            start_position  = q.start_position,
            lap_time_sec    = q.lap_time_sec,
            lap_speed_mph   = q.lap_speed_mph,
            source          = q.source,
        )
        for q, d, ds in rows
    ]


@router.post("/{race_id}")
def save_qualifying(race_id: int, body: QualifyingIn, db: Session = Depends(get_db)):
    """Save qualifying positions. Upserts — safe to call multiple times."""
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    saved = 0
    for driver_id, start_pos in body.positions.items():
        existing = db.query(Qualifying).filter(
            Qualifying.race_id == race_id,
            Qualifying.driver_id == int(driver_id),
        ).first()

        if existing:
            existing.start_position = start_pos
            existing.source         = body.source
            existing.session_date   = datetime.utcnow()
        else:
            db.add(Qualifying(
                race_id         = race_id,
                driver_id       = int(driver_id),
                start_position  = start_pos,
                source          = body.source,
                session_date    = datetime.utcnow(),
            ))
        saved += 1

    db.commit()
    return {"saved": saved, "race_id": race_id, "source": body.source}


@router.delete("/{race_id}")
def clear_qualifying(race_id: int, db: Session = Depends(get_db)):
    deleted = db.query(Qualifying).filter(Qualifying.race_id == race_id).delete()
    db.commit()
    return {"deleted": deleted, "race_id": race_id}
