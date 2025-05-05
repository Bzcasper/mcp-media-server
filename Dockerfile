FROM python:3.10-slim

# Set working directory
WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    curl \
    wget \
    software-properties-common \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for better Python behavior in containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on

# Create non-root user for security
RUN groupadd -r mcp && useradd --no-log-init -r -g mcp mcp

# Create necessary directories
RUN mkdir -p /app/logs /app/downloads /app/processed /app/thumbnails /app/cache \
    && chown -R mcp:mcp /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=mcp:mcp . .

# Install healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Switch to non-root user
USER mcp

# Expose port
EXPOSE 9000

# Set the entrypoint
ENTRYPOINT ["python", "main.py"]

# Default command - SSE transport and API server
CMD ["--transport", "sse", "--api"]
