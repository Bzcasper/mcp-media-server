"""
Backup manager for MCP Media Server.
Handles automated backups, retention policies, and restoration.
"""
import os
import json
import shutil
import logging
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Tuple
import asyncio

from src.config.settings import get_settings
from src.config.key_manager import key_manager
from src.utils.file_utils import list_directory, ensure_directory_exists

logger = logging.getLogger(__name__)
settings = get_settings()

class BackupManager:
    """
    Manages backups of the MCP Media Server data.
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super(BackupManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the backup manager."""
        if self._initialized:
            return
            
        # Backup storage paths
        self.backups_dir = Path(settings.get_absolute_path("backups"))
        self.backups_dir.mkdir(exist_ok=True)
        self.metadata_path = self.backups_dir / "backup_metadata.json"
        
        # Backup configuration
        self.retention_days = 30
        self.max_backups = 10
        
        # Load backup metadata
        self.metadata = self._load_metadata()
        
        # Track ongoing backup operations
        self.active_backups = set()
        
        self._initialized = True
        logger.info("Backup Manager initialized")
    
    def _load_metadata(self) -> Dict[str, Any]:
        """Load backup metadata from storage."""
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading backup metadata: {e}")
        
        # Initialize fresh metadata
        return {
            "backups": [],
            "last_backup": None,
            "total_backups": 0
        }
    
    def _save_metadata(self):
        """Save backup metadata to storage."""
        try:
            with open(self.metadata_path, "w") as f:
                json.dump(self.metadata, f, indent=2)
            logger.info("Backup metadata saved")
        except Exception as e:
            logger.error(f"Error saving backup metadata: {e}")
    
    async def create_backup(self, backup_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a backup of the MCP Media Server data.
        
        Args:
            backup_name: Optional name for the backup
            
        Returns:
            Dict containing backup information
        """
        # Generate backup name if not provided
        if not backup_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"mcp_backup_{timestamp}"
        
        # Create a unique backup ID
        backup_id = f"{backup_name}_{datetime.now().timestamp()}"
        
        # Add to active backups
        self.active_backups.add(backup_id)
        
        try:
            # Directories to backup
            backup_dirs = [
                "logs",
                "downloads",
                "processed",
                "thumbnails",
                "keys"
            ]
            
            # Create temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Backup directories
                for dir_name in backup_dirs:
                    source_dir = settings.get_absolute_path(dir_name)
                    if Path(source_dir).exists():
                        # Create target directory
                        target_dir = temp_path / dir_name
                        await ensure_directory_exists(target_dir)
                        
                        # Copy files
                        try:
                            files = await list_directory(source_dir)
                            for file_info in files:
                                if file_info["is_file"]:
                                    source_file = Path(file_info["path"])
                                    target_file = target_dir / file_info["name"]
                                    shutil.copy2(source_file, target_file)
                        except Exception as e:
                            logger.warning(f"Error backing up {dir_name}: {e}")
                
                # Export environment variables (without sensitive data)
                env_vars = {}
                for key, value in os.environ.items():
                    if key.startswith("MCP_") and not key.endswith("_KEY") and not key.endswith("_PASSWORD"):
                        env_vars[key] = value
                
                # Save environment variables
                env_file = temp_path / "environment.json"
                with open(env_file, "w") as f:
                    json.dump(env_vars, f, indent=2)
                
                # Create the backup archive
                backup_path = self.backups_dir / f"{backup_name}.tar.gz"
                with tarfile.open(backup_path, "w:gz") as tar:
                    tar.add(temp_dir, arcname=backup_name)
                
                # Update metadata
                backup_info = {
                    "id": backup_id,
                    "name": backup_name,
                    "path": str(backup_path),
                    "size": backup_path.stat().st_size,
                    "timestamp": datetime.now().isoformat(),
                    "directories": backup_dirs
                }
                
                self.metadata["backups"].append(backup_info)
                self.metadata["last_backup"] = backup_info
                self.metadata["total_backups"] = len(self.metadata["backups"])
                
                # Save metadata
                self._save_metadata()
                
                # Apply retention policy
                await self._apply_retention_policy()
                
                logger.info(f"Backup created: {backup_name}")
                return backup_info
        
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return {
                "id": backup_id,
                "name": backup_name,
                "error": str(e),
                "status": "failed",
                "timestamp": datetime.now().isoformat()
            }
        finally:
            # Remove from active backups
            self.active_backups.discard(backup_id)
    
    async def restore_backup(self, backup_id: str) -> Dict[str, Any]:
        """
        Restore a backup.
        
        Args:
            backup_id: ID of the backup to restore
            
        Returns:
            Dict containing restoration information
        """
        # Find the backup
        backup_info = None
        for backup in self.metadata["backups"]:
            if backup["id"] == backup_id:
                backup_info = backup
                break
        
        if not backup_info:
            logger.error(f"Backup not found: {backup_id}")
            return {
                "status": "error",
                "message": f"Backup not found: {backup_id}"
            }
        
        try:
            backup_path = Path(backup_info["path"])
            if not backup_path.exists():
                logger.error(f"Backup file not found: {backup_path}")
                return {
                    "status": "error",
                    "message": f"Backup file not found: {backup_path}"
                }
            
            # Create a temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Extract the backup
                with tarfile.open(backup_path, "r:gz") as tar:
                    tar.extractall(temp_path)
                
                backup_name = backup_info["name"]
                extracted_path = temp_path / backup_name
                
                # Restore directories
                for dir_name in backup_info["directories"]:
                    source_dir = extracted_path / dir_name
                    if source_dir.exists():
                        target_dir = settings.get_absolute_path(dir_name)
                        
                        # Create a backup of the current data
                        backup_dir = Path(str(target_dir) + ".bak")
                        if Path(target_dir).exists():
                            shutil.copytree(target_dir, backup_dir, dirs_exist_ok=True)
                        
                        # Restore files
                        try:
                            await ensure_directory_exists(target_dir)
                            for item in source_dir.glob("*"):
                                if item.is_file():
                                    shutil.copy2(item, target_dir / item.name)
                        except Exception as e:
                            logger.error(f"Error restoring {dir_name}: {e}")
                            
                            # Rollback
                            if backup_dir.exists():
                                shutil.rmtree(target_dir, ignore_errors=True)
                                shutil.copytree(backup_dir, target_dir, dirs_exist_ok=True)
                            
                            raise
                
                # Cleanup backups
                for dir_name in backup_info["directories"]:
                    backup_dir = Path(str(settings.get_absolute_path(dir_name)) + ".bak")
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir, ignore_errors=True)
                
                logger.info(f"Backup restored: {backup_id}")
                return {
                    "status": "success",
                    "backup_id": backup_id,
                    "message": "Backup restored successfully",
                    "timestamp": datetime.now().isoformat()
                }
        
        except Exception as e:
            logger.error(f"Error restoring backup: {e}")
            return {
                "status": "error",
                "backup_id": backup_id,
                "message": f"Error restoring backup: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
    
    async def list_backups(self) -> List[Dict[str, Any]]:
        """
        List all available backups.
        
        Returns:
            List of backup information dictionaries
        """
        try:
            # Verify and clean up backup list
            valid_backups = []
            for backup in self.metadata["backups"]:
                backup_path = Path(backup["path"])
                if backup_path.exists():
                    valid_backups.append(backup)
                else:
                    logger.warning(f"Backup file not found, removing from metadata: {backup['path']}")
            
            # Update metadata if needed
            if len(valid_backups) != len(self.metadata["backups"]):
                self.metadata["backups"] = valid_backups
                self.metadata["total_backups"] = len(valid_backups)
                self._save_metadata()
            
            return valid_backups
        
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return []
    
    async def delete_backup(self, backup_id: str) -> bool:
        """
        Delete a backup.
        
        Args:
            backup_id: ID of the backup to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Find the backup
            backup_info = None
            for i, backup in enumerate(self.metadata["backups"]):
                if backup["id"] == backup_id:
                    backup_info = backup
                    backup_index = i
                    break
            
            if not backup_info:
                logger.error(f"Backup not found: {backup_id}")
                return False
            
            # Delete the file
            backup_path = Path(backup_info["path"])
            if backup_path.exists():
                backup_path.unlink()
            
            # Update metadata
            self.metadata["backups"].pop(backup_index)
            self.metadata["total_backups"] = len(self.metadata["backups"])
            self._save_metadata()
            
            logger.info(f"Backup deleted: {backup_id}")
            return True
        
        except Exception as e:
            logger.error(f"Error deleting backup: {e}")
            return False
    
    async def _apply_retention_policy(self):
        """Apply the backup retention policy."""
        try:
            # Sort backups by timestamp (oldest first)
            backups = sorted(
                self.metadata["backups"],
                key=lambda x: datetime.fromisoformat(x["timestamp"])
            )
            
            # Keep the most recent backups
            if len(backups) > self.max_backups:
                backups_to_delete = backups[:-self.max_backups]
                
                for backup in backups_to_delete:
                    await self.delete_backup(backup["id"])
        
        except Exception as e:
            logger.error(f"Error applying retention policy: {e}")
    
    async def schedule_automatic_backups(self, interval_hours: int = 24):
        """
        Schedule automatic backups at specified intervals.
        
        Args:
            interval_hours: Interval between backups in hours
        """
        try:
            while True:
                # Create a backup
                await self.create_backup()
                
                # Wait for the next backup
                await asyncio.sleep(interval_hours * 3600)
        
        except asyncio.CancelledError:
            logger.info("Automatic backup scheduler stopped")
        except Exception as e:
            logger.error(f"Error in automatic backup scheduler: {e}")
            # Auto-restart the scheduler after a delay
            await asyncio.sleep(60)
            asyncio.create_task(self.schedule_automatic_backups(interval_hours))


# Create and export the backup manager instance
backup_manager = BackupManager()
