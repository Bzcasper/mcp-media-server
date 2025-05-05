"""
Core MCP Server implementation
"""
import sys
import os
from pathlib import Path
import logging
from typing import Optional, Dict, Any

# Add parent directory to path so we can import from the src directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Import MCP SDK
from mcp.server.fastmcp import FastMCP

# Import configuration
from src.config.settings import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent.parent / "logs" / "server.log", mode="a")
    ]
)
logger = logging.getLogger(__name__)

# Create directories if they don't exist
for directory in ["logs", "downloads", "processed", "thumbnails", "cache"]:
    dir_path = Path(__file__).parent.parent.parent / directory
    dir_path.mkdir(exist_ok=True)
    logger.info(f"Ensuring directory exists: {dir_path}")

class MCPMediaServer:
    """
    MCP Media Server that integrates yt-dlp, ffmpeg, Supabase, and Pinecone.
    """
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern to ensure only one server instance exists."""
        if cls._instance is None:
            cls._instance = super(MCPMediaServer, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, name: str = "Media Processing Server"):
        """Initialize the MCP server if not already initialized."""
        if self._initialized:
            return
            
        self.settings = get_settings()
        self.name = name
        
        # Initialize MCP server
        self.mcp = FastMCP(name)
        
        # Store registered tools, resources, and prompts
        self._tools = {}
        self._resources = {}
        self._prompts = {}
        
        # Mark as initialized
        self._initialized = True
        logger.info(f"MCPMediaServer '{name}' initialized")
    
    def register_tool(self, func):
        """Register a tool with the MCP server."""
        self._tools[func.__name__] = func
        return self.mcp.tool()(func)
    
    def register_resource(self, uri_template):
        """Register a resource with the MCP server."""
        def decorator(func):
            self._resources[uri_template] = func
            return self.mcp.resource(uri_template)(func)
        return decorator
    
    def register_prompt(self, prompt_id, template=None):
        """Register a prompt with the MCP server."""
        def decorator(func):
            self._prompts[prompt_id] = func
            return self.mcp.prompt(prompt_id, template)(func)
        return decorator
    
    def run(self, transport: str = "stdio", host: Optional[str] = None, 
            port: Optional[int] = None):
        """Run the MCP server."""
        logger.info(f"Starting MCP server with transport: {transport}")
        
        if transport == "sse":
            host = host or self.settings.MCP_SERVER_HOST
            port = port or self.settings.MCP_SERVER_PORT
            logger.info(f"Server will listen on {host}:{port}")
            
        return self.mcp.run(
            transport=transport,
            host=host,
            port=port
        )
    
    def get_registered_tools(self) -> Dict[str, Any]:
        """Get all registered tools."""
        return self._tools
        
    def get_registered_resources(self) -> Dict[str, Any]:
        """Get all registered resources."""
        return self._resources
        
    def get_registered_prompts(self) -> Dict[str, Any]:
        """Get all registered prompts."""
        return self._prompts


# Create and export the server instance
mcp_server = MCPMediaServer()
