# ============================================================
# scrapers/results_scraper.py
# Scrapes race results + calculates DK/FD fantasy points
# Triggered automatically after race ends on Sunday
#
# Imports scoring directly from scoring.py (single source of truth)
# ============================================================

import httpx
from datetime import datetime
from sqlalchemy.orm import Session
from models import Race, Driver, DriverSeason, Result, Qualifying
from scoring import calc_dk_points, calc_fd_points


async def scrape_results(race_id: int, db: Session) -> int:
    """
    Pull race results, calculate DK + FD points, save to DB.
    Returns number of results saved.
    """
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise ValueError(f"Race {race_id} not found")

    url = f"https://cf.nascar.com/cacher/{race.season}/1/{race.race_number}/race-results.json"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    entries    = data.get("data", {}).get("entries", [])
    total_laps = data.get("data", {}).get("laps_in_race", race.scheduled_laps)

    # Update race with actual laps
    race.actual_laps = total_laps
    race.status      = "completed"

    # Build qualifying start position map
    qual_map = {
        q.driver_id: q.start_position
        for q in db.query(Qualifying).filter(Qualifying.race_id == race_id).all()
    }

    saved = 0
    for entry in entries:
        car_number     = str(entry.get("car_number", ""))
        finish_pos     = int(entry.get("finish_position", 0))
        laps_completed = int(entry.get("laps_completed", 0))
        laps_led       = int(entry.get("laps_led", 0))
        fastest_laps   = int(entry.get("fastest_laps_run", 0))  # count, not boolean
        status         = entry.get("status", "running").lower()
        green_fl_speed = entry.get("average_speed")

        if finish_pos == 0:
            continue

        season_info = (
            db.query(DriverSeason)
            .filter(DriverSeason.car_number == car_number, DriverSeason.season == race.season)
            .first()
        )
        if not season_info:
            continue

        driver_id = season_info.driver_id
        start_pos = qual_map.get(driver_id, finish_pos)

        # Calculate DK points — scoring.py is the single source of truth
        dk = calc_dk_points(
            finish_position=finish_pos,
            start_position=start_pos,
            laps_led=laps_led,
            fastest_laps=fastest_laps,
        )

        # Calculate FD points
        fd = calc_fd_points(
            finish_position=finish_pos,
            start_position=start_pos,
            laps_completed=laps_completed,
            laps_led=laps_led,
        )

        existing = db.query(Result).filter(
            Result.race_id == race_id, Result.driver_id == driver_id
        ).first()

        values = dict(
            finish_position      = finish_pos,
            start_position       = start_pos,
            laps_completed       = laps_completed,
            laps_led             = laps_led,
            fastest_lap          = fastest_laps > 0,   # boolean for display
            green_flag_speed     = float(green_fl_speed) if green_fl_speed else None,
            status               = status,
            dk_points            = dk["dk_points"],
            dk_place_pts         = dk["dk_place_pts"],
            dk_place_diff_pts    = dk["dk_place_diff_pts"],
            dk_laps_led_pts      = dk["dk_laps_led_pts"],
            dk_fast_lap_pts      = dk["dk_fast_lap_pts"],
            dk_laps_complete_pts = None,               # not used in DK Classic
            dk_dominator_bonus   = None,               # not used in DK Classic
            fd_points            = fd["fd_points"],
            fd_place_pts         = fd["fd_place_pts"],
            fd_place_diff_pts    = fd["fd_place_diff_pts"],
            fd_laps_led_pts      = fd["fd_laps_led_pts"],
            fd_laps_complete_pts = fd["fd_laps_complete_pts"],
            fd_fast_lap_pts      = 0.0,                # FD does not have fastest lap bonus
        )

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            db.add(Result(race_id=race_id, driver_id=driver_id, **values))

        saved += 1

    db.commit()
    return saved
