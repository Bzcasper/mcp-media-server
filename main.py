"""
Main entry point for the MCP Media Server.
Enhanced for production with error handling, recovery, and monitoring.
"""
import os
import sys
import asyncio
import logging
import argparse
import signal
import traceback
import time
from datetime import datetime
from pathlib import Path
from functools import partial

# Ensure proper Python version
if sys.version_info < (3, 9):
    print("Error: Python 3.9 or higher is required.")
    sys.exit(1)

# Add src directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Setup basic logging first - will be enhanced later
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global flags for graceful shutdown
shutdown_requested = False
restart_requested = False

# Import settings first to handle early configuration
try:
    from src.config.settings import get_settings
    settings = get_settings()
except Exception as e:
    logger.critical(f"Failed to load settings: {e}")
    print(f"CRITICAL ERROR: Failed to load settings: {e}")
    sys.exit(1)

# Set up proper logging with file handlers
try:
    log_dir = Path(settings.get_absolute_path("logs"))
    log_dir.mkdir(exist_ok=True, parents=True)
    
    log_file = log_dir / f"server_{datetime.now().strftime('%Y%m%d')}.log"
    error_log = log_dir / f"error_{datetime.now().strftime('%Y%m%d')}.log"
    
    # Configure logging with both file and console handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO if not settings.DEBUG else logging.DEBUG)
    
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add new handlers
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(log_file)
    error_handler = logging.FileHandler(error_log)
    
    # Set levels
    console_handler.setLevel(logging.INFO)
    file_handler.setLevel(logging.INFO)
    error_handler.setLevel(logging.ERROR)
    
    # Create formatters
    standard_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s"
    )
    
    # Set formatters
    console_handler.setFormatter(standard_formatter)
    file_handler.setFormatter(standard_formatter)
    error_handler.setFormatter(detailed_formatter)
    
    # Add filters to error handler
    error_handler.setLevel(logging.ERROR)
    
    # Add handlers to root logger
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    
    logger.info("Logging configured successfully")
except Exception as e:
    print(f"Warning: Error setting up logging: {e}")
    # Proceed with basic logging

# Import server components
try:
    # Core components
    from src.core.server import mcp_server
    from src.tasks.scheduler import scheduler
    from src.utils.error_monitor import error_monitor
    from src.utils.backup_manager import backup_manager
    from src.db.connection_manager import connection_manager
    from src.config.key_manager import key_manager
    
    # Import all tools to register them
    import src.tools.youtube_tools
    import src.tools.ffmpeg_tools
    import src.tools.vector_tools
except Exception as e:
    logger.critical(f"Failed to import required components: {e}")
    traceback.print_exc()
    sys.exit(1)

def signal_handler(signum, frame):
    """Handle signals for graceful shutdown."""
    global shutdown_requested, restart_requested
    
    if signum == signal.SIGINT or signum == signal.SIGTERM:
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        shutdown_requested = True
    elif signum == signal.SIGHUP:
        logger.info("Received SIGHUP, will restart after shutdown...")
        restart_requested = True
        shutdown_requested = True

# Install signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGHUP'):  # Not available on Windows
    signal.signal(signal.SIGHUP, signal_handler)

async def check_system_requirements():
    """Check system requirements and dependencies."""
    try:
        # Check for ffmpeg
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-version"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            if result.returncode != 0:
                logger.warning("FFmpeg not found or not working properly")
                print("WARNING: FFmpeg not found or not working properly")
                print("Media processing functionality may be limited")
            else:
                logger.info("FFmpeg check passed")
        except Exception as e:
            logger.warning(f"FFmpeg check failed: {e}")
            print(f"WARNING: FFmpeg check failed: {e}")
            print("Media processing functionality may be limited")
        
        # Check for required directories
        required_dirs = [
            "logs", "downloads", "processed", "thumbnails", 
            "cache", "backups", "keys", "fallbacks"
        ]
        
        for directory in required_dirs:
            dir_path = Path(settings.get_absolute_path(directory))
            dir_path.mkdir(exist_ok=True, parents=True)
            logger.info(f"Ensured directory exists: {dir_path}")
        
        # Check required API keys
        key_status = key_manager.get_all_required_keys()
        missing_keys = [k for k, v in key_status.items() if not v]
        
        if missing_keys:
            missing_keys_str = ", ".join(missing_keys)
            logger.warning(f"Missing required API keys: {missing_keys_str}")
            print(f"WARNING: Missing required API keys: {missing_keys_str}")
            print("Some functionality may be limited")
        else:
            logger.info("All required API keys are configured")
        
        return True
    except Exception as e:
        logger.error(f"Error checking system requirements: {e}")
        return False

async def initialize_databases():
    """Initialize database connections with retries."""
    max_retries = 3
    retry_delay = 5  # seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            # Check database connections using the connection manager
            logger.info(f"Initializing database connections (attempt {attempt}/{max_retries})...")
            
            health = await connection_manager.check_all_connections()
            
            # Check if both connections are healthy
            if (health.get("supabase", {}).get("healthy", False) and 
                health.get("pinecone", {}).get("healthy", False)):
                logger.info("All database connections initialized successfully")
                return True
            
            # If we get here, at least one connection failed
            if not health.get("supabase", {}).get("healthy", False):
                logger.warning("Supabase connection failed, will retry")
            
            if not health.get("pinecone", {}).get("healthy", False):
                logger.warning("Pinecone connection failed, will retry")
            
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.warning("Maximum retry attempts reached, proceeding with fallbacks")
                # We'll proceed with fallbacks enabled
                return False
            
        except Exception as e:
            logger.error(f"Error initializing databases: {e}")
            
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.warning("Maximum retry attempts reached, proceeding with fallbacks")
                # We'll proceed with fallbacks enabled
                return False
    
    return False

async def start_background_tasks():
    """Start background tasks for monitoring and maintenance."""
    try:
        # Start connection monitoring
        connection_monitor_task = asyncio.create_task(
            connection_manager.monitor_connections(check_interval=60)
        )
        
        # Start automatic backup scheduler
        backup_interval_hours = 24
        automatic_backup_task = asyncio.create_task(
            backup_manager.schedule_automatic_backups(interval_hours=backup_interval_hours)
        )
        
        # Return all tasks so they can be cancelled on shutdown
        return [connection_monitor_task, automatic_backup_task]
    except Exception as e:
        logger.error(f"Error starting background tasks: {e}")
        return []

async def start_api_server():
    """Start the API server."""
    try:
        import uvicorn
        from src.api.app import app
        
        # Create API config
        config = uvicorn.Config(
            app=app,
            host=settings.MCP_SERVER_HOST,
            port=int(settings.MCP_SERVER_PORT),
            log_level="info" if not settings.DEBUG else "debug",
            workers=1
        )
        
        # Create server instance
        server = uvicorn.Server(config)
        logger.info(f"API server starting on {settings.MCP_SERVER_HOST}:{settings.MCP_SERVER_PORT}")
        
        # Start the server
        await server.serve()
    except Exception as e:
        logger.error(f"Error starting API server: {e}")
        raise

async def perform_system_backup():
    """Perform a system backup on startup/shutdown."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_info = await backup_manager.create_backup(f"mcp_backup_{timestamp}")
        
        if backup_info.get("status") == "failed":
            logger.warning(f"System backup failed: {backup_info.get('error')}")
            return False
        
        logger.info(f"System backup completed: {backup_info.get('name')}")
        return True
    except Exception as e:
        logger.error(f"Error performing system backup: {e}")
        return False

async def graceful_shutdown(background_tasks=None):
    """Perform a graceful shutdown."""
    logger.info("Performing graceful shutdown...")
    
    # Cancel background tasks
    if background_tasks:
        for task in background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    
    # Stop the task scheduler
    scheduler.stop()
    logger.info("Task scheduler stopped")
    
    # Perform a system backup
    await perform_system_backup()
    
    logger.info("Shutdown complete")

async def start_server(transport: str = "stdio", run_api: bool = False):
    """
    Start the MCP server with enhanced error handling and monitoring.
    
    Args:
        transport: Transport to use (stdio, sse)
        run_api: Whether to run the API server
    """
    global shutdown_requested, restart_requested
    
    # Check system requirements
    await check_system_requirements()
    
    # Perform initial backup
    await perform_system_backup()
    
    # Initialize databases
    db_status = await initialize_databases()
    if not db_status:
        logger.warning("Database initialization incomplete, proceeding with fallbacks")
    
    # Start the task scheduler
    scheduler.start()
    logger.info("Task scheduler started")
    
    # Start background tasks
    background_tasks = await start_background_tasks()
    
    try:
        # Start the API server if requested
        api_server_task = None
        if run_api:
            api_server_task = asyncio.create_task(start_api_server())
        
        # Run the MCP server
        if transport == "sse":
            host = settings.MCP_SERVER_HOST
            port = int(settings.MCP_SERVER_PORT)
            logger.info(f"Starting MCP server with SSE transport on {host}:{port}")
            
            # Run the MCP server in a task so we can monitor for shutdown requests
            mcp_task = asyncio.create_task(
                mcp_server.run(transport=transport, host=host, port=port)
            )
            
            # Monitor for shutdown requests
            while not shutdown_requested:
                await asyncio.sleep(1)
                
                if mcp_task.done():
                    exception = mcp_task.exception()
                    if exception:
                        logger.error(f"MCP server error: {exception}")
                        break
            
            # Cancel the MCP task if still running
            if not mcp_task.done():
                mcp_task.cancel()
                try:
                    await mcp_task
                except asyncio.CancelledError:
                    pass
        else:
            # For stdio transport, we need to run the server in the main thread
            # so just check if shutdown was requested during initialization
            if not shutdown_requested:
                logger.info("Starting MCP server with stdio transport")
                mcp_server.run(transport=transport)
        
        # Wait for API server to complete if it was started
        if api_server_task and not api_server_task.done():
            api_server_task.cancel()
            try:
                await api_server_task
            except asyncio.CancelledError:
                pass
    
    except KeyboardInterrupt:
        logger.info("Shutdown requested via keyboard interrupt")
        shutdown_requested = True
    
    except Exception as e:
        logger.error(f"Error running server: {e}")
        traceback.print_exc()
    
    finally:
        # Perform graceful shutdown
        await graceful_shutdown(background_tasks)
        
        # Check if restart was requested
        if restart_requested:
            logger.info("Restarting server...")
            os.execv(sys.executable, [sys.executable] + sys.argv)

def main():
    """Main entry point with argument parsing and error handling."""
    parser = argparse.ArgumentParser(description="MCP Media Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport to use (stdio, sse)"
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Run the API server"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Override the port from configuration"
    )
    parser.add_argument(
        "--host",
        type=str,
        help="Override the host from configuration"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    
    args = parser.parse_args()
    
    # Override settings if provided
    if args.port:
        os.environ["MCP_SERVER_PORT"] = str(args.port)
    
    if args.host:
        os.environ["MCP_SERVER_HOST"] = args.host
    
    if args.debug:
        os.environ["DEBUG"] = "True"
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Record start time
    start_time = time.time()
    
    # Run the server
    try:
        # For SSE transport or when running the API server, use asyncio
        if args.transport == "sse" or args.api:
            asyncio.run(start_server(args.transport, args.api))
        else:
            # For stdio transport without API, we can use a simpler approach
            asyncio.run(start_server("stdio", False))
    
    except KeyboardInterrupt:
        print("\nServer shutdown requested via keyboard interrupt")
    
    except SystemExit:
        # Normal exit, no need to log
        pass
    
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        # Record uptime
        uptime = time.time() - start_time
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        logger.info(f"Server uptime: {int(hours)}h {int(minutes)}m {int(seconds)}s")
        
        # Wait a moment before exiting to allow logs to be written
        time.sleep(0.5)
        
        if restart_requested:
            # Exit with special code to indicate restart
            sys.exit(42)
        else:
            # Normal exit
            sys.exit(0)


if __name__ == "__main__":
    # Check for restart loop
    if os.environ.get("MCP_RESTART_COUNT"):
        restart_count = int(os.environ.get("MCP_RESTART_COUNT", "0"))
        # Prevent infinite restart loops
        if restart_count > 5:
            print("Too many restart attempts, exiting")
            sys.exit(1)
        os.environ["MCP_RESTART_COUNT"] = str(restart_count + 1)
    else:
        os.environ["MCP_RESTART_COUNT"] = "1"
    
    # Run main function
    exit_code = main()
    
    # Handle restart if requested
    if exit_code == 42:
        print("Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
