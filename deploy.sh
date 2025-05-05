#!/bin/bash
# deploy.sh - Enhanced deployment script with health verification

set -e

# Configuration
DOCKER_COMPOSE_FILE="docker-compose.yml"
HEALTH_CHECK_URL="http://localhost:9000/health"
HEALTH_CHECK_RETRIES=10
HEALTH_CHECK_DELAY=10

# Function to check server health
check_health() {
  for i in $(seq 1 $HEALTH_CHECK_RETRIES); do
    echo "Health check attempt $i of $HEALTH_CHECK_RETRIES..."

    HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_CHECK_URL)

    if [ $HEALTH_STATUS -eq 200 ]; then
      echo "Health check passed!"
      return 0
    else
      echo "Health check failed with status: $HEALTH_STATUS"
      echo "Waiting $HEALTH_CHECK_DELAY seconds before retry..."
      sleep $HEALTH_CHECK_DELAY
    fi
  done

  echo "Health check failed after $HEALTH_CHECK_RETRIES attempts!"
  return 1
}

# Display deployment banner
echo "===================================================="
echo "        MCP Media Server Deployment Script          "
echo "===================================================="
echo "Starting deployment at $(date)"

# Create backup before deployment
echo "Creating backup before deployment..."
./create_backup.sh pre-deploy

# Pull latest changes
echo "Pulling latest changes from repository..."
git pull

# Build the new containers
echo "Building containers..."
docker-compose -f $DOCKER_COMPOSE_FILE build

# Stop and remove existing containers
echo "Stopping existing containers..."
docker-compose -f $DOCKER_COMPOSE_FILE down

# Start the containers
echo "Starting containers..."
docker-compose -f $DOCKER_COMPOSE_FILE up -d

# Check server health
echo "Checking server health..."
if check_health; then
  echo "Deployment successful!"
else
  echo "Deployment failed! Rolling back..."

  # Roll back to the previous version
  echo "Rolling back to previous version..."
  git reset --hard HEAD~1

  # Rebuild and restart
  docker-compose -f $DOCKER_COMPOSE_FILE build
  docker-compose -f $DOCKER_COMPOSE_FILE up -d

  # Check health after rollback
  echo "Checking health after rollback..."
  if check_health; then
    echo "Rollback successful!"
  else
    echo "WARNING: Rollback failed! Manual intervention required."
  fi

  exit 1
fi

# Show running containers
echo "Running containers:"
docker-compose -f $DOCKER_COMPOSE_FILE ps

echo "Deployment completed at $(date)"
