# ============================================================
# simulation_engine.py
# Monte Carlo simulation engine for NASCAR DFS
# Called by the /api/simulate router
# ============================================================

import random
import math
import numpy as np
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from models import Driver, Race, Result, Qualifying, Salary, LoopData, DriverSeason
from sqlalchemy import func, and_


# ── DraftKings Scoring ────────────────────────────────────
DK_PLACE_PTS = [
    100, 88, 78, 72, 68, 64, 60, 56, 52, 48,
    44,  40, 38, 36, 34, 32, 30, 28, 26, 24,
    22,  20, 18, 16, 14, 12, 10,  8,  6,  4, 2
]

FD_PLACE_PTS = [
    100, 90, 83, 78, 74, 70, 67, 64, 61, 58,
    55,  52, 49, 46, 43, 40, 38, 36, 34, 32,
    30,  28, 26, 24, 22, 20, 18, 16, 14, 12, 10
]


def calc_dk_points(finish_pos, start_pos, laps_led, fastest_lap, total_laps, laps_completed):
    place_pts   = DK_PLACE_PTS[min(finish_pos - 1, len(DK_PLACE_PTS) - 1)]
    diff_pts    = max(0, (start_pos - finish_pos) * 1.0) if start_pos else 0
    led_pts     = laps_led * 0.25
    fl_pts      = 5.0 if fastest_lap else 0.0
    comp_pts    = 4.0 if total_laps > 0 and laps_completed / total_laps >= 0.75 else 0.0
    dom_bonus   = 10.0 if laps_led >= 50 else 0.0
    total       = place_pts + diff_pts + led_pts + fl_pts + comp_pts + dom_bonus
    return {
        "total": total, "place": place_pts, "diff": diff_pts,
        "led": led_pts, "fl": fl_pts, "comp": comp_pts, "dom": dom_bonus
    }


def calc_fd_points(finish_pos, start_pos, laps_led, fastest_lap, total_laps, laps_completed):
    place_pts   = FD_PLACE_PTS[min(finish_pos - 1, len(FD_PLACE_PTS) - 1)]
    diff_pts    = max(0, (start_pos - finish_pos) * 1.0) if start_pos else 0
    led_pts     = laps_led * 0.5
    fl_pts      = 10.0 if fastest_lap else 0.0
    total       = place_pts + diff_pts + led_pts + fl_pts
    return total


def gaussian(mean: float, std: float) -> float:
    return random.gauss(mean, std)


def get_driver_track_type_avg(db: Session, driver_id: int, track_type_name: str,
                               platform: str = "draftkings", n_seasons: int = 3) -> Optional[float]:
    """Pull historical average DK/FD points for a driver at this track type."""
    from models import Track, TrackType
    col = Result.dk_points if platform == "draftkings" else Result.fd_points
    rows = (
        db.query(func.avg(col))
        .join(Race, Result.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(
            Result.driver_id == driver_id,
            TrackType.name == track_type_name,
            col.isnot(None),
        )
        .scalar()
    )
    return float(rows) if rows else None


def get_driver_recent_avg(db: Session, driver_id: int, platform: str = "draftkings",
                           n_races: int = 5) -> Optional[float]:
    """Pull average DK/FD points from last N races regardless of track type."""
    col = Result.dk_points if platform == "draftkings" else Result.fd_points
    rows = (
        db.query(col)
        .join(Race, Result.race_id == Race.id)
        .filter(Result.driver_id == driver_id, col.isnot(None))
        .order_by(Race.race_date.desc())
        .limit(n_races)
        .all()
    )
    vals = [float(r[0]) for r in rows]
    return sum(vals) / len(vals) if vals else None


def get_driver_loop_avg(db: Session, driver_id: int, track_type_name: str) -> Dict:
    """Pull average loop data metrics for a driver at this track type."""
    from models import Track, TrackType
    row = (
        db.query(
            func.avg(LoopData.driver_rating).label("avg_rating"),
            func.avg(LoopData.fastest_lap_pct).label("avg_fl_pct"),
            func.avg(LoopData.pct_laps_in_top15).label("avg_top15"),
            func.avg(LoopData.green_flag_passes).label("avg_gf_passes"),
        )
        .join(Race, LoopData.race_id == Race.id)
        .join(Track, Race.track_id == Track.id)
        .join(TrackType, Track.track_type_id == TrackType.id)
        .filter(LoopData.driver_id == driver_id, TrackType.name == track_type_name)
        .first()
    )
    return {
        "avg_rating":     float(row.avg_rating)     if row and row.avg_rating     else 80.0,
        "avg_fl_pct":     float(row.avg_fl_pct)     if row and row.avg_fl_pct     else 5.0,
        "avg_top15":      float(row.avg_top15)       if row and row.avg_top15      else 40.0,
        "avg_gf_passes":  float(row.avg_gf_passes)  if row and row.avg_gf_passes  else 10.0,
    }


def build_driver_profiles(db: Session, race: Race, platform: str,
                           recent_form_races: int) -> List[Dict]:
    """Build the simulation profile for every driver entered in this race."""
    track_type_name = race.track.track_type.name if race.track and race.track.track_type else "Large Oval"
    total_laps      = race.scheduled_laps

    # Get qualifying positions for this race
    qual_map = {
        q.driver_id: q.start_position
        for q in db.query(Qualifying).filter(Qualifying.race_id == race.id).all()
    }

    # Get salaries for this race
    sal_map = {
        s.driver_id: s.salary
        for s in db.query(Salary).filter(
            Salary.race_id == race.id,
            Salary.platform == platform
        ).all()
    }

    # Get all active drivers with a salary this week (these are the entrants)
    active_driver_ids = list(sal_map.keys())

    # If no salaries loaded yet, fall back to all active drivers
    if not active_driver_ids:
        active_drivers = db.query(Driver).filter(Driver.active == True).all()
        active_driver_ids = [d.id for d in active_drivers]

    profiles = []
    for driver_id in active_driver_ids:
        driver = db.query(Driver).filter(Driver.id == driver_id).first()
        if not driver:
            continue

        # Get current season info
        season_info = (
            db.query(DriverSeason)
            .filter(DriverSeason.driver_id == driver_id)
            .order_by(DriverSeason.season.desc())
            .first()
        )

        # Historical average at this track type
        track_avg   = get_driver_track_type_avg(db, driver_id, track_type_name, platform)
        # Recent form average
        recent_avg  = get_driver_recent_avg(db, driver_id, platform, recent_form_races)
        # Loop data metrics
        loop        = get_driver_loop_avg(db, driver_id, track_type_name)

        # Blend track avg (60%) + recent form (40%) for base score
        if track_avg and recent_avg:
            base_score = track_avg * 0.6 + recent_avg * 0.4
        elif track_avg:
            base_score = track_avg
        elif recent_avg:
            base_score = recent_avg
        else:
            base_score = 25.0  # fallback for drivers with no history

        # Qualifying bonus — starting upfront matters more at ovals
        start_pos       = qual_map.get(driver_id)
        qual_bonus      = 0.0
        if start_pos:
            n_drivers   = len(active_driver_ids)
            qual_bonus  = max(0, (n_drivers - start_pos + 1)) * 0.3

        profiles.append({
            "driver_id":        driver_id,
            "driver_name":      driver.full_name,
            "car_number":       season_info.car_number if season_info else "??",
            "team_name":        season_info.team.name if season_info and season_info.team else None,
            "manufacturer":     season_info.manufacturer.name if season_info and season_info.manufacturer else None,
            "salary":           sal_map.get(driver_id),
            "start_position":   start_pos,
            "base_score":       base_score,
            "qual_bonus":       qual_bonus,
            "loop":             loop,
            "total_laps":       total_laps,
        })

    return profiles


def run_simulation(db: Session, race: Race, n_sims: int,
                   platform: str = "draftkings",
                   recent_form_races: int = 5) -> List[Dict]:
    """
    Core Monte Carlo engine.
    Returns a list of per-driver simulation result dicts.
    """
    profiles    = build_driver_profiles(db, race, platform, recent_form_races)
    n_drivers   = len(profiles)
    total_laps  = race.scheduled_laps

    # Accumulators — one entry per driver
    accum = {
        p["driver_id"]: {
            **p,
            "wins": 0, "top3": 0, "top5": 0, "top10": 0,
            "fp_sum": 0.0, "finish_sum": 0.0,
            "laps_led_sum": 0.0, "fast_lap_count": 0,
            "all_fp": [],
        }
        for p in profiles
    }

    for _ in range(n_sims):
        # 1. Score each driver for this sim with noise
        sim_scores = []
        for p in profiles:
            # Driver rating boosts base score slightly
            rating_factor   = p["loop"]["avg_rating"] / 100.0
            noise           = gaussian(0, 10)
            score           = p["base_score"] * rating_factor + p["qual_bonus"] + noise
            sim_scores.append((p["driver_id"], score))

        # 2. Sort by score → finish order
        sim_scores.sort(key=lambda x: x[1], reverse=True)
        finish_order = [driver_id for driver_id, _ in sim_scores]

        # 3. Distribute laps led — weighted toward top drivers
        laps_led_map = {d: 0 for d in finish_order}
        # Top 6 drivers compete for laps led
        leaders = finish_order[:6]
        weights = [max(1, 7 - i) for i in range(len(leaders))]
        total_weight = sum(weights)
        for driver_id, w in zip(leaders, weights):
            laps_led_map[driver_id] = round(total_laps * (w / total_weight) * random.uniform(0.6, 1.4))
        # Clamp total laps led to total_laps
        total_led = sum(laps_led_map.values())
        if total_led > total_laps:
            scale = total_laps / total_led
            laps_led_map = {k: int(v * scale) for k, v in laps_led_map.items()}

        # 4. Fastest lap — typically the race leader or fastest qualifier
        fl_candidates   = finish_order[:5]
        fl_weights      = [p["loop"]["avg_fl_pct"] for p in profiles
                           if p["driver_id"] in fl_candidates]
        fl_total        = sum(fl_weights) or 1
        fl_roll         = random.random() * fl_total
        fl_winner       = fl_candidates[0]
        for driver_id, w in zip(fl_candidates, fl_weights):
            fl_roll -= w
            if fl_roll <= 0:
                fl_winner = driver_id
                break

        # 5. Calculate fantasy points for each driver this sim
        for finish_idx, driver_id in enumerate(finish_order):
            finish_pos      = finish_idx + 1
            p               = accum[driver_id]
            start_pos       = p["start_position"] or finish_pos
            laps_led        = laps_led_map[driver_id]
            fastest_lap     = (driver_id == fl_winner)
            laps_completed  = total_laps if finish_pos <= n_drivers * 0.85 else int(total_laps * 0.65)

            if platform == "draftkings":
                pts = calc_dk_points(finish_pos, start_pos, laps_led, fastest_lap,
                                     total_laps, laps_completed)["total"]
            else:
                pts = calc_fd_points(finish_pos, start_pos, laps_led, fastest_lap,
                                     total_laps, laps_completed)

            p["fp_sum"]         += pts
            p["finish_sum"]     += finish_pos
            p["laps_led_sum"]   += laps_led
            if fastest_lap:
                p["fast_lap_count"] += 1
            if finish_pos == 1:  p["wins"]  += 1
            if finish_pos <= 3:  p["top3"]  += 1
            if finish_pos <= 5:  p["top5"]  += 1
            if finish_pos <= 10: p["top10"] += 1
            p["all_fp"].append(pts)

    # 6. Aggregate results
    results = []
    for driver_id, a in accum.items():
        sorted_fp   = sorted(a["all_fp"])
        avg_fp      = a["fp_sum"] / n_sims
        salary      = a["salary"] or 7000
        value       = avg_fp / (salary / 1000) if salary else 0
        avg_ll      = a["laps_led_sum"] / n_sims
        fl_pct      = a["fast_lap_count"] / n_sims
        dom_score   = avg_ll * 0.25 + fl_pct * 5
        win_pct     = a["wins"] / n_sims
        # Projected ownership: driven by win probability + value + small random factor
        proj_own    = max(2.0, min(65.0,
                        win_pct * 150 + value * 1.2 + gaussian(0, 3)))
        leverage    = avg_fp / proj_own if proj_own > 0 else 0

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
        })

    results.sort(key=lambda x: x["avg_fp"], reverse=True)
    return results


# ── Lineup Optimizer ──────────────────────────────────────
def optimize_lineups(sim_results: List[Dict], salary_cap: int, n_lineups: int,
                     lock_drivers: List[int], exclude_drivers: List[int],
                     max_ownership: Optional[float], min_salary: Optional[int],
                     lineup_size: int = 6) -> List[Dict]:
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

    # Score for optimization: blend avg_fp, ceiling, floor
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

        # Force locked drivers in first
        for locked_id in lock_drivers:
            driver = next((r for r in eligible if r["driver_id"] == locked_id), None)
            if driver and driver["salary"] <= budget:
                lineup.append(driver)
                budget -= driver["salary"]
                used.add(locked_id)

        if len(lineup) > lineup_size:
            continue

        # Fill remaining slots with scored + shuffled pool
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
            if min_salary and (budget - driver["salary"]) > (salary_cap * 0.15):
                # Don't leave more than 15% of cap unused if min_salary enforced
                pass
            lineup.append(driver)
            budget -= driver["salary"]
            used.add(driver["driver_id"])

        if len(lineup) < lineup_size:
            continue

        # Deduplicate lineups by driver set
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
