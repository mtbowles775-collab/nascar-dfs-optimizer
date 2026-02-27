# ============================================================
# routers/track_types.py
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models import TrackType, Track, Race, Result

router = APIRouter()


@router.get("")
def list_track_types(db: Session = Depends(get_db)):
    rows = (
        db.query(TrackType.name, func.count(Race.id).label("race_count"))
        .join(Track, Track.track_type_id == TrackType.id)
        .join(Race, Race.track_id == Track.id)
        .group_by(TrackType.name)
        .order_by(func.count(Race.id).desc())
        .all()
    )
    return [{"name": r.name, "race_count": r.race_count} for r in rows]


@router.get("/{track_type_name}/summary")
def get_track_type_summary(track_type_name: str, db: Session = Depends(get_db)):
    # Check track type exists
    tt = db.query(TrackType).filter(TrackType.name == track_type_name).first()
    if not tt:
        raise HTTPException(status_code=404, detail=f"Track type '{track_type_name}' not found")

    # Total races at this track type
    total_races = (
        db.query(func.count(Race.id))
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id)
        .scalar()
    )

    # Base query — all results at this track type
    base = (
        db.query(Result)
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id)
    )

    avg_winner_dk = (
        db.query(func.avg(Result.dk_points))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id, Result.finish_position == 1)
        .scalar()
    )

    max_dk = (
        db.query(func.max(Result.dk_points))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id)
        .scalar()
    )

    avg_winner_laps_led = (
        db.query(func.avg(Result.laps_led))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id, Result.finish_position == 1)
        .scalar()
    )

    avg_dominator_dk = (
        db.query(func.avg(Result.dk_points))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id, Result.laps_led >= 50)
        .scalar()
    )

    # Chalk win rate: winners who started top 5
    total_winners = (
        db.query(func.count(Result.id))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(Track.track_type_id == tt.id, Result.finish_position == 1)
        .scalar()
    ) or 0

    chalk_winners = (
        db.query(func.count(Result.id))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .filter(
            Track.track_type_id == tt.id,
            Result.finish_position == 1,
            Result.start_position <= 5
        )
        .scalar()
    ) or 0

    chalk_win_rate = round((chalk_winners / total_winners * 100), 1) if total_winners > 0 else None

    return {
        "track_type":           track_type_name,
        "total_races":          total_races,
        "avg_winner_dk":        round(float(avg_winner_dk), 1) if avg_winner_dk else None,
        "max_dk_ever":          round(float(max_dk), 1) if max_dk else None,
        "avg_winner_laps_led":  round(float(avg_winner_laps_led), 0) if avg_winner_laps_led else None,
        "avg_dominator_dk":     round(float(avg_dominator_dk), 1) if avg_dominator_dk else None,
        "chalk_win_rate":       chalk_win_rate,
    }
