@echo off
:: deploy_caddy.bat - Script to deploy MCP Media Server with Caddy reverse proxy on Windows

echo ====================================================
echo   MCP Media Server Deployment with Caddy
echo ====================================================
echo Starting deployment at %date% %time%

:: Configuration
set DOMAIN=mcp.aitoolpool.com
set HEALTH_CHECK_RETRIES=5
set HEALTH_CHECK_DELAY=10
set PROJECT_DIR=%cd%
set CADDY_FILE=%PROJECT_DIR%\Caddyfile
set DOCKER_COMPOSE_FILE=%PROJECT_DIR%\docker-compose.yml

:: Check directory structure
echo Setting up directory structure...
if not exist logs\caddy mkdir logs\caddy

:: Create Caddyfile if it doesn't exist
if not exist "%CADDY_FILE%" (
    echo Creating Caddyfile...
    (
        echo %DOMAIN% {
        echo     # Reverse proxy to MCP Media Server
        echo     reverse_proxy mcp-server:9000 {
        echo         # Health checks
        echo         health_uri /health
        echo         health_interval 30s
        echo         health_timeout 10s
        echo         health_status 200
        echo
        echo         # Headers
        echo         header_up Host {http.request.host}
        echo         header_up X-Real-IP {http.request.remote}
        echo         header_up X-Forwarded-For {http.request.remote}
        echo         header_up X-Forwarded-Proto {http.request.scheme}
        echo     }
        echo
        echo     # Security headers
        echo     header {
        echo         # Remove server header
        echo         -Server
        echo
        echo         # Security headers
        echo         Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        echo         X-Content-Type-Options "nosniff"
        echo         X-Frame-Options "SAMEORIGIN"
        echo         Referrer-Policy "strict-origin-when-cross-origin"
        echo         Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        echo
        echo         # Enable compression
        echo         defer
        echo     }
        echo
        echo     # Enable logs
        echo     log {
        echo         output file /logs/caddy/access.log {
        echo             roll_size 10mb
        echo             roll_keep 10
        echo         }
        echo         format json
        echo     }
        echo
        echo     # Enable errors logs
        echo     handle_errors {
        echo         respond "Server error: {http.error.status_code}"
        echo     }
        echo
        echo     # TLS configuration (Caddy automatically manages SSL certificates)
        echo     tls {
        echo         protocols tls1.2 tls1.3
        echo     }
        echo }
    ) > "%CADDY_FILE%"
    echo Caddyfile created
)

:: Create Dockerfile.monitor if it doesn't exist
if not exist "%PROJECT_DIR%\Dockerfile.monitor" (
    echo Creating Dockerfile.monitor...
    (
        echo FROM python:3.10-slim
        echo.
        echo # Set working directory
        echo WORKDIR /app
        echo.
        echo # Install system dependencies
        echo RUN apt-get update ^&^& \
        echo     apt-get install -y --no-install-recommends \
        echo     curl \
        echo     ^&^& apt-get clean \
        echo     ^&^& rm -rf /var/lib/apt/lists/*
        echo.
        echo # Set environment variables
        echo ENV PYTHONDONTWRITEBYTECODE=1 \
        echo     PYTHONUNBUFFERED=1 \
        echo     PYTHONFAULTHANDLER=1
        echo.
        echo # Create non-root user
        echo RUN groupadd -r mcp ^&^& useradd --no-log-init -r -g mcp mcp
        echo.
        echo # Create necessary directories
        echo RUN mkdir -p /app/logs/caddy \
        echo     ^&^& chown -R mcp:mcp /app
        echo.
        echo # Copy monitoring script
        echo COPY monitor_health.py /app/
        echo COPY requirements-monitor.txt /app/
        echo.
        echo # Install Python dependencies
        echo RUN pip install --no-cache-dir -r requirements-monitor.txt
        echo.
        echo # Set the script as executable
        echo RUN chmod +x /app/monitor_health.py
        echo.
        echo # Switch to non-root user
        echo USER mcp
        echo.
        echo # Start the monitoring script
        echo CMD ["python", "monitor_health.py", "--host", "mcp-server", "--port", "9000"]
    ) > "%PROJECT_DIR%\Dockerfile.monitor"
    echo Dockerfile.monitor created
)

:: Create requirements-monitor.txt if it doesn't exist
if not exist "%PROJECT_DIR%\requirements-monitor.txt" (
    echo Creating requirements-monitor.txt...
    (
        echo requests>=2.28.1
        echo docker>=6.0.1
        echo psutil>=5.9.4
        echo pyyaml>=6.0
        echo python-dotenv>=1.0.0
    ) > "%PROJECT_DIR%\requirements-monitor.txt"
    echo requirements-monitor.txt created
)

:: Check for monitor_health.py
if not exist "%PROJECT_DIR%\monitor_health.py" (
    echo monitor_health.py not found! Please make sure it exists before continuing.
    exit /b 1
)

:: Verify docker-compose.yml
if exist "%DOCKER_COMPOSE_FILE%" (
    echo Checking docker-compose.yml for Caddy configuration...
    findstr /c:"caddy:" "%DOCKER_COMPOSE_FILE%" >nul
    if errorlevel 1 (
        echo Warning: Caddy service not found in docker-compose.yml
        echo Please update your docker-compose.yml to include the Caddy service
        echo See docker-compose-caddy.yml for an example
    ) else (
        echo Caddy configuration found in docker-compose.yml
    )
) else (
    echo docker-compose.yml not found! Please make sure it exists before continuing.
    exit /b 1
)

:: Build and start the containers
echo Building and starting containers...
docker-compose build
docker-compose up -d

:: Wait for services to initialize
echo Waiting for services to initialize...
timeout /t 10 /nobreak >nul

:: Check if Caddy is running
echo Checking Caddy status...
docker-compose ps | findstr /c:"caddy" | findstr /c:"Up" >nul
if errorlevel 1 (
    echo Error: Caddy is not running!
    echo Check logs with: docker-compose logs caddy
    exit /b 1
) else (
    echo Caddy is running
)

:: Check if MCP server is running
echo Checking MCP server status...
docker-compose ps | findstr /c:"mcp-server" | findstr /c:"Up" >nul
if errorlevel 1 (
    echo Error: MCP server is not running!
    echo Check logs with: docker-compose logs mcp-server
    exit /b 1
) else (
    echo MCP server is running
)

:: Health check
echo Performing health check...
for /l %%i in (1, 1, %HEALTH_CHECK_RETRIES%) do (
    echo Health check attempt %%i of %HEALTH_CHECK_RETRIES%...

    curl -f -s "http://localhost:9000/health" >nul 2>&1
    if not errorlevel 1 (
        echo MCP server health check passed!
        goto :health_check_done
    ) else if %%i equ %HEALTH_CHECK_RETRIES% (
        echo Health check failed after %HEALTH_CHECK_RETRIES% attempts!
        echo Please check logs with: docker-compose logs mcp-server
        echo Deployment may be incomplete.
    ) else (
        echo Health check failed, retrying in %HEALTH_CHECK_DELAY% seconds...
        timeout /t %HEALTH_CHECK_DELAY% /nobreak >nul
    )
)

:health_check_done

:: Wait for Caddy to obtain SSL certificate
echo Waiting for Caddy to obtain SSL certificate for %DOMAIN%...
echo This may take a few minutes...
timeout /t 30 /nobreak >nul

:: Final verification
echo Verifying Caddy setup...
docker-compose logs caddy | findstr /c:"Certificate obtained successfully" >nul
if not errorlevel 1 (
    echo Success! Caddy obtained SSL certificate for %DOMAIN%
) else (
    docker-compose logs caddy | findstr /c:"tls.handshake" >nul
    if not errorlevel 1 (
        echo Caddy is serving TLS, but certificate status is unclear
        echo Please check with: docker-compose logs caddy ^| findstr Certificate
    ) else (
        echo Warning: Could not verify SSL certificate status
        echo Please check with: docker-compose logs caddy
    )
)

echo.
echo ====================================================
echo Deployment Summary:
echo ====================================================
echo MCP Media Server is now available at: https://%DOMAIN%
echo API Documentation: https://%DOMAIN%/docs
echo.
echo To view logs:
echo   docker-compose logs -f
echo.
echo To check Caddy status:
echo   docker-compose logs caddy
echo.
echo To stop services:
echo   docker-compose down
echo.
echo Deployment completed at %date% %time%
echo ====================================================

exit /b 0
