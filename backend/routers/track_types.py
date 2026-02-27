# ============================================================
# routers/track_types.py
# GET /api/track-types
# GET /api/track-types/{name}/summary   ← powers Track Insights panel
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
    """
    Historical summary stats for a track type.
    Powers the Track Insights sidebar panel in the frontend.
    """
    row = (
        db.query(
            TrackType.name.label("track_type"),
            func.count(func.distinct(Race.id)).label("total_races"),
            func.avg(Result.dk_points).filter(Result.finish_position == 1).label("avg_winner_dk"),
            func.max(Result.dk_points).label("max_dk_ever"),
            func.avg(Result.laps_led).filter(Result.finish_position == 1).label("avg_winner_laps_led"),
            func.avg(Result.dk_points).filter(Result.laps_led >= 50).label("avg_dominator_dk"),
            (
                func.count(Result.id).filter(
                    Result.finish_position == 1,
                    Result.start_position <= 5
                ).cast(db.bind.dialect.name == "postgresql" and "float" or "float")
                /
                func.nullif(
                    func.count(Result.id).filter(Result.finish_position == 1), 0
                ) * 100
            ).label("chalk_win_rate"),
        )
        .join(Track, Result.race_id == Race.id)  # via Race
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(TrackType.name == track_type_name)
        .group_by(TrackType.name)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail=f"Track type '{track_type_name}' not found")

    return {
        "track_type":             row.track_type,
        "total_races":            row.total_races,
        "avg_winner_dk":          round(float(row.avg_winner_dk), 1) if row.avg_winner_dk else None,
        "max_dk_ever":            round(float(row.max_dk_ever), 1) if row.max_dk_ever else None,
        "avg_winner_laps_led":    round(float(row.avg_winner_laps_led), 0) if row.avg_winner_laps_led else None,
        "avg_dominator_dk":       round(float(row.avg_dominator_dk), 1) if row.avg_dominator_dk else None,
        "chalk_win_rate":         round(float(row.chalk_win_rate), 1) if row.chalk_win_rate else None,
    }
