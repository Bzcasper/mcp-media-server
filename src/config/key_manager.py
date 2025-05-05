"""
Secure key management for MCP Media Server.
Handles encryption, rotation, and fallbacks for API keys.
"""
import os
import json
import base64
import logging
import secrets
from pathlib import Path
from typing import Dict, Any, Optional, Union
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class KeyManager:
    """
    Secure key management with encryption, rotation and fallbacks.
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super(KeyManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the key manager."""
        if self._initialized:
            return
            
        # Key storage paths
        self.keys_dir = Path(settings.get_absolute_path("keys"))
        self.keys_dir.mkdir(exist_ok=True)
        self.encrypted_keys_path = self.keys_dir / "encrypted_keys.json"
        self.rotation_log_path = self.keys_dir / "rotation_log.json"
        
        # Initialize the encryption key
        self._initialize_encryption()
        
        # Load or initialize keys
        self.keys = self._load_keys()
        
        # Track when keys were last accessed
        self.key_access = {}
        
        self._initialized = True
        logger.info("Key Manager initialized")
    
    def _initialize_encryption(self):
        """Initialize the encryption mechanism."""
        # Get or generate a master password
        master_password = os.environ.get("MCP_MASTER_PASSWORD")
        if not master_password:
            # Check if we have a stored key
            key_file = self.keys_dir / ".master.key"
            if key_file.exists():
                with open(key_file, "rb") as f:
                    master_password = f.read().decode('utf-8')
            else:
                # Generate a secure random password and store it
                master_password = secrets.token_hex(32)
                # Only create the key file in development
                if settings.DEBUG:
                    with open(key_file, "wb") as f:
                        f.write(master_password.encode('utf-8'))
                    key_file.chmod(0o600)  # Restrictive permissions
        
        # Derive a key from the password
        password = master_password.encode()
        salt = b'mcp_media_server_salt_fixed'  # Using a fixed salt for reproducibility
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        
        key = base64.urlsafe_b64encode(kdf.derive(password))
        self.cipher = Fernet(key)
    
    def _load_keys(self) -> Dict[str, str]:
        """Load keys from encrypted storage or initialize defaults."""
        if self.encrypted_keys_path.exists():
            try:
                with open(self.encrypted_keys_path, "rb") as f:
                    encrypted_data = f.read()
                
                decrypted_data = self.cipher.decrypt(encrypted_data)
                return json.loads(decrypted_data)
            except Exception as e:
                logger.error(f"Error loading encrypted keys: {e}")
                logger.warning("Using environment variables as fallback")
        
        # Initialize with environment variables
        keys = {
            "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
            "SUPABASE_KEY": os.environ.get("SUPABASE_KEY", ""),
            "PINECONE_API_KEY": os.environ.get("PINECONE_API_KEY", ""),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        }
        
        # Save the keys
        self._save_keys(keys)
        
        return keys
    
    def _save_keys(self, keys: Dict[str, str]):
        """Save keys to encrypted storage."""
        try:
            # Encrypt the data
            encrypted_data = self.cipher.encrypt(json.dumps(keys).encode())
            
            # Create a temporary file first (atomic write)
            temp_path = self.encrypted_keys_path.with_suffix('.tmp')
            with open(temp_path, "wb") as f:
                f.write(encrypted_data)
            
            # Rename to the actual file (atomic operation)
            temp_path.rename(self.encrypted_keys_path)
            
            # Set restrictive permissions
            self.encrypted_keys_path.chmod(0o600)
            
            logger.info("Keys saved to encrypted storage")
        except Exception as e:
            logger.error(f"Error saving encrypted keys: {e}")
    
    def get_key(self, key_name: str, default: str = "") -> str:
        """
        Get a key value.
        
        Args:
            key_name: Name of the key
            default: Default value if key not found
            
        Returns:
            The key value
        """
        # Record access time
        self.key_access[key_name] = datetime.now()
        
        # Try to get from stored keys
        value = self.keys.get(key_name, "")
        
        # If not found, try environment
        if not value:
            value = os.environ.get(key_name, default)
        
        return value
    
    def set_key(self, key_name: str, value: str) -> bool:
        """
        Set a key value.
        
        Args:
            key_name: Name of the key
            value: Value of the key
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Update the key
            self.keys[key_name] = value
            
            # Save the keys
            self._save_keys(self.keys)
            
            # Record access time
            self.key_access[key_name] = datetime.now()
            
            return True
        except Exception as e:
            logger.error(f"Error setting key {key_name}: {e}")
            return False
    
    def rotate_key(self, key_name: str, new_value: str) -> bool:
        """
        Rotate a key with a new value while keeping the old one as a backup.
        
        Args:
            key_name: Name of the key
            new_value: New value for the key
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get the old value
            old_value = self.get_key(key_name, "")
            
            # Update the key
            self.keys[key_name] = new_value
            
            # Save the backup
            backup_key_name = f"{key_name}_backup"
            self.keys[backup_key_name] = old_value
            
            # Log the rotation
            self._log_rotation(key_name)
            
            # Save the keys
            self._save_keys(self.keys)
            
            logger.info(f"Key {key_name} rotated successfully")
            return True
        except Exception as e:
            logger.error(f"Error rotating key {key_name}: {e}")
            return False
    
    def _log_rotation(self, key_name: str):
        """Log key rotation events."""
        rotation_log = {}
        
        # Load existing log if it exists
        if self.rotation_log_path.exists():
            try:
                with open(self.rotation_log_path, "r") as f:
                    rotation_log = json.load(f)
            except Exception as e:
                logger.error(f"Error loading rotation log: {e}")
        
        # Update the log
        if key_name not in rotation_log:
            rotation_log[key_name] = []
        
        rotation_log[key_name].append({
            "timestamp": datetime.now().isoformat(),
            "rotated_by": "system"
        })
        
        # Save the log
        try:
            with open(self.rotation_log_path, "w") as f:
                json.dump(rotation_log, f, indent=2)
            
            # Set restrictive permissions
            self.rotation_log_path.chmod(0o600)
        except Exception as e:
            logger.error(f"Error saving rotation log: {e}")
    
    def get_backup_key(self, key_name: str) -> str:
        """
        Get a backup key value.
        
        Args:
            key_name: Name of the key
            
        Returns:
            The backup key value or empty string if not found
        """
        backup_key_name = f"{key_name}_backup"
        return self.get_key(backup_key_name, "")
    
    def verify_key(self, key_name: str) -> bool:
        """
        Verify that a key exists and is not empty.
        
        Args:
            key_name: Name of the key
            
        Returns:
            True if the key exists and is not empty, False otherwise
        """
        return bool(self.get_key(key_name, ""))
    
    def get_all_required_keys(self) -> Dict[str, bool]:
        """
        Check all required keys and return their status.
        
        Returns:
            Dict mapping key names to validity status
        """
        required_keys = [
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "PINECONE_API_KEY",
            "OPENAI_API_KEY",
            "JWT_SECRET"
        ]
        
        return {key: self.verify_key(key) for key in required_keys}


# Create and export the key manager instance
key_manager = KeyManager()
