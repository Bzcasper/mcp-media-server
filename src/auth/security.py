"""
Authentication and security utilities for the MCP Media Server.
"""
import os
import time
import logging
import json
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
import secrets
import string

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from src.config.settings import get_settings
from src.db.supabase_init import get_supabase_client

logger = logging.getLogger(__name__)
settings = get_settings()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token settings
SECRET_KEY = settings.JWT_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES


class Token(BaseModel):
    """Token model."""
    access_token: str
    token_type: str
    expires_at: datetime
    user_id: str


class TokenData(BaseModel):
    """Token data model."""
    user_id: Optional[str] = None
    email: Optional[str] = None
    permissions: List[str] = []
    exp: Optional[int] = None


class UserData(BaseModel):
    """User data model."""
    id: str
    email: str
    display_name: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    permissions: List[str] = []


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate a password hash."""
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Data to encode in the token
        expires_delta: Optional expiration time delta
        
    Returns:
        JWT token string
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    return encoded_jwt


async def get_user_by_email(email: str) -> Optional[UserData]:
    """
    Get a user by email.
    
    Args:
        email: User's email address
        
    Returns:
        UserData if found, None otherwise
    """
    try:
        supabase = get_supabase_client()
        
        # Check if the user exists in Supabase Auth
        user_response = supabase.auth.admin.get_user_by_email(email)
        
        if not user_response or not user_response.user:
            return None
        
        # Get user data from the database
        user_id = user_response.user.id
        user_data_response = supabase.table("users") \
            .select("*") \
            .eq("id", user_id) \
            .limit(1) \
            .execute()
        
        if not user_data_response.data:
            # User exists in Auth but not in the database
            # Create a basic record
            user_data = {
                "id": user_id,
                "email": email,
                "display_name": email.split("@")[0],
                "is_active": True,
                "is_admin": False,
                "permissions": ["read"]
            }
            
            supabase.table("users").insert(user_data).execute()
        else:
            user_data = user_data_response.data[0]
        
        return UserData(
            id=user_id,
            email=email,
            display_name=user_data.get("display_name"),
            is_active=user_data.get("is_active", True),
            is_admin=user_data.get("is_admin", False),
            permissions=user_data.get("permissions", ["read"])
        )
    
    except Exception as e:
        logger.error(f"Error getting user by email: {e}")
        return None


async def authenticate_user(email: str, password: str) -> Optional[UserData]:
    """
    Authenticate a user with email and password.
    
    Args:
        email: User's email address
        password: User's password
        
    Returns:
        UserData if authentication successful, None otherwise
    """
    try:
        supabase = get_supabase_client()
        
        # Sign in with email and password
        auth_response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if not auth_response or not auth_response.user:
            return None
        
        # Get user data
        user_id = auth_response.user.id
        user_data_response = supabase.table("users") \
            .select("*") \
            .eq("id", user_id) \
            .limit(1) \
            .execute()
        
        if not user_data_response.data:
            # User exists in Auth but not in the database
            # Create a basic record
            user_data = {
                "id": user_id,
                "email": email,
                "display_name": email.split("@")[0],
                "is_active": True,
                "is_admin": False,
                "permissions": ["read"]
            }
            
            supabase.table("users").insert(user_data).execute()
        else:
            user_data = user_data_response.data[0]
        
        return UserData(
            id=user_id,
            email=email,
            display_name=user_data.get("display_name"),
            is_active=user_data.get("is_active", True),
            is_admin=user_data.get("is_admin", False),
            permissions=user_data.get("permissions", ["read"])
        )
    
    except Exception as e:
        logger.error(f"Error authenticating user: {e}")
        return None


def decode_token(token: str) -> Optional[TokenData]:
    """
    Decode and validate a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        TokenData if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        user_id = payload.get("sub")
        email = payload.get("email")
        permissions = payload.get("permissions", [])
        exp = payload.get("exp")
        
        if user_id is None:
            return None
        
        return TokenData(
            user_id=user_id,
            email=email,
            permissions=permissions,
            exp=exp
        )
    
    except JWTError:
        return None


def generate_api_key(length: int = 32) -> str:
    """
    Generate a secure API key.
    
    Args:
        length: Length of the API key
        
    Returns:
        API key string
    """
    alphabet = string.ascii_letters + string.digits
    api_key = ''.join(secrets.choice(alphabet) for _ in range(length))
    
    # Add a prefix for easy identification
    return f"mcp_{api_key}"


async def create_api_key(
    user_id: str,
    name: str,
    permissions: List[str] = ["read"],
    expires_in_days: Optional[int] = None
) -> Dict[str, Any]:
    """
    Create a new API key.
    
    Args:
        user_id: ID of the user to create the key for
        name: Name of the API key
        permissions: List of permissions for the key
        expires_in_days: Optional expiration time in days
        
    Returns:
        Dict containing the API key information
    """
    try:
        supabase = get_supabase_client()
        
        # Generate a secure API key
        api_key = generate_api_key()
        
        # Calculate expiration date if provided
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)
        
        # Store in the database
        api_key_data = {
            "user_id": user_id,
            "api_key": api_key,
            "name": name,
            "permissions": permissions,
            "expires_at": expires_at.isoformat() if expires_at else None
        }
        
        response = supabase.table("user_api_keys").insert(api_key_data).execute()
        
        if not response.data:
            raise ValueError("Failed to create API key")
        
        # Return the key information
        key_info = response.data[0]
        key_info["api_key"] = api_key  # Include the key in the response
        
        return key_info
    
    except Exception as e:
        logger.error(f"Error creating API key: {e}")
        raise


async def validate_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Validate an API key.
    
    Args:
        api_key: API key to validate
        
    Returns:
        Dict containing the API key information if valid, None otherwise
    """
    try:
        supabase = get_supabase_client()
        
        # Find the API key in the database
        response = supabase.table("user_api_keys") \
            .select("*") \
            .eq("api_key", api_key) \
            .limit(1) \
            .execute()
        
        if not response.data:
            return None
        
        key_info = response.data[0]
        
        # Check if the key has expired
        if key_info.get("expires_at"):
            expires_at = datetime.fromisoformat(key_info["expires_at"])
            if expires_at < datetime.utcnow():
                return None
        
        # Update last_used_at
        supabase.table("user_api_keys") \
            .update({"last_used_at": datetime.utcnow().isoformat()}) \
            .eq("id", key_info["id"]) \
            .execute()
        
        # Get user information
        user_response = supabase.table("users") \
            .select("*") \
            .eq("id", key_info["user_id"]) \
            .limit(1) \
            .execute()
        
        if not user_response.data:
            return None
        
        user_info = user_response.data[0]
        
        # Check if the user is active
        if not user_info.get("is_active", True):
            return None
        
        # Return combined information
        return {
            "api_key_id": key_info["id"],
            "user_id": key_info["user_id"],
            "name": key_info["name"],
            "permissions": key_info["permissions"],
            "expires_at": key_info.get("expires_at"),
            "user": {
                "email": user_info.get("email"),
                "display_name": user_info.get("display_name"),
                "is_admin": user_info.get("is_admin", False)
            }
        }
    
    except Exception as e:
        logger.error(f"Error validating API key: {e}")
        return None


async def revoke_api_key(api_key_id: str, user_id: str) -> bool:
    """
    Revoke an API key.
    
    Args:
        api_key_id: ID of the API key to revoke
        user_id: ID of the user who owns the key (for validation)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        supabase = get_supabase_client()
        
        # Find the API key in the database
        response = supabase.table("user_api_keys") \
            .select("*") \
            .eq("id", api_key_id) \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if not response.data:
            return False
        
        # Delete the API key
        supabase.table("user_api_keys") \
            .delete() \
            .eq("id", api_key_id) \
            .execute()
        
        return True
    
    except Exception as e:
        logger.error(f"Error revoking API key: {e}")
        return False
