# datamax_dpl

<!-- Badges -->
[![CI](https://github.com/bitranox/datamax_dpl/actions/workflows/default_cicd_public.yml/badge.svg)](https://github.com/bitranox/datamax_dpl/actions/workflows/default_cicd_public.yml)
[![CodeQL](https://github.com/bitranox/datamax_dpl/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/datamax_dpl/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/datamax_dpl?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/datamax_dpl.svg)](https://pypi.org/project/datamax_dpl/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/datamax_dpl.svg)](https://pypi.org/project/datamax_dpl/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/datamax_dpl/graph/badge.svg?token=UFBaUDIgRk)](https://codecov.io/gh/bitranox/datamax_dpl)
[![Maintainability](https://qlty.sh/badges/041ba2c1-37d6-40bb-85a0-ec5a8a0aca0c/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/datamax_dpl)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)


`datamax_dpl` is a template CLI application demonstrating configuration management and structured logging. It showcases rich-click for ergonomics and lib_cli_exit_tools for exits, providing a solid foundation for building CLI applications.
- CLI entry point styled with rich-click (rich output + click ergonomics).
- Layered configuration system with lib_layered_config (defaults -> app -> host -> user -> .env -> env).
  The `.env` is the first one found from the current directory upward; when there is none,
  `$XDG_CONFIG_HOME/datamax-dpl/.env` (`~/.config/...` when unset) is read on Linux
  (see [CONFIG.md](CONFIG.md) for macOS and Windows).
- Rich structured logging with lib_log_rich (console, journald, eventlog, Graylog/GELF).
- Exit-code and messaging helpers powered by lib_cli_exit_tools.
- Metadata helpers ready for packaging, testing, and release automation.


### Python 3.10+ Baseline

- The project targets **Python 3.10 and newer**.
- Runtime dependencies are declared with >= floors in pyproject.toml. Dev
  dependencies (pytest, ruff, pyright, bandit, etc.) are declared the same way.
- CI workflows exercise GitHub's rolling runner images (`ubuntu-latest`,
  `macos-latest`, `windows-latest`) and cover CPython 3.10 through 3.15
  alongside the latest available 3.x release provided by Actions.

---

## Install - recommended via uv

[uv](https://docs.astral.sh/uv/) is an ultrafast Python package manager written in Rust (10-20x faster than pip/poetry).

### Install uv (if not already installed) 
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy the actual binaries
cp /root/.local/bin/uv /usr/local/bin/uv
cp /root/.local/bin/uvx /usr/local/bin/uvx

# Ensure world-executable
chmod 755 /usr/local/bin/uv /usr/local/bin/uvx

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### One-shot run (no install needed)

```bash
uvx datamax_dpl@latest --help
```

### Persistent install as CLI tool

```bash
# One-time install from PyPI (isolated environment, added to PATH)
uv tool install datamax-dpl
# or from the git repository
uv tool install --from "git+https://github.com/bitranox/datamax_dpl.git" datamax-dpl
# Pick the interpreter explicitly if needed, e.g. --python 3.14
# Update (requires network)
uv tool upgrade datamax-dpl
# Run
datamax-dpl --help
```

### Install as project dependency

```bash
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
uv pip install datamax_dpl
```

For alternative install paths (pip, pipx, source builds, etc.), see
[INSTALL.md](INSTALL.md). All supported methods register both the
`datamax_dpl` and `datamax-dpl` commands on your PATH.

### Why we recommend uv: supply-chain protection

This repository commits a `uv.lock`. It pins every package the project needs, transitive
dependencies included, to an exact version and to the hashes of that version's files. The
`exclude-newer` setting under `[tool.uv]` in `pyproject.toml` adds a cooldown: uv leaves any
release younger than that out of the lock. CI installs from the lock, so a hijacked release that
gets pulled again within the cooldown never reaches our builds, and a file whose hash does not
match is refused.

That protection stays in this repository. The installable package declares only lower bounds
(`>=`) for its dependencies, so a plain `pip install` takes the newest release of every transitive
dependency at the moment you run it. If one of them was hijacked an hour earlier, you get the
hijacked version. `pipx install` and `uv tool install` without options do the same.

To install the command line tool with a cooldown of your own (uv then skips any release younger
than 7 days, transitive ones included), or to run the exact tree our CI tests:

```bash
uv tool install --exclude-newer "7 days" datamax_dpl
git clone https://github.com/bitranox/datamax_dpl.git && cd datamax_dpl && uv sync --locked && uv run datamax_dpl --help
```

If you depend on datamax_dpl in your own project, give that project the same cooldown (7 days here, as
an example) and commit the `uv.lock` that `uv add datamax_dpl` writes:

```toml
[tool.uv]
exclude-newer = "7 days"
```

---

## Configuration

See [CONFIG.md](CONFIG.md) for detailed documentation on the layered configuration system, including precedence rules, profile support, and customization best practices.

---

## Quick Start

```bash
# Install
uv tool install datamax_dpl

# Verify
datamax-dpl --version

# deploy config files (user level; --target app or host needs sudo)
datamax-dpl config-deploy --target user

# Try it out
datamax-dpl hello
datamax-dpl info
datamax-dpl config
```

---

## Usage

The CLI leverages [rich-click](https://github.com/ewels/rich-click) so help output, validation errors, and prompts render with Rich styling while keeping the familiar click ergonomics.

### Available Commands

```bash
# Display package information
datamax-dpl info

# Greeting and error-handling demos
datamax-dpl hello
datamax-dpl fail
datamax-dpl --traceback fail

# Configuration management
datamax-dpl config                         # Show current configuration
datamax-dpl config --format json           # Show as JSON
datamax-dpl config --section lib_log_rich  # Show specific section
datamax-dpl config --profile production    # Use a named profile

# Deploy configuration templates to target directories
# Without profile:
datamax-dpl config-deploy --target app    # -> /etc/xdg/{slug}/config.toml
datamax-dpl config-deploy --target host   # -> /etc/xdg/{slug}/hosts/{hostname}.toml
datamax-dpl config-deploy --target user   # -> ~/.config/{slug}/config.toml

# With profile:
datamax-dpl config-deploy --target app --profile production   # -> /etc/xdg/{slug}/profile/production/config.toml
datamax-dpl config-deploy --target host --profile production  # -> /etc/xdg/{slug}/profile/production/hosts/{hostname}.toml
datamax-dpl config-deploy --target user --profile production  # -> ~/.config/{slug}/profile/production/config.toml

# With custom permissions (POSIX only):
datamax-dpl config-deploy --target user --file-mode 640       # Files with rw-r----- (640)
datamax-dpl config-deploy --target user --dir-mode 750        # Directories with rwxr-x--- (750)
datamax-dpl config-deploy --target app --no-permissions       # Skip permission setting (use umask)

# Profile names: alphanumeric, hyphens, underscores; max 64 chars; must start with letter/digit
# See CONFIG.md for full validation rules

# Deploy configuration examples
datamax-dpl config-generate-examples --destination ./examples

# Load configuration from an explicit .env file (skips upward directory search)
datamax-dpl --env-file /path/to/.env config
datamax-dpl --env-file ./environments/production.env send-notification ...

# Override configuration at runtime (repeatable --set)
datamax-dpl --set lib_log_rich.console_level=DEBUG config
datamax-dpl --set email.smtp_hosts='["smtp.example.com:587"]' config --format json

# Logging demo
datamax-dpl logdemo
LOG_CONSOLE_LEVEL=DEBUG datamax-dpl logdemo   # logdemo reads LOG_* only, not --set

# Send email
datamax-dpl send-email \
    --to recipient@example.com \
    --subject "Test Email" \
    --body "Hello from bitranox!"

# Send email with HTML body and attachments
datamax-dpl send-email \
    --to recipient@example.com \
    --subject "Monthly Report" \
    --body "See attached." \
    --body-html "<h1>Report</h1><p>Details in the PDF.</p>" \
    --attachment report.pdf

# Send plain-text notification
datamax-dpl send-notification \
    --to ops@example.com \
    --subject "Deploy OK" \
    --message "Application deployed successfully"

# All commands work with any entry point
python -m datamax_dpl info
uvx datamax_dpl info
```

---

### Email Sending

The application includes email sending capabilities via [btx-lib-mail](https://pypi.org/project/btx-lib-mail/), supporting both simple notifications and rich HTML emails with attachments.

#### Email Configuration

Configure email settings via environment variables, `.env` file, or configuration files:

**Environment Variables:**

Environment variables use the format: `<PREFIX>___<SECTION>__<KEY>=value`
- Triple underscore (`___`) separates PREFIX from SECTION
- Double underscore (`__`) separates SECTION from KEY

```bash
# A list is a JSON array; a comma-separated value would be ONE host string
export DATAMAX_DPL___EMAIL__SMTP_HOSTS='["smtp.gmail.com:587","smtp.backup.com:587"]'
export DATAMAX_DPL___EMAIL__FROM_ADDRESS="alerts@myapp.com"
export DATAMAX_DPL___EMAIL__SMTP_USERNAME="your-email@gmail.com"
export DATAMAX_DPL___EMAIL__SMTP_PASSWORD="your-app-password"
export DATAMAX_DPL___EMAIL__USE_STARTTLS="true"
export DATAMAX_DPL___EMAIL__TIMEOUT="60.0"
```

**Configuration File**:
```toml
[email]
smtp_hosts = ["smtp.gmail.com:587", "smtp.backup.com:587"]  # Fallback to backup if primary fails
from_address = "alerts@myapp.com"
smtp_username = "myuser@gmail.com"
smtp_password = "secret_password"  # Consider using environment variables for sensitive data
use_starttls = true
starttls_verify = true   # false accepts any certificate: only for a known internal relay
timeout = 60.0
# local_hostname = "mail.example.com"   # EHLO name; set it where reverse DNS is slow
# delivery_deadline = 300               # upper bound in seconds for one SMTP session; 0 = none
```

A key `[email]` or `[email.attachments]` does not know is refused (exit 78,
`email.<key>: unknown key`), whichever layer it comes from, so a typo cannot leave a setting at
its default.

**`.env` File** (no prefix in .env; a prefixed key is not recognised):
```bash
# Email configuration for local testing
EMAIL__SMTP_HOSTS=["smtp.gmail.com:587"]
EMAIL__FROM_ADDRESS=noreply@example.com
```

#### Gmail Configuration Example

For Gmail, create an [App Password](https://support.google.com/accounts/answer/185833) instead of using your account password.
In a `.env` file (no prefix in .env; a prefixed key is not recognised):

```bash
EMAIL__SMTP_HOSTS=["smtp.gmail.com:587"]
EMAIL__FROM_ADDRESS=your-email@gmail.com
EMAIL__SMTP_USERNAME=your-email@gmail.com
EMAIL__SMTP_PASSWORD=your-16-char-app-password
```

or as environment variables (these need the prefix):

```bash
export DATAMAX_DPL___EMAIL__SMTP_HOSTS='["smtp.gmail.com:587"]'
export DATAMAX_DPL___EMAIL__FROM_ADDRESS=your-email@gmail.com
export DATAMAX_DPL___EMAIL__SMTP_USERNAME=your-email@gmail.com
export DATAMAX_DPL___EMAIL__SMTP_PASSWORD=your-16-char-app-password
```

#### Send Simple Email

```bash
# Send basic email to one recipient
datamax-dpl send-email \
    --to recipient@example.com \
    --subject "Test Email" \
    --body "Hello from bitranox!"

# Send to multiple recipients
datamax-dpl send-email \
    --to user1@example.com \
    --to user2@example.com \
    --subject "Team Update" \
    --body "Please review the latest changes"
```

#### Send HTML Email with Attachments

```bash
datamax-dpl send-email \
    --to recipient@example.com \
    --subject "Monthly Report" \
    --body "Please find the monthly report attached." \
    --body-html "<h1>Monthly Report</h1><p>See attached PDF for details.</p>" \
    --attachment report.pdf \
    --attachment data.csv
```

#### Send Notifications

For simple plain-text notifications, use the convenience command:

```bash
# Single recipient
datamax-dpl send-notification \
    --to ops@example.com \
    --subject "Deployment Success" \
    --message "Application deployed successfully to production at $(date)"

# Multiple recipients
datamax-dpl send-notification \
    --to admin1@example.com \
    --to admin2@example.com \
    --subject "System Alert" \
    --message "Database backup completed successfully"
```

#### Programmatic Email Usage

```python
from pydantic import SecretStr

from datamax_dpl.adapters.email.sender import EmailConfig
from datamax_dpl.composition import send_email, send_notification

# Configure email. EmailConfig is a btx_lib_mail ConfMail, so in Python the fields use the
# library's names (smtphosts, smtp_use_starttls, smtp_timeout, smtp_starttls_verify,
# smtp_local_hostname, smtp_delivery_deadline); configuration files keep their keys (smtp_hosts,
# use_starttls, timeout, starttls_verify, local_hostname, delivery_deadline). smtp_password is a SecretStr: it prints as
# '**********' in logs, reprs and dumps, and config.smtp_password.get_secret_value() returns
# the plain text.
config = EmailConfig(
    smtphosts=["smtp.gmail.com:587"],
    from_address="alerts@myapp.com",
    smtp_username="myuser@gmail.com",
    smtp_password=SecretStr("app-password"),
    smtp_timeout=60.0,
)

# Send simple email
send_email(
    config=config,
    recipients="recipient@example.com",
    subject="Test Email",
    body="Hello from Python!",
)

# Send email with HTML and attachments
from pathlib import Path

send_email(
    config=config,
    recipients=["user1@example.com", "user2@example.com"],
    subject="Report",
    body="See attached report",
    body_html="<h1>Report</h1><p>Details in attachment</p>",
    attachments=[Path("report.pdf")],
)

# Send notification
send_notification(
    config=config,
    recipients="ops@example.com",
    subject="Deployment Complete",
    message="Production deployment finished successfully",
)
```

#### Email Troubleshooting

**Connection Failures:**
- Verify SMTP hostname and port are correct
- Check firewall allows outbound connections on SMTP port
- Test connectivity: `telnet smtp.gmail.com 587`

**Authentication Errors:**
- For Gmail: Use App Password, not account password
- Ensure username/password are correct
- Check for 2FA requirements

**Emails Not Arriving:**
- Check recipient's spam folder
- Verify `from_address` is valid and not blacklisted
- Review SMTP server logs for delivery status

## Further Documentation

- [Install Guide](INSTALL.md)
- [Development Handbook](DEVELOPMENT.md)
- [Contributor Guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Module Reference](docs/systemdesign/module_reference.md)
- [License](LICENSE)
