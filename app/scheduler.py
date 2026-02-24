"""Background scheduler for FCC data updates."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

from app.config import get_settings
from fcc_importer.loader import weekly_import

logger = logging.getLogger(__name__)
console = Console()


class FCCScheduler:
    """Manages scheduled FCC data updates."""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.settings = get_settings()
        self.is_running = False
        self.last_update = None
        self.last_error = None
    
    async def start(self):
        """Start the scheduler."""
        try:
            # Schedule weekly updates (every Monday at 2 AM)
            self.scheduler.add_job(
                self._weekly_update_wrapper,
                CronTrigger(day_of_week=0, hour=2, minute=0),
                id='fcc_weekly_update',
                name='FCC Weekly Update',
                misfire_grace_time=60,  # Allow 1 minute grace period
                coalesce=True,  # Don't run multiple times if scheduler was stopped
                max_instances=1,  # Only one instance at a time
            )
            
            if not self.scheduler.running:
                self.scheduler.start()
                self.is_running = True
                logger.info("FCC scheduler started - weekly updates scheduled for Mondays at 2 AM")
                console.print("[green]✓[/] FCC scheduler started")
            
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            console.print(f"[red]✗[/] Failed to start scheduler: {e}")
    
    async def stop(self):
        """Stop the scheduler."""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                self.is_running = False
                logger.info("FCC scheduler stopped")
                console.print("[yellow]⊘[/] FCC scheduler stopped")
        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")
    
    async def _weekly_update_wrapper(self):
        """Wrapper for weekly update with error handling."""
        try:
            logger.info("Starting scheduled FCC weekly update...")
            console.print("[cyan]→[/] Starting FCC weekly update...")
            
            # Run the import in a thread to avoid blocking
            await asyncio.to_thread(
                self._run_weekly_update
            )
            
            # Rebuild the pre-computed grid cache with fresh data
            try:
                from app.grid_cache import refresh_grid_cache
                from app.database import engine
                await refresh_grid_cache(engine)
            except Exception as e:
                logger.error(f"Grid cache refresh failed: {e}", exc_info=True)

            self.last_update = datetime.now()
            logger.info("FCC weekly update completed successfully")
            console.print("[green]✓[/] FCC weekly update completed successfully")
            
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"FCC weekly update failed: {e}", exc_info=True)
            console.print(f"[red]✗[/] FCC weekly update failed: {e}")
    
    def _run_weekly_update(self):
        """Run the weekly update (synchronous, runs in thread)."""
        try:
            # Import here to avoid circular imports
            from fcc_importer.loader import weekly_import
            import asyncio as aio
            
            # Create new event loop for this thread
            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    weekly_import(
                        data_dir=self.settings.fcc_data_dir,
                        batch_size=5000,
                        concurrency=4,
                    )
                )
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"Error running weekly import: {e}", exc_info=True)
            raise
    
    def get_status(self) -> dict:
        """Get scheduler status."""
        next_run = None
        if self.scheduler.running:
            job = self.scheduler.get_job('fcc_weekly_update')
            if job:
                next_run = job.next_run_time
        
        return {
            "running": self.is_running and self.scheduler.running,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "next_update": next_run.isoformat() if next_run else None,
            "last_error": self.last_error,
        }


# Global scheduler instance
_scheduler = None


def get_scheduler() -> FCCScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = FCCScheduler()
    return _scheduler
