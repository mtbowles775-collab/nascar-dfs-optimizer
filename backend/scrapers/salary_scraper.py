# ============================================================
# scrapers/salary_scraper.py
# Auto-scrapes DraftKings NASCAR salaries — no CSV needed.
#
# Flow:
#   1. Hit DK lobby API → find upcoming NASCAR Classic contest
#   2. Extract draftGroupId
#   3. Hit draftables API → get all players + salaries
#   4. Match players to our drivers table by name
#   5. Upsert into salaries table for the next scheduled race
#
# Unofficial DK endpoints — no auth required, but could change.
# Triggered by: POST /api/admin/scrape/salaries
# Scheduled:    Tuesday + Wednesday 9am ET (auto via scheduler)
# ============================================================

import httpx
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DK_CONTESTS_URL  = "https://www.draftkings.com/lobby/getcontests?sport=NASCAR"
DK_DRAFTABLES_URL = "https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables"

# DK game type names we care about — Classic only (not Showdown/Tiers)
DK_CLASSIC_KEYWORDS = ["nascar", "cup", "classic"]
DK_EXCLUDE_KEYWORDS = ["showdown", "tiers", "tier", "xfinity", "trucks", "craftsman"]


def _find_nascar_classic_draft_group(contests_data: dict) -> tuple[int | None, str | None]:
    """
    Parse the DK lobby response and find the NASCAR Cup Classic draft group.
    Returns (draftGroupId, contestName) or (None, None) if not found.
    """
    contests = contests_data.get("Contests", [])
    draft_groups = contests_data.get("DraftGroups", [])

    # Build a set of draftGroupIds from Classic NASCAR contests
    classic_dg_ids = set()
    for contest in contests:
        name = (contest.get("n") or "").lower()

        # Must contain NASCAR-related keyword
        if not any(kw in name for kw in DK_CLASSIC_KEYWORDS):
            continue

        # Must NOT be Showdown/Tiers/Xfinity/Trucks
        if any(kw in name for kw in DK_EXCLUDE_KEYWORDS):
            continue

        dg_id = contest.get("dg")
        if dg_id:
            classic_dg_ids.add((dg_id, contest.get("n", "")))

    if not classic_dg_ids:
        # Fallback: look through DraftGroups directly
        for dg in draft_groups:
            dg_name = (dg.get("DraftGroupTag") or dg.get("GameTypeName") or "").lower()
            if "nascar" in dg_name and "showdown" not in dg_name:
                return dg.get("DraftGroupId"), dg.get("GameTypeName", "")

        return None, None

    # If multiple found, pick the one with the soonest start
    # (They're usually sorted by start time already)
    dg_id, name = list(classic_dg_ids)[0]
    return dg_id, name


def _match_driver(db: Session, dk_name: str, race_season: int):
    """
    Match a DK player name to our drivers table.
    Tries: exact full name → last name → first+last fuzzy
    Returns driver_id or None.
    """
    from models import Driver, DriverSeason
    from sqlalchemy import func

    # Clean up DK name (sometimes has suffixes like "Jr." handled differently)
    clean = dk_name.strip()
    parts = clean.split()
    if len(parts) < 2:
        return None

    first = parts[0]
    last  = " ".join(parts[1:])  # handles "Jr.", "II", etc.

    # 1. Exact match first + last
    driver = db.query(Driver).filter(
        func.lower(Driver.first_name) == first.lower(),
        func.lower(Driver.last_name)  == last.lower(),
    ).first()
    if driver:
        return driver.id

    # 2. Last name only (handles middle names, typos in first)
    matches = db.query(Driver).filter(
        func.lower(Driver.last_name) == last.lower()
    ).all()
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        # Multiple drivers with same last name — try to narrow by season
        for m in matches:
            season_entry = db.query(DriverSeason).filter(
                DriverSeason.driver_id == m.id,
                DriverSeason.season    == race_season,
            ).first()
            if season_entry:
                return m.id

    # 3. Try last name only with the last word of the DK name
    last_word = parts[-1]
    matches2 = db.query(Driver).filter(
        func.lower(Driver.last_name) == last_word.lower()
    ).all()
    if len(matches2) == 1:
        return matches2[0].id

    logger.warning(f"Could not match DK player '{dk_name}' to any driver")
    return None


async def scrape_dk_salaries(db: Session) -> dict:
    """
    Main entry point. Finds the next scheduled Cup race, scrapes DK salaries,
    and upserts into the salaries table.
    Returns a summary dict.
    """
    from models import Race, Salary
    from datetime import date

    # Find the next scheduled Cup race
    race = (
        db.query(Race)
        .filter(
            Race.race_date >= date.today(),
            Race.status    == "scheduled",
            Race.series    == "cup",
            Race.race_number > 0,
        )
        .order_by(Race.race_date)
        .first()
    )
    if not race:
        return {"error": "No upcoming Cup race found"}

    async with httpx.AsyncClient(
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
    ) as client:

        # Step 1: Get contest list
        logger.info("Fetching DK contest lobby...")
        resp = await client.get(DK_CONTESTS_URL)
        if resp.status_code != 200:
            return {"error": f"DK lobby returned {resp.status_code}"}

        contests_data = resp.json()
        draft_group_id, contest_name = _find_nascar_classic_draft_group(contests_data)

        if not draft_group_id:
            return {
                "error": "No NASCAR Classic draft group found in DK lobby. "
                         "Salaries may not be posted yet (typically Tuesday–Wednesday)."
            }

        logger.info(f"Found DK draft group {draft_group_id}: {contest_name}")

        # Step 2: Get draftables (players + salaries)
        url = DK_DRAFTABLES_URL.format(draft_group_id=draft_group_id)
        resp2 = await client.get(url)
        if resp2.status_code != 200:
            return {"error": f"DK draftables returned {resp2.status_code}"}

        draftables_data = resp2.json()

    # Step 3: Parse players
    players = draftables_data.get("draftables", [])
    if not players:
        return {"error": "No draftables found in DK response"}

    saved = 0
    skipped = 0
    unmatched = []

    for player in players:
        dk_name = player.get("displayName") or player.get("playerName", "")
        salary  = player.get("salary")

        if not dk_name or not salary:
            skipped += 1
            continue

        # Only include drivers (not any other position if DK adds them)
        position = (player.get("position") or "").upper()
        if position and position not in ("D", "DR", "DRIVER", ""):
            skipped += 1
            continue

        driver_id = _match_driver(db, dk_name, race.season)
        if not driver_id:
            unmatched.append(dk_name)
            skipped += 1
            continue

        # Get previous salary for this driver to calculate change
        prev = db.query(Salary).filter(
            Salary.driver_id       == driver_id,
            Salary.platform        == "draftkings",
            Salary.roster_position == "driver",
        ).order_by(Salary.created_at.desc()).first()

        salary_change = None
        if prev and prev.race_id != race.id:
            salary_change = salary - prev.salary

        # Upsert
        existing = db.query(Salary).filter(
            Salary.race_id         == race.id,
            Salary.driver_id       == driver_id,
            Salary.platform        == "draftkings",
            Salary.roster_position == "driver",
        ).first()

        if existing:
            existing.salary        = salary
            existing.salary_change = salary_change
        else:
            db.add(Salary(
                race_id         = race.id,
                driver_id       = driver_id,
                platform        = "draftkings",
                salary          = salary,
                salary_change   = salary_change,
                roster_position = "driver",
            ))
        saved += 1

    db.commit()

    result = {
        "race_id":        race.id,
        "race_name":      race.race_name,
        "race_date":      str(race.race_date),
        "draft_group_id": draft_group_id,
        "contest_name":   contest_name,
        "platform":       "draftkings",
        "saved":          saved,
        "skipped":        skipped,
    }

    if unmatched:
        result["unmatched_players"] = unmatched
        logger.warning(f"Unmatched DK players: {unmatched}")

    logger.info(f"DK salaries: {saved} saved, {skipped} skipped for {race.race_name}")
    return result
