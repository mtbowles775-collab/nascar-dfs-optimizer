# ============================================================
# schemas.py — Pydantic models for request/response validation
# These define what the API accepts and returns as JSON
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal


# ── Track Types ───────────────────────────────────────────
class TrackTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    class Config: from_attributes = True


# ── Tracks ────────────────────────────────────────────────
class TrackOut(BaseModel):
    id: int
    name: str
    short_name: Optional[str]
    track_type: Optional[TrackTypeOut]
    length_miles: Decimal
    surface: Optional[str]
    is_oval: Optional[bool]
    city: Optional[str]
    state_country: Optional[str]
    class Config: from_attributes = True


# ── Drivers ───────────────────────────────────────────────
class DriverSeasonOut(BaseModel):
    season: int
    car_number: str
    team_name: Optional[str] = None
    manufacturer_name: Optional[str] = None
    class Config: from_attributes = True

class DriverOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    full_name: str
    active: bool
    class Config: from_attributes = True

class DriverDetailOut(DriverOut):
    seasons: List[DriverSeasonOut] = []
    class Config: from_attributes = True


# ── Races ─────────────────────────────────────────────────
class RaceOut(BaseModel):
    id: int
    season: int
    race_number: int
    race_name: Optional[str]
    race_date: Optional[date]
    scheduled_laps: int
    actual_laps: Optional[int]
    status: Optional[str]
    track: Optional[TrackOut]
    class Config: from_attributes = True


# ── Qualifying ────────────────────────────────────────────
class QualifyingOut(BaseModel):
    id: int
    driver_id: int
    driver_name: Optional[str] = None
    car_number: Optional[str] = None
    start_position: int
    lap_time_sec: Optional[Decimal]
    lap_speed_mph: Optional[Decimal]
    source: Optional[str]
    class Config: from_attributes = True

class QualifyingIn(BaseModel):
    # POST body: {driver_id: start_position, ...}
    positions: dict[int, int]
    source: str = "manual"


# ── Practice ──────────────────────────────────────────────
class PracticeOut(BaseModel):
    id: int
    driver_id: int
    driver_name: Optional[str] = None
    session_number: int
    best_lap_time: Optional[Decimal]
    best_lap_speed: Optional[Decimal]
    avg_lap_speed: Optional[Decimal]
    laps_run: Optional[int]
    position: Optional[int]
    class Config: from_attributes = True


# ── Results ───────────────────────────────────────────────
class ResultOut(BaseModel):
    id: int
    driver_id: int
    driver_name: Optional[str] = None
    car_number: Optional[str] = None
    finish_position: int
    start_position: Optional[int]
    laps_completed: Optional[int]
    laps_led: Optional[int]
    fastest_lap: Optional[bool]
    green_flag_speed: Optional[Decimal]
    dk_salary: Optional[int]
    dk_points: Optional[Decimal]
    dk_place_pts: Optional[Decimal]
    dk_place_diff_pts: Optional[Decimal]
    dk_laps_led_pts: Optional[Decimal]
    dk_fast_lap_pts: Optional[Decimal]
    dk_dominator_bonus: Optional[Decimal]
    fd_salary: Optional[int]
    fd_points: Optional[Decimal]
    status: Optional[str]
    class Config: from_attributes = True


# ── Loop Data ─────────────────────────────────────────────
class LoopDataOut(BaseModel):
    driver_id: int
    driver_name: Optional[str] = None
    green_flag_passes: Optional[int]
    green_flag_passed: Optional[int]
    quality_passes: Optional[int]
    laps_in_top15: Optional[int]
    laps_in_top10: Optional[int]
    laps_in_top5: Optional[int]
    pct_laps_in_top15: Optional[Decimal]
    fastest_lap_pct: Optional[Decimal]
    avg_running_position: Optional[Decimal]
    driver_rating: Optional[Decimal]
    stage_points_total: Optional[int]
    class Config: from_attributes = True


# ── Salaries ──────────────────────────────────────────────
class SalaryOut(BaseModel):
    driver_id: int
    driver_name: Optional[str] = None
    car_number: Optional[str] = None
    platform: str
    salary: int
    salary_change: Optional[int]
    class Config: from_attributes = True

class SalaryIn(BaseModel):
    driver_id: int
    platform: str
    salary: int
    roster_position: str = "driver"


# ── Ownership ─────────────────────────────────────────────
class OwnershipOut(BaseModel):
    driver_id: int
    driver_name: Optional[str] = None
    platform: str
    contest_type: Optional[str]
    ownership_pct: Optional[Decimal]
    captain_pct: Optional[Decimal]
    class Config: from_attributes = True

class OwnershipIn(BaseModel):
    driver_id: int
    platform: str
    contest_type: str = "gpp"
    ownership_pct: Decimal
    captain_pct: Optional[Decimal] = None
    source: Optional[str] = None


# ── Simulation ────────────────────────────────────────────
class SimulateRequest(BaseModel):
    race_id: int
    n_sims: int = Field(default=1000, ge=100, le=10000)
    platform: str = "draftkings"          # 'draftkings' or 'fanduel'
    use_cached_qual: bool = True           # use qualifying data from DB if available
    salary_weight: float = 1.0            # multiplier for salary-based adjustments
    recent_form_races: int = 5            # how many recent races to weight heavily

class SimDriverResult(BaseModel):
    driver_id: int
    driver_name: str
    car_number: str
    team_name: Optional[str]
    manufacturer: Optional[str]
    salary: Optional[int]
    start_position: Optional[int]
    avg_fp: float
    median_fp: float
    floor_fp: float
    ceiling_fp: float
    avg_finish: float
    avg_laps_led: float
    fast_lap_pct: float
    win_pct: float
    top3_pct: float
    top5_pct: float
    top10_pct: float
    proj_ownership: float
    leverage_score: float
    value: float                          # avg_fp / (salary / 1000)
    dominator_score: float                # avg_laps_led * 0.25 + fast_lap_pct * 5
    # Phase 3: underlying metrics for display
    current_form_finish: Optional[float] = None   # avg finish over last 10 races (any track)
    current_form_pts: Optional[float] = None      # avg DK/FD pts over last 10 races
    current_form_races: Optional[int] = None      # how many races in the sample
    tt_form_finish: Optional[float] = None        # avg finish at this track type (last 6)
    tt_form_races: Optional[int] = None           # races at this track type in sample
    driver_rating: Optional[float] = None         # loop data driver rating at this track (last 5)
    avg_fast_laps: Optional[float] = None         # simulated avg fast laps per race
    # Scoring component averages
    avg_place_pts: Optional[float] = None         # avg finish position points per sim
    avg_diff_pts: Optional[float] = None          # avg place differential points per sim
    avg_led_pts: Optional[float] = None           # avg laps led points per sim
    avg_fl_pts: Optional[float] = None            # avg fastest lap points per sim

class SimSettingsOut(BaseModel):
    # Sample sizes
    form_window: int = 10
    tt_form_window: int = 6
    track_rating_window: int = 5
    recent_form_races: int = 5
    # Toggles
    use_track_type: bool = True
    use_specific_track: bool = True
    use_recent_form: bool = True
    # Finish model weights (0-100)
    w_finish_track_type: int = 35
    w_finish_specific_track: int = 25
    w_finish_recent_form: int = 20
    w_finish_loop_data: int = 20
    # Laps led / fastest laps model weights
    w_laps_led_loop: int = 60
    w_fast_laps_loop: int = 60
    # Variance controls
    variance_finish: int = 100
    variance_laps_led: int = 100
    variance_fast_laps: int = 100

class SimulateResponse(BaseModel):
    simulation_id: int
    race_id: int
    race_name: str
    track_name: str
    track_type: str
    n_sims: int
    qual_locked: bool
    ran_at: datetime
    settings: Optional[SimSettingsOut] = None
    drivers: List[SimDriverResult]


# ── Lineups ───────────────────────────────────────────────
class OptimizeRequest(BaseModel):
    race_id: int
    platform: str = "draftkings"
    salary_cap: int = 50000
    n_lineups: int = Field(default=5, ge=1, le=150)
    min_salary: Optional[int] = None     # floor — don't leave too much on table
    max_ownership: Optional[float] = None # cap max projected ownership per driver
    lock_drivers: List[int] = []         # driver IDs to force into all lineups
    exclude_drivers: List[int] = []      # driver IDs to exclude

class LineupDriverOut(BaseModel):
    slot: int
    driver_id: int
    driver_name: str
    car_number: str
    salary: int
    avg_fp: float
    floor_fp: float
    ceiling_fp: float
    proj_ownership: float
    class Config: from_attributes = True

class LineupOut(BaseModel):
    id: Optional[int]
    label: Optional[str]
    platform: str
    total_salary: int
    salary_remaining: int
    proj_fp: float
    proj_ceiling: float
    drivers: List[LineupDriverOut]
    class Config: from_attributes = True
