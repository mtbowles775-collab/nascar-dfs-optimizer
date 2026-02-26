# ============================================================
# routers/simulate.py
# POST /api/simulate        — run a simulation
# GET  /api/simulate/{id}/latest — get cached results
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Simulation, SimulationDriverResult
from schemas import SimulateRequest, SimulateResponse, SimDriverResult
from simulation_engine import run_simulation
from datetime import datetime
import json

router = APIRouter()


@router.post("", response_model=SimulateResponse)
def run_sim(req: SimulateRequest, db: Session = Depends(get_db)):
    # Load race with track + track_type
    race = db.query(Race).filter(Race.id == req.race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {req.race_id} not found")

    # Check qualifying lock
    from models import Qualifying
    qual_count  = db.query(Qualifying).filter(Qualifying.race_id == race.id).count()
    qual_locked = qual_count > 0

    # Run the simulation
    results = run_simulation(
        db=db,
        race=race,
        n_sims=req.n_sims,
        platform=req.platform,
        recent_form_races=req.recent_form_races,
    )

    # Persist simulation + per-driver results
    sim = Simulation(
        race_id         = race.id,
        n_sims          = req.n_sims,
        ran_at          = datetime.utcnow(),
        qual_locked     = qual_locked,
        results_json    = results,
        settings_json   = req.dict(),
    )
    db.add(sim)
    db.flush()  # get sim.id without committing

    for r in results:
        dr = SimulationDriverResult(
            simulation_id   = sim.id,
            driver_id       = r["driver_id"],
            avg_fp          = r["avg_fp"],
            median_fp       = r["median_fp"],
            floor_fp        = r["floor_fp"],
            ceiling_fp      = r["ceiling_fp"],
            avg_finish      = r["avg_finish"],
            avg_laps_led    = r["avg_laps_led"],
            fast_lap_pct    = r["fast_lap_pct"],
            win_pct         = r["win_pct"],
            top3_pct        = r["top3_pct"],
            top5_pct        = r["top5_pct"],
            top10_pct       = r["top10_pct"],
            proj_ownership  = r["proj_ownership"],
            leverage_score  = r["leverage_score"],
        )
        db.add(dr)

    db.commit()

    track_type = race.track.track_type.name if race.track and race.track.track_type else "Unknown"

    return SimulateResponse(
        simulation_id   = sim.id,
        race_id         = race.id,
        race_name       = race.race_name or "",
        track_name      = race.track.name if race.track else "",
        track_type      = track_type,
        n_sims          = req.n_sims,
        qual_locked     = qual_locked,
        ran_at          = sim.ran_at,
        drivers         = [SimDriverResult(**r) for r in results],
    )


@router.get("/{race_id}/latest", response_model=SimulateResponse)
def get_latest_sim(race_id: int, db: Session = Depends(get_db)):
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")

    sim = (
        db.query(Simulation)
        .filter(Simulation.race_id == race_id)
        .order_by(Simulation.ran_at.desc())
        .first()
    )
    if not sim:
        raise HTTPException(status_code=404, detail="No simulation found for this race. Run one first.")

    track_type = race.track.track_type.name if race.track and race.track.track_type else "Unknown"

    return SimulateResponse(
        simulation_id   = sim.id,
        race_id         = race.id,
        race_name       = race.race_name or "",
        track_name      = race.track.name if race.track else "",
        track_type      = track_type,
        n_sims          = sim.n_sims,
        qual_locked     = sim.qual_locked,
        ran_at          = sim.ran_at,
        drivers         = [SimDriverResult(**r) for r in sim.results_json],
    )
