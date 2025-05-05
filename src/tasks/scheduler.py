"""
Scheduled tasks for the MCP Media Server.
"""
import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union, Callable, Coroutine
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from src.config.settings import get_settings
from src.utils.progress import ProgressTracker
from src.utils.cache import Cache
from src.services.webhook_service import retry_failed_webhooks

logger = logging.getLogger(__name__)
settings = get_settings()

class TaskScheduler:
    """
    Scheduler for running background tasks.
    """
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Create a new instance if one doesn't exist."""
        if cls._instance is None:
            cls._instance = super(TaskScheduler, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the scheduler if not already initialized."""
        if self._initialized:
            return
            
        # Create the scheduler
        self.scheduler = AsyncIOScheduler()
        
        # Configure job stores
        self.scheduler.configure(
            jobstores={
                'default': MemoryJobStore(),
                'persistent': MemoryJobStore(),
            },
            job_defaults={
                'coalesce': True,
                'max_instances': 1,
                'misfire_grace_time': 60
            }
        )
        
        # Initialize task registry
        self.tasks = {}
        
        # Mark as initialized
        self._initialized = True
        logger.info("Task scheduler initialized")
    
    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            try:
                # Add default maintenance tasks
                self._add_maintenance_tasks()
                
                # Start the scheduler
                self.scheduler.start()
                logger.info("Task scheduler started")
            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}")
    
    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            try:
                self.scheduler.shutdown()
                logger.info("Task scheduler stopped")
            except Exception as e:
                logger.error(f"Failed to stop scheduler: {e}")
    
    def add_task(
        self,
        task_id: str,
        func: Callable[..., Coroutine],
        trigger: Union[str, CronTrigger, IntervalTrigger],
        args: Optional[List] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        jobstore: str = 'default',
        replace_existing: bool = True
    ):
        """
        Add a task to the scheduler.
        
        Args:
            task_id: Unique identifier for the task
            func: Function to run
            trigger: Trigger for the task
            args: Positional arguments for the function
            kwargs: Keyword arguments for the function
            jobstore: Job store to use
            replace_existing: Whether to replace an existing task with the same ID
            
        Returns:
            Job ID if successful, None otherwise
        """
        try:
            # Add the job to the scheduler
            job = self.scheduler.add_job(
                func=func,
                trigger=trigger,
                args=args or [],
                kwargs=kwargs or {},
                id=task_id,
                jobstore=jobstore,
                replace_existing=replace_existing
            )
            
            # Register the task
            self.tasks[task_id] = {
                "id": task_id,
                "function": func.__name__,
                "next_run_time": job.next_run_time,
                "trigger": str(trigger),
                "jobstore": jobstore
            }
            
            logger.info(f"Task added: {task_id}, next run: {job.next_run_time}")
            return task_id
            
        except Exception as e:
            logger.error(f"Failed to add task {task_id}: {e}")
            return None
    
    def remove_task(self, task_id: str):
        """
        Remove a task from the scheduler.
        
        Args:
            task_id: ID of the task to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Remove the job from the scheduler
            self.scheduler.remove_job(task_id)
            
            # Remove from the registry
            if task_id in self.tasks:
                del self.tasks[task_id]
            
            logger.info(f"Task removed: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove task {task_id}: {e}")
            return False
    
    def get_tasks(self) -> List[Dict[str, Any]]:
        """
        Get all registered tasks.
        
        Returns:
            List of task information dictionaries
        """
        tasks = []
        
        for job in self.scheduler.get_jobs():
            task_info = {
                "id": job.id,
                "function": job.func.__name__,
                "next_run_time": job.next_run_time,
                "trigger": str(job.trigger),
                "jobstore": job.jobstore
            }
            
            tasks.append(task_info)
        
        return tasks
    
    def _add_maintenance_tasks(self):
        """Add default maintenance tasks."""
        # Only add maintenance tasks if enabled
        if not settings.SCHEDULED_TASKS_ENABLED:
            logger.info("Scheduled tasks are disabled")
            return
            
        # Clean up expired cache entries hourly
        self.add_task(
            task_id="clean_cache",
            func=self._clean_cache,
            trigger=IntervalTrigger(hours=1),
            jobstore="persistent"
        )
        
        # Clean up old progress tracking data daily
        self.add_task(
            task_id="clean_progress_data",
            func=self._clean_progress_data,
            trigger=CronTrigger(hour=2, minute=0),  # 2:00 AM
            jobstore="persistent"
        )
        
        # Retry failed webhooks every 5 minutes
        self.add_task(
            task_id="retry_failed_webhooks",
            func=retry_failed_webhooks,
            trigger=IntervalTrigger(minutes=5),
            jobstore="persistent"
        )
        
        logger.info("Maintenance tasks added")
    
    async def _clean_cache(self):
        """Clean expired cache entries."""
        try:
            cache = Cache()
            memory_removed, disk_removed = cache.clean_expired()
            
            logger.info(
                f"Cache cleanup: removed {memory_removed} memory items, "
                f"{disk_removed} disk items"
            )
            
        except Exception as e:
            logger.error(f"Error cleaning cache: {e}")
    
    async def _clean_progress_data(self):
        """Clean old progress tracking data."""
        try:
            # Clean progress data older than 7 days
            removed = ProgressTracker.clean_completed_jobs(
                max_age_seconds=7 * 24 * 60 * 60  # 7 days
            )
            
            logger.info(f"Progress data cleanup: removed {removed} old entries")
            
        except Exception as e:
            logger.error(f"Error cleaning progress data: {e}")


# Create and export the scheduler instance
scheduler = TaskScheduler()


# Add additional task functions here
async def batch_generate_embeddings_task():
    """Generate embeddings for videos that don't have them yet."""
    from src.tools.vector_tools import batch_generate_embeddings
    
    try:
        result = await batch_generate_embeddings(
            video_ids=None,  # Process all videos without embeddings
            limit=50,  # Process 50 videos at a time
            include_audio_transcription=True
        )
        
        logger.info(
            f"Batch embedding generation: processed {result.get('total_processed')} videos, "
            f"successful: {result.get('successful')}, failed: {result.get('failed')}"
        )
        
    except Exception as e:
        logger.error(f"Error in batch_generate_embeddings_task: {e}")


async def cleanup_temporary_files_task():
    """Clean up temporary files."""
    try:
        # Get paths to cleanup
        download_dir = settings.get_absolute_path(settings.DOWNLOAD_DIR)
        processed_dir = settings.get_absolute_path(settings.PROCESSED_DIR)
        cache_dir = settings.get_absolute_path(settings.CACHE_DIR)
        
        # Get current time
        now = time.time()
        
        # Threshold for old files (7 days)
        threshold = now - (7 * 24 * 60 * 60)
        
        # Clean up download directory
        removed_count = 0
        
        for root, dirs, files in os.walk(download_dir):
            for file in files:
                file_path = os.path.join(root, file)
                
                # Check if file is older than the threshold
                file_modified = os.path.getmtime(file_path)
                if file_modified < threshold:
                    try:
                        os.remove(file_path)
                        removed_count += 1
                    except Exception as e:
                        logger.error(f"Error removing file {file_path}: {e}")
        
        # Log results
        logger.info(f"Temporary files cleanup: removed {removed_count} old files")
        
    except Exception as e:
        logger.error(f"Error in cleanup_temporary_files_task: {e}")
