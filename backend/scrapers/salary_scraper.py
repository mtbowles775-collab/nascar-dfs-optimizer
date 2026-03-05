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
import unicodedata
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """
    Normalize a name for fuzzy matching:
    - Strip accents (Suárez → Suarez)
    - Remove trailing periods from suffixes (Jr. → Jr)
    - Lowercase
    """
    # Strip accents via Unicode normalization
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Remove trailing periods (handles Jr. → Jr, Sr. → Sr, II. etc.)
    ascii_name = ascii_name.replace(".", "")
    return ascii_name.lower().strip()


def match_driver(db: Session, dk_name: str, race_season: int):
    """
    Match a DK player name to our drivers table.
    Tries: exact first+last -> last name only -> last word of name.
    Normalizes accents and suffixes before comparing.
    Returns driver_id or None.
    """
    from models import Driver, DriverSeason
    from sqlalchemy import func

    clean = dk_name.strip()
    parts = clean.split()
    if len(parts) < 2:
        return None

    first     = parts[0]
    last      = " ".join(parts[1:])   # handles "Jr.", "II", etc.
    norm_first = _normalize(first)
    norm_last  = _normalize(last)

    # Pull all drivers and compare normalized — handles accents + Jr./Jr mismatches
    all_drivers = db.query(Driver).all()

    # 1. Exact match first + last (normalized)
    for d in all_drivers:
        if _normalize(d.first_name) == norm_first and _normalize(d.last_name) == norm_last:
            return d.id

    # 2. Last name only (normalized)
    last_matches = [d for d in all_drivers if _normalize(d.last_name) == norm_last]
    if len(last_matches) == 1:
        return last_matches[0].id
    if len(last_matches) > 1:
        # Multiple same last name — narrow by season entry
        for m in last_matches:
            season_entry = db.query(DriverSeason).filter(
                DriverSeason.driver_id == m.id,
                DriverSeason.season    == race_season,
            ).first()
            if season_entry:
                return m.id

    # 3. Last word of name only (Jr. suffix edge cases)
    last_word      = parts[-1]
    norm_last_word = _normalize(last_word)
    if norm_last_word != norm_last:
        word_matches = [d for d in all_drivers if _normalize(d.last_name) == norm_last_word]
        if len(word_matches) == 1:
            return word_matches[0].id

    logger.warning(f"Could not match DK player '{dk_name}' to any driver")
    return None
