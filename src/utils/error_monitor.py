"""
Error monitoring system for MCP Media Server.
Tracks errors, implements circuit breakers, and provides recovery strategies.
"""
import os
import json
import time
import logging
import traceback
from enum import Enum
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Callable, Coroutine, TypeVar, Generic
import asyncio
import functools

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Type variables
T = TypeVar('T')
R = TypeVar('R')

class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, no requests allowed
    HALF_OPEN = "half_open"  # Testing if system recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.
    
    Prevents repeated failures by temporarily blocking operations
    after a threshold of failures is reached.
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        reset_timeout: int = 300
    ):
        """
        Initialize the circuit breaker.
        
        Args:
            name: Name of the circuit
            failure_threshold: Number of failures before opening the circuit
            recovery_timeout: Time in seconds before testing if system recovered
            reset_timeout: Time in seconds before resetting failure count
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.reset_timeout = reset_timeout
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        self.last_success_time = time.time()
    
    def record_failure(self):
        """Record a failure."""
        current_time = time.time()
        self.last_failure_time = current_time
        
        # Reset failure count if too much time passed since last failure
        if current_time - self.last_failure_time > self.reset_timeout:
            self.failure_count = 0
        
        self.failure_count += 1
        
        # Open the circuit if threshold is reached
        if self.failure_count >= self.failure_threshold:
            if self.state == CircuitState.CLOSED:
                logger.warning(f"Circuit {self.name} opened due to {self.failure_count} failures")
            
            self.state = CircuitState.OPEN
    
    def record_success(self):
        """Record a success."""
        self.last_success_time = time.time()
        
        # If in half-open state, a success closes the circuit
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info(f"Circuit {self.name} closed after successful recovery")
    
    def allow_request(self) -> bool:
        """
        Check if a request should be allowed.
        
        Returns:
            True if request is allowed, False otherwise
        """
        current_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # Check if recovery timeout elapsed
            if current_time - self.last_failure_time > self.recovery_timeout:
                logger.info(f"Circuit {self.name} half-open, testing recovery")
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            # Only allow one request at a time in half-open state
            return True
        
        return True


class ErrorMonitor:
    """
    Error monitoring and tracking system.
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super(ErrorMonitor, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the error monitor."""
        if self._initialized:
            return
            
        # Error storage paths
        self.errors_dir = Path(settings.get_absolute_path("logs/errors"))
        self.errors_dir.mkdir(exist_ok=True, parents=True)
        self.error_summary_path = self.errors_dir / "error_summary.json"
        
        # Error tracking
        self.error_counts = {}
        self.last_errors = {}
        self.circuit_breakers = {}
        
        # Load error summary
        self._load_error_summary()
        
        # Background tasks
        self.tasks = []
        
        self._initialized = True
        logger.info("Error Monitor initialized")
    
    def _load_error_summary(self):
        """Load error summary from disk."""
        if self.error_summary_path.exists():
            try:
                with open(self.error_summary_path, "r") as f:
                    data = json.load(f)
                
                self.error_counts = data.get("error_counts", {})
                self.last_errors = data.get("last_errors", {})
            except Exception as e:
                logger.error(f"Error loading error summary: {e}")
    
    def _save_error_summary(self):
        """Save error summary to disk."""
        try:
            data = {
                "error_counts": self.error_counts,
                "last_errors": self.last_errors,
                "updated_at": datetime.now().isoformat()
            }
            
            with open(self.error_summary_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving error summary: {e}")
    
    def track_error(
        self,
        error: Exception,
        context: str,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM
    ):
        """
        Track an error occurrence.
        
        Args:
            error: The exception
            context: Context where the error occurred
            severity: Severity of the error
        """
        try:
            error_type = type(error).__name__
            error_message = str(error)
            error_key = f"{context}:{error_type}"
            
            # Increment error count
            if error_key not in self.error_counts:
                self.error_counts[error_key] = 0
            self.error_counts[error_key] += 1
            
            # Store last error details
            self.last_errors[error_key] = {
                "type": error_type,
                "message": error_message,
                "context": context,
                "severity": severity.value,
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now().isoformat(),
                "count": self.error_counts[error_key]
            }
            
            # Check if we should trigger circuit breaker
            if context in self.circuit_breakers:
                self.circuit_breakers[context].record_failure()
            
            # Log the error
            log_method = logger.error
            if severity == ErrorSeverity.CRITICAL:
                log_method = logger.critical
            
            log_method(
                f"Error in {context}: {error_type}: {error_message} "
                f"(occurred {self.error_counts[error_key]} times)"
            )
            
            # Save detailed error to file
            self._save_error_details(error_key, error)
            
            # Update summary
            self._save_error_summary()
        
        except Exception as e:
            logger.error(f"Error in error tracking: {e}")
    
    def _save_error_details(self, error_key: str, error: Exception):
        """Save detailed error information to file."""
        try:
            error_count = self.error_counts[error_key]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            filename = f"{error_key.replace(':', '_')}_{timestamp}.txt"
            file_path = self.errors_dir / filename
            
            with open(file_path, "w") as f:
                f.write(f"Error: {type(error).__name__}\n")
                f.write(f"Message: {str(error)}\n")
                f.write(f"Occurrence: {error_count}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write("\nTraceback:\n")
                f.write(traceback.format_exc())
        
        except Exception as e:
            logger.error(f"Error saving error details: {e}")
    
    def register_circuit_breaker(
        self, 
        context: str, 
        failure_threshold: int = 5,
        recovery_timeout: int = 60
    ) -> CircuitBreaker:
        """
        Register a circuit breaker for a specific context.
        
        Args:
            context: The context to protect
            failure_threshold: Number of failures before opening the circuit
            recovery_timeout: Time in seconds before testing if system recovered
            
        Returns:
            The circuit breaker instance
        """
        if context not in self.circuit_breakers:
            self.circuit_breakers[context] = CircuitBreaker(
                name=context,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout
            )
        
        return self.circuit_breakers[context]
    
    def get_circuit_breaker(self, context: str) -> Optional[CircuitBreaker]:
        """
        Get a circuit breaker for a specific context.
        
        Args:
            context: The context
            
        Returns:
            The circuit breaker or None if not found
        """
        return self.circuit_breakers.get(context)
    
    def circuit_is_closed(self, context: str) -> bool:
        """
        Check if a circuit breaker is closed (allowing operations).
        
        Args:
            context: The context
            
        Returns:
            True if closed or not found, False if open
        """
        if context not in self.circuit_breakers:
            return True
        
        return self.circuit_breakers[context].allow_request()
    
    def record_success(self, context: str):
        """
        Record a successful operation for a circuit breaker.
        
        Args:
            context: The context
        """
        if context in self.circuit_breakers:
            self.circuit_breakers[context].record_success()
    
    def get_error_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all tracked errors.
        
        Returns:
            Dict containing error summary
        """
        return {
            "total_error_types": len(self.error_counts),
            "total_error_count": sum(self.error_counts.values()),
            "error_counts": self.error_counts,
            "last_errors": self.last_errors,
            "circuit_breakers": {
                context: {
                    "state": breaker.state.value,
                    "failure_count": breaker.failure_count,
                    "last_failure_time": datetime.fromtimestamp(breaker.last_failure_time).isoformat() if breaker.last_failure_time else None,
                    "last_success_time": datetime.fromtimestamp(breaker.last_success_time).isoformat() if breaker.last_success_time else None
                }
                for context, breaker in self.circuit_breakers.items()
            }
        }
    
    def retry_with_backoff(
        self,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        initial_wait: float = 1.0,
        exceptions: tuple = (Exception,),
        circuit_breaker_context: Optional[str] = None
    ):
        """
        Decorator for retrying functions with exponential backoff.
        
        Args:
            max_retries: Maximum number of retries
            backoff_factor: Factor to multiply wait time by on each retry
            initial_wait: Initial wait time in seconds
            exceptions: Tuple of exceptions to catch and retry
            circuit_breaker_context: Optional circuit breaker context
        """
        def decorator(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                retry_count = 0
                wait_time = initial_wait
                
                while True:
                    # Check circuit breaker
                    if circuit_breaker_context and not self.circuit_is_closed(circuit_breaker_context):
                        raise RuntimeError(f"Circuit breaker {circuit_breaker_context} is open")
                    
                    try:
                        result = await func(*args, **kwargs)
                        
                        # Record success if using circuit breaker
                        if circuit_breaker_context:
                            self.record_success(circuit_breaker_context)
                            
                        return result
                    
                    except exceptions as e:
                        retry_count += 1
                        
                        # Track the error
                        self.track_error(
                            error=e,
                            context=circuit_breaker_context or func.__name__,
                            severity=ErrorSeverity.MEDIUM if retry_count < max_retries else ErrorSeverity.HIGH
                        )
                        
                        # Check if we've hit the retry limit
                        if retry_count >= max_retries:
                            logger.error(f"Max retries ({max_retries}) reached for {func.__name__}")
                            raise
                        
                        # Wait with exponential backoff
                        logger.warning(
                            f"Retrying {func.__name__} in {wait_time:.2f}s "
                            f"after error: {str(e)} (attempt {retry_count}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        wait_time *= backoff_factor
            
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                retry_count = 0
                wait_time = initial_wait
                
                while True:
                    # Check circuit breaker
                    if circuit_breaker_context and not self.circuit_is_closed(circuit_breaker_context):
                        raise RuntimeError(f"Circuit breaker {circuit_breaker_context} is open")
                    
                    try:
                        result = func(*args, **kwargs)
                        
                        # Record success if using circuit breaker
                        if circuit_breaker_context:
                            self.record_success(circuit_breaker_context)
                            
                        return result
                    
                    except exceptions as e:
                        retry_count += 1
                        
                        # Track the error
                        self.track_error(
                            error=e,
                            context=circuit_breaker_context or func.__name__,
                            severity=ErrorSeverity.MEDIUM if retry_count < max_retries else ErrorSeverity.HIGH
                        )
                        
                        # Check if we've hit the retry limit
                        if retry_count >= max_retries:
                            logger.error(f"Max retries ({max_retries}) reached for {func.__name__}")
                            raise
                        
                        # Wait with exponential backoff
                        logger.warning(
                            f"Retrying {func.__name__} in {wait_time:.2f}s "
                            f"after error: {str(e)} (attempt {retry_count}/{max_retries})"
                        )
                        time.sleep(wait_time)
                        wait_time *= backoff_factor
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator


# Create and export the error monitor instance
error_monitor = ErrorMonitor()
