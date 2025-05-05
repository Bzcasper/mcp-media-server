@echo off
echo Starting MCP Media Server...

:: Create Python virtual environment if it doesn't exist
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate
)

:: Create necessary directories if they don't exist
if not exist logs mkdir logs
if not exist downloads mkdir downloads 
if not exist processed mkdir processed
if not exist thumbnails mkdir thumbnails
if not exist cache mkdir cache

:: Start the server
echo Starting MCP server...
python main.py %*

:: Keep command window open on errors
if %ERRORLEVEL% NEQ 0 (
    echo Server stopped with error level %ERRORLEVEL%
    pause
)
