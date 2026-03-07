# ============================================================
# simulation_engine.py  —  V2: Race-Outcome-First Engine
# ============================================================
#
# Architecture:
#   1. Build driver profiles from Admin-configurable history buckets
#   2. Simulate FINISHING POSITION (independent model)
#   3. Simulate LAPS LED (uses finish + loop data)
#   4. Simulate FASTEST LAPS (uses finish + loop data)
#   5. Apply qualifying / place differential
#   6. Calculate DraftKings/FanDuel points as FINAL LAYER
#
# Key rule: DK/FD points are an OUTPUT of the simulation,
#           never the basis of the simulation.
#
# Contract: run_simulation() returns List[Dict] consumed by
# routers/simulate.py — output schema must stay compatible.
# ============================================================

import random
import math
import numpy as np
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from models import (
    Driver, Race, Result, Qualifying, Salary,
    LoopData, DriverSeason, Track, TrackType, SimSettings,
)
from scoring import calc_dk_points as _calc_dk, calc_fd_points as _calc_fd


# ── Empirical tables (from 15k+ results analysis) ───────

# Laps-led probability by finish position
# {finish_group_max: (pct_with_any_laps_led, avg_laps_led_if_leading)}
LAPS_LED_BY_FINISH = {
    1:  (0.998, 79.6),
    3:  (0.640, 27.2),
    5:  (0.506, 14.2),
    10: (0.361,  8.3),
    20: (0.199,  3.4),
    40: (0.121,  2.0),
}

# Fast-lap distribution by finish position
FAST_LAPS_BY_FINISH = {
    1:  (0.898, 29.4),
    3:  (0.894, 17.9),
    5:  (0.873, 11.6),
    10: (0.780,  7.3),
    20: (0.600,  3.7),
    40: (0.438,  2.4),
}

# Rookie baseline expected finishes by salary tier
ROOKIE_FINISH_BASELINES = {
    "high":   15.0,   # salary >= 9000
    "mid":    22.0,   # salary 7000-8999
    "low":    28.0,   # salary < 7000
}

FIELD_AVG_FINISH = 20.0
ROOKIE_SIGMA_MULTIPLIER = 1.4


# ── Scoring wrappers ────────────────────────────────────

def calc_dk_points(finish_pos, start_pos, laps_led, fastest_laps, total_laps, laps_completed):
    result = _calc_dk(
        finish_position=finish_pos,
        start_position=start_pos,
        laps_led=laps_led,
        fastest_laps=fastest_laps,
    )
    return {
        "total": result["dk_points"],
        "place": result["dk_place_pts"],
        "diff":  result["dk_place_diff_pts"],
        "led":   result["dk_laps_led_pts"],
        "fl":    result["dk_fast_lap_pts"],
    }


def calc_fd_points(finish_pos, start_pos, laps_led, fastest_laps, total_laps, laps_completed):
    result = _calc_fd(
        finish_position=finish_pos,
        start_position=start_pos,
        laps_completed=laps_completed,
        laps_led=laps_led,
    )
    return result["fd_points"]


# ── History bucket loaders ───────────────────────────────
# These pull RACE OUTCOME data (finish, laps led, driver rating, etc.)
# NOT DK/FD points. This is the core architectural change.

def _get_track_type_history(
    db: Session, driver_id: int, track_type_name: str, n_races: int
) -> Dict:
    """
    Last N races at the same TRACK TYPE.
    Returns avg_finish, finish_variance, avg_laps_led, avg_driver_rating,
    avg_running_pos, avg_fastest_laps, race_count.
    """
    rows = (
        db.query(
            Result.finish_position,
            Result.laps_led,
            LoopData.driver_rating,
            LoopData.avg_running_position,
            LoopData.fastest_laps,
            LoopData.laps_in_top5,
            LoopData.green_flag_passes,
            LoopData.green_flag_passed,
            LoopData.pct_laps_in_top15,
        )
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .outerjoin(LoopData, and_(
            LoopData.race_id == Result.race_id,
            LoopData.driver_id == Result.driver_id,
        ))
        .filter(
            Result.driver_id == driver_id,
            TrackType.name == track_type_name,
            Result.finish_position.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    if not rows:
        return {"race_count": 0}

    finishes = [float(r[0]) for r in rows]
    laps_led = [float(r[1] or 0) for r in rows]
    ratings = [float(r[2]) for r in rows if r[2]]
    run_pos = [float(r[3]) for r in rows if r[3]]
    fast_laps = [float(r[4] or 0) for r in rows]
    top5_laps = [float(r[5] or 0) for r in rows]
    gf_passes = [float(r[6] or 0) for r in rows]
    gf_passed = [float(r[7] or 0) for r in rows]
    top15_pct = [float(r[8]) for r in rows if r[8]]

    avg_finish = sum(finishes) / len(finishes)
    finish_var = np.std(finishes) if len(finishes) > 1 else 8.0

    return {
        "avg_finish":       round(avg_finish, 2),
        "finish_variance":  round(float(finish_var), 2),
        "avg_laps_led":     round(sum(laps_led) / len(laps_led), 2) if laps_led else 0,
        "avg_driver_rating":round(sum(ratings) / len(ratings), 1) if ratings else None,
        "avg_running_pos":  round(sum(run_pos) / len(run_pos), 2) if run_pos else None,
        "avg_fastest_laps": round(sum(fast_laps) / len(fast_laps), 2) if fast_laps else 0,
        "avg_top5_laps":    round(sum(top5_laps) / len(top5_laps), 1) if top5_laps else 0,
        "avg_gf_passes":    round(sum(gf_passes) / len(gf_passes), 1) if gf_passes else 0,
        "avg_gf_passed":    round(sum(gf_passed) / len(gf_passed), 1) if gf_passed else 0,
        "avg_top15_pct":    round(sum(top15_pct) / len(top15_pct), 1) if top15_pct else None,
        "race_count":       len(rows),
    }


def _get_specific_track_history(
    db: Session, driver_id: int, track_id: int, n_races: int
) -> Dict:
    """
    Last N races at the EXACT TRACK.
    Same output shape as track type history.
    """
    rows = (
        db.query(
            Result.finish_position,
            Result.laps_led,
            LoopData.driver_rating,
            LoopData.avg_running_position,
            LoopData.fastest_laps,
            LoopData.laps_in_top5,
            LoopData.pct_laps_in_top15,
        )
        .join(Race, Result.race_id == Race.id)
        .outerjoin(LoopData, and_(
            LoopData.race_id == Result.race_id,
            LoopData.driver_id == Result.driver_id,
        ))
        .filter(
            Result.driver_id == driver_id,
            Race.track_id == track_id,
            Result.finish_position.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    if not rows:
        return {"race_count": 0}

    finishes = [float(r[0]) for r in rows]
    laps_led = [float(r[1] or 0) for r in rows]
    ratings = [float(r[2]) for r in rows if r[2]]
    run_pos = [float(r[3]) for r in rows if r[3]]
    fast_laps = [float(r[4] or 0) for r in rows]
    top5_laps = [float(r[5] or 0) for r in rows]
    top15_pct = [float(r[6]) for r in rows if r[6]]

    avg_finish = sum(finishes) / len(finishes)
    finish_var = np.std(finishes) if len(finishes) > 1 else 8.0

    return {
        "avg_finish":       round(avg_finish, 2),
        "finish_variance":  round(float(finish_var), 2),
        "avg_laps_led":     round(sum(laps_led) / len(laps_led), 2) if laps_led else 0,
        "avg_driver_rating":round(sum(ratings) / len(ratings), 1) if ratings else None,
        "avg_running_pos":  round(sum(run_pos) / len(run_pos), 2) if run_pos else None,
        "avg_fastest_laps": round(sum(fast_laps) / len(fast_laps), 2) if fast_laps else 0,
        "avg_top5_laps":    round(sum(top5_laps) / len(top5_laps), 1) if top5_laps else 0,
        "avg_top15_pct":    round(sum(top15_pct) / len(top15_pct), 1) if top15_pct else None,
        "race_count":       len(rows),
    }


def _get_recent_form(
    db: Session, driver_id: int, n_races: int
) -> Dict:
    """
    Last N races at ANY track — overall recent form.
    Same output shape as track type history.
    """
    rows = (
        db.query(
            Result.finish_position,
            Result.laps_led,
            LoopData.driver_rating,
            LoopData.avg_running_position,
            LoopData.fastest_laps,
            LoopData.laps_in_top5,
            LoopData.pct_laps_in_top15,
        )
        .join(Race, Result.race_id == Race.id)
        .outerjoin(LoopData, and_(
            LoopData.race_id == Result.race_id,
            LoopData.driver_id == Result.driver_id,
        ))
        .filter(
            Result.driver_id == driver_id,
            Result.finish_position.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    if not rows:
        return {"race_count": 0}

    finishes = [float(r[0]) for r in rows]
    laps_led = [float(r[1] or 0) for r in rows]
    ratings = [float(r[2]) for r in rows if r[2]]
    run_pos = [float(r[3]) for r in rows if r[3]]
    fast_laps = [float(r[4] or 0) for r in rows]
    top5_laps = [float(r[5] or 0) for r in rows]
    top15_pct = [float(r[6]) for r in rows if r[6]]

    avg_finish = sum(finishes) / len(finishes)
    finish_var = np.std(finishes) if len(finishes) > 1 else 8.0

    return {
        "avg_finish":       round(avg_finish, 2),
        "finish_variance":  round(float(finish_var), 2),
        "avg_laps_led":     round(sum(laps_led) / len(laps_led), 2) if laps_led else 0,
        "avg_driver_rating":round(sum(ratings) / len(ratings), 1) if ratings else None,
        "avg_running_pos":  round(sum(run_pos) / len(run_pos), 2) if run_pos else None,
        "avg_fastest_laps": round(sum(fast_laps) / len(fast_laps), 2) if fast_laps else 0,
        "avg_top5_laps":    round(sum(top5_laps) / len(top5_laps), 1) if top5_laps else 0,
        "avg_top15_pct":    round(sum(top15_pct) / len(top15_pct), 1) if top15_pct else None,
        "race_count":       len(rows),
    }


def _get_loop_profile(
    db: Session, driver_id: int, track_type_name: str
) -> Dict:
    """
    Aggregate loop-data metrics at this track type (all available races).
    These are the raw pace/control signals used by finish, laps-led, and fast-lap models.
    """
    row = (
        db.query(
            func.avg(LoopData.driver_rating).label("avg_rating"),
            func.avg(LoopData.fastest_lap_pct).label("avg_fl_pct"),
            func.avg(LoopData.pct_laps_in_top15).label("avg_top15"),
            func.avg(LoopData.avg_running_position).label("avg_run_pos"),
            func.avg(LoopData.fastest_laps).label("avg_fl_count"),
            func.avg(LoopData.laps_in_top5).label("avg_top5_laps"),
            func.avg(LoopData.green_flag_passes).label("avg_gf_passes"),
            func.avg(LoopData.green_flag_passed).label("avg_gf_passed"),
            func.avg(LoopData.passing_differential).label("avg_pass_diff"),
        )
        .join(Race, LoopData.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(LoopData.driver_id == driver_id, TrackType.name == track_type_name)
        .first()
    )
    return {
        "avg_rating":     float(row.avg_rating)     if row and row.avg_rating     else 80.0,
        "avg_fl_pct":     float(row.avg_fl_pct)     if row and row.avg_fl_pct     else 3.0,
        "avg_top15":      float(row.avg_top15)      if row and row.avg_top15      else 40.0,
        "avg_run_pos":    float(row.avg_run_pos)    if row and row.avg_run_pos    else 20.0,
        "avg_fl_count":   float(row.avg_fl_count)   if row and row.avg_fl_count   else 3.0,
        "avg_top5_laps":  float(row.avg_top5_laps)  if row and row.avg_top5_laps  else 0.0,
        "avg_gf_passes":  float(row.avg_gf_passes)  if row and row.avg_gf_passes  else 0.0,
        "avg_gf_passed":  float(row.avg_gf_passed)  if row and row.avg_gf_passed  else 0.0,
        "avg_pass_diff":  float(row.avg_pass_diff)  if row and row.avg_pass_diff  else 0.0,
    }


def _get_track_caution_rate(db: Session, track_type_name: str) -> float:
    """Average caution-laps-as-pct-of-total for this track type."""
    row = (
        db.query(
            func.avg(
                Race.caution_laps * 1.0 / func.nullif(Race.scheduled_laps, 0)
            ).label("avg_caution_pct")
        )
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            TrackType.name == track_type_name,
            Race.caution_laps.isnot(None),
            Race.scheduled_laps > 0,
        )
        .first()
    )
    if row and row.avg_caution_pct:
        return min(float(row.avg_caution_pct), 0.5)
    defaults = {
        "Flat": 0.22, "Steep": 0.20, "Large Oval": 0.18,
        "Road": 0.12, "Restrictor Plate": 0.25,
    }
    return defaults.get(track_type_name, 0.20)


def _get_salary_tier(salary: Optional[int]) -> str:
    if salary is None:
        return "mid"
    if salary >= 9000:
        return "high"
    if salary >= 7000:
        return "mid"
    return "low"


def _get_track_type_name(race: Race) -> str:
    if race.track and race.track.track_type:
        return race.track.track_type.name
    return "Large Oval"


# ── Profile builder ──────────────────────────────────────

def build_driver_profiles(
    db: Session, race: Race, platform: str, settings: SimSettings,
) -> List[Dict]:
    """
    Build simulation profiles using Admin-configurable history buckets.

    For each driver, dynamically pull:
      - last N same-track-type races (if toggle on)
      - last N exact-track races (if toggle on)
      - last N total recent races (if toggle on)

    Compute expected finish from weighted blend + loop data.
    NO DK/FD points are used in profile building.
    """
    track_type_name = _get_track_type_name(race)
    total_laps      = race.scheduled_laps or 200

    # Load settings
    tt_n       = settings.tt_form_window       if settings else 6
    st_n       = settings.track_rating_window  if settings else 5
    rf_n       = settings.form_window          if settings else 10
    use_tt     = settings.use_track_type       if settings else True
    use_st     = settings.use_specific_track   if settings else True
    use_rf     = settings.use_recent_form      if settings else True

    # Weights (0-100 -> 0.0-1.0)
    w_tt       = (settings.w_finish_track_type     if settings else 35) / 100.0
    w_st       = (settings.w_finish_specific_track if settings else 25) / 100.0
    w_rf       = (settings.w_finish_recent_form    if settings else 20) / 100.0
    w_loop     = (settings.w_finish_loop_data      if settings else 20) / 100.0

    # Variance
    base_sigma = (settings.variance_finish if settings else 100) / 10.0  # default 10.0

    # Pre-load qualifying & salary maps
    qual_map = {
        q.driver_id: q.start_position
        for q in db.query(Qualifying).filter(Qualifying.race_id == race.id).all()
    }
    sal_map = {
        s.driver_id: s.salary
        for s in db.query(Salary).filter(
            Salary.race_id == race.id,
            Salary.platform == platform
        ).all()
    }

    active_driver_ids = list(sal_map.keys())
    if not active_driver_ids:
        active_drivers = db.query(Driver).filter(Driver.active == True).all()
        active_driver_ids = [d.id for d in active_drivers]

    caution_rate = _get_track_caution_rate(db, track_type_name)

    profiles = []
    for driver_id in active_driver_ids:
        driver = db.query(Driver).filter(Driver.id == driver_id).first()
        if not driver:
            continue

        season_info = (
            db.query(DriverSeason)
            .filter(DriverSeason.driver_id == driver_id)
            .order_by(DriverSeason.season.desc())
            .first()
        )

        salary = sal_map.get(driver_id)
        start_pos = qual_map.get(driver_id)

        # ── Pull history buckets based on toggles ──
        tt_hist = _get_track_type_history(db, driver_id, track_type_name, tt_n) if use_tt else {"race_count": 0}
        st_hist = _get_specific_track_history(db, driver_id, race.track_id, st_n) if use_st else {"race_count": 0}
        rf_hist = _get_recent_form(db, driver_id, rf_n) if use_rf else {"race_count": 0}

        # ── Loop data profile (always loaded) ──
        loop = _get_loop_profile(db, driver_id, track_type_name)

        # ── Compute expected finish (weighted blend) ──
        finish_components = []
        active_weights = []

        if use_tt and tt_hist["race_count"] > 0:
            finish_components.append(tt_hist["avg_finish"])
            active_weights.append(w_tt)

        if use_st and st_hist["race_count"] > 0:
            finish_components.append(st_hist["avg_finish"])
            active_weights.append(w_st)

        if use_rf and rf_hist["race_count"] > 0:
            finish_components.append(rf_hist["avg_finish"])
            active_weights.append(w_rf)

        # Loop data contribution: avg_running_position is the best finish proxy
        if loop["avg_run_pos"] and loop["avg_run_pos"] < 40:
            finish_components.append(loop["avg_run_pos"])
            active_weights.append(w_loop)

        if finish_components and sum(active_weights) > 0:
            # Normalize weights to sum to 1.0
            total_w = sum(active_weights)
            expected_finish = sum(
                f * (w / total_w) for f, w in zip(finish_components, active_weights)
            )
        else:
            # Rookie / no-history fallback
            tier = _get_salary_tier(salary)
            expected_finish = ROOKIE_FINISH_BASELINES[tier]

        # ── Finish variance (from history or default) ──
        variances = []
        if tt_hist["race_count"] > 1:
            variances.append(tt_hist["finish_variance"])
        if rf_hist["race_count"] > 1:
            variances.append(rf_hist["finish_variance"])
        historical_var = sum(variances) / len(variances) if variances else 8.0

        # Blend historical variance with admin-controlled base sigma
        driver_sigma = (historical_var * 0.5 + base_sigma * 0.5)

        # Widen for thin history
        total_races = tt_hist["race_count"] + st_hist["race_count"] + rf_hist["race_count"]
        is_rookie = total_races < 5
        if is_rookie:
            driver_sigma *= ROOKIE_SIGMA_MULTIPLIER

        # Driver rating multiplier (100 = average, so higher = stronger)
        # Use it to slightly adjust expected finish: higher rating -> lower (better) finish
        rating = loop["avg_rating"]
        rating_adj = (100.0 - rating) * 0.05  # +5 rating -> 0.25 better finish
        expected_finish = max(1.0, expected_finish + rating_adj)

        # ── Laps-led strength (for laps-led model) ──
        ll_history_avg = 0.0
        ll_count = 0
        if tt_hist["race_count"] > 0 and tt_hist.get("avg_laps_led"):
            ll_history_avg += tt_hist["avg_laps_led"]
            ll_count += 1
        if st_hist["race_count"] > 0 and st_hist.get("avg_laps_led"):
            ll_history_avg += st_hist["avg_laps_led"]
            ll_count += 1
        if rf_hist["race_count"] > 0 and rf_hist.get("avg_laps_led"):
            ll_history_avg += rf_hist["avg_laps_led"]
            ll_count += 1
        ll_history_avg = ll_history_avg / ll_count if ll_count > 0 else 0

        # ── Fastest-laps strength ──
        fl_history_avg = 0.0
        fl_count = 0
        if tt_hist["race_count"] > 0 and tt_hist.get("avg_fastest_laps"):
            fl_history_avg += tt_hist["avg_fastest_laps"]
            fl_count += 1
        if st_hist["race_count"] > 0 and st_hist.get("avg_fastest_laps"):
            fl_history_avg += st_hist["avg_fastest_laps"]
            fl_count += 1
        if rf_hist["race_count"] > 0 and rf_hist.get("avg_fastest_laps"):
            fl_history_avg += rf_hist["avg_fastest_laps"]
            fl_count += 1
        fl_history_avg = fl_history_avg / fl_count if fl_count > 0 else 0

        # ── Display-only fields (backwards compatible with frontend) ──
        current_form_finish = rf_hist.get("avg_finish") if rf_hist["race_count"] > 0 else None
        tt_form_finish = tt_hist.get("avg_finish") if tt_hist["race_count"] > 0 else None
        track_driver_rating = st_hist.get("avg_driver_rating") if st_hist["race_count"] > 0 else None

        profiles.append({
            "driver_id":        driver_id,
            "driver_name":      driver.full_name,
            "car_number":       season_info.car_number if season_info else "??",
            "team_name":        season_info.team.name if season_info and season_info.team else None,
            "manufacturer":     season_info.manufacturer.name if season_info and season_info.manufacturer else None,
            "salary":           salary,
            "start_position":   start_pos,
            # Core simulation inputs
            "expected_finish":  round(expected_finish, 2),
            "sigma":            round(driver_sigma, 2),
            "is_rookie":        is_rookie,
            "total_laps":       total_laps,
            "track_type":       track_type_name,
            "caution_rate":     caution_rate,
            # Loop data
            "loop":             loop,
            # Laps-led and fast-lap model inputs
            "ll_history_avg":   ll_history_avg,
            "fl_history_avg":   fl_history_avg,
            # Display-only fields (backwards compatible)
            "current_form_finish": current_form_finish,
            "current_form_pts":    None,  # No longer tracking DK pts in profiles
            "current_form_races":  rf_hist["race_count"],
            "tt_form_finish":      tt_form_finish,
            "tt_form_races":       tt_hist["race_count"],
            "driver_rating":       track_driver_rating,
            "avg_fl_count":        loop["avg_fl_count"],
        })

    return profiles


# ── Step 1: Finish simulation ────────────────────────────

def _simulate_finish_order(profiles: List[Dict]) -> List[Tuple[int, int]]:
    """
    For each driver, sample from a distribution around their expected finish.
    Rank all sampled values and assign actual finishing positions 1-N.

    Returns list of (driver_id, assigned_finish_position).
    """
    sampled = []
    for p in profiles:
        # Sample: lower expected_finish is better, noise can move them up or down
        raw_finish = random.gauss(p["expected_finish"], p["sigma"])
        # Clamp to reasonable range
        raw_finish = max(0.5, raw_finish)
        sampled.append((p["driver_id"], raw_finish))

    # Sort by sampled finish (lower = better)
    sampled.sort(key=lambda x: x[1])

    # Assign actual positions 1, 2, 3, ...
    return [(driver_id, pos + 1) for pos, (driver_id, _) in enumerate(sampled)]


# ── Step 2: Laps led simulation ──────────────────────────

def _simulate_laps_led(
    finish_results: List[Tuple[int, int]],
    profiles_map: Dict,
    total_laps: int,
    caution_rate: float,
    w_loop: float,
) -> Dict[int, int]:
    """
    Simulate laps led using:
      - Finish position (from Step 1)
      - Historical dominance (ll_history_avg)
      - Loop data (driver rating, top5 laps)
      - Caution rate (more cautions = more spread)

    Loop data weight (w_loop) controls how much loop data matters vs finish position.
    """
    n_drivers = len(finish_results)
    laps_led = {d: 0 for d, _ in finish_results}

    # Caution dispersion factor
    dispersion = 1.0 + (caution_rate - 0.20) * 2.0

    for driver_id, finish_pos in finish_results:
        p = profiles_map[driver_id]

        # Empirical baseline from finish position
        prob, avg = _laps_led_for_position(finish_pos)
        scale = total_laps / 200.0
        avg_scaled = avg * scale

        # Loop data boost: driver_rating and historical laps-led
        loop_boost = 1.0
        if w_loop > 0:
            rating = p["loop"]["avg_rating"]
            # Above-average rating increases laps led chance
            rating_factor = (rating / 100.0) ** 1.5  # exponential impact
            # Historical laps-led gives direct boost
            hist_ll = p.get("ll_history_avg", 0)
            hist_factor = 1.0 + (hist_ll / max(total_laps, 1)) * 3.0
            loop_boost = (1.0 - w_loop) + w_loop * rating_factor * hist_factor

        avg_scaled *= loop_boost

        # Apply caution dispersion
        if finish_pos <= 3:
            avg_scaled *= max(0.4, 1.0 / dispersion)
        elif finish_pos > 10:
            avg_scaled *= min(2.0, dispersion * 0.8)

        # Roll probability
        if random.random() > prob:
            continue

        laps = max(1, int(random.expovariate(1.0 / max(avg_scaled, 1.0))))
        laps_led[driver_id] = laps

    # Clamp total
    total_led = sum(laps_led.values())
    if total_led > total_laps:
        scale_down = total_laps / total_led
        laps_led = {k: max(0, int(v * scale_down)) for k, v in laps_led.items()}

    # Winner must lead at least 1 lap
    winner_id = finish_results[0][0]
    if laps_led[winner_id] == 0:
        laps_led[winner_id] = max(1, int(total_laps * 0.05))

    return laps_led


def _laps_led_for_position(finish_pos: int) -> Tuple[float, float]:
    for max_pos, (prob, avg) in sorted(LAPS_LED_BY_FINISH.items()):
        if finish_pos <= max_pos:
            return (prob, avg)
    return (0.10, 1.5)


# ── Step 3: Fastest laps simulation ──────────────────────

def _simulate_fast_laps(
    finish_results: List[Tuple[int, int]],
    laps_led_map: Dict[int, int],
    profiles_map: Dict,
    total_laps: int,
    w_loop: float,
) -> Dict[int, int]:
    """
    Simulate fastest laps using:
      - Finish position
      - Laps led (correlation)
      - Historical FL% from loop data
      - Green flag speed signals

    Fastest laps are correlated with laps led but not identical.
    """
    fast_laps = {d: 0 for d, _ in finish_results}

    shares = {}
    for driver_id, finish_pos in finish_results:
        p = profiles_map[driver_id]

        # Empirical baseline from finish position
        _, avg_fl = _fast_laps_for_position(finish_pos)
        baseline_rate = avg_fl / 200.0

        # Loop data contribution: historical FL%
        hist_fl_pct = p["loop"]["avg_fl_pct"] / 100.0
        hist_fl_avg = p.get("fl_history_avg", 0) / max(total_laps, 1)

        # Laps led correlation: if you lead laps, you probably have fast laps
        ll_bonus = (laps_led_map.get(driver_id, 0) / max(total_laps, 1)) * 0.3

        # Blend
        loop_signal = (hist_fl_pct + hist_fl_avg) / 2.0
        blended = baseline_rate * (1.0 - w_loop) + (loop_signal + ll_bonus) * w_loop

        shares[driver_id] = max(0.005, min(blended, 0.25))

    # Normalize
    total_share = sum(shares.values())
    if total_share == 0:
        return fast_laps

    for driver_id, _ in finish_results:
        expected = (shares[driver_id] / total_share) * total_laps
        noisy = max(0, int(random.gauss(expected, max(1.0, expected * 0.4))))
        fast_laps[driver_id] = noisy

    # Clamp
    total_fl = sum(fast_laps.values())
    if total_fl > total_laps:
        scale = total_laps / total_fl
        fast_laps = {k: max(0, int(v * scale)) for k, v in fast_laps.items()}

    return fast_laps


def _fast_laps_for_position(finish_pos: int) -> Tuple[float, float]:
    for max_pos, (prob, avg) in sorted(FAST_LAPS_BY_FINISH.items()):
        if finish_pos <= max_pos:
            return (prob, avg)
    return (0.30, 1.5)


# ── Main simulation ─────────────────────────────────────

def run_simulation(
    db: Session, race: Race, n_sims: int,
    platform: str = "draftkings",
    settings: SimSettings = None,
    # Legacy params (still accepted for backwards compatibility)
    recent_form_races: int = 5,
    form_window: int = 10,
    tt_form_window: int = 6,
    track_rating_window: int = 5,
) -> List[Dict]:
    """
    V2 Race-Outcome-First Monte Carlo Engine.

    For each simulation:
      1. Simulate finishing positions
      2. Simulate laps led
      3. Simulate fastest laps
      4. Apply qualifying (place differential)
      5. Calculate DK/FD points as final layer
    """
    # Load settings from DB if not provided
    if settings is None:
        settings = db.query(SimSettings).filter(SimSettings.id == 1).first()

    profiles = build_driver_profiles(db, race, platform, settings)
    n_drivers = len(profiles)
    total_laps = race.scheduled_laps or 200

    profiles_map = {p["driver_id"]: p for p in profiles}
    caution_rate = profiles[0]["caution_rate"] if profiles else 0.20

    # Weights for sub-models
    w_ll_loop = (settings.w_laps_led_loop if settings else 60) / 100.0
    w_fl_loop = (settings.w_fast_laps_loop if settings else 60) / 100.0

    # Accumulators
    accum = {
        p["driver_id"]: {
            **p,
            "wins": 0, "top3": 0, "top5": 0, "top10": 0,
            "fp_sum": 0.0, "finish_sum": 0.0,
            "laps_led_sum": 0.0, "fast_lap_sum": 0.0,
            "all_fp": [],
        }
        for p in profiles
    }

    for _ in range(n_sims):
        # Step 1: Simulate finish order
        finish_results = _simulate_finish_order(profiles)

        # Step 2: Simulate laps led
        laps_led_map = _simulate_laps_led(
            finish_results, profiles_map, total_laps, caution_rate, w_ll_loop
        )

        # Step 3: Simulate fastest laps
        fast_laps_map = _simulate_fast_laps(
            finish_results, laps_led_map, profiles_map, total_laps, w_fl_loop
        )

        # Steps 4 & 5: Apply qualifying + calculate DK/FD points
        for driver_id, finish_pos in finish_results:
            p = accum[driver_id]
            start_pos = p["start_position"] or finish_pos
            laps_led = laps_led_map.get(driver_id, 0)
            fastest_laps = fast_laps_map.get(driver_id, 0)
            laps_completed = total_laps if finish_pos <= n_drivers * 0.85 else int(total_laps * 0.65)

            if platform == "draftkings":
                pts = calc_dk_points(finish_pos, start_pos, laps_led, fastest_laps,
                                     total_laps, laps_completed)["total"]
            else:
                pts = calc_fd_points(finish_pos, start_pos, laps_led, fastest_laps,
                                     total_laps, laps_completed)

            p["fp_sum"]       += pts
            p["finish_sum"]   += finish_pos
            p["laps_led_sum"] += laps_led
            p["fast_lap_sum"] += fastest_laps
            if finish_pos == 1:  p["wins"]  += 1
            if finish_pos <= 3:  p["top3"]  += 1
            if finish_pos <= 5:  p["top5"]  += 1
            if finish_pos <= 10: p["top10"] += 1
            p["all_fp"].append(pts)

    # ── Aggregate results ──
    results = []
    for driver_id, a in accum.items():
        sorted_fp = sorted(a["all_fp"])
        avg_fp    = a["fp_sum"] / n_sims
        salary    = a["salary"] or 7000
        value     = avg_fp / (salary / 1000) if salary else 0
        avg_ll    = a["laps_led_sum"] / n_sims
        avg_fl    = a["fast_lap_sum"] / n_sims
        fl_pct    = avg_fl / total_laps if total_laps > 0 else 0
        win_pct   = a["wins"] / n_sims
        avg_finish = a["finish_sum"] / n_sims

        dom_score = avg_ll * 0.25 + avg_fl * 0.10

        # Ownership projection
        salary_rank = _salary_rank(a["salary"], [p["salary"] for p in profiles])
        proj_own = _project_ownership(win_pct, value, salary_rank, n_drivers)
        leverage = avg_fp / proj_own if proj_own > 0 else 0

        results.append({
            "driver_id":        driver_id,
            "driver_name":      a["driver_name"],
            "car_number":       a["car_number"],
            "team_name":        a["team_name"],
            "manufacturer":     a["manufacturer"],
            "salary":           a["salary"],
            "start_position":   a["start_position"],
            "avg_fp":           round(avg_fp, 2),
            "median_fp":        round(sorted_fp[int(n_sims * 0.5)], 2),
            "floor_fp":         round(sorted_fp[int(n_sims * 0.1)], 2),
            "ceiling_fp":       round(sorted_fp[int(n_sims * 0.9)], 2),
            "avg_finish":       round(avg_finish, 2),
            "avg_laps_led":     round(avg_ll, 2),
            "fast_lap_pct":     round(fl_pct, 4),
            "win_pct":          round(win_pct, 4),
            "top3_pct":         round(a["top3"] / n_sims, 4),
            "top5_pct":         round(a["top5"] / n_sims, 4),
            "top10_pct":        round(a["top10"] / n_sims, 4),
            "proj_ownership":   round(proj_own, 2),
            "leverage_score":   round(leverage, 2),
            "value":            round(value, 3),
            "dominator_score":  round(dom_score, 2),
            # Display metrics (backwards compatible)
            "current_form_finish": a.get("current_form_finish"),
            "current_form_pts":    a.get("current_form_pts"),
            "current_form_races":  a.get("current_form_races"),
            "tt_form_finish":      a.get("tt_form_finish"),
            "tt_form_races":       a.get("tt_form_races"),
            "driver_rating":       a.get("driver_rating"),
            "avg_fast_laps":       round(avg_fl, 2),
        })

    results.sort(key=lambda x: x["avg_fp"], reverse=True)
    return results


# ── Ownership projection ────────────────────────────────

def _salary_rank(salary: Optional[int], all_salaries: List[Optional[int]]) -> float:
    valid = sorted([s for s in all_salaries if s], reverse=True)
    if not salary or not valid:
        return 0.5
    try:
        idx = valid.index(salary)
        return 1.0 - (idx / len(valid))
    except ValueError:
        return 0.5


def _project_ownership(win_pct: float, value: float, salary_rank: float,
                       n_drivers: int) -> float:
    salary_base = 3.0 + salary_rank * 22.0
    win_boost = win_pct * 80.0
    value_boost = max(0, (value - 3.0)) * 2.0
    raw = salary_base + win_boost + value_boost + random.gauss(0, 2.5)
    return max(1.5, min(55.0, raw))


# ── Lineup Optimizer ────────────────────────────────────

def optimize_lineups(
    sim_results: List[Dict], salary_cap: int, n_lineups: int,
    lock_drivers: List[int], exclude_drivers: List[int],
    max_ownership: Optional[float], min_salary: Optional[int],
    lineup_size: int = 6,
) -> List[Dict]:
    """
    Generate N optimal DFS lineups from simulation results.
    """
    eligible = [
        r for r in sim_results
        if r["driver_id"] not in exclude_drivers
        and r["salary"] is not None
        and (max_ownership is None or r["proj_ownership"] <= max_ownership)
    ]

    def score(r, randomness=0.15):
        base = r["avg_fp"] * 0.55 + r["ceiling_fp"] * 0.30 + r["floor_fp"] * 0.15
        return base * (1 + random.uniform(-randomness, randomness))

    generated = []
    seen = set()
    attempts = 0
    max_attempts = n_lineups * 50

    while len(generated) < n_lineups and attempts < max_attempts:
        attempts += 1
        budget = salary_cap
        lineup = []
        used = set()

        for locked_id in lock_drivers:
            driver = next((r for r in eligible if r["driver_id"] == locked_id), None)
            if driver and driver["salary"] <= budget:
                lineup.append(driver)
                budget -= driver["salary"]
                used.add(locked_id)

        if len(lineup) > lineup_size:
            continue

        pool = sorted(
            [r for r in eligible if r["driver_id"] not in used],
            key=score, reverse=True
        )

        for driver in pool:
            if len(lineup) >= lineup_size:
                break
            if driver["salary"] > budget:
                continue
            lineup.append(driver)
            budget -= driver["salary"]
            used.add(driver["driver_id"])

        if len(lineup) < lineup_size:
            continue

        key = frozenset(d["driver_id"] for d in lineup)
        if key in seen:
            continue
        seen.add(key)

        total_salary = sum(d["salary"] for d in lineup)
        proj_fp = sum(d["avg_fp"] for d in lineup)
        proj_ceiling = sum(d["ceiling_fp"] for d in lineup)

        generated.append({
            "lineup":           lineup,
            "total_salary":     total_salary,
            "salary_remaining": salary_cap - total_salary,
            "proj_fp":          round(proj_fp, 2),
            "proj_ceiling":     round(proj_ceiling, 2),
        })

    generated.sort(key=lambda x: x["proj_fp"], reverse=True)
    return generated
