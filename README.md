# SystemGuardian

SystemGuardian is a production-ready system maintenance assistant for Linux servers. It automates
routine update, cleanup, and health-check tasks while remaining auditable, configurable, and safe for
critical environments. The tool is implemented in **Python 3** to take advantage of structured
logging, rich configuration handling, and cross-distribution abstractions that are difficult to
maintain in shell scripts.

## Key Capabilities

- **Modular execution** – Choose between update, cleanup, health-check, or full-run workflows from
  the command line. Each task is implemented as an independent function for easy extension.
- **Centralised configuration** – Tune behaviour through `systemguardian.conf`, including feature
  toggles, package-manager preferences, logging destinations, and service/process lists.
- **Cross-distro intelligence** – Automatically detects the host distribution and selects the
  appropriate package manager (`apt`, `dnf`, `yum`, `pacman`, or `zypper`).
- **Robust logging** – Structured log output with timestamps, severity levels, and built-in log
  rotation keeps historical records manageable.
- **Strict error handling** – Critical failures stop execution immediately with clear log messages
  while non-critical warnings are surfaced in the final report.
- **Dry-run mode** – Preview every command before it runs, making audits and change windows safer.
- **Comprehensive health checks** – Optional checks include S.M.A.R.T. disk health via
  `smartmontools`, systemd service status, and firewall activity (`ufw` or `firewalld`).
- **Advanced cleanup** – Handles package cache purges, systemd journal rotation, Docker resource
  pruning, and optional kernel cleanup routines.
- **Timeshift awareness** – When enabled, warns if a recent Timeshift snapshot is unavailable before
  destructive operations.

## Requirements

- Linux distribution with Python 3.8 or newer.
- Root privileges (`sudo` or direct root execution) – SystemGuardian will exit if not run as root.
- Package manager supported by the host distribution (`apt`, `dnf`, `yum`, `pacman`, or `zypper`).
- Optional tools depending on enabled features:
  - `smartctl` from **smartmontools** for disk health checks.
  - `systemctl` for service status validation.
  - `ufw` or `firewall-cmd` for firewall status detection.
  - `docker` for container cleanup tasks.
  - `timeshift` if Timeshift snapshot enforcement is enabled.

## Installation

1. Clone the repository and change into the project directory:
   ```bash
   git clone https://github.com/your-org/SystemGuardian.git
   cd SystemGuardian
   ```
2. Ensure the script is executable:
   ```bash
   chmod +x systemguardian.py
   ```
3. Copy the sample configuration and adjust to match your environment:
   ```bash
   sudo mkdir -p /etc/systemguardian
   sudo cp systemguardian.conf /etc/systemguardian/systemguardian.conf
   sudo ${EDITOR:-vi} /etc/systemguardian/systemguardian.conf
   ```

## Configuration

SystemGuardian reads configuration values from the first available file in the following order:

1. Path provided via `--config /path/to/file`
2. `./systemguardian.conf` (current directory)
3. `/etc/systemguardian/systemguardian.conf`
4. `/etc/systemguardian.conf`

Every option is documented in the sample `systemguardian.conf`. Key settings include:

- **Logging** – `log_file`, `log_max_bytes`, `log_backup_count`, `log_level`
- **Feature toggles** – enable or disable updates, cleanup routines, health checks, Docker prune,
  kernel cleanup, and Timeshift snapshot enforcement.
- **Cleanup controls** – journal retention, Docker prune flags, package cache behaviour.
- **Health checks** – firewall tool preference, critical service list, process termination list.
- **Package manager overrides** – allow custom upgrade commands per manager.

## Usage

SystemGuardian must be executed with root privileges. The tool provides dedicated modes for common
maintenance scenarios:

```bash
sudo ./systemguardian.py --update        # Run only package updates
sudo ./systemguardian.py --clean         # Run cleanup tasks (cache, logs, optional Docker)
sudo ./systemguardian.py --health-check  # Run health checks only
sudo ./systemguardian.py --full-run      # Run all enabled tasks (default if no flag provided)
sudo ./systemguardian.py --dry-run --full-run  # Preview actions without executing commands
sudo ./systemguardian.py --config /path/to/custom.conf --clean
```

Use `--help` to view the complete CLI reference. When `--dry-run` is supplied, commands are logged
and printed but not executed, making it easy to test configuration changes.

## Logging and Reporting

- **Log file** – All actions, warnings, and errors are written to the configured log file using a
  rotating handler. If the configured destination is not writable, SystemGuardian falls back to
  `./systemguardian.log` and notifies the operator.
- **Summary report** – Upon completion (or upon encountering a critical error) a concise summary is
  printed to STDOUT and logged. The report includes the hostname, timestamp, actions performed,
  metrics (such as reclaimed disk space), and any warnings or errors observed.

## Development

Contributions are welcome. Please lint new Python code with `black` and `flake8`, and include
updates to the sample configuration or documentation when behaviour changes. Submit pull requests
with detailed descriptions of your modifications.

## License

SystemGuardian is distributed under the [MIT License](LICENSE). See the license file for details.
