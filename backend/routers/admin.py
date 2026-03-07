# ============================================================
# routers/admin.py — Admin endpoints for data management
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Race, Result, LoopData, Qualifying, SimSettings
from datetime import date, datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/scrape/qualifying/{race_id}")
async def trigger_qualifying(race_id: int, db: Session = Depends(get_db)):
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise HTTPException(status_code=404, detail=f"Race {race_id} not found")
    from scrapers.qualifying_scraper import scrape_qualifying
    count = await scrape_qualifying(race_id, db)
    return {"race_id": race_id, "qualifying_saved": count}


@router.post("/salaries/load-from-browser")
async def load_salaries_from_browser(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Receives raw player data fetched by the browser from DK or FanDuel,
    matches drivers, and saves salaries to the DB.

    DK payload:   { draft_group_id: int, players: [...DK draftables...] }
    FD payload:   { platform: "fanduel", draft_group_id: str, players: [...FD players...] }
    """
    from scrapers.salary_scraper import match_driver
    from models import Salary

    players        = payload.get("players", [])
    draft_group_id = payload.get("draft_group_id")
    platform       = payload.get("platform", "draftkings").lower()

    if not players:
        raise HTTPException(status_code=400, detail="No players in payload")

    # Find next scheduled Cup race
    race = (
        db.query(Race)
        .filter(
            Race.race_date   >= date.today(),
            Race.status      == "scheduled",
            Race.race_number >  0,
        )
        .order_by(Race.race_date)
        .first()
    )
    if not race:
        raise HTTPException(status_code=404, detail="No upcoming race found")

    saved     = 0
    skipped   = 0
    unmatched = []

    for player in players:
        # ── Parse name + salary based on platform ──────────────
        if platform == "fanduel":
            first = player.get("first_name") or player.get("firstName", "")
            last  = player.get("last_name")  or player.get("lastName", "")
            # Handle FD Redux store format (first_name/last_name fields)
            if first and last:
                name = f"{first} {last}"
            else:
                name = player.get("displayName", "")
            salary   = player.get("salary")
            position = (player.get("rosterPosition") or player.get("position") or "Driver").upper()
            # FD: skip non-driver positions
            if position and position not in ("D", "DR", "DRIVER", ""):
                skipped += 1
                continue
        else:
            # DraftKings format
            name     = player.get("displayName") or player.get("playerName", "")
            salary   = player.get("salary")
            position = (player.get("position") or "").upper()
            if position and position not in ("D", "DR", "DRIVER", ""):
                skipped += 1
                continue

        if not name or not salary:
            skipped += 1
            continue

        driver_id = match_driver(db, name, race.season)
        if not driver_id:
            unmatched.append(name)
            skipped += 1
            continue

        # Salary change vs most recent previous salary for this platform
        prev = db.query(Salary).filter(
            Salary.driver_id       == driver_id,
            Salary.platform        == platform,
            Salary.roster_position == "driver",
        ).order_by(Salary.created_at.desc()).first()

        salary_change = None
        if prev and prev.race_id != race.id:
            salary_change = salary - prev.salary

        # Upsert
        existing = db.query(Salary).filter(
            Salary.race_id         == race.id,
            Salary.driver_id       == driver_id,
            Salary.platform        == platform,
            Salary.roster_position == "driver",
        ).first()

        if existing:
            existing.salary        = salary
            existing.salary_change = salary_change
        else:
            db.add(Salary(
                race_id         = race.id,
                driver_id       = driver_id,
                platform        = platform,
                salary          = salary,
                salary_change   = salary_change,
                roster_position = "driver",
            ))
        saved += 1

    db.commit()

    result = {
        "race_id":        race.id,
        "race_name":      race.race_name,
        "race_date":      str(race.race_date),
        "draft_group_id": draft_group_id,
        "platform":       platform,
        "saved":          saved,
        "skipped":        skipped,
    }
    if unmatched:
        result["unmatched_players"] = unmatched

    logger.info(f"Browser salary load ({platform}): {saved} saved for {race.race_name}")
    return result


@router.post("/race-results/load-from-browser")
async def load_race_results_from_browser(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Receives race results + loop data scraped from Racing Reference
    by the browser console script.

    Payload: {
      race_number: int,        # e.g. 3
      season: int,             # e.g. 2026
      race_meta: { cautions, caution_laps, lead_changes, actual_laps },
      results: [ { driver, finish, start, laps, status, laps_led } ],
      loop_data: [ { driver, start, mid_race, finish, high_pos, low_pos,
                     avg_pos, pass_diff, gf_passes, gf_times_passed,
                     quality_passes, pct_quality_passes, fastest_laps,
                     top15_laps, pct_top15, laps_led, pct_laps_led,
                     total_laps, driver_rating } ]
    }
    """
    from scrapers.salary_scraper import match_driver
    from scoring import calc_all_points

    race_number = payload.get("race_number")
    season      = payload.get("season")
    results_arr = payload.get("results", [])
    loop_arr    = payload.get("loop_data", [])
    race_meta   = payload.get("race_meta", {})

    if not race_number or not season:
        raise HTTPException(status_code=400, detail="race_number and season required")
    if not results_arr:
        raise HTTPException(status_code=400, detail="No results in payload")

    # Find the race
    race = (
        db.query(Race)
        .filter(Race.season == season, Race.race_number == race_number, Race.series == "cup")
        .first()
    )
    if not race:
        raise HTTPException(status_code=404,
                            detail=f"No cup race found: season={season}, race_number={race_number}")

    # Build loop_data lookup by driver name for fastest_laps + extra fields
    loop_by_name = {}
    for ld in loop_arr:
        loop_by_name[ld["driver"].strip().lower()] = ld

    # ── Process results ──────────────────────────────────────
    saved_results = 0
    saved_loop    = 0
    unmatched     = []

    for row in results_arr:
        name           = row.get("driver", "").strip()
        finish         = int(row.get("finish", 0))
        start          = int(row.get("start", 0))
        laps_completed = int(row.get("laps", 0))
        laps_led       = int(row.get("laps_led", 0))
        status_val     = row.get("status", "running").strip().lower()

        if not name or not finish:
            continue

        driver_id = match_driver(db, name, season)
        if not driver_id:
            unmatched.append(name)
            continue

        # Get fastest_laps from loop data
        ld_row = loop_by_name.get(name.lower(), {})
        fastest_laps = int(ld_row.get("fastest_laps", 0))

        # Calculate DK + FD points using scoring.py
        pts = calc_all_points(
            finish_position = finish,
            start_position  = start,
            laps_completed  = laps_completed,
            laps_led        = laps_led,
            fastest_laps    = fastest_laps,
        )

        # Upsert result
        existing = db.query(Result).filter(
            Result.race_id == race.id, Result.driver_id == driver_id
        ).first()

        result_data = dict(
            finish_position     = finish,
            start_position      = start,
            laps_completed      = laps_completed,
            laps_led            = laps_led,
            status              = status_val,
            fastest_lap         = fastest_laps > 0,
            driver_rating       = float(ld_row.get("driver_rating", 0)) or None,
            dk_points           = pts["dk_points"],
            dk_place_pts        = pts["dk_place_pts"],
            dk_place_diff_pts   = pts["dk_place_diff_pts"],
            dk_laps_led_pts     = pts["dk_laps_led_pts"],
            dk_fast_lap_pts     = pts["dk_fast_lap_pts"],
            dk_laps_complete_pts= None,
            dk_dominator_bonus  = None,
            fd_points           = pts["fd_points"],
            fd_place_pts        = pts["fd_place_pts"],
            fd_place_diff_pts   = pts["fd_place_diff_pts"],
            fd_laps_led_pts     = pts["fd_laps_led_pts"],
            fd_fast_lap_pts     = 0.0,
            fd_laps_complete_pts= pts["fd_laps_complete_pts"],
        )

        if existing:
            for k, v in result_data.items():
                setattr(existing, k, v)
        else:
            db.add(Result(race_id=race.id, driver_id=driver_id, **result_data))
        saved_results += 1

    # ── Process loop data ────────────────────────────────────
    for ld in loop_arr:
        name = ld.get("driver", "").strip()
        if not name:
            continue

        driver_id = match_driver(db, name, season)
        if not driver_id:
            continue

        loop_data = dict(
            green_flag_passes    = int(ld.get("gf_passes", 0)),
            green_flag_passed    = int(ld.get("gf_times_passed", 0)),
            quality_passes       = int(ld.get("quality_passes", 0)),
            avg_running_position = float(ld.get("avg_pos", 0)) or None,
            fastest_laps         = int(ld.get("fastest_laps", 0)),
            fastest_lap_pct      = None,
            laps_in_top15        = int(ld.get("top15_laps", 0)),
            pct_laps_in_top15    = float(ld.get("pct_top15", 0)) or None,
            passing_differential = int(ld.get("pass_diff", 0)),
            driver_rating        = float(ld.get("driver_rating", 0)) or None,
        )

        # Calculate fastest_lap_pct
        total_laps = int(ld.get("total_laps", 0))
        if total_laps > 0 and loop_data["fastest_laps"] > 0:
            loop_data["fastest_lap_pct"] = round(
                loop_data["fastest_laps"] / total_laps * 100, 2
            )

        existing_ld = db.query(LoopData).filter(
            LoopData.race_id == race.id, LoopData.driver_id == driver_id
        ).first()

        if existing_ld:
            for k, v in loop_data.items():
                setattr(existing_ld, k, v)
        else:
            db.add(LoopData(race_id=race.id, driver_id=driver_id, **loop_data))
        saved_loop += 1

    # ── Update race metadata ─────────────────────────────────
    if race_meta:
        if race_meta.get("actual_laps"):
            race.actual_laps = int(race_meta["actual_laps"])
        if race_meta.get("cautions"):
            race.caution_count = int(race_meta["cautions"])
        if race_meta.get("caution_laps"):
            race.caution_laps = int(race_meta["caution_laps"])
        if race_meta.get("lead_changes"):
            race.lead_changes = int(race_meta["lead_changes"])
    race.status = "completed"

    db.commit()

    resp = {
        "race_id":       race.id,
        "race_name":     race.race_name,
        "season":        season,
        "race_number":   race_number,
        "results_saved": saved_results,
        "loop_saved":    saved_loop,
    }
    if unmatched:
        resp["unmatched_drivers"] = unmatched

    logger.info(f"Racing Reference load: {saved_results} results + {saved_loop} loop for {race.race_name}")
    return resp


@router.get("/data-status")
def data_status(db: Session = Depends(get_db)):
    from sqlalchemy import func
    from models import Driver, Track, Salary, Simulation

    total_drivers  = db.query(func.count(Driver.id)).scalar()
    active_drivers = db.query(func.count(Driver.id)).filter(Driver.active == True).scalar()
    total_tracks   = db.query(func.count(Track.id)).scalar()
    total_races    = db.query(func.count(Race.id)).scalar()
    total_results  = db.query(func.count(Result.id)).scalar()
    total_loop     = db.query(func.count(LoopData.id)).scalar()
    total_qual     = db.query(func.count(Qualifying.id)).scalar()
    total_salaries = db.query(func.count(Salary.id)).scalar()
    total_sims     = db.query(func.count(Simulation.id)).scalar()

    current_year = date.today().year
    season_races = db.query(func.count(Race.id)).filter(Race.season == current_year).scalar()
    completed    = db.query(func.count(Race.id)).filter(
        Race.season == current_year, Race.status == "completed"
    ).scalar()
    scheduled    = db.query(func.count(Race.id)).filter(
        Race.season == current_year, Race.status == "scheduled"
    ).scalar()

    return {
        "totals": {
            "drivers":        total_drivers,
            "active_drivers": active_drivers,
            "tracks":         total_tracks,
            "races":          total_races,
            "results":        total_results,
            "loop_data":      total_loop,
            "qualifying":     total_qual,
            "salaries":       total_salaries,
            "simulations":    total_sims,
        },
        "current_season": {
            "year":        current_year,
            "total_races": season_races,
            "completed":   completed,
            "scheduled":   scheduled,
        },
    }


@router.get("/next-race")
def next_race_info(db: Session = Depends(get_db)):
    from models import Salary
    from sqlalchemy import func

    race = (
        db.query(Race)
        .filter(Race.race_date >= date.today(), Race.status == "scheduled")
        .order_by(Race.race_date)
        .first()
    )
    if not race:
        return {"message": "No upcoming races found"}

    qual_count = db.query(func.count(Qualifying.id)).filter(
        Qualifying.race_id == race.id
    ).scalar()

    salary_count = db.query(func.count(Salary.id)).filter(
        Salary.race_id == race.id
    ).scalar()

    return {
        "race_id":     race.id,
        "race_name":   race.race_name,
        "race_date":   str(race.race_date),
        "race_number": race.race_number,
        "qualifying":  qual_count,
        "salaries":    salary_count,
        "ready":       qual_count > 0 and salary_count > 0,
    }


# ── Sim Settings ──────────────────────────────────────────────

@router.get("/sim-settings")
def get_sim_settings(db: Session = Depends(get_db)):
    """Return current simulation settings (singleton row)."""
    settings = db.query(SimSettings).filter(SimSettings.id == 1).first()
    if not settings:
        settings = SimSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return _settings_dict(settings)


@router.put("/sim-settings")
def update_sim_settings(payload: dict, db: Session = Depends(get_db)):
    """Update simulation settings. Accepts any subset of keys."""
    settings = db.query(SimSettings).filter(SimSettings.id == 1).first()
    if not settings:
        settings = SimSettings(id=1)
        db.add(settings)

    # Integer fields (sample sizes 1-50, weights 0-100, variance 10-300)
    INT_FIELDS = {
        "form_window": (1, 50), "tt_form_window": (1, 50),
        "track_rating_window": (1, 50), "recent_form_races": (1, 50),
        "w_finish_track_type": (0, 100), "w_finish_specific_track": (0, 100),
        "w_finish_recent_form": (0, 100), "w_finish_loop_data": (0, 100),
        "w_laps_led_loop": (0, 100), "w_fast_laps_loop": (0, 100),
        "variance_finish": (10, 300), "variance_laps_led": (10, 300),
        "variance_fast_laps": (10, 300),
    }
    BOOL_FIELDS = {"use_track_type", "use_specific_track", "use_recent_form"}

    updated = []
    for key, (lo, hi) in INT_FIELDS.items():
        if key in payload:
            val = int(payload[key])
            if val < lo or val > hi:
                raise HTTPException(status_code=400, detail=f"{key} must be between {lo} and {hi}")
            setattr(settings, key, val)
            updated.append(key)

    for key in BOOL_FIELDS:
        if key in payload:
            setattr(settings, key, bool(payload[key]))
            updated.append(key)

    if not updated:
        raise HTTPException(status_code=400, detail="No valid settings provided")

    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    logger.info(f"Sim settings updated: {updated}")
    result = _settings_dict(settings)
    result["updated_fields"] = updated
    return result


def _settings_dict(settings) -> dict:
    """Convert SimSettings row to API response dict."""
    return {
        "form_window":            settings.form_window,
        "tt_form_window":         settings.tt_form_window,
        "track_rating_window":    settings.track_rating_window,
        "recent_form_races":      settings.recent_form_races,
        "use_track_type":         settings.use_track_type,
        "use_specific_track":     settings.use_specific_track,
        "use_recent_form":        settings.use_recent_form,
        "w_finish_track_type":    settings.w_finish_track_type,
        "w_finish_specific_track":settings.w_finish_specific_track,
        "w_finish_recent_form":   settings.w_finish_recent_form,
        "w_finish_loop_data":     settings.w_finish_loop_data,
        "w_laps_led_loop":        settings.w_laps_led_loop,
        "w_fast_laps_loop":       settings.w_fast_laps_loop,
        "variance_finish":        settings.variance_finish,
        "variance_laps_led":      settings.variance_laps_led,
        "variance_fast_laps":     settings.variance_fast_laps,
        "updated_at":             str(settings.updated_at) if settings.updated_at else None,
    }
