# ============================================================
# routers/salaries.py — DK/FD salary data
# ============================================================
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Salary, Driver, DriverSeason
from schemas import SalaryIn

router = APIRouter()


@router.get("/{race_id}")
def get_salaries(race_id: int, platform: str = "draftkings", db: Session = Depends(get_db)):
    rows = (
        db.query(Salary, Driver, DriverSeason)
        .join(Driver, Salary.driver_id == Driver.id)
        .outerjoin(
            DriverSeason,
            (DriverSeason.driver_id == Driver.id)
        )
        .filter(Salary.race_id == race_id, Salary.platform == platform)
        .order_by(Salary.salary.desc())
        .distinct(Salary.id)   # avoid duplicates from multiple seasons
        .all()
    )

    seen = set()
    out = []
    for s, d, ds in rows:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append({
            "driver_id":     s.driver_id,
            "driver_name":   d.full_name,
            "car_number":    ds.car_number if ds else None,
            "platform":      s.platform,
            "salary":        s.salary,
            "salary_change": s.salary_change,
        })
    return out


@router.post("/{race_id}")
def save_salaries(race_id: int, entries: List[SalaryIn], db: Session = Depends(get_db)):
    saved = 0
    for entry in entries:
        existing = db.query(Salary).filter(
            Salary.race_id         == race_id,
            Salary.driver_id       == entry.driver_id,
            Salary.platform        == entry.platform,
            Salary.roster_position == entry.roster_position,
        ).first()
        if existing:
            existing.salary_change = entry.salary - existing.salary
            existing.salary        = entry.salary
        else:
            db.add(Salary(
                race_id         = race_id,
                driver_id       = entry.driver_id,
                platform        = entry.platform,
                salary          = entry.salary,
                roster_position = entry.roster_position,
            ))
        saved += 1
    db.commit()
    return {"saved": saved}
