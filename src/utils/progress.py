"""
Progress tracking utility for long-running operations.
"""
import time
import logging
import json
import threading
from typing import Dict, Any, Optional, List
from pathlib import Path
import os

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class ProgressTracker:
    """
    Tracks progress of long-running operations and stores progress data.
    """
    
    # Class-level storage for progress data
    # This ensures progress is tracked across multiple instances
    _progress_data = {}
    _lock = threading.RLock()  # Reentrant lock for thread safety
    
    def __init__(self, job_id: str):
        """
        Initialize a progress tracker for a specific job.
        
        Args:
            job_id: Unique identifier for the job
        """
        self.job_id = job_id
        
        # Ensure the job exists in the progress data
        with self._lock:
            if job_id not in self._progress_data:
                self._progress_data[job_id] = {
                    "job_id": job_id,
                    "status": "initializing",
                    "progress": 0,
                    "start_time": time.time(),
                    "end_time": None,
                    "message": None,
                    "params": {}
                }
    
    def init_job(
        self, 
        job_type: str, 
        status: str = "starting",
        video_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Initialize job information.
        
        Args:
            job_type: Type of job (e.g., "youtube_download", "video_processing")
            status: Initial status
            video_id: Optional video ID if the job is related to a video
            params: Optional parameters for the job
            
        Returns:
            Dict containing the current job information
        """
        with self._lock:
            self._progress_data[self.job_id].update({
                "job_type": job_type,
                "status": status,
                "progress": 0,
                "start_time": time.time(),
                "message": "Job initialized",
                "params": params or {},
            })
            
            if video_id:
                self._progress_data[self.job_id]["video_id"] = video_id
            
            # Save progress data to disk
            self._save_progress_data()
            
            return self._progress_data[self.job_id].copy()
    
    def update_progress(
        self, 
        progress: int,
        status: Optional[str] = None,
        message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update the progress of a job.
        
        Args:
            progress: Progress percentage (0-100)
            status: Optional status update
            message: Optional message
            
        Returns:
            Dict containing the current job information
        """
        with self._lock:
            if self.job_id not in self._progress_data:
                # Job not found, create it
                self._progress_data[self.job_id] = {
                    "job_id": self.job_id,
                    "status": "unknown",
                    "progress": 0,
                    "start_time": time.time(),
                    "end_time": None,
                    "message": None,
                    "params": {}
                }
            
            # Update progress data
            self._progress_data[self.job_id]["progress"] = max(0, min(100, progress))
            
            if status:
                self._progress_data[self.job_id]["status"] = status
                
                # If status is a completion status, set end_time
                if status in ["complete", "error", "cancelled"]:
                    self._progress_data[self.job_id]["end_time"] = time.time()
            
            if message:
                self._progress_data[self.job_id]["message"] = message
                
            # Update timestamp
            self._progress_data[self.job_id]["updated_at"] = time.time()
            
            # Save progress data to disk
            self._save_progress_data()
            
            # Log the progress update
            log_message = (
                f"Job {self.job_id} progress: {progress}%, "
                f"status: {self._progress_data[self.job_id]['status']}"
            )
            if message:
                log_message += f", message: {message}"
                
            logger.info(log_message)
            
            return self._progress_data[self.job_id].copy()
    
    def get_progress(self) -> Dict[str, Any]:
        """
        Get the current progress information for a job.
        
        Returns:
            Dict containing the current job information
        """
        with self._lock:
            if self.job_id not in self._progress_data:
                return {
                    "job_id": self.job_id,
                    "status": "not_found",
                    "progress": 0,
                    "error": "Job not found"
                }
            
            return self._progress_data[self.job_id].copy()
    
    def _save_progress_data(self):
        """Save progress data to disk."""
        try:
            # Create the logs directory if it doesn't exist
            logs_dir = Path(settings.get_absolute_path("logs"))
            logs_dir.mkdir(exist_ok=True)
            
            # Create a progress subdirectory
            progress_dir = logs_dir / "progress"
            progress_dir.mkdir(exist_ok=True)
            
            # Save the progress data for this job
            progress_file = progress_dir / f"{self.job_id}.json"
            
            with open(progress_file, 'w') as f:
                json.dump(self._progress_data[self.job_id], f, indent=2)
                
        except Exception as e:
            logger.error(f"Error saving progress data: {e}")
    
    @classmethod
    def get_all_jobs(cls) -> List[Dict[str, Any]]:
        """
        Get information for all tracked jobs.
        
        Returns:
            List of job information dictionaries
        """
        with cls._lock:
            return list(cls._progress_data.values())
    
    @classmethod
    def get_job(cls, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information for a specific job.
        
        Args:
            job_id: Unique identifier for the job
            
        Returns:
            Dict containing the job information, or None if not found
        """
        with cls._lock:
            return cls._progress_data.get(job_id, {}).copy()
    
    @classmethod
    def clean_completed_jobs(cls, max_age_seconds: int = 86400) -> int:
        """
        Remove completed jobs older than the specified age.
        
        Args:
            max_age_seconds: Maximum age in seconds (default: 24 hours)
            
        Returns:
            Number of jobs removed
        """
        current_time = time.time()
        jobs_to_remove = []
        
        with cls._lock:
            # Find jobs to remove
            for job_id, job_data in cls._progress_data.items():
                if job_data.get("status") in ["complete", "error", "cancelled"]:
                    end_time = job_data.get("end_time")
                    
                    if end_time and (current_time - end_time) > max_age_seconds:
                        jobs_to_remove.append(job_id)
            
            # Remove the jobs
            for job_id in jobs_to_remove:
                del cls._progress_data[job_id]
                
                # Also remove the progress file
                try:
                    progress_file = Path(settings.get_absolute_path("logs")) / "progress" / f"{job_id}.json"
                    if progress_file.exists():
                        os.remove(progress_file)
                except Exception as e:
                    logger.error(f"Error removing progress file for job {job_id}: {e}")
        
        return len(jobs_to_remove)
