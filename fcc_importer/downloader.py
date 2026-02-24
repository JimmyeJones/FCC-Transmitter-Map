"""Download FCC bulk data files."""

import asyncio
import os
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn

from app.config import get_settings

console = Console()
settings = get_settings()

# FCC ULS bulk download files we need
# Land Mobile services contain the bulk of scanner-relevant data
FULL_DOWNLOAD_FILES = [
    # Land Mobile services
    "l_LMpriv.zip",   # Land Mobile - Private
    "l_LMcomm.zip",   # Land Mobile - Commercial
    "l_LMbcast.zip",  # Land Mobile - Broadcast Auxiliary
    # Radio Services
    "l_micro.zip",    # Microwave
    "l_coast.zip",    # Coastal
    "l_aviation.zip", # Aviation
    "l_IG.zip",       # Industrial/Business
    "l_market.zip",   # Market-based
    "l_paging.zip",   # Paging
    # Public Safety
    "l_public.zip",   # Public Safety Pool
    # Amateur Radio
    "l_amateur.zip",  # Amateur Radio
    # Other services
    "l_other.zip",    # Miscellaneous (ATVs, Telemetry, etc)
]

WEEKLY_UPDATE_FILES = [
    # Land Mobile
    "a_LMpriv.zip",
    "a_LMcomm.zip",
    "a_LMbcast.zip",
    # Radio Services
    "a_micro.zip",
    "a_coast.zip",
    "a_aviation.zip",
    "a_IG.zip",
    "a_market.zip",
    "a_paging.zip",
    # Public Safety
    "a_public.zip",
    # Amateur Radio
    "a_amateur.zip",
    # Other services
    "a_other.zip",
]


async def download_file(url: str, dest: Path, client: httpx.AsyncClient) -> Path:
    """Download a single file with progress display."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Downloading[/] {url}")

    async with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code == 404:
            console.print(f"[yellow]Not found (404):[/] {url}")
            return dest
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))

        with Progress(
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            task = progress.add_task("download", total=total)
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

    console.print(f"[green]Saved[/] {dest}")
    return dest


async def download_full(data_dir: str | None = None) -> list[Path]:
    """Download all full FCC bulk data files. Skips already-downloaded files."""
    data_dir = data_dir or settings.fcc_data_dir
    base_path = Path(data_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    downloaded = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        for filename in FULL_DOWNLOAD_FILES:
            dest = base_path / filename
            if dest.exists() and dest.stat().st_size > 0:
                console.print(f"[green]Already downloaded:[/] {filename} ({dest.stat().st_size / 1048576:.1f} MB)")
                downloaded.append(dest)
                continue
            url = f"{settings.fcc_bulk_url}{filename}"
            try:
                await download_file(url, dest, client)
                downloaded.append(dest)
            except httpx.HTTPError as e:
                console.print(f"[red]Error downloading {filename}:[/] {e}")

    return downloaded


async def download_weekly(data_dir: str | None = None) -> list[Path]:
    """Download weekly update files."""
    data_dir = data_dir or settings.fcc_data_dir
    base_path = Path(data_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    downloaded = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        for filename in WEEKLY_UPDATE_FILES:
            url = f"{settings.fcc_weekly_url}{filename}"
            dest = base_path / filename
            try:
                await download_file(url, dest, client)
                downloaded.append(dest)
            except httpx.HTTPError as e:
                console.print(f"[red]Error downloading {filename}:[/] {e}")

    return downloaded
