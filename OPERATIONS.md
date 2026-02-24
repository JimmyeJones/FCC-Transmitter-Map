# Quick Reference Guide

Essential commands for managing the FCC Radio License Map in production.

## Getting Started

### Initial Deployment

```bash
# Linux/Mac - Using bash script
chmod +x deploy.sh
./deploy.sh deploy

# Windows - Using batch script
deploy.bat deploy

# Or manually with Docker Compose
docker compose up -d --build
docker compose exec web python -m app.cli import-full
```

## Daily Operations

### Check Application Status

```bash
# View health status
curl http://localhost:8000/api/health

# View scheduler status  
curl http://localhost:8000/api/admin/scheduler

# Check all containers
docker compose ps

# View recent logs
docker compose logs --tail=50 web
```

### View Logs

```bash
# View recent logs from web service
docker compose logs web

# Follow live logs (Ctrl+C to exit)
docker compose logs -f web

# View logs from all services
docker compose logs

# View database logs
docker compose logs db

# View nginx logs
docker compose logs nginx
```

## Maintenance Tasks

### Database Backup

```bash
# Backup to file
docker compose exec db pg_dump -U fcc fcc > backup-$(date +%Y%m%d-%H%M%S).sql

# Compressed backup
docker compose exec db pg_dump -U fcc fcc | gzip > backup-$(date +%Y%m%d).sql.gz

# View backup size
ls -lh backup-*.sql
```

### Database Restore

```bash
# Restore from backup
docker compose exec -T db psql -U fcc fcc < backup-20240101.sql

# Restore compressed backup
gunzip --stdout backup-20240101.sql.gz | docker compose exec -T db psql -U fcc fcc
```

### Verify Data

```bash
# Count licenses in database
docker compose exec db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM license;"

# Count by state
docker compose exec db psql -U fcc -d fcc -c "SELECT state, COUNT(*) FROM location GROUP BY state ORDER BY COUNT(*) DESC LIMIT 10;"

# Count by service
docker compose exec db psql -U fcc -d fcc -c "SELECT service, COUNT(*) FROM license GROUP BY service ORDER BY COUNT(*) DESC;"

# Find recent importer
docker compose exec db psql -U fcc -d fcc -c "SELECT * FROM license ORDER BY id DESC LIMIT 5;"
```

## Troubleshooting

### Containers Won't Start

```bash
# Check error logs
docker compose logs

# Verify configuration
docker compose config

# Try rebuilding everything
docker compose down
docker compose up -d --build

# Check Docker resources
docker system df
docker system prune -a  # WARNING: Removes unused images/containers
```

### Database Connection Issues

```bash
# Test database connection
docker compose exec -T web python -c "from app.database import get_db; print('Connected')"

# Check database is running
docker compose ps db

# View database logs
docker compose logs db

# Connect to database directly
docker compose exec db psql -U fcc -d fcc
```

### High Memory Usage

```bash
# Check resource usage
docker stats

# Restart web service
docker compose restart web

# Show memory usage by service
docker compose exec web free -m
```

### Data Not Showing

```bash
# Check if data imported successfully
docker compose exec db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM license, location;"

# List tables in database
docker compose exec db psql -U fcc -d fcc -c "\dt"

# Check data import logs
docker compose logs web | grep -i "import\|error"

# Manually trigger import
docker compose exec web python -m app.cli import-update
```

## Performance Monitoring

### CPU and Memory Usage

```bash
# Monitor in real-time
docker stats

# Show current usage
docker stats --no-stream

# Get container sizes
docker compose ps -a
du -sh ./data/*  # Data directory size
```

### Database Performance

```bash
# Check database size
docker compose exec db psql -U fcc -d fcc -c "SELECT pg_size_pretty(pg_database_size('fcc'));"

# Show table sizes
docker compose exec db psql -U fcc -d fcc -c "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;"

# Check index sizes
docker compose exec db psql -U fcc -d fcc -c "SELECT schemaname, indexname, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_indexes ORDER BY pg_relation_size(indexrelid) DESC;"

# Show query performance stats
docker compose exec db psql -U fcc -d fcc -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" 2>/dev/null || true
```

## Configuration Changes

### Update Environment Variables

```bash
# Edit .env file
nano .env  # or use your preferred editor

# Restart services to apply changes
docker compose restart web

# For database changes, may need rebuild
docker compose down
docker compose up -d
```

### Available Environment Variables

```bash
# Logging level (DEBUG, INFO, WARNING, ERROR)
FCC_LOG_LEVEL=INFO

# Map settings
FCC_MAP_DEFAULT_ZOOM=5
FCC_MAP_MAX_RESULTS=5000

# Cache (if Redis enabled)
FCC_REDIS_URL=redis://localhost:6379/0

# Database
FCC_DATABASE_URL=postgresql+asyncpg://fcc:fcc@db:5432/fcc
```

## Scheduler Management

### Check Next Scheduled Update

```bash
curl http://localhost:8000/api/admin/scheduler
```

Response example:
```json
{
  "running": true,
  "last_update": "2024-01-08T02:00:15Z",
  "last_error": null,
  "next_run": "2024-01-15T02:00:00Z"
}
```

### Manual Update

```bash
# Run incremental weekly update
docker compose exec web python -m app.cli import-update

# Full import (rare, use for data corruption recovery)
docker compose exec web python -m app.cli import-full
```

### Automatic Scheduler

The application automatically runs updates every Monday at 2 AM UTC. No manual action needed. Monitor with the endpoint above.

## Deployment Updates

### Update to New Version

```bash
# Backup database first!
docker compose exec db pg_dump -U fcc fcc > backup-before-upgrade.sql

# Stop services
docker compose down

# Update code (git pull or however you manage versions)
git pull origin main

# Rebuild and start
docker compose up -d --build

# Verify everything works
docker compose ps
curl http://localhost:8000/api/health
```

### Rollback After Update

```bash
# Stop services
docker compose down

# Restore database backup
docker compose up -d db
docker compose exec -T db psql -U fcc fcc < backup-before-upgrade.sql

# Restart web service
docker compose up -d web
```

## Useful Docker Commands

```bash
# List all containers
docker ps -a

# Stop all containers
docker compose down

# Remove all containers (WARNING: deletes data in volumes)
docker compose down -v

# View container resource usage
docker stats

# Execute command in container
docker compose exec web <command>

# View container's processes
docker compose top web

# Inspect container details
docker inspect <container_id>

# Get container IP address
docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <container_id>
```

## Emergency Procedures

### Emergency Stop

```bash
# Graceful stop (recommended)
docker compose down

# Force stop (if graceful doesn't work)
docker compose kill
```

### Emergency Data Recovery

```bash
# If database corrupted, restore from backup
docker compose down
docker compose up -d db
docker compose exec -T db psql -U fcc fcc < latest-backup.sql
docker compose up -d web

# If data files corrupted, reset data directory
docker volume rm fcc_db_data  # WARNING: Deletes all data
docker compose up -d
docker compose exec web python -m app.cli import-full
```

### Reset Everything

```bash
# WARNING: This deletes all data and containers
docker compose down -v
rm -rf ./data/
docker compose up -d --build
docker compose exec web python -m app.cli import-full
```

## Security

### Regular Security Updates

```bash
# Update base images
docker compose pull

# Rebuild containers with latest images
docker compose up -d --build

# Check for security vulnerabilities in Python packages
docker compose exec web pip audit
```

### Database Security

```bash
# Change database password
docker compose exec db psql -U fcc -d fcc -c "ALTER ROLE fcc WITH PASSWORD 'newpassword';"

# Then update .env with new password and restart
# FCC_DATABASE_URL=postgresql+asyncpg://fcc:newpassword@db:5432/fcc
docker compose restart web
```

## Support & Debugging

### Collect Debug Information

```bash
# Save all logs to file
docker compose logs > debug-logs-$(date +%Y%m%d-%H%M%S).txt

# System information
docker version
docker compose version
docker system df

# Configuration dump
docker compose config > config-dump.yml

# Create a debug report
cat > debug-report.txt << EOF
=== Docker ===
$(docker version)

=== Docker Compose ===
$(docker compose version)

=== Containers ===
$(docker compose ps -a)

=== Recent Logs ===
$(docker compose logs --tail=100)

=== Database ===
Container: $(docker compose ps db)
Size: $(docker compose exec -T db psql -U fcc -d fcc -c "SELECT pg_size_pretty(pg_database_size('fcc'));" 2>/dev/null)
Tables: $(docker compose exec -T db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM information_schema.tables;" 2>/dev/null)

=== API Health ===
$(curl -s http://localhost:8000/api/health)
EOF
```

