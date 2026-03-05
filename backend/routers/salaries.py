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
        db.query(Salary, Driver)
        .join(Driver, Salary.driver_id == Driver.id)
        .filter(Salary.race_id == race_id, Salary.platform == platform)
        .order_by(Salary.salary.desc())
        .all()
    )

    # Get current season car numbers separately to avoid duplicate rows
    from models import Race
    race = db.query(Race).filter(Race.id == race_id).first()
    season = race.season if race else 2026

    driver_seasons = {}
    if rows:
        driver_ids = [d.id for _, d in rows]
        ds_rows = db.query(DriverSeason).filter(
            DriverSeason.driver_id.in_(driver_ids),
            DriverSeason.season == season,
        ).all()
        driver_seasons = {ds.driver_id: ds for ds in ds_rows}

    out = []
    for s, d in rows:
        ds = driver_seasons.get(d.id)
        out.append({
            "driver_id":     s.driver_id,
            "driver_name":   f"{d.first_name} {d.last_name}",
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
