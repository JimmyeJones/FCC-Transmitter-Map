# FCC Radio License Map

Self-hosted Python web application that imports FCC license data and displays it on an interactive map.

## Features

- **Interactive Map**: Browse radio licenses on a zoomable, searchable map
- **Shareable Filters**: Apply filters and share the URL with encoded filter parameters (e.g., `/?state=VA&service=IG`)
- **Comprehensive Data**: Includes Land Mobile, Microwave, Aviation, Paging, Public Safety, Amateur Radio, and more
- **Real-time Search**: Filter by callsign, state, radio service, frequency range, and geography
- **Frequency Search**: Find all licenses operating on or near a specific frequency
- **State & County Browse**: Navigate by geographic regions
- **Full State Names**: Displays complete state names throughout the interface instead of abbreviations
- **Robust Data Handling**: Properly handles missing/unknown data values for cleaner display

## Quick Start (Docker)

```bash
docker compose up -d
```

Then visit http://localhost:8000

## Import Data

```bash
# Full import (downloads ~2GB of FCC data)
docker compose exec web python -m app.cli import-full

# Weekly update
docker compose exec web python -m app.cli import-update
```

## Features & Usage

### Map View (/)
- Interactive map with marker clustering
- **Filters**:
  - **Callsign**: Search for specific radio stations
  - **State**: Filter by U.S. state or territory
  - **Radio Service**: Filter by service type (Land Mobile, Amateur, etc.)
  - **Frequency Range**: Filter by operating frequency in MHz
- **URL Sharing**: All filters are reflected in the URL for easy sharing
  - Example: `/?state=VA&service=IG` shows Virginia Industrial/Business licenses

### State Browse (/browse)
- Browse all states with license counts
- Full state names displayed

### County View (/county/<state>/<county>)
- List all licenses in a specific county
- Sort by callsign or licensee name
- Pagination support

### License Detail (/license/<callsign>)
- Full license information
- All transmitter/receiver locations
- Frequency assignments with emission designators

### Frequency Search (/frequency/<mhz>)
- Find all licenses on or near a specific frequency
- Configurable tolerance (default ±12.5 kHz)
- Results sorted by frequency

## Data Sources

All data from official FCC public datasets:

- **Land Mobile**: Private, Commercial, Broadcast Auxiliary
- **Radio Services**: Microwave, Coastal, Aviation, Industrial/Business, Market-based, Paging
- **Public Safety**: Conventional and trunked systems
- **Amateur Radio**: Licensed amateur radio operators
- **Miscellaneous**: TV booster, wildlife, other services

Data is downloaded directly from:
- [FCC ULS Bulk Downloads](https://data.fcc.gov/download/pub/uls/complete/)
- [Weekly Updates](https://data.fcc.gov/download/pub/uls/daily/)

No web scraping - only official FCC public data.

## Stack

- **Backend**: FastAPI + Uvicorn
- **Database**: PostgreSQL + PostGIS
- **Frontend**: Jinja2 + HTMX + Leaflet.js
- **Data**: FCC ULS bulk data
- **Deployment**: Docker + Docker Compose

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Initialize database
python -m app.cli init-db --drop

# Run dev server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In another terminal, import data
python -m app.cli import-full
```

## Configuration

Create a `.env` file (or use environment variables):

```bash
# Database
FCC_DATABASE_URL=postgresql+asyncpg://fcc:fcc@localhost:5432/fcc
FCC_DATABASE_SYNC_URL=postgresql://fcc:fcc@localhost:5432/fcc

# FCC Data Sources
FCC_FCC_BULK_URL=https://data.fcc.gov/download/pub/uls/complete/
FCC_FCC_WEEKLY_URL=https://data.fcc.gov/download/pub/uls/daily/
FCC_FCC_DATA_DIR=./data/fcc_downloads

# Application
FCC_APP_TITLE=FCC Radio License Map
FCC_DEBUG=false

# Map Defaults (centered on US)
FCC_MAP_DEFAULT_LAT=39.8283
FCC_MAP_DEFAULT_LNG=-98.5795
FCC_MAP_DEFAULT_ZOOM=5
FCC_MAP_MAX_RESULTS=5000

# Cache (optional)
# FCC_REDIS_URL=redis://localhost:6379/0
FCC_CACHE_TTL=3600
```

## Troubleshooting

### Markers not loading on map
- The map automatically filters out locations with invalid or missing coordinates
- Check the browser console for any errors
- Ensure your database has been populated with `import-full`

### Performance issues
- PostGIS spatial indexes are built automatically after import
- For large datasets, allow 30+ minutes for the initial import
- Weekly updates are incremental and much faster

### Database connection errors
- Verify PostgreSQL and PostGIS are running
- Check credentials in `.env` or environment variables
- PostGIS extension is installed: `CREATE EXTENSION postgis;`

## Production Deployment

### Prerequisites
- Server with Docker, Docker Compose installed
- At least 2GB RAM and 50GB disk space
- Optional: SSL certificate for HTTPS

### Deployment Steps

1. **Clone repository and prepare environment**
   ```bash
   git clone https://github.com/JimmyeJones/FCC-Transmitter-Map
   cd FCC-Transmitter-Map
   cp .env.example .env
   # Edit .env with your production settings
   ```

2. **Build and start containers**
   ```bash
   docker compose up -d --build
   ```

3. **Import initial FCC data** (first time only)
   ```bash
   docker compose exec web python -m app.cli import-full
   ```

4. **Verify deployment**
   ```bash
   # Check health status
   curl http://localhost:8000/api/health
   
   # Check scheduler status
   curl http://localhost:8000/api/admin/scheduler
   
   # View logs
   docker compose logs -f web
   ```

### Enable HTTPS (Recommended)

The app includes Caddy for automatic HTTPS with Let's Encrypt certificates.

1. **Set your domain in `.env`**
   ```bash
   # Add this line to .env
   DOMAIN=your-domain.com
   ```

2. **Ensure DNS points to your server**
   - Create an A record: `your-domain.com` → `your-server-ip`

3. **Start with HTTPS enabled**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.https.yml up -d
   ```

That's it! Caddy automatically obtains and renews SSL certificates from Let's Encrypt.

Your site will be available at `https://your-domain.com`

### Automated Updates

The application automatically updates FCC data every Monday at 2 AM (UTC). Key features:

- **Scheduler**: APScheduler runs weekly incremental updates
- **Error handling**: Failed updates don't crash the application
- **Health monitoring**: Check `/api/health` endpoint to verify scheduler operation
- **Graceful shutdown**: Scheduler stops cleanly when containers are stopped

Monitor scheduler status:
```bash
# Current scheduler status
curl http://localhost:8000/api/admin/scheduler

# Response example:
{
  "running": true,
  "last_update": "2024-01-08T02:00:15Z",
  "last_error": null,
  "next_run": "2024-01-15T02:00:00Z"
}
```

### HTTPS Configuration (Optional)

To enable HTTPS, provide SSL certificates and update `nginx.conf`:

```nginx
server {
    listen 443 ssl;
    ssl_certificate /etc/nginx/certs/cert.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;
    # ... rest of config
}

server {
    listen 80;
    return 301 https://$host$request_uri;  # Redirect HTTP to HTTPS
}
```

Then update docker-compose.yml to expose port 443 and mount certificates.

### Monitoring

The application provides health check endpoints for monitoring integration:

```bash
# Basic application health
GET /api/health
# Returns: {"status": "healthy|unhealthy", "database": "connected|disconnected", "scheduler": {...}}

# Scheduler status (admin endpoint)
GET /api/admin/scheduler
# Returns next run time, last update, and error information
```

### Backup Strategy

PostgreSQL data persists in a Docker volume. To backup:

```bash
# Backup database
docker compose exec db pg_dump -U fcc fcc > backup.sql

# Restore from backup
docker compose exec -T db psql -U fcc fcc < backup.sql
```

For production, consider:
- Regular automated backups (daily or weekly)
- Off-site backup storage
- Backup retention policy (30-90 days recommended)

### Performance Tuning

Configured production settings:
- **PostgreSQL**: max_connections=200, shared_buffers=256MB
- **Web service**: CPU limit 1, memory limit 1GB
- **Nginx**: Reverse proxy with connection pooling
- **Data caching**: Optional Redis integration (set FCC_REDIS_URL in .env)

### Logging

Access logs from all services:

```bash
# View logs
docker compose logs -f

# Specific service logs
docker compose logs -f web
docker compose logs -f db
docker compose logs -f nginx

# Log file location (Docker)
docker inspect <container-id> | grep LogPath
```

Log levels can be configured via `FCC_LOG_LEVEL` environment variable (DEBUG, INFO, WARNING, ERROR).

