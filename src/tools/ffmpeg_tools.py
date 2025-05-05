"""
FFmpeg tools for the MCP Media Server.
"""
import os
import sys
import asyncio
import logging
import json
import uuid
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Tuple

import ffmpeg
import aiofiles
from PIL import Image

from src.core.server import mcp_server
from src.config.settings import get_settings
from src.utils.progress import ProgressTracker
from src.utils.cache import Cache
from src.services.webhook_service import trigger_webhook
from src.db.supabase_init import get_supabase_client

logger = logging.getLogger(__name__)
settings = get_settings()
cache = Cache()


async def get_video_metadata(input_file: str) -> Dict[str, Any]:
    """
    Get metadata for a video file using FFmpeg.
    
    Args:
        input_file: Path to the input video file
        
    Returns:
        Dict containing the video metadata
    """
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"File not found: {input_file}")
        
    try:
        # Run FFprobe command to get video information in JSON format
        cmd = [
            'ffprobe', 
            '-v', 'quiet', 
            '-print_format', 'json', 
            '-show_format', 
            '-show_streams', 
            input_file
        ]
        
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await result.communicate()
        
        if result.returncode != 0:
            raise RuntimeError(f"FFprobe error: {stderr.decode().strip()}")
            
        # Parse the JSON output
        metadata = json.loads(stdout.decode())
        
        # Extract relevant information
        info = {
            "format": {
                "format_name": metadata.get("format", {}).get("format_name"),
                "duration": float(metadata.get("format", {}).get("duration", 0)),
                "size": int(metadata.get("format", {}).get("size", 0)),
                "bit_rate": int(metadata.get("format", {}).get("bit_rate", 0)),
            },
            "streams": []
        }
        
        # Extract stream information
        for stream in metadata.get("streams", []):
            stream_info = {
                "index": stream.get("index"),
                "codec_type": stream.get("codec_type"),
                "codec_name": stream.get("codec_name"),
                "codec_long_name": stream.get("codec_long_name"),
            }
            
            # Add video-specific information
            if stream.get("codec_type") == "video":
                stream_info.update({
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "display_aspect_ratio": stream.get("display_aspect_ratio"),
                    "field_order": stream.get("field_order"),
                    "r_frame_rate": stream.get("r_frame_rate"),
                    "avg_frame_rate": stream.get("avg_frame_rate"),
                    "duration": float(stream.get("duration", 0)),
                    "bit_rate": int(stream.get("bit_rate", 0)),
                })
            
            # Add audio-specific information
            elif stream.get("codec_type") == "audio":
                stream_info.update({
                    "sample_rate": stream.get("sample_rate"),
                    "channels": stream.get("channels"),
                    "channel_layout": stream.get("channel_layout"),
                    "duration": float(stream.get("duration", 0)),
                    "bit_rate": int(stream.get("bit_rate", 0)),
                })
            
            info["streams"].append(stream_info)
            
        return info
        
    except Exception as e:
        logger.error(f"Error getting video metadata: {e}")
        raise ValueError(f"Failed to get video metadata: {str(e)}")


@mcp_server.register_tool
async def process_video(
    input_file: str, 
    operation: str = "compress", 
    output_format: str = None,
    resolution: Optional[str] = None,
    framerate: Optional[int] = None,
    crf: Optional[int] = None,
    preset: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    output_filename: Optional[str] = None,
    notify_webhook: bool = False
) -> Dict[str, Any]:
    """
    Process a video using FFmpeg.
    
    Args:
        input_file: Path to the input video file
        operation: Operation to perform (compress, trim, convert, extract_audio)
        output_format: Output format (mp4, webm, gif, etc.)
        resolution: Output resolution (1080p, 720p, 480p, etc.)
        framerate: Output framerate (e.g., 30, 60)
        crf: Constant Rate Factor for compression (0-51, lower is better quality)
        preset: FFmpeg preset (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)
        audio_bitrate: Audio bitrate (e.g., 128k, 192k)
        start_time: Start time for trim (format: HH:MM:SS or seconds)
        end_time: End time for trim (format: HH:MM:SS or seconds)
        output_filename: Optional custom filename (without extension)
        notify_webhook: Whether to send a webhook notification when complete
        
    Returns:
        Dict containing information about the processed file
    """
    # Check if the input file exists
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    # Create unique IDs for tracking
    job_id = str(uuid.uuid4())
    
    # Initialize progress tracker
    progress_tracker = ProgressTracker(job_id)
    progress_tracker.init_job(
        job_type=f"video_{operation}",
        status="starting",
        params={
            "input_file": input_file,
            "operation": operation,
            "output_format": output_format,
            "resolution": resolution,
            "framerate": framerate,
            "crf": crf,
            "preset": preset,
            "audio_bitrate": audio_bitrate,
            "start_time": start_time,
            "end_time": end_time
        }
    )
    
    try:
        # Get the input file information
        progress_tracker.update_progress(5, "analyzing_input")
        metadata = await get_video_metadata(input_file)
        
        # Create the output directory
        processed_dir = Path(settings.get_absolute_path(settings.PROCESSED_DIR))
        processed_dir.mkdir(exist_ok=True)
        
        # Determine output format
        input_ext = os.path.splitext(input_file)[1].lower()[1:]
        output_ext = output_format.lower() if output_format else input_ext
        
        # Generate output filename
        if output_filename:
            base_filename = output_filename
        else:
            # Create a filename based on the operation and input filename
            input_basename = os.path.basename(input_file)
            input_name = os.path.splitext(input_basename)[0]
            base_filename = f"{input_name}_{operation}"
            
            # Add resolution to filename if specified
            if resolution:
                base_filename = f"{base_filename}_{resolution}"
        
        # Ensure no spaces in the filename
        base_filename = base_filename.replace(" ", "_")
        
        # Full output path
        output_file = str(processed_dir / f"{base_filename}.{output_ext}")
        
        # Set FFmpeg parameters
        ffmpeg_threads = settings.FFMPEG_THREADS
        ffmpeg_preset = preset or settings.FFMPEG_PRESET or "medium"
        
        # Start building the FFmpeg command
        progress_tracker.update_progress(10, "configuring_ffmpeg")
        
        try:
            # Start with the input file
            stream = ffmpeg.input(input_file)
            
            # Apply trim if start_time or end_time is specified
            if start_time or end_time:
                progress_tracker.update_progress(15, "configuring_trim")
                
                # Parse time values
                if start_time:
                    # If it's just a number, treat as seconds
                    if start_time.replace('.', '').isdigit():
                        start_time = float(start_time)
                
                if end_time:
                    # If it's just a number, treat as seconds
                    if end_time.replace('.', '').isdigit():
                        end_time = float(end_time)
                
                # Apply trim
                if start_time and end_time:
                    stream = stream.trim(start=start_time, end=end_time)
                elif start_time:
                    stream = stream.trim(start=start_time)
                elif end_time:
                    # If only end_time is specified, trim from beginning to end_time
                    stream = stream.trim(end=end_time)
                
                # Ensure proper timestamps after trim
                stream = stream.setpts('PTS-STARTPTS')
            
            # Handle different operations
            if operation == "extract_audio":
                progress_tracker.update_progress(20, "configuring_audio_extraction")
                
                # Force audio output format
                output_ext = output_format or "mp3"
                output_file = str(processed_dir / f"{base_filename}.{output_ext}")
                
                # Extract audio stream
                audio_stream = stream.audio
                
                # Set audio bitrate if specified
                audio_opts = {}
                if audio_bitrate:
                    audio_opts['audio_bitrate'] = audio_bitrate
                
                # Output audio only
                stream = ffmpeg.output(
                    audio_stream, 
                    output_file,
                    **audio_opts
                )
                
            elif operation == "compress":
                progress_tracker.update_progress(20, "configuring_compression")
                
                # Set video compression options
                video_opts = {
                    'c:v': 'libx264',
                    'crf': crf or 23,  # Default to reasonable quality
                    'preset': ffmpeg_preset,
                    'threads': ffmpeg_threads
                }
                
                # Set audio compression options
                audio_opts = {
                    'c:a': 'aac',
                    'b:a': audio_bitrate or '128k'
                }
                
                # Apply resolution if specified
                if resolution:
                    if resolution == "1080p":
                        video_opts['vf'] = 'scale=-1:1080'
                    elif resolution == "720p":
                        video_opts['vf'] = 'scale=-1:720'
                    elif resolution == "480p":
                        video_opts['vf'] = 'scale=-1:480'
                    elif resolution == "360p":
                        video_opts['vf'] = 'scale=-1:360'
                    elif resolution == "240p":
                        video_opts['vf'] = 'scale=-1:240'
                    else:
                        # Parse custom resolution (e.g., 1280x720)
                        if 'x' in resolution:
                            width, height = resolution.split('x')
                            video_opts['vf'] = f'scale={width}:{height}'
                
                # Apply framerate if specified
                if framerate:
                    video_opts['r'] = framerate
                
                # Combine options
                output_opts = {**video_opts, **audio_opts}
                
                # Create output stream
                stream = ffmpeg.output(
                    stream,
                    output_file,
                    **output_opts
                )
                
            elif operation == "convert":
                progress_tracker.update_progress(20, "configuring_conversion")
                
                # Default options
                output_opts = {
                    'c:v': 'libx264',
                    'preset': ffmpeg_preset,
                    'threads': ffmpeg_threads
                }
                
                # Apply resolution if specified
                if resolution:
                    if resolution == "1080p":
                        output_opts['vf'] = 'scale=-1:1080'
                    elif resolution == "720p":
                        output_opts['vf'] = 'scale=-1:720'
                    elif resolution == "480p":
                        output_opts['vf'] = 'scale=-1:480'
                    elif resolution == "360p":
                        output_opts['vf'] = 'scale=-1:360'
                    elif resolution == "240p":
                        output_opts['vf'] = 'scale=-1:240'
                    else:
                        # Parse custom resolution (e.g., 1280x720)
                        if 'x' in resolution:
                            width, height = resolution.split('x')
                            output_opts['vf'] = f'scale={width}:{height}'
                
                # Apply framerate if specified
                if framerate:
                    output_opts['r'] = framerate
                
                # Set quality if specified
                if crf is not None:
                    output_opts['crf'] = crf
                
                # Set audio options
                if audio_bitrate:
                    output_opts['b:a'] = audio_bitrate
                
                # Create output stream
                stream = ffmpeg.output(
                    stream,
                    output_file,
                    **output_opts
                )
                
            else:
                # Default to basic conversion if operation is not recognized
                progress_tracker.update_progress(20, "configuring_basic_conversion")
                
                stream = ffmpeg.output(
                    stream,
                    output_file,
                    vcodec='libx264',
                    acodec='aac',
                    preset=ffmpeg_preset,
                    threads=ffmpeg_threads
                )
            
            # Add overwrite output flag
            stream = ffmpeg.overwrite_output(stream)
            
            # Generate the FFmpeg command
            _, cmd = stream.compile()
            logger.info(f"FFmpeg command: {' '.join(cmd)}")
            
            # Start the processing
            progress_tracker.update_progress(30, "processing")
            
            # Run the FFmpeg command
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait for the process to complete while checking progress
            stdout, stderr = await process.communicate()
            
            # Check for errors
            if process.returncode != 0:
                error_message = stderr.decode().strip()
                logger.error(f"FFmpeg error: {error_message}")
                progress_tracker.update_progress(0, "error", message=error_message)
                raise RuntimeError(f"FFmpeg error: {error_message}")
            
            # Processing complete
            progress_tracker.update_progress(100, "complete")
            
            # Get metadata of the output file
            output_metadata = await get_video_metadata(output_file)
            
            # Create result object
            result = {
                "job_id": job_id,
                "operation": operation,
                "input_file": input_file,
                "output_file": output_file,
                "format": output_ext,
                "size_bytes": os.path.getsize(output_file),
                "input_metadata": metadata,
                "output_metadata": output_metadata,
                "status": "complete"
            }
            
            # Send webhook notification if requested
            if notify_webhook and settings.WEBHOOK_ENABLED:
                await trigger_webhook(
                    event_type="video_processed",
                    job_id=job_id,
                    status="complete",
                    payload=result
                )
            
            return result
            
        except Exception as e:
            logger.error(f"FFmpeg processing error: {e}")
            progress_tracker.update_progress(0, "error", message=str(e))
            raise
    
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error processing video: {error_message}")
        progress_tracker.update_progress(0, "error", message=error_message)
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="video_processed",
                job_id=job_id,
                status="error",
                payload={"error": error_message}
            )
        
        raise ValueError(f"Failed to process video: {error_message}")


@mcp_server.register_tool
async def batch_process_videos(
    input_files: List[str],
    operation: str = "compress",
    output_format: str = None,
    resolution: Optional[str] = None,
    notify_webhook: bool = False
) -> Dict[str, Any]:
    """
    Process multiple videos using FFmpeg.
    
    Args:
        input_files: List of paths to the input video files
        operation: Operation to perform (compress, trim, convert, extract_audio)
        output_format: Output format (mp4, webm, gif, etc.)
        resolution: Output resolution (1080p, 720p, 480p, etc.)
        notify_webhook: Whether to send a webhook notification when complete
        
    Returns:
        Dict containing information about the processed files
    """
    if not input_files:
        raise ValueError("No input files provided")
    
    # Create a unique batch ID
    batch_id = str(uuid.uuid4())
    
    # Create a progress tracker for the batch
    progress_tracker = ProgressTracker(batch_id)
    progress_tracker.init_job(
        job_type=f"batch_video_{operation}",
        status="starting",
        params={
            "file_count": len(input_files),
            "operation": operation,
            "output_format": output_format,
            "resolution": resolution
        }
    )
    
    try:
        results = []
        total_files = len(input_files)
        
        # Process each file
        for i, input_file in enumerate(input_files):
            # Update batch progress
            batch_progress = int((i / total_files) * 100)
            progress_tracker.update_progress(
                batch_progress, 
                f"processing_video_{i+1}_of_{total_files}"
            )
            
            try:
                # Process the video
                result = await process_video(
                    input_file=input_file,
                    operation=operation,
                    output_format=output_format,
                    resolution=resolution,
                    notify_webhook=False  # Only notify for the whole batch
                )
                
                results.append(result)
                
            except Exception as e:
                # Log the error but continue with other files
                logger.error(f"Error processing video {i+1}/{total_files} ({input_file}): {e}")
                results.append({
                    "input_file": input_file,
                    "status": "error",
                    "error": str(e)
                })
        
        # Complete the batch
        progress_tracker.update_progress(100, "complete")
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="batch_video_processed",
                job_id=batch_id,
                status="complete",
                payload={
                    "batch_id": batch_id,
                    "total_files": total_files,
                    "successful_operations": len([r for r in results if r.get("status") == "complete"]),
                    "failed_operations": len([r for r in results if r.get("status") == "error"]),
                    "results": results
                }
            )
        
        return {
            "batch_id": batch_id,
            "total_files": total_files,
            "successful_operations": len([r for r in results if r.get("status") == "complete"]),
            "failed_operations": len([r for r in results if r.get("status") == "error"]),
            "results": results
        }
    
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error in batch processing: {error_message}")
        progress_tracker.update_progress(0, "error", message=error_message)
        
        # Send webhook notification if requested
        if notify_webhook and settings.WEBHOOK_ENABLED:
            await trigger_webhook(
                event_type="batch_video_processed",
                job_id=batch_id,
                status="error",
                payload={"error": error_message}
            )
        
        raise ValueError(f"Failed to process batch: {error_message}")


@mcp_server.register_tool
async def extract_thumbnail(
    input_file: str,
    output_file: Optional[str] = None,
    time_offset: str = "00:00:01",
    width: Optional[int] = None,
    height: Optional[int] = None
) -> Union[bool, str]:
    """
    Extract a thumbnail from a video file.
    
    Args:
        input_file: Path to the input video file
        output_file: Path to save the thumbnail (if None, will be auto-generated)
        time_offset: Time offset for the thumbnail (format: HH:MM:SS or seconds)
        width: Optional width of the thumbnail
        height: Optional height of the thumbnail
        
    Returns:
        Path to the extracted thumbnail if successful, False otherwise
    """
    # Check if the input file exists
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    try:
        # Create the thumbnails directory
        thumbnails_dir = Path(settings.get_absolute_path(settings.THUMBNAILS_DIR))
        thumbnails_dir.mkdir(exist_ok=True)
        
        # Generate output filename if not provided
        if not output_file:
            input_basename = os.path.basename(input_file)
            input_name = os.path.splitext(input_basename)[0]
            output_file = str(thumbnails_dir / f"{input_name}_thumbnail.jpg")
        
        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file if it exists
            '-ss', str(time_offset),  # Seek to the specified time
            '-i', input_file,  # Input file
            '-vframes', '1',  # Extract one frame
            '-q:v', '2',  # Quality level (lower values = higher quality, 2-31)
        ]
        
        # Add resize filter if width or height is specified
        if width or height:
            width_str = str(width) if width else '-1'
            height_str = str(height) if height else '-1'
            cmd.extend(['-vf', f'scale={width_str}:{height_str}'])
        
        # Add output file
        cmd.append(output_file)
        
        # Run the FFmpeg command
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Wait for the process to complete
        stdout, stderr = await process.communicate()
        
        # Check if the thumbnail was successfully created
        if process.returncode != 0:
            error_message = stderr.decode().strip()
            logger.error(f"Thumbnail extraction error: {error_message}")
            return False
        
        # Check if the output file exists
        if not os.path.isfile(output_file):
            logger.error(f"Thumbnail was not created: {output_file}")
            return False
        
        # Optimize the thumbnail (resize if needed)
        if width and height:
            try:
                with Image.open(output_file) as img:
                    # Resize the image while maintaining aspect ratio
                    img.thumbnail((width, height), Image.LANCZOS)
                    img.save(output_file, "JPEG", quality=90, optimize=True)
            except Exception as e:
                logger.warning(f"Error optimizing thumbnail: {e}")
        
        return output_file
    
    except Exception as e:
        logger.error(f"Error extracting thumbnail: {e}")
        return False


@mcp_server.register_tool
async def analyze_video(
    input_file: str,
    analysis_type: str = "technical"
) -> Dict[str, Any]:
    """
    Analyze a video file to extract technical details or other information.
    
    Args:
        input_file: Path to the input video file
        analysis_type: Type of analysis to perform (technical, scenes, motion, etc.)
        
    Returns:
        Dict containing analysis results
    """
    # Check if the input file exists
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    try:
        # Get basic metadata regardless of analysis type
        metadata = await get_video_metadata(input_file)
        
        results = {
            "filename": os.path.basename(input_file),
            "file_size": os.path.getsize(input_file),
            "technical": {
                "format": metadata.get("format", {}),
                "streams": metadata.get("streams", [])
            }
        }
        
        # Perform specific analysis based on the requested type
        if analysis_type == "technical":
            # Technical analysis is already covered by the metadata
            pass
            
        elif analysis_type == "scenes":
            # Scene detection is more complex and would typically be done
            # with PySceneDetect or similar libraries, but we'll simulate
            # a basic version using FFmpeg's scene detection filter
            
            # Create a temporary file for the scene detection output
            scene_output = f"{os.path.splitext(input_file)[0]}_scenes.txt"
            
            cmd = [
                'ffmpeg',
                '-i', input_file,
                '-filter:v', 'select=\'gt(scene,0.4)\',showinfo',
                '-f', 'null',
                '-'
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # Parse the output to find scene changes
            scene_changes = []
            for line in stderr.decode().split('\n'):
                if 'pts_time' in line and 'scene' in line:
                    # Extract the timestamp
                    pts_time = line.split('pts_time:')[1].split()[0]
                    scene_score = float(line.split('scene:')[1].split()[0])
                    
                    scene_changes.append({
                        "timestamp": float(pts_time),
                        "score": scene_score
                    })
            
            results["scenes"] = {
                "count": len(scene_changes),
                "changes": scene_changes
            }
            
        elif analysis_type == "motion":
            # Basic motion analysis using FFmpeg's motion detection filter
            cmd = [
                'ffmpeg',
                '-i', input_file,
                '-filter:v', 'mestimate=epzs:mb_size=16:search_param=7',
                '-f', 'null',
                '-'
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            # For a real implementation, you would need to parse the motion vectors
            # from FFmpeg's output, but this is just a placeholder
            results["motion"] = {
                "analysis": "Motion analysis would be performed here"
            }
        
        return results
    
    except Exception as e:
        logger.error(f"Error analyzing video: {e}")
        raise ValueError(f"Failed to analyze video: {str(e)}")
