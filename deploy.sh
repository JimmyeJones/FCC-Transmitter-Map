#!/bin/bash
# Deploy and monitor FCC Radio License Map

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
DOCKER_COMPOSE="docker compose"
TIMEOUT_SECONDS=300  # 5 minutes for import to complete

log_info() {
    echo -e "${GREEN}ℹ${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install Docker."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose not found. Please install Docker Compose."
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

check_env_file() {
    if [ ! -f .env ]; then
        log_warn ".env file not found. Creating from .env.example..."
        if [ -f .env.example ]; then
            cp .env.example .env
            log_info "Please edit .env with your production settings"
        else
            log_error ".env.example not found"
            exit 1
        fi
    else
        log_success ".env file exists"
    fi
}

build_and_start() {
    log_info "Building and starting containers..."
    $DOCKER_COMPOSE down --remove-orphans 2>/dev/null || true
    $DOCKER_COMPOSE up -d --build
    
    # Wait for services to be healthy
    log_info "Waiting for services to become healthy..."
    local elapsed=0
    while [ $elapsed -lt $TIMEOUT_SECONDS ]; do
        if $DOCKER_COMPOSE exec -T web curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
            log_success "Services are ready"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        echo -n "."
    done
    
    log_error "Services failed to start within $TIMEOUT_SECONDS seconds"
    $DOCKER_COMPOSE logs
    exit 1
}

check_data() {
    log_info "Checking database data..."
    local count=$($DOCKER_COMPOSE exec -T db psql -U fcc -d fcc -t -c "SELECT COUNT(*) FROM license;" 2>/dev/null || echo "0")
    
    if [ "$count" -eq 0 ]; then
        log_warn "No license data found in database"
        read -p "Import FCC data now? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            import_data
        fi
    else
        log_success "Database contains $count licenses"
    fi
}

import_data() {
    log_info "Starting FCC data import (this may take 30-60 minutes)..."
    log_warn "Keep this window open until import completes"
    
    $DOCKER_COMPOSE exec web python -m app.cli import-full
    
    local new_count=$($DOCKER_COMPOSE exec -T db psql -U fcc -d fcc -t -c "SELECT COUNT(*) FROM license;" 2>/dev/null)
    log_success "Import complete. Database now contains $new_count licenses"
}

verify_deployment() {
    log_info "Verifying deployment..."
    echo ""
    
    # Check containers
    log_info "Container status:"
    $DOCKER_COMPOSE ps
    echo ""
    
    # Check health endpoint
    log_info "Checking health endpoint..."
    if health_response=$($DOCKER_COMPOSE exec -T web curl -s http://localhost:8000/api/health); then
        echo "  $health_response" | head -1  # Show first line
        log_success "API health check passed"
    else
        log_error "API health check failed"
    fi
    echo ""
    
    # Check scheduler
    log_info "Checking scheduler status..."
    if scheduler_response=$($DOCKER_COMPOSE exec -T web curl -s http://localhost:8000/api/admin/scheduler); then
        log_success "Scheduler status retrieved"
        echo "  Next update: $(echo "$scheduler_response" | grep -o '"next_run":"[^"]*"' | cut -d'"' -f4)"
    fi
    echo ""
    
    log_success "Deployment verified successfully!"
    log_info "View at: http://localhost:8000"
}

view_logs() {
    log_info "Displaying recent logs (Ctrl+C to exit)..."
    $DOCKER_COMPOSE logs -f --tail=50
}

main() {
    cat > /dev/null << "EOF"
     _____ _____ _____  ____  _____  _____ 
    |  ___|  __ \|  __ \|  _ \|  __ \|  _  |
    | |_  | |  \/| |  \\| | | | |  \\| | | |
    |  _| | | __ | | __/| | | | | __ | | | |
    | |   | |_\_ | | | _ | |_| | |_\_ | |_| |
    |_|    \_____|_| (_)|____/|______|_____/
    
    Radio License Map - Production Deployment
EOF
    
    echo ""
    
    case "${1:-}" in
        "check")
            check_prerequisites
            check_env_file
            verify_deployment
            ;;
        "import")
            import_data
            ;;
        "logs")
            view_logs
            ;;
        "deploy")
            check_prerequisites
            check_env_file
            build_and_start
            check_data
            verify_deployment
            echo ""
            log_info "Deployment complete!"
            log_info "Application running at: http://localhost:8000"
            ;;
        *)
            echo "Usage: $0 COMMAND"
            echo ""
            echo "Commands:"
            echo "  deploy    - Full deployment (build, start, verify)"
            echo "  check     - Verify deployment status"
            echo "  import    - Import FCC data"
            echo "  logs      - View application logs"
            echo ""
            echo "Example:"
            echo "  $0 deploy     # First time deployment"
            echo "  $0 check      # Verify deployment is healthy"
            echo "  $0 import     # Import data manually"
            echo "  $0 logs       # View live logs"
            exit 1
            ;;
    esac
}

main "$@"

