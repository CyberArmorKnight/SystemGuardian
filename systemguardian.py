#!/usr/bin/env python3
"""SystemGuardian - Advanced Linux maintenance utility.

This tool automates routine maintenance tasks (updates, cleanups, and
health checks) while remaining configurable, auditable, and safe for
production use.  See README.md for full documentation.
"""
from __future__ import annotations

import argparse
import configparser
import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


class SystemGuardianError(Exception):
    """Raised when a critical failure occurs during execution."""


@dataclass
class Summary:
    actions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)


class SystemGuardian:
    """Core application class implementing all SystemGuardian features."""

    DEFAULT_CONFIG_PATHS = (
        Path("./systemguardian.conf"),
        Path("/etc/systemguardian/systemguardian.conf"),
        Path("/etc/systemguardian.conf"),
    )

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.config = self._load_config(args.config)
        self.dry_run = bool(args.dry_run)
        self.summary = Summary()

        self._configure_logging()
        self.logger = logging.getLogger("SystemGuardian")
        self.logger.debug("Initialized logger")

        self._ensure_root_privileges()

        self.distro_id, self.package_manager = self._detect_distribution()
        self.logger.info(
            "Detected distribution: id=%s, package manager=%s",
            self.distro_id or "unknown",
            self.package_manager or "unavailable",
        )

    # ------------------------------------------------------------------
    # Configuration and logging
    # ------------------------------------------------------------------
    def _load_config(self, config_path: Optional[str]) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        paths: Iterable[Path]
        if config_path:
            paths = (Path(config_path),)
        else:
            paths = self.DEFAULT_CONFIG_PATHS

        read_files = parser.read([str(p) for p in paths if p.exists()])
        if not read_files:
            print(
                "[WARN] No configuration file found. Using built-in defaults.",
                file=sys.stderr,
            )
        return parser

    def _configure_logging(self) -> None:
        requested_log_path = Path(
            self.config.get(
                "general",
                "log_file",
                fallback="/var/log/systemguardian/systemguardian.log",
            )
        )

        try:
            requested_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path = requested_log_path
        except PermissionError:
            fallback_path = Path("./systemguardian.log")
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            log_path = fallback_path
            print(
                f"[WARN] Unable to write to {requested_log_path}. Using {fallback_path} instead.",
                file=sys.stderr,
            )

        max_bytes = self.config.getint("general", "log_max_bytes", fallback=5 * 1024 * 1024)
        backup_count = self.config.getint("general", "log_backup_count", fallback=5)
        log_level_name = self.config.get("general", "log_level", fallback="INFO").upper()
        log_level = getattr(logging, log_level_name, logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        handler.setFormatter(formatter)

        console = logging.StreamHandler()
        console.setFormatter(formatter)

        root_logger = logging.getLogger("SystemGuardian")
        root_logger.setLevel(log_level)
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        root_logger.addHandler(console)

    # ------------------------------------------------------------------
    # Execution orchestration
    # ------------------------------------------------------------------
    def run(self) -> None:
        command_selected = any(
            (
                self.args.update,
                self.args.clean,
                self.args.full_run,
                self.args.health_check,
            )
        )

        if not command_selected:
            self.logger.info("No command specified. Defaulting to --full-run.")
            self.args.full_run = True

        try:
            if self.args.update:
                self._execute_updates()
            elif self.args.clean:
                self._execute_cleanup()
            elif self.args.health_check:
                self._execute_health_checks()
            elif self.args.full_run:
                self._full_run()
            else:
                raise SystemGuardianError("No actionable command specified.")
        except SystemGuardianError as exc:
            self.summary.errors.append(str(exc))
            self.logger.error("SystemGuardian aborted: %s", exc)
            self._print_summary()
            sys.exit(1)
        except KeyboardInterrupt:
            self.logger.error("Execution interrupted by user.")
            self.summary.errors.append("Execution interrupted by user")
            self._print_summary()
            sys.exit(130)

        self._print_summary()

    def _full_run(self) -> None:
        if self._is_enabled("features", "enable_update", default=True):
            self._execute_updates()
        else:
            self.logger.info("Updates disabled via configuration.")

        if self._is_enabled("features", "enable_cleanup", default=True):
            self._execute_cleanup()
        else:
            self.logger.info("Cleanup disabled via configuration.")

        if self._is_enabled("features", "enable_health_checks", default=True):
            self._execute_health_checks()
        else:
            self.logger.info("Health checks disabled via configuration.")

    # ------------------------------------------------------------------
    # Task execution helpers
    # ------------------------------------------------------------------
    def _execute_updates(self) -> None:
        if not self._is_enabled("features", "enable_update", default=True):
            self.logger.info("Update task disabled via configuration.")
            return

        self.logger.info("Starting package update workflow.")
        self._require_timeshift_snapshot_if_configured()

        if not self.package_manager:
            raise SystemGuardianError("Unable to determine supported package manager for updates.")

        if self.package_manager == "apt":
            upgrade_cmd = self.config.get(
                "package_manager",
                "apt_upgrade_command",
                fallback="full-upgrade",
            )
            self._run_command(["apt-get", "update"], "Refreshing apt package metadata")
            self._run_command(
                ["apt-get", upgrade_cmd, "-y"],
                f"Running apt-get {upgrade_cmd}",
            )
        elif self.package_manager in {"dnf", "yum"}:
            upgrade_cmd = self.config.get(
                "package_manager",
                "dnf_upgrade_command",
                fallback="upgrade",
            )
            self._run_command(
                [self.package_manager, upgrade_cmd, "-y"],
                f"Running {self.package_manager} {upgrade_cmd}",
            )
        elif self.package_manager == "pacman":
            self._run_command(
                ["pacman", "-Syu", "--noconfirm"],
                "Updating pacman packages",
            )
        elif self.package_manager == "zypper":
            self._run_command(["zypper", "refresh"], "Refreshing zypper repositories")
            self._run_command(
                ["zypper", "update", "-y"],
                "Updating zypper packages",
            )
        else:
            raise SystemGuardianError(f"Unsupported package manager: {self.package_manager}")

        self.summary.actions.append("Packages updated")
        self._check_reboot_requirement()

    def _execute_cleanup(self) -> None:
        if not self._is_enabled("features", "enable_cleanup", default=True):
            self.logger.info("Cleanup task disabled via configuration.")
            return

        self.logger.info("Starting cleanup workflow.")
        self._require_timeshift_snapshot_if_configured()

        before_cleanup = self._get_root_disk_usage()

        self._terminate_processes()
        self._cleanup_packages()
        self._cleanup_logs()
        if self._is_enabled("features", "enable_kernel_cleanup", default=False):
            self._cleanup_old_kernels()
        if self._is_enabled("features", "enable_docker_prune", default=False):
            self._docker_prune()

        after_cleanup = self._get_root_disk_usage()
        freed = after_cleanup.free - before_cleanup.free
        if freed > 0:
            freed_gb = freed / (1024 ** 3)
            metric = f"Disk space freed: {freed_gb:.2f} GiB"
            self.summary.metrics.append(metric)
            self.logger.info(metric)
        self.summary.actions.append("Cleanup operations completed")

    def _execute_health_checks(self) -> None:
        if not self._is_enabled("features", "enable_health_checks", default=True):
            self.logger.info("Health check task disabled via configuration.")
            return

        self.logger.info("Starting health checks.")
        if self._is_enabled("features", "enable_service_checks", default=True):
            self._check_services()
        if self._is_enabled("features", "enable_firewall_check", default=True):
            self._check_firewall()
        if self._is_enabled("features", "enable_smart_checks", default=True):
            self._check_smart_status()
        self.summary.actions.append("Health checks completed")

    # ------------------------------------------------------------------
    # Individual capabilities
    # ------------------------------------------------------------------
    def _require_timeshift_snapshot_if_configured(self) -> None:
        if not self._is_enabled("features", "require_timeshift_snapshot", default=False):
            return
        max_age_hours = self.config.getint(
            "general", "timeshift_snapshot_max_age_hours", fallback=24
        )
        if not shutil.which("timeshift"):
            warning = (
                "Timeshift requirement enabled but timeshift executable not found. Proceeding without verification."
            )
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return

        output = self._run_command(
            ["timeshift", "--list"],
            "Checking for recent Timeshift snapshots",
            capture_output=True,
            check=False,
        )
        if output is None:
            warning = "Unable to list Timeshift snapshots."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return

        snapshot_pattern = re.compile(r"^\s*Date:\s*(?P<date>\d{4}-\d{2}-\d{2})\s+Time:\s*(?P<time>\d{2}:\d{2}:\d{2})", re.MULTILINE)
        snapshots = [match.groupdict() for match in snapshot_pattern.finditer(output)]
        if not snapshots:
            warning = "No Timeshift snapshots found; consider taking one before continuing."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return

        latest = snapshots[-1]
        snapshot_dt = datetime.datetime.fromisoformat(f"{latest['date']}T{latest['time']}")
        age_hours = (datetime.datetime.now() - snapshot_dt).total_seconds() / 3600
        if age_hours > max_age_hours:
            warning = (
                f"Latest Timeshift snapshot is {age_hours:.1f} hours old; consider creating a newer snapshot."
            )
            self.logger.warning(warning)
            self.summary.warnings.append(warning)

    def _terminate_processes(self) -> None:
        processes = self.config.get("processes", "kill_list", fallback="")
        targets = [proc.strip() for proc in processes.split(",") if proc.strip()]
        if not targets:
            return
        for process in targets:
            if shutil.which("pkill"):
                self._run_command(["pkill", "-f", process], f"Terminating process pattern '{process}'", check=False)
            else:
                warning = "pkill not available; cannot terminate processes"
                self.logger.warning(warning)
                self.summary.warnings.append(warning)
                break
        else:
            self.summary.actions.append("Requested processes terminated")

    def _cleanup_packages(self) -> None:
        if not self.package_manager:
            warning = "No package manager detected; skipping package cleanup."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return

        clean_cache = self._is_enabled("cleanup", "clean_package_cache", default=True)

        if self.package_manager == "apt":
            self._run_command(["apt-get", "autoremove", "-y"], "Removing unused apt packages")
            if clean_cache:
                self._run_command(["apt-get", "clean"], "Cleaning apt package cache")
        elif self.package_manager in {"dnf", "yum"}:
            self._run_command([self.package_manager, "autoremove", "-y"], f"Removing unused {self.package_manager} packages")
            if clean_cache:
                self._run_command([self.package_manager, "clean", "all"], f"Cleaning {self.package_manager} caches")
        elif self.package_manager == "pacman":
            if clean_cache:
                self._run_command(["pacman", "-Sc", "--noconfirm"], "Cleaning pacman cache")
        elif self.package_manager == "zypper":
            if clean_cache:
                self._run_command(["zypper", "clean", "--all"], "Cleaning zypper caches")
        else:
            warning = f"Cleanup not implemented for package manager {self.package_manager}."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)

    def _cleanup_logs(self) -> None:
        journal_days = self.config.getint("cleanup", "journal_vacuum_days", fallback=14)
        if shutil.which("journalctl") and journal_days > 0:
            self._run_command(
                ["journalctl", f"--vacuum-time={journal_days}d"],
                f"Vacuuming journal logs older than {journal_days} days",
                check=False,
            )

    def _cleanup_old_kernels(self) -> None:
        if self.package_manager == "apt":
            self._run_command(
                ["apt-get", "autoremove", "--purge", "-y"],
                "Purging orphaned apt kernels",
            )
        elif self.package_manager in {"dnf", "yum"}:
            cmd = [
                self.package_manager,
                "remove",
                "--oldinstallonly",
                "--setopt",
                "installonly_limit=2",
                "-y",
            ]
            self._run_command(cmd, "Removing older kernels via DNF/YUM", check=False)
        elif self.package_manager == "zypper":
            cmd = ["zypper", "packages", "--orphaned"]
            output = self._run_command(cmd, "Listing orphaned packages", capture_output=True, check=False)
            if output:
                self.logger.info("Review orphaned packages listed above to remove unused kernels as needed.")
        else:
            warning = f"Kernel cleanup not supported for package manager {self.package_manager}."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)

    def _docker_prune(self) -> None:
        if not shutil.which("docker"):
            warning = "Docker not installed; skipping docker system prune."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return
        additional_flags = self.config.get("cleanup", "docker_prune_flags", fallback="")
        cmd = ["docker", "system", "prune", "-af"]
        if additional_flags:
            cmd.extend(additional_flags.split())
        self._run_command(cmd, "Pruning Docker resources", check=False)

    def _check_reboot_requirement(self) -> None:
        reboot_flag = Path("/var/run/reboot-required")
        if reboot_flag.exists():
            message = "System restart recommended (reboot-required flag present)."
            self.summary.warnings.append(message)
            self.logger.warning(message)

    def _check_services(self) -> None:
        services = self.config.get("services", "critical_services", fallback="")
        service_list = [svc.strip() for svc in services.split(",") if svc.strip()]
        if not service_list:
            self.logger.info("No services configured for monitoring.")
            return
        if not shutil.which("systemctl"):
            warning = "systemctl not available; skipping service status checks."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return
        failed = []
        for service in service_list:
            result = self._run_command(
                ["systemctl", "is-active", service],
                f"Checking service '{service}'",
                capture_output=True,
                check=False,
            )
            if result is None:
                failed.append(service)
            else:
                status = result.strip()
                if status != "active":
                    failed.append(service)
        if failed:
            warning = f"Services not active: {', '.join(failed)}"
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
        else:
            self.logger.info("All monitored services are active.")

    def _check_firewall(self) -> None:
        tool = self.config.get("health", "firewall_tool", fallback="auto").lower()
        status_reported = False
        if tool in {"auto", "ufw"} and shutil.which("ufw"):
            output = self._run_command(["ufw", "status"], "Checking ufw status", capture_output=True, check=False)
            if output:
                status_reported = True
                if "Status: active" in output:
                    self.logger.info("ufw firewall is active.")
                else:
                    warning = "ufw firewall is not active."
                    self.logger.warning(warning)
                    self.summary.warnings.append(warning)
        if tool in {"auto", "firewalld"} and shutil.which("firewall-cmd"):
            output = self._run_command(["firewall-cmd", "--state"], "Checking firewalld status", capture_output=True, check=False)
            if output:
                status_reported = True
                if output.strip() != "running":
                    warning = "firewalld firewall is not running."
                    self.logger.warning(warning)
                    self.summary.warnings.append(warning)
                else:
                    self.logger.info("firewalld firewall is running.")
        if not status_reported:
            warning = "No supported firewall tool detected for status check."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)

    def _check_smart_status(self) -> None:
        if not shutil.which("smartctl"):
            warning = "smartctl not available; skipping S.M.A.R.T. disk checks."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return
        disks_output = self._run_command(
            ["lsblk", "-dn", "-o", "NAME,TYPE"],
            "Enumerating block devices",
            capture_output=True,
            check=False,
        )
        if not disks_output:
            warning = "Unable to enumerate block devices for S.M.A.R.T. checks."
            self.logger.warning(warning)
            self.summary.warnings.append(warning)
            return
        disks = []
        for line in disks_output.splitlines():
            try:
                name, dev_type = line.split()
            except ValueError:
                continue
            if dev_type == "disk":
                disks.append(name)
        if not disks:
            self.logger.info("No disk devices detected for S.M.A.R.T. check.")
            return
        for disk in disks:
            device = f"/dev/{disk}"
            output = self._run_command(
                ["smartctl", "-H", device],
                f"Checking SMART health for {device}",
                capture_output=True,
                check=False,
            )
            if not output:
                warning = f"Unable to retrieve SMART status for {device}."
                self.logger.warning(warning)
                self.summary.warnings.append(warning)
                continue
            if "PASSED" in output.upper():
                self.logger.info("SMART health check passed for %s", device)
            else:
                warning = f"SMART health check reported issues for {device}."
                self.logger.warning(warning)
                self.summary.warnings.append(warning)

    # ------------------------------------------------------------------
    # Utility functions
    # ------------------------------------------------------------------
    def _ensure_root_privileges(self) -> None:
        if os.geteuid() != 0:
            raise SystemGuardianError("SystemGuardian must be executed with root privileges.")

    def _detect_distribution(self) -> tuple[Optional[str], Optional[str]]:
        os_release = Path("/etc/os-release")
        distro_id = None
        if os_release.exists():
            parser = configparser.ConfigParser()
            parser.read_string("[os_release]\n" + os_release.read_text())
            distro_id = parser.get("os_release", "ID", fallback=None)
            like = parser.get("os_release", "ID_LIKE", fallback="")
        else:
            like = ""

        candidate_pkg_managers = [
            ("apt", ["apt-get"]),
            ("dnf", ["dnf"]),
            ("yum", ["yum"]),
            ("pacman", ["pacman"]),
            ("zypper", ["zypper"]),
        ]
        package_manager = None
        for manager, commands in candidate_pkg_managers:
            if any(shutil.which(cmd) for cmd in commands):
                package_manager = manager
                break

        if not package_manager and like:
            like_lower = like.lower()
            if "debian" in like_lower:
                package_manager = "apt"
            elif any(term in like_lower for term in ("rhel", "fedora", "centos")):
                package_manager = "dnf"
            elif "arch" in like_lower:
                package_manager = "pacman"

        return distro_id, package_manager

    def _run_command(
        self,
        command: Sequence[str],
        description: str,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> Optional[str]:
        command_display = " ".join(command)
        if self.dry_run:
            self.logger.info("[DRY-RUN] %s", description)
            self.logger.debug("[DRY-RUN] Command: %s", command_display)
            return "" if capture_output else None

        self.logger.info("%s", description)
        self.logger.debug("Executing command: %s", command_display)
        try:
            result = subprocess.run(
                command,
                check=check,
                capture_output=capture_output,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            message = f"Command failed ({description}): {exc}".rstrip()
            self.logger.error(message)
            raise SystemGuardianError(message) from exc
        except FileNotFoundError as exc:
            message = f"Command not found for task '{description}': {command[0]}"
            self.logger.error(message)
            raise SystemGuardianError(message) from exc

        if not check and result.returncode != 0:
            warning = f"Command returned non-zero exit status {result.returncode}: {command_display}"
            self.logger.warning(warning)
            self.summary.warnings.append(warning)

        if capture_output:
            stdout = result.stdout.strip()
            if stdout:
                self.logger.debug("Command output: %s", stdout)
            return stdout
        return None

    def _get_root_disk_usage(self):
        """Return disk usage statistics for the root filesystem."""

        return shutil.disk_usage("/")

    def _is_enabled(self, section: str, option: str, *, default: bool) -> bool:
        try:
            return self.config.getboolean(section, option)
        except (configparser.NoOptionError, configparser.NoSectionError, ValueError):
            return default

    def _print_summary(self) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hostname = os.uname().nodename
        summary_lines = [
            "\n========= SystemGuardian Summary =========",
            f"Host: {hostname}",
            f"Timestamp: {timestamp}",
        ]
        if self.summary.actions:
            summary_lines.append("\nActions:")
            summary_lines.extend(f" - {action}" for action in self.summary.actions)
        if self.summary.metrics:
            summary_lines.append("\nMetrics:")
            summary_lines.extend(f" - {metric}" for metric in self.summary.metrics)
        if self.summary.warnings:
            summary_lines.append("\nWarnings:")
            summary_lines.extend(f" - {warning}" for warning in self.summary.warnings)
        if self.summary.errors:
            summary_lines.append("\nErrors:")
            summary_lines.extend(f" - {error}" for error in self.summary.errors)
        summary_lines.append("==========================================\n")
        report = "\n".join(summary_lines)
        print(report)
        logging.getLogger("SystemGuardian").info(report)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SystemGuardian - advanced system maintenance tool",
    )
    parser.add_argument(
        "--config",
        help="Path to configuration file (default: first available standard location)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what actions would be taken without executing them",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--update", action="store_true", help="Run only update tasks")
    group.add_argument("--clean", action="store_true", help="Run only cleanup tasks")
    group.add_argument("--health-check", action="store_true", help="Run only health checks")
    group.add_argument("--full-run", action="store_true", help="Run all enabled tasks (default)")
    parser.add_argument(
        "--version",
        action="version",
        version="SystemGuardian 2.0",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    guardian = SystemGuardian(args)
    guardian.run()


if __name__ == "__main__":
    main()
