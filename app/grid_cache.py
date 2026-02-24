"""Pre-computed grid cache for sub-second map cluster responses.

On startup the ``ensure_grid_cache`` coroutine creates a small
``map_grid_cache`` table (a few thousand rows total) that stores
pre-aggregated cluster counts for every zoom-level grid size.
The map endpoint queries this table instead of scanning 1 M+ rows
at every request.

The table is shared across all Uvicorn workers and persists across
restarts.  ``refresh_grid_cache`` rebuilds the data after weekly
FCC data imports.
"""

import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Grid sizes must match the zoom-ladder in routes/api.py
GRID_SIZES: list[float] = [2.0, 1.0, 0.4, 0.15, 0.06, 0.02, 0.005]

_DDL = """\
CREATE TABLE IF NOT EXISTS map_grid_cache (
    grid_size  DOUBLE PRECISION NOT NULL,
    grid_lat   INTEGER          NOT NULL,
    grid_lng   INTEGER          NOT NULL,
    avg_lat    DOUBLE PRECISION NOT NULL,
    avg_lng    DOUBLE PRECISION NOT NULL,
    cnt        INTEGER          NOT NULL,
    state      VARCHAR(2),
    county     VARCHAR(100),
    PRIMARY KEY (grid_size, grid_lat, grid_lng)
)
"""

_TEMP_ACTIVE = """\
CREATE TEMP TABLE _active_locs ON COMMIT DROP AS
SELECT latitude, longitude, state, county
FROM   locations l
WHERE  l.latitude  IS NOT NULL
  AND  l.longitude IS NOT NULL
  AND  EXISTS (
         SELECT 1
         FROM   licenses lic
         WHERE  lic.id = l.license_id
           AND  lic.status = 'A'
       )
"""

_INSERT_GRID = """\
INSERT INTO map_grid_cache
       (grid_size, grid_lat, grid_lng, avg_lat, avg_lng, cnt, state, county)
SELECT :gs,
       CAST(FLOOR(latitude  / :gs) AS INTEGER),
       CAST(FLOOR(longitude / :gs) AS INTEGER),
       AVG(latitude),
       AVG(longitude),
       COUNT(*),
       MIN(state),
       MIN(county)
FROM   _active_locs
GROUP  BY FLOOR(latitude / :gs), FLOOR(longitude / :gs)
ON CONFLICT (grid_size, grid_lat, grid_lng) DO NOTHING
"""


async def _populate(conn, *, truncate: bool = False) -> int:
    """Insert pre-aggregated rows for every grid size.  Returns row count."""
    if truncate:
        await conn.execute(text("TRUNCATE map_grid_cache"))

    # Materialize active locations once (single expensive scan)
    await conn.execute(text(_TEMP_ACTIVE))
    await conn.execute(text("ANALYZE _active_locs"))

    total = 0
    for gs in GRID_SIZES:
        r = await conn.execute(text(_INSERT_GRID), {"gs": gs})
        total += r.rowcount
        logger.debug("  grid %.3f → %d rows", gs, r.rowcount)
    return total


async def ensure_grid_cache(engine: AsyncEngine) -> None:
    """Create the table and populate it **if empty**.

    Safe to call from multiple Uvicorn workers concurrently — duplicates
    are silently ignored via ``ON CONFLICT DO NOTHING``.
    """
    # Make sure table exists (retry once if another worker raced us)
    for attempt in range(2):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(_DDL))
            break
        except Exception:
            if attempt == 0:
                import asyncio
                await asyncio.sleep(0.5)
            else:
                raise

    async with engine.begin() as conn:
        row = await conn.execute(
            text("SELECT COUNT(*) FROM map_grid_cache")
        )
        if row.scalar() > 0:
            logger.info("Grid cache already populated — skipping")
            return

    # Table is empty → populate inside a fresh transaction
    t0 = time.time()
    logger.info("Populating grid cache for %d grid sizes …", len(GRID_SIZES))
    async with engine.begin() as conn:
        total = await _populate(conn, truncate=False)
    logger.info("Grid cache ready — %d rows in %.1fs", total, time.time() - t0)


async def refresh_grid_cache(engine: AsyncEngine) -> None:
    """Full rebuild — call after weekly data imports."""
    t0 = time.time()
    logger.info("Refreshing grid cache …")
    for attempt in range(2):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(_DDL))
            break
        except Exception:
            if attempt == 0:
                import asyncio
                await asyncio.sleep(0.5)
            else:
                raise
    async with engine.begin() as conn:
        total = await _populate(conn, truncate=True)
    logger.info("Grid cache refreshed — %d rows in %.1fs", total, time.time() - t0)
