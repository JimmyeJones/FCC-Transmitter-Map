"""Parse FCC ULS pipe-delimited data files."""

import csv
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

from rich.console import Console

console = Console()

# FCC data files can have very large fields — raise the CSV limit
csv.field_size_limit(sys.maxsize)


@dataclass
class RawLicense:
    """Parsed license header (HD) record."""
    unique_system_identifier: str = ""
    callsign: str = ""
    license_status: str = ""
    radio_service_code: str = ""
    grant_date: str = ""
    expired_date: str = ""
    effective_date: str = ""
    licensee_name: str = ""  # Combined from EN record
    frn: str = ""


@dataclass
class RawEntity:
    """Parsed entity (EN) record — licensee info."""
    unique_system_identifier: str = ""
    entity_name: str = ""
    first_name: str = ""
    last_name: str = ""
    frn: str = ""
    state: str = ""


@dataclass
class RawLocation:
    """Parsed location (LO) record."""
    unique_system_identifier: str = ""
    location_number: str = ""
    lat_degrees: str = ""
    lat_minutes: str = ""
    lat_seconds: str = ""
    lat_direction: str = ""
    long_degrees: str = ""
    long_minutes: str = ""
    long_seconds: str = ""
    long_direction: str = ""
    ground_elevation: str = ""
    county: str = ""
    state: str = ""
    radius_of_operation: str = ""


@dataclass
class RawFrequency:
    """Parsed frequency (FR) record."""
    unique_system_identifier: str = ""
    location_number: str = ""
    frequency_assigned: str = ""
    frequency_upper_band: str = ""
    emission_designator: str = ""
    power: str = ""
    station_class_code: str = ""
    unit_of_power: str = ""


@dataclass
class ParsedLicenseBundle:
    """All data associated with one ULS system identifier."""
    license: RawLicense = field(default_factory=RawLicense)
    entity: RawEntity | None = None
    locations: list[RawLocation] = field(default_factory=list)
    frequencies: list[RawFrequency] = field(default_factory=list)


# FCC ULS file type -> record type prefix
FILE_RECORD_TYPES = {
    "HD.dat": "HD",  # License header
    "EN.dat": "EN",  # Entity (licensee)
    "LO.dat": "LO",  # Location
    "FR.dat": "FR",  # Frequency
}


def _extract_zip(zip_path: Path, extract_to: Path) -> Path:
    """Extract a ZIP file and return the extraction directory."""
    extract_dir = extract_to / zip_path.stem
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    return extract_dir


def _parse_pipe_file(filepath: Path) -> Generator[list[str], None, None]:
    """Parse a pipe-delimited FCC file, yielding rows."""
    if not filepath.exists():
        return
    with open(filepath, "r", encoding="latin-1", errors="replace") as f:
        reader = csv.reader(f, delimiter="|")
        for row in reader:
            yield row


def parse_hd_row(row: list[str]) -> RawLicense:
    """Parse an HD (Header/License) record.

    FCC ULS HD record layout (0-indexed):
      0: Record Type        1: Unique System Identifier
      2: ULS File Number    3: EBF Number
      4: Call Sign          5: License Status
      6: Radio Service Code 7: Grant Date
      8: Expired Date       9: Cancellation Date
      ...
      42: Effective Date
    """
    return RawLicense(
        unique_system_identifier=row[1] if len(row) > 1 else "",
        callsign=row[4] if len(row) > 4 else "",
        license_status=row[5] if len(row) > 5 else "",
        radio_service_code=row[6] if len(row) > 6 else "",
        grant_date=row[7] if len(row) > 7 else "",
        expired_date=row[8] if len(row) > 8 else "",
        effective_date=row[42] if len(row) > 42 else "",
    )


def parse_en_row(row: list[str]) -> RawEntity:
    """Parse an EN (Entity) record.

    FCC ULS EN record layout (0-indexed):
      0: Record Type        1: Unique System Identifier
      2: ULS File Number    3: EBF Number
      4: Call Sign          5: Entity Type
      6: Licensee ID        7: Entity Name
      8: First Name         9: MI
      10: Last Name         11: Suffix
      12: Phone             13: Fax
      14: Email             15: Street Address
      16: City              17: State
      18: Zip Code          ...
      22: FRN
    """
    return RawEntity(
        unique_system_identifier=row[1] if len(row) > 1 else "",
        entity_name=row[7] if len(row) > 7 else "",
        first_name=row[8] if len(row) > 8 else "",
        last_name=row[10] if len(row) > 10 else "",
        frn=row[22] if len(row) > 22 else "",
        state=row[17] if len(row) > 17 else "",
    )


def parse_lo_row(row: list[str]) -> RawLocation:
    """Parse an LO (Location) record.

    FCC ULS LO record layout (0-indexed):
      0: Record Type        1: Unique System Identifier
      2: ULS File Number    3: EBF Number
      4: Call Sign          5: Location Action Performed
      6: Location Type Code 7: Location Class Code
      8: Location Number    9: Site Status
      10: Corresponding Fixed Location
      11: Location Address  12: Location City
      13: Location County   14: Location State
      15: Radius of Operation  16: Area of Operation Code
      17: Clearance Indicator  18: Ground Elevation
      19: Lat Degrees       20: Lat Minutes
      21: Lat Seconds       22: Lat Direction
      23: Long Degrees      24: Long Minutes
      25: Long Seconds      26: Long Direction
    """
    return RawLocation(
        unique_system_identifier=row[1] if len(row) > 1 else "",
        location_number=row[8] if len(row) > 8 else "",
        lat_degrees=row[19] if len(row) > 19 else "",
        lat_minutes=row[20] if len(row) > 20 else "",
        lat_seconds=row[21] if len(row) > 21 else "",
        lat_direction=row[22] if len(row) > 22 else "",
        long_degrees=row[23] if len(row) > 23 else "",
        long_minutes=row[24] if len(row) > 24 else "",
        long_seconds=row[25] if len(row) > 25 else "",
        long_direction=row[26] if len(row) > 26 else "",
        ground_elevation=row[18] if len(row) > 18 else "",
        county=row[13] if len(row) > 13 else "",
        state=row[14] if len(row) > 14 else "",
        radius_of_operation=row[15] if len(row) > 15 else "",
    )


def parse_fr_row(row: list[str]) -> RawFrequency:
    """Parse an FR (Frequency) record.

    FCC ULS FR record layout (0-indexed):
      0: Record Type         1: Unique System Identifier
      2: ULS File Number     3: EBF Number
      4: Call Sign           5: Frequency Action Performed
      6: Location Number     7: Antenna Number
      8: Class Station Code  9: Op Altitude Code
      10: Frequency Assigned 11: Frequency Upper Band
      12: Frequency Carrier  13: Time Begin Operations
      14: Time End Operations 15: Power Output
      16: Power ERP          17: Tolerance
      18: Frequency Text     19: Status Code
      20: Status Date        21: EIRP
      22: Transmitter Make   23: Transmitter Model
      24: Auto Progressive Number  25: Count
      26: Emission Designator
    """
    return RawFrequency(
        unique_system_identifier=row[1] if len(row) > 1 else "",
        location_number=row[6] if len(row) > 6 else "",
        frequency_assigned=row[10] if len(row) > 10 else "",
        frequency_upper_band=row[11] if len(row) > 11 else "",
        emission_designator=row[26] if len(row) > 26 else "",
        power=row[15] if len(row) > 15 else "",
        station_class_code=row[8] if len(row) > 8 else "",
        unit_of_power="",
    )


def parse_zip_file(zip_path: Path) -> dict[str, list]:
    """Extract and parse all records from a FCC ULS ZIP file.

    Returns a dict keyed by record type with lists of parsed records.
    """
    extract_dir = _extract_zip(zip_path, zip_path.parent / "_extracted")
    console.print(f"[cyan]Parsing[/] {zip_path.name}")

    results: dict[str, list] = {
        "HD": [],
        "EN": [],
        "LO": [],
        "FR": [],
    }

    parsers = {
        "HD": parse_hd_row,
        "EN": parse_en_row,
        "LO": parse_lo_row,
        "FR": parse_fr_row,
    }

    for filename, record_type in FILE_RECORD_TYPES.items():
        filepath = extract_dir / filename
        if not filepath.exists():
            # Try uppercase
            filepath = extract_dir / filename.upper()
        if not filepath.exists():
            console.print(f"  [yellow]Missing {filename}[/]")
            continue

        count = 0
        parser = parsers[record_type]
        for row in _parse_pipe_file(filepath):
            if len(row) < 2:
                continue
            try:
                parsed = parser(row)
                results[record_type].append(parsed)
                count += 1
            except (IndexError, ValueError) as e:
                continue  # Skip malformed rows

        console.print(f"  [green]{record_type}:[/] {count:,} records")

    return results
