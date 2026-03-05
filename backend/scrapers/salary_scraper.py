# ============================================================
# scrapers/salary_scraper.py
# Processes DraftKings salary data sent from the browser.
#
# DK blocks cloud server IPs (Railway) from their lobby/API.
# Solution: browser fetches directly from DK, sends player
# data to this backend for matching + saving.
#
# Called by: POST /api/admin/salaries/load-from-browser
# ============================================================

import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def match_driver(db: Session, dk_name: str, race_season: int):
    """
    Match a DK player name to our drivers table.
    Tries: exact first+last -> last name only -> last word of name.
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
        # Multiple same last name — narrow by season entry
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
