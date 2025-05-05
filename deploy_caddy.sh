#!/bin/bash
# deploy_caddy.sh - Script to deploy MCP Media Server with Caddy reverse proxy

set -e

# Configuration
DOMAIN="mcp.aitoolpool.com"
DNS_CHECK_TIMEOUT=30
HEALTH_CHECK_RETRIES=5
HEALTH_CHECK_DELAY=10
PROJECT_DIR=$(pwd)
CADDY_FILE="$PROJECT_DIR/Caddyfile"
DOCKER_COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# Display banner
echo "===================================================="
echo "  MCP Media Server Deployment with Caddy for $DOMAIN"
echo "===================================================="
echo "Starting deployment at $(date)"

# Check if DNS is properly configured
echo "Checking DNS configuration for $DOMAIN..."
if host $DOMAIN > /dev/null 2>&1; then
  echo "DNS check passed: $DOMAIN resolves correctly"
else
  echo "Warning: DNS check failed for $DOMAIN"
  echo "Make sure the domain points to your server's IP address"
  echo "Continuing deployment..."
fi

# Check directory structure
echo "Setting up directory structure..."
mkdir -p logs/caddy

# Create Caddyfile if it doesn't exist
if [ ! -f "$CADDY_FILE" ]; then
  echo "Creating Caddyfile..."
  cat > "$CADDY_FILE" << EOF
$DOMAIN {
    # Reverse proxy to MCP Media Server
    reverse_proxy mcp-server:9000 {
        # Health checks
        health_uri /health
        health_interval 30s
        health_timeout 10s
        health_status 200

        # Headers
        header_up Host {http.request.host}
        header_up X-Real-IP {http.request.remote}
        header_up X-Forwarded-For {http.request.remote}
        header_up X-Forwarded-Proto {http.request.scheme}
    }

    # Security headers
    header {
        # Remove server header
        -Server

        # Security headers
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"

        # Enable compression
        defer
    }

    # Enable logs
    log {
        output file /logs/caddy/access.log {
            roll_size 10mb
            roll_keep 10
        }
        format json
    }

    # Enable errors logs
    handle_errors {
        respond "Server error: {http.error.status_code}"
    }

    # TLS configuration (Caddy automatically manages SSL certificates)
    tls {
        protocols tls1.2 tls1.3
    }
}
EOF
  echo "Caddyfile created"
fi

# Create Dockerfile.monitor if it doesn't exist
if [ ! -f "$PROJECT_DIR/Dockerfile.monitor" ]; then
  echo "Creating Dockerfile.monitor..."
  cat > "$PROJECT_DIR/Dockerfile.monitor" << EOF
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \\
    apt-get install -y --no-install-recommends \\
    curl \\
    && apt-get clean \\
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PYTHONFAULTHANDLER=1

# Create non-root user
RUN groupadd -r mcp && useradd --no-log-init -r -g mcp mcp

# Create necessary directories
RUN mkdir -p /app/logs/caddy \\
    && chown -R mcp:mcp /app

# Copy monitoring script
COPY monitor_health.py /app/
COPY requirements-monitor.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements-monitor.txt

# Set the script as executable
RUN chmod +x /app/monitor_health.py

# Switch to non-root user
USER mcp

# Start the monitoring script
CMD ["python", "monitor_health.py", "--host", "mcp-server", "--port", "9000"]
EOF
  echo "Dockerfile.monitor created"
fi

# Create requirements-monitor.txt if it doesn't exist
if [ ! -f "$PROJECT_DIR/requirements-monitor.txt" ]; then
  echo "Creating requirements-monitor.txt..."
  cat > "$PROJECT_DIR/requirements-monitor.txt" << EOF
requests>=2.28.1
docker>=6.0.1
psutil>=5.9.4
pyyaml>=6.0
python-dotenv>=1.0.0
EOF
  echo "requirements-monitor.txt created"
fi

# Create or update monitor_health.py if needed
if [ ! -f "$PROJECT_DIR/monitor_health.py" ]; then
  echo "monitor_health.py not found! Please make sure it exists before continuing."
  exit 1
fi

# Verify or update docker-compose.yml
if [ -f "$DOCKER_COMPOSE_FILE" ]; then
  echo "Checking docker-compose.yml for Caddy configuration..."
  if ! grep -q "caddy:" "$DOCKER_COMPOSE_FILE"; then
    echo "Warning: Caddy service not found in docker-compose.yml"
    echo "Please update your docker-compose.yml to include the Caddy service"
    echo "See docker-compose-caddy.yml for an example"
  else
    echo "Caddy configuration found in docker-compose.yml"
  fi
else
  echo "docker-compose.yml not found! Please make sure it exists before continuing."
  exit 1
fi

# Build and start the containers
echo "Building and starting containers..."
docker-compose build
docker-compose up -d

# Wait for services to initialize
echo "Waiting for services to initialize..."
sleep 10

# Check if Caddy is running
echo "Checking Caddy status..."
if docker-compose ps | grep -q "caddy.*Up"; then
  echo "Caddy is running"
else
  echo "Error: Caddy is not running!"
  echo "Check logs with: docker-compose logs caddy"
  exit 1
fi

# Check if MCP server is running
echo "Checking MCP server status..."
if docker-compose ps | grep -q "mcp-server.*Up"; then
  echo "MCP server is running"
else
  echo "Error: MCP server is not running!"
  echo "Check logs with: docker-compose logs mcp-server"
  exit 1
fi

# Health check
echo "Performing health check..."
for i in $(seq 1 $HEALTH_CHECK_RETRIES); do
  echo "Health check attempt $i of $HEALTH_CHECK_RETRIES..."

  if curl -f -s "http://localhost:9000/health" > /dev/null; then
    echo "MCP server health check passed!"
    break
  elif [ $i -eq $HEALTH_CHECK_RETRIES ]; then
    echo "Health check failed after $HEALTH_CHECK_RETRIES attempts!"
    echo "Please check logs with: docker-compose logs mcp-server"
    echo "Deployment may be incomplete."
  else
    echo "Health check failed, retrying in $HEALTH_CHECK_DELAY seconds..."
    sleep $HEALTH_CHECK_DELAY
  fi
done

# Wait for Caddy to obtain SSL certificate
echo "Waiting for Caddy to obtain SSL certificate for $DOMAIN..."
echo "This may take a few minutes..."
sleep 30

# Final verification
echo "Verifying Caddy setup..."
if docker-compose logs caddy | grep -q "Certificate obtained successfully"; then
  echo "Success! Caddy obtained SSL certificate for $DOMAIN"
elif docker-compose logs caddy | grep -q "tls.handshake"; then
  echo "Caddy is serving TLS, but certificate status is unclear"
  echo "Please check with: docker-compose logs caddy | grep Certificate"
else
  echo "Warning: Could not verify SSL certificate status"
  echo "Please check with: docker-compose logs caddy"
fi

echo ""
echo "===================================================="
echo "Deployment Summary:"
echo "===================================================="
echo "MCP Media Server is now available at: https://$DOMAIN"
echo "API Documentation: https://$DOMAIN/docs"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f"
echo ""
echo "To check Caddy status:"
echo "  docker-compose logs caddy"
echo ""
echo "To stop services:"
echo "  docker-compose down"
echo ""
echo "Deployment completed at $(date)"
echo "===================================================="
