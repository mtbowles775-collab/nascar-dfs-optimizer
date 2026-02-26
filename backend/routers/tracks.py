# ============================================================
# routers/tracks.py
# ============================================================
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from typing import List
from database import get_db
from models import Track, TrackType
from schemas import TrackOut

router = APIRouter()

@router.get("", response_model=List[TrackOut])
def list_tracks(db: Session = Depends(get_db)):
    return (
        db.query(Track)
        .options(joinedload(Track.track_type))
        .filter(Track.active == True)
        .order_by(Track.name)
        .all()
    )

@router.get("/types")
def list_track_types(db: Session = Depends(get_db)):
    return db.query(TrackType).order_by(TrackType.id).all()
