# ============================================================
# scrapers/salary_scraper.py
# Auto-scrapes DraftKings NASCAR salaries — no CSV needed.
#
# Flow:
#   1. Accept a draftGroupId (from DK contest URL) OR try api.draftkings.com
#      to find it automatically
#   2. Hit draftables API → get all players + salaries
#   3. Match players to our drivers table by name
#   4. Upsert into salaries table for the next scheduled race
#
# Triggered by: POST /api/admin/scrape/salaries?draft_group_id=12345
# Scheduled:    Tuesday + Wednesday 9am ET — but needs draft_group_id set
#               in DB or passed manually each week
# ============================================================

import asyncio
import httpx
import logging
from datetime import date
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DK_DRAFTABLES_URL  = "https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables"
DK_DRAFTGROUPS_URL = "https://api.draftkings.com/draftgroups/v1/draftgroups?sportId=6"  # 6 = NASCAR on DK

# Keywords to identify NASCAR Cup Classic contests (exclude Showdown/Xfinity/Trucks)
DK_CLASSIC_KEYWORDS = ["nascar", "cup", "classic", "500", "400", "300", "phoenix", "race"]
DK_EXCLUDE_KEYWORDS = ["showdown", "tiers", "tier", "xfinity", "trucks", "craftsman"]


async def _find_nascar_draft_group_auto() -> tuple[int | None, str | None]:
    """
    Try to find the NASCAR Cup draft group via api.draftkings.com (not the blocked lobby).
    Returns (draftGroupId, name) or (None, None).
    """
    async with httpx.AsyncClient(
        timeout=15,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
    ) as client:
        try:
            resp = await client.get(DK_DRAFTGROUPS_URL)
            if resp.status_code != 200:
                logger.warning(f"DK draftgroups API returned {resp.status_code}")
                return None, None

            data = resp.json()
            draft_groups = data.get("draftGroups", [])

            for dg in draft_groups:
                game_type = (dg.get("gameType") or dg.get("gameTypeName") or "").lower()
                contest_type = (dg.get("contestType") or "").lower()
                name = (dg.get("name") or game_type or "").lower()

                # Must not be Showdown/Xfinity/Trucks
                if any(kw in name for kw in DK_EXCLUDE_KEYWORDS):
                    continue
                if any(kw in contest_type for kw in DK_EXCLUDE_KEYWORDS):
                    continue

                # Classic NASCAR — return first match
                dg_id = dg.get("draftGroupId")
                if dg_id:
                    return dg_id, dg.get("name") or game_type

        except Exception as e:
            logger.error(f"Auto draft group lookup failed: {e}")

    return None, None


def _match_driver(db: Session, dk_name: str, race_season: int):
    """
    Match a DK player name to our drivers table.
    Tries: exact first+last -> last name only -> last word of name
    Returns driver_id or None.
    """
    from models import Driver, DriverSeason
    from sqlalchemy import func

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

    # 2. Last name only
    matches = db.query(Driver).filter(
        func.lower(Driver.last_name) == last.lower()
    ).all()
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        for m in matches:
            season_entry = db.query(DriverSeason).filter(
                DriverSeason.driver_id == m.id,
                DriverSeason.season    == race_season,
            ).first()
            if season_entry:
                return m.id

    # 3. Last word of name only (Jr. suffix edge cases)
    last_word = parts[-1]
    if last_word.lower() != last.lower():
        matches2 = db.query(Driver).filter(
            func.lower(Driver.last_name) == last_word.lower()
        ).all()
        if len(matches2) == 1:
            return matches2[0].id

    logger.warning(f"Could not match DK player '{dk_name}' to any driver")
    return None


async def scrape_dk_salaries(db: Session, draft_group_id: int | None = None) -> dict:
    """
    Main entry point. Finds the next scheduled Cup race, scrapes DK salaries,
    and upserts into the salaries table.

    draft_group_id: Pass explicitly from the DK contest URL, or leave None to
                    attempt auto-detection via api.draftkings.com.
    Returns a summary dict.
    """
    from models import Race, Salary

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

    contest_name = None

    # Step 1: Get draft group ID
    if draft_group_id:
        logger.info(f"Using provided draftGroupId: {draft_group_id}")
        contest_name = f"manual (id={draft_group_id})"
    else:
        logger.info("Attempting auto-detect of NASCAR DK draft group...")
        draft_group_id, contest_name = await _find_nascar_draft_group_auto()

        if not draft_group_id:
            return {
                "error": "Could not auto-detect NASCAR draft group. "
                         "Pass draft_group_id manually: POST /api/admin/scrape/salaries?draft_group_id=XXXXX "
                         "Find it in the DK contest URL when you open a NASCAR contest."
            }

    logger.info(f"Using DK draft group {draft_group_id}: {contest_name}")

    # Step 2: Get draftables (players + salaries) — this endpoint is never blocked
    async with httpx.AsyncClient(
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
    ) as client:
        url = DK_DRAFTABLES_URL.format(draft_group_id=draft_group_id)
        resp = await client.get(url)
        if resp.status_code != 200:
            return {"error": f"DK draftables returned {resp.status_code}"}
        draftables_data = resp.json()

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

        # Only include driver positions
        position = (player.get("position") or "").upper()
        if position and position not in ("D", "DR", "DRIVER", ""):
            skipped += 1
            continue

        driver_id = _match_driver(db, dk_name, race.season)
        if not driver_id:
            unmatched.append(dk_name)
            skipped += 1
            continue

        # Calculate salary change vs previous race
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
