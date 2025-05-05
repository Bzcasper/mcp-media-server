"""
Webhook service for sending notifications about job completion and events.
"""
import json
import logging
import aiohttp
import asyncio
import time
from typing import Dict, Any, List, Optional, Union

from src.config.settings import get_settings
from src.db.supabase_init import get_supabase_client

logger = logging.getLogger(__name__)
settings = get_settings()

class RateLimiter:
    """Rate limiter for webhook requests."""
    
    def __init__(self, max_requests: int = 60, period: int = 60):
        """
        Initialize the rate limiter.
        
        Args:
            max_requests: Maximum number of requests in the period
            period: Time period in seconds
        """
        self.max_requests = max_requests
        self.period = period
        self.timestamps = []
    
    async def wait_if_needed(self):
        """
        Wait if rate limit would be exceeded.
        
        Returns:
            True if the request can proceed, False if rate limited
        """
        current_time = time.time()
        
        # Remove timestamps older than the period
        self.timestamps = [t for t in self.timestamps if current_time - t < self.period]
        
        # Check if rate limit would be exceeded
        if len(self.timestamps) >= self.max_requests:
            # Calculate the wait time until the oldest timestamp expires
            wait_time = self.period - (current_time - self.timestamps[0])
            
            if wait_time > 0:
                logger.warning(f"Rate limit reached, waiting {wait_time:.2f} seconds")
                await asyncio.sleep(wait_time)
                
                # After waiting, remove expired timestamps again
                current_time = time.time()
                self.timestamps = [t for t in self.timestamps if current_time - t < self.period]
        
        # Add the current timestamp
        self.timestamps.append(current_time)
        return True


# Create a global rate limiter
rate_limiter = RateLimiter(
    max_requests=settings.RATE_LIMIT_REQUESTS, 
    period=settings.RATE_LIMIT_PERIOD
)


async def trigger_webhook(
    event_type: str,
    job_id: str,
    status: str,
    video_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Trigger webhooks for an event.
    
    Args:
        event_type: Type of event (e.g., "video_downloaded", "video_processed")
        job_id: ID of the job
        status: Status of the job (e.g., "complete", "error")
        video_id: Optional ID of the video
        payload: Optional additional payload to include
        
    Returns:
        True if all webhooks were triggered successfully, False otherwise
    """
    # Check if webhooks are enabled
    if not settings.WEBHOOK_ENABLED:
        logger.info(f"Webhooks are disabled, not sending notification for {event_type}")
        return False
    
    webhook_endpoints = settings.WEBHOOK_ENDPOINTS
    if not webhook_endpoints:
        logger.warning("No webhook endpoints configured")
        return False
    
    # Prepare the payload
    webhook_payload = {
        "event_type": event_type,
        "job_id": job_id,
        "status": status,
        "timestamp": time.time()
    }
    
    if video_id:
        webhook_payload["video_id"] = video_id
        
    if payload:
        webhook_payload["data"] = payload
    
    all_successful = True
    
    # Send the webhook to all endpoints
    for endpoint in webhook_endpoints:
        # Check rate limit
        await rate_limiter.wait_if_needed()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json=webhook_payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "MCP-Media-Server/1.0"
                    },
                    timeout=10  # 10 seconds timeout
                ) as response:
                    if response.status >= 200 and response.status < 300:
                        logger.info(f"Webhook sent successfully to {endpoint}: {response.status}")
                        
                        # Record the webhook event in Supabase if available
                        try:
                            supabase = get_supabase_client()
                            webhook_data = {
                                "job_id": job_id,
                                "event_type": event_type,
                                "status": status,
                                "payload": webhook_payload,
                                "endpoint": endpoint
                            }
                            
                            # Ensure data is JSON serializable
                            webhook_data = json.loads(json.dumps(webhook_data, default=str))
                            
                            # Insert into database
                            supabase.table("webhook_events").insert(webhook_data).execute()
                        except Exception as e:
                            logger.error(f"Failed to record webhook event: {e}")
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to send webhook to {endpoint}: "
                            f"Status {response.status}, Response: {error_text}"
                        )
                        all_successful = False
        
        except Exception as e:
            logger.error(f"Error sending webhook to {endpoint}: {e}")
            all_successful = False
    
    return all_successful


async def retry_failed_webhooks(max_retries: int = 3, retry_delay: int = 300) -> int:
    """
    Retry failed webhook notifications.
    
    Args:
        max_retries: Maximum number of retries per webhook
        retry_delay: Delay between retries in seconds
        
    Returns:
        Number of webhooks successfully retried
    """
    try:
        supabase = get_supabase_client()
        
        # Get failed webhook events
        response = supabase.table("webhook_events") \
            .select("*") \
            .eq("status", "error") \
            .order("sent_at", {"ascending": False}) \
            .limit(50) \
            .execute()
        
        failed_webhooks = response.data
        
        if not failed_webhooks:
            logger.info("No failed webhooks to retry")
            return 0
        
        logger.info(f"Found {len(failed_webhooks)} failed webhooks to retry")
        
        successfully_retried = 0
        
        for webhook in failed_webhooks:
            webhook_id = webhook.get("id")
            job_id = webhook.get("job_id")
            event_type = webhook.get("event_type")
            endpoint = webhook.get("endpoint")
            payload = webhook.get("payload", {})
            
            # Check if this webhook has been retried too many times
            retries = webhook.get("retries", 0)
            if retries >= max_retries:
                logger.warning(
                    f"Webhook {webhook_id} has reached maximum retries "
                    f"({retries}/{max_retries}), skipping"
                )
                continue
            
            # Wait for rate limiting
            await rate_limiter.wait_if_needed()
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        endpoint,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "MCP-Media-Server/1.0",
                            "X-Retry-Count": str(retries + 1)
                        },
                        timeout=10  # 10 seconds timeout
                    ) as response:
                        if response.status >= 200 and response.status < 300:
                            logger.info(
                                f"Webhook {webhook_id} retried successfully: "
                                f"{response.status}"
                            )
                            
                            # Update the webhook status in Supabase
                            supabase.table("webhook_events") \
                                .update({
                                    "status": "complete",
                                    "retries": retries + 1,
                                    "retry_sent_at": time.time()
                                }) \
                                .eq("id", webhook_id) \
                                .execute()
                            
                            successfully_retried += 1
                        else:
                            error_text = await response.text()
                            logger.error(
                                f"Failed to retry webhook {webhook_id}: "
                                f"Status {response.status}, Response: {error_text}"
                            )
                            
                            # Update the retry count in Supabase
                            supabase.table("webhook_events") \
                                .update({
                                    "retries": retries + 1,
                                    "retry_sent_at": time.time(),
                                    "error": f"Status {response.status}, Response: {error_text}"
                                }) \
                                .eq("id", webhook_id) \
                                .execute()
            
            except Exception as e:
                logger.error(f"Error retrying webhook {webhook_id}: {e}")
                
                # Update the retry count in Supabase
                supabase.table("webhook_events") \
                    .update({
                        "retries": retries + 1,
                        "retry_sent_at": time.time(),
                        "error": str(e)
                    }) \
                    .eq("id", webhook_id) \
                    .execute()
            
            # Wait before the next retry
            await asyncio.sleep(retry_delay / max_retries)
        
        return successfully_retried
        
    except Exception as e:
        logger.error(f"Error in retry_failed_webhooks: {e}")
        return 0
