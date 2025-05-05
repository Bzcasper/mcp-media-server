#!/usr/bin/env python3
"""
Health monitoring script for MCP Media Server.
Can be run as a standalone service or from a cron job.
"""
import argparse
import json
import logging
import os
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import docker
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/health_monitor.log", mode="a"),
    ],
)
logger = logging.getLogger("health_monitor")

# Default configuration
DEFAULT_CONFIG = {
    "server": {
        "host": "localhost",
        "port": 9000,
        "endpoints": {
            "health": "/health",
            "connection_health": "/admin/connection_health",
        },
    },
    "monitoring": {
        "check_interval": 60,  # seconds
        "failure_threshold": 3,
        "recovery_action": "restart",  # none, restart, reboot
        "notification_enabled": True,
    },
    "notification": {
        "email": {
            "enabled": False,
            "smtp_server": "smtp.example.com",
            "smtp_port": 587,
            "username": "alerts@example.com",
            "password": "",
            "from_address": "alerts@example.com",
            "to_addresses": ["admin@example.com"],
        },
        "slack": {"enabled": False, "webhook_url": ""},
    },
    "docker": {
        "container_name": "mcp-media-server",
        "compose_path": "./docker-compose.yml",
    },
}


class HealthMonitor:
    """Health monitoring system for MCP Media Server."""

    def __init__(self, config_path=None):
        """Initialize the health monitor."""
        # Load configuration
        self.config = self._load_config(config_path)

        # Monitoring state
        self.failure_count = 0
        self.last_check_time = None
        self.last_success_time = None
        self.last_failure_time = None
        self.last_recovery_time = None
        self.recovery_in_progress = False

        # Docker client for container management
        self.docker_client = None
        try:
            self.docker_client = docker.from_env()
            logger.info("Docker client initialized")
        except Exception as e:
            logger.warning(f"Docker client initialization failed: {e}")
            logger.warning("Container management will not be available")

    def _load_config(self, config_path):
        """Load configuration from file or use defaults."""
        config = DEFAULT_CONFIG.copy()

        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    loaded_config = json.load(f)

                # Merge with default config (simple recursive merge)
                self._merge_config(config, loaded_config)
                logger.info(f"Configuration loaded from {config_path}")
            except Exception as e:
                logger.error(f"Failed to load configuration: {e}")
                logger.info("Using default configuration")
        else:
            logger.info("Using default configuration")

        return config

    def _merge_config(self, base, override):
        """Recursively merge configuration dictionaries."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def check_health(self):
        """Check the health of the MCP Media Server."""
        self.last_check_time = datetime.now()

        try:
            # Construct the health endpoint URL
            health_url = f"http://{self.config['server']['host']}:{self.config['server']['port']}{self.config['server']['endpoints']['health']}"

            # Make the request
            response = requests.get(health_url, timeout=10)

            # Check if the response is valid
            if response.status_code == 200:
                health_data = response.json()

                # Check if the status is healthy
                if health_data.get("status") == "healthy":
                    self._handle_success(health_data)
                    return True, health_data
                else:
                    logger.warning(f"Unhealthy status: {health_data.get('status')}")
                    self._handle_failure(f"Unhealthy status: {health_data}")
                    return False, health_data
            else:
                logger.warning(
                    f"Health check failed with status code: {response.status_code}"
                )
                self._handle_failure(f"HTTP {response.status_code}: {response.text}")
                return False, {"status": "error", "http_status": response.status_code}

        except requests.RequestException as e:
            logger.error(f"Health check request failed: {e}")
            self._handle_failure(f"Request failed: {str(e)}")
            return False, {"status": "error", "message": str(e)}

        except Exception as e:
            logger.error(f"Unexpected error in health check: {e}")
            self._handle_failure(f"Unexpected error: {str(e)}")
            return False, {"status": "error", "message": str(e)}

    def check_connection_health(self):
        """Check the health of database connections."""
        try:
            # Construct the connection health endpoint URL
            health_url = f"http://{self.config['server']['host']}:{self.config['server']['port']}{self.config['server']['endpoints']['connection_health']}"

            # Make the request
            response = requests.get(health_url, timeout=10)

            # Check if the response is valid
            if response.status_code == 200:
                health_data = response.json()
                return True, health_data
            else:
                logger.warning(
                    f"Connection health check failed with status code: {response.status_code}"
                )
                return False, {"status": "error", "http_status": response.status_code}

        except requests.RequestException as e:
            logger.error(f"Connection health check request failed: {e}")
            return False, {"status": "error", "message": str(e)}

        except Exception as e:
            logger.error(f"Unexpected error in connection health check: {e}")
            return False, {"status": "error", "message": str(e)}

    def _handle_success(self, health_data):
        """Handle a successful health check."""
        self.last_success_time = datetime.now()

        # Reset failure count if we had failures
        if self.failure_count > 0:
            logger.info(f"Service recovered after {self.failure_count} failures")
            self.failure_count = 0

            # Send recovery notification
            if self.config["monitoring"]["notification_enabled"]:
                self._send_notification(
                    "MCP Media Server Recovery",
                    f"Service has recovered at {self.last_success_time.isoformat()}",
                )

    def _handle_failure(self, reason):
        """Handle a failed health check."""
        self.last_failure_time = datetime.now()
        self.failure_count += 1

        logger.warning(
            f"Health check failed ({self.failure_count}/{self.config['monitoring']['failure_threshold']}): {reason}"
        )

        # Check if we've hit the failure threshold
        if self.failure_count >= self.config["monitoring"]["failure_threshold"]:
            logger.error(
                f"Failure threshold reached: {self.failure_count} consecutive failures"
            )

            # Take recovery action
            if not self.recovery_in_progress:
                self._take_recovery_action()

                # Send failure notification
                if self.config["monitoring"]["notification_enabled"]:
                    self._send_notification(
                        "MCP Media Server Failure",
                        f"Service has failed {self.failure_count} times. "
                        + f"Last failure at {self.last_failure_time.isoformat()}. "
                        + f"Recovery action: {self.config['monitoring']['recovery_action']}",
                    )

    def _take_recovery_action(self):
        """Take the configured recovery action."""
        self.recovery_in_progress = True
        action = self.config["monitoring"]["recovery_action"]

        try:
            if action == "none":
                logger.info("No recovery action configured")

            elif action == "restart":
                logger.info("Attempting to restart the service")

                if self.docker_client:
                    # Try to restart the Docker container
                    self._restart_container()
                else:
                    # Fallback to docker-compose command
                    self._restart_using_compose()

                self.last_recovery_time = datetime.now()
                logger.info("Service restart initiated")

            elif action == "reboot":
                logger.warning("Initiating system reboot")

                # This requires proper permissions (usually root)
                # and should be used with caution
                self._reboot_system()

                self.last_recovery_time = datetime.now()
                logger.info("System reboot initiated")

            else:
                logger.warning(f"Unknown recovery action: {action}")

        except Exception as e:
            logger.error(f"Failed to take recovery action: {e}")

        finally:
            self.recovery_in_progress = False

    # Implementation of the recovery methods
    def _restart_container(self):
        """Restart the Docker container."""
        try:
            container_name = self.config["docker"]["container_name"]
            container = self.docker_client.containers.get(container_name)

            logger.info(f"Stopping container: {container_name}")
            container.stop(timeout=30)  # Give it 30 seconds to stop gracefully

            logger.info(f"Starting container: {container_name}")
            container.start()

            logger.info(f"Container {container_name} has been restarted")
            return True

        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False

    def _restart_using_compose(self):
        """Restart using docker-compose command."""
        try:
            compose_path = self.config["docker"]["compose_path"]

            # Check if the compose file exists
            if not os.path.exists(compose_path):
                logger.error(f"Docker Compose file not found: {compose_path}")
                return False

            # Run docker-compose restart
            result = subprocess.run(
                ["docker-compose", "-f", compose_path, "restart"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode == 0:
                logger.info("Service restarted successfully via docker-compose")
                return True
            else:
                logger.error(f"docker-compose restart failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Failed to restart using docker-compose: {e}")
            return False

    def _reboot_system(self):
        """Reboot the system (requires proper permissions)."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    [
                        "shutdown",
                        "/r",
                        "/t",
                        "30",
                        "/c",
                        "MCP Media Server health check initiated reboot",
                    ]
                )
            else:
                subprocess.run(
                    [
                        "sudo",
                        "shutdown",
                        "-r",
                        "+1",
                        "MCP Media Server health check initiated reboot",
                    ]
                )

            logger.warning("System reboot scheduled")
            return True

        except Exception as e:
            logger.error(f"Failed to initiate system reboot: {e}")
            return False

    def _send_notification(self, subject, message):
        """Send a notification about the health status."""
        # Email notification
        if self.config["notification"]["email"]["enabled"]:
            self._send_email_notification(subject, message)

        # Slack notification
        if self.config["notification"]["slack"]["enabled"]:
            self._send_slack_notification(subject, message)

    def _send_email_notification(self, subject, message):
        """Send an email notification."""
        try:
            email_config = self.config["notification"]["email"]

            msg = EmailMessage()
            msg.set_content(message)
            msg["Subject"] = subject
            msg["From"] = email_config["from_address"]
            msg["To"] = ", ".join(email_config["to_addresses"])

            # Connect to SMTP server
            with smtplib.SMTP(
                email_config["smtp_server"], email_config["smtp_port"]
            ) as server:
                server.starttls()

                # Login if credentials are provided
                if email_config["username"] and email_config["password"]:
                    server.login(email_config["username"], email_config["password"])

                # Send the email
                server.send_message(msg)

            logger.info(f"Email notification sent: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False

    def _send_slack_notification(self, subject, message):
        """Send a Slack notification."""
        try:
            webhook_url = self.config["notification"]["slack"]["webhook_url"]

            # Prepare the payload
            payload = {"text": f"*{subject}*\n{message}"}

            # Send the request
            response = requests.post(
                webhook_url, json=payload, headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                logger.info("Slack notification sent successfully")
                return True
            else:
                logger.error(
                    f"Failed to send Slack notification: HTTP {response.status_code}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False

    def run(self):
        """Run the health monitoring loop."""
        logger.info("Starting health monitoring")

        try:
            while True:
                # Check the server health
                health_status, health_data = self.check_health()

                # Check connection health if server is healthy
                if health_status:
                    conn_status, conn_data = self.check_connection_health()
                    if not conn_status:
                        logger.warning(f"Connection health check failed: {conn_data}")

                # Wait for the next check
                time.sleep(self.config["monitoring"]["check_interval"])

        except KeyboardInterrupt:
            logger.info("Health monitoring stopped by user")

        except Exception as e:
            logger.error(f"Unexpected error in health monitoring: {e}")
            return False


def main():
    """Main entry point for the health monitor."""
    parser = argparse.ArgumentParser(description="MCP Media Server Health Monitor")
    parser.add_argument("--config", type=str, help="Path to configuration file")
    parser.add_argument(
        "--once", action="store_true", help="Run health check once and exit"
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Override check interval from configuration (seconds)",
    )

    args = parser.parse_args()

    # Create the health monitor
    monitor = HealthMonitor(args.config)

    # Override check interval if provided
    if args.interval is not None:
        monitor.config["monitoring"]["check_interval"] = args.interval

    # Run once or continuously
    if args.once:
        health_status, health_data = monitor.check_health()
        print(json.dumps(health_data, indent=2))
        sys.exit(0 if health_status else 1)
    else:
        monitor.run()


if __name__ == "__main__":
    main()
