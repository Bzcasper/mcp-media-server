#!/usr/bin/env python3
"""
Install the MCP Media Server for Claude Desktop.
"""
import os
import sys
import json
import platform
import argparse
from pathlib import Path

def find_claude_config():
    """Find the Claude Desktop configuration file."""
    config_path = None
    
    if platform.system() == "Windows":
        # Windows: %APPDATA%\Claude\claude_desktop_config.json
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
    
    elif platform.system() == "Darwin":
        # macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
        home = os.environ.get("HOME")
        if home:
            config_path = Path(home) / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    
    elif platform.system() == "Linux":
        # Linux: ~/.config/Claude/claude_desktop_config.json
        home = os.environ.get("HOME")
        if home:
            config_path = Path(home) / ".config" / "Claude" / "claude_desktop_config.json"
    
    return config_path

def get_python_path():
    """Get the path to the Python executable."""
    # If we're in a virtual environment, use that Python
    if os.environ.get("VIRTUAL_ENV"):
        if platform.system() == "Windows":
            return Path(os.environ["VIRTUAL_ENV"]) / "Scripts" / "python.exe"
        else:
            return Path(os.environ["VIRTUAL_ENV"]) / "bin" / "python"
    
    # Otherwise, use the current Python interpreter
    return Path(sys.executable)

def get_mcp_server_path():
    """Get the path to the MCP server script."""
    return Path(__file__).resolve().parent / "main.py"

def update_claude_config(server_name="mcp-media-server"):
    """Update the Claude Desktop configuration to include the MCP server."""
    config_path = find_claude_config()
    
    if not config_path:
        print("Error: Could not find Claude Desktop configuration path.")
        print("Please manually configure the MCP server in Claude Desktop.")
        return False
    
    # Create the parent directory if it doesn't exist
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Read existing configuration if it exists
    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print("Warning: Existing configuration is invalid. Creating a new one.")
    
    # Ensure the mcpServers section exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}
    
    # Get the paths
    python_path = get_python_path()
    server_path = get_mcp_server_path()
    
    # Add the MCP server configuration
    config["mcpServers"][server_name] = {
        "command": str(python_path),
        "args": [str(server_path)]
    }
    
    # Write the updated configuration
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        
        print(f"MCP server '{server_name}' added to Claude Desktop configuration.")
        print(f"Configuration updated at: {config_path}")
        return True
    
    except Exception as e:
        print(f"Error updating configuration: {e}")
        return False

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Install MCP Media Server for Claude Desktop")
    parser.add_argument(
        "--name",
        default="mcp-media-server",
        help="Name for the MCP server in Claude Desktop"
    )
    
    args = parser.parse_args()
    
    print("=== MCP Media Server Installation for Claude Desktop ===")
    
    # Update Claude Desktop configuration
    success = update_claude_config(args.name)
    
    if success:
        print("\n=== Installation Complete ===")
        print("The MCP Media Server is now available in Claude Desktop.")
        print("To use it, restart Claude Desktop and look for the server in the MCP tools section.")
    else:
        print("\n=== Installation Failed ===")
        print("Please configure the MCP server manually in Claude Desktop.")

if __name__ == "__main__":
    main()
