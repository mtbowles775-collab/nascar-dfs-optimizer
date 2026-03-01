# ============================================================
# models.py — SQLAlchemy ORM models
# One class per database table
# ============================================================

from sqlalchemy import (
    Column, Integer, String, Numeric, Boolean, Date,
    DateTime, Text, ForeignKey, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


# ── User (subscription / auth) ────────────────────────────
class User(Base):
    __tablename__ = "users"
    id                  = Column(Integer, primary_key=True)
    supabase_uid        = Column(String(255), unique=True, nullable=False, index=True)
    email               = Column(String(255), unique=True, nullable=False)
    display_name        = Column(String(100))
    tier                = Column(String(20), default="free")         # free | pro | admin
    subscription_status = Column(String(20), default="inactive")     # inactive | active | canceled | past_due
    stripe_customer_id  = Column(String(255))
    is_admin            = Column(Boolean, default=False)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())
    lineups             = relationship("Lineup", back_populates="user")


# ── Track hierarchy ───────────────────────────────────────
class TrackType(Base):
    __tablename__ = "track_types"
    id          = Column(Integer, primary_key=True)
    name        = Column(String(50), nullable=False, unique=True)
    description = Column(Text)
    tracks      = relationship("Track", back_populates="track_type")


class Track(Base):
    __tablename__ = "tracks"
    id              = Column(Integer, primary_key=True)
    name            = Column(String(100), nullable=False)
    short_name      = Column(String(50))
    track_type_id   = Column(Integer, ForeignKey("track_types.id"))
    length_miles    = Column(Numeric(5, 3), nullable=False)
    surface         = Column(String(20), default="Asphalt")
    is_oval         = Column(Boolean, default=True)
    city            = Column(String(50))
    state_country   = Column(String(50))
    active          = Column(Boolean, default=True)
    created_at      = Column(DateTime, server_default=func.now())
    track_type      = relationship("TrackType", back_populates="tracks")
    races           = relationship("Race", back_populates="track")


# ── Teams & manufacturers ────────────────────────────────
class Manufacturer(Base):
    __tablename__ = "manufacturers"
    id      = Column(Integer, primary_key=True)
    name    = Column(String(50), nullable=False, unique=True)
    teams   = relationship("Team", back_populates="manufacturer")


class Team(Base):
    __tablename__ = "teams"
    id              = Column(Integer, primary_key=True)
    name            = Column(String(100), nullable=False)
    short_name      = Column(String(50))
    manufacturer_id = Column(Integer, ForeignKey("manufacturers.id"))
    active          = Column(Boolean, default=True)
    founded_year    = Column(Integer)
    manufacturer    = relationship("Manufacturer", back_populates="teams")
    driver_seasons  = relationship("DriverSeason", back_populates="team")


# ── Drivers ──────────────────────────────────────────────
class Driver(Base):
    __tablename__ = "drivers"
    id              = Column(Integer, primary_key=True)
    first_name      = Column(String(50), nullable=False)
    last_name       = Column(String(50), nullable=False)
    nationality     = Column(String(50), default="American")
    birth_date      = Column(Date)
    active          = Column(Boolean, default=True)
    created_at      = Column(DateTime, server_default=func.now())
    seasons         = relationship("DriverSeason", back_populates="driver")
    results         = relationship("Result", back_populates="driver")
    qualifying      = relationship("Qualifying", back_populates="driver")
    practice        = relationship("Practice", back_populates="driver")
    loop_data       = relationship("LoopData", back_populates="driver")
    ownership       = relationship("Ownership", back_populates="driver")
    salaries        = relationship("Salary", back_populates="driver")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class DriverSeason(Base):
    __tablename__ = "driver_seasons"
    id              = Column(Integer, primary_key=True)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    season          = Column(Integer, nullable=False)
    car_number      = Column(String(4), nullable=False)
    team_id         = Column(Integer, ForeignKey("teams.id"))
    manufacturer_id = Column(Integer, ForeignKey("manufacturers.id"))
    __table_args__  = (UniqueConstraint("driver_id", "season"),)
    driver          = relationship("Driver", back_populates="seasons")
    team            = relationship("Team", back_populates="driver_seasons")
    manufacturer    = relationship("Manufacturer")


# ── Races ────────────────────────────────────────────────
class Race(Base):
    __tablename__ = "races"
    id              = Column(Integer, primary_key=True)
    season          = Column(Integer, nullable=False)
    race_number     = Column(Integer, nullable=False)
    series          = Column(String(20), default="cup", nullable=False)  # cup | xfinity | trucks
    track_id        = Column(Integer, ForeignKey("tracks.id"), nullable=False)
    race_name       = Column(String(150))
    race_date       = Column(Date)
    scheduled_laps  = Column(Integer, nullable=False)
    actual_laps     = Column(Integer)
    stage1_laps     = Column(Integer)
    stage2_laps     = Column(Integer)
    stage3_laps     = Column(Integer)
    total_miles     = Column(Numeric(7, 2))
    caution_count   = Column(Integer)                               # NEW: for caution modeling
    caution_laps    = Column(Integer)                               # NEW: for caution modeling
    lead_changes    = Column(Integer)                               # NEW: dominator analysis
    leaders_count   = Column(Integer)                               # NEW: dominator analysis
    status          = Column(String(20), default="scheduled")
    notes           = Column(Text)
    created_at      = Column(DateTime, server_default=func.now())
    __table_args__  = (UniqueConstraint("season", "race_number", "series"),)
    track           = relationship("Track", back_populates="races")
    results         = relationship("Result", back_populates="race")
    qualifying      = relationship("Qualifying", back_populates="race")
    practice        = relationship("Practice", back_populates="race")
    loop_data       = relationship("LoopData", back_populates="race")
    simulations     = relationship("Simulation", back_populates="race")


# ── Qualifying ───────────────────────────────────────────
class Qualifying(Base):
    __tablename__ = "qualifying"
    id              = Column(Integer, primary_key=True)
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    start_position  = Column(Integer, nullable=False)
    lap_time_sec    = Column(Numeric(8, 4))
    lap_speed_mph   = Column(Numeric(7, 3))
    session_date    = Column(DateTime)
    source          = Column(String(30), default="scraped")
    created_at      = Column(DateTime, server_default=func.now())
    __table_args__  = (UniqueConstraint("race_id", "driver_id"),)
    race            = relationship("Race", back_populates="qualifying")
    driver          = relationship("Driver", back_populates="qualifying")


# ── Practice ─────────────────────────────────────────────
class Practice(Base):
    __tablename__ = "practice"
    id              = Column(Integer, primary_key=True)
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    session_number  = Column(Integer, default=1)
    best_lap_time   = Column(Numeric(8, 4))
    best_lap_speed  = Column(Numeric(7, 3))
    avg_lap_speed   = Column(Numeric(7, 3))
    laps_run        = Column(Integer)
    position        = Column(Integer)
    session_date    = Column(DateTime)
    source          = Column(String(30), default="scraped")
    created_at      = Column(DateTime, server_default=func.now())
    __table_args__  = (UniqueConstraint("race_id", "driver_id", "session_number"),)
    race            = relationship("Race", back_populates="practice")
    driver          = relationship("Driver", back_populates="practice")


# ── Results ──────────────────────────────────────────────
class Result(Base):
    __tablename__ = "results"
    id                  = Column(Integer, primary_key=True)
    race_id             = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id           = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    finish_position     = Column(Integer, nullable=False)
    start_position      = Column(Integer)
    laps_completed      = Column(Integer)
    laps_led            = Column(Integer, default=0)
    status              = Column(String(50), default="running")
    fastest_lap         = Column(Boolean, default=False)
    fastest_lap_time    = Column(Numeric(8, 4))
    fastest_lap_speed   = Column(Numeric(7, 3))
    green_flag_laps     = Column(Integer)
    green_flag_speed    = Column(Numeric(7, 3))
    dk_salary           = Column(Integer)
    dk_points           = Column(Numeric(6, 2))
    dk_place_pts        = Column(Numeric(6, 2))
    dk_place_diff_pts   = Column(Numeric(6, 2))
    dk_laps_led_pts     = Column(Numeric(6, 2))
    dk_fast_lap_pts     = Column(Numeric(6, 2))
    dk_laps_complete_pts= Column(Numeric(6, 2))
    dk_dominator_bonus  = Column(Numeric(6, 2))
    fd_salary           = Column(Integer)
    fd_points           = Column(Numeric(6, 2))
    fd_place_pts        = Column(Numeric(6, 2))
    fd_laps_led_pts     = Column(Numeric(6, 2))
    fd_fast_lap_pts     = Column(Numeric(6, 2))
    fd_place_diff_pts   = Column(Numeric(6, 2))
    fd_laps_complete_pts= Column(Numeric(6, 2))
    created_at          = Column(DateTime, server_default=func.now())
    __table_args__      = (UniqueConstraint("race_id", "driver_id"),)
    race                = relationship("Race", back_populates="results")
    driver              = relationship("Driver", back_populates="results")


# ── Loop Data ────────────────────────────────────────────
class LoopData(Base):
    __tablename__ = "loop_data"
    id                      = Column(Integer, primary_key=True)
    race_id                 = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id               = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    green_flag_passes       = Column(Integer, default=0)
    green_flag_passed       = Column(Integer, default=0)
    quality_passes          = Column(Integer, default=0)
    quality_passed          = Column(Integer, default=0)
    laps_in_top15           = Column(Integer, default=0)
    laps_in_top10           = Column(Integer, default=0)
    laps_in_top5            = Column(Integer, default=0)
    pct_laps_in_top15       = Column(Numeric(5, 2))
    fastest_laps            = Column(Integer, default=0)
    fastest_lap_pct         = Column(Numeric(5, 2))
    avg_running_position    = Column(Numeric(5, 2))
    driver_rating           = Column(Numeric(6, 2))
    passing_differential    = Column(Integer, default=0)            # NEW: from live feed
    avg_speed               = Column(Numeric(7, 3))                 # NEW: from live feed
    avg_restart_speed       = Column(Numeric(7, 3))                 # NEW: from live feed
    best_lap_speed          = Column(Numeric(7, 3))                 # NEW: from live feed
    stage1_points           = Column(Integer, default=0)
    stage2_points           = Column(Integer, default=0)
    stage3_points           = Column(Integer, default=0)            # NEW: 3rd stage
    stage_points_total      = Column(Integer, default=0)
    created_at              = Column(DateTime, server_default=func.now())
    __table_args__          = (UniqueConstraint("race_id", "driver_id"),)
    race                    = relationship("Race", back_populates="loop_data")
    driver                  = relationship("Driver", back_populates="loop_data")


# ── Ownership ────────────────────────────────────────────
class Ownership(Base):
    __tablename__ = "ownership"
    id              = Column(Integer, primary_key=True)
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    platform        = Column(String(20), nullable=False)
    contest_type    = Column(String(30))
    ownership_pct   = Column(Numeric(5, 2))
    captain_pct     = Column(Numeric(5, 2))
    recorded_at     = Column(DateTime, server_default=func.now())
    source          = Column(String(50))
    __table_args__  = (UniqueConstraint("race_id", "driver_id", "platform", "contest_type"),)
    race            = relationship("Race")
    driver          = relationship("Driver", back_populates="ownership")


# ── Salaries ─────────────────────────────────────────────
class Salary(Base):
    __tablename__ = "salaries"
    id              = Column(Integer, primary_key=True)
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    platform        = Column(String(20), nullable=False)
    salary          = Column(Integer, nullable=False)
    salary_change   = Column(Integer)
    roster_position = Column(String(20), default="driver")
    created_at      = Column(DateTime, server_default=func.now())
    __table_args__  = (UniqueConstraint("race_id", "driver_id", "platform", "roster_position"),)
    race            = relationship("Race")
    driver          = relationship("Driver", back_populates="salaries")


# ── Simulations ──────────────────────────────────────────
class Simulation(Base):
    __tablename__ = "simulations"
    id              = Column(Integer, primary_key=True)
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    n_sims          = Column(Integer, nullable=False)
    ran_at          = Column(DateTime, server_default=func.now())
    qual_locked     = Column(Boolean, default=False)
    results_json    = Column(JSON, nullable=False)
    settings_json   = Column(JSON)
    race            = relationship("Race", back_populates="simulations")
    driver_results  = relationship("SimulationDriverResult", back_populates="simulation",
                                   cascade="all, delete-orphan")


class SimulationDriverResult(Base):
    __tablename__ = "simulation_driver_results"
    id              = Column(Integer, primary_key=True)
    simulation_id   = Column(Integer, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False)
    driver_id       = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    avg_fp          = Column(Numeric(6, 2))
    median_fp       = Column(Numeric(6, 2))
    floor_fp        = Column(Numeric(6, 2))
    ceiling_fp      = Column(Numeric(6, 2))
    avg_finish      = Column(Numeric(5, 2))
    avg_laps_led    = Column(Numeric(6, 2))
    fast_lap_pct    = Column(Numeric(5, 3))
    win_pct         = Column(Numeric(5, 3))
    top3_pct        = Column(Numeric(5, 3))
    top5_pct        = Column(Numeric(5, 3))
    top10_pct       = Column(Numeric(5, 3))
    proj_ownership  = Column(Numeric(5, 2))
    leverage_score  = Column(Numeric(5, 2))
    __table_args__  = (UniqueConstraint("simulation_id", "driver_id"),)
    simulation      = relationship("Simulation", back_populates="driver_results")
    driver          = relationship("Driver")


# ── Lineups (user-scoped) ────────────────────────────────
class Lineup(Base):
    __tablename__ = "lineups"
    id              = Column(Integer, primary_key=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable for migration
    race_id         = Column(Integer, ForeignKey("races.id"), nullable=False)
    platform        = Column(String(20), default="draftkings")
    label           = Column(String(100))
    total_salary    = Column(Integer)
    proj_fp         = Column(Numeric(6, 2))
    proj_ceiling    = Column(Numeric(6, 2))
    created_at      = Column(DateTime, server_default=func.now())
    user            = relationship("User", back_populates="lineups")
    drivers         = relationship("LineupDriver", back_populates="lineup",
                                   cascade="all, delete-orphan")


class LineupDriver(Base):
    __tablename__ = "lineup_drivers"
    id          = Column(Integer, primary_key=True)
    lineup_id   = Column(Integer, ForeignKey("lineups.id", ondelete="CASCADE"), nullable=False)
    driver_id   = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    slot        = Column(Integer)
    __table_args__ = (UniqueConstraint("lineup_id", "driver_id"),)
    lineup      = relationship("Lineup", back_populates="drivers")
    driver      = relationship("Driver")
