# Production Deployment Guide

Complete walkthrough for deploying to a production server.

## Prerequisites

### Server Requirements

- **OS**: Ubuntu 20.04+ LTS (or any modern Linux with Docker support)
- **CPU**: 2+ cores (4 cores recommended)
- **RAM**: 4GB minimum (8GB recommended)
- **Disk**: 100GB minimum (SSD recommended)
- **Network**: Static IP, DNS configured

### Software Requirements

- Docker: `20.10+`
- Docker Compose: `2.0+`
- Git (for version control)
- Backup tools (optional but recommended)

### Credentials/Certificates (if using HTTPS)

- SSL certificate and private key (from Let's Encrypt or your CA)
- Domain name (if using HTTPS)

## Step 1: Prepare Server

### 1.1 Install Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add current user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker compose version
```

### 1.2 Create Application Directory

```bash
# Create directory structure
sudo mkdir -p /opt/fcc-radio-map
sudo chown $USER:$USER /opt/fcc-radio-map
cd /opt/fcc-radio-map

# Create data directories
mkdir -p data/fcc_downloads
mkdir -p logs
mkdir -p backups
```

### 1.3 Configure Firewall

```bash
# Allow SSH, HTTP, HTTPS only
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS (if using SSL)
sudo ufw enable
```

## Step 2: Deploy Application

### 2.1 Clone/Copy Application

```bash
# Option A: Clone from git repository
git clone https://github.com/yourepo/fcc-radio-map.git /opt/fcc-radio-map

# Option B: Copy from local
scp -r ./* user@server:/opt/fcc-radio-map/
```

### 2.2 Configure Environment

```bash
cd /opt/fcc-radio-map

# Copy configuration template
cp .env.example .env

# Edit configuration with production values
nano .env
```

**Important settings to review/update**:

```bash
# Change database password!
FCC_DATABASE_URL=postgresql+asyncpg://fcc:CHANGE_ME_PASSWORD@db:5432/fcc
FCC_DATABASE_SYNC_URL=postgresql://fcc:CHANGE_ME_PASSWORD@db:5432/fcc

# Ensure debug is off
FCC_DEBUG=false

# Set log level (INFO for quiet, DEBUG for verbose)
FCC_LOG_LEVEL=INFO

# Configure allowed hosts (if you care about HTTP Host header validation)
# FCC_ALLOWED_HOSTS=your-domain.com

# Optional: Enable Redis caching
# FCC_REDIS_URL=redis://redis:6379/0
```

### 2.3 Update Docker Compose (Production Settings)

Edit `docker-compose.yml` to ensure production settings:

```yaml
services:
  web:
    # Ensure proper resource limits
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
    
    # Environment variables from .env
    env_file: .env
    
    # Restart policy
    restart: unless-stopped
    
    # Health check
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  db:
    # Similar health check and resource limits
    restart: unless-stopped
    # Persist data
    volumes:
      - db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U fcc"]
      interval: 30s
      timeout: 10s
      retries: 3

  nginx:
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"  # If using HTTPS
```

### 2.4 Configure Nginx for Production

If you want custom domain/SSL, edit `nginx.conf`:

```nginx
# HTTP to HTTPS redirect (if using SSL)
server {
    listen 80;
    return 301 https://$host$request_uri;
}

# Main server block
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    # SSL configuration
    ssl_certificate /etc/nginx/certs/cert.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    # Rest of configuration...
}
```

Then mount certificates in docker-compose.yml:

```yaml
  nginx:
    volumes:
      - ./certs:/etc/nginx/certs:ro
```

## Step 3: Initialize Application

### 3.1 Start Services

```bash
cd /opt/fcc-radio-map

# Build and start all containers
docker compose up -d --build

# Verify they're running
docker compose ps

# Should show:
# Container         Status
# fcc-db            Up (healthy)
# fcc-web           Up (healthy)
# fcc-nginx         Up (healthy)
```

### 3.2 Initial Data Import

Run the full import (this takes 30-60 minutes):

```bash
# Start import
docker compose exec web python -m app.cli import-full

# Monitor progress in another terminal
docker compose logs -f web | grep -i "progress\|error\|completed"
```

The import will:
1. Download FCC data (several GB)
2. Parse CSV files
3. Load into PostgreSQL
4. Build spatial indexes
5. Verify data integrity

### 3.3 Verify Deployment

```bash
# Test API endpoints
curl -s http://localhost:8000/api/health | jq .
curl -s http://localhost:8000/api/admin/scheduler | jq .

# Check logs for errors
docker compose logs web | grep ERROR || echo "No errors found"

# Check database has data
docker compose exec db psql -U fcc -d fcc -c "SELECT COUNT(*) FROM license;"
```

Response should show:
- `/api/health`: `"status": "healthy"`
- `/api/admin/scheduler`: Next update on Monday 2 AM
- `COUNT(*)`: Should be > 0 (millions of licenses)

## Step 4: Setup Monitoring & Backups

### 4.1 Automated Backups

Create backup script at `/opt/fcc-radio-map/backup.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/opt/fcc-radio-map/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/fcc_backup_$DATE.sql.gz"

# Create backup
cd /opt/fcc-radio-map
docker compose exec -T db pg_dump -U fcc fcc | gzip > "$BACKUP_FILE"

# Keep only last 30 days
find "$BACKUP_DIR" -name "fcc_backup_*.sql.gz" -mtime +30 -delete

echo "Backup created: $BACKUP_FILE"
```

Set it to run daily:

```bash
chmod +x backup.sh

# Add to crontab (runs daily at 2 AM)
(crontab -l 2>/dev/null; echo "0 2 * * * cd /opt/fcc-radio-map && ./backup.sh") | crontab -
```

### 4.2 Monitor Scheduler

```bash
# Check if running (should show task executing Monday 2 AM)
# Create a monitoring script at /opt/fcc-radio-map/check-health.sh

#!/bin/bash
echo "=== Health Check ==="
curl -s http://localhost:8000/api/health | jq '.'

echo -e "\n=== Scheduler Status ==="
curl -s http://localhost:8000/api/admin/scheduler | jq '.'

echo -e "\n=== Container Status ==="
docker compose ps

echo -e "\n=== Disk Usage ==="
df -h /opt/fcc-radio-map
```

Make it executable and run manually or automate:

```bash
chmod +x check-health.sh
./check-health.sh

# Add to cron (daily at 8 AM)
(crontab -l 2>/dev/null; echo "0 8 * * * /opt/fcc-radio-map/check-health.sh >> /opt/fcc-radio-map/logs/health-check.log 2>&1") | crontab -
```

## Step 5: Verify Scheduler

### 5.1 Check Next Update

```bash
# View scheduler status
curl http://localhost:8000/api/admin/scheduler

# Response will show:
{
  "running": true,
  "last_update": "2024-01-08T02:05:30.123456Z",
  "next_update": "2024-01-15T02:00:00Z",
  "last_error": null
}
```

### 5.2 Monitor First Update

Next Monday at 2 AM UTC, the scheduler will run automatically:

```bash
# Watch logs from Sunday night through Monday morning
# You can tail logs in tmux/screen session
screen -S log-monitor
docker compose logs -f web | grep -i "update\|scheduler\|import"

# Monday ~2 AM you'll see:
# Starting scheduled FCC weekly update...
# ... downloading files ...
# ... loading data ...
# FCC weekly update completed successfully
```

## Step 6: SSL Certificate Setup (Optional)

### 6.1 Get Let's Encrypt Certificate

```bash
# Install Certbot
sudo apt-get install certbot

# Get certificate
sudo certbot certonly --standalone \
  -d your-domain.com \
  -d www.your-domain.com

# Certificates will be in /etc/letsencrypt/live/your-domain.com/
```

### 6.2 Mount in Docker

Create `/opt/fcc-radio-map/certs` directory and update docker-compose.yml:

```yaml
nginx:
  volumes:
    - /etc/letsencrypt/live/your-domain.com:/certs:ro
```

Update nginx.conf:

```nginx
ssl_certificate /certs/fullchain.pem;
ssl_certificate_key /certs/privkey.pem;
```

Restart:

```bash
docker compose restart nginx
```

### 6.3 Auto-Renew Certificate

```bash
# Create renewal hook to restart nginx
sudo mkdir -p /etc/letsencrypt/renewal-hooks/post
sudo cat > /etc/letsencrypt/renewal-hooks/post/docker-restart.sh << 'EOF'
#!/bin/bash
cd /opt/fcc-radio-map
docker compose restart nginx
EOF

sudo chmod +x /etc/letsencrypt/renewal-hooks/post/docker-restart.sh

# Test renewal
sudo certbot renew --dry-run

# Certbot will auto-renew via cron
```

## Step 7: Production Hardening

### 7.1 Update System

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get autoremove -y
```

### 7.2 Configure Log Rotation

Create `/etc/logrotate.d/fcc-radio`:

```
/opt/fcc-radio-map/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ${USER} ${USER}
}
```

### 7.3 Set Auto-Startup

```bash
# Services restart automatically on reboot
# Verify in docker-compose.yml: restart: unless-stopped

# Reboot to test
sudo reboot

# After reboot, verify containers restarted
docker compose ps
```

## Step 8: Domain Setup (If Using Custom Domain)

### 8.1 DNS Configuration

Update your DNS provider:

```
A record: your-domain.com -> your-server-ip
```

### 8.2 Test Domain

```bash
# Once DNS propagates (5-60 minutes)
curl http://your-domain.com
curl http://your-domain.com/api/health
```

## Checklist: Production Deployment Complete

- [ ] Server provisioned and firewall configured
- [ ] Application cloned/copied to `/opt/fcc-radio-map`
- [ ] `.env` configured with production settings
- [ ] Database password changed from default
- [ ] Initial data imported successfully
- [ ] `/api/health` returning "healthy"
- [ ] `/api/admin/scheduler` shows upcoming Monday run
- [ ] Database backup script created and tested
- [ ] Health check script created
- [ ] Logs monitored for errors
- [ ] SSL certificate installed (if using HTTPS)
- [ ] Favicon and custom domain configured (if desired)
- [ ] Auto-restart on reboot verified
- [ ] Cron jobs for backup and monitoring scheduled

## Troubleshooting

### Containers Won't Start

```bash
docker compose logs
# Check for port conflicts, insufficient disk space, or permission issues
```

### High Memory Usage

```bash
docker stats
# Restart web service if needed
docker compose restart web
```

### Scheduler Not Running

```bash
docker compose logs web | grep -i scheduler

# If needed, manually trigger update
docker compose exec web python -m app.cli import-update
```

### SSL Certificate Issues

```bash
# Check certificate
docker compose exec nginx openssl s_client -connect localhost:443

# Renew manually
sudo certbot renew --force-renewal
```

## Maintenance Schedule

- **Daily**: Automated backup runs at 2 AM, health check at 8 AM
- **Weekly**: Manual verification of scheduler status
- **Monthly**: Review disk usage, clean old backups
- **Quarterly**: Update Docker images and OS packages
- **Annually**: Renew SSL certificate (auto-renewed before expiration)

## Going Live

Once everything is verified:

1. Update DNS to point to production server
2. Monitor logs for first 24 hours
3. Verify scheduler runs on next Monday 2 AM UTC
4. Set up monitoring alerts (optional - PagerDuty, Uptime Robot, etc.)
5. Document any customizations made

## Support & Recovery

### Full System Recovery from Backup

```bash
# Stop services
docker compose down

# Restore database
docker compose up -d db
docker compose exec -T db psql -U fcc fcc < backups/fcc_backup_YYYYMMDD_HHMMSS.sql

# Start web service
docker compose up -d web
```

### Quick Restart (if stuck/frozen)

```bash
docker compose restart

# Or harder restart
docker compose down
docker compose up -d
```

---

Your production deployment is now live and will automatically update with the latest FCC data every Monday at 2 AM UTC!

For ongoing operations, see [OPERATIONS.md](OPERATIONS.md).

