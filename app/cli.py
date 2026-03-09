"""CLI management commands for FCC Radio Map."""

import asyncio
import typer
from rich.console import Console

app = typer.Typer(help="FCC Radio License Map management commands.")
console = Console()


@app.command()
def import_full(
    data_dir: str = typer.Option(None, help="Directory to store downloaded FCC data"),
    batch_size: int = typer.Option(5000, help="Records per DB insert batch"),
    concurrency: int = typer.Option(4, help="Number of concurrent DB insert tasks"),
):
    """Run a full import of FCC bulk data.

    Indexes are automatically dropped before loading and rebuilt afterwards,
    which dramatically reduces insert overhead on large datasets.
    """
    from fcc_importer.loader import full_import
    console.print(f"[bold blue]Starting full FCC data import[/] "
                  f"(batch={batch_size:,}, concurrency={concurrency})")
    asyncio.run(full_import(data_dir, batch_size=batch_size, concurrency=concurrency))


@app.command()
def import_update(
    data_dir: str = typer.Option(None, help="Directory to store downloaded FCC data"),
    batch_size: int = typer.Option(5000, help="Records per DB insert batch"),
    concurrency: int = typer.Option(4, help="Number of concurrent DB insert tasks"),
):
    """Run a weekly incremental update."""
    from fcc_importer.loader import weekly_import
    console.print("[bold blue]Starting weekly FCC data update...[/]")
    asyncio.run(weekly_import(data_dir, batch_size=batch_size, concurrency=concurrency))


@app.command()
def import_streaming(
    data_dir: str = typer.Option(None, help="Directory to store downloaded FCC data"),
    batch_size: int = typer.Option(2000, help="Records per DB insert batch"),
):
    """Run a memory-efficient streaming import for low-RAM environments.

    This mode processes data in small chunks rather than loading entire files
    into memory. Use this if the standard import-full command crashes due to
    out-of-memory errors on your VPS.
    """
    from fcc_importer.loader import full_import_streaming
    console.print(f"[bold blue]Starting streaming FCC data import[/] "
                  f"(batch={batch_size:,}, low-memory mode)")
    asyncio.run(full_import_streaming(data_dir, batch_size=batch_size))


@app.command()
def init_db(
    drop_existing: bool = typer.Option(False, "--drop", help="Drop existing tables first"),
):
    """Initialize the database schema (create tables)."""
    from sqlalchemy import create_engine, text
    from app.config import get_settings
    from app.database import Base
    from app.models import License, Location, Frequency, RadioService  # noqa: ensure models loaded

    settings = get_settings()
    # Use sync URL for table creation
    sync_url = settings.database_sync_url
    engine = create_engine(sync_url)

    # Enable PostGIS
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.commit()

    if drop_existing:
        console.print("[yellow]Dropping existing tables...[/]")
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)
    console.print("[green]Database tables created successfully.[/]")
    engine.dispose()


@app.command()
def rebuild_indexes():
    """Rebuild database indexes."""
    from sqlalchemy import create_engine, text
    from app.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)

    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_locations_geom ON locations USING gist (geom)",
        "CREATE INDEX IF NOT EXISTS ix_frequencies_frequency_mhz ON frequencies (frequency_mhz)",
        "CREATE INDEX IF NOT EXISTS ix_locations_state ON locations (state)",
        "CREATE INDEX IF NOT EXISTS ix_locations_county ON locations (county)",
        "CREATE INDEX IF NOT EXISTS ix_licenses_callsign ON licenses (callsign)",
        "CREATE INDEX IF NOT EXISTS ix_licenses_radio_service ON licenses (radio_service)",
        "CREATE INDEX IF NOT EXISTS ix_locations_state_county ON locations (state, county)",
        "CREATE INDEX IF NOT EXISTS ix_frequencies_freq_range ON frequencies (frequency_mhz, frequency_upper_mhz)",
        "CREATE INDEX IF NOT EXISTS ix_licenses_callsign_status ON licenses (callsign, status)",
    ]

    with engine.connect() as conn:
        for idx_sql in indexes:
            console.print(f"[cyan]Creating:[/] {idx_sql}")
            conn.execute(text(idx_sql))
        conn.commit()

    console.print("[green]All indexes rebuilt.[/]")
    engine.dispose()


@app.command()
def vacuum():
    """Run VACUUM ANALYZE on all tables."""
    from sqlalchemy import create_engine, text
    from app.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_sync_url, isolation_level="AUTOCOMMIT")

    tables = ["licenses", "locations", "frequencies", "radio_services"]
    with engine.connect() as conn:
        for table in tables:
            console.print(f"[cyan]VACUUM ANALYZE[/] {table}")
            conn.execute(text(f"VACUUM ANALYZE {table}"))

    console.print("[green]VACUUM ANALYZE complete.[/]")
    engine.dispose()


@app.command()
def stats():
    """Show database statistics."""
    from sqlalchemy import create_engine, text
    from app.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_sync_url)

    with engine.connect() as conn:
        tables = {
            "licenses": "SELECT COUNT(*) FROM licenses",
            "locations": "SELECT COUNT(*) FROM locations",
            "frequencies": "SELECT COUNT(*) FROM frequencies",
            "radio_services": "SELECT COUNT(*) FROM radio_services",
            "locations_with_geom": "SELECT COUNT(*) FROM locations WHERE geom IS NOT NULL",
            "active_licenses": "SELECT COUNT(*) FROM licenses WHERE status = 'A'",
            "states": "SELECT COUNT(DISTINCT state) FROM locations WHERE state IS NOT NULL",
        }

        console.rule("[bold]Database Statistics[/]")
        for label, query in tables.items():
            try:
                result = conn.execute(text(query))
                count = result.scalar()
                console.print(f"  {label:.<30} {count:>12,}")
            except Exception as e:
                console.print(f"  {label:.<30} [red]Error: {e}[/]")

    engine.dispose()


@app.command()
def init_services():
    """Initialize radio service reference codes."""
    from fcc_importer.loader import init_radio_services
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from app.config import get_settings

    settings = get_settings()

    async def _run():
        engine = create_async_engine(settings.database_url)
        async_sess = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_sess() as session:
            await init_radio_services(session)
        await engine.dispose()

    asyncio.run(_run())


@app.command()
def load_counties(
    data_dir: str = typer.Option(None, help="Directory to store downloaded county data"),
):
    """Download and load US county boundaries for local reverse geocoding.
    
    Downloads the Census Bureau county shapefile and loads it into PostGIS.
    This only needs to be run once.
    """
    import zipfile
    import tempfile
    import urllib.request
    from pathlib import Path
    from sqlalchemy import create_engine, text
    from app.config import get_settings
    
    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    
    # Use provided data_dir or temp directory
    if data_dir:
        download_dir = Path(data_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
    else:
        download_dir = Path(tempfile.gettempdir()) / "fcc_county_data"
        download_dir.mkdir(parents=True, exist_ok=True)
    
    # Census Bureau county shapefile URL (500k resolution - good balance of detail/size)
    url = "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip"
    zip_path = download_dir / "cb_2022_us_county_500k.zip"
    
    # Download if not already present
    if not zip_path.exists():
        console.print(f"[cyan]Downloading county boundaries from Census Bureau...[/]")
        console.print(f"  URL: {url}")
        urllib.request.urlretrieve(url, zip_path)
        console.print(f"[green]Downloaded to {zip_path}[/]")
    else:
        console.print(f"[yellow]Using cached file: {zip_path}[/]")
    
    # Extract the shapefile
    extract_dir = download_dir / "county_shp"
    if not extract_dir.exists():
        console.print("[cyan]Extracting shapefile...[/]")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
    
    shp_file = extract_dir / "cb_2022_us_county_500k.shp"
    if not shp_file.exists():
        console.print(f"[red]Error: Shapefile not found at {shp_file}[/]")
        engine.dispose()
        return
    
    # State FIPS to abbreviation mapping
    state_fips_to_abbrev = {
        "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
        "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
        "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
        "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
        "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
        "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
        "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
        "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
        "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
        "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
        "56": "WY", "72": "PR", "78": "VI", "66": "GU", "60": "AS",
        "69": "MP",
    }
    
    console.print("[cyan]Loading county boundaries into database...[/]")
    
    try:
        import shapefile  # pyshp library
    except ImportError:
        console.print("[red]Error: pyshp library not installed. Run: pip install pyshp[/]")
        engine.dispose()
        return
    
    # Read shapefile
    sf = shapefile.Reader(str(shp_file))
    
    # Get field names
    fields = [f[0] for f in sf.fields[1:]]  # Skip DeletionFlag
    statefp_idx = fields.index("STATEFP")
    countyfp_idx = fields.index("COUNTYFP")
    name_idx = fields.index("NAME")
    
    with engine.connect() as conn:
        # Create table if not exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS county_boundaries (
                id SERIAL PRIMARY KEY,
                state_fips VARCHAR(2) NOT NULL,
                county_fips VARCHAR(3) NOT NULL,
                state_abbrev VARCHAR(2) NOT NULL,
                county_name VARCHAR(100) NOT NULL,
                geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_county_boundaries_geom ON county_boundaries USING gist (geom)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_county_boundaries_state_county ON county_boundaries (state_abbrev, county_name)"))
        
        # Clear existing data
        conn.execute(text("TRUNCATE county_boundaries"))
        conn.commit()
        
        # Insert counties
        count = 0
        for shape_rec in sf.iterShapeRecords():
            rec = shape_rec.record
            shape = shape_rec.shape
            
            state_fips = rec[statefp_idx]
            county_fips = rec[countyfp_idx]
            county_name = rec[name_idx].upper()
            state_abbrev = state_fips_to_abbrev.get(state_fips, state_fips)
            
            # Convert shape to WKT
            # Handle both Polygon and MultiPolygon
            if shape.shapeType == shapefile.POLYGON:
                # Build WKT for polygon(s)
                parts = list(shape.parts) + [len(shape.points)]
                polygons = []
                for i in range(len(parts) - 1):
                    ring = shape.points[parts[i]:parts[i+1]]
                    ring_wkt = ",".join(f"{p[0]} {p[1]}" for p in ring)
                    polygons.append(f"(({ring_wkt}))")
                wkt = f"MULTIPOLYGON({','.join(polygons)})"
            else:
                continue  # Skip non-polygon shapes
            
            conn.execute(text("""
                INSERT INTO county_boundaries (state_fips, county_fips, state_abbrev, county_name, geom)
                VALUES (:state_fips, :county_fips, :state_abbrev, :county_name, ST_GeomFromText(:wkt, 4326))
            """), {
                "state_fips": state_fips,
                "county_fips": county_fips,
                "state_abbrev": state_abbrev,
                "county_name": county_name,
                "wkt": wkt,
            })
            count += 1
            
            if count % 500 == 0:
                console.print(f"  Loaded {count:,} counties...")
                conn.commit()
        
        conn.commit()
    
    console.print(f"[bold green]Loaded {count:,} county boundaries![/]")
    engine.dispose()


@app.command()
def fill_counties(
    batch_size: int = typer.Option(10000, help="Number of locations to update per batch"),
):
    """Fill in missing county data using local PostGIS spatial lookup.
    
    Uses the county_boundaries table to determine county from coordinates.
    Run 'load-counties' first to populate the boundary data.
    """
    from sqlalchemy import create_engine, text
    from app.config import get_settings
    
    settings = get_settings()
    engine = create_engine(settings.database_sync_url)
    
    with engine.connect() as conn:
        # Check if county_boundaries table exists and has data
        result = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'county_boundaries'"
        ))
        if result.scalar() == 0:
            console.print("[red]Error: county_boundaries table not found.[/]")
            console.print("[yellow]Run 'python manage.py load-counties' first.[/]")
            engine.dispose()
            return
        
        result = conn.execute(text("SELECT COUNT(*) FROM county_boundaries"))
        boundary_count = result.scalar()
        if boundary_count == 0:
            console.print("[red]Error: county_boundaries table is empty.[/]")
            console.print("[yellow]Run 'python manage.py load-counties' first.[/]")
            engine.dispose()
            return
        
        console.print(f"[cyan]Using {boundary_count:,} county boundaries for lookup[/]")
        
        # Count locations needing update
        result = conn.execute(text(
            "SELECT COUNT(*) FROM locations "
            "WHERE (county IS NULL OR county = '') "
            "AND geom IS NOT NULL"
        ))
        total_missing = result.scalar()
        console.print(f"[cyan]Found {total_missing:,} locations with coordinates but missing county[/]")
        
        if total_missing == 0:
            console.print("[green]Nothing to update![/]")
            engine.dispose()
            return
        
        # Update in batches using spatial join
        console.print(f"[bold blue]Updating counties using spatial lookup...[/]")
        
        total_updated = 0
        while True:
            # Update a batch of locations using ST_Contains
            result = conn.execute(text(f"""
                UPDATE locations l
                SET county = cb.county_name,
                    state = COALESCE(l.state, cb.state_abbrev)
                FROM county_boundaries cb
                WHERE (l.county IS NULL OR l.county = '')
                AND l.geom IS NOT NULL
                AND ST_Contains(cb.geom, l.geom)
                AND l.id IN (
                    SELECT id FROM locations
                    WHERE (county IS NULL OR county = '')
                    AND geom IS NOT NULL
                    LIMIT {batch_size}
                )
            """))
            
            updated = result.rowcount
            conn.commit()
            
            if updated == 0:
                break
            
            total_updated += updated
            console.print(f"  Updated {total_updated:,}/{total_missing:,} locations...")
        
        # Check for remaining (outside US boundaries)
        result = conn.execute(text(
            "SELECT COUNT(*) FROM locations "
            "WHERE (county IS NULL OR county = '') "
            "AND geom IS NOT NULL"
        ))
        remaining = result.scalar()
    
    console.print(f"\n[bold green]Complete![/]")
    console.print(f"  Updated: {total_updated:,}")
    if remaining > 0:
        console.print(f"  [yellow]Remaining (outside US boundaries): {remaining:,}[/]")
    engine.dispose()


@app.command()
def run_server(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to listen on"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
):
    """Start the development server."""
    import uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
