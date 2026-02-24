#!/bin/bash
# Validation script for FCC Radio License Map production deployment
# Verifies all components are correctly configured and operational

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
WARN=0
FAIL=0

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASS++))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((WARN++))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAIL++))
}

echo -e "${BLUE}=== FCC Radio License Map - Production Validation ===${NC}\n"

# Check 1: Environment File
echo -e "${BLUE}[1/10] Configuration Files${NC}"
if [ -f .env ]; then
    check_pass ".env file exists"
    
    # Check critical settings
    if grep -q "FCC_DEBUG=false" .env; then
        check_pass "FCC_DEBUG is disabled (not debug mode)"
    else
        check_warn "FCC_DEBUG may not be explicitly set to false"
    fi
    
    if grep -q "FCC_DATABASE_URL" .env; then
        if grep -q "fcc:fcc@" .env; then
            check_warn "Database password is still default (change in production)"
        else
            check_pass "Database password appears custom"
        fi
    else
        check_fail "FCC_DATABASE_URL not configured"
    fi
else
    check_fail ".env file missing"
fi

if [ -f docker-compose.yml ]; then
    check_pass "docker-compose.yml exists"
else
    check_fail "docker-compose.yml missing"
fi

echo ""

# Check 2: Docker & Docker Compose
echo -e "${BLUE}[2/10] Docker Installation${NC}"
if command -v docker &> /dev/null; then
    VERSION=$(docker --version)
    check_pass "Docker installed: $VERSION"
else
    check_fail "Docker not installed"
fi

if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    VERSION=$(docker compose version 2>/dev/null || docker-compose --version)
    check_pass "Docker Compose installed: $VERSION"
else
    check_fail "Docker Compose not installed"
fi

echo ""

# Check 3: Application Files
echo -e "${BLUE}[3/10] Application Files${NC}"
REQUIRED_FILES=(
    "app/main.py"
    "app/config.py"
    "app/scheduler.py"
    "app/routes/api.py"
    "app/routes/web.py"
    "app/models.py"
    "pyproject.toml"
    "Dockerfile"
    "nginx.conf"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        check_pass "$file exists"
    else
        check_fail "$file missing"
    fi
done

echo ""

# Check 4: Container Status
echo -e "${BLUE}[4/10] Container Status${NC}"
if docker compose ps 2>/dev/null | grep -q "postgres\|web\|nginx"; then
    check_pass "Docker Compose can list containers"
    
    if docker compose ps 2>/dev/null | grep -q "Up"; then
        check_pass "At least one container is running"
        
        # Check each service
        if docker compose ps 2>/dev/null | grep postgres | grep -q "Up"; then
            check_pass "PostgreSQL container is running"
        else
            check_warn "PostgreSQL container not running"
        fi
        
        if docker compose ps 2>/dev/null | grep web | grep -q "Up"; then
            check_pass "Web container is running"
        else
            check_warn "Web container not running"
        fi
        
        if docker compose ps 2>/dev/null | grep nginx | grep -q "Up"; then
            check_pass "Nginx container is running"
        else
            check_warn "Nginx container not running"
        fi
    else
        check_warn "No containers are running (run 'docker compose up -d')"
    fi
else
    check_warn "Cannot connect to Docker daemon"
fi

echo ""

# Check 5: API Endpoints
echo -e "${BLUE}[5/10] API Endpoints${NC}"
if docker compose ps 2>/dev/null | grep web | grep -q "Up"; then
    # Check health endpoint
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        HEALTH=$(curl -s http://localhost:8000/api/health)
        if echo "$HEALTH" | grep -q "healthy\|unhealthy"; then
            check_pass "Health endpoint responds"
            if echo "$HEALTH" | grep -q '"database".*"connected"'; then
                check_pass "Database connection verified"
            else
                check_warn "Database may not be connected"
            fi
        fi
    else
        check_warn "Health endpoint not responding"
    fi
    
    # Check web interface
    if curl -s http://localhost:8000/ | grep -q "FCC\|Radio\|License"; then
        check_pass "Web interface is accessible"
    else
        check_warn "Web interface may not be loading correctly"
    fi
    
    # Check scheduler endpoint
    if curl -s http://localhost:8000/api/admin/scheduler > /dev/null 2>&1; then
        SCHEDULER=$(curl -s http://localhost:8000/api/admin/scheduler)
        check_pass "Scheduler endpoint responds"
        if echo "$SCHEDULER" | grep -q "next_update\|next_run"; then
            check_pass "Scheduler has next update scheduled"
        fi
    else
        check_warn "Scheduler endpoint not responding"
    fi
else
    check_warn "Web container not running - cannot test endpoints"
fi

echo ""

# Check 6: Database Data
echo -e "${BLUE}[6/10] Database Data${NC}"
if docker compose ps 2>/dev/null | grep postgres | grep -q "Up"; then
    COUNT=$(docker compose exec -T db psql -U fcc -d fcc -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d ' ' || echo "0")
    
    if [ "$COUNT" -gt 0 ]; then
        check_pass "Database has $COUNT tables"
        
        LICENSE_COUNT=$(docker compose exec -T db psql -U fcc -d fcc -t -c "SELECT COUNT(*) FROM license;" 2>/dev/null | tr -d ' ' || echo "0")
        if [ "$LICENSE_COUNT" -gt 0 ]; then
            check_pass "Database contains $LICENSE_COUNT licenses"
        else
            check_warn "No licenses in database - run import?"
        fi
    else
        check_warn "Database appears empty"
    fi
else
    check_warn "Database container not running"
fi

echo ""

# Check 7: Storage & Resources
echo -e "${BLUE}[7/10] Storage & Resources${NC}"
if [ -d "./data" ]; then
    SIZE=$(du -sh ./data 2>/dev/null | cut -f1)
    check_pass "Data directory exists: $SIZE"
else
    check_warn "Data directory missing"
fi

if [ -d "./logs" ]; then
    SIZE=$(du -sh ./logs 2>/dev/null | cut -f1)
    check_pass "Logs directory exists: $SIZE"
else
    check_warn "Logs directory missing"
fi

# Check disk space
DISK_AVAIL=$(df . 2>/dev/null | tail -1 | awk '{print int($4/1024/1024)"GB"}' || echo "unknown")
check_pass "Disk available: $DISK_AVAIL"

echo ""

# Check 8: Security Configuration
echo -e "${BLUE}[8/10] Security Configuration${NC}"
if [ -f nginx.conf ]; then
    if grep -q "ssl_certificate\|listen 443" nginx.conf; then
        check_pass "SSL/TLS appears to be configured in nginx"
    else
        check_warn "SSL/TLS not configured (consider enabling for production)"
    fi
    
    if grep -q "proxy_pass.*8000\|upstream web" nginx.conf; then
        check_pass "Nginx reverse proxy configured"
    else
        check_fail "Nginx proxy not configured"
    fi
else
    check_fail "nginx.conf missing"
fi

if grep -q "FCC_DEBUG=false" .env 2>/dev/null; then
    check_pass "Debug mode disabled"
else
    check_warn "Debug mode not explicitly disabled"
fi

echo ""

# Check 9: Logging & Monitoring
echo -e "${BLUE}[9/10] Logging & Monitoring${NC}"
if [ -f "OPERATIONS.md" ]; then
    check_pass "Operations guide available"
else
    check_warn "OPERATIONS.md not found"
fi

if [ -f "deployment-checklist.md" ]; then
    check_pass "Deployment checklist available"
else
    check_warn "deployment-checklist.md not found"
fi

if [ -f "PRODUCTION_READY.md" ]; then
    check_pass "Production documentation available"
else
    check_warn "PRODUCTION_READY.md not found"
fi

if [ -f "deploy.sh" ]; then
    check_pass "Deploy script available"
else
    check_warn "deploy.sh not found"
fi

echo ""

# Check 10: Documentation
echo -e "${BLUE}[10/10] Documentation${NC}"
if [ -f "README.md" ]; then
    if grep -q "Production\|production\|deploy" README.md; then
        check_pass "README.md includes production information"
    else
        check_warn "README.md may not include production information"
    fi
else
    check_fail "README.md missing"
fi

echo ""

# Summary
echo -e "${BLUE}=== Validation Summary ===${NC}"
TOTAL=$((PASS + WARN + FAIL))
echo "Passed:  ${GREEN}$PASS${NC}"
echo "Warnings: ${YELLOW}$WARN${NC}"
echo "Failed:  ${RED}$FAIL${NC}"
echo "Total:   $TOTAL"

echo ""

if [ $FAIL -eq 0 ]; then
    if [ $WARN -eq 0 ]; then
        echo -e "${GREEN}✓ Validation passed - Application is production-ready!${NC}"
        exit 0
    else
        echo -e "${YELLOW}⚠ Validation passed with warnings - Review above${NC}"
        exit 0
    fi
else
    echo -e "${RED}✗ Validation failed - Fix errors above before deploying${NC}"
    exit 1
fi

