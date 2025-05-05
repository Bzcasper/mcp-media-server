#!/bin/bash
# MCP Media Server monitoring script

# Log file path
LOG_FILE="/app/logs/monitor.log"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# Function to log messages
log() {
  echo "[$TIMESTAMP] $1" >> $LOG_FILE
  echo "[$TIMESTAMP] $1"
}

# Check if the service is healthy
log "Checking MCP Media Server health..."
response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9000/health)

if [ $response -ne 200 ]; then
  log "MCP Media Server is not responding (status code: $response). Restarting..."

  # Check if the container is running
  if docker ps | grep -q mcp-media-server; then
    log "Container is running but unresponsive. Restarting container..."
    docker-compose restart mcp-server
  else
    log "Container is not running. Starting service..."
    docker-compose up -d
  fi

  # Wait for the service to restart
  sleep 10

  # Check if restart was successful
  new_response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9000/health)
  if [ $new_response -eq 200 ]; then
    log "Service successfully restarted."
  else
    log "Service restart failed. Manual intervention required."
    # Uncomment to send email notification
    # mail -s "MCP Media Server restart failed" your@email.com
  fi
else
  log "MCP Media Server is healthy."

  # Check resource usage
  cpu_usage=$(docker stats mcp-media-server --no-stream --format "{{.CPUPerc}}")
  mem_usage=$(docker stats mcp-media-server --no-stream --format "{{.MemUsage}}")
  log "Resource usage - CPU: $cpu_usage, Memory: $mem_usage"
fi
