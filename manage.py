#!/usr/bin/env python3
"""Management CLI entry point.

Usage:
    python manage.py import-full
    python manage.py import-update
    python manage.py rebuild-indexes
    python manage.py vacuum
    python manage.py stats
    python manage.py init-db
    python manage.py init-services
    python manage.py load-counties
    python manage.py fill-counties
    python manage.py run-server
"""

from app.cli import app

if __name__ == "__main__":
    app()
