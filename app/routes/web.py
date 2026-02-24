"""Web UI routes — server-rendered Jinja2 templates with HTMX support."""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import License, Location, Frequency, RadioService
from app.config import get_settings
from app.utils import US_STATES, parse_emission_designator

router = APIRouter()
settings = get_settings()


def format_frequency(mhz: float | None) -> str:
    """Format frequency with appropriate units (kHz, MHz, or GHz).
    
    Avoids scientific notation and uses the most natural unit.
    """
    if mhz is None or mhz == 0:
        return "—"
    
    abs_mhz = abs(mhz)
    
    # GHz (>= 1000 MHz)
    if abs_mhz >= 1000:
        ghz = mhz / 1000
        if ghz >= 100:
            return f"{ghz:.0f} GHz"
        elif ghz >= 10:
            return f"{ghz:.1f} GHz"
        else:
            return f"{ghz:.2f} GHz"
    # MHz (1 to 999.999 MHz)
    elif abs_mhz >= 1:
        if abs_mhz >= 100:
            return f"{mhz:.1f} MHz"
        elif abs_mhz >= 10:
            return f"{mhz:.2f} MHz"
        else:
            return f"{mhz:.3f} MHz"
    # kHz (< 1 MHz)
    else:
        khz = mhz * 1000
        if abs(khz) >= 100:
            return f"{khz:.0f} kHz"
        elif abs(khz) >= 10:
            return f"{khz:.1f} kHz"
        else:
            return f"{khz:.2f} kHz"


def format_frequency_range(low_mhz: float, high_mhz: float) -> str:
    """Format a frequency range with consistent units across both endpoints."""
    if low_mhz == high_mhz:
        return format_frequency(low_mhz)

    max_abs = max(abs(low_mhz), abs(high_mhz))
    if max_abs >= 1000:
        return f"{low_mhz / 1000:.3f}–{high_mhz / 1000:.3f} GHz"
    if max_abs >= 1:
        return f"{low_mhz:.3f}–{high_mhz:.3f} MHz"
    return f"{low_mhz * 1000:.2f}–{high_mhz * 1000:.2f} kHz"


def _templates():
    from app.main import templates
    return templates


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main map view."""
    return _templates().TemplateResponse("index.html", {
        "request": request,
        "title": settings.app_title,
        "default_lat": settings.map_default_lat,
        "default_lng": settings.map_default_lng,
        "default_zoom": settings.map_default_zoom,
    })


@router.get("/state/{state_code}", response_class=HTMLResponse)
async def state_view(
    request: Request,
    state_code: str,
    db: AsyncSession = Depends(get_db),
):
    """List of counties in a state."""
    state_upper = state_code.upper()
    state_name = US_STATES.get(state_upper, state_upper)

    stmt = (
        select(
            Location.county,
            func.count(Location.id).label("count"),
        )
        .where(Location.state == state_upper)
        .where(Location.county.isnot(None))
        .where(Location.county != "")
        .group_by(Location.county)
        .order_by(Location.county)
    )
    result = await db.execute(stmt)
    counties = result.all()

    return _templates().TemplateResponse("state.html", {
        "request": request,
        "title": f"{state_name} — {settings.app_title}",
        "state_code": state_upper,
        "state_name": state_name,
        "counties": counties,
    })


@router.get("/county/{state}/{county}", response_class=HTMLResponse)
async def county_view(
    request: Request,
    state: str,
    county: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, le=200),
    sort: str = Query("callsign"),
    service: str | None = Query(None),
    status: str | None = Query("A"),
    db: AsyncSession = Depends(get_db),
):
    """List of licenses in a county."""
    from sqlalchemy.orm import selectinload
    from app.models import Frequency
    
    state_upper = state.upper()
    state_name = US_STATES.get(state_upper, state_upper)

    stmt = (
        select(License)
        .join(Location, Location.license_id == License.id)
        .where(Location.state == state_upper)
        .where(Location.county.ilike(county))
        .options(
            selectinload(License.locations).selectinload(Location.frequencies)
        )
        .distinct()
    )

    if service:
        stmt = stmt.where(License.radio_service == service.upper())
    
    if status:
        stmt = stmt.where(License.status == status.upper())

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0
    pages = (total + per_page - 1) // per_page

    # Sort
    if sort == "licensee":
        stmt = stmt.order_by(License.licensee_name)
    else:
        stmt = stmt.order_by(License.callsign)

    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    licenses = result.unique().scalars().all()
    
    # Process licenses to include frequency info
    license_data = []
    for lic in licenses:
        # Get all frequencies from all locations for this license
        frequencies = set()
        for location in lic.locations:
            for freq in location.frequencies:
                if freq.frequency_mhz is not None:
                    frequencies.add(freq.frequency_mhz)
        
        freq_list = sorted(frequencies)
        
        # Build a list of formatted individual frequencies (up to 3) plus a
        # count badge when there are more.
        freq_items: list[str] = []
        for f in freq_list[:3]:
            freq_items.append(format_frequency(f))
        
        license_data.append({
            "callsign": lic.callsign,
            "licensee_name": lic.licensee_name,
            "radio_service": lic.radio_service,
            "status": lic.status,
            "frequency_display": format_frequency(freq_list[0]) if freq_list else "—",
            "freq_items": freq_items,
            "freq_count": len(freq_list),
        })

    return _templates().TemplateResponse("county.html", {
        "request": request,
        "title": f"{county}, {state_name} — {settings.app_title}",
        "state_code": state_upper,
        "state_name": state_name,
        "county": county,
        "licenses": license_data,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "sort": sort,
        "service_filter": service,
        "status_filter": status,
        "US_STATES": US_STATES,
    })


@router.get("/license/{callsign}", response_class=HTMLResponse)
async def license_view(
    request: Request,
    callsign: str,
    db: AsyncSession = Depends(get_db),
):
    """Full license detail page."""
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
        return _templates().TemplateResponse("404.html", {
            "request": request,
            "title": "Not Found",
            "message": f"Callsign {callsign.upper()} not found.",
        }, status_code=404)

    # Decode emission designators
    for lic in licenses:
        for loc in lic.locations:
            for freq in loc.frequencies:
                if freq.emission_designator:
                    freq._parsed_emission = parse_emission_designator(freq.emission_designator)
                else:
                    freq._parsed_emission = {}

    return _templates().TemplateResponse("license.html", {
        "request": request,
        "title": f"{callsign.upper()} — {settings.app_title}",
        "licenses": licenses,
        "callsign": callsign.upper(),
        "US_STATES": US_STATES,
    })


@router.get("/frequency/{frequency}", response_class=HTMLResponse)
async def frequency_view(
    request: Request,
    frequency: float,
    tolerance: float = Query(0.0125),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, le=200),
    status: str | None = Query("A"),
    db: AsyncSession = Depends(get_db),
):
    """Search by frequency page."""
    stmt = (
        select(
            Frequency.frequency_mhz,
            Frequency.emission_designator,
            Frequency.power,
            Frequency.unit_of_power,
            Frequency.station_class,
            Location.state,
            Location.county,
            Location.latitude,
            Location.longitude,
            License.callsign,
            License.licensee_name,
            License.radio_service,
            License.status,
        )
        .join(Location, Frequency.location_id == Location.id)
        .join(License, Location.license_id == License.id)
        .where(Frequency.frequency_mhz.between(frequency - tolerance, frequency + tolerance))
    )
    
    if status:
        stmt = stmt.where(License.status == status.upper())
    
    stmt = stmt.order_by(Frequency.frequency_mhz)

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0
    pages = (total + per_page - 1) // per_page

    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    frequencies_raw = result.all()
    
    # Format frequencies with appropriate units
    frequencies = []
    for row in frequencies_raw:
        freq_dict = {
            "frequency_mhz": row.frequency_mhz,
            "frequency_display": format_frequency(row.frequency_mhz),
            "emission_designator": row.emission_designator,
            "power": row.power,
            "unit_of_power": row.unit_of_power,
            "station_class": row.station_class,
            "state": row.state,
            "county": row.county,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "callsign": row.callsign,
            "licensee_name": row.licensee_name,
            "radio_service": row.radio_service,
            "status": row.status,
        }
        frequencies.append(freq_dict)

    return _templates().TemplateResponse("frequency.html", {
        "request": request,
        "title": f"{format_frequency(frequency)} — {settings.app_title}",
        "frequency": frequency,
        "frequency_display": format_frequency(frequency),
        "tolerance": tolerance,
        "frequencies": frequencies,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "status_filter": status,
        "US_STATES": US_STATES,
    })


@router.get("/browse", response_class=HTMLResponse)
async def browse(request: Request, db: AsyncSession = Depends(get_db)):
    """Browse states."""
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
    states = result.all()

    state_data = []
    for row in states:
        state_data.append({
            "code": row.state,
            "name": US_STATES.get(row.state, row.state),
            "count": row.count,
        })


    return _templates().TemplateResponse("browse.html", {
        "request": request,
        "title": f"Browse States — {settings.app_title}",
        "states": state_data,
    })


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Search page."""
    results = []
    total = 0

    if q and len(q) >= 2:
        stmt = (
            select(
                License.callsign,
                License.licensee_name,
                License.radio_service,
                License.status,
            )
            .where(
                License.callsign.ilike(f"%{q}%")
                | License.licensee_name.ilike(f"%{q}%")
            )
            .distinct()
            .order_by(License.callsign)
            .limit(100)
        )
        result = await db.execute(stmt)
        results = result.all()
        total = len(results)

    return _templates().TemplateResponse("search.html", {
        "request": request,
        "title": f"Search — {settings.app_title}",
        "query": q or "",
        "results": results,
        "total": total,
    })


# HTMX partial endpoint for map filter results
@router.get("/partials/filter-results", response_class=HTMLResponse)
async def filter_results_partial(
    request: Request,
    state: str | None = Query(None),
    county: str | None = Query(None),
    frequency_min: float | None = Query(None),
    frequency_max: float | None = Query(None),
    service: str | None = Query(None),
    callsign: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, le=100),
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial for dynamic filter results sidebar."""
    stmt = (
        select(
            License.callsign,
            License.licensee_name,
            License.radio_service,
            Location.state,
            Location.county,
            Location.latitude,
            Location.longitude,
        )
        .join(Location, Location.license_id == License.id)
    )

    if state:
        stmt = stmt.where(Location.state == state.upper())
    if county:
        stmt = stmt.where(Location.county.ilike(f"%{county}%"))
    if service:
        stmt = stmt.where(License.radio_service == service.upper())
    if callsign:
        stmt = stmt.where(License.callsign.ilike(f"%{callsign}%"))

    if frequency_min is not None or frequency_max is not None:
        stmt = stmt.join(Frequency, Frequency.location_id == Location.id)
        if frequency_min is not None:
            stmt = stmt.where(Frequency.frequency_mhz >= frequency_min)
        if frequency_max is not None:
            stmt = stmt.where(Frequency.frequency_mhz <= frequency_max)

    stmt = stmt.distinct().order_by(License.callsign)

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    items = result.all()

    return _templates().TemplateResponse("partials/filter_results.html", {
        "request": request,
        "items": items,
        "total": total,
        "page": page,
        "pages": (total + per_page - 1) // per_page,
    })
