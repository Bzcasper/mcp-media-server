#!/bin/bash
echo "Starting MCP Media Server..."

# Create Python virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "Installing dependencies..."
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Create necessary directories if they don't exist
mkdir -p logs downloads processed thumbnails cache

# Start the server
echo "Starting MCP server..."
python main.py "$@"

# Check exit code
if [ $? -ne 0 ]; then
    echo "Server stopped with error code $?"
    read -p "Press Enter to continue..."
fi
