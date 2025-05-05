"""
File utility functions for the MCP Media Server.
"""
import os
import shutil
import time
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

async def ensure_directory_exists(directory: Union[str, Path]) -> bool:
    """
    Ensure a directory exists, creating it if necessary.
    
    Args:
        directory: Path to the directory
        
    Returns:
        True if the directory exists or was created, False otherwise
    """
    try:
        directory_path = Path(directory)
        directory_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Error ensuring directory exists: {e}")
        return False


async def get_file_info(file_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """
    Get information about a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Dict containing file information, or None if the file doesn't exist
    """
    try:
        path = Path(file_path)
        
        if not path.exists():
            return None
        
        # Get file stats
        stats = path.stat()
        
        return {
            "name": path.name,
            "path": str(path.absolute()),
            "size": stats.st_size,
            "created": stats.st_ctime,
            "modified": stats.st_mtime,
            "accessed": stats.st_atime,
            "is_file": path.is_file(),
            "is_dir": path.is_dir(),
            "extension": path.suffix.lower()[1:] if path.suffix else "",
            "parent": str(path.parent.absolute())
        }
    except Exception as e:
        logger.error(f"Error getting file info: {e}")
        return None


async def list_directory(directory: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    List the contents of a directory.
    
    Args:
        directory: Path to the directory
        
    Returns:
        List of file information dictionaries
    """
    try:
        dir_path = Path(directory)
        
        if not dir_path.exists() or not dir_path.is_dir():
            return []
        
        files = []
        
        for item in dir_path.iterdir():
            # Get basic info
            stats = item.stat()
            
            files.append({
                "name": item.name,
                "path": str(item.absolute()),
                "size": stats.st_size,
                "modified": stats.st_mtime,
                "is_file": item.is_file(),
                "is_dir": item.is_dir(),
                "extension": item.suffix.lower()[1:] if item.suffix else ""
            })
        
        # Sort by name
        files.sort(key=lambda x: x["name"])
        
        return files
    except Exception as e:
        logger.error(f"Error listing directory: {e}")
        return []


async def delete_file(file_path: Union[str, Path]) -> bool:
    """
    Delete a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        True if the file was deleted, False otherwise
    """
    try:
        path = Path(file_path)
        
        if not path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return False
        
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        
        logger.info(f"Deleted: {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return False


async def move_file(
    source_path: Union[str, Path], 
    destination_path: Union[str, Path]
) -> bool:
    """
    Move a file to a new location.
    
    Args:
        source_path: Path to the source file
        destination_path: Path to the destination
        
    Returns:
        True if the file was moved, False otherwise
    """
    try:
        source = Path(source_path)
        destination = Path(destination_path)
        
        if not source.exists():
            logger.warning(f"Source file does not exist: {source_path}")
            return False
        
        # Create destination directory if it doesn't exist
        destination.parent.mkdir(parents=True, exist_ok=True)
        
        # Move the file
        shutil.move(str(source), str(destination))
        
        logger.info(f"Moved: {source_path} -> {destination_path}")
        return True
    except Exception as e:
        logger.error(f"Error moving file: {e}")
        return False


async def copy_file(
    source_path: Union[str, Path], 
    destination_path: Union[str, Path]
) -> bool:
    """
    Copy a file to a new location.
    
    Args:
        source_path: Path to the source file
        destination_path: Path to the destination
        
    Returns:
        True if the file was copied, False otherwise
    """
    try:
        source = Path(source_path)
        destination = Path(destination_path)
        
        if not source.exists():
            logger.warning(f"Source file does not exist: {source_path}")
            return False
        
        # Create destination directory if it doesn't exist
        destination.parent.mkdir(parents=True, exist_ok=True)
        
        if source.is_file():
            shutil.copy2(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(str(source), str(destination))
        
        logger.info(f"Copied: {source_path} -> {destination_path}")
        return True
    except Exception as e:
        logger.error(f"Error copying file: {e}")
        return False


async def clean_old_files(
    directory: Union[str, Path],
    max_age_days: int = 7,
    recursive: bool = False
) -> int:
    """
    Clean old files from a directory.
    
    Args:
        directory: Path to the directory
        max_age_days: Maximum age of files in days
        recursive: Whether to clean subdirectories recursively
        
    Returns:
        Number of files deleted
    """
    try:
        dir_path = Path(directory)
        
        if not dir_path.exists() or not dir_path.is_dir():
            return 0
        
        # Convert days to seconds
        max_age_seconds = max_age_days * 24 * 60 * 60
        current_time = time.time()
        
        deleted_count = 0
        
        # Get all files in the directory
        if recursive:
            all_files = list(dir_path.glob("**/*"))
        else:
            all_files = list(dir_path.glob("*"))
        
        # Check each file
        for file_path in all_files:
            if not file_path.is_file():
                continue
            
            file_modified = file_path.stat().st_mtime
            
            # Check if the file is older than the threshold
            if current_time - file_modified > max_age_seconds:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
        
        return deleted_count
    except Exception as e:
        logger.error(f"Error cleaning old files: {e}")
        return 0


async def get_directory_size(directory: Union[str, Path]) -> int:
    """
    Get the total size of a directory in bytes.
    
    Args:
        directory: Path to the directory
        
    Returns:
        Size in bytes
    """
    try:
        dir_path = Path(directory)
        
        if not dir_path.exists() or not dir_path.is_dir():
            return 0
        
        total_size = 0
        
        for path in dir_path.glob("**/*"):
            if path.is_file():
                total_size += path.stat().st_size
        
        return total_size
    except Exception as e:
        logger.error(f"Error getting directory size: {e}")
        return 0
