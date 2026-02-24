"""JSON API endpoints for map data and search."""

import hashlib, json, math, time
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.database import get_db
from app.grid_cache import GRID_SIZES
from app.models import License, Location, Frequency, RadioService
from app.config import get_settings
from app.utils import parse_emission_designator, US_STATES
from app.scheduler import get_scheduler

router = APIRouter()
settings = get_settings()

# ---------- lightweight in-memory cache for wide-zoom map queries ----------
_map_cache: dict[str, tuple[float, dict]] = {}   # key → (expiry_ts, response)
_MAP_CACHE_TTL = 60   # seconds


def _cache_key(params: dict) -> str:
    raw = json.dumps(params, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    entry = _map_cache.get(key)
    if entry and entry[0] > time.time():
        return entry[1]
    _map_cache.pop(key, None)
    return None


def _cache_put(key: str, value: dict) -> None:
    # Evict stale entries when cache grows
    if len(_map_cache) > 200:
        now = time.time()
        stale = [k for k, (exp, _) in _map_cache.items() if exp <= now]
        for k in stale:
            del _map_cache[k]
    _map_cache[key] = (time.time() + _MAP_CACHE_TTL, value)


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint for monitoring."""
    try:
        # Check database connection
        await db.execute(text("SELECT 1"))
        
        # Get scheduler status
        scheduler = get_scheduler()
        scheduler_status = scheduler.get_status()
        
        return {
            "status": "healthy",
            "database": "connected",
            "scheduler": scheduler_status,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
        }, 503


@router.get("/admin/scheduler")
async def get_scheduler_status():
    """Get scheduler status and next update time."""
    scheduler = get_scheduler()
    return scheduler.get_status()


@router.get("/map")
async def get_map_data(
    min_lat: float = Query(-90),
    max_lat: float = Query(90),
    min_lng: float = Query(-180),
    max_lng: float = Query(180),
    frequency_min: float | None = Query(None),
    frequency_max: float | None = Query(None),
    state: str | None = Query(None),
    service: str | None = Query(None),
    status: str | None = Query("A"),
    callsign: str | None = Query(None),
    limit: int = Query(5000, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Get license locations within a bounding box for map display.

    Uses arithmetic grid-snapping (FLOOR) for server-side clustering at
    wide zoom levels.  Cluster mode uses EXISTS semi-join to avoid the
    cost of a full JOIN.  Results are cached briefly for wide views.
    """

    # ---- snap bbox to 0.01° while always expanding the viewport -------
    # Using round() can collapse very small high-zoom bounds to the same
    # value, causing detail markers to disappear. Floor min / ceil max
    # preserves cache coalescing while guaranteeing containment.
    dp = 2
    scale = 10**dp
    r_min_lat = math.floor(min_lat * scale) / scale
    r_max_lat = math.ceil(max_lat * scale) / scale
    r_min_lng = math.floor(min_lng * scale) / scale
    r_max_lng = math.ceil(max_lng * scale) / scale

    # ---- check cache -------------------------------------------------
    cache_params = {
        "bb": (r_min_lat, r_max_lat, r_min_lng, r_max_lng),
        "fmin": frequency_min, "fmax": frequency_max,
        "st": state, "svc": service, "status": status,
        "cs": callsign,
    }
    ckey = _cache_key(cache_params)
    cached = _cache_get(ckey)
    if cached is not None:
        return cached

    # --- choose grid resolution from bounding-box span ----------------
    lat_span = r_max_lat - r_min_lat
    lng_span = r_max_lng - r_min_lng
    span = max(lat_span, lng_span)

    if span > 50:
        grid_size = 2.5
    elif span > 20:
        grid_size = 1.5
    elif span > 8:
        grid_size = 0.6
    elif span > 3:
        grid_size = 0.25
    elif span > 1:
        grid_size = 0.1
    elif span > 0.4:
        grid_size = 0.04
    elif span > 0.08:
        grid_size = 0.008
    else:
        grid_size = 0.0  # individual-point mode at high zoom

    # ---- base filter — plain lat/lng range for fast btree lookups ----
    base_filter = [
        Location.latitude.isnot(None),
        Location.longitude.isnot(None),
        Location.latitude >= r_min_lat,
        Location.latitude <= r_max_lat,
        Location.longitude >= r_min_lng,
        Location.longitude <= r_max_lng,
    ]

    if state:
        base_filter.append(Location.state == state.upper())

    # -- license-side filters via EXISTS (semi-join, no row duplication)
    license_conds = []
    if service:
        license_conds.append(License.radio_service == service.upper())
    if status:
        license_conds.append(License.status == status.upper())
    if callsign:
        license_conds.append(License.callsign.ilike(f"%{callsign}%"))

    if license_conds:
        base_filter.append(
            exists(
                select(License.id).where(
                    License.id == Location.license_id,
                    *license_conds,
                )
            )
        )

    # -- frequency filters via EXISTS
    freq_conds = []
    if frequency_min is not None:
        freq_conds.append(Frequency.frequency_mhz >= frequency_min)
    if frequency_max is not None:
        freq_conds.append(Frequency.frequency_mhz <= frequency_max)

    if freq_conds:
        base_filter.append(
            exists(
                select(Frequency.id).where(
                    Frequency.location_id == Location.id,
                    *freq_conds,
                )
            )
        )

    # ---- cluster mode (grid_size > 0) --------------------------------
    if grid_size > 0:

        # -- try pre-computed grid cache (status='A', no extra filters) --
        use_cache = (
            (not status or status.upper() == "A")
            and not service
            and not callsign
            and not frequency_min
            and not frequency_max
            and not state
        )

        if use_cache:
            try:
                min_glat = int(r_min_lat // grid_size)
                max_glat = int(r_max_lat // grid_size)
                min_glng = int(r_min_lng // grid_size)
                max_glng = int(r_max_lng // grid_size)

                cache_rows = (
                    await db.execute(
                        text(
                            "SELECT avg_lat, avg_lng, cnt, state, county "
                            "FROM   map_grid_cache "
                            "WHERE  grid_size = :gs "
                            "  AND  grid_lat BETWEEN :a AND :b "
                            "  AND  grid_lng BETWEEN :c AND :d "
                            "ORDER  BY cnt DESC "
                            "LIMIT  :lim"
                        ),
                        {
                            "gs": grid_size,
                            "a": min_glat, "b": max_glat,
                            "c": min_glng, "d": max_glng,
                            "lim": limit,
                        },
                    )
                ).all()

                if cache_rows:
                    features = []
                    total_count = 0
                    for r in cache_rows:
                        cnt = int(r.cnt)
                        total_count += cnt
                        features.append({
                            "lat": round(float(r.avg_lat), 6),
                            "lng": round(float(r.avg_lng), 6),
                            "cnt": cnt,
                            "state": r.state or "",
                            "county": r.county or "",
                        })
                    response = {
                        "count": len(features),
                        "total_count": total_count,
                        "mode": "cluster",
                        "grid": grid_size,
                        "features": features,
                    }
                    _cache_put(ckey, response)
                    return response
            except Exception:
                pass  # table missing / empty — fall through to live query

        # -- live cluster query (fallback / filtered requests) ----------
        grid_lat = func.floor(Location.latitude / grid_size)
        grid_lng = func.floor(Location.longitude / grid_size)

        cluster_stmt = (
            select(
                func.avg(Location.latitude).label("lat"),
                func.avg(Location.longitude).label("lng"),
                func.count(Location.id).label("cnt"),
                func.min(Location.state).label("state"),
                func.min(Location.county).label("county"),
            )
            .where(*base_filter)
            .group_by(grid_lat, grid_lng)
            .order_by(func.count(Location.id).desc())
            .limit(limit)
        )

        result = await db.execute(cluster_stmt)
        rows = result.all()

        features = []
        total_count = 0
        for r in rows:
            cnt = int(r.cnt)
            total_count += cnt
            features.append({
                "lat": round(float(r.lat), 6),
                "lng": round(float(r.lng), 6),
                "cnt": cnt,
                "state": r.state or "",
                "county": r.county or "",
            })

        response = {
            "count": len(features),
            "total_count": total_count,
            "mode": "cluster",
            "grid": grid_size,
            "features": features,
        }
        _cache_put(ckey, response)
        return response

    # ---- detail mode (fully zoomed in) — needs full JOIN for fields --
    detail_filter = [
        Location.latitude.isnot(None),
        Location.longitude.isnot(None),
        Location.latitude >= r_min_lat,
        Location.latitude <= r_max_lat,
        Location.longitude >= r_min_lng,
        Location.longitude <= r_max_lng,
    ]
    if state:
        detail_filter.append(Location.state == state.upper())
    if service:
        detail_filter.append(License.radio_service == service.upper())
    if status:
        detail_filter.append(License.status == status.upper())
    if callsign:
        detail_filter.append(License.callsign.ilike(f"%{callsign}%"))

    detail_stmt = (
        select(
            Location.id,
            Location.latitude,
            Location.longitude,
            Location.state,
            Location.county,
            License.callsign,
            License.licensee_name,
            License.radio_service,
        )
        .join(License, Location.license_id == License.id)
        .where(*detail_filter)
        .order_by(Location.id)
        .limit(limit)
    )
    if freq_conds:
        detail_stmt = detail_stmt.join(
            Frequency, Frequency.location_id == Location.id
        ).where(*freq_conds)

    result = await db.execute(detail_stmt)
    rows = result.all()

    features = []
    for row in rows:
        features.append({
            "lat": row.latitude,
            "lng": row.longitude,
            "cs": row.callsign,
            "lic": row.licensee_name or "",
            "svc": row.radio_service or "",
            "state": row.state or "",
            "county": row.county or "",
        })

    return {
        "count": len(features),
        "total_count": len(features),
        "mode": "detail",
        "grid": 0,
        "features": features,
    }


@router.get("/license/{callsign}")
async def get_license(
    callsign: str,
    db: AsyncSession = Depends(get_db),
):
    """Get full license details by callsign."""
    stmt = (
        select(License)
        .where(License.callsign == callsign.upper())
        .options(
            selectinload(License.locations).selectinload(Location.frequencies),
            selectinload(License.service),
        )
    )
    result = await db.execute(stmt)
    licenses = result.scalars().all()

    if not licenses:
        return {"error": "License not found"}

    output = []
    for lic in licenses:
        lic_data = {
            "callsign": lic.callsign,
            "licensee_name": lic.licensee_name,
            "radio_service": lic.radio_service,
            "service_description": lic.service.description if lic.service else None,
            "status": lic.status,
            "grant_date": str(lic.grant_date) if lic.grant_date else None,
            "expiration_date": str(lic.expiration_date) if lic.expiration_date else None,
            "frn": lic.frn,
            "locations": [],
        }
        for loc in lic.locations:
            loc_data = {
                "latitude": loc.latitude,
                "longitude": loc.longitude,
                "state": loc.state,
                "county": loc.county,
                "ground_elevation": loc.ground_elevation,
                "frequencies": [],
            }
            for freq in loc.frequencies:
                loc_data["frequencies"].append({
                    "frequency_mhz": freq.frequency_mhz,
                    "frequency_upper_mhz": freq.frequency_upper_mhz,
                    "emission_designator": freq.emission_designator,
                    "power": freq.power,
                    "unit_of_power": freq.unit_of_power,
                    "station_class": freq.station_class,
                })
            lic_data["locations"].append(loc_data)
        output.append(lic_data)

    return output[0] if len(output) == 1 else output


@router.get("/search")
async def search_licenses(
    q: str | None = Query(None),
    callsign: str | None = Query(None),
    frequency_min: float | None = Query(None),
    frequency_max: float | None = Query(None),
    state: str | None = Query(None),
    county: str | None = Query(None),
    service: str | None = Query(None),
    emission: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, le=200),
    sort: str = Query("callsign"),
    db: AsyncSession = Depends(get_db),
):
    """Search and filter licenses."""
    stmt = (
        select(
            License.callsign,
            License.licensee_name,
            License.radio_service,
            License.status,
            Location.state,
            Location.county,
            Location.latitude,
            Location.longitude,
        )
        .join(Location, Location.license_id == License.id)
    )

    # Filters
    if q:
        stmt = stmt.where(
            License.callsign.ilike(f"%{q}%")
            | License.licensee_name.ilike(f"%{q}%")
        )
    if callsign:
        stmt = stmt.where(License.callsign.ilike(f"%{callsign}%"))
    if state:
        stmt = stmt.where(Location.state == state.upper())
    if county:
        stmt = stmt.where(Location.county.ilike(f"%{county}%"))
    if service:
        stmt = stmt.where(License.radio_service == service.upper())

    # Frequency filter
    if frequency_min is not None or frequency_max is not None or emission:
        stmt = stmt.join(Frequency, Frequency.location_id == Location.id)
        if frequency_min is not None:
            stmt = stmt.where(Frequency.frequency_mhz >= frequency_min)
        if frequency_max is not None:
            stmt = stmt.where(Frequency.frequency_mhz <= frequency_max)
        if emission:
            stmt = stmt.where(Frequency.emission_designator.ilike(f"%{emission}%"))

    stmt = stmt.distinct()

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Sorting
    if sort == "callsign":
        stmt = stmt.order_by(License.callsign)
    elif sort == "licensee":
        stmt = stmt.order_by(License.licensee_name)
    elif sort == "state":
        stmt = stmt.order_by(Location.state, Location.county)
    else:
        stmt = stmt.order_by(License.callsign)

    # Pagination
    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for row in rows:
        items.append({
            "callsign": row.callsign,
            "licensee_name": row.licensee_name or "Unlicensed",
            "radio_service": row.radio_service or "Unknown",
            "status": row.status or "Unknown",
            "state": row.state or "Unknown",
            "county": row.county or "Unknown County",
            "latitude": row.latitude,
            "longitude": row.longitude,
        })

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "items": items,
    }


@router.get("/frequency/{mhz}")
async def search_by_frequency(
    mhz: float,
    tolerance: float = Query(0.0125, description="Tolerance in MHz (default ±12.5 kHz)"),
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Search for licenses by specific frequency."""
    stmt = (
        select(
            Frequency.frequency_mhz,
            Frequency.emission_designator,
            Frequency.power,
            Frequency.unit_of_power,
            Frequency.station_class,
            Location.latitude,
            Location.longitude,
            Location.state,
            Location.county,
            License.callsign,
            License.licensee_name,
            License.radio_service,
        )
        .join(Location, Frequency.location_id == Location.id)
        .join(License, Location.license_id == License.id)
        .where(Frequency.frequency_mhz.between(mhz - tolerance, mhz + tolerance))
        .order_by(Frequency.frequency_mhz)
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for row in rows:
        items.append({
            "frequency_mhz": row.frequency_mhz,
            "emission_designator": row.emission_designator,
            "power": row.power,
            "unit_of_power": row.unit_of_power,
            "station_class": row.station_class,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "state": row.state,
            "county": row.county,
            "callsign": row.callsign,
            "licensee_name": row.licensee_name,
            "radio_service": row.radio_service,
        })

    return {"frequency": mhz, "tolerance": tolerance, "count": len(items), "items": items}


@router.get("/states")
async def list_states(db: AsyncSession = Depends(get_db)):
    """List all valid states with license counts."""
    valid_states = list(US_STATES.keys())

    stmt = (
        select(
            Location.state,
            func.count(Location.id).label("count"),
        )
        .where(Location.state.isnot(None))
        .where(Location.state != "")
        .where(Location.state.in_(valid_states))
        .group_by(Location.state)
        .order_by(Location.state)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [{"state": row.state, "name": US_STATES.get(row.state, row.state), "count": row.count} for row in rows]


@router.get("/counties/{state}")
async def list_counties(state: str, db: AsyncSession = Depends(get_db)):
    """List counties in a state with license counts."""
    stmt = (
        select(
            Location.county,
            func.count(Location.id).label("count"),
        )
        .where(Location.state == state.upper())
        .where(Location.county.isnot(None))
        .where(Location.county != "")
        .group_by(Location.county)
        .order_by(Location.county)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [{"county": row.county, "count": row.count} for row in rows]


@router.get("/services")
async def list_services(db: AsyncSession = Depends(get_db)):
    """List all radio service codes with descriptions."""
    stmt = select(RadioService).order_by(RadioService.code)
    result = await db.execute(stmt)
    services = result.scalars().all()
    return [{"code": s.code, "description": s.description} for s in services]


@router.get("/emission/{designator}")
async def decode_emission(designator: str):
    """Decode an emission designator."""
    return parse_emission_designator(designator)
