"""
FastAPI application for the MCP Media Server.
"""
import os
import time
import logging
from typing import Dict, Any, List, Optional, Union

from fastapi import FastAPI, Depends, HTTPException, Header, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.config.settings import get_settings
from src.auth.security import (
    create_access_token, authenticate_user, decode_token,
    create_api_key, validate_api_key, revoke_api_key
)
from src.utils.progress import ProgressTracker
from src.utils.cache import Cache
from src.db.supabase_init import get_supabase_client
from src.db.pinecone_init import get_pinecone_client
from src.core.server import mcp_server

# Import tools for API exposure
from src.tools.youtube_tools import download_youtube, search_videos, batch_download_youtube
from src.tools.ffmpeg_tools import process_video, batch_process_videos, analyze_video, extract_thumbnail
from src.tools.vector_tools import search_videos_by_text, similar_videos

logger = logging.getLogger(__name__)
settings = get_settings()

# Create the FastAPI app
app = FastAPI(
    title="MCP Media Server API",
    description="API for the MCP Media Server",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update with actual origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()


# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware."""
    # Get client IP
    client_ip = request.client.host
    
    # Skip rate limiting for certain paths
    if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
        return await call_next(request)
    
    # Check rate limiting
    if settings.RATE_LIMIT_ENABLED:
        # Implement rate limiting
        # This is a simple implementation - in production, use Redis or a similar 
        # distributed cache for proper rate limiting
        cache = Cache()
        cache_key = f"rate_limit_{client_ip}"
        
        # Get current request count
        current_count = cache.get(cache_key, 0)
        
        if current_count >= settings.RATE_LIMIT_REQUESTS:
            # Rate limit exceeded
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                media_type="application/json"
            )
        
        # Increment request count
        cache.set(cache_key, current_count + 1, expire_in=settings.RATE_LIMIT_PERIOD)
    
    # Continue with the request
    return await call_next(request)


# Authentication dependency
async def get_current_user_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get the current user from a token."""
    token = credentials.credentials
    token_data = decode_token(token)
    
    if token_data is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token_data


async def get_current_user_from_api_key(
    x_api_key: str = Header(None)
):
    """Get the current user from an API key."""
    if x_api_key is None:
        raise HTTPException(
            status_code=401,
            detail="API key is required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    key_info = await validate_api_key(x_api_key)
    
    if key_info is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    return key_info


# Define request and response models
class LoginRequest(BaseModel):
    """Login request model."""
    email: str
    password: str


class TokenResponse(BaseModel):
    """Token response model."""
    access_token: str
    token_type: str
    expires_at: str
    user_id: str


class ApiKeyRequest(BaseModel):
    """API key request model."""
    name: str
    permissions: List[str] = ["read"]
    expires_in_days: Optional[int] = None


class ApiKeyResponse(BaseModel):
    """API key response model."""
    id: str
    api_key: str
    name: str
    permissions: List[str]
    expires_at: Optional[str] = None


class VideoDownloadRequest(BaseModel):
    """Video download request model."""
    url: str
    format: str = "mp4"
    quality: str = "best"
    audio_only: bool = False
    output_filename: Optional[str] = None
    create_thumbnail: bool = True
    notify_webhook: bool = False


class VideoProcessRequest(BaseModel):
    """Video process request model."""
    input_file: str
    operation: str = "compress"
    output_format: Optional[str] = None
    resolution: Optional[str] = None
    framerate: Optional[int] = None
    crf: Optional[int] = None
    preset: Optional[str] = None
    audio_bitrate: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    output_filename: Optional[str] = None
    notify_webhook: bool = False


class BatchVideoDownloadRequest(BaseModel):
    """Batch video download request model."""
    urls: List[str]
    format: str = "mp4"
    quality: str = "best"
    audio_only: bool = False
    notify_webhook: bool = False


class SearchRequest(BaseModel):
    """Search request model."""
    query: str
    max_results: int = 10


class VectorSearchRequest(BaseModel):
    """Vector search request model."""
    query: str
    limit: int = 10
    filter: Optional[Dict[str, Any]] = None
    namespace: str = ""


class SimilarVideosRequest(BaseModel):
    """Similar videos request model."""
    video_id: str
    limit: int = 10
    namespace: str = ""


class JobStatusResponse(BaseModel):
    """Job status response model."""
    job_id: str
    status: str
    progress: int
    message: Optional[str] = None
    start_time: float
    end_time: Optional[float] = None
    params: Dict[str, Any] = {}


# Define routes
@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the MCP Media Server API"}


@app.post("/auth/login", response_model=TokenResponse)
async def login(login_data: LoginRequest):
    """Login endpoint."""
    user = await authenticate_user(login_data.email, login_data.password)
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    token_data = {
        "sub": user.id,
        "email": user.email,
        "permissions": user.permissions
    }
    
    access_token = create_access_token(token_data)
    expires_at = time.time() + settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
        "user_id": user.id
    }


@app.post("/auth/api-keys", response_model=ApiKeyResponse)
async def create_api_key_endpoint(
    api_key_data: ApiKeyRequest,
    user_data = Depends(get_current_user_from_token)
):
    """Create API key endpoint."""
    try:
        key_info = await create_api_key(
            user_id=user_data.user_id,
            name=api_key_data.name,
            permissions=api_key_data.permissions,
            expires_in_days=api_key_data.expires_in_days
        )
        
        return {
            "id": key_info["id"],
            "api_key": key_info["api_key"],
            "name": key_info["name"],
            "permissions": key_info["permissions"],
            "expires_at": key_info.get("expires_at")
        }
    
    except Exception as e:
        logger.error(f"Error creating API key: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create API key: {str(e)}"
        )


@app.delete("/auth/api-keys/{api_key_id}")
async def revoke_api_key_endpoint(
    api_key_id: str,
    user_data = Depends(get_current_user_from_token)
):
    """Revoke API key endpoint."""
    success = await revoke_api_key(api_key_id, user_data.user_id)
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail="API key not found or not owned by user"
        )
    
    return {"message": "API key revoked successfully"}


@app.post("/videos/download")
async def download_video_endpoint(
    download_data: VideoDownloadRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Download video endpoint."""
    try:
        # Check permissions
        if "download" not in user_data["permissions"] and "write" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await download_youtube(
            url=download_data.url,
            format=download_data.format,
            quality=download_data.quality,
            audio_only=download_data.audio_only,
            output_filename=download_data.output_filename,
            create_thumbnail=download_data.create_thumbnail,
            notify_webhook=download_data.notify_webhook
        )
        
        # Add the user ID to the response
        result["user_id"] = user_data["user_id"]
        
        return result
    
    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download video: {str(e)}"
        )


@app.post("/videos/batch-download")
async def batch_download_videos_endpoint(
    batch_data: BatchVideoDownloadRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Batch download videos endpoint."""
    try:
        # Check permissions
        if "download" not in user_data["permissions"] and "write" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await batch_download_youtube(
            urls=batch_data.urls,
            format=batch_data.format,
            quality=batch_data.quality,
            audio_only=batch_data.audio_only,
            notify_webhook=batch_data.notify_webhook
        )
        
        # Add the user ID to the response
        result["user_id"] = user_data["user_id"]
        
        return result
    
    except Exception as e:
        logger.error(f"Error batch downloading videos: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to batch download videos: {str(e)}"
        )


@app.post("/videos/process")
async def process_video_endpoint(
    process_data: VideoProcessRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Process video endpoint."""
    try:
        # Check permissions
        if "process" not in user_data["permissions"] and "write" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await process_video(
            input_file=process_data.input_file,
            operation=process_data.operation,
            output_format=process_data.output_format,
            resolution=process_data.resolution,
            framerate=process_data.framerate,
            crf=process_data.crf,
            preset=process_data.preset,
            audio_bitrate=process_data.audio_bitrate,
            start_time=process_data.start_time,
            end_time=process_data.end_time,
            output_filename=process_data.output_filename,
            notify_webhook=process_data.notify_webhook
        )
        
        # Add the user ID to the response
        result["user_id"] = user_data["user_id"]
        
        return result
    
    except Exception as e:
        logger.error(f"Error processing video: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process video: {str(e)}"
        )


@app.post("/videos/analyze")
async def analyze_video_endpoint(
    input_file: str,
    analysis_type: str = "technical",
    user_data = Depends(get_current_user_from_api_key)
):
    """Analyze video endpoint."""
    try:
        # Check permissions
        if "read" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await analyze_video(
            input_file=input_file,
            analysis_type=analysis_type
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error analyzing video: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze video: {str(e)}"
        )


@app.post("/videos/search")
async def search_videos_endpoint(
    search_data: SearchRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Search videos endpoint."""
    try:
        # Check permissions
        if "read" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await search_videos(
            query=search_data.query,
            max_results=search_data.max_results
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error searching videos: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search videos: {str(e)}"
        )


@app.post("/videos/vector-search")
async def vector_search_endpoint(
    search_data: VectorSearchRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Vector search endpoint."""
    try:
        # Check permissions
        if "read" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await search_videos_by_text(
            query=search_data.query,
            limit=search_data.limit,
            filter=search_data.filter,
            namespace=search_data.namespace
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error vector searching videos: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to vector search videos: {str(e)}"
        )


@app.post("/videos/similar")
async def similar_videos_endpoint(
    similar_data: SimilarVideosRequest,
    user_data = Depends(get_current_user_from_api_key)
):
    """Similar videos endpoint."""
    try:
        # Check permissions
        if "read" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        result = await similar_videos(
            video_id=similar_data.video_id,
            limit=similar_data.limit,
            namespace=similar_data.namespace
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error finding similar videos: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to find similar videos: {str(e)}"
        )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user_data = Depends(get_current_user_from_api_key)
):
    """Get job status endpoint."""
    try:
        # Check permissions
        if "read" not in user_data["permissions"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        
        progress_tracker = ProgressTracker(job_id)
        job_status = progress_tracker.get_progress()
        
        if job_status.get("status") == "not_found":
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )
        
        return job_status
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job status: {str(e)}"
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    # Check if databases are initialized
    supabase_healthy = False
    pinecone_healthy = False
    
    try:
        # Check Supabase
        supabase = get_supabase_client()
        supabase_response = supabase.table("videos").select("id").limit(1).execute()
        supabase_healthy = True
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
    
    try:
        # Check Pinecone
        pinecone = get_pinecone_client()
        pinecone_indexes = pinecone.client.list_indexes()
        pinecone_healthy = True
    except Exception as e:
        logger.error(f"Pinecone health check failed: {e}")
    
    return {
        "status": "healthy",
        "version": "1.0.0",
        "databases": {
            "supabase": "healthy" if supabase_healthy else "unhealthy",
            "pinecone": "healthy" if pinecone_healthy else "unhealthy"
        },
        "server_info": {
            "registered_tools": len(mcp_server.get_registered_tools()),
            "registered_resources": len(mcp_server.get_registered_resources()),
            "registered_prompts": len(mcp_server.get_registered_prompts())
        }
    }
