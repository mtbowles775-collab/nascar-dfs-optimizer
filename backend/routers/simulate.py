# ============================================================
# routers/simulate.py
# POST /api/simulate        — run a simulation
# GET  /api/simulate/{id}/latest — get cached results
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Simulation, SimulationDriverResult, SimSettings
from schemas import SimulateRequest, SimulateResponse, SimDriverResult, SimSettingsOut
from simulation_engine import run_simulation
from datetime import datetime
import json

router = APIRouter()


def _build_settings_out(settings) -> SimSettingsOut:
    """Build SimSettingsOut from DB row or dict."""
    if isinstance(settings, dict):
        return SimSettingsOut(**{k: v for k, v in settings.items() if k != "updated_at" and k != "updated_fields"})
    return SimSettingsOut(
        form_window=settings.form_window,
        tt_form_window=settings.tt_form_window,
        track_rating_window=settings.track_rating_window,
        recent_form_races=settings.recent_form_races,
        use_track_type=settings.use_track_type,
        use_specific_track=settings.use_specific_track,
        use_recent_form=settings.use_recent_form,
        w_finish_track_type=settings.w_finish_track_type,
        w_finish_specific_track=settings.w_finish_specific_track,
        w_finish_recent_form=settings.w_finish_recent_form,
        w_finish_loop_data=settings.w_finish_loop_data,
        w_laps_led_loop=settings.w_laps_led_loop,
        w_fast_laps_loop=settings.w_fast_laps_loop,
        variance_finish=settings.variance_finish,
        variance_laps_led=settings.variance_laps_led,
        variance_fast_laps=settings.variance_fast_laps,
    )


@router.post("", response_model=SimulateResponse)
def run_sim(req: SimulateRequest, db: Session = Depends(get_db)):
    race = db.query(Race).filter(Race.id == req.race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {req.race_id} not found")

    from models import Qualifying
    qual_count = db.query(Qualifying).filter(Qualifying.race_id == race.id).count()
    qual_locked = qual_count > 0

    # Load sim settings from DB
    settings = db.query(SimSettings).filter(SimSettings.id == 1).first()

    # Run the V2 simulation (passes settings object directly)
    results = run_simulation(
        db=db,
        race=race,
        n_sims=req.n_sims,
        platform=req.platform,
        settings=settings,
    )

    # Build settings snapshot for storage
    settings_snapshot = {
        "platform": req.platform,
        "n_sims": req.n_sims,
        "form_window": settings.form_window if settings else 10,
        "tt_form_window": settings.tt_form_window if settings else 6,
        "track_rating_window": settings.track_rating_window if settings else 5,
        "recent_form_races": settings.recent_form_races if settings else 5,
        "use_track_type": settings.use_track_type if settings else True,
        "use_specific_track": settings.use_specific_track if settings else True,
        "use_recent_form": settings.use_recent_form if settings else True,
        "w_finish_track_type": settings.w_finish_track_type if settings else 35,
        "w_finish_specific_track": settings.w_finish_specific_track if settings else 25,
        "w_finish_recent_form": settings.w_finish_recent_form if settings else 20,
        "w_finish_loop_data": settings.w_finish_loop_data if settings else 20,
        "w_laps_led_loop": settings.w_laps_led_loop if settings else 60,
        "w_fast_laps_loop": settings.w_fast_laps_loop if settings else 60,
        "variance_finish": settings.variance_finish if settings else 100,
        "variance_laps_led": settings.variance_laps_led if settings else 100,
        "variance_fast_laps": settings.variance_fast_laps if settings else 100,
        "engine_version": "v2_race_outcome_first",
    }

    # Persist simulation + per-driver results
    sim = Simulation(
        race_id=race.id,
        n_sims=req.n_sims,
        ran_at=datetime.utcnow(),
        qual_locked=qual_locked,
        results_json=results,
        settings_json=settings_snapshot,
    )
    db.add(sim)
    db.flush()

    for r in results:
        dr = SimulationDriverResult(
            simulation_id=sim.id,
            driver_id=r["driver_id"],
            avg_fp=r["avg_fp"],
            median_fp=r["median_fp"],
            floor_fp=r["floor_fp"],
            ceiling_fp=r["ceiling_fp"],
            avg_finish=r["avg_finish"],
            avg_laps_led=r["avg_laps_led"],
            fast_lap_pct=r["fast_lap_pct"],
            win_pct=r["win_pct"],
            top3_pct=r["top3_pct"],
            top5_pct=r["top5_pct"],
            top10_pct=r["top10_pct"],
            proj_ownership=r["proj_ownership"],
            leverage_score=r["leverage_score"],
        )
        db.add(dr)

    db.commit()

    track_type = race.track.track_type.name if race.track and race.track.track_type else "Unknown"

    return SimulateResponse(
        simulation_id=sim.id,
        race_id=race.id,
        race_name=race.race_name or "",
        track_name=race.track.name if race.track else "",
        track_type=track_type,
        n_sims=req.n_sims,
        qual_locked=qual_locked,
        ran_at=sim.ran_at,
        settings=_build_settings_out(settings),
        drivers=[SimDriverResult(**r) for r in results],
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

    # Pull settings from sim's settings_json if available, otherwise from DB
    settings_json = sim.settings_json or {}
    db_settings = db.query(SimSettings).filter(SimSettings.id == 1).first()

    # Build settings output — prefer sim snapshot, fall back to DB
    sim_settings_out = SimSettingsOut(
        form_window=settings_json.get("form_window", db_settings.form_window if db_settings else 10),
        tt_form_window=settings_json.get("tt_form_window", db_settings.tt_form_window if db_settings else 6),
        track_rating_window=settings_json.get("track_rating_window", db_settings.track_rating_window if db_settings else 5),
        recent_form_races=settings_json.get("recent_form_races", db_settings.recent_form_races if db_settings else 5),
        use_track_type=settings_json.get("use_track_type", db_settings.use_track_type if db_settings else True),
        use_specific_track=settings_json.get("use_specific_track", db_settings.use_specific_track if db_settings else True),
        use_recent_form=settings_json.get("use_recent_form", db_settings.use_recent_form if db_settings else True),
        w_finish_track_type=settings_json.get("w_finish_track_type", db_settings.w_finish_track_type if db_settings else 35),
        w_finish_specific_track=settings_json.get("w_finish_specific_track", db_settings.w_finish_specific_track if db_settings else 25),
        w_finish_recent_form=settings_json.get("w_finish_recent_form", db_settings.w_finish_recent_form if db_settings else 20),
        w_finish_loop_data=settings_json.get("w_finish_loop_data", db_settings.w_finish_loop_data if db_settings else 20),
        w_laps_led_loop=settings_json.get("w_laps_led_loop", db_settings.w_laps_led_loop if db_settings else 60),
        w_fast_laps_loop=settings_json.get("w_fast_laps_loop", db_settings.w_fast_laps_loop if db_settings else 60),
        variance_finish=settings_json.get("variance_finish", db_settings.variance_finish if db_settings else 100),
        variance_laps_led=settings_json.get("variance_laps_led", db_settings.variance_laps_led if db_settings else 100),
        variance_fast_laps=settings_json.get("variance_fast_laps", db_settings.variance_fast_laps if db_settings else 100),
    )

    return SimulateResponse(
        simulation_id=sim.id,
        race_id=race.id,
        race_name=race.race_name or "",
        track_name=race.track.name if race.track else "",
        track_type=track_type,
        n_sims=sim.n_sims,
        qual_locked=sim.qual_locked,
        ran_at=sim.ran_at,
        settings=sim_settings_out,
        drivers=[SimDriverResult(**r) for r in sim.results_json],
    )
