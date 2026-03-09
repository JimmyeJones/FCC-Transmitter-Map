"""Load normalized FCC data into PostgreSQL."""

import asyncio
from pathlib import Path

from sqlalchemy import text, insert
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn

from app.config import get_settings
from app.models import License, Location, Frequency
from app.utils import parse_emission_designator
from fcc_importer.parser import parse_zip_file
from fcc_importer.normalizer import (
    build_bundles,
    normalize_license,
    normalize_location,
    normalize_frequency,
)

# ---------------------------------------------------------------------------
# Index management helpers
# ---------------------------------------------------------------------------

# Non-PK indexes that are expensive to maintain during bulk inserts.
# These are dropped before a full import and recreated afterwards.
_BULK_INDEXES: list[str] = [
    # licenses
    "CREATE INDEX IF NOT EXISTS ix_licenses_callsign         ON licenses (callsign)",
    "CREATE INDEX IF NOT EXISTS ix_licenses_radio_service    ON licenses (radio_service)",
    "CREATE INDEX IF NOT EXISTS ix_licenses_callsign_status  ON licenses (callsign, status)",
    # locations
    "CREATE INDEX IF NOT EXISTS ix_locations_license_id      ON locations (license_id)",
    "CREATE INDEX IF NOT EXISTS ix_locations_county          ON locations (county)",
    "CREATE INDEX IF NOT EXISTS ix_locations_state           ON locations (state)",
    "CREATE INDEX IF NOT EXISTS ix_locations_state_county    ON locations (state, county)",
    # locations GIST — most expensive to keep live during inserts
    "CREATE INDEX IF NOT EXISTS ix_locations_geom            ON locations USING gist (geom)",
    # frequencies
    "CREATE INDEX IF NOT EXISTS ix_frequencies_location_id   ON frequencies (location_id)",
    "CREATE INDEX IF NOT EXISTS ix_frequencies_frequency_mhz ON frequencies (frequency_mhz)",
    "CREATE INDEX IF NOT EXISTS ix_frequencies_freq_range    ON frequencies (frequency_mhz, frequency_upper_mhz)",
]

_BULK_INDEX_NAMES: list[str] = [
    sql.split("IF NOT EXISTS")[1].strip().split()[0]
    for sql in _BULK_INDEXES
]


async def _drop_import_indexes(engine) -> None:
    """Drop non-PK indexes so bulk inserts are not slowed by index maintenance."""
    console.print("[yellow]Dropping indexes for bulk import...[/]")
    async with engine.connect() as conn:
        for name in _BULK_INDEX_NAMES:
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        await conn.commit()
    console.print(f"[green]Dropped {len(_BULK_INDEX_NAMES)} indexes[/]")


async def _recreate_import_indexes(engine) -> None:
    """Recreate indexes that were dropped before the bulk import."""
    console.print("[yellow]Rebuilding indexes — this may take several minutes...[/]")
    async with engine.connect() as conn:
        for i, sql in enumerate(_BULK_INDEXES, 1):
            name = _BULK_INDEX_NAMES[i - 1]
            console.print(f"  [{i}/{len(_BULK_INDEXES)}] {name}")
            await conn.execute(text(sql.strip()))
            await conn.commit()  # commit individually so progress is visible
    console.print("[green]All indexes rebuilt[/]")


console = Console()
settings = get_settings()

# Common FCC Radio Service codes
RADIO_SERVICE_CODES = {
    "IG": "Industrial/Business Pool",
    "YG": "Industrial/Business Pool, 800 MHz",
    "YX": "SMR, 800 MHz",
    "YK": "Trunked SMR, 800 MHz",
    "YW": "Public Safety Pool, 800 MHz",
    "SG": "Conventional Public Safety, 800 MHz",
    "SY": "Trunked Public Safety, 800 MHz",
    "PA": "Public Safety, APCO-16",
    "PW": "Public Safety Pool",
    "GP": "General Pool",
    "GX": "SMR",
    "LN": "902-928 MHz",
    "PC": "Public Coast Stations",
    "BA": "TV Microwave Booster",
    "CF": "Cable TV Relay",
    "MG": "Microwave Industrial/Business",
    "MW": "Microwave Public Safety",
    "MK": "Alaska Fixed",
    "QM": "Commercial Operator",
    "AF": "Aeronautical Fixed",
    "AI": "Aircraft Station",
    "MA": "Marine Auxiliary",
    "MR": "Marine Radiolocation",
    "MC": "Coastal Group",
    "SA": "Ship Station",
    "HA": "Amateur",
    "HV": "Amateur Vanity",
    "LP": "Broadcast Auxiliary Low Power",
    "TP": "Broadcast Auxiliary TV Pickup",
    "RP": "Broadcast Auxiliary Remote Pickup",
    "TS": "TV Studio-Transmitter Link",
    "RS": "Radio Studio-Transmitter Link",
    "TI": "TV Intercity Relay",
    "RI": "Radio Intercity Relay",
    "TB": "TV Booster",
    "RB": "Radio Booster",
    "CD": "Paging and Radiotelephone",
    "CE": "Digital Electronic Message Service",
    "CN": "Paging, Narrowband PCS",
    "CP": "Part 22 VHF/UHF Paging",
    "QO": "Other",
    # Additional services
    "WL": "Wireless Licensed",
    "WU": "Wireless Unlicensed",
    "PI": "Paging - In-Building",
    "PD": "Paging - Dispatch",
    "BT": "Broadcast TV",
    "BR": "Broadcast Radio",
    "SR": "Satellite Service",
}


async def init_radio_services(session: AsyncSession) -> None:
    """Insert or update radio service reference codes."""
    for code, description in RADIO_SERVICE_CODES.items():
        await session.execute(
            text("""
                INSERT INTO radio_services (code, description)
                VALUES (:code, :description)
                ON CONFLICT (code) DO UPDATE SET description = :description
            """),
            {"code": code, "description": description},
        )
    await session.commit()
    console.print(f"[green]Loaded {len(RADIO_SERVICE_CODES)} radio service codes[/]")


async def _ensure_radio_services(bundles: dict, session: AsyncSession) -> None:
    """Auto-insert any radio service codes found in data but missing from the DB."""
    # Collect all unique service codes from the bundles
    codes_in_data: set[str] = set()
    for bundle in bundles.values():
        code = bundle.license.radio_service_code.strip()
        if code:
            codes_in_data.add(code)

    if not codes_in_data:
        return

    # Find which ones already exist
    result = await session.execute(
        text("SELECT code FROM radio_services")
    )
    existing = {row[0] for row in result}
    missing = codes_in_data - existing

    if missing:
        for code in sorted(missing):
            await session.execute(
                text("INSERT INTO radio_services (code, description) VALUES (:c, :d) ON CONFLICT DO NOTHING"),
                {"c": code, "d": f"Auto-discovered ({code})"},
            )
        await session.commit()
        console.print(f"  [cyan]Auto-inserted {len(missing)} new radio service codes:[/] {', '.join(sorted(missing))}")


async def load_bundles_to_db(
    bundles: dict,
    session_factory: async_sessionmaker,
    batch_size: int = 5000,
    concurrency: int = 4,
) -> tuple[int, int, int]:
    """Load normalized bundles into the database.

    Pipeline:
      1. All normalization runs in a background thread (CPU-bound, non-blocking).
      2. Prepared batches are inserted concurrently using *concurrency* async
         tasks, each with its own session/connection.

    Returns (license_count, location_count, frequency_count).
    """
    # Auto-insert any unknown radio service codes first (single session, quick)
    async with session_factory() as svc_session:
        await _ensure_radio_services(bundles, svc_session)

    bundle_list = list(bundles.values())
    total = len(bundle_list)

    # ------------------------------------------------------------------
    # Step 1 — Normalize ALL records in a thread so the event loop stays
    #           free and Python CPU work overlaps with any pending DB I/O.
    # ------------------------------------------------------------------
    console.print(f"  [cyan]Pre-normalizing {total:,} bundles (background thread)...[/]")
    all_prepared: list[dict] = await asyncio.to_thread(_normalize_all_sync, bundle_list)
    console.print(f"  [green]Normalization complete:[/] {len(all_prepared):,} valid records")

    # ------------------------------------------------------------------
    # Step 2 — Fan out DB inserts across *concurrency* concurrent sessions
    # ------------------------------------------------------------------
    batches = [
        all_prepared[i : i + batch_size]
        for i in range(0, len(all_prepared), batch_size)
    ]

    sem = asyncio.Semaphore(concurrency)
    license_count = 0
    location_count = 0
    frequency_count = 0
    error_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Inserting batches", total=len(batches))

        async def run_batch(batch_num: int, batch: list[dict]) -> None:
            nonlocal license_count, location_count, frequency_count, error_count
            async with sem:
                try:
                    async with session_factory() as session:
                        lc, loc, fc = await _insert_prepared_batch(batch, session)
                    license_count += lc
                    location_count += loc
                    frequency_count += fc
                except Exception as exc:
                    error_count += 1
                    console.print(f"  [yellow]Batch {batch_num} error:[/] {exc}")
                finally:
                    progress.update(task, advance=1)

        await asyncio.gather(*(run_batch(i, b) for i, b in enumerate(batches)))

    if error_count:
        console.print(f"  [yellow]{error_count} batch(es) failed[/]")

    return license_count, location_count, frequency_count


# ---------------------------------------------------------------------------
# CPU normalization helper — runs in asyncio.to_thread
# ---------------------------------------------------------------------------

def _normalize_all_sync(bundle_list: list) -> list[dict]:
    """Normalize every bundle synchronously; designed to run in a thread pool.

    Keeping this outside the event loop means the heavy Python loops don't
    block async scheduling, and on CPython the GIL is released during the
    thread handoff so other threads (e.g. DB network I/O) can progress.
    """
    out: list[dict] = []
    skip = 0
    for bundle in bundle_list:
        try:
            norm_lic = normalize_license(bundle.license, bundle.entity)
            if not norm_lic["callsign"]:
                continue

            normalized_locations: list[dict] = [
                normalize_location(raw_loc) for raw_loc in bundle.locations
            ]
            normalized_frequencies: list[dict] = [
                normalize_frequency(raw_freq) for raw_freq in bundle.frequencies
            ]

            out.append(
                {
                    "license": norm_lic,
                    "locations": normalized_locations,
                    "frequencies": normalized_frequencies,
                }
            )
        except Exception:
            skip += 1
            continue

    if skip:
        console.print(f"  [yellow]Skipped {skip:,} bad records during normalization[/]")
    return out


# ---------------------------------------------------------------------------
# Single-batch DB insertion — called from concurrent async tasks
# ---------------------------------------------------------------------------

async def _insert_prepared_batch(
    prepared_batch: list[dict],
    session: AsyncSession,
) -> tuple[int, int, int]:
    """Insert one pre-normalized batch.  Returns (lic_count, loc_count, freq_count).

    Uses ``synchronous_commit = off`` for this session so PostgreSQL doesn't
    fsync WAL on every commit — safe for bulk imports where re-running is an
    option.
    """
    # Bulk-import perf hint: skip WAL fsync wait (data is still written)
    await session.execute(text("SET synchronous_commit = off"))

    license_rows = [entry["license"] for entry in prepared_batch]
    if not license_rows:
        return 0, 0, 0

    # 1) Insert licenses, get generated IDs
    result = await session.execute(
        insert(License).returning(License.id),
        license_rows,
    )
    license_ids = [row[0] for row in result]

    # 2) Build location rows (all for this batch)
    location_rows: list[dict] = []
    location_owner: list[tuple[int, int | None]] = []

    for idx, entry in enumerate(prepared_batch):
        license_id = license_ids[idx]
        for norm_loc in entry["locations"]:
            loc_num = norm_loc.get("location_number")
            loc_row = dict(norm_loc)
            geom_wkt = loc_row.pop("geom_wkt", None)
            loc_row["license_id"] = license_id
            if geom_wkt:
                loc_row["geom"] = geom_wkt
            location_rows.append(loc_row)
            location_owner.append((idx, loc_num))

    location_id_map: dict[tuple[int, int | None], int] = {}
    first_location_id_by_bundle: dict[int, int] = {}

    if location_rows:
        result = await session.execute(
            insert(Location).returning(Location.id),
            location_rows,
        )
        location_ids_db = [row[0] for row in result]
        for (bundle_idx, loc_num), location_id in zip(location_owner, location_ids_db):
            location_id_map[(bundle_idx, loc_num)] = location_id
            first_location_id_by_bundle.setdefault(bundle_idx, location_id)

    # 3) Build frequency rows, resolving location FK
    unresolved_by_bundle: dict[int, list[dict]] = {}
    frequency_rows: list[dict] = []

    for idx, entry in enumerate(prepared_batch):
        for norm_freq in entry["frequencies"]:
            freq_row = dict(norm_freq)
            loc_num = freq_row.pop("location_number", None)
            target_loc_id = location_id_map.get((idx, loc_num))
            if target_loc_id is None:
                target_loc_id = first_location_id_by_bundle.get(idx)
            if target_loc_id is None:
                unresolved_by_bundle.setdefault(idx, []).append(freq_row)
                continue
            freq_row["location_id"] = target_loc_id
            frequency_rows.append(freq_row)

    # 4) Dummy locations for any bundles that have frequencies but no locations
    bundles_needing_dummy = [
        idx for idx in unresolved_by_bundle if idx not in first_location_id_by_bundle
    ]
    dummy_location_rows = [{"license_id": license_ids[idx]} for idx in bundles_needing_dummy]
    dummy_location_count = 0

    if dummy_location_rows:
        result = await session.execute(
            insert(Location).returning(Location.id),
            dummy_location_rows,
        )
        dummy_ids = [row[0] for row in result]
        dummy_location_count = len(dummy_ids)
        for bundle_idx, dummy_loc_id in zip(bundles_needing_dummy, dummy_ids):
            first_location_id_by_bundle[bundle_idx] = dummy_loc_id

    for bundle_idx, unresolved_rows in unresolved_by_bundle.items():
        fallback_loc_id = first_location_id_by_bundle.get(bundle_idx)
        if fallback_loc_id is None:
            continue
        for freq_row in unresolved_rows:
            freq_row["location_id"] = fallback_loc_id
            frequency_rows.append(freq_row)

    if frequency_rows:
        await session.execute(insert(Frequency), frequency_rows)

    await session.commit()

    return (
        len(license_rows),
        len(location_rows) + dummy_location_count,
        len(frequency_rows),
    )


async def clear_all_data(engine) -> None:
    """Delete all license/location/frequency data.

    Uses TRUNCATE ... RESTART IDENTITY CASCADE which is far faster than DELETE
    on large tables — it skips row-level WAL and resets sequences for a clean slate.
    """
    console.print("[yellow]Clearing existing data...[/]")
    async with engine.connect() as conn:
        await conn.execute(
            text("TRUNCATE TABLE frequencies, locations, licenses RESTART IDENTITY CASCADE")
        )
        await conn.commit()
    console.print("[green]All data cleared[/]")


async def import_zip_file(
    zip_path: Path,
    session_factory: async_sessionmaker,
    batch_size: int = 5000,
    concurrency: int = 4,
) -> tuple[int, int, int]:
    """Import a single FCC ZIP file into the database."""
    parsed = parse_zip_file(zip_path)
    bundles = build_bundles(parsed)
    console.print(f"  [cyan]Found {len(bundles):,} license bundles[/]")
    return await load_bundles_to_db(bundles, session_factory, batch_size=batch_size, concurrency=concurrency)


async def full_import(
    data_dir: str | None = None,
    batch_size: int = 5000,
    concurrency: int = 4,
) -> None:
    """Run a full import: download all files + load into DB.

    Performance strategy:
    - Indexes are dropped before bulk loading and rebuilt at the end.
    - Normalization is offloaded to a background thread.
    - DB inserts are fanned out across *concurrency* concurrent sessions.
    - synchronous_commit=off removes per-commit fsync latency.
    """
    from fcc_importer.downloader import download_full

    data_dir = data_dir or settings.fcc_data_dir

    console.rule("[bold blue]FCC Full Import[/]")

    # Download
    console.print("[bold]Step 1: Download FCC data[/]")
    zip_files = await download_full(data_dir)

    if not zip_files:
        console.print("[red]No files downloaded![/]")
        return

    # Engine sized to handle concurrent batch tasks plus a couple of overhead connections
    engine = create_async_engine(
        settings.database_url,
        pool_size=concurrency + 2,
        max_overflow=concurrency,
    )
    async_sess = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Init reference data
    console.print("[bold]Step 2: Initialize reference data[/]")
    async with async_sess() as session:
        await init_radio_services(session)

    # Wipe tables (fast TRUNCATE)
    console.print("[bold]Step 3: Clear existing data[/]")
    await clear_all_data(engine)

    # Drop secondary indexes — reindex after insert is much faster than live maintenance
    console.print("[bold]Step 4: Drop indexes for bulk load[/]")
    await _drop_import_indexes(engine)

    # Bulk load
    console.print("[bold]Step 5: Import data[/]")
    total_lic = total_loc = total_freq = 0
    for zf in zip_files:
        if zf.exists():
            lc, lo, fr = await import_zip_file(
                zf, async_sess, batch_size=batch_size, concurrency=concurrency
            )
            total_lic += lc
            total_loc += lo
            total_freq += fr

    # Rebuild indexes now that all data is in place
    console.print("[bold]Step 6: Rebuild indexes[/]")
    await _recreate_import_indexes(engine)

    console.rule("[bold green]Import Complete[/]")
    console.print(f"  Licenses:    {total_lic:>12,}")
    console.print(f"  Locations:   {total_loc:>12,}")
    console.print(f"  Frequencies: {total_freq:>12,}")

    await engine.dispose()


async def weekly_import(
    data_dir: str | None = None,
    batch_size: int = 5000,
    concurrency: int = 4,
) -> None:
    """Run a weekly incremental import."""
    from fcc_importer.downloader import download_weekly

    data_dir = data_dir or settings.fcc_data_dir

    console.rule("[bold blue]FCC Weekly Update[/]")

    # Download
    console.print("[bold]Step 1: Download weekly updates[/]")
    zip_files = await download_weekly(data_dir)

    if not zip_files:
        console.print("[red]No files downloaded![/]")
        return

    engine = create_async_engine(
        settings.database_url,
        pool_size=concurrency + 2,
        max_overflow=concurrency,
    )
    async_sess = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    console.print("[bold]Step 2: Import updates[/]")
    total_lic = total_loc = total_freq = 0
    for zf in zip_files:
        if zf.exists():
            parsed = parse_zip_file(zf)
            bundles = build_bundles(parsed)

            # Bulk-delete all USIs that are being replaced, using a single round-trip
            usis_to_delete = [int(usi) for usi in bundles if usi.strip().isdigit()]
            if usis_to_delete:
                async with async_sess() as session:
                    await session.execute(
                        text("DELETE FROM licenses WHERE unique_system_identifier = ANY(:usis)"),
                        {"usis": usis_to_delete},
                    )
                    await session.commit()

            lc, lo, fr = await load_bundles_to_db(
                bundles, async_sess, batch_size=batch_size, concurrency=concurrency
            )
            total_lic += lc
            total_loc += lo
            total_freq += fr

    console.rule("[bold green]Weekly Update Complete[/]")
    console.print(f"  Licenses:    {total_lic:>12,}")
    console.print(f"  Locations:   {total_loc:>12,}")
    console.print(f"  Frequencies: {total_freq:>12,}")

    await engine.dispose()


# ---------------------------------------------------------------------------
# Memory-efficient streaming import for low-RAM environments
# ---------------------------------------------------------------------------


async def streaming_import_zip(
    zip_path: Path,
    session_factory: async_sessionmaker,
    batch_size: int = 2000,
) -> tuple[int, int, int]:
    """Import a single FCC ZIP file using streaming/chunked processing.
    
    This version uses much less memory by:
    1. Loading EN (entity) data into a dict first (needed for joins)
    2. Streaming HD records in chunks, inserting licenses
    3. Streaming LO records, inserting locations in batches
    4. Streaming FR records, inserting frequencies in batches
    
    The USI -> license_id and (USI, loc_num) -> location_id mappings are
    built incrementally and kept in memory, but the raw parsed data is streamed.
    """
    from fcc_importer.parser import (
        extract_zip_to_dir,
        iter_hd_file,
        iter_en_file,
        iter_lo_file,
        iter_fr_file,
    )
    
    console.print(f"[cyan]Streaming import:[/] {zip_path.name}")
    
    # Extract ZIP
    extract_dir = extract_zip_to_dir(zip_path)
    
    # Step 1: Load EN records into memory (needed for joining with HD)
    # This is unavoidable but we only store minimal data
    console.print("  [cyan]Loading entity data...[/]")
    en_by_usi: dict[str, tuple[str, str]] = {}  # usi -> (name, frn)
    en_count = 0
    for en in iter_en_file(extract_dir):
        usi = en.unique_system_identifier.strip()
        if usi:
            name = en.entity_name.strip()
            if not name:
                parts = [en.first_name.strip(), en.last_name.strip()]
                name = " ".join(p for p in parts if p)
            en_by_usi[usi] = (name or None, en.frn.strip() or None)
            en_count += 1
    console.print(f"  [green]Loaded {en_count:,} entity records[/]")
    
    # Step 2: Stream HD records in chunks, insert licenses
    console.print("  [cyan]Importing licenses...[/]")
    usi_to_license_id: dict[str, int] = {}
    usi_to_radio_service: dict[str, str] = {}
    license_count = 0
    
    for hd_chunk in iter_hd_file(extract_dir, chunk_size=batch_size):
        license_rows = []
        chunk_usis = []
        
        for hd in hd_chunk:
            usi = hd.unique_system_identifier.strip()
            if not usi:
                continue
            callsign = hd.callsign.strip()
            if not callsign:
                continue
            
            en_data = en_by_usi.get(usi)
            licensee_name, frn = en_data if en_data else (None, None)
            
            license_rows.append({
                "unique_system_identifier": int(usi) if usi.isdigit() else None,
                "callsign": callsign,
                "licensee_name": licensee_name,
                "radio_service": hd.radio_service_code.strip() or None,
                "status": hd.license_status.strip() or None,
                "grant_date": _parse_date_safe(hd.grant_date),
                "expiration_date": _parse_date_safe(hd.expired_date),
                "effective_date": _parse_date_safe(hd.effective_date),
                "frn": frn,
            })
            chunk_usis.append(usi)
            usi_to_radio_service[usi] = hd.radio_service_code.strip()
        
        if license_rows:
            async with session_factory() as session:
                await session.execute(text("SET synchronous_commit = off"))
                result = await session.execute(
                    insert(License).returning(License.id),
                    license_rows,
                )
                license_ids = [row[0] for row in result]
                await session.commit()
                
                for usi, lid in zip(chunk_usis, license_ids):
                    usi_to_license_id[usi] = lid
                license_count += len(license_ids)
    
    console.print(f"  [green]Inserted {license_count:,} licenses[/]")
    
    # Free EN memory - no longer needed
    del en_by_usi
    
    # Step 3: Stream LO records, insert locations in batches
    console.print("  [cyan]Importing locations...[/]")
    location_key_to_id: dict[tuple[str, int | None], int] = {}  # (usi, loc_num) -> location_id
    location_count = 0
    location_batch = []
    location_keys = []
    
    for lo in iter_lo_file(extract_dir):
        usi = lo.unique_system_identifier.strip()
        if usi not in usi_to_license_id:
            continue
        
        license_id = usi_to_license_id[usi]
        loc_num = int(lo.location_number) if lo.location_number.strip().isdigit() else None
        
        lat = _dms_to_decimal_safe(lo.lat_degrees, lo.lat_minutes, lo.lat_seconds, lo.lat_direction)
        lng = _dms_to_decimal_safe(lo.long_degrees, lo.long_minutes, lo.long_seconds, lo.long_direction)
        
        loc_row = {
            "license_id": license_id,
            "location_number": loc_num,
            "latitude": lat,
            "longitude": lng,
            "county": lo.county.strip() or None,
            "state": lo.state.strip() or None,
            "radius_km": _parse_float_safe(lo.radius_of_operation),
            "ground_elevation": _parse_float_safe(lo.ground_elevation),
            "lat_degrees": int(lo.lat_degrees) if lo.lat_degrees.strip().isdigit() else None,
            "lat_minutes": int(lo.lat_minutes) if lo.lat_minutes.strip().isdigit() else None,
            "lat_seconds": _parse_float_safe(lo.lat_seconds),
            "lat_direction": lo.lat_direction.strip() or None,
            "long_degrees": int(lo.long_degrees) if lo.long_degrees.strip().isdigit() else None,
            "long_minutes": int(lo.long_minutes) if lo.long_minutes.strip().isdigit() else None,
            "long_seconds": _parse_float_safe(lo.long_seconds),
            "long_direction": lo.long_direction.strip() or None,
        }
        
        if lat is not None and lng is not None:
            loc_row["geom"] = f"SRID=4326;POINT({lng} {lat})"
        
        location_batch.append(loc_row)
        location_keys.append((usi, loc_num))
        
        if len(location_batch) >= batch_size:
            async with session_factory() as session:
                await session.execute(text("SET synchronous_commit = off"))
                result = await session.execute(
                    insert(Location).returning(Location.id),
                    location_batch,
                )
                loc_ids = [row[0] for row in result]
                await session.commit()
                
                for key, loc_id in zip(location_keys, loc_ids):
                    location_key_to_id[key] = loc_id
                location_count += len(loc_ids)
            location_batch = []
            location_keys = []
    
    # Insert remaining locations
    if location_batch:
        async with session_factory() as session:
            await session.execute(text("SET synchronous_commit = off"))
            result = await session.execute(
                insert(Location).returning(Location.id),
                location_batch,
            )
            loc_ids = [row[0] for row in result]
            await session.commit()
            
            for key, loc_id in zip(location_keys, loc_ids):
                location_key_to_id[key] = loc_id
            location_count += len(loc_ids)
    
    console.print(f"  [green]Inserted {location_count:,} locations[/]")
    
    # Step 4: Stream FR records, insert frequencies in batches
    console.print("  [cyan]Importing frequencies...[/]")
    frequency_count = 0
    freq_batch = []
    
    for fr in iter_fr_file(extract_dir):
        usi = fr.unique_system_identifier.strip()
        if usi not in usi_to_license_id:
            continue
        
        loc_num = int(fr.location_number) if fr.location_number.strip().isdigit() else None
        location_id = location_key_to_id.get((usi, loc_num))
        
        # If no location found, try to find any location for this license
        if location_id is None:
            for (u, ln), lid in location_key_to_id.items():
                if u == usi:
                    location_id = lid
                    break
        
        if location_id is None:
            continue
        
        freq_mhz = _parse_float_safe(fr.frequency_assigned)
        if freq_mhz is None:
            continue
        
        emission = fr.emission_designator.strip()
        parsed_em = parse_emission_designator(emission) if emission else {}
        
        freq_batch.append({
            "location_id": location_id,
            "frequency_mhz": freq_mhz,
            "frequency_upper_mhz": _parse_float_safe(fr.frequency_upper_band),
            "emission_designator": emission or None,
            "power": _parse_float_safe(fr.power),
            "station_class": fr.station_class_code.strip() or None,
            "emission_bandwidth": parsed_em.get("bandwidth"),
            "emission_modulation": parsed_em.get("modulation"),
            "emission_signal_type": parsed_em.get("signal_type"),
        })
        
        if len(freq_batch) >= batch_size:
            async with session_factory() as session:
                await session.execute(text("SET synchronous_commit = off"))
                await session.execute(insert(Frequency), freq_batch)
                await session.commit()
                frequency_count += len(freq_batch)
            freq_batch = []
    
    # Insert remaining frequencies
    if freq_batch:
        async with session_factory() as session:
            await session.execute(text("SET synchronous_commit = off"))
            await session.execute(insert(Frequency), freq_batch)
            await session.commit()
            frequency_count += len(freq_batch)
    
    console.print(f"  [green]Inserted {frequency_count:,} frequencies[/]")
    
    return license_count, location_count, frequency_count


def _parse_date_safe(date_str: str):
    """Safely parse a date string."""
    from datetime import datetime
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float_safe(value: str) -> float | None:
    """Safely parse a float."""
    if not value or not value.strip():
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _dms_to_decimal_safe(degrees: str, minutes: str, seconds: str, direction: str) -> float | None:
    """Convert DMS to decimal degrees safely."""
    d = int(degrees) if degrees.strip().isdigit() else None
    if d is None:
        return None
    m = int(minutes) if minutes.strip().isdigit() else 0
    s = _parse_float_safe(seconds) or 0.0
    decimal = d + m / 60.0 + s / 3600.0
    if direction and direction.strip().upper() in ("S", "W"):
        decimal = -decimal
    return decimal


async def full_import_streaming(
    data_dir: str | None = None,
    batch_size: int = 2000,
) -> None:
    """Run a full import using memory-efficient streaming.
    
    This version processes files one at a time and streams records
    in batches, making it suitable for low-memory VPS environments.
    """
    from fcc_importer.downloader import download_full

    data_dir = data_dir or settings.fcc_data_dir

    console.rule("[bold blue]FCC Full Import (Streaming Mode)[/]")

    # Download
    console.print("[bold]Step 1: Download FCC data[/]")
    zip_files = await download_full(data_dir)

    if not zip_files:
        console.print("[red]No files downloaded![/]")
        return

    engine = create_async_engine(
        settings.database_url,
        pool_size=4,
        max_overflow=2,
    )
    async_sess = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Init reference data
    console.print("[bold]Step 2: Initialize reference data[/]")
    async with async_sess() as session:
        await init_radio_services(session)

    # Wipe tables
    console.print("[bold]Step 3: Clear existing data[/]")
    await clear_all_data(engine)

    # Drop indexes
    console.print("[bold]Step 4: Drop indexes for bulk load[/]")
    await _drop_import_indexes(engine)

    # Stream import each file
    console.print("[bold]Step 5: Import data (streaming mode)[/]")
    total_lic = total_loc = total_freq = 0
    for zf in zip_files:
        if zf.exists():
            lc, lo, fr = await streaming_import_zip(zf, async_sess, batch_size=batch_size)
            total_lic += lc
            total_loc += lo
            total_freq += fr

    # Rebuild indexes
    console.print("[bold]Step 6: Rebuild indexes[/]")
    await _recreate_import_indexes(engine)

    console.rule("[bold green]Import Complete[/]")
    console.print(f"  Licenses:    {total_lic:>12,}")
    console.print(f"  Locations:   {total_loc:>12,}")
    console.print(f"  Frequencies: {total_freq:>12,}")

    await engine.dispose()
