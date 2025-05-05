"""
Caching utility for storing and retrieving frequently used data.
"""
import os
import time
import json
import hashlib
import logging
import threading
from typing import Dict, Any, Optional, List, Union, Tuple
from pathlib import Path
import pickle

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class Cache:
    """
    Caching system with memory and disk storage options.
    """
    
    # Class-level storage for memory cache
    _memory_cache = {}
    _expiry_times = {}
    _lock = threading.RLock()  # Reentrant lock for thread safety
    
    def __init__(self, use_disk_cache: bool = True, max_memory_items: int = 1000):
        """
        Initialize the cache.
        
        Args:
            use_disk_cache: Whether to use disk cache in addition to memory cache
            max_memory_items: Maximum number of items to store in memory cache
        """
        self.use_disk_cache = use_disk_cache
        self.max_memory_items = max_memory_items
        
        # Create cache directory if using disk cache
        if self.use_disk_cache:
            self.cache_dir = Path(settings.get_absolute_path(settings.CACHE_DIR))
            self.cache_dir.mkdir(exist_ok=True)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a value from the cache.
        
        Args:
            key: Cache key
            default: Default value to return if key not found
            
        Returns:
            Cached value or default
        """
        # Check memory cache first
        with self._lock:
            # Check if the key exists and is not expired
            if key in self._memory_cache:
                if key in self._expiry_times and self._expiry_times[key] < time.time():
                    # Key has expired, remove it
                    del self._memory_cache[key]
                    del self._expiry_times[key]
                else:
                    # Key exists and is not expired
                    return self._memory_cache[key]
        
        # If not in memory cache and disk cache is enabled, check disk
        if self.use_disk_cache:
            try:
                # Generate disk cache key (hash of the original key)
                disk_key = self._get_disk_key(key)
                cache_file = self.cache_dir / f"{disk_key}.cache"
                
                if cache_file.exists():
                    # Read the cache file
                    with open(cache_file, 'rb') as f:
                        data = pickle.load(f)
                        
                    # Check if the data has expired
                    if "expiry" in data and data["expiry"] < time.time():
                        # Data has expired, remove it
                        os.remove(cache_file)
                        return default
                    
                    # Data is valid, store in memory cache for faster access next time
                    with self._lock:
                        self._memory_cache[key] = data["value"]
                        if "expiry" in data:
                            self._expiry_times[key] = data["expiry"]
                        
                        # Check if memory cache is too large
                        self._check_memory_cache_size()
                    
                    return data["value"]
                    
            except Exception as e:
                logger.error(f"Error reading from disk cache: {e}")
        
        # Not found in any cache
        return default
    
    def set(self, key: str, value: Any, expire_in: Optional[int] = None) -> bool:
        """
        Set a value in the cache.
        
        Args:
            key: Cache key
            value: Value to cache
            expire_in: Expiration time in seconds (None for no expiration)
            
        Returns:
            True if successful, False otherwise
        """
        expiry = None
        if expire_in is not None:
            expiry = time.time() + expire_in
        
        # Store in memory cache
        with self._lock:
            self._memory_cache[key] = value
            if expiry:
                self._expiry_times[key] = expiry
                
            # Check if memory cache is too large
            self._check_memory_cache_size()
        
        # Store in disk cache if enabled
        if self.use_disk_cache:
            try:
                # Generate disk cache key (hash of the original key)
                disk_key = self._get_disk_key(key)
                cache_file = self.cache_dir / f"{disk_key}.cache"
                
                # Prepare data for disk cache
                data = {
                    "key": key,
                    "value": value,
                    "created_at": time.time()
                }
                
                if expiry:
                    data["expiry"] = expiry
                
                # Write to disk cache
                with open(cache_file, 'wb') as f:
                    pickle.dump(data, f)
                    
                return True
                
            except Exception as e:
                logger.error(f"Error writing to disk cache: {e}")
                return False
        
        return True
    
    def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if found and deleted, False otherwise
        """
        found = False
        
        # Remove from memory cache
        with self._lock:
            if key in self._memory_cache:
                del self._memory_cache[key]
                found = True
            
            if key in self._expiry_times:
                del self._expiry_times[key]
        
        # Remove from disk cache if enabled
        if self.use_disk_cache:
            try:
                # Generate disk cache key (hash of the original key)
                disk_key = self._get_disk_key(key)
                cache_file = self.cache_dir / f"{disk_key}.cache"
                
                if cache_file.exists():
                    os.remove(cache_file)
                    found = True
                    
            except Exception as e:
                logger.error(f"Error deleting from disk cache: {e}")
        
        return found
    
    def clear(self) -> bool:
        """
        Clear all cached values.
        
        Returns:
            True if successful, False otherwise
        """
        # Clear memory cache
        with self._lock:
            self._memory_cache.clear()
            self._expiry_times.clear()
        
        # Clear disk cache if enabled
        if self.use_disk_cache:
            try:
                # Remove all cache files
                for cache_file in self.cache_dir.glob("*.cache"):
                    try:
                        os.remove(cache_file)
                    except Exception as e:
                        logger.error(f"Error removing cache file {cache_file}: {e}")
                        
                return True
                
            except Exception as e:
                logger.error(f"Error clearing disk cache: {e}")
                return False
        
        return True
    
    def clean_expired(self) -> Tuple[int, int]:
        """
        Clean expired cached values.
        
        Returns:
            Tuple of (memory_items_removed, disk_items_removed)
        """
        memory_removed = 0
        disk_removed = 0
        
        # Clean memory cache
        current_time = time.time()
        with self._lock:
            # Find expired keys
            expired_keys = [
                key for key, expiry in self._expiry_times.items()
                if expiry < current_time
            ]
            
            # Remove expired keys
            for key in expired_keys:
                del self._memory_cache[key]
                del self._expiry_times[key]
                memory_removed += 1
        
        # Clean disk cache if enabled
        if self.use_disk_cache:
            try:
                # Check each cache file
                for cache_file in self.cache_dir.glob("*.cache"):
                    try:
                        with open(cache_file, 'rb') as f:
                            data = pickle.load(f)
                            
                        # Check if the data has expired
                        if "expiry" in data and data["expiry"] < current_time:
                            # Data has expired, remove it
                            os.remove(cache_file)
                            disk_removed += 1
                            
                    except Exception as e:
                        logger.error(f"Error checking cache file {cache_file}: {e}")
                        # Remove invalid cache files
                        try:
                            os.remove(cache_file)
                            disk_removed += 1
                        except:
                            pass
                            
            except Exception as e:
                logger.error(f"Error cleaning disk cache: {e}")
        
        return (memory_removed, disk_removed)
    
    def _get_disk_key(self, key: str) -> str:
        """
        Generate a disk cache key from the original key.
        
        Args:
            key: Original cache key
            
        Returns:
            Hashed key for disk storage
        """
        # Generate a hash of the key
        hash_obj = hashlib.md5(key.encode('utf-8'))
        return hash_obj.hexdigest()
    
    def _check_memory_cache_size(self):
        """Check if memory cache is too large and evict items if needed."""
        if len(self._memory_cache) > self.max_memory_items:
            # Remove the oldest items first (those with earliest expiry)
            items_to_remove = len(self._memory_cache) - self.max_memory_items
            
            # Sort keys by expiry time (oldest first)
            sorted_keys = sorted(
                self._expiry_times.keys(),
                key=lambda k: self._expiry_times.get(k, float('inf'))
            )
            
            # Remove the oldest items
            for key in sorted_keys[:items_to_remove]:
                del self._memory_cache[key]
                del self._expiry_times[key]
