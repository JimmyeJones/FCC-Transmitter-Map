"""Normalize raw FCC parsed data into database-ready structures."""

from datetime import date, datetime
from functools import lru_cache

from rich.console import Console

from fcc_importer.parser import (
    ParsedLicenseBundle,
    RawEntity,
    RawFrequency,
    RawLicense,
    RawLocation,
)
from app.utils import parse_emission_designator

console = Console()


@lru_cache(maxsize=4096)
def _parse_date_cached(date_str: str) -> date | None:
    """Cached parser for FCC date strings (MM/DD/YYYY)."""
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date(date_str: str) -> date | None:
    """Parse FCC date string (MM/DD/YYYY) into a date object."""
    if not date_str or not date_str.strip():
        return None
    return _parse_date_cached(date_str.strip())


def _parse_float(value: str) -> float | None:
    """Safely parse a float."""
    if not value or not value.strip():
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_int(value: str) -> int | None:
    """Safely parse an integer."""
    if not value or not value.strip():
        return None
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return None


def _dms_to_decimal(degrees: str, minutes: str, seconds: str, direction: str) -> float | None:
    """Convert DMS (degrees/minutes/seconds) to decimal degrees."""
    d = _parse_int(degrees)
    m = _parse_int(minutes)
    s = _parse_float(seconds)
    if d is None:
        return None
    m = m or 0
    s = s or 0.0
    decimal = d + m / 60.0 + s / 3600.0
    if direction and direction.strip().upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def _get_licensee_name(entity: RawEntity | None) -> str | None:
    """Build licensee name from entity record."""
    if not entity:
        return None
    name = entity.entity_name.strip()
    if not name:
        parts = [entity.first_name.strip(), entity.last_name.strip()]
        name = " ".join(p for p in parts if p)
    return name or None


def _compute_frequency_mhz(freq_str: str) -> float | None:
    """Convert FCC frequency value to MHz.

    FCC stores frequencies in MHz in the FR file.
    """
    val = _parse_float(freq_str)
    if val is None:
        return None
    # FCC frequency_assigned is in MHz
    return val


def normalize_license(
    raw: RawLicense,
    entity: RawEntity | None = None,
) -> dict:
    """Normalize a license record into a dict suitable for DB insertion."""
    usi = _parse_int(raw.unique_system_identifier)
    return {
        "unique_system_identifier": usi,
        "callsign": raw.callsign.strip(),
        "licensee_name": _get_licensee_name(entity),
        "radio_service": raw.radio_service_code.strip() or None,
        "status": raw.license_status.strip() or None,
        "grant_date": _parse_date(raw.grant_date),
        "expiration_date": _parse_date(raw.expired_date),
        "effective_date": _parse_date(raw.effective_date),
        "frn": (entity.frn.strip() if entity else None) or None,
    }


def normalize_location(raw: RawLocation) -> dict:
    """Normalize a location record."""
    lat = _dms_to_decimal(
        raw.lat_degrees, raw.lat_minutes, raw.lat_seconds, raw.lat_direction
    )
    lng = _dms_to_decimal(
        raw.long_degrees, raw.long_minutes, raw.long_seconds, raw.long_direction
    )
    return {
        "location_number": _parse_int(raw.location_number),
        "latitude": lat,
        "longitude": lng,
        "county": raw.county.strip() or None,
        "state": raw.state.strip() or None,
        "radius_km": _parse_float(raw.radius_of_operation),
        "ground_elevation": _parse_float(raw.ground_elevation),
        "lat_degrees": _parse_int(raw.lat_degrees),
        "lat_minutes": _parse_int(raw.lat_minutes),
        "lat_seconds": _parse_float(raw.lat_seconds),
        "lat_direction": raw.lat_direction.strip() or None,
        "long_degrees": _parse_int(raw.long_degrees),
        "long_minutes": _parse_int(raw.long_minutes),
        "long_seconds": _parse_float(raw.long_seconds),
        "long_direction": raw.long_direction.strip() or None,
        "geom_wkt": f"SRID=4326;POINT({lng} {lat})" if lat is not None and lng is not None else None,
    }


def normalize_frequency(raw: RawFrequency) -> dict:
    """Normalize a frequency record."""
    emission = raw.emission_designator.strip()
    parsed_emission = parse_emission_designator(emission) if emission else {}

    return {
        "location_number": _parse_int(raw.location_number),
        "frequency_mhz": _compute_frequency_mhz(raw.frequency_assigned),
        "frequency_upper_mhz": _compute_frequency_mhz(raw.frequency_upper_band),
        "emission_designator": emission or None,
        "power": _parse_float(raw.power),
        "station_class": raw.station_class_code.strip() or None,
        "unit_of_power": raw.unit_of_power.strip() or None,
        "emission_bandwidth": parsed_emission.get("bandwidth"),
        "emission_modulation": parsed_emission.get("modulation"),
        "emission_signal_type": parsed_emission.get("signal_type"),
    }


def build_bundles(parsed_data: dict[str, list]) -> dict[str, ParsedLicenseBundle]:
    """Group all parsed records by unique_system_identifier into bundles."""
    bundles: dict[str, ParsedLicenseBundle] = {}

    # Index HD records
    for hd in parsed_data.get("HD", []):
        usi = hd.unique_system_identifier.strip()
        if not usi:
            continue
        bundle = bundles.setdefault(usi, ParsedLicenseBundle())
        bundle.license = hd

    # Attach EN records
    for en in parsed_data.get("EN", []):
        usi = en.unique_system_identifier.strip()
        if usi in bundles:
            bundles[usi].entity = en

    # Attach LO records
    for lo in parsed_data.get("LO", []):
        usi = lo.unique_system_identifier.strip()
        if usi in bundles:
            bundles[usi].locations.append(lo)

    # Attach FR records
    for fr in parsed_data.get("FR", []):
        usi = fr.unique_system_identifier.strip()
        if usi in bundles:
            bundles[usi].frequencies.append(fr)

    return bundles
