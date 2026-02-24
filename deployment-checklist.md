# Production Deployment Checklist

Use this checklist to verify your FCC Radio License Map deployment is production-ready.

## Pre-Deployment

- [ ] **Review Configuration**
  - [ ] `.env` file created with production settings
  - [ ] Database credentials secured (avoid default values)
  - [ ] `FCC_DEBUG=false` set in production
  - [ ] `FCC_LOG_LEVEL` appropriate for production (INFO recommended)

- [ ] **Resource Allocation**
  - [ ] Server has at least 2GB RAM available
  - [ ] Server has at least 50GB free disk space
  - [ ] Docker daemon configured with sufficient disk space

- [ ] **Network & Security**
  - [ ] Firewall configured (ports 80/443 open to internet, 5432 NOT open)
  - [ ] SSL certificate obtained (if using HTTPS)
  - [ ] Nginx configuration reviewed for security headers

## Deployment

- [ ] **Build & Start**
  ```bash
  docker compose down  # Clean state if upgrading
  docker compose up -d --build
  ```
  - [ ] All containers start successfully
  - [ ] No errors in `docker compose logs`

- [ ] **Initial Data Import** (First deployment only)
  ```bash
  docker compose exec web python -m app.cli import-full
  ```
  - [ ] Import completes without errors (may take 30-60 minutes)
  - [ ] Check progress: `docker compose logs -f web`

## Post-Deployment Verification

- [ ] **Health Checks**
  - [ ] Web app responds: `curl http://localhost:8000/`
  - [ ] API health: `curl http://localhost:8000/api/health`
    - Status should be: `"status": "healthy"`
    - Database should show: `"database": "connected"`
  - [ ] Scheduler status: `curl http://localhost:8000/api/admin/scheduler`
    - Should show next run time for Monday 2 AM UTC

- [ ] **Map Data**
  - [ ] Visit http://localhost:8000/ in browser
  - [ ] **Browse page**: States load and show license counts
  - [ ] **Map page**: Markers appear when zoomed in to at least zoom level 5
  - [ ] **State filter**: Works and preserves URL parameters
  - [ ] **Status filter**: "Active" vs "Expired" licenses display correctly

- [ ] **Search Features**
  - [ ] License search works (e.g., search for "K1ZZZ")
  - [ ] Frequency search works (e.g., frequency 146.520)
  - [ ] County view displays licenses properly
  - [ ] Pagination works on large result sets

- [ ] **Container Health**
  ```bash
  docker compose ps
  ```
  - [ ] All containers show "healthy" or "running"
  - [ ] Web container status: `Up (healthy)`
  - [ ] Database container status: `Up (healthy)`
  - [ ] Nginx container status: `Up (healthy)`

- [ ] **Logs Monitoring**
  ```bash
  docker compose logs --tail=50 web
  ```
  - [ ] No ERROR level messages
  - [ ] Scheduler shows: "FCC scheduler started"
  - [ ] Application is listening on configured port

- [ ] **Database Verification**
  ```bash
  docker compose exec db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM license;"
  ```
  - [ ] Returns count > 0 (number of licenses in database)

## Ongoing Operations

- [ ] **Monitor Scheduler**
  - [ ] Check `/api/admin/scheduler` weekly
  - [ ] Verify no errors since last run
  - [ ] Confirm next_run shows future Monday 2 AM

- [ ] **Log Management**
  - [ ] Monitor logs for recurring ERROR patterns
  - [ ] Adjust `FCC_LOG_LEVEL` if logs too verbose (change to WARNING)

- [ ] **Backup Strategy Implemented**
  - [ ] Regular database backups configured
  - [ ] Backup location and retention policy documented
  - [ ] Test backup/restore procedure

- [ ] **Performance Monitoring**
  - [ ] Monitor page load times (should be < 2 seconds typically)
  - [ ] Check Docker resource usage: `docker stats`
  - [ ] Monitor disk space usage: `df -h`

## HTTPS Setup (If Required)

- [ ] SSL certificate placed in `/etc/nginx/certs/`
- [ ] `nginx.conf` updated with SSL configuration
- [ ] docker-compose.yml updated to expose port 443
- [ ] HTTP redirects to HTTPS (port 80 → 443)
- [ ] Certificate renewal strategy documented

## Troubleshooting Reference

### Container won't start
```bash
# Check specific container logs
docker compose logs web

# Verify configuration
docker compose config

# Try rebuilding
docker compose down
docker compose up -d --build
```

### No data displaying
```bash
# Check database connection
curl http://localhost:8000/api/health

# Verify data was imported
docker compose exec db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM license;"

# If count is 0, run: docker compose exec web python -m app.cli import-full
```

### High memory usage
```bash
# Check resource usage
docker stats

# Restart container
docker compose restart web
```

### Scheduler not running
```bash
# Check scheduler status
curl http://localhost:8000/api/admin/scheduler

# Check logs
docker compose logs web | grep -i scheduler
```

## Upgrade Procedure

When upgrading to a new version:

1. Backup database: `docker compose exec db pg_dump -U fcc fcc > backup-$(date +%Y%m%d).sql`
2. Stop containers: `docker compose down`
3. Update code/docker-compose.yml
4. Build and start: `docker compose up -d --build`
5. Verify health: `curl http://localhost:8000/api/health`
6. Check logs: `docker compose logs web`

## Success Criteria

Your deployment is production-ready when:

✓ All containers healthy and running  
✓ `/api/health` returns `"status": "healthy"`  
✓ Web interface loads and displays data correctly  
✓ Scheduler reports upcoming Monday 2 AM run  
✓ Most recent logs show no ERROR messages  
✓ Database has license data (count > 0)  
✓ Backup strategy implemented and tested  

