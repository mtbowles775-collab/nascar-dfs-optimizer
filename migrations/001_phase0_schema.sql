-- ============================================================
-- Phase 0 Migration: Schema updates for Turn Four
-- Run against your Supabase PostgreSQL database
-- Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS checks)
-- ============================================================

-- ── 1. Users table (Supabase Auth integration) ─────────
CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    supabase_uid        VARCHAR(36) UNIQUE NOT NULL,
    email               VARCHAR(255) UNIQUE NOT NULL,
    display_name        VARCHAR(100),
    tier                VARCHAR(20) DEFAULT 'free',
    subscription_status VARCHAR(20) DEFAULT 'inactive',
    is_admin            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_supabase_uid ON users(supabase_uid);


-- ── 2. Add series column to races ──────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'races' AND column_name = 'series'
    ) THEN
        ALTER TABLE races ADD COLUMN series VARCHAR(10) DEFAULT 'cup' NOT NULL;
    END IF;
END $$;

-- Update unique constraint to include series
-- (drop old one first, then create new)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'races_season_race_number_key'
    ) THEN
        ALTER TABLE races DROP CONSTRAINT races_season_race_number_key;
    END IF;
END $$;
ALTER TABLE races ADD CONSTRAINT races_season_race_number_series_key
    UNIQUE (season, race_number, series);


-- ── 3. Add race metadata columns ──────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'races' AND column_name = 'caution_segments') THEN
        ALTER TABLE races ADD COLUMN caution_segments INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'races' AND column_name = 'caution_laps') THEN
        ALTER TABLE races ADD COLUMN caution_laps INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'races' AND column_name = 'lead_changes') THEN
        ALTER TABLE races ADD COLUMN lead_changes INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'races' AND column_name = 'number_of_leaders') THEN
        ALTER TABLE races ADD COLUMN number_of_leaders INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'races' AND column_name = 'nascar_race_id') THEN
        ALTER TABLE races ADD COLUMN nascar_race_id INTEGER;
        CREATE INDEX IF NOT EXISTS idx_races_nascar_race_id ON races(nascar_race_id);
    END IF;
END $$;


-- ── 4. Add nascar_driver_id to drivers ────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'drivers' AND column_name = 'nascar_driver_id') THEN
        ALTER TABLE drivers ADD COLUMN nascar_driver_id INTEGER UNIQUE;
        CREATE INDEX IF NOT EXISTS idx_drivers_nascar_driver_id ON drivers(nascar_driver_id);
    END IF;
END $$;


-- ── 5. Add driver_rating to results ──────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'results' AND column_name = 'driver_rating') THEN
        ALTER TABLE results ADD COLUMN driver_rating NUMERIC(6,2);
    END IF;
END $$;


-- ── 6. Add live-feed columns to loop_data ────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'loop_data' AND column_name = 'passing_differential') THEN
        ALTER TABLE loop_data ADD COLUMN passing_differential INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'loop_data' AND column_name = 'avg_speed') THEN
        ALTER TABLE loop_data ADD COLUMN avg_speed NUMERIC(7,3);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'loop_data' AND column_name = 'avg_restart_speed') THEN
        ALTER TABLE loop_data ADD COLUMN avg_restart_speed NUMERIC(7,3);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'loop_data' AND column_name = 'best_lap_speed') THEN
        ALTER TABLE loop_data ADD COLUMN best_lap_speed NUMERIC(7,3);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'loop_data' AND column_name = 'laps_position_improved') THEN
        ALTER TABLE loop_data ADD COLUMN laps_position_improved INTEGER DEFAULT 0;
    END IF;
END $$;


-- ── 7. Add user_id to lineups ────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'lineups' AND column_name = 'user_id') THEN
        ALTER TABLE lineups ADD COLUMN user_id INTEGER REFERENCES users(id);
    END IF;
END $$;


-- ── 8. Verify migration ─────────────────────────────
SELECT 'Migration complete. Verify:' AS status;
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('users', 'races', 'drivers', 'results', 'loop_data', 'lineups')
  AND column_name IN (
    'supabase_uid', 'tier', 'series', 'caution_segments', 'caution_laps',
    'lead_changes', 'number_of_leaders', 'nascar_race_id', 'nascar_driver_id',
    'driver_rating', 'passing_differential', 'avg_speed', 'avg_restart_speed',
    'best_lap_speed', 'laps_position_improved', 'user_id'
  )
ORDER BY table_name, column_name;
