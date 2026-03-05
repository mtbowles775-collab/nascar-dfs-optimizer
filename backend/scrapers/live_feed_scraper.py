# ============================================================
# scrapers/live_feed_scraper.py
# Pulls race results + loop data from NASCAR live feed
# Source: cf.nascar.com/live/feeds/
#
# FIXED: Now imports scoring from scoring.py (single source of truth)
# instead of maintaining duplicate scoring constants.
# ============================================================

import httpx
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_

logger = logging.getLogger(__name__)

LIVE_FEED_URL = "https://cf.nascar.com/live/feeds/live-feed.json"
STAGE_POINTS_URL = "https://cf.nascar.com/live/feeds/live-stage-points.json"
PIT_DATA_URL = "https://cf.nascar.com/live/feeds/live-pit-data.json"

# NASCAR series_id → our series string
SERIES_MAP = {1: "cup", 2: "xfinity", 3: "trucks"}


def calculate_dk_points(
    finish: int,
    start: int,
    laps_led: int,
    is_fastest_lap: bool,
    laps_completed: int,
    total_laps: int,
) -> dict:
    """
    Calculate DraftKings fantasy points from raw race data.
    Uses scoring.py as the single source of truth for point values.
    """
    from scoring import calc_dk_points

    # The live feed gives us is_fastest_lap as a boolean, but scoring.py
    # expects fastest_laps as a count. Convert: True → 1 fastest lap.
    fastest_laps = 1 if is_fastest_lap else 0

    result = calc_dk_points(
        finish_position=finish,
        start_position=start,
        laps_led=laps_led,
        fastest_laps=fastest_laps,
    )

    # Add laps_complete_pts (not in base scoring.py — completion bonus)
    # DK awards 0 for laps completed in standard Classic format
    result["dk_laps_complete_pts"] = 0.0

    # Add dominator bonus (not in base scoring.py)
    # DK does NOT have a dominator bonus in Classic format
    result["dk_dominator_bonus"] = 0.0

    return result


def calculate_fd_points(
    finish: int,
    start: int,
    laps_led: int,
    is_fastest_lap: bool,
    laps_completed: int,
    total_laps: int,
) -> dict:
    """
    Calculate FanDuel fantasy points from raw race data.
    Uses scoring.py as the single source of truth for point values.
    """
    from scoring import calc_fd_points

    result = calc_fd_points(
        finish_position=finish,
        start_position=start,
        laps_completed=laps_completed,
        laps_led=laps_led,
    )

    # FD does not have a separate fastest lap bonus in standard scoring
    result["fd_fast_lap_pts"] = 0.0

    return result


def _get_or_create_driver(db: Session, vehicle: dict) -> int:
    """Find or create a driver from live feed vehicle data. Returns driver_id."""
    from models import Driver

    nascar_id = vehicle["driver"]["driver_id"]

    # Try by NASCAR driver_id first (fastest path)
    driver = db.query(Driver).filter(Driver.nascar_driver_id == nascar_id).first()
    if driver:
        return driver.id

    # Fallback: match by name
    full_name = vehicle["driver"]["full_name"]
    # Handle "(i)" ineligible marker: "Shane Van Gisbergen(i)" → "Shane Van Gisbergen"
    clean_name = full_name.replace("(i)", "").strip()
    parts = clean_name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    driver = db.query(Driver).filter(
        Driver.first_name == first_name,
        Driver.last_name == last_name,
    ).first()

    if driver:
        # Backfill the nascar_driver_id
        driver.nascar_driver_id = nascar_id
        db.flush()
        return driver.id

    # Create new driver
    driver = Driver(
        first_name=first_name,
        last_name=last_name,
        nascar_driver_id=nascar_id,
        active=True,
    )
    db.add(driver)
    db.flush()
    logger.info(f"Created new driver: {first_name} {last_name} (NASCAR ID: {nascar_id})")
    return driver.id


def _find_or_create_race(db: Session, feed_data: dict) -> "Race":
    """Find or create a Race record from live feed data. Returns Race object."""
    from models import Race, Track

    nascar_race_id = feed_data["race_id"]
    series = SERIES_MAP.get(feed_data["series_id"], "cup")

    # Try by nascar_race_id first
    race = db.query(Race).filter(Race.nascar_race_id == nascar_race_id).first()
    if race:
        return race

    # Try matching by track name + approximate date
    track_name = feed_data["track_name"]
    track = db.query(Track).filter(Track.name.ilike(f"%{track_name}%")).first()

    if track:
        # Look for a scheduled race at this track in the current season
        today = datetime.now(timezone.utc).date()
        race = (
            db.query(Race)
            .filter(
                Race.track_id == track.id,
                Race.season == today.year,
                Race.series == series,
                Race.status == "scheduled",
            )
            .order_by(Race.race_date)
            .first()
        )
        if race:
            race.nascar_race_id = nascar_race_id
            db.flush()
            return race

    # If we still can't find it, create a minimal race record
    logger.warning(f"Could not match race {nascar_race_id} to existing record, creating new one")
    today = datetime.now(timezone.utc).date()
    race = Race(
        season=today.year,
        race_number=0,   # placeholder — needs manual fix
        series=series,
        track_id=track.id if track else 1,
        race_name=feed_data.get("run_name", "Unknown Race"),
        race_date=today,
        scheduled_laps=feed_data["laps_in_race"],
        nascar_race_id=nascar_race_id,
        status="scheduled",
        notes="Auto-created from live feed — verify race_number and track_id",
    )
    db.add(race)
    db.flush()
    return race


async def scrape_live_feed(db: Session) -> dict:
    """
    Pull the NASCAR live feed and save Results + LoopData.
    Returns summary dict. Safe to call multiple times (upserts).
    """
    from models import Result, LoopData, Race

    async with httpx.AsyncClient(timeout=15) as client:
        # ── Fetch live feed ──
        resp = await client.get(LIVE_FEED_URL)
        if resp.status_code != 200:
            return {"error": f"Live feed returned {resp.status_code}"}

        feed = resp.json()
        vehicles = feed.get("vehicles", [])

        if not vehicles:
            return {"error": "No vehicles in live feed"}

        # ── Check race status ──
        run_type = feed.get("run_type")
        if run_type != 3:
            return {
                "skipped": True,
                "reason": f"run_type={run_type} (not a race). 1=practice, 2=qualifying, 3=race",
            }

        total_laps = feed["laps_in_race"]
        current_lap = feed["lap_number"]
        is_complete = current_lap >= total_laps

        # ── Find/create race record ──
        race = _find_or_create_race(db, feed)

        # Update race metadata
        race.actual_laps = current_lap
        race.caution_segments = feed.get("number_of_caution_segments")
        race.caution_laps = feed.get("number_of_caution_laps")
        race.lead_changes = feed.get("number_of_lead_changes")
        race.number_of_leaders = feed.get("number_of_leaders")

        if is_complete:
            race.status = "completed"

        # Stage info
        stage_info = feed.get("stage", {})
        if stage_info.get("stage_num") == 3:
            race.stage3_laps = stage_info.get("laps_in_stage")

        # ── Fetch stage points ──
        stage_points_map = {}  # {nascar_driver_id: {1: pts, 2: pts}}
        try:
            stage_resp = await client.get(STAGE_POINTS_URL)
            if stage_resp.status_code == 200:
                for stage in stage_resp.json():
                    stage_num = stage.get("stage_number")
                    race_id_check = stage.get("race_id")
                    if race_id_check != feed["race_id"]:
                        continue
                    for r in stage.get("results", []):
                        driver_key = r.get("driver_id") or r.get("vehicle_number")
                        if driver_key not in stage_points_map:
                            stage_points_map[driver_key] = {}
                        stage_points_map[driver_key][stage_num] = r.get("stage_points", 0)
        except Exception as e:
            logger.warning(f"Stage points fetch failed: {e}")

        # ── Find who had fastest lap ──
        fastest_driver_id = None
        if vehicles:
            fastest = max(vehicles, key=lambda v: v.get("best_lap_speed", 0))
            fastest_driver_id = fastest["driver"]["driver_id"]

        # ── Process each vehicle ──
        results_saved = 0
        loop_saved = 0

        for v in vehicles:
            driver_id = _get_or_create_driver(db, v)
            nascar_did = v["driver"]["driver_id"]
            finish = v["running_position"]
            start = v["starting_position"]
            laps_completed = v["laps_completed"]

            # Calculate laps led from segments
            laps_led = sum(
                seg["end_lap"] - seg["start_lap"] + 1
                for seg in v.get("laps_led", [])
            )

            is_fastest = (nascar_did == fastest_driver_id)

            # ── DK + FD Fantasy Points (using scoring.py via wrappers) ──
            dk = calculate_dk_points(finish, start, laps_led, is_fastest, laps_completed, total_laps)
            fd = calculate_fd_points(finish, start, laps_led, is_fastest, laps_completed, total_laps)

            # ── Upsert Result ──
            result = db.query(Result).filter(
                Result.race_id == race.id,
                Result.driver_id == driver_id,
            ).first()

            result_data = dict(
                finish_position     = finish,
                start_position      = start,
                laps_completed      = laps_completed,
                laps_led            = laps_led,
                status              = "running" if v["status"] == 1 else "out",
                fastest_lap         = is_fastest,
                fastest_lap_speed   = v.get("best_lap_speed"),
                fastest_lap_time    = v.get("best_lap_time"),
                **dk, **fd,
            )

            if result:
                for k, val in result_data.items():
                    setattr(result, k, val)
            else:
                result = Result(race_id=race.id, driver_id=driver_id, **result_data)
                db.add(result)
            results_saved += 1

            # ── Upsert LoopData ──
            loop = db.query(LoopData).filter(
                LoopData.race_id == race.id,
                LoopData.driver_id == driver_id,
            ).first()

            # Stage points for this driver
            driver_stages = stage_points_map.get(nascar_did, {})
            s1_pts = driver_stages.get(1, 0)
            s2_pts = driver_stages.get(2, 0)

            total_laps_safe = max(laps_completed, 1)
            loop_data = dict(
                green_flag_passes       = v.get("passes_made", 0),
                green_flag_passed       = v.get("times_passed", 0),
                quality_passes          = v.get("quality_passes", 0),
                avg_running_position    = v.get("average_running_position"),
                fastest_lap_pct         = round(v.get("fastest_laps_run", 0) / total_laps_safe * 100, 2),
                passing_differential    = v.get("passing_differential", 0),
                avg_speed               = v.get("average_speed"),
                avg_restart_speed       = v.get("average_restart_speed"),
                best_lap_speed          = v.get("best_lap_speed"),
                laps_position_improved  = v.get("laps_position_improved", 0),
                stage1_points           = s1_pts,
                stage2_points           = s2_pts,
                stage_points_total      = s1_pts + s2_pts,
            )

            if loop:
                for k, val in loop_data.items():
                    setattr(loop, k, val)
            else:
                loop = LoopData(race_id=race.id, driver_id=driver_id, **loop_data)
                db.add(loop)
            loop_saved += 1

        db.commit()

        return {
            "race_id":          race.id,
            "nascar_race_id":   feed["race_id"],
            "race_name":        feed.get("run_name"),
            "track_name":       feed.get("track_name"),
            "series":           SERIES_MAP.get(feed["series_id"], "unknown"),
            "laps":             f"{current_lap}/{total_laps}",
            "is_complete":      is_complete,
            "results_saved":    results_saved,
            "loop_data_saved":  loop_saved,
            "cautions":         feed.get("number_of_caution_segments"),
            "lead_changes":     feed.get("number_of_lead_changes"),
        }
