# ============================================================
# simulation_engine.py  —  Phase 3 Monte Carlo Engine
# ============================================================
#
# Upgrades over V1:
#   1. Recency-weighted track-type scoring (exponential decay)
#   2. Power-law laps-led distribution (fitted to real data)
#   3. Fast-lap distribution model (driver-level FL rate)
#   4. Track-type-specific variance
#   5. Rookie / thin-history handling
#   6. Caution-based laps-led dispersion
#   7. Improved ownership projection
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
    LoopData, DriverSeason, Track, TrackType,
)
from scoring import calc_dk_points as _calc_dk, calc_fd_points as _calc_fd


# ── Constants ──────────────────────────────────────────────

# Exponential decay factors per track type (higher = more weight on old data)
TRACK_TYPE_DECAY = {
    "Flat":             0.92,   # half-life ~8 races at type
    "Steep":            0.90,   # ~6
    "Large Oval":       0.88,   # ~5
    "Road":             0.85,   # ~4
    "Restrictor Plate": 0.80,   # ~3
}

# How much of the final base score comes from track-type history vs recent form
# (recent form is ALSO filtered to same track type now)
TRACK_TYPE_HISTORY_WEIGHT = {
    "Flat":             0.80,
    "Steep":            0.80,
    "Large Oval":       0.75,
    "Road":             0.70,
    "Restrictor Plate": 0.65,
}

# Noise (sigma) for Gaussian perturbation per track type
TRACK_TYPE_SIGMA = {
    "Flat":             11.0,
    "Steep":            12.0,
    "Large Oval":       10.0,
    "Road":              9.0,
    "Restrictor Plate": 14.0,
}

# Laps-led probability by finish position (from data analysis)
# {finish_group_max: (pct_with_any_laps_led, avg_laps_led_if_leading)}
LAPS_LED_BY_FINISH = {
    1:  (0.998, 79.6),
    3:  (0.640, 27.2),
    5:  (0.506, 14.2),
    10: (0.361,  8.3),
    20: (0.199,  3.4),
    40: (0.121,  2.0),
}

# Fast-lap distribution by finish position (from data analysis)
FAST_LAPS_BY_FINISH = {
    1:  (0.898, 29.4),    # (pct_with_any, avg_count)
    3:  (0.894, 17.9),
    5:  (0.873, 11.6),
    10: (0.780,  7.3),
    20: (0.600,  3.7),
    40: (0.438,  2.4),
}

# Rookie / thin-history baseline DK points by salary tier
ROOKIE_BASELINES = {
    "high":   35.0,   # salary >= 9000
    "mid":    28.0,   # salary 7000-8999
    "low":    20.0,   # salary < 7000
}

# Field-average fallback when no salary info
FIELD_AVG_FALLBACK = 25.0

# Rookie noise is wider (more uncertainty)
ROOKIE_SIGMA_MULTIPLIER = 1.4


# ── Scoring wrappers ──────────────────────────────────────

def calc_dk_points(finish_pos, start_pos, laps_led, fastest_laps, total_laps, laps_completed):
    """DK scoring via scoring.py. Returns dict with 'total' key."""
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
    """FD scoring via scoring.py. Returns float."""
    result = _calc_fd(
        finish_position=finish_pos,
        start_position=start_pos,
        laps_completed=laps_completed,
        laps_led=laps_led,
    )
    return result["fd_points"]


# ── Data loaders ──────────────────────────────────────────

def _get_track_type_name(race: Race) -> str:
    """Safely extract track type name from race."""
    if race.track and race.track.track_type:
        return race.track.track_type.name
    return "Large Oval"


def _get_recency_weighted_avg(
    db: Session, driver_id: int, track_type_name: str,
    platform: str, decay_factor: float
) -> Optional[float]:
    """
    Pull historical DK/FD points for a driver at this track type,
    weighted by exponential decay (most recent race at this type = weight 1.0).
    """
    col = Result.dk_points if platform == "draftkings" else Result.fd_points
    rows = (
        db.query(col, Race.race_date)
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            Result.driver_id == driver_id,
            TrackType.name == track_type_name,
            col.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .all()
    )
    if not rows:
        return None

    weighted_sum = 0.0
    weight_sum = 0.0
    for i, (pts, _) in enumerate(rows):
        w = decay_factor ** i
        weighted_sum += float(pts) * w
        weight_sum += w

    return weighted_sum / weight_sum if weight_sum > 0 else None


def _get_recent_same_tt_avg(
    db: Session, driver_id: int, track_type_name: str,
    platform: str, n_races: int = 3
) -> Optional[float]:
    """
    Pull average DK/FD points from last N races at the SAME track type.
    This captures recent momentum filtered to the relevant surface.
    """
    col = Result.dk_points if platform == "draftkings" else Result.fd_points
    rows = (
        db.query(col)
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            Result.driver_id == driver_id,
            TrackType.name == track_type_name,
            col.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    vals = [float(r[0]) for r in rows]
    return sum(vals) / len(vals) if vals else None


def _get_driver_loop_profile(
    db: Session, driver_id: int, track_type_name: str
) -> Dict:
    """
    Pull average loop-data metrics at this track type.
    Returns: driver_rating, fastest_lap_pct, pct_laps_in_top15,
             avg_running_position, fastest_laps_avg
    """
    row = (
        db.query(
            func.avg(LoopData.driver_rating).label("avg_rating"),
            func.avg(LoopData.fastest_lap_pct).label("avg_fl_pct"),
            func.avg(LoopData.pct_laps_in_top15).label("avg_top15"),
            func.avg(LoopData.avg_running_position).label("avg_run_pos"),
            func.avg(LoopData.fastest_laps).label("avg_fl_count"),
            func.avg(LoopData.laps_in_top5).label("avg_top5_laps"),
        )
        .join(Race, LoopData.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(LoopData.driver_id == driver_id, TrackType.name == track_type_name)
        .first()
    )
    return {
        "avg_rating":    float(row.avg_rating)    if row and row.avg_rating    else 80.0,
        "avg_fl_pct":    float(row.avg_fl_pct)    if row and row.avg_fl_pct    else 3.0,
        "avg_top15":     float(row.avg_top15)     if row and row.avg_top15     else 40.0,
        "avg_run_pos":   float(row.avg_run_pos)   if row and row.avg_run_pos   else 20.0,
        "avg_fl_count":  float(row.avg_fl_count)  if row and row.avg_fl_count  else 3.0,
        "avg_top5_laps": float(row.avg_top5_laps) if row and row.avg_top5_laps else 0.0,
    }


def _get_track_caution_rate(db: Session, track_type_name: str) -> float:
    """
    Average caution-laps-as-pct-of-total for this track type.
    Higher = more cautions = laps led spread more evenly.
    Returns a value between 0 and 1.
    """
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
    # Defaults by track type if no data
    defaults = {
        "Flat": 0.22, "Steep": 0.20, "Large Oval": 0.18,
        "Road": 0.12, "Restrictor Plate": 0.25,
    }
    return defaults.get(track_type_name, 0.20)


def _get_salary_tier(salary: Optional[int]) -> str:
    """Map salary to tier for rookie baseline."""
    if salary is None:
        return "mid"
    if salary >= 9000:
        return "high"
    if salary >= 7000:
        return "mid"
    return "low"


def _count_driver_races_at_type(
    db: Session, driver_id: int, track_type_name: str
) -> int:
    """How many completed races does this driver have at this track type?"""
    return (
        db.query(func.count(Result.id))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            Result.driver_id == driver_id,
            TrackType.name == track_type_name,
        )
        .scalar()
    ) or 0


def _get_current_form(db: Session, driver_id: int, platform: str, n_races: int = 10) -> Dict:
    """
    Pull recent form stats (any track type) for display.
    Returns avg_finish and avg_pts over the last N races.
    """
    col = Result.dk_points if platform == "draftkings" else Result.fd_points
    rows = (
        db.query(Result.finish_position, col)
        .join(Race, Result.race_id == Race.id)
        .filter(Result.driver_id == driver_id, col.isnot(None))
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    if not rows:
        return {"current_form_finish": None, "current_form_pts": None, "current_form_races": 0}
    finishes = [float(r[0]) for r in rows]
    pts = [float(r[1]) for r in rows]
    return {
        "current_form_finish": round(sum(finishes) / len(finishes), 1),
        "current_form_pts": round(sum(pts) / len(pts), 1),
        "current_form_races": len(rows),
    }


def _get_track_type_form(db: Session, driver_id: int, track_type_name: str,
                         platform: str, n_races: int = 6) -> Dict:
    """
    Pull track-type-specific form for display.
    Returns avg_finish over the last N races at this track type.
    """
    rows = (
        db.query(Result.finish_position)
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
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
        return {"tt_form_finish": None, "tt_form_races": 0}
    finishes = [float(r[0]) for r in rows]
    return {
        "tt_form_finish": round(sum(finishes) / len(finishes), 1),
        "tt_form_races": len(rows),
    }


def _get_track_driver_rating(db: Session, driver_id: int, track_id: int,
                              n_races: int = 5) -> Optional[float]:
    """
    Pull average driver rating at a SPECIFIC TRACK over the last N races.
    More targeted than track-type-level rating.
    """
    rows = (
        db.query(LoopData.driver_rating)
        .join(Race, LoopData.race_id == Race.id)
        .filter(
            LoopData.driver_id == driver_id,
            Race.track_id == track_id,
            LoopData.driver_rating.isnot(None),
        )
        .order_by(Race.season.desc(), Race.race_number.desc())
        .limit(n_races)
        .all()
    )
    if not rows:
        return None
    vals = [float(r[0]) for r in rows]
    return round(sum(vals) / len(vals), 1)


# ── Profile builder ───────────────────────────────────────

def build_driver_profiles(
    db: Session, race: Race, platform: str, recent_form_races: int
) -> List[Dict]:
    """
    Build Phase 3 simulation profile for every driver entered in this race.
    Each profile contains: base_score, is_rookie, loop metrics, qual data,
    fast-lap rate, and track-type config.
    """
    track_type_name = _get_track_type_name(race)
    total_laps      = race.scheduled_laps or 200  # fallback
    decay_factor    = TRACK_TYPE_DECAY.get(track_type_name, 0.88)
    history_weight  = TRACK_TYPE_HISTORY_WEIGHT.get(track_type_name, 0.75)
    sigma           = TRACK_TYPE_SIGMA.get(track_type_name, 10.0)

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

    # Entrants = drivers with a salary this week
    active_driver_ids = list(sal_map.keys())
    if not active_driver_ids:
        active_drivers = db.query(Driver).filter(Driver.active == True).all()
        active_driver_ids = [d.id for d in active_drivers]

    # Caution rate for laps-led dispersion
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

        # ── Base score: recency-weighted track-type avg ──
        track_avg  = _get_recency_weighted_avg(db, driver_id, track_type_name, platform, decay_factor)
        recent_avg = _get_recent_same_tt_avg(db, driver_id, track_type_name, platform, recent_form_races)

        # Count races at this track type to detect thin history
        race_count = _count_driver_races_at_type(db, driver_id, track_type_name)
        is_rookie  = race_count < 5

        if track_avg and recent_avg:
            base_score = track_avg * history_weight + recent_avg * (1 - history_weight)
        elif track_avg:
            base_score = track_avg
        elif recent_avg:
            base_score = recent_avg
        else:
            # Rookie / no-history fallback
            tier = _get_salary_tier(salary)
            base_score = ROOKIE_BASELINES[tier]

        # Widen noise for rookies
        driver_sigma = sigma * ROOKIE_SIGMA_MULTIPLIER if is_rookie else sigma

        # ── Loop-data profile ──
        loop = _get_driver_loop_profile(db, driver_id, track_type_name)

        # ── Display-only stats (passed through to frontend) ──
        current_form = _get_current_form(db, driver_id, platform)
        tt_form = _get_track_type_form(db, driver_id, track_type_name, platform)
        track_rating = _get_track_driver_rating(db, driver_id, race.track_id)

        # ── Qualifying bonus ──
        # Starting upfront matters; scale by how many drivers
        qual_bonus = 0.0
        if start_pos:
            n_drivers = len(active_driver_ids)
            # Top qualifier gets ~0.3 * n_drivers bonus, last gets 0
            qual_bonus = max(0, (n_drivers - start_pos + 1)) * 0.3

        profiles.append({
            "driver_id":        driver_id,
            "driver_name":      driver.full_name,
            "car_number":       season_info.car_number if season_info else "??",
            "team_name":        season_info.team.name if season_info and season_info.team else None,
            "manufacturer":     season_info.manufacturer.name if season_info and season_info.manufacturer else None,
            "salary":           salary,
            "start_position":   start_pos,
            "base_score":       base_score,
            "qual_bonus":       qual_bonus,
            "loop":             loop,
            "sigma":            driver_sigma,
            "is_rookie":        is_rookie,
            "race_count":       race_count,
            "total_laps":       total_laps,
            "track_type":       track_type_name,
            "caution_rate":     caution_rate,
            # Display-only fields for frontend
            "current_form_finish": current_form["current_form_finish"],
            "current_form_pts":    current_form["current_form_pts"],
            "current_form_races":  current_form["current_form_races"],
            "tt_form_finish":      tt_form["tt_form_finish"],
            "tt_form_races":       tt_form["tt_form_races"],
            "driver_rating":       track_rating,
            "avg_fl_count":        loop["avg_fl_count"],
        })

    return profiles


# ── Sub-models for each sim iteration ─────────────────────

def _simulate_finish_order(profiles: List[Dict]) -> List[int]:
    """
    Score each driver with noise and return finish order (list of driver_ids).
    Uses driver rating as a multiplier on base score.
    """
    sim_scores = []
    for p in profiles:
        # Driver rating: 100 = average, so /100 gives a multiplier near 1.0
        rating_factor = p["loop"]["avg_rating"] / 100.0
        noise = random.gauss(0, p["sigma"])
        score = p["base_score"] * rating_factor + p["qual_bonus"] + noise
        sim_scores.append((p["driver_id"], score))

    sim_scores.sort(key=lambda x: x[1], reverse=True)
    return [driver_id for driver_id, _ in sim_scores]


def _simulate_laps_led(
    finish_order: List[int], profiles_map: Dict, total_laps: int,
    caution_rate: float
) -> Dict[int, int]:
    """
    Power-law laps-led distribution.

    Uses empirical probabilities by finish position:
      - Winner: ~80 laps avg, 99.8% lead at least 1
      - 2-3: ~27 avg, 64% chance
      - etc.

    Caution rate affects concentration: more cautions = more lead changes
    = laps spread more evenly (lower dominator share).
    """
    n_drivers = len(finish_order)
    laps_led = {d: 0 for d in finish_order}

    # Caution dispersion: high caution → reduce dominator concentration
    # 0.20 caution rate = neutral, higher spreads more
    dispersion = 1.0 + (caution_rate - 0.20) * 2.0  # range ~0.6 to 1.6

    for idx, driver_id in enumerate(finish_order):
        finish_pos = idx + 1

        # Look up probability and avg from empirical table
        prob, avg = _laps_led_for_position(finish_pos)

        # Scale average by total_laps / 200 (data was mostly 200-lap races)
        scale = total_laps / 200.0
        avg_scaled = avg * scale

        # Apply caution dispersion: more cautions = spread laps away from leader
        if finish_pos <= 3:
            # Top drivers lose share when there are more cautions
            avg_scaled *= max(0.4, 1.0 / dispersion)
        elif finish_pos > 10:
            # Mid/back drivers gain a bit
            avg_scaled *= min(2.0, dispersion * 0.8)

        # Roll whether this driver leads any laps
        if random.random() > prob:
            continue

        # Draw from exponential-ish distribution with the scaled mean
        laps = max(1, int(random.expovariate(1.0 / max(avg_scaled, 1.0))))
        laps_led[driver_id] = laps

    # Clamp total to total_laps
    total_led = sum(laps_led.values())
    if total_led > total_laps:
        scale_down = total_laps / total_led
        laps_led = {k: max(0, int(v * scale_down)) for k, v in laps_led.items()}

    # Ensure at least 1 lap led for the winner
    winner = finish_order[0]
    if laps_led[winner] == 0:
        laps_led[winner] = max(1, int(total_laps * 0.05))

    return laps_led


def _laps_led_for_position(finish_pos: int) -> Tuple[float, float]:
    """Return (probability_of_leading, avg_laps_if_leading) for a finish position."""
    for max_pos, (prob, avg) in sorted(LAPS_LED_BY_FINISH.items()):
        if finish_pos <= max_pos:
            return (prob, avg)
    return (0.10, 1.5)


def _simulate_fast_laps(
    finish_order: List[int], profiles_map: Dict, total_laps: int
) -> Dict[int, int]:
    """
    Distribute fastest-lap counts across the field.

    Unlike laps led (concentrated in winner), fast laps are spread more
    broadly — even P20 drivers average 3.7 fast laps per race.

    Uses two signals blended together:
      1. Empirical rate by finish position (from data)
      2. Driver's historical fastest_lap_pct at this track type (from loop data)
    """
    n_drivers = len(finish_order)
    fast_laps = {d: 0 for d in finish_order}

    # Step 1: Compute each driver's expected FL share
    shares = {}
    for idx, driver_id in enumerate(finish_order):
        finish_pos = idx + 1
        p = profiles_map[driver_id]

        # Empirical baseline from finish position
        _, avg_fl = _fast_laps_for_position(finish_pos)
        baseline_rate = avg_fl / 200.0  # normalize to per-lap rate

        # Driver's historical FL% at this track type
        hist_fl_pct = p["loop"]["avg_fl_pct"] / 100.0  # stored as percentage

        # Blend: 50% finish-position baseline + 50% historical driver skill
        blended_rate = baseline_rate * 0.5 + hist_fl_pct * 0.5

        # Clamp to reasonable range
        blended_rate = max(0.005, min(blended_rate, 0.25))

        shares[driver_id] = blended_rate

    # Step 2: Normalize so total fast laps ≈ total_laps
    total_share = sum(shares.values())
    if total_share == 0:
        return fast_laps

    for driver_id in finish_order:
        expected = (shares[driver_id] / total_share) * total_laps
        # Add noise: Poisson-like variability
        noisy = max(0, int(random.gauss(expected, max(1.0, expected * 0.4))))
        fast_laps[driver_id] = noisy

    # Clamp total to total_laps
    total_fl = sum(fast_laps.values())
    if total_fl > total_laps:
        scale = total_laps / total_fl
        fast_laps = {k: max(0, int(v * scale)) for k, v in fast_laps.items()}

    return fast_laps


def _fast_laps_for_position(finish_pos: int) -> Tuple[float, float]:
    """Return (pct_with_any, avg_count) for a finish position."""
    for max_pos, (prob, avg) in sorted(FAST_LAPS_BY_FINISH.items()):
        if finish_pos <= max_pos:
            return (prob, avg)
    return (0.30, 1.5)


# ── Main simulation ───────────────────────────────────────

def run_simulation(
    db: Session, race: Race, n_sims: int,
    platform: str = "draftkings",
    recent_form_races: int = 5,
) -> List[Dict]:
    """
    Phase 3 Monte Carlo engine.
    Returns a list of per-driver simulation result dicts.
    """
    profiles    = build_driver_profiles(db, race, platform, recent_form_races)
    n_drivers   = len(profiles)
    total_laps  = race.scheduled_laps or 200

    # Build lookup map for fast access in inner loop
    profiles_map = {p["driver_id"]: p for p in profiles}

    # Get caution rate (same for all drivers at this track type)
    caution_rate = profiles[0]["caution_rate"] if profiles else 0.20

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
        # 1. Determine finish order
        finish_order = _simulate_finish_order(profiles)

        # 2. Distribute laps led (power-law)
        laps_led_map = _simulate_laps_led(finish_order, profiles_map, total_laps, caution_rate)

        # 3. Distribute fast laps
        fast_laps_map = _simulate_fast_laps(finish_order, profiles_map, total_laps)

        # 4. Calculate fantasy points for each driver
        for finish_idx, driver_id in enumerate(finish_order):
            finish_pos      = finish_idx + 1
            p               = accum[driver_id]
            start_pos       = p["start_position"] or finish_pos
            laps_led        = laps_led_map.get(driver_id, 0)
            fastest_laps    = fast_laps_map.get(driver_id, 0)
            # Laps completed: most drivers finish all laps unless DNF
            laps_completed  = total_laps if finish_pos <= n_drivers * 0.85 else int(total_laps * 0.65)

            if platform == "draftkings":
                pts = calc_dk_points(finish_pos, start_pos, laps_led, fastest_laps,
                                     total_laps, laps_completed)["total"]
            else:
                pts = calc_fd_points(finish_pos, start_pos, laps_led, fastest_laps,
                                     total_laps, laps_completed)

            p["fp_sum"]         += pts
            p["finish_sum"]     += finish_pos
            p["laps_led_sum"]   += laps_led
            p["fast_lap_sum"]   += fastest_laps
            if finish_pos == 1:  p["wins"]  += 1
            if finish_pos <= 3:  p["top3"]  += 1
            if finish_pos <= 5:  p["top5"]  += 1
            if finish_pos <= 10: p["top10"] += 1
            p["all_fp"].append(pts)

    # ── Aggregate results ──
    results = []
    for driver_id, a in accum.items():
        sorted_fp   = sorted(a["all_fp"])
        avg_fp      = a["fp_sum"] / n_sims
        salary      = a["salary"] or 7000
        value       = avg_fp / (salary / 1000) if salary else 0
        avg_ll      = a["laps_led_sum"] / n_sims
        avg_fl      = a["fast_lap_sum"] / n_sims
        fl_pct      = avg_fl / total_laps if total_laps > 0 else 0
        win_pct     = a["wins"] / n_sims

        # Dominator score: laps led + fast laps contribution
        dom_score   = avg_ll * 0.25 + avg_fl * 0.10

        # ── Ownership projection (improved) ──
        # Primary drivers: salary rank is biggest ownership predictor
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
            "avg_finish":       round(a["finish_sum"] / n_sims, 2),
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
            # Phase 3: underlying metrics for display
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


# ── Ownership projection ──────────────────────────────────

def _salary_rank(salary: Optional[int], all_salaries: List[Optional[int]]) -> float:
    """Return 0-1 where 1 = highest salary. Used for ownership projection."""
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
    """
    Improved ownership projection.
    In DFS, salary rank is the #1 predictor of ownership (expensive = popular).
    Win probability and value are secondary factors.
    """
    # Salary-driven base: top salary → ~25%, bottom → ~3%
    salary_base = 3.0 + salary_rank * 22.0

    # Win probability boost
    win_boost = win_pct * 80.0

    # Value boost (high value = popular in cash games)
    value_boost = max(0, (value - 3.0)) * 2.0

    # Combine with noise
    raw = salary_base + win_boost + value_boost + random.gauss(0, 2.5)

    # Normalize so total ownership ≈ n_drivers * (100 / lineup_size)
    # For a 6-driver lineup, each slot has ~16.7% average ownership
    # Total ownership across field should sum to ~600%
    return max(1.5, min(55.0, raw))


# ── Lineup Optimizer ──────────────────────────────────────

def optimize_lineups(
    sim_results: List[Dict], salary_cap: int, n_lineups: int,
    lock_drivers: List[int], exclude_drivers: List[int],
    max_ownership: Optional[float], min_salary: Optional[int],
    lineup_size: int = 6,
) -> List[Dict]:
    """
    Generate N optimal DFS lineups from simulation results.
    Uses a weighted-random greedy approach with correlation constraints
    to ensure lineup diversity.
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

    generated   = []
    seen        = set()
    attempts    = 0
    max_attempts = n_lineups * 50

    while len(generated) < n_lineups and attempts < max_attempts:
        attempts += 1
        budget  = salary_cap
        lineup  = []
        used    = set()

        # Force locked drivers first
        for locked_id in lock_drivers:
            driver = next((r for r in eligible if r["driver_id"] == locked_id), None)
            if driver and driver["salary"] <= budget:
                lineup.append(driver)
                budget -= driver["salary"]
                used.add(locked_id)

        if len(lineup) > lineup_size:
            continue

        # Fill remaining slots
        pool = sorted(
            [r for r in eligible if r["driver_id"] not in used],
            key=score,
            reverse=True
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

        total_salary    = sum(d["salary"] for d in lineup)
        proj_fp         = sum(d["avg_fp"] for d in lineup)
        proj_ceiling    = sum(d["ceiling_fp"] for d in lineup)

        generated.append({
            "lineup":           lineup,
            "total_salary":     total_salary,
            "salary_remaining": salary_cap - total_salary,
            "proj_fp":          round(proj_fp, 2),
            "proj_ceiling":     round(proj_ceiling, 2),
        })

    generated.sort(key=lambda x: x["proj_fp"], reverse=True)
    return generated
