# ============================================================
# routers/ownership.py — Ownership projections
# ============================================================
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Ownership, Driver
from schemas import OwnershipIn

router = APIRouter()


@router.get("/{race_id}")
def get_ownership(race_id: int, platform: str = "draftkings", db: Session = Depends(get_db)):
    rows = (
        db.query(Ownership, Driver)
        .join(Driver, Ownership.driver_id == Driver.id)
        .filter(Ownership.race_id == race_id, Ownership.platform == platform)
        .order_by(Ownership.ownership_pct.desc())
        .all()
    )
    return [
        {
            "driver_id":        o.driver_id,
            "driver_name":      d.full_name,
            "platform":         o.platform,
            "contest_type":     o.contest_type,
            "ownership_pct":    float(o.ownership_pct) if o.ownership_pct else None,
            "captain_pct":      float(o.captain_pct) if o.captain_pct else None,
        }
        for o, d in rows
    ]


@router.post("/{race_id}")
def save_ownership(race_id: int, entries: List[OwnershipIn], db: Session = Depends(get_db)):
    saved = 0
    for entry in entries:
        existing = db.query(Ownership).filter(
            Ownership.race_id      == race_id,
            Ownership.driver_id    == entry.driver_id,
            Ownership.platform     == entry.platform,
            Ownership.contest_type == entry.contest_type,
        ).first()
        if existing:
            existing.ownership_pct = entry.ownership_pct
            existing.captain_pct   = entry.captain_pct
            existing.source        = entry.source
        else:
            db.add(Ownership(
                race_id        = race_id,
                driver_id      = entry.driver_id,
                platform       = entry.platform,
                contest_type   = entry.contest_type,
                ownership_pct  = entry.ownership_pct,
                captain_pct    = entry.captain_pct,
                source         = entry.source,
            ))
        saved += 1
    db.commit()
    return {"saved": saved}
