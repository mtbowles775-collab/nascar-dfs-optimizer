# ============================================================
# scoring.py — DraftKings & FanDuel NASCAR point calculations
# 
# SINGLE SOURCE OF TRUTH for all DFS scoring.
# Called by:
#   - backfill_scores.py (historical)
#   - results scraper (future races)
#   - simulation engine (projections)
# ============================================================

# ── DraftKings NASCAR Classic ────────────────────────────
# Source: draftkings.com/help/rules/nascar (updated 02/06/2021)

DK_FINISH_PTS = {
    1: 45, 2: 42, 3: 41, 4: 40, 5: 39,
    6: 38, 7: 37, 8: 36, 9: 35, 10: 34,
    11: 32, 12: 31, 13: 30, 14: 29, 15: 28,
    16: 27, 17: 26, 18: 25, 19: 24, 20: 23,
    21: 21, 22: 20, 23: 19, 24: 18, 25: 17,
    26: 16, 27: 15, 28: 14, 29: 13, 30: 12,
    31: 10, 32: 9, 33: 8, 34: 7, 35: 6,
    36: 5, 37: 4, 38: 3, 39: 2, 40: 1,
}

DK_PLACE_DIFF_PER_POS  = 1.0   # ±1.0 per position (start − finish)
DK_FASTEST_LAP_PTS     = 0.45  # per lap with fastest time
DK_LAPS_LED_PTS        = 0.25  # per lap led


# ── FanDuel NASCAR ───────────────────────────────────────
# Source: FRCS.pro verified scoring tables

FD_FINISH_PTS = {
    1: 43, 2: 40, 3: 38,
    # 4th onward: 37, 36, 35... decreasing by 1
    **{pos: 41 - pos for pos in range(4, 41)},
}

FD_PLACE_DIFF_PER_POS  = 1.0   # ±1.0 per position (start − finish)
FD_LAPS_COMPLETED_PTS  = 0.1   # per lap completed
FD_LAPS_LED_PTS        = 0.1   # per lap led


def calc_dk_points(finish_position: int,
                   start_position: int,
                   laps_led: int = 0,
                   fastest_laps: int = 0) -> dict:
    """
    Calculate DraftKings NASCAR Classic points.
    
    Returns dict with breakdown:
        dk_place_pts, dk_place_diff_pts, dk_laps_led_pts,
        dk_fast_lap_pts, dk_points (total)
    """
    # Finish position points (0 if outside top 40)
    place_pts = DK_FINISH_PTS.get(finish_position, 0)
    
    # Place differential: start - finish (positive = gained positions)
    place_diff = 0.0
    if start_position and start_position > 0:
        place_diff = (start_position - finish_position) * DK_PLACE_DIFF_PER_POS
    
    # Fastest laps
    fast_lap_pts = fastest_laps * DK_FASTEST_LAP_PTS
    
    # Laps led
    laps_led_pts = laps_led * DK_LAPS_LED_PTS
    
    total = place_pts + place_diff + fast_lap_pts + laps_led_pts
    
    return {
        "dk_place_pts":      round(place_pts, 2),
        "dk_place_diff_pts": round(place_diff, 2),
        "dk_laps_led_pts":   round(laps_led_pts, 2),
        "dk_fast_lap_pts":   round(fast_lap_pts, 2),
        "dk_points":         round(total, 2),
    }


def calc_fd_points(finish_position: int,
                   start_position: int,
                   laps_completed: int = 0,
                   laps_led: int = 0) -> dict:
    """
    Calculate FanDuel NASCAR points.
    
    Returns dict with breakdown:
        fd_place_pts, fd_place_diff_pts, fd_laps_led_pts,
        fd_laps_complete_pts, fd_points (total)
    """
    # Finish position points (0 if outside top 40)
    place_pts = FD_FINISH_PTS.get(finish_position, 0)
    
    # Place differential: start - finish (positive = gained positions)
    place_diff = 0.0
    if start_position and start_position > 0:
        place_diff = (start_position - finish_position) * FD_PLACE_DIFF_PER_POS
    
    # Laps completed
    laps_complete_pts = laps_completed * FD_LAPS_COMPLETED_PTS
    
    # Laps led
    laps_led_pts = laps_led * FD_LAPS_LED_PTS
    
    total = place_pts + place_diff + laps_complete_pts + laps_led_pts
    
    return {
        "fd_place_pts":         round(place_pts, 2),
        "fd_place_diff_pts":    round(place_diff, 2),
        "fd_laps_led_pts":      round(laps_led_pts, 2),
        "fd_laps_complete_pts": round(laps_complete_pts, 2),
        "fd_points":            round(total, 2),
    }


def calc_all_points(finish_position: int,
                    start_position: int,
                    laps_completed: int = 0,
                    laps_led: int = 0,
                    fastest_laps: int = 0) -> dict:
    """
    Calculate both DK and FD points in one call.
    Returns merged dict with all breakdowns.
    """
    dk = calc_dk_points(finish_position, start_position, laps_led, fastest_laps)
    fd = calc_fd_points(finish_position, start_position, laps_completed, laps_led)
    return {**dk, **fd}


# ── Quick verification ───────────────────────────────────
if __name__ == "__main__":
    # William Byron 2024 Daytona 500:
    # Finish: 1st, Start: 18th, Laps led: 4, Fastest laps: 5, Laps completed: 200
    dk = calc_dk_points(finish_position=1, start_position=18, laps_led=4, fastest_laps=5)
    fd = calc_fd_points(finish_position=1, start_position=18, laps_completed=200, laps_led=4)
    
    print("Byron 2024 Daytona 500 verification:")
    print(f"  DK: {dk['dk_points']} (expected 65.25)")
    print(f"    Place: {dk['dk_place_pts']}, Diff: {dk['dk_place_diff_pts']}, "
          f"FL: {dk['dk_fast_lap_pts']}, LL: {dk['dk_laps_led_pts']}")
    print(f"  FD: {fd['fd_points']} (expected 80.40)")
    print(f"    Place: {fd['fd_place_pts']}, Diff: {fd['fd_place_diff_pts']}, "
          f"LC: {fd['fd_laps_complete_pts']}, LL: {fd['fd_laps_led_pts']}")
    
    assert dk["dk_points"] == 65.25, f"DK FAIL: {dk['dk_points']}"
    assert fd["fd_points"] == 80.40, f"FD FAIL: {fd['fd_points']}"
    print("\n✅ All verifications passed!")
