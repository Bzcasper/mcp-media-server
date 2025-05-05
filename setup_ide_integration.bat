@echo off
echo Setting up MCP Media Server integration with development environment...

:: Check if Docker is running
docker info > nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Docker is not running. Please start Docker Desktop and try again.
    exit /b 1
)

:: Check if the container is running
docker ps | findstr "mcp-media-server" > nul
if %ERRORLEVEL% NEQ 0 (
    echo MCP Media Server is not running. Starting it now...
    docker-compose up -d
    
    :: Wait for the service to start
    echo Waiting for service to start...
    timeout /t 10 /nobreak > nul
) else (
    echo MCP Media Server is already running.
)

:: Generate API key for IDE integration
echo Generating API key for IDE integration...
call generate_api_key.bat > api_key_output.txt

:: Extract the API key
for /f "tokens=2 delims=:" %%a in ('findstr "API Key" api_key_output.txt') do (
    set API_KEY=%%a
    set API_KEY=!API_KEY: =!
)

:: Update the IDE config file with the API key
echo Updating IDE configuration...
powershell -Command "(Get-Content ide-config.json) -replace 'YOUR_GENERATED_API_KEY', '%API_KEY%' | Set-Content ide-config.json"

:: Clean up
del api_key_output.txt

:: Create symbolic links for easier file access
echo Creating symbolic links for media directories...
mklink /D "%USERPROFILE%\mcp_media" "%CD%\downloads"

echo Setup complete! Your MCP Media Server is now integrated with your development environment.
echo API Configuration:
echo - API URL: http://localhost:9000
echo - API Key: %API_KEY%
echo - Media Files: %USERPROFILE%\mcp_media

echo.
echo You can now configure Roo Code and Windsurf IDE to use these settings.
