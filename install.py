#!/usr/bin/env python3
"""
Installation script for the MCP Media Server.
"""
import os
import sys
import subprocess
import platform
import shutil
import secrets
from pathlib import Path

def check_python_version():
    """Check if the Python version is compatible."""
    if sys.version_info < (3, 10):
        print("Error: Python 3.10 or higher is required.")
        print(f"Current Python version: {platform.python_version()}")
        sys.exit(1)
    
    print(f"Python version check passed: {platform.python_version()}")

def check_ffmpeg():
    """Check if ffmpeg is installed."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            check=True
        )
        print("FFmpeg check passed: FFmpeg is installed")
        return True
    except FileNotFoundError:
        print("Warning: FFmpeg not found in PATH.")
        print("Please install FFmpeg before running the MCP Media Server.")
        print("  - Windows: https://ffmpeg.org/download.html")
        print("  - macOS: brew install ffmpeg")
        print("  - Linux: apt install ffmpeg or yum install ffmpeg")
        return False
    except subprocess.CalledProcessError:
        print("Warning: FFmpeg is installed but returned an error.")
        return False

def create_virtual_environment():
    """Create a virtual environment."""
    if os.path.exists("venv"):
        print("Virtual environment already exists, skipping creation.")
        return
    
    print("Creating virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
    print("Virtual environment created.")

def install_dependencies():
    """Install dependencies from requirements.txt."""
    print("Installing dependencies...")
    
    # Determine the Python executable in the virtual environment
    if platform.system() == "Windows":
        pip_exec = os.path.join("venv", "Scripts", "pip")
    else:
        pip_exec = os.path.join("venv", "bin", "pip")
    
    subprocess.run([pip_exec, "install", "-U", "pip"], check=True)
    subprocess.run([pip_exec, "install", "-r", "requirements.txt"], check=True)
    print("Dependencies installed.")

def create_directories():
    """Create necessary directories."""
    directories = ["logs", "downloads", "processed", "thumbnails", "cache"]
    
    for directory in directories:
        dir_path = Path(directory)
        dir_path.mkdir(exist_ok=True)
        print(f"Directory created: {dir_path}")

def create_env_file():
    """Create .env file from .env.example."""
    if os.path.exists(".env"):
        print(".env file already exists, skipping creation.")
        return
    
    if not os.path.exists(".env.example"):
        print("Error: .env.example file not found.")
        return
    
    print("Creating .env file from .env.example...")
    
    # Generate a JWT secret
    jwt_secret = secrets.token_hex(32)
    
    with open(".env.example", "r") as example_file:
        env_content = example_file.read()
    
    # Replace the JWT secret placeholder
    env_content = env_content.replace(
        "JWT_SECRET=generate_a_secure_random_key_and_replace_this",
        f"JWT_SECRET={jwt_secret}"
    )
    
    with open(".env", "w") as env_file:
        env_file.write(env_content)
    
    print(".env file created.")
    print("Please edit .env file to set your API keys and configuration.")

def set_file_permissions():
    """Set executable permissions for scripts on Unix-like systems."""
    if platform.system() != "Windows":
        try:
            os.chmod("start_server.sh", 0o755)
            print("Set executable permission for start_server.sh")
        except Exception as e:
            print(f"Error setting permissions: {e}")

def main():
    """Main installation function."""
    print("=== MCP Media Server Installation ===")
    
    # Check Python version
    check_python_version()
    
    # Check FFmpeg
    check_ffmpeg()
    
    # Create directories
    create_directories()
    
    # Create virtual environment
    create_virtual_environment()
    
    # Install dependencies
    install_dependencies()
    
    # Create .env file
    create_env_file()
    
    # Set file permissions
    set_file_permissions()
    
    print("\n=== Installation Complete ===")
    print("To start the server:")
    if platform.system() == "Windows":
        print("  Run: start_server.bat")
    else:
        print("  Run: ./start_server.sh")
    print("\nMake sure to edit the .env file to set your API keys before starting the server.")


if __name__ == "__main__":
    main()
