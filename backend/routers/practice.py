# ============================================================
# routers/practice.py — Practice session data
# ============================================================
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
from models import Practice, Driver
from schemas import PracticeOut

router = APIRouter()


@router.get("/{race_id}", response_model=List[PracticeOut])
def get_practice(race_id: int, session: Optional[int] = None, db: Session = Depends(get_db)):
    q = (
        db.query(Practice, Driver)
        .join(Driver, Practice.driver_id == Driver.id)
        .filter(Practice.race_id == race_id)
    )
    if session:
        q = q.filter(Practice.session_number == session)

    rows = q.order_by(Practice.session_number, Practice.position).all()

    return [
        PracticeOut(
            id              = p.id,
            driver_id       = p.driver_id,
            driver_name     = d.full_name,
            session_number  = p.session_number,
            best_lap_time   = p.best_lap_time,
            best_lap_speed  = p.best_lap_speed,
            avg_lap_speed   = p.avg_lap_speed,
            laps_run        = p.laps_run,
            position        = p.position,
        )
        for p, d in rows
    ]
