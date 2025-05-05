# MCP Media Server

A custom MCP (Model Context Protocol) server for media processing, built with Python. This server enables AI assistants like Claude to interact with media files, perform video processing, and integrate with Supabase and Pinecone for data storage and vector search.

## Features

### Core Features
- YouTube video downloading using yt-dlp
- Video processing with FFmpeg
- Supabase integration for metadata storage
- Pinecone integration for vector search
- MCP server for AI assistant integration

### Advanced Features
- Progress tracking for long-running operations
- Webhook support for notifications
- Batch processing for multiple files
- Caching for improved performance
- Rate limiting for API protection
- User authentication and API key management
- Scheduled tasks for maintenance
- RESTful API gateway
- Continuous operation with Docker
- Integration with development IDEs (Roo Code & Windsurf)

## Prerequisites

- Python 3.10 or higher
- FFmpeg
- Supabase account
- Pinecone account
- OpenAI API key (for embeddings)
- Docker and Docker Compose (for containerized deployment)

## Installation

### Option 1: Manual Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/mcp-media-server.git
   cd mcp-media-server
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure the server by copying and editing the example environment file:
   ```bash
   cp .env.example .env
   ```

5. Edit the `.env` file to add your API keys and configuration.

### Option 2: Docker Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/mcp-media-server.git
   cd mcp-media-server
   ```

2. Configure the server by copying and editing the example environment file:
   ```bash
   cp .env.example .env
   ```

3. Edit the `.env` file to add your API keys and configuration.

4. Build and start the Docker container:
   ```bash
   docker-compose up -d
   ```

## Usage

### Running the MCP Server

To run the MCP server:

```bash
python main.py
```

The server uses stdio transport by default for direct integration with Claude Desktop or other MCP clients.

### Starting with SSE Transport

To run the server with Server-Sent Events (SSE) transport:

```bash
python main.py --transport sse
```

### Running the API Server

To run the API server alongside the MCP server:

```bash
python main.py --api
```

### Starting Both SSE and API Server

To run both the SSE transport and API server:

```bash
python main.py --transport sse --api
```

### Continuous Operation with Docker

For 24/7 operation in production environments:

1. Deploy using Docker Compose:
   ```bash
   docker-compose up -d
   ```

2. Set up automatic restarts and monitoring:
   ```bash
   # Make scripts executable
   chmod +x deploy.sh monitor.sh generate_api_key.sh
   
   # Deploy with health checks
   ./deploy.sh
   
   # Add monitoring to crontab (runs every 5 minutes)
   (crontab -l 2>/dev/null; echo "*/5 * * * * /path/to/mcp-media-server/monitor.sh") | crontab -
   ```

3. Set up as a system service (Linux):
   ```bash
   # Copy service file
   sudo cp mcp-media-server.service /etc/systemd/system/
   
   # Edit the service file to update the path
   sudo nano /etc/systemd/system/mcp-media-server.service
   
   # Enable and start the service
   sudo systemctl enable mcp-media-server.service
   sudo systemctl start mcp-media-server.service
   ```

### IDE Integration (Roo Code & Windsurf)

For Windows users:

1. Run the IDE integration setup script:
   ```bash
   setup_ide_integration.bat
   ```

For Linux/macOS users:

1. Generate an API key:
   ```bash
   ./generate_api_key.sh
   ```

2. Configure your IDE using the generated key and settings from `ide-config.json`

### Integrating with Claude Desktop

1. Open Claude Desktop app
2. Navigate to Settings
3. Add a new MCP server with the following configuration:
   - Name: MCP Media Server
   - Command: Path to your Python executable
   - Arguments: Path to the main.py file

Example configuration:
```json
{
  "mcpServers": {
    "mcp-media-server": {
      "command": "C:\\path\\to\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\mcp-media-server\\main.py"]
    }
  }
}
```

## API Documentation

Once the API server is running, you can access the API documentation at:

```
http://localhost:9000/docs
```

### Key API Endpoints

- `/videos/download` - Download a video from YouTube
- `/videos/process` - Process a video using FFmpeg
- `/videos/search` - Search for videos on YouTube
- `/videos/vector-search` - Semantic search for videos
- `/videos/similar` - Find similar videos

## Development

### Project Structure

```
mcp-media-server/
├── src/
│   ├── api/              # RESTful API implementation
│   ├── auth/             # Authentication and security
│   ├── core/             # Core MCP server implementation
│   ├── config/           # Configuration settings
│   ├── db/               # Database integrations
│   ├── services/         # Background services
│   ├── tasks/            # Scheduled tasks
│   ├── tools/            # MCP tools implementation
│   ├── utils/            # Utilities and helpers
│   └── webhooks/         # Webhook handlers
├── downloads/            # Downloaded files
├── processed/            # Processed files
├── thumbnails/           # Generated thumbnails
├── cache/                # Cache files
├── logs/                 # Log files
├── .env                  # Environment variables
├── docker-compose.yml    # Docker configuration
├── Dockerfile            # Docker build instructions
├── deploy.sh             # Deployment script
├── monitor.sh            # Health monitoring script
├── generate_api_key.sh   # API key generation script
├── mcp-media-server.service # Systemd service file
├── setup_ide_integration.bat # IDE integration for Windows
├── ide-config.json       # Configuration for IDE integration
├── main.py               # Application entry point
└── requirements.txt      # Python dependencies
```

### Adding New Tools

To add a new tool to the MCP server, create a new function in the appropriate file in the `src/tools` directory and decorate it with `@mcp_server.register_tool`.

Example:

```python
from src.core.server import mcp_server

@mcp_server.register_tool
async def my_new_tool(param1: str, param2: int) -> dict:
    """
    Description of the new tool.
    
    Args:
        param1: Description of param1
        param2: Description of param2
        
    Returns:
        Dict containing the result
    """
    # Implement the tool functionality
    result = {"status": "success", "message": "Tool executed successfully"}
    return result
```

### Container Management

View logs:
```bash
docker-compose logs -f mcp-server
```

Check container status:
```bash
docker ps | grep mcp-server
```

Monitor resource usage:
```bash
docker stats mcp-server
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
