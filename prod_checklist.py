#!/usr/bin/env python3
"""
Production Readiness Checker for MCP Media Server.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import docker
import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("production_check")


class ProductionChecker:
    """Checks production readiness of MCP Media Server deployment."""

    def __init__(self):
        """Initialize the checker."""
        self.results = {
            "server": {"status": "unknown", "details": {}, "passed": False},
            "database": {"status": "unknown", "details": {}, "passed": False},
            "security": {"status": "unknown", "details": {}, "passed": False},
            "docker": {"status": "unknown", "details": {}, "passed": False},
            "monitoring": {"status": "unknown", "details": {}, "passed": False},
            "backups": {"status": "unknown", "details": {}, "passed": False},
            "network": {"status": "unknown", "details": {}, "passed": False},
            "system": {"status": "unknown", "details": {}, "passed": False},
        }

        # Docker client
        try:
            self.docker_client = docker.from_env()
        except:
            self.docker_client = None
            logger.warning("Docker client initialization failed")

    def check_server(self, host="localhost", port=9000):
        """Check server health and configuration."""
        result = {
            "health_check": False,
            "api_accessible": False,
            "version_info": None,
            "uptime": None,
            "total_resources": None,
        }

        try:
            # Check health endpoint
            health_url = f"http://{host}:{port}/health"
            response = requests.get(health_url, timeout=10)

            if response.status_code == 200:
                result["health_check"] = True
                health_data = response.json()
                result["version_info"] = health_data.get("version", "unknown")

                # Check API access
                api_url = f"http://{host}:{port}/docs"
                api_response = requests.get(api_url, timeout=10)
                result["api_accessible"] = api_response.status_code == 200

                # Get resource information
                result["total_resources"] = health_data.get("server_info", {}).get(
                    "registered_tools", 0
                )

                self.results["server"]["status"] = "passed"
                self.results["server"]["passed"] = True
            else:
                self.results["server"]["status"] = "failed"
                result["error"] = (
                    f"Health check failed with status code: {response.status_code}"
                )
        except Exception as e:
            self.results["server"]["status"] = "error"
            result["error"] = str(e)

        self.results["server"]["details"] = result
        return result

    def check_database(self, host="localhost", port=9000):
        """Check database connectivity and health."""
        result = {
            "supabase": {"connected": False, "circuit_breaker": "unknown"},
            "pinecone": {"connected": False, "circuit_breaker": "unknown"},
            "fallbacks_configured": False,
        }

        try:
            # Check connection health
            conn_url = f"http://{host}:{port}/admin/connection_health"
            response = requests.get(conn_url, timeout=10)

            if response.status_code == 200:
                conn_data = response.json()

                # Check Supabase
                supabase_health = conn_data.get("supabase", {})
                result["supabase"]["connected"] = supabase_health.get("healthy", False)
                result["supabase"]["circuit_breaker"] = conn_data.get(
                    "circuit_breakers", {}
                ).get("supabase", "unknown")

                # Check Pinecone
                pinecone_health = conn_data.get("pinecone", {})
                result["pinecone"]["connected"] = pinecone_health.get("healthy", False)
                result["pinecone"]["circuit_breaker"] = conn_data.get(
                    "circuit_breakers", {}
                ).get("pinecone", "unknown")

                # Check if fallbacks exist
                fallbacks_dir = Path("src/db/fallbacks")
                result["fallbacks_configured"] = fallbacks_dir.exists() and any(
                    fallbacks_dir.iterdir()
                )

                # Set overall status
                if result["supabase"]["connected"] and result["pinecone"]["connected"]:
                    self.results["database"]["status"] = "passed"
                    self.results["database"]["passed"] = True
                elif result["fallbacks_configured"]:
                    self.results["database"]["status"] = "warning"
                    self.results["database"]["passed"] = True
                    result["warning"] = "Using fallback databases"
                else:
                    self.results["database"]["status"] = "failed"
                    result["error"] = (
                        "Database connections failed and no fallbacks configured"
                    )
            else:
                self.results["database"]["status"] = "failed"
                result["error"] = (
                    f"Connection health check failed with status code: {response.status_code}"
                )
        except Exception as e:
            self.results["database"]["status"] = "error"
            result["error"] = str(e)

        self.results["database"]["details"] = result
        return result

    def check_security(self):
        """Check security configuration."""
        result = {
            "ssl_configured": False,
            "api_keys_secured": False,
            "jwt_secure": False,
            "env_file_permissions": False,
            "nginx_configured": False,
        }

        try:
            # Check for SSL configuration
            ssl_dir = Path("nginx/ssl")
            result["ssl_configured"] = ssl_dir.exists() and any(ssl_dir.glob("*.crt"))

            # Check env file permissions
            env_file = Path(".env")
            if env_file.exists():
                # On Unix, check file permissions
                if sys.platform != "win32":
                    permissions = oct(os.stat(env_file).st_mode & 0o777)
                    # Should be 0600 (owner read/write only)
                    result["env_file_permissions"] = permissions == "0o600"
                else:
                    # On Windows, just check it exists
                    result["env_file_permissions"] = True

            # Check for Nginx configuration
            nginx_conf = Path("nginx/nginx.conf")
            result["nginx_configured"] = nginx_conf.exists()

            # Check for secure JWT configuration
            # This is a basic check - in a real scenario, you'd verify the actual secret
            env_content = ""
            if env_file.exists():
                with open(env_file, "r") as f:
                    env_content = f.read()

                # Check if JWT_SECRET is set and not the default
                if "JWT_SECRET=generate_a_secure_random_key" not in env_content:
                    result["jwt_secure"] = True

            # Check API key storage
            keys_dir = Path("keys")
            result["api_keys_secured"] = keys_dir.exists()

            # Set overall status
            security_score = sum(1 for value in result.values() if value)
            if security_score >= 4:
                self.results["security"]["status"] = "passed"
                self.results["security"]["passed"] = True
            elif security_score >= 2:
                self.results["security"]["status"] = "warning"
                self.results["security"]["passed"] = True
                result["warning"] = "Some security measures are not configured"
            else:
                self.results["security"]["status"] = "failed"
                result["error"] = "Most security measures are not configured"
        except Exception as e:
            self.results["security"]["status"] = "error"
            result["error"] = str(e)

        self.results["security"]["details"] = result
        return result

    def check_docker(self):
        """Check Docker configuration and container health."""
        result = {
            "compose_file_exists": False,
            "containers_running": False,
            "resources_configured": False,
            "volumes_configured": False,
            "healthchecks_configured": False,
            "restart_policy": False,
        }

        try:
            # Check Docker Compose file
            compose_file = Path("docker-compose.yml")
            result["compose_file_exists"] = compose_file.exists()

            if result["compose_file_exists"]:
                # Parse the compose file
                with open(compose_file, "r") as f:
                    compose_data = f.read()

                # Check for specific configurations
                result["restart_policy"] = (
                    "restart: always" in compose_data
                    or "restart: unless-stopped" in compose_data
                )
                result["healthchecks_configured"] = "healthcheck:" in compose_data
                result["resources_configured"] = (
                    "resources:" in compose_data and "limits:" in compose_data
                )
                result["volumes_configured"] = "volumes:" in compose_data

            # Check container status
            if self.docker_client:
                containers = self.docker_client.containers.list(
                    filters={"name": "mcp-media-server"}
                )
                result["containers_running"] = len(containers) > 0

                # Additional container details
                if result["containers_running"]:
                    container = containers[0]
                    result["container_status"] = container.status
                    result["container_health"] = (
                        container.attrs.get("State", {})
                        .get("Health", {})
                        .get("Status", "unknown")
                    )

            # Set overall status
            docker_score = sum(1 for value in result.values() if value)
            if docker_score >= 5:
                self.results["docker"]["status"] = "passed"
                self.results["docker"]["passed"] = True
            elif docker_score >= 3:
                self.results["docker"]["status"] = "warning"
                self.results["docker"]["passed"] = True
                result["warning"] = "Some Docker configurations are missing"
            else:
                self.results["docker"]["status"] = "failed"
                result["error"] = "Docker configuration is incomplete"
        except Exception as e:
            self.results["docker"]["status"] = "error"
            result["error"] = str(e)

        self.results["docker"]["details"] = result
        return result

    def check_monitoring(self):
        """Check monitoring configuration."""
        result = {
            "monitor_script_exists": False,
            "grafana_configured": False,
            "prometheus_configured": False,
            "alerts_configured": False,
            "logs_configured": False,
        }

        try:
            # Check monitor script
            monitor_script = Path("monitor_health.py")
            result["monitor_script_exists"] = monitor_script.exists()

            # Check Prometheus configuration
            prometheus_config = Path("prometheus.yml")
            result["prometheus_configured"] = prometheus_config.exists()

            # Check Grafana configuration
            grafana_dir = Path("grafana")
            result["grafana_configured"] = (
                grafana_dir.exists() and (grafana_dir / "dashboards").exists()
            )

            # Check alerts configuration
            if monitor_script.exists():
                with open(monitor_script, "r") as f:
                    script_content = f.read()

                result["alerts_configured"] = (
                    "notification" in script_content
                    and "send_notification" in script_content
                )

            # Check logs configuration
            logs_dir = Path("logs")
            result["logs_configured"] = logs_dir.exists()

            # Set overall status
            monitoring_score = sum(1 for value in result.values() if value)
            if monitoring_score >= 4:
                self.results["monitoring"]["status"] = "passed"
                self.results["monitoring"]["passed"] = True
            elif monitoring_score >= 2:
                self.results["monitoring"]["status"] = "warning"
                self.results["monitoring"]["passed"] = True
                result["warning"] = "Some monitoring configurations are missing"
            else:
                self.results["monitoring"]["status"] = "failed"
                result["error"] = "Monitoring is not properly configured"
        except Exception as e:
            self.results["monitoring"]["status"] = "error"
            result["error"] = str(e)

        self.results["monitoring"]["details"] = result
        return result

    def check_backups(self):
        """Check backup configuration."""
        result = {
            "backup_script_exists": False,
            "backup_dir_exists": False,
            "automatic_backups": False,
            "backup_retention": False,
            "recent_backup_exists": False,
        }

        try:
            # Check for backup script in main.py
            main_script = Path("main.py")
            if main_script.exists():
                with open(main_script, "r") as f:
                    main_content = f.read()

                result["backup_script_exists"] = (
                    "backup_manager" in main_content
                    and "perform_system_backup" in main_content
                )

            # Check backup directory
            backup_dir = Path("backups")
            result["backup_dir_exists"] = backup_dir.exists()

            # Check for recent backups
            if result["backup_dir_exists"]:
                backups = list(backup_dir.glob("*.tar.gz"))
                result["recent_backup_exists"] = len(backups) > 0

                # Find the most recent backup
                if result["recent_backup_exists"]:
                    most_recent = max(backups, key=lambda p: p.stat().st_mtime)
                    most_recent_time = datetime.fromtimestamp(
                        most_recent.stat().st_mtime
                    )
                    result["most_recent_backup"] = most_recent_time.isoformat()

                    # Check if it's within the last 24 hours
                    backup_age = datetime.now() - most_recent_time
                    result["backup_is_recent"] = (
                        backup_age.total_seconds() < 86400
                    )  # 24 hours

            # Check for automatic backups
            utils_dir = Path("src/utils")
            if utils_dir.exists():
                backup_manager = utils_dir / "backup_manager.py"
                if backup_manager.exists():
                    with open(backup_manager, "r") as f:
                        backup_content = f.read()

                    result["automatic_backups"] = (
                        "schedule_automatic_backups" in backup_content
                    )
                    result["backup_retention"] = (
                        "apply_retention_policy" in backup_content
                    )

            # Set overall status
            backup_score = sum(1 for value in result.values() if value)
            if backup_score >= 4:
                self.results["backups"]["status"] = "passed"
                self.results["backups"]["passed"] = True
            elif backup_score >= 2:
                self.results["backups"]["status"] = "warning"
                self.results["backups"]["passed"] = True
                result["warning"] = "Backup configuration is incomplete"
            else:
                self.results["backups"]["status"] = "failed"
                result["error"] = "Backup system is not properly configured"
        except Exception as e:
            self.results["backups"]["status"] = "error"
            result["error"] = str(e)

        self.results["backups"]["details"] = result
        return result

    def check_network(self, host="localhost", port=9000):
        """Check network configuration."""
        result = {
            "server_reachable": False,
            "nginx_configured": False,
            "ssl_enabled": False,
            "cors_configured": False,
            "rate_limiting": False,
        }

        try:
            # Check if server is reachable
            try:
                response = requests.get(f"http://{host}:{port}/health", timeout=5)
                result["server_reachable"] = response.status_code == 200
            except:
                result["server_reachable"] = False

            # Check Nginx configuration
            nginx_dir = Path("nginx")
            nginx_conf = nginx_dir / "nginx.conf"
            result["nginx_configured"] = nginx_conf.exists()

            # Check for SSL configuration
            if result["nginx_configured"]:
                with open(nginx_conf, "r") as f:
                    nginx_content = f.read()

                result["ssl_enabled"] = (
                    "ssl" in nginx_content and "443" in nginx_content
                )
                result["cors_configured"] = (
                    "add_header" in nginx_content
                    and "Access-Control-Allow-Origin" in nginx_content
                )
                result["rate_limiting"] = (
                    "limit_req_zone" in nginx_content
                    or "limit_conn_zone" in nginx_content
                )

            # Set overall status
            network_score = sum(1 for value in result.values() if value)
            if network_score >= 4:
                self.results["network"]["status"] = "passed"
                self.results["network"]["passed"] = True
            elif network_score >= 2:
                self.results["network"]["status"] = "warning"
                self.results["network"]["passed"] = True
                result["warning"] = "Network configuration is incomplete"
            else:
                self.results["network"]["status"] = "failed"
                result["error"] = "Network is not properly configured"
        except Exception as e:
            self.results["network"]["status"] = "error"
            result["error"] = str(e)

        self.results["network"]["details"] = result
        return result

    def check_system(self):
        """Check system resources and configuration."""
        result = {
            "disk_space": {"available": 0, "total": 0, "status": "unknown"},
            "memory": {"available": 0, "total": 0, "status": "unknown"},
            "cpu": {"load": 0, "cores": 0, "status": "unknown"},
            "python_version": {"version": "unknown", "status": "unknown"},
            "docker_version": {"version": "unknown", "status": "unknown"},
        }

        try:
            # Check disk space
            if sys.platform == "win32":
                # On Windows
                import ctypes

                free_bytes = ctypes.c_ulonglong(0)
                total_bytes = ctypes.c_ulonglong(0)
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p("."),
                    None,
                    ctypes.pointer(total_bytes),
                    ctypes.pointer(free_bytes),
                )
                free_gb = free_bytes.value / (1024**3)
                total_gb = total_bytes.value / (1024**3)
            else:
                # On Unix
                import shutil

                disk_usage = shutil.disk_usage(".")
                free_gb = disk_usage.free / (1024**3)
                total_gb = disk_usage.total / (1024**3)

            result["disk_space"]["available"] = round(free_gb, 2)
            result["disk_space"]["total"] = round(total_gb, 2)
            result["disk_space"]["status"] = (
                "ok" if free_gb > 10 else "warning" if free_gb > 5 else "critical"
            )

            # Check memory
            if sys.platform == "win32":
                # On Windows
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                memory_status = MEMORYSTATUSEX()
                memory_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status))

                avail_mem_gb = memory_status.ullAvailPhys / (1024**3)
                total_mem_gb = memory_status.ullTotalPhys / (1024**3)
            else:
                # On Unix
                import psutil

                memory = psutil.virtual_memory()
                avail_mem_gb = memory.available / (1024**3)
                total_mem_gb = memory.total / (1024**3)

            result["memory"]["available"] = round(avail_mem_gb, 2)
            result["memory"]["total"] = round(total_mem_gb, 2)
            result["memory"]["status"] = (
                "ok"
                if avail_mem_gb > 2
                else "warning" if avail_mem_gb > 1 else "critical"
            )

            # Check CPU
            import psutil

            result["cpu"]["cores"] = psutil.cpu_count(logical=True)
            result["cpu"]["load"] = psutil.cpu_percent(interval=1)
            result["cpu"]["status"] = (
                "ok"
                if result["cpu"]["load"] < 70
                else "warning" if result["cpu"]["load"] < 90 else "critical"
            )

            # Check Python version
            result["python_version"][
                "version"
            ] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            result["python_version"]["status"] = (
                "ok"
                if sys.version_info >= (3, 9)
                else "warning" if sys.version_info >= (3, 7) else "critical"
            )

            # Check Docker version
            if self.docker_client:
                docker_version = self.docker_client.version()
                result["docker_version"]["version"] = docker_version.get(
                    "Version", "unknown"
                )
                result["docker_version"]["status"] = "ok"
            else:
                result["docker_version"]["status"] = "warning"
                result["docker_version"]["version"] = "not available"

            # Set overall status
            critical_count = sum(
                1
                for category in result.values()
                if category.get("status") == "critical"
            )
            warning_count = sum(
                1 for category in result.values() if category.get("status") == "warning"
            )

            if critical_count > 0:
                self.results["system"]["status"] = "failed"
                result["error"] = "System has critical resource issues"
            elif warning_count > 0:
                self.results["system"]["status"] = "warning"
                self.results["system"]["passed"] = True
                result["warning"] = "System has resource warnings"
            else:
                self.results["system"]["status"] = "passed"
                self.results["system"]["passed"] = True
        except Exception as e:
            self.results["system"]["status"] = "error"
            result["error"] = str(e)

        self.results["system"]["details"] = result
        return result

    def run_checks(self, host="localhost", port=9000):
        """Run all checks."""
        print("Running production readiness checks...")

        # Run checks
        self.check_server(host, port)
        self.check_database(host, port)
        self.check_security()
        self.check_docker()
        self.check_monitoring()
        self.check_backups()
        self.check_network(host, port)
        self.check_system()

        return self.results

    def print_results(self):
        """Print the check results."""
        print("\n=== Production Readiness Check Results ===\n")

        # Calculate overall status
        passed_count = sum(
            1 for category in self.results.values() if category.get("passed", False)
        )
        total_count = len(self.results)

        overall_status = (
            "✅ PASSED"
            if passed_count == total_count
            else "⚠️ WARNING" if passed_count >= total_count * 0.75 else "❌ FAILED"
        )

        print(
            f"Overall Status: {overall_status} ({passed_count}/{total_count} checks passed)\n"
        )

        # Print category results
        for category, result in self.results.items():
            status_icon = (
                "✅"
                if result.get("status") == "passed"
                else "⚠️" if result.get("status") == "warning" else "❌"
            )
            print(f"{status_icon} {category.upper()}: {result.get('status')}")

            # Print details for failed or warning categories
            if result.get("status") in ["failed", "warning", "error"]:
                for key, value in result.get("details", {}).items():
                    if key in ["error", "warning"]:
                        print(f"   - {key}: {value}")
                    elif isinstance(value, dict) and value.get("status") in [
                        "warning",
                        "critical",
                    ]:
                        print(f"   - {key}: {value}")
                    elif isinstance(value, bool) and not value:
                        print(f"   - {key}: ❌")

        print("\n===========================================\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="MCP Media Server Production Readiness Checker"
    )
    parser.add_argument(
        "--host", type=str, default="localhost", help="Host address of the MCP server"
    )
    parser.add_argument("--port", type=int, default=9000, help="Port of the MCP server")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    # Run the checks
    checker = ProductionChecker()
    results = checker.run_checks(args.host, args.port)

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        checker.print_results()

    # Exit with status code
    # 0 = all passed, 1 = some warnings, 2 = failures
    passed_count = sum(
        1 for category in results.values() if category.get("passed", False)
    )
    total_count = len(results)

    if passed_count == total_count:
        sys.exit(0)
    elif passed_count >= total_count * 0.75:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
