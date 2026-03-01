# Phase 0 — Foundation Fixes

## Files Changed (11 files)

### Modified
| File | What Changed |
|------|-------------|
| `backend/main.py` | Fixed CORS (regex for Vercel), added lifespan startup/shutdown, scheduler auto-starts |
| `backend/models.py` | Added `User` model, `series` on Race, `nascar_driver_id` on Driver, `nascar_race_id` on Race, race metadata fields (cautions/lead_changes), loop data fields (passing_diff/speeds), `driver_rating` on Result, `user_id` on Lineup |
| `backend/scheduler.py` | Rewired to use live feed scraper, polls every 30min on race days (Sat 12-7pm, Sun 2pm-midnight ET) |
| `backend/routers/results.py` | Split to standalone, fixed N+1 query (single JOIN instead of per-row lookups) |
| `backend/routers/practice.py` | Split to standalone, fixed N+1 query |
| `backend/routers/ownership.py` | Split to standalone, fixed N+1 query |
| `backend/routers/salaries.py` | Split to standalone, fixed N+1 query |
| `backend/routers/lineups.py` | Split to standalone |
| `backend/routers/admin.py` | Split to standalone, added `/scrape/live-feed` endpoint |
| `backend/routers/__init__.py` | Cleaned up (no cross-imports) |

### Added
| File | What It Does |
|------|-------------|
| `backend/scrapers/live_feed_scraper.py` | Pulls cf.nascar.com live feed → upserts Results + LoopData + race metadata. Calculates DK + FD fantasy points. Auto-creates drivers. |
| `migrations/001_phase0_schema.sql` | Safe migration for all new columns/tables (idempotent, run multiple times) |

### Removed
| File | Why |
|------|-----|
| `backend/routers/stubs.py` | Dead code, no longer needed after router split |

## Key Fixes
1. **CORS** — `https://*.vercel.app` wildcard doesn't work in CORSMiddleware; switched to `allow_origin_regex`
2. **Router split** — All 5 routers that were crammed into `results.py` are now standalone modules
3. **N+1 queries** — Results, practice, ownership, salaries all used per-row `db.query(Driver)` loops; now use JOINs
4. **Scheduler** — Was a dead import (never called `start_scheduler()`); now auto-starts via FastAPI lifespan
5. **Live feed scraper** — Replaces broken `cf.nascar.com/cacher/` endpoints with working live feed
6. **Series support** — Race table now has `series` column for Cup/Xfinity/Trucks
7. **User model** — Ready for Supabase Auth integration with tier/subscription fields

## Migration Steps
1. Run `migrations/001_phase0_schema.sql` against your Supabase database
2. Deploy updated backend to Railway
3. Test: `POST /api/admin/scrape/live-feed` to verify live feed scraper works
