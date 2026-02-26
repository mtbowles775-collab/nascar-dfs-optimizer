# ============================================================
# routers/results.py
# ============================================================
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
from models import Result, Race, Driver, DriverSeason
from schemas import ResultOut

router = APIRouter()

@router.get("/{race_id}", response_model=List[ResultOut])
def get_race_results(race_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(Result)
        .filter(Result.race_id == race_id)
        .order_by(Result.finish_position)
        .all()
    )
    out = []
    for r in rows:
        driver = db.query(Driver).filter(Driver.id == r.driver_id).first()
        season_info = (
            db.query(DriverSeason)
            .join(Race, Race.season == DriverSeason.season)
            .filter(DriverSeason.driver_id == r.driver_id, Race.id == race_id)
            .first()
        )
        out.append(ResultOut(
            id                  = r.id,
            driver_id           = r.driver_id,
            driver_name         = driver.full_name if driver else None,
            car_number          = season_info.car_number if season_info else None,
            finish_position     = r.finish_position,
            start_position      = r.start_position,
            laps_completed      = r.laps_completed,
            laps_led            = r.laps_led,
            fastest_lap         = r.fastest_lap,
            green_flag_speed    = r.green_flag_speed,
            dk_salary           = r.dk_salary,
            dk_points           = r.dk_points,
            dk_place_pts        = r.dk_place_pts,
            dk_place_diff_pts   = r.dk_place_diff_pts,
            dk_laps_led_pts     = r.dk_laps_led_pts,
            dk_fast_lap_pts     = r.dk_fast_lap_pts,
            dk_dominator_bonus  = r.dk_dominator_bonus,
            fd_salary           = r.fd_salary,
            fd_points           = r.fd_points,
            status              = r.status,
        ))
    return out


# ============================================================
# routers/practice.py
# ============================================================
from fastapi import APIRouter as PracticeRouter
from schemas import PracticeOut

practice_router = PracticeRouter()

@practice_router.get("/{race_id}", response_model=List[PracticeOut])
def get_practice(race_id: int, session: Optional[int] = None, db: Session = Depends(get_db)):
    from models import Practice
    q = db.query(Practice).filter(Practice.race_id == race_id)
    if session:
        q = q.filter(Practice.session_number == session)
    rows = q.order_by(Practice.session_number, Practice.position).all()
    out = []
    for r in rows:
        driver = db.query(Driver).filter(Driver.id == r.driver_id).first()
        out.append(PracticeOut(
            id              = r.id,
            driver_id       = r.driver_id,
            driver_name     = driver.full_name if driver else None,
            session_number  = r.session_number,
            best_lap_time   = r.best_lap_time,
            best_lap_speed  = r.best_lap_speed,
            avg_lap_speed   = r.avg_lap_speed,
            laps_run        = r.laps_run,
            position        = r.position,
        ))
    return out


# ============================================================
# routers/ownership.py
# ============================================================
from fastapi import APIRouter as OwnershipRouter
from schemas import OwnershipOut, OwnershipIn

ownership_router = OwnershipRouter()

@ownership_router.get("/{race_id}")
def get_ownership(race_id: int, platform: str = "draftkings", db: Session = Depends(get_db)):
    from models import Ownership
    rows = (
        db.query(Ownership)
        .filter(Ownership.race_id == race_id, Ownership.platform == platform)
        .order_by(Ownership.ownership_pct.desc())
        .all()
    )
    out = []
    for r in rows:
        driver = db.query(Driver).filter(Driver.id == r.driver_id).first()
        out.append({
            "driver_id":        r.driver_id,
            "driver_name":      driver.full_name if driver else None,
            "platform":         r.platform,
            "contest_type":     r.contest_type,
            "ownership_pct":    float(r.ownership_pct) if r.ownership_pct else None,
            "captain_pct":      float(r.captain_pct) if r.captain_pct else None,
        })
    return out

@ownership_router.post("/{race_id}")
def save_ownership(race_id: int, entries: List[OwnershipIn], db: Session = Depends(get_db)):
    from models import Ownership
    saved = 0
    for entry in entries:
        existing = db.query(Ownership).filter(
            Ownership.race_id       == race_id,
            Ownership.driver_id     == entry.driver_id,
            Ownership.platform      == entry.platform,
            Ownership.contest_type  == entry.contest_type,
        ).first()
        if existing:
            existing.ownership_pct  = entry.ownership_pct
            existing.captain_pct    = entry.captain_pct
            existing.source         = entry.source
        else:
            db.add(Ownership(
                race_id         = race_id,
                driver_id       = entry.driver_id,
                platform        = entry.platform,
                contest_type    = entry.contest_type,
                ownership_pct   = entry.ownership_pct,
                captain_pct     = entry.captain_pct,
                source          = entry.source,
            ))
        saved += 1
    db.commit()
    return {"saved": saved}


# ============================================================
# routers/salaries.py
# ============================================================
from fastapi import APIRouter as SalariesRouter
from schemas import SalaryOut, SalaryIn

salaries_router = SalariesRouter()

@salaries_router.get("/{race_id}")
def get_salaries(race_id: int, platform: str = "draftkings", db: Session = Depends(get_db)):
    from models import Salary
    rows = (
        db.query(Salary)
        .filter(Salary.race_id == race_id, Salary.platform == platform)
        .order_by(Salary.salary.desc())
        .all()
    )
    out = []
    for r in rows:
        driver = db.query(Driver).filter(Driver.id == r.driver_id).first()
        season_info = (
            db.query(DriverSeason)
            .filter(DriverSeason.driver_id == r.driver_id)
            .order_by(DriverSeason.season.desc())
            .first()
        )
        out.append({
            "driver_id":    r.driver_id,
            "driver_name":  driver.full_name if driver else None,
            "car_number":   season_info.car_number if season_info else None,
            "platform":     r.platform,
            "salary":       r.salary,
            "salary_change":r.salary_change,
        })
    return out

@salaries_router.post("/{race_id}")
def save_salaries(race_id: int, entries: List[SalaryIn], db: Session = Depends(get_db)):
    from models import Salary
    saved = 0
    for entry in entries:
        existing = db.query(Salary).filter(
            Salary.race_id          == race_id,
            Salary.driver_id        == entry.driver_id,
            Salary.platform         == entry.platform,
            Salary.roster_position  == entry.roster_position,
        ).first()
        if existing:
            # Track salary change
            existing.salary_change  = entry.salary - existing.salary
            existing.salary         = entry.salary
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


# ============================================================
# routers/lineups.py
# ============================================================
from fastapi import APIRouter as LineupsRouter
from schemas import OptimizeRequest, LineupOut, LineupDriverOut
from simulation_engine import optimize_lineups

lineups_router = LineupsRouter()

@lineups_router.post("/optimize")
def optimize(req: OptimizeRequest, db: Session = Depends(get_db)):
    # Get latest sim results for this race
    from models import Simulation
    sim = (
        db.query(Simulation)
        .filter(Simulation.race_id == req.race_id)
        .order_by(Simulation.ran_at.desc())
        .first()
    )
    if not sim:
        raise HTTPException(status_code=404, detail="No simulation found. Run a simulation first.")

    sim_results = sim.results_json  # already a list of dicts

    # Attach current salary if not already in sim results
    from models import Salary
    sal_map = {
        s.driver_id: s.salary
        for s in db.query(Salary).filter(
            Salary.race_id == req.race_id,
            Salary.platform == req.platform,
        ).all()
    }
    for r in sim_results:
        if r.get("salary") is None:
            r["salary"] = sal_map.get(r["driver_id"])

    lineups = optimize_lineups(
        sim_results     = sim_results,
        salary_cap      = req.salary_cap,
        n_lineups       = req.n_lineups,
        lock_drivers    = req.lock_drivers,
        exclude_drivers = req.exclude_drivers,
        max_ownership   = req.max_ownership,
        min_salary      = req.min_salary,
    )

    out = []
    for i, l in enumerate(lineups):
        drivers_out = []
        for slot, d in enumerate(l["lineup"], 1):
            drivers_out.append(LineupDriverOut(
                slot            = slot,
                driver_id       = d["driver_id"],
                driver_name     = d["driver_name"],
                car_number      = d["car_number"],
                salary          = d["salary"] or 0,
                avg_fp          = d["avg_fp"],
                floor_fp        = d["floor_fp"],
                ceiling_fp      = d["ceiling_fp"],
                proj_ownership  = d["proj_ownership"],
            ))
        out.append(LineupOut(
            id              = None,
            label           = f"Lineup {i+1}",
            platform        = req.platform,
            total_salary    = l["total_salary"],
            salary_remaining= l["salary_remaining"],
            proj_fp         = l["proj_fp"],
            proj_ceiling    = l["proj_ceiling"],
            drivers         = drivers_out,
        ))
    return out

@lineups_router.post("/save")
def save_lineup(race_id: int, lineup_data: LineupOut, db: Session = Depends(get_db)):
    from models import Lineup, LineupDriver
    lineup = Lineup(
        race_id         = race_id,
        platform        = lineup_data.platform,
        label           = lineup_data.label,
        total_salary    = lineup_data.total_salary,
        proj_fp         = lineup_data.proj_fp,
        proj_ceiling    = lineup_data.proj_ceiling,
    )
    db.add(lineup)
    db.flush()
    for d in lineup_data.drivers:
        db.add(LineupDriver(lineup_id=lineup.id, driver_id=d.driver_id, slot=d.slot))
    db.commit()
    return {"id": lineup.id, "label": lineup.label}

@lineups_router.get("")
def get_lineups(race_id: int, db: Session = Depends(get_db)):
    from models import Lineup
    return db.query(Lineup).filter(Lineup.race_id == race_id).all()


# ============================================================
# routers/admin.py — scraper triggers + bulk operations
# ============================================================
from fastapi import APIRouter as AdminRouter
import httpx

admin_router = AdminRouter()

@admin_router.post("/scrape/qualifying/{race_id}")
async def trigger_qual_scrape(race_id: int, db: Session = Depends(get_db)):
    """Manually trigger a qualifying scrape for a specific race."""
    from scrapers.qualifying_scraper import scrape_qualifying
    try:
        result = await scrape_qualifying(race_id, db)
        return {"status": "success", "scraped": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.post("/scrape/results/{race_id}")
async def trigger_results_scrape(race_id: int, db: Session = Depends(get_db)):
    """Manually trigger a results scrape + DK/FD point calculation."""
    from scrapers.results_scraper import scrape_results
    try:
        result = await scrape_results(race_id, db)
        return {"status": "success", "scraped": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """Quick health check on data completeness."""
    from models import Race, Result, Qualifying, Salary, LoopData
    return {
        "races":        db.query(Race).count(),
        "results":      db.query(Result).count(),
        "qualifying":   db.query(Qualifying).count(),
        "salaries":     db.query(Salary).count(),
        "loop_data":    db.query(LoopData).count(),
    }


# ── Wire up all secondary routers ─────────────────────────
# These are imported by main.py using the names below
from routers.results import practice_router    as practice
from routers.results import ownership_router   as ownership
from routers.results import salaries_router    as salaries
from routers.results import lineups_router     as lineups
from routers.results import admin_router       as admin
