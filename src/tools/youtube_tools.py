"""
YouTube downloader tools for the MCP Media Server.
"""
import os
import sys
import asyncio
import logging
import json
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError
import aiofiles

from src.core.server import mcp_server
from src.config.settings import get_settings
from src.utils.progress import ProgressTracker
from src.utils.cache import Cache
from src.services.webhook_service import trigger_webhook
from src.db.supabase_init import get_supabase_client

logger = logging.getLogger(__name__)
settings = get_settings()
cache = Cache()

class ProgressHook:
    """Progress hook to track download progress."""
    
    def __init__(self, job_id: str, video_id: str = None):
        """Initialize the progress hook."""
        self.job_id = job_id
        self.video_id = video_id
        self.tracker = ProgressTracker(job_id)
        self.start_time = None
        self.filename = None
        self.total_bytes = 0
        self.downloaded_bytes = 0
        
    def __call__(self, d: Dict[str, Any]):
        """Handle progress updates."""
        if d['status'] == 'downloading':
            if self.start_time is None:
                self.start_time = d.get('_elapsed_str', '0s')
                
            self.filename = d.get('filename')
            self.total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            self.downloaded_bytes = d.get('downloaded_bytes', 0)
            
            if self.total_bytes > 0:
                progress = int((self.downloaded_bytes / self.total_bytes) * 100)
                self.tracker.update_progress(progress)
            else:
                # If total_bytes is not available, use the progress provided by yt-dlp
                progress = d.get('progress', {}).get('percentage')
                if progress:
                    self.tracker.update_progress(int(progress * 100))
                    
        elif d['status'] == 'finished':
            self.tracker.update_progress(100, 'download_complete')
            logger.info(f"Download complete: {self.filename}")
            
        elif d['status'] == 'error':
            error_message = d.get('error', 'Unknown error')
            self.tracker.update_progress(progress=0, status='error', message=error_message)
            logger.error(f"Download error: {error_message}")


async def get_video_info(url: str) -> Dict[str, Any]:
    """
    Extract video information without downloading.
    
    Args:
        url: URL of the video
        
    Returns:
        Dict containing video information
    """
    # Create a temporary directory for cache
    cache_dir = Path(settings.get_absolute_path(settings.CACHE_DIR))
    cache_dir.mkdir(exist_ok=True)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'cachedir': str(cache_dir),
        'extract_flat': False,
    }
    
    try:
        # Use yt-dlp to extract video information
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # If it's a playlist, return the first video
            if 'entries' in info:
                if not info['entries']:
                    raise ValueError("No videos found in playlist")
                info = info['entries'][0]
            
            # Clean and return relevant information
            return {
                'id': info.get('id'),
                'title': info.get('title'),
                'description': info.get('description', ''),
                'uploader': info.get('uploader'),
                'duration': info.get('duration'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'upload_date': info.get('upload_date'),
                'formats': [
                    {
                        'format_id': f.get('format_id'),
                        'resolution': f.get('resolution'),
                        'fps': f.get('fps'),
                        'filesize': f.get('filesize'),
                        'vcodec': f.get('vcodec'),
                        'acodec': f.get('acodec'),
                        'format': f.get('format')
                    }
                    for f in info.get('formats', []) if f.get('format_id')
                ],
                'thumbnails': info.get('thumbnails', []),
                'tags': info.get('tags', []),
                'categories': info.get('categories', []),
                'webpage_url': info.get('webpage_url'),
                'is_live': info.get('is_live', False),
                'extractor': info.get('extractor', '')
            }
    
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        raise ValueError(f"Failed to extract video information: {str(e)}")


@mcp_server.register_tool
async def download_youtube(
    url: str, 
    format: str = "mp4", 
    quality: str = "best",
    audio_only: bool = False,
    output_filename: Optional[str] = None,
    create_thumbnail: bool = True,
    notify_webhook: bool = False
) -> Dict[str, Any]:
    """
    Download a video from YouTube or other supported platforms.
    
    Args:
        url: URL of the video to download
        format: Output format (mp4, mp3, webm, etc.)
        quality: Quality of the video (best, worst, 1080p, 720p, etc.)
        audio_only: If True, download audio only
        output_filename: Optional custom filename (without extension)
        create_thumbnail: Whether to create a thumbnail for the video
        notify_webhook: Whether to send a webhook notification when complete
        
    Returns:
        Dict containing information about the downloaded file
    """
    # Create unique IDs for tracking
    job_id = str(uuid.uuid4())
    video_id = str(uuid.uuid4())
    
    # Create necessary directories
    download_dir = Path(settings.get_absolute_path(settings.DOWNLOAD_DIR))
    download_dir.mkdir(exist_ok=True)
    
    # Cache check - see if we've already downloaded this video
    cache_key = f"download_{url}_{format}_{quality}_{audio_only}"
    cached_result = cache.get(cache_key)
    
    if cached_result:
        logger.info(f"Using cached download result for {url}")
        
        # Check if file exists
        if os.path.isfile(cached_result.get('filepath')):
            # Update only the job_id and video_id
            cached_result['job_id'] = job_id
            cached_result['video_id'] = video_id
            return cached_result
            
        # If file doesn't exist, remove from cache and proceed with download
        cache.delete(cache_key)
    
    # Initialize progress tracker
    progress_tracker = ProgressTracker(job_id)
    progress_tracker.init_job(
        job_type="youtube_download",
        status="starting",
        video_id=video_id,
        params={
            "url": url,
            "format": format,
            "quality": quality,
            "audio_only": audio_only
        }
    )
    
    try:
        # Get video info first
        progress_tracker.update_progress(5, "fetching_video_info")
        video_info = await get_video_info(url)
        
        # Generate filename
        if output_filename:
            base_filename = f"{output_filename}"
        else:
            # Create a safe filename from the video title
            base_filename = "".join(
                c if c.isalnum() or c in " ._-" else "_" 
                for c in video_info.get('title', f"video_{video_id}")
            ).strip()
        
        # Ensure no spaces in the filename
        base_filename = base_filename.replace(" ", "_")
        
        # Determine output path
        output_path = os.path.join(download_dir, base_filename)
        
        # Configure yt-dlp options
        ydl_opts = {
            'quiet': True,
            'no_warnings': False,  # Show warnings for debugging
            'progress_hooks': [ProgressHook(job_id, video_id)],
            'outtmpl': f"{output_path}.%(ext)s",
            'cachedir': str(Path(settings.get_absolute_path(settings.CACHE_DIR))),
            'verbose': settings.DEBUG,
        }
        
        # Adjust options based on format and audio_only
        if audio_only or format == "mp3":
            progress_tracker.update_progress(10, "configuring_audio_download")
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': format if format in ['mp3', 'aac', 'm4a', 'wav', 'opus'] else 'mp3',
                    'preferredquality': '192',
                }],
            })
            expected_ext = format if format in ['mp3', 'aac', 'm4a', 'wav', 'opus'] else 'mp3'
        else:
            progress_tracker.update_progress(10, "configuring_video_download")
            
            if quality in ['720p', '1080p', '480p', '360p', '240p', '144p']:
                # Handle resolution-based quality
                resolution = quality.replace('p', '')
                format_str = f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]'
            elif quality == 'best':
                format_str = 'bestvideo+bestaudio/best'
            elif quality == 'worst':
                format_str = 'worstvideo+worstaudio/worst'
            else:
                # Default to best quality
                format_str = 'bestvideo+bestaudio/best'
            
            ydl_opts.update({
                'format': format_str,
            })
            
            # Add format conversion if needed
            if format != 'mp4':
                ydl_opts.update({
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': format,
                    }],
                })
            
            expected_ext = format
        
        # Update progress
        progress_tracker.update_progress(15, "starting_download")
        
        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.download([url])
            except DownloadError as e:
                logger.error(f"Download error: {e}")
                progress_tracker.update_progress(0, "error", message=str(e))
                raise ValueError(f"Failed to download: {str(e)}")
        
        # Determine the actual output file
        filename = f"{base_filename}.{expected_ext}"
        filepath = os.path.join(download_dir, filename)
        
        # Check if the file exists
        if not os.path.isfile(filepath):
            # Try to find it with any extension
            for ext in ['mp4', 'webm', 'mkv', 'mp3', 'm4a', 'aac', 'wav', 'opus']:
                test_path = f"{output_path}.{ext}"
                if os.path.isfile(test_path):
                    filepath = test_path
                    filename = os.path.basename(filepath)
                    break
            else:
                # If we still can't find it, raise an error
                raise ValueError("Downloaded file not found")
        
        # Generate a thumbnail if requested
        thumbnail_path = None
        if create_thumbnail:
            progress_tracker.update_progress(90, "creating_thumbnail")
            thumbnail_path = await generate_thumbnail(filepath, video_id)
        
        # Complete the job
        progress_tracker.update_progress(100, "complete")
        
        # Create result object
        result = {
            "job_id": job_id,
            "video_id": video_id,
            "filename": filename,
            "filepath": filepath,
            "format": expected_ext,
            "size_bytes": os.path.getsize(filepath),
            "thumbnail_path": thumbnail_path,
            "duration": video_info.get('duration'),
            "title": video_info.get('title'),
            "description": video_info.get('description', ''),
            "tags": video_info.get('tags', []),
            "uploader": video_info.get('uploader'),
            "upload_date": video_info.get('upload_date'),
            "view_count": video_info.get('view_count'),
            "like_count": video_info.get('like_count'),
            "status": "complete",
            "url": url
        }
        
        # Store in Supabase if integrated
        try:
            supabase = get_supabase_client()
            video_data = {
                "id": video_id,
                "filename": filename,
                "file_path": filepath,
                "title": video_info.get('title'),
                "description": video_info.get('description', ''),
                "tags": video_info.get('tags', []),
                "duration": video_info.get('duration'),
                "size_bytes": os.path.getsize(filepath),
                "format": expected_ext,
                "thumbnail_path": thumbnail_path,
                "metadata": {
                    "url": url,
                    "uploader": video_info.get('uploader'),
                    "upload_date": video_info.get('upload_date'),
                    "view_count": video_info.get('view_count'),
                    "like_count": video_info.get('like_count')
                }
            }
            
            # Ensure data is JSON serializable
            video_data = json.loads(json.dumps(video_data, default=str))
            
            # Insert into database
            supabase.table("videos").insert(video_data).execute()
            logger.info(f"Video metadata stored in Supabase: {video_id}")
        except Exception as e:
            logger.error(f"Failed to store video metadata in Supabase: {e}")
            # Continue execution even if database storage fails
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="video_downloaded",
                job_id=job_id,
                video_id=video_id,
                status="complete",
                payload=result
            )
        
        # Cache the result
        cache.set(cache_key, result, expire_in=86400)  # Cache for 24 hours
        
        return result
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error downloading video: {error_message}")
        progress_tracker.update_progress(0, "error", message=error_message)
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="video_downloaded",
                job_id=job_id,
                video_id=video_id,
                status="error",
                payload={"error": error_message}
            )
        
        raise ValueError(f"Failed to download video: {error_message}")


async def generate_thumbnail(video_path: str, video_id: str) -> Optional[str]:
    """
    Generate a thumbnail for a video using FFmpeg.
    
    Args:
        video_path: Path to the video file
        video_id: ID of the video
        
    Returns:
        Path to the thumbnail file if successful, None otherwise
    """
    from src.tools.ffmpeg_tools import extract_thumbnail
    
    try:
        # Generate thumbnail
        thumbnails_dir = Path(settings.get_absolute_path(settings.THUMBNAILS_DIR))
        thumbnails_dir.mkdir(exist_ok=True)
        
        thumbnail_path = str(thumbnails_dir / f"{video_id}.jpg")
        
        # Use the FFmpeg tool to extract a thumbnail
        success = await extract_thumbnail(
            input_file=video_path,
            output_file=thumbnail_path,
            time_offset="00:00:05"  # 5 seconds into the video
        )
        
        if success:
            return thumbnail_path
        return None
    
    except Exception as e:
        logger.error(f"Failed to generate thumbnail: {e}")
        return None


@mcp_server.register_tool
async def batch_download_youtube(
    urls: List[str],
    format: str = "mp4",
    quality: str = "best",
    audio_only: bool = False,
    notify_webhook: bool = False
) -> List[Dict[str, Any]]:
    """
    Download multiple videos from YouTube or other supported platforms.
    
    Args:
        urls: List of URLs to download
        format: Output format (mp4, mp3, webm, etc.)
        quality: Quality of the videos (best, worst, 1080p, 720p, etc.)
        audio_only: If True, download audio only
        notify_webhook: Whether to send a webhook notification when complete
        
    Returns:
        List of dictionaries containing information about the downloaded files
    """
    if not urls:
        raise ValueError("No URLs provided")
    
    # Create a unique batch ID
    batch_id = str(uuid.uuid4())
    
    # Create a progress tracker for the batch
    progress_tracker = ProgressTracker(batch_id)
    progress_tracker.init_job(
        job_type="batch_youtube_download",
        status="starting",
        params={
            "url_count": len(urls),
            "format": format,
            "quality": quality,
            "audio_only": audio_only
        }
    )
    
    try:
        results = []
        total_urls = len(urls)
        
        # Process each URL
        for i, url in enumerate(urls):
            # Update batch progress
            batch_progress = int((i / total_urls) * 100)
            progress_tracker.update_progress(
                batch_progress, 
                f"downloading_video_{i+1}_of_{total_urls}"
            )
            
            try:
                # Download the video
                result = await download_youtube(
                    url=url,
                    format=format,
                    quality=quality,
                    audio_only=audio_only,
                    create_thumbnail=True,
                    notify_webhook=False  # Only notify for the whole batch
                )
                
                results.append(result)
                
            except Exception as e:
                # Log the error but continue with other downloads
                logger.error(f"Error downloading video {i+1}/{total_urls} ({url}): {e}")
                results.append({
                    "url": url,
                    "status": "error",
                    "error": str(e)
                })
        
        # Complete the batch
        progress_tracker.update_progress(100, "complete")
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="batch_video_downloaded",
                job_id=batch_id,
                status="complete",
                payload={
                    "batch_id": batch_id,
                    "total_videos": total_urls,
                    "successful_downloads": len([r for r in results if r.get("status") == "complete"]),
                    "failed_downloads": len([r for r in results if r.get("status") == "error"]),
                    "results": results
                }
            )
        
        return {
            "batch_id": batch_id,
            "total_videos": total_urls,
            "successful_downloads": len([r for r in results if r.get("status") == "complete"]),
            "failed_downloads": len([r for r in results if r.get("status") == "error"]),
            "results": results
        }
    
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error in batch download: {error_message}")
        progress_tracker.update_progress(0, "error", message=error_message)
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="batch_video_downloaded",
                job_id=batch_id,
                status="error",
                payload={"error": error_message}
            )
        
        raise ValueError(f"Failed to process batch download: {error_message}")


@mcp_server.register_tool
async def search_videos(query: str, max_results: int = 10) -> Dict[str, Any]:
    """
    Search for videos on YouTube and return information about them.
    
    Args:
        query: Search query
        max_results: Maximum number of results to return
        
    Returns:
        Dict containing search results
    """
    # Create a temporary directory for cache
    cache_dir = Path(settings.get_absolute_path(settings.CACHE_DIR))
    cache_dir.mkdir(exist_ok=True)
    
    # Check cache
    cache_key = f"search_{query}_{max_results}"
    cached_result = cache.get(cache_key)
    
    if cached_result:
        logger.info(f"Using cached search results for '{query}'")
        return cached_result
    
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'cachedir': str(cache_dir),
        }
        
        # Add ytsearch prefix if needed
        if not (
            query.startswith("https://") or 
            query.startswith("http://") or 
            query.startswith("ytsearch") or
            query.startswith("ytsearchdate")
        ):
            query = f"ytsearch{max_results}:{query}"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            
            if not info or 'entries' not in info:
                return {
                    "query": query,
                    "results": []
                }
            
            results = []
            for entry in info.get('entries', [])[:max_results]:
                if not entry:
                    continue
                    
                results.append({
                    'id': entry.get('id'),
                    'title': entry.get('title'),
                    'url': entry.get('url', entry.get('webpage_url')),
                    'uploader': entry.get('uploader'),
                    'upload_date': entry.get('upload_date'),
                    'description': entry.get('description', ''),
                    'thumbnail': next((t.get('url') for t in entry.get('thumbnails', []) 
                                      if t.get('url')), None),
                    'duration': entry.get('duration'),
                    'view_count': entry.get('view_count'),
                    'channel_id': entry.get('channel_id'),
                })
            
            search_results = {
                "query": query,
                "results": results
            }
            
            # Cache the results
            cache.set(cache_key, search_results, expire_in=3600)  # Cache for 1 hour
            
            return search_results
    
    except Exception as e:
        logger.error(f"Error searching videos: {e}")
        raise ValueError(f"Failed to search videos: {str(e)}")
