# ============================================================
# scrapers/qualifying_scraper.py
# Scrapes qualifying results from NASCAR.com after sessions
# Triggered automatically on Friday/Saturday night via scheduler
# ============================================================

import httpx
import re
from datetime import datetime
from sqlalchemy.orm import Session
from models import Race, Driver, DriverSeason, Qualifying


async def scrape_qualifying(race_id: int, db: Session) -> int:
    """
    Pull qualifying results for a race and save to DB.
    Returns count of positions saved.
    """
    race = db.query(Race).filter(Race.id == race_id).first()
    if not race:
        raise ValueError(f"Race {race_id} not found")

    # Use race.season dynamically — do NOT hardcode the year
    # Correct format: /cacher/{season}/1/{race_number}/qualifying.json
    url = f"https://cf.nascar.com/cacher/{race.season}/1/{race.race_number}/qualifying.json"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    qual_entries = data.get("data", {}).get("entries", [])
    if not qual_entries:
        # Try alternate endpoint format
        url2 = f"https://cf.nascar.com/live/feeds/qualifying_{race.race_number}.json"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url2)
            if resp.status_code == 200:
                data = resp.json()
                qual_entries = data.get("qualifying", [])

    saved = 0
    for entry in qual_entries:
        driver_name     = entry.get("driver_name", "")
        car_number      = str(entry.get("car_number", ""))
        position        = int(entry.get("position", 0))
        lap_time        = entry.get("best_lap_time")
        lap_speed       = entry.get("best_lap_speed")

        if position == 0:
            continue

        # Find driver by car number + season
        season_info = (
            db.query(DriverSeason)
            .filter(
                DriverSeason.car_number == car_number,
                DriverSeason.season     == race.season
            )
            .first()
        )

        if not season_info:
            # Fallback: try to match by driver name
            parts   = driver_name.split()
            last    = parts[-1] if parts else ""
            driver  = db.query(Driver).filter(Driver.last_name.ilike(f"%{last}%")).first()
            if not driver:
                continue
            driver_id = driver.id
        else:
            driver_id = season_info.driver_id

        # Upsert qualifying record
        existing = db.query(Qualifying).filter(
            Qualifying.race_id   == race_id,
            Qualifying.driver_id == driver_id,
        ).first()

        if existing:
            existing.start_position = position
            existing.lap_time_sec   = float(lap_time) if lap_time else None
            existing.lap_speed_mph  = float(lap_speed) if lap_speed else None
            existing.session_date   = datetime.utcnow()
            existing.source         = "scraped"
        else:
            db.add(Qualifying(
                race_id         = race_id,
                driver_id       = driver_id,
                start_position  = position,
                lap_time_sec    = float(lap_time) if lap_time else None,
                lap_speed_mph   = float(lap_speed) if lap_speed else None,
                session_date    = datetime.utcnow(),
                source          = "scraped",
            ))
        saved += 1

    db.commit()
    return saved
