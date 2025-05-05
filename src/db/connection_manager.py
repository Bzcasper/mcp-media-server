"""
Database connection manager for MCP Media Server.
Handles connection pooling, retry logic, and fallbacks.
"""
import os
import logging
import asyncio
import time
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timedelta

from src.config.settings import get_settings
from src.config.key_manager import key_manager
from src.utils.error_monitor import error_monitor, ErrorSeverity

logger = logging.getLogger(__name__)
settings = get_settings()

class ConnectionManager:
    """
    Manages database connections with monitoring and fallbacks.
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super(ConnectionManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the connection manager."""
        if self._initialized:
            return
            
        # Connection pools
        self.supabase_clients = {}
        self.pinecone_clients = {}
        
        # Connection health
        self.connection_health = {
            "supabase": {
                "healthy": False,
                "last_check": None,
                "last_success": None,
                "failure_count": 0
            },
            "pinecone": {
                "healthy": False,
                "last_check": None,
                "last_success": None,
                "failure_count": 0
            }
        }
        
        # Register circuit breakers
        self.supabase_circuit = error_monitor.register_circuit_breaker(
            context="supabase_connection",
            failure_threshold=5,
            recovery_timeout=60
        )
        
        self.pinecone_circuit = error_monitor.register_circuit_breaker(
            context="pinecone_connection",
            failure_threshold=5,
            recovery_timeout=60
        )
        
        self._initialized = True
        logger.info("Connection Manager initialized")
    
    async def get_supabase_client(self, use_fallback: bool = True) -> Any:
        """
        Get a Supabase client.
        
        Args:
            use_fallback: Whether to use fallback mechanisms on failure
            
        Returns:
            Supabase client
        """
        # Check circuit breaker
        if not error_monitor.circuit_is_closed("supabase_connection"):
            if not use_fallback:
                raise RuntimeError("Supabase circuit breaker is open")
            
            # Use fallback if available
            return await self._get_supabase_fallback()
        
        try:
            from src.db.supabase_init import get_supabase_client as get_client
            
            # Update health check
            self.connection_health["supabase"]["last_check"] = datetime.now()
            
            # Get client
            client = get_client()
            
            # Test connection
            await self._test_supabase_connection(client)
            
            # Update health metrics
            self.connection_health["supabase"]["healthy"] = True
            self.connection_health["supabase"]["last_success"] = datetime.now()
            self.connection_health["supabase"]["failure_count"] = 0
            
            # Record success for circuit breaker
            error_monitor.record_success("supabase_connection")
            
            return client
        
        except Exception as e:
            # Track error
            error_monitor.track_error(
                error=e,
                context="supabase_connection",
                severity=ErrorSeverity.HIGH
            )
            
            # Update health metrics
            self.connection_health["supabase"]["healthy"] = False
            self.connection_health["supabase"]["failure_count"] += 1
            
            if use_fallback:
                logger.warning("Using Supabase fallback mechanism")
                return await self._get_supabase_fallback()
            
            raise
    
    async def _test_supabase_connection(self, client):
        """Test Supabase connection by executing a simple query."""
        try:
            # Execute a simple query
            response = client.table("videos").select("id").limit(1).execute()
            
            # Force evaluation of the response
            list(response.data)
            
            return True
        except Exception as e:
            logger.error(f"Supabase connection test failed: {e}")
            raise
    
    async def _get_supabase_fallback(self):
        """Get a fallback Supabase client or implementation."""
        # Try alternative URL/key if available
        backup_url = key_manager.get_backup_key("SUPABASE_URL")
        backup_key = key_manager.get_backup_key("SUPABASE_KEY")
        
        if backup_url and backup_key:
            try:
                from supabase import create_client, Client
                
                client = create_client(backup_url, backup_key)
                
                # Test connection
                await self._test_supabase_connection(client)
                
                logger.info("Using backup Supabase credentials successfully")
                return client
            except Exception as e:
                logger.error(f"Backup Supabase connection failed: {e}")
        
        # If we get here, we need a more extreme fallback
        # Implement a local SQLite fallback for critical operations
        logger.warning("Using SQLite local fallback for Supabase")
        
        # This is a simplified mock that provides minimal functionality
        # In a real implementation, this would be more sophisticated
        from src.db.fallbacks.supabase_fallback import LocalSupabaseFallback
        return LocalSupabaseFallback()
    
    async def get_pinecone_client(self, use_fallback: bool = True) -> Any:
        """
        Get a Pinecone client.
        
        Args:
            use_fallback: Whether to use fallback mechanisms on failure
            
        Returns:
            Pinecone client
        """
        # Check circuit breaker
        if not error_monitor.circuit_is_closed("pinecone_connection"):
            if not use_fallback:
                raise RuntimeError("Pinecone circuit breaker is open")
            
            # Use fallback if available
            return await self._get_pinecone_fallback()
        
        try:
            from src.db.pinecone_init import get_pinecone_client as get_client
            
            # Update health check
            self.connection_health["pinecone"]["last_check"] = datetime.now()
            
            # Get client
            client = get_client()
            
            # Test connection
            await self._test_pinecone_connection(client)
            
            # Update health metrics
            self.connection_health["pinecone"]["healthy"] = True
            self.connection_health["pinecone"]["last_success"] = datetime.now()
            self.connection_health["pinecone"]["failure_count"] = 0
            
            # Record success for circuit breaker
            error_monitor.record_success("pinecone_connection")
            
            return client
        
        except Exception as e:
            # Track error
            error_monitor.track_error(
                error=e,
                context="pinecone_connection",
                severity=ErrorSeverity.HIGH
            )
            
            # Update health metrics
            self.connection_health["pinecone"]["healthy"] = False
            self.connection_health["pinecone"]["failure_count"] += 1
            
            if use_fallback:
                logger.warning("Using Pinecone fallback mechanism")
                return await self._get_pinecone_fallback()
            
            raise
    
    async def _test_pinecone_connection(self, client):
        """Test Pinecone connection by listing indexes."""
        try:
            # List indexes to test connection
            indexes = client.client.list_indexes()
            
            # Force evaluation
            list(indexes)
            
            return True
        except Exception as e:
            logger.error(f"Pinecone connection test failed: {e}")
            raise
    
    async def _get_pinecone_fallback(self):
        """Get a fallback Pinecone client or implementation."""
        # Try alternative API key if available
        backup_key = key_manager.get_backup_key("PINECONE_API_KEY")
        
        if backup_key:
            try:
                from pinecone import Pinecone
                
                client = Pinecone(api_key=backup_key)
                
                # Test connection
                client.list_indexes()
                
                logger.info("Using backup Pinecone credentials successfully")
                return client
            except Exception as e:
                logger.error(f"Backup Pinecone connection failed: {e}")
        
        # If we get here, we need a more extreme fallback
        # Implement a local fallback for vector search
        logger.warning("Using local fallback for Pinecone")
        
        # This is a simplified mock that provides minimal functionality
        # In a real implementation, this would be more sophisticated
        from src.db.fallbacks.pinecone_fallback import LocalPineconeFallback
        return LocalPineconeFallback()
    
    async def get_connection_health(self) -> Dict[str, Any]:
        """
        Get the health status of all database connections.
        
        Returns:
            Dict containing health information
        """
        return {
            "supabase": self.connection_health["supabase"],
            "pinecone": self.connection_health["pinecone"],
            "circuit_breakers": {
                "supabase": self.supabase_circuit.state.value,
                "pinecone": self.pinecone_circuit.state.value
            }
        }
    
    async def check_all_connections(self):
        """Check the health of all database connections."""
        try:
            # Check Supabase
            try:
                await self.get_supabase_client(use_fallback=False)
                logger.info("Supabase connection is healthy")
            except Exception as e:
                logger.error(f"Supabase connection check failed: {e}")
            
            # Check Pinecone
            try:
                await self.get_pinecone_client(use_fallback=False)
                logger.info("Pinecone connection is healthy")
            except Exception as e:
                logger.error(f"Pinecone connection check failed: {e}")
            
            return await self.get_connection_health()
        
        except Exception as e:
            logger.error(f"Error checking connections: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
    
    async def monitor_connections(self, check_interval: int = 60):
        """
        Continuously monitor database connections.
        
        Args:
            check_interval: Interval between checks in seconds
        """
        try:
            while True:
                await self.check_all_connections()
                await asyncio.sleep(check_interval)
        
        except asyncio.CancelledError:
            logger.info("Connection monitoring stopped")
        except Exception as e:
            logger.error(f"Error in connection monitoring: {e}")
            # Auto-restart the monitoring after a delay
            await asyncio.sleep(60)
            asyncio.create_task(self.monitor_connections(check_interval))


# Create and export the connection manager instance
connection_manager = ConnectionManager()
