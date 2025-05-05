"""
Webhook handlers for the MCP Media Server.
"""
import os
import logging
import json
import asyncio
from typing import Dict, Any, List, Optional

from src.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class WebhookHandler:
    """
    Base class for webhook handlers.
    """
    
    def __init__(self, endpoint: str):
        """Initialize the webhook handler."""
        self.endpoint = endpoint
    
    async def handle_event(
        self, 
        event_type: str, 
        job_id: str, 
        status: str, 
        payload: Dict[str, Any]
    ) -> bool:
        """
        Handle a webhook event.
        
        Args:
            event_type: Type of event
            job_id: ID of the job
            status: Status of the job
            payload: Additional payload
            
        Returns:
            True if handled successfully, False otherwise
        """
        raise NotImplementedError("Subclasses must implement handle_event")


class FileSystemWebhookHandler(WebhookHandler):
    """
    Webhook handler that writes events to the file system.
    
    This is useful for testing and development.
    """
    
    def __init__(self, endpoint: str):
        """Initialize the webhook handler."""
        super().__init__(endpoint)
        
        # Extract the path from the endpoint
        # Example: file://logs/webhooks
        if endpoint.startswith("file://"):
            self.output_dir = endpoint[7:]
        else:
            self.output_dir = endpoint
    
    async def handle_event(
        self, 
        event_type: str, 
        job_id: str, 
        status: str, 
        payload: Dict[str, Any]
    ) -> bool:
        """
        Handle a webhook event by writing it to a file.
        
        Args:
            event_type: Type of event
            job_id: ID of the job
            status: Status of the job
            payload: Additional payload
            
        Returns:
            True if handled successfully, False otherwise
        """
        try:
            # Create the output directory if it doesn't exist
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Create a filename based on the event type and job ID
            filename = f"{event_type}_{job_id}_{status}.json"
            file_path = os.path.join(self.output_dir, filename)
            
            # Prepare the data to write
            data = {
                "event_type": event_type,
                "job_id": job_id,
                "status": status,
                "payload": payload
            }
            
            # Write the data to the file
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Webhook event written to file: {file_path}")
            return True
        
        except Exception as e:
            logger.error(f"Error handling webhook event: {e}")
            return False


class EmailWebhookHandler(WebhookHandler):
    """
    Webhook handler that sends email notifications.
    
    This can be implemented using a mail service.
    """
    
    def __init__(self, endpoint: str):
        """Initialize the webhook handler."""
        super().__init__(endpoint)
        
        # Extract the email address from the endpoint
        # Example: mailto:user@example.com
        if endpoint.startswith("mailto:"):
            self.email = endpoint[7:]
        else:
            self.email = endpoint
    
    async def handle_event(
        self, 
        event_type: str, 
        job_id: str, 
        status: str, 
        payload: Dict[str, Any]
    ) -> bool:
        """
        Handle a webhook event by sending an email.
        
        Args:
            event_type: Type of event
            job_id: ID of the job
            status: Status of the job
            payload: Additional payload
            
        Returns:
            True if handled successfully, False otherwise
        """
        try:
            # Simple logging implementation
            # In a real implementation, this would send an email
            logger.info(
                f"Would send email to {self.email} - "
                f"Event: {event_type}, Job: {job_id}, Status: {status}"
            )
            
            # For demonstration purposes, just log the message
            # In a real implementation, use an email library to send the message
            
            return True
        
        except Exception as e:
            logger.error(f"Error handling webhook event: {e}")
            return False


class DiscordWebhookHandler(WebhookHandler):
    """
    Webhook handler that sends notifications to Discord.
    """
    
    def __init__(self, endpoint: str):
        """Initialize the webhook handler."""
        super().__init__(endpoint)
    
    async def handle_event(
        self, 
        event_type: str, 
        job_id: str, 
        status: str, 
        payload: Dict[str, Any]
    ) -> bool:
        """
        Handle a webhook event by sending a Discord message.
        
        Args:
            event_type: Type of event
            job_id: ID of the job
            status: Status of the job
            payload: Additional payload
            
        Returns:
            True if handled successfully, False otherwise
        """
        try:
            # Simple logging implementation
            # In a real implementation, this would send a Discord message
            logger.info(
                f"Would send Discord message to {self.endpoint} - "
                f"Event: {event_type}, Job: {job_id}, Status: {status}"
            )
            
            # For demonstration purposes, just log the message
            # In a real implementation, use the Discord API to send the message
            
            return True
        
        except Exception as e:
            logger.error(f"Error handling webhook event: {e}")
            return False


# Factory function to create the appropriate webhook handler
def create_webhook_handler(endpoint: str) -> Optional[WebhookHandler]:
    """
    Create a webhook handler for the given endpoint.
    
    Args:
        endpoint: Webhook endpoint URL
        
    Returns:
        WebhookHandler instance, or None if the endpoint type is not supported
    """
    if endpoint.startswith("file://"):
        return FileSystemWebhookHandler(endpoint)
    elif endpoint.startswith("mailto:"):
        return EmailWebhookHandler(endpoint)
    elif endpoint.startswith("https://discord.com/api/webhooks/"):
        return DiscordWebhookHandler(endpoint)
    elif endpoint.startswith("http://") or endpoint.startswith("https://"):
        # For generic HTTP endpoints, we'll handle them in the webhook_service.py
        return None
    else:
        logger.warning(f"Unsupported webhook endpoint: {endpoint}")
        return None


# Use this function to dispatch webhook events to appropriate handlers
async def dispatch_webhook_event(
    event_type: str,
    job_id: str,
    status: str,
    payload: Dict[str, Any]
) -> Dict[str, bool]:
    """
    Dispatch a webhook event to all configured handlers.
    
    Args:
        event_type: Type of event
        job_id: ID of the job
        status: Status of the job
        payload: Additional payload
        
    Returns:
        Dict mapping endpoint to success status
    """
    if not settings.WEBHOOK_ENABLED:
        logger.info("Webhooks are disabled")
        return {}
    
    webhook_endpoints = settings.WEBHOOK_ENDPOINTS
    results = {}
    
    for endpoint in webhook_endpoints:
        handler = create_webhook_handler(endpoint)
        
        if handler:
            success = await handler.handle_event(
                event_type=event_type,
                job_id=job_id,
                status=status,
                payload=payload
            )
            
            results[endpoint] = success
    
    return results
