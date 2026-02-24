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
