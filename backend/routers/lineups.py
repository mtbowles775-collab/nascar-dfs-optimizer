# ============================================================
# routers/lineups.py — Lineup optimization and saving
# ============================================================
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Simulation, Salary, Lineup, LineupDriver
from schemas import OptimizeRequest, LineupOut, LineupDriverOut
from simulation_engine import optimize_lineups

router = APIRouter()


@router.post("/optimize")
def optimize(req: OptimizeRequest, db: Session = Depends(get_db)):
    sim = (
        db.query(Simulation)
        .filter(Simulation.race_id == req.race_id)
        .order_by(Simulation.ran_at.desc())
        .first()
    )
    if not sim:
        raise HTTPException(status_code=404, detail="No simulation found. Run a simulation first.")

    sim_results = sim.results_json

    # Attach salary if not in sim results
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
    for i, lineup in enumerate(lineups):
        drivers_out = [
            LineupDriverOut(
                slot            = slot,
                driver_id       = d["driver_id"],
                driver_name     = d["driver_name"],
                car_number      = d["car_number"],
                salary          = d["salary"] or 0,
                avg_fp          = d["avg_fp"],
                floor_fp        = d["floor_fp"],
                ceiling_fp      = d["ceiling_fp"],
                proj_ownership  = d["proj_ownership"],
            )
            for slot, d in enumerate(lineup["lineup"], 1)
        ]
        out.append(LineupOut(
            id              = None,
            label           = f"Lineup {i+1}",
            platform        = req.platform,
            total_salary    = lineup["total_salary"],
            salary_remaining= lineup["salary_remaining"],
            proj_fp         = lineup["proj_fp"],
            proj_ceiling    = lineup["proj_ceiling"],
            drivers         = drivers_out,
        ))
    return out


@router.post("/save")
def save_lineup(race_id: int, lineup_data: LineupOut, db: Session = Depends(get_db)):
    lineup = Lineup(
        race_id      = race_id,
        # user_id    = current_user.id,  # TODO: add when auth is enforced
        platform     = lineup_data.platform,
        label        = lineup_data.label,
        total_salary = lineup_data.total_salary,
        proj_fp      = lineup_data.proj_fp,
        proj_ceiling = lineup_data.proj_ceiling,
    )
    db.add(lineup)
    db.flush()
    for d in lineup_data.drivers:
        db.add(LineupDriver(lineup_id=lineup.id, driver_id=d.driver_id, slot=d.slot))
    db.commit()
    return {"id": lineup.id, "label": lineup.label}


@router.get("")
def get_lineups(race_id: int, db: Session = Depends(get_db)):
    return db.query(Lineup).filter(Lineup.race_id == race_id).all()
