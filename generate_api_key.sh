#!/bin/bash
# Generate an API key for IDE integration

# Get the container ID
CONTAINER_ID=$(docker ps -qf "name=mcp-media-server")

# Execute the command to generate an API key
docker exec $CONTAINER_ID python -c "
import asyncio
import sys
sys.path.insert(0, '.')
from src.auth.security import create_api_key

async def generate_key():
    key_info = await create_api_key(
        user_id='system',
        name='ide-integration',
        permissions=['read', 'write', 'download', 'process'],
        expires_in_days=365
    )
    print(f'API Key: {key_info[\"api_key\"]}')
    print(f'Key ID: {key_info[\"id\"]}')

asyncio.run(generate_key())
"
