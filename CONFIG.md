# Configuration System

This project uses [`lib_layered_config`](https://github.com/bitranox/lib_layered_config) to manage configuration through a layered merging system. Configuration values are loaded from multiple sources and merged in a defined order, allowing flexible overrides from system-wide defaults down to individual command-line arguments.

## Key Concepts

- **Layered merging**: Configuration is assembled from multiple files and sources, with later layers overriding earlier ones
- **Cross-platform paths**: Follows XDG conventions on Linux, standard locations on macOS and Windows
- **Profile support**: Named profiles allow environment-specific configurations (e.g., `production`, `staging`, `test`)
- **TOML format**: All configuration files use TOML syntax
- **Runtime overrides**: Values can be overridden via environment variables or CLI flags without modifying files

---

## Configuration Layers

Configuration is loaded and merged in the following order (lowest to highest precedence):

| Priority | Layer        | Description                                     |
|:--------:|--------------|-------------------------------------------------|
| 1        | **defaults** | Bundled with the package (`defaultconfig.toml`) |
| 2        | **app**      | System-wide settings for all machines           |
| 3        | **host**     | Machine-specific overrides                      |
| 4        | **user**     | User's personal settings                        |
| 5        | **.env**     | First `.env` found upward, else per-user `.env` |
| 6        | **env vars** | Environment variables                           |
| 7        | **CLI**      | Command-line `--set` flags (highest priority)   |

**Merge behavior**: Each layer only needs to specify values it wants to override. Unspecified values inherit from lower layers.

---

## File Locations

### Platform-Specific Paths

| Layer    | Linux                                   | macOS                                                               | Windows                                               |
|----------|-----------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------|
| defaults | (bundled with package)                  | (bundled with package)                                              | (bundled with package)                                |
| app      | `/etc/xdg/{slug}/config.toml`           | `/Library/Application Support/{vendor}/{app}/config.toml`           | `C:\ProgramData\{vendor}\{app}\config.toml`           |
| host     | `/etc/xdg/{slug}/hosts/{hostname}.toml` | `/Library/Application Support/{vendor}/{app}/hosts/{hostname}.toml` | `C:\ProgramData\{vendor}\{app}\hosts\{hostname}.toml` |
| user     | `~/.config/{slug}/config.toml`          | `~/Library/Application Support/{vendor}/{app}/config.toml`          | `%APPDATA%\{vendor}\{app}\config.toml`                |
| .env     | `~/.config/{slug}/.env` (fallback)      | `~/Library/Application Support/{vendor}/{app}/.env` (fallback)      | `%APPDATA%\{vendor}\{app}\.env` (fallback)            |

Every layer reads `config.toml` first and then the files in the `config.d/` directory next to it
(for the host layer: `hosts/{hostname}.d/`), in lexical order; a `config.d/` file may be TOML, YAML
or JSON. On Linux the app and host layers are read from `/etc/xdg/{slug}/` and then from
`/etc/{slug}/`, and `$XDG_CONFIG_HOME` replaces `~/.config` when it is set. The `.env` row is a
fallback: it is read only when no `.env` is found in the working directory or one of its parents.

### Path Placeholders

| Placeholder  | Linux           | macOS / Windows |
|--------------|-----------------|-----------------|
| `{slug}`     | `datamax-dpl`   | -               |
| `{vendor}`   | -               | `bitranox`      |
| `{app}`      | -               | `datamax_dpl`   |
| `{hostname}` | System hostname | System hostname |

### Concrete Examples

**Linux:**
- User config: `~/.config/datamax-dpl/config.toml`
- App config: `/etc/xdg/datamax-dpl/config.toml`
- Host config: `/etc/xdg/datamax-dpl/hosts/myserver.toml`

**macOS:**
- User config: `~/Library/Application Support/bitranox/datamax_dpl/config.toml`

**Windows:**
- User config: `%APPDATA%\bitranox\datamax_dpl\config.toml`

---

## CLI Commands

### Global Options

These options apply to all commands and must be specified **before** the command name:

| Option                    | Description                                                               |
|---------------------------|---------------------------------------------------------------------------|
| `--version`               | Show version and exit.                                                    |
| `--profile NAME`          | Load configuration from a named profile (e.g., `production`, `test`).     |
| `--set SECTION.KEY=VALUE` | Override a configuration setting. Can be repeated for multiple overrides. |
| `--env-file PATH`         | Explicit `.env` file path. Skips the default upward directory search.     |
| `--traceback`             | Show full Python traceback on errors (useful for debugging).              |
| `--no-traceback`          | Hide traceback, show only error message (default).                        |
| `--json` / `--json-bare`  | Answer with a JSON envelope (indented / one line), see the README.        |

**Example usage:**

```bash
# Use a specific profile
datamax-dpl --profile production config

# Override settings at runtime (repeatable)
datamax-dpl --set lib_log_rich.console_level=DEBUG config
datamax-dpl --set printer.model=dmx-600 --set printer.timeout=5 config --section printer

# Load configuration from an explicit .env file
datamax-dpl --env-file /etc/datamax-dpl/.env config
datamax-dpl --env-file ./environments/production.env config --section printer

# Show full traceback for debugging
datamax-dpl --traceback config-deploy --target user
```

---

### View Configuration

Display the merged configuration from all sources (defaults -> app -> host -> user -> .env -> env vars).

#### Options Reference

| Option           | Required | Description                                                |
|------------------|:--------:|------------------------------------------------------------|
| `--format`       | No       | Output format: `human` (default) or `json`.                |
| `--section NAME` | No       | Show only a specific section (e.g., `printer`, `preview`). |
| `--profile NAME` | No       | Load configuration for a specific profile.                 |

#### Examples

```bash
# Show merged configuration from all sources
datamax-dpl config

# Output as JSON (useful for scripting)
datamax-dpl config --format json

# Show specific section only
datamax-dpl config --section lib_log_rich

# Load configuration for a specific profile
datamax-dpl config --profile production

# Combine options
datamax-dpl config --profile staging --format json --section printer
```

### Deploy Configuration Files

Deploy bundled default configuration to platform-specific directories.

#### Options Reference

| Option             | Required | Description                                                                                                                   |
|--------------------|:--------:|-------------------------------------------------------------------------------------------------------------------------------|
| `--target`         | Yes      | Target layer: `app`, `host`, or `user`. Can be specified multiple times.                                                      |
| `--force`          | No       | Replace existing files whose content differs; the old file is kept as `<name>.bak`. Without this, existing files are skipped. |
| `--profile NAME`   | No       | Deploy to a profile-specific subdirectory (e.g., `profile/production/`).                                                      |
| `--permissions`    | No       | Set Unix permissions even when the configured `enabled` is false.                                                             |
| `--no-permissions` | No       | Disable permission setting; use system umask instead. Not combinable with a mode option.                                      |
| `--dir-mode MODE`  | No       | Override directory permissions (octal: `750` or `0o750`).                                                                     |
| `--file-mode MODE` | No       | Override file permissions (octal: `640` or `0o640`).                                                                          |

#### Basic Examples

```bash
# Create user configuration file
datamax-dpl config-deploy --target user

# Deploy to system-wide location (requires privileges)
sudo datamax-dpl config-deploy --target app

# Deploy host-specific configuration
sudo datamax-dpl config-deploy --target host

# Deploy to multiple locations at once
datamax-dpl config-deploy --target user --target host

# Overwrite existing configuration
datamax-dpl config-deploy --target user --force

# Deploy to a specific profile directory
datamax-dpl config-deploy --target user --profile production

# Deploy production profile and overwrite if exists
datamax-dpl config-deploy --target user --profile production --force
```

Every target receives `config.toml` plus `config.d/40-layered-config.toml`, `config.d/50-printer.toml`
and `config.d/90-logging.toml` (the host target writes `hosts/{hostname}.toml` and
`hosts/{hostname}.d/`). With `--force`, a file whose content differs from the bundled one is
replaced and the old file is kept as `<name>.bak` (numbered, `<name>.bak.1` and so on, when a
backup already exists); a file whose content is already identical is left as it is, mode
included. When nothing differs, `config-deploy --force` writes nothing and says so.

#### Deploying for Other Users

To deploy user-level configuration for a different user account, use `sudo -u`:

```bash
# Deploy user config for 'serviceaccount' user
sudo -u serviceaccount datamax-dpl config-deploy --target user

# Deploy with a specific profile
sudo -u serviceaccount datamax-dpl config-deploy --target user --profile production

# The config is created in that user's home directory (Linux):
# ~serviceaccount/.config/datamax-dpl/config.toml
```

**Important notes:**

- Using `sudo` alone (without `-u`) deploys to root's home directory, not the target user's
- Always use `sudo -u <username>` when deploying for service accounts or other users
- Files are created with ownership of the target user (correct behavior)
- File permissions are set according to the `user` layer defaults (`0o700`/`0o600` = private)

**Common deployment scenarios:**

```bash
# System admin deploying app-wide config (all users)
sudo datamax-dpl config-deploy --target app

# System admin deploying for a service account
sudo -u myservice datamax-dpl config-deploy --target user

# System admin deploying host-specific config
sudo datamax-dpl config-deploy --target host

# Regular user deploying their own config (no sudo needed)
datamax-dpl config-deploy --target user
```

#### File Permissions (POSIX Only)

On Linux and macOS, `config-deploy` sets Unix file permissions based on the target layer. Windows uses ACLs and ignores these settings.

| Target | Directory Mode      | File Mode           | Description                             |
|--------|:-------------------:|:-------------------:|-----------------------------------------|
| `app`  | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | World-readable for system-wide config   |
| `host` | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | World-readable for host-specific config |
| `user` | `0o700` (rwx------) | `0o600` (rw-------) | Private to user only                    |

**Permission options:**

```bash
# Skip permission setting entirely (use system umask)
datamax-dpl config-deploy --target user --no-permissions

# Override directory mode (octal)
datamax-dpl config-deploy --target user --dir-mode 750

# Override file mode (octal)
datamax-dpl config-deploy --target user --file-mode 640

# Both overrides together
datamax-dpl config-deploy --target user --dir-mode 750 --file-mode 640

# Octal formats: both "750" and "0o750" are accepted
datamax-dpl config-deploy --target user --dir-mode 0o750
```

A mode must be a plain octal literal (`750` or `0o750`; no sign, whitespace, `_` or other base)
within `0`..`0o7777`. Because the deployed files can hold credentials, a mode is also refused when
it sets the setuid, setgid or sticky bit, grants group or world write, puts an execute bit on a
file, or takes the owner's access away (a directory needs owner `rwx`, a file owner `rw`). Group
write is refused like world write because a group can hold every local account (on macOS all of
them share `staff`), and a configuration file never needs `x`. A refused mode is a usage error
(exit 2) that names the offending bits, and nothing is written.

**Configurable defaults:**

Permission defaults can be customized in `[lib_layered_config.default_permissions]`:

```toml
[lib_layered_config.default_permissions]
# Values: octal strings ("0o755", "755"); a bare integer is refused (see below)
app_directory = "0o755"
app_file = "0o644"
host_directory = "0o755"
host_file = "0o644"
user_directory = "0o700"
user_file = "0o600"

# Set to false to disable permission setting by default
enabled = true
```

`config-deploy` decides none of this itself: it passes its options and any `--set` of this
section to lib_layered_config, which applies each target's own layer modes. The library reads the
section from the bundled defaults, the configuration files this deploy does not overwrite and the
environment, with the `--set` values laid over them. It never reads `.env` for it (neither one
found from the working directory nor an explicit `--env-file`), so a `.env` can neither change a
deployed mode nor block a deploy. `--dir-mode`/`--file-mode` override the configured modes for
every target, and a key left out falls back to the layer default in the table above. Without
`--permissions`/`--no-permissions`, `enabled` decides; `enabled = false` behaves like
`--no-permissions`, and `--permissions` sets the modes anyway.

A configured mode follows the same rules as `--dir-mode`/`--file-mode`: a plain octal STRING
(`"0o750"`, `"750"`), never setuid/setgid/sticky, group or world write, an execute bit on a file,
or a directory without owner `rwx` / a file without owner `rw`. A bare integer is refused rather
than reinterpreted: TOML `user_file = 400`, `--set ...user_file=400` and an environment value `400`
all arrive as the DECIMAL integer 400, which is `0o620`, not the owner-read-only mode the digits
suggest. Quote it in TOML (`user_file = "640"`); in an environment variable or `--set`, use the
`0o` prefix (`0o640`), which is never read as a number. `enabled` must be a real boolean
(`true`/`false` in TOML, an environment variable or `--set`); `"no"`, `"off"`, `0` or `1` are
refused rather than read as one. An unknown key in the section is refused rather than ignored.
Any violation stops `config-deploy` before it writes anything, with exit 78, one line per problem
naming the key and where it was set, and a hint, for example:

```text
Error: lib_layered_config.default_permissions.user_file: a bare integer is read as decimal (400 = 0o620); write the mode as an octal string: "0o640" (quoted) in a file, 0o640 in the environment or a runtime override (such as an application's --set) (source: env)
Hint: to deploy anyway, pass both --dir-mode and --file-mode (the built-in modes are 700 and 600 for user, 755 and 644 for app and host); --no-permissions also deploys, but leaves every mode to the umask, which can make a user file that holds secrets readable by other accounts.
```

The same refusal applies when a file the library reads cannot be loaded: `config-deploy` never
falls back to the layer defaults, which may be wider than what was configured. A refused `--set`
of the section names `(source: override)` and has no hint, since no option gets past it: fix or
drop the `--set`. The files a deploy writes are never read for it, so `config-deploy --force`
replaces a destination that carries a bad value, or does not parse at all, without further options.

### Generate Example Configuration Files

Create example TOML files showing all available options with default values and documentation comments. Useful for learning the configuration structure or creating initial configuration files.

#### Options Reference

| Option              | Required | Description                                                         |
|---------------------|:--------:|---------------------------------------------------------------------|
| `--destination DIR` | Yes      | Directory to write example files.                                   |
| `--force`           | No       | Overwrite existing files. Without this, existing files are skipped. |

#### Examples

```bash
# Generate examples in a specific directory
datamax-dpl config-generate-examples --destination ./examples

# Overwrite existing example files
datamax-dpl config-generate-examples --destination ./examples --force

# Generate examples in current directory
datamax-dpl config-generate-examples --destination .
```

#### Generated Files

The command writes lib_layered_config's example layout: one placeholder file per layer location
(`config.toml`, `config.d/10-override.toml`, `hosts/your-hostname.toml`, under `xdg/` and `home/`
or the platform's equivalents) plus a `.env.example`. They show WHERE each layer reads from and
hold a generic `[service]` placeholder, not this application's settings. For a file holding this
application's defaults with every option documented, use `config-deploy`.

### Runtime Overrides

Use `--set` to override configuration values without modifying files. This option:
- Has the **highest precedence** among the configuration layers; for logging, lib_log_rich's own `LOG_*` variables still override it
- Can be **repeated** to set multiple values
- Must appear **before** the command name

#### Syntax

```
--set SECTION.KEY=VALUE
--set SECTION.SUBSECTION.KEY=VALUE
```

#### Examples

```bash
# Override a single value
datamax-dpl --set lib_log_rich.console_level=DEBUG config

# Override multiple values
datamax-dpl --set lib_log_rich.console_level=DEBUG --set lib_log_rich.console_format_preset=short config

# Override nested values
datamax-dpl --set lib_log_rich.payload_limits.context_max_keys=30 config

# Override with JSON arrays/objects (use single quotes around the value)
datamax-dpl --set printer.options='["datamatrix"]' config --section printer

# Combine with profile
datamax-dpl --profile production --set lib_log_rich.console_level=DEBUG config
```

#### Supported Value Types

| Type        | Example                                                       |
|-------------|---------------------------------------------------------------|
| String      | `--set section.key=value`                                     |
| Integer     | `--set section.timeout=30`                                    |
| Float       | `--set section.ratio=0.5`                                     |
| Boolean     | `--set section.enabled=true` or `--set section.enabled=false` |
| JSON Array  | `--set section.hosts='["a.com", "b.com"]'`                    |
| JSON Object | `--set section.metadata='{"key": "value"}'`                   |

---

## Profiles

Profiles provide isolated configuration namespaces for different environments (e.g., `production`, `staging`, `test`).

### Profile Name Requirements

Profile names are validated for security and cross-platform compatibility:

| Rule                   | Description                                                                               |
|------------------------|-------------------------------------------------------------------------------------------|
| **Maximum length**     | 64 characters                                                                             |
| **Allowed characters** | ASCII letters (`a-z`, `A-Z`), digits (`0-9`), hyphens (`-`), underscores (`_`)            |
| **Start character**    | Must start with a letter or digit (not `-` or `_`)                                        |
| **Reserved names**     | Windows reserved names rejected: `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` |
| **Path safety**        | No path separators (`/`, `\`) or traversal sequences (`..`)                               |

**Valid examples:** `production`, `staging-v2`, `test_env`, `dev01`

**Invalid examples:** `../etc` (path traversal), `-invalid` (starts with hyphen), `CON` (Windows reserved)

### Which Layers Are Affected?

| Layer    | Affected by Profile? | Notes                                                                        |
|----------|:--------------------:|------------------------------------------------------------------------------|
| defaults | No                   | Always loaded from package                                                   |
| app      | Yes                  | Uses `profile/<name>/` subdirectory                                          |
| host     | Yes                  | Uses `profile/<name>/` subdirectory                                          |
| user     | Yes                  | Uses `profile/<name>/` subdirectory                                          |
| .env     | Fallback only        | The per-user fallback `.env` uses `profile/<name>/`; one found upward is not |
| env vars | No                   | Environment                                                                  |
| CLI      | No                   | Command line                                                                 |

### Profile Path Examples

**Without profile:**
- `~/.config/datamax-dpl/config.toml`

**With profile `production`:**
- `~/.config/datamax-dpl/profile/production/config.toml`

### Reading Behavior

Profile directories are **separate namespaces**. Configuration deployed with a profile is only visible when reading with that same profile.

| Command                       | Sees `app` layer?                  | Sees `user` layer?                 |
|-------------------------------|------------------------------------|------------------------------------|
| `config` (no profile)         | Only if deployed without profile   | Only if deployed without profile   |
| `config --profile production` | Only if deployed with `production` | Only if deployed with `production` |

**Example**: If you deploy `app` with `--profile production` but `user` without a profile:

| Command                       | app layer | user layer |
|-------------------------------|:---------:|:----------:|
| `config`                      | No        | Yes        |
| `config --profile production` | Yes       | No         |

---

## Printer Section

The `[printer]` section (shipped in `config.d/50-printer.toml`) holds the defaults of every label
and printer command. A command-line flag (`--model`, `--firmware`, `--printer`, `--policy`,
`--timeout`, `--cutter`, `--option`) wins over the configured value; on `status` and `memory`
`--timeout` is the whole wait for the printer, in place of `status_wait`. The URI ships empty, so
nothing is contacted until one is set or passed with `--printer`. A key this section does not
declare is refused naming it, as is a section that is no table (`printer = 5`), and a number refuses
a boolean (`printer.timeout: needs a number, got true`). Numeric text stays a number, because `.env`
delivers `1.50` as text. The same holds for `[printers.<name>]` and `[preview]`.

| Key                              | Type                                             | Default                              | Environment variable                                    | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|----------------------------------|--------------------------------------------------|--------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `model`                          | string                                           | `"generic"`                          | `DATAMAX_DPL___PRINTER__MODEL`                          | Printer model profile; `datamax-dpl models` lists them. `generic` emits only what every model understands.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `firmware`                       | string `"<major>.<minor>"`                       | `""`                                 | `DATAMAX_DPL___PRINTER__FIRMWARE`                       | Printer firmware, for features that need a newer one (`"2.01"` unlocks circles on a DMX 400). Empty: the oldest.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `uri`                            | string                                           | `""`                                 | `DATAMAX_DPL___PRINTER__URI`                            | Where jobs and printer commands go: `tcp://HOST:PORT`, `serial:///dev/ttyS0?baud=9600`, `device:///dev/usb/lp0`, `file:PATH` or `-` (stdout).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `policy`                         | `"degrade"` or `"strict"`                        | `"degrade"`                          | `DATAMAX_DPL___PRINTER__POLICY`                         | `degrade` applies documented fallbacks and reports them; `strict` refuses the label instead.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `timeout`                        | float, > 0, at most 2147483                      | `10.0`                               | `DATAMAX_DPL___PRINTER__TIMEOUT`                        | Seconds for one transport operation: connect, write one piece of a job (`send_chunk_bytes`), wait for an answer. `device:` and `file:` keep to it on POSIX only; on Windows, and on `-` everywhere, a write takes as long as the far end takes. `status` and `memory` wait `status_wait` in all for their first answer; `--timeout` sets that whole wait instead.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `status_wait`                    | float, > 0, at most 2147483                      | `30.0`                               | `DATAMAX_DPL___PRINTER__STATUS_WAIT`                    | Seconds `status` and `memory` keep trying for the printer's first answer. A connection that took no request (refused, timed out while connecting, closed or reset before the request or a byte of answer) is tried again on a new one; an open but silent connection is read until the wait ends and never asked twice. `memory` waits at most `directory_read_limit` (120 s) for each answer on one connection, so `--kind all` up to three times that. After an image job a print server was measured refusing for at least 7 s and staying silent past 10 s. `--timeout` replaces it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `answer_retry_interval`          | float, > 0, at most 2147483                      | `0.25`                               | `DATAMAX_DPL___PRINTER__ANSWER_RETRY_INTERVAL`          | Seconds `status` and `memory` pause between two connections within `status_wait`, and `print` within `stored_names_wait`. A refused connect within one connection's opening is retried by `refused_connect_wait` and `refused_connect_retry_interval` instead.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `stored_names_wait`              | float, > 0, at most 2147483                      | `30.0`                               | `DATAMAX_DPL___PRINTER__STORED_NAMES_WAIT`              | Seconds `print` keeps trying for each read of the printer's stored image names (`<STX>W`): before a job no clear precedes, and in every round of the upload check after a label's downloads. The answer's last byte must arrive within it (the `directory_idle` silence that ends it, 1 s, comes on top). A printer was measured silent for 10-20 s after an image job. On a link that never answers each read waits this long (a connect refused after it is retried as long), and the upload check refuses the label after its rounds: about 186 s for a first label and 180 s for a later one with the defaults (see the upload check below). `--timeout` leaves it.                                                                                                                                                                                                                                                                                                                                                                                        |
| `directory_idle`                 | float, > 0, at most 2147483                      | `1.0`                                | `DATAMAX_DPL___PRINTER__DIRECTORY_IDLE`                 | Seconds of silence that end a `<STX>W` directory answer, which has no terminator: every names read of `print` and every answer of `memory` (whose `--idle` wins). A printer sends its listing in one go, and a 9600 baud line moves about a thousand bytes in 1 s. Raise it for a print server or printer that pauses mid-listing (the listing is then cut short and its names unread); lower it on a fast link, since each read ends this long after the last byte.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `directory_read_limit`           | float, > 0, at most 2147483                      | `120.0`                              | `DATAMAX_DPL___PRINTER__DIRECTORY_READ_LIMIT`           | Longest one `memory` answer may take, from the request to the silence that ends it, per kind; an answer still running then is a timeout. 120 s fits a 64 KiB listing at 9600 baud (68 s); a slower line or a longer listing needs more (2400 baud: about 273 s for 64 KiB). `print`'s names reads are bounded by `stored_names_wait` instead. It must be longer than `directory_idle` (and `memory --idle`), or no answer could end within it; such a value is refused naming both.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `reset_check`                    | boolean                                          | `true`                               | `DATAMAX_DPL___PRINTER__RESET_CHECK`                    | Whether `print` asks the printer for a reset report (`<SOH>A`) before a job on a two-way link. `false` asks nothing and acts on no report, not even one a names read carries: the first label after a power cycle may then print shifted (one Allegro 2: about 12 mm) with nothing said. See [A printer reset before a job](#a-printer-reset-before-a-job).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `reset_check_wait`               | float, > 0, at most 2147483                      | `3.0`                                | `DATAMAX_DPL___PRINTER__RESET_CHECK_WAIT`               | Seconds `print` waits for the one status read before a job on a two-way link, which reports a printer reset; `--timeout` leaves it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `calibrate_after_reset`          | boolean                                          | `true`                               | `DATAMAX_DPL___PRINTER__CALIBRATE_AFTER_RESET`          | On a reported reset, print one calibration label before the job; `false` only reports the reset.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `calibration_label_text`         | string, 1 to 255 characters                      | `"Printer Reset - Calibration Feed"` | `DATAMAX_DPL___PRINTER__CALIBRATION_LABEL_TEXT`         | What the calibration label says, mapped as a text field in font 2.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `upload_check_rounds`            | integer, 0 to 3                                  | `3`                                  | `DATAMAX_DPL___PRINTER__UPLOAD_CHECK_ROUNDS`            | How many rounds the upload check of `print` (two-way link) runs before it refuses a label whose images the printer does not list (`image_upload_refused`): 1 uploads and reads once; 2 then clears the module where a clear may go and uploads again (where none may go, only the images the last read did not list; after a read with no answer it first reads the names again and uploads what that read does not list, and with no answer to that read either the round is left out); 3 then uploads the images halved 2x2. Each round after the first reports `image_upload_retried`. 0 reads nothing back after the upload and retries nothing: the format follows the downloads as on a one-way link. Warning: on one Allegro 2 a format whose image was missing printed the rest of the label with no error (2026-11-01), so a lost download then goes unseen; the names read before downloads no clear precedes still runs, since re-sending a name the printer held printed nothing (2026-10-30). A one-way link and `render`/`validate` never check. |
| `max_input_bytes`                | integer, > 0, at most 1073741824 (bytes)         | `16777216`                           | `DATAMAX_DPL___PRINTER__MAX_INPUT_BYTES`                | Largest label or data file `render`, `validate`, `print`, `preview` and `explain` read (16 MiB); a larger one is refused with exit 2 before it is read whole, so a wrong path (a disk image) fails fast. Raising it raises the peak memory by the file's size, twice that for a JSON array: its bytes are held while its records are read, one record at a time in every format. See [Batches and memory](#batches-and-memory).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `max_batch_records`              | integer, > 0, at most 536870912                  | `100000`                             | `DATAMAX_DPL___PRINTER__MAX_BATCH_RECORDS`              | Most records one `--data` batch of `render`, `validate` or `print` may hold; more is refused with exit 2 before anything is rendered. A bound on time, not on memory: each record is rendered once (again only where the printer's answer changes the label), and on a two-way link each label with an image costs a directory read of about 1 s plus the printer's answer, two for a new image with no clear before it. See [Batches and memory](#batches-and-memory).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `max_batch_job_bytes`            | integer, > 0, at most 1099511627776              | `67108864`                           | `DATAMAX_DPL___PRINTER__MAX_BATCH_JOB_BYTES`            | Most bytes the jobs of one `--data` batch of `render` with no `-o` may come to, the one command that holds the whole job because its answer is the job; past it the command stops with exit 2 (`BatchTooLargeError`) naming this key and `-o FILE`, without rendering the rest. It bounds the job, not the memory: see [Batches and memory](#batches-and-memory). 100000 records of a one-field label come to 6-7 MB. `render -o`, `validate` and `print` hold no job and do not read it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `max_reported_findings`          | integer, > 0, at most 100000000                  | `1000`                               | `DATAMAX_DPL___PRINTER__MAX_REPORTED_FINDINGS`          | How many findings a `--data` batch's answer keeps in full. Past that it keeps the first of each code, so a warning that comes late is not lost to the infos before it, and the first error; what it keeps is bounded by the codes that exist. The answer counts all of them, per code and in total, says so when it cuts, and human mode then prints the counts per code. Any error refuses the job whether or not its text was kept.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `max_reported_downloads`         | integer, > 0, at most 100000000                  | `1000`                               | `DATAMAX_DPL___PRINTER__MAX_REPORTED_DOWNLOADS`         | How many downloaded image names a `--data` batch's answer lists. The answer counts all of them and says so when it cuts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `send_chunk_bytes`               | integer, > 0, at most 65536                      | `1024`                               | `DATAMAX_DPL___PRINTER__SEND_CHUNK_BYTES`               | Largest piece of a job handed to one write over tcp or serial. `timeout` bounds each piece, not the whole job, so it means the printer took no data for that long: at the about 1390 bytes/s of a print server feeding a 9600 baud printer, 1024 bytes take under a second. A larger piece needs a `timeout` long enough to send it. At most 65536: Windows takes one send of any size whole while its socket buffer is not full, so a larger piece comes back sent from a printer that reads nothing and the timeout never fires.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `refused_connect_wait`           | float, > 0, at most 2147483                      | `3.0`                                | `DATAMAX_DPL___PRINTER__REFUSED_CONNECT_WAIT`           | Seconds one opening of a tcp connection keeps retrying a refused connect before it reports the refusal, never longer than `timeout`. A print server that just closed a job's connection refuses the next one for a moment (about 1-2 s measured on a SerComm PS301). Other connect errors (unknown host, unreachable, timeout) are not retried; a host that keeps refusing is reported after this plus one connect (on Windows a refusal itself takes about 2 s).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `refused_connect_retry_interval` | float, > 0, at most 2147483                      | `0.25`                               | `DATAMAX_DPL___PRINTER__REFUSED_CONNECT_RETRY_INTERVAL` | Seconds one opening of a tcp connection pauses before it tries a refused connect again within `refused_connect_wait`; no connect starts once the next would fall past that wait.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `cutter`                         | `"yes"`, `"no"`, `"unknown"`                     | `"unknown"`                          | `DATAMAX_DPL___PRINTER__CUTTER`                         | Whether a cutter is installed and enabled. `no` refuses a label that asks for cutting; `unknown` sends it with a note.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `options`                        | list of strings                                  | `[]`                                 | `DATAMAX_DPL___PRINTER__OPTIONS`                        | Installed hardware options that unlock features; currently only `"datamatrix"`. As a variable: `'["datamatrix"]'`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `scale_across`                   | float, 0.5-1.5                                   | unset                                | `DATAMAX_DPL___PRINTER__SCALE_ACROSS`                   | Printed length divided by nominal length across the web, for this printer. Set by `calibrate set`. Unset: the model's default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `scale_along`                    | float, 0.5-1.5                                   | unset                                | `DATAMAX_DPL___PRINTER__SCALE_ALONG`                    | The same along the feed; it depends on the platen roller (an Allegro 2 measured 0.977 uncorrected; its model default after calibration is 0.976). Fonts and images sized in dots are not scaled. Unset: the model's default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `offset_across`                  | float, -10 to 10 (mm)                            | unset                                | `DATAMAX_DPL___PRINTER__OFFSET_ACROSS`                  | Millimetres every field moves to the right on this printer (negative: left), independent of the tear-off position. Set by `calibrate set --left/--right`. Unset: the model's default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `offset_along`                   | float, -10 to 10 (mm)                            | unset                                | `DATAMAX_DPL___PRINTER__OFFSET_ALONG`                   | Millimetres every field moves toward the label top (negative: down). Set by `calibrate set --top/--bottom`. Unset: the model's default.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `stop_position`                  | float, > 0, at most 253.746 (mm), or `"printer"` | unset                                | `DATAMAX_DPL___PRINTER__STOP_POSITION`                  | Tear-off stop position, sent as `<STX>f` exactly as set (no scale or offset) before every job that does not cut. `"printer"` sends none and keeps the printer's own setting, the model's default included. A label's own `stop_position` wins, and so does a present distance a model without `<STX>Kf` sends as one. Unset: the model's default, else the printer's own setting.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `cutter_stop_position`           | float, > 0, at most 253.746 (mm)                 | unset                                | `DATAMAX_DPL___PRINTER__CUTTER_STOP_POSITION`           | Stop position for a job that cuts, sent exactly as set; such a job never uses `stop_position`. Unset: the model's cutter default (Allegro 2: 76.8 mm, `<STX>f302`, measured with a cutter on one printer), else no stop position.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `joined_stop_position`           | float, > 0, at most 253.746 (mm)                 | unset                                | `DATAMAX_DPL___PRINTER__JOINED_STOP_POSITION`           | Stop position for a record of a `--data` series that the next record follows uncut, sent exactly as set. Set it at or below the start of print: a stop past it makes the printer pull the stock back before the next record (manual p.22). Unset: the model's default (Allegro 2: 25.4 mm, `<STX>f100`, measured with a cutter on one printer), else `cutter_stop_position`. The last record and the calibration label stop at `cutter_stop_position`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `cut_every`                      | integer, 1-9999                                  | unset                                | `DATAMAX_DPL___PRINTER__CUT_EVERY`                      | The cut a label without its own `cut` gets: after every N labels, as `"cut": {"every": N}`. The label's own `cut` wins. Unset: a label without `cut` is cut only after the last label of the print (`cut_after_print`), or not at all where that is off (its job then switches the cutter off). A label cannot opt out of it: to print one job without it where the config sets it, run that job with `--set printer.cut_every=null`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `cut_after_print`                | boolean                                          | `true`                               | `DATAMAX_DPL___PRINTER__CUT_AFTER_PRINT`                | With `cutter` `yes` on a model whose job sets the cutter (`<STX>V`), cut after the last label of every print, one label or a whole `--data` series: such a printer stops the stock at the cutter, where it cannot be torn off. The labels before the last stay joined unless `cut` or `cut_every` says otherwise; a cut's `at_end: false` is overridden (`cut_at_end_kept`). The cutter is then on when the print ends, so the printer's FEED key cuts too. `feed` and the calibration label are never cut. `false`: a print without a cut is not cut.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `image_memory_bytes`             | integer, > 0 (bytes)                             | unset                                | `DATAMAX_DPL___PRINTER__IMAGE_MEMORY_BYTES`             | Bytes of image dot data one job may download. Past it, `degrade` sends every image but a QR Code at half resolution and double magnification (`image_halved`), `strict` refuses (`image_memory_exceeded`). Unset: the model's budget (Allegro 2: 49088), else none. `print` on a two-way link leaves the model's budget out and lets the upload check decide (`image_budget_skipped`), unless `upload_check_rounds` is 0, which reads nothing back; a value set here applies there too.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `image_module`                   | `"A"` to `"E"`                                   | unset                                | `DATAMAX_DPL___PRINTER__IMAGE_MODULE`                   | Memory module images are downloaded to. Unset: the model's built-in module (`datamax-dpl capabilities MODEL` shows it); a model without one (Prodigy, Prodigy Plus) refuses a job that downloads an image (`image_module_unknown`). `C` (the default module) makes `print` on a two-way link refuse such a job before sending anything (`image_module_unlisted`): no directory lists it by that letter.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `clear_graphics_before`          | boolean                                          | `true`                               | `DATAMAX_DPL___PRINTER__CLEAR_GRAPHICS_BEFORE`          | Clear the image module before each label and its downloads, a label without images included: `<STX>q` to the model's built-in RAM, the plain Prodigy's `<STX>R` for each name it downloads; a plug-in module is never cleared. `false` keeps what the printer stores: a label may then place an image without a bitmap, and a download of a name the printer holds is left out (`image_name_held`), so `print` reads the names before each such label, and a batch with no printer to read is refused (`batch_image_names_unread`) unless `clear_graphics_after` clears the module after each label. Keeping a stored image across a `--data` batch needs `clear_graphics_after` `false` too (below).                                                                                                                                                                                                                                                                                                                                                          |
| `clear_graphics_after`           | boolean                                          | `true`                               | `DATAMAX_DPL___PRINTER__CLEAR_GRAPHICS_AFTER`           | Clear the image module again after each label, the same way, so the next job finds it empty. `<STX>q` removes every image stored there too, so a `--data` batch whose label places a stored image (no bitmap) is refused from its second record on (`stored_image_cleared`) unless both clear keys are `false`; the plain Prodigy's clear after removes only the label's own downloads.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `held_image_severity`            | `"info"`, `"warning"` or `"error"`               | `"warning"`                          | `DATAMAX_DPL___PRINTER__HELD_IMAGE_SEVERITY`            | Severity of `image_name_held` and `image_names_unread`, the findings of a label whose image downloads no clear precedes (a module that is not the model's built-in RAM, a model that can clear nothing). `error` refuses such a label whatever `policy` says.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `max_format_rows`                | integer, > 0 (dot rows)                          | unset                                | `DATAMAX_DPL___PRINTER__MAX_FORMAT_ROWS`                | Highest dot row along the feed one format may reach, at dot height 1, as the job commands it (scale and offset included; an image only the printer holds counts by its reference row). A field past it is refused under both policies (`format_past_max_length`); a label `dot_size` of height 2 doubles it. Unset: the model's limit (Allegro 2: 1648, the lowest cut seen), else none.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `max_polygon_points`             | integer, > 0, at most 10000                      | `60`                                 | `DATAMAX_DPL___PRINTER__MAX_POLYGON_POINTS`             | Most points one polygon of a label may have. The manual sets no limit for the polygon record, so this one is ours: a label with a longer polygon is refused when it is read (exit 2), and a library job past `RenderOptions.max_polygon_points` gets `polygon_too_many_points`. Every point is eight bytes of the record.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `inch_unit_dots`                 | integer, > 0 (dots)                              | unset                                | `DATAMAX_DPL___PRINTER__INCH_UNIT_DOTS`                 | Whole printhead dots one 1/100 in prints as, on a printer that rounds the inch unit to whole dots; every distance of a job in 1/100 in is worked out from it. Unset: the model's, 2 on the Allegro 2 (a 787/100 in line printed as 1574 dot rows on one CA02 03.04), the exact unit elsewhere. Set it for another firmware or a printer that rounds differently; `scale_along` and `scale_across` absorb what remains.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `send_settings`                  | list of strings                                  | unset (every one)                    | `DATAMAX_DPL___PRINTER__SEND_SETTINGS`                  | The persistent printer settings each label's job sends before its format, of `"media"`, `"stop"`, `"start_of_print"`, `"max_label_length"`, `"feed_rate"` and `"software_switches"`. Unset sends every one; a setting left out keeps whatever the printer holds, and an empty list sends none. As a variable: `'["media", "stop"]'`. See [The printer state each job sets](#the-printer-state-each-job-sets).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `media`                          | `"gap"`, `"reflective"` or `"continuous"`        | unset                                | `DATAMAX_DPL___PRINTER__MEDIA`                          | Media sensor a job sets when the label names none (`<STX>e` gap, `<STX>r` reflective, `<STX>c` continuous with the label's length); the label's own `media` wins. Unset leaves the sensor as the label says, or as the printer holds it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `start_of_print_mm`              | float, 12.7 to 2539.746 (mm)                     | unset (the model's)                  | `DATAMAX_DPL___PRINTER__START_OF_PRINT_MM`              | Start of print, sent as `<STX>O` exactly as set (no scale or offset). Unset takes the model's default from the manual (p.31): 55.88 mm on most models, 63.5 mm on the Prodigy, 27.94 mm on the Prodigy Plus and Titan 6200, none on the Prodigy Max, `generic` and `permissive`, which then send none (`printer_settings_unknown`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `max_label_length_mm`            | float, 127 to 2539.746 (mm)                      | unset (the model's)                  | `DATAMAX_DPL___PRINTER__MAX_LABEL_LENGTH_MM`            | How far the printer searches for the next label edge, sent as `<STX>M`. Under 127 mm is refused (the manual asks for at least 5 in, p.30). Unset takes the model's default (304.8 or 406.4 mm, none on `generic` and `permissive`). A gap or reflective label not at least 2.54 mm shorter gets no `<STX>M` (`max_label_length_too_short`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `feed_rate`                      | speed letter `"A"` (1 in/s) to `"S"` (10 in/s)   | unset (the model's)                  | `DATAMAX_DPL___PRINTER__FEED_RATE`                      | Speed the feed button feeds at, sent as `<STX>S`. Unset takes the model's slew default (Appendix M), none on the DMX 400, `generic` and `permissive` (`printer_settings_unknown`); a letter outside the model's slew range sends that default instead, or nothing where there is none (`feed_rate_out_of_range`, info).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `heat`                           | integer, 0 to 30                                 | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__HEAT`                           | Heat (`Hnn`) for a label that sets none; the label's own wins. Above the model's maximum it is clamped (`heat_out_of_range`). Unset sends none: the printer keeps its own. See [Label settings for every label](#label-settings-for-every-label).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `print_speed`                    | speed letter `"A"` (1 in/s) to `"S"` (10 in/s)   | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__PRINT_SPEED`                    | Print speed (`Px`) for a label that sets none, the same way (`speed_out_of_range`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `slew_speed`                     | speed letter `"A"` (1 in/s) to `"S"` (10 in/s)   | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__SLEW_SPEED`                     | Slew speed (`Sx`) for a label that sets none, the same way.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `backup_speed`                   | speed letter `"A"` (1 in/s) to `"S"` (10 in/s)   | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__BACKUP_SPEED`                   | Backfeed speed (`pX`) for a label that sets none, the same way; a model without one deviates (`speed_unsupported`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `column_offset`                  | float, 0 to 100000 (mm)                          | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__COLUMN_OFFSET`                  | Column offset (`Cnnnn`) in mm for a label that sets none, whatever unit the label uses; prints through `scale_across` like the label's own. The calibration label after a reset and the `calibrate print` labels take none.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `row_offset`                     | float, 0 to 100000 (mm)                          | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__ROW_OFFSET`                     | Row offset (`Rnnnn`) in mm for a label that sets none, the same way, along the feed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `present_distance`               | float, > 0, at most 100000 (mm)                  | unset (the printer's own)            | `DATAMAX_DPL___PRINTER__PRESENT_DISTANCE`               | Present distance (`<STX>Kf`) in mm for a label that sets none: how far a printed label is fed out for removal. Sent only on a model with `<STX>Kf`; on any other model it is not sent (the info `configured_present_distance_unsupported` names `stop_position` and `cutter_stop_position`, which set the stop there), so it never replaces a label's stop or the cutter stop. More than 9999/100 in is refused (`present_distance_too_large`). `send_settings` without `stop` leaves it out (`label_setting_not_sent`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `label_present`                  | `"yes"`, `"no"`, `"unknown"`                     | `"unknown"`                          | `DATAMAX_DPL___PRINTER__LABEL_PRESENT`                  | Whether the label-present sensor is on: with `cutter`, the bits of the software switches (`<STX>V`). While either is `unknown`, a model whose `<STX>V` has that bit gets none (`software_switches_not_sent`, info).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `label_width`                    | float, > 0, at most 100000 (mm)                  | unset                                | `DATAMAX_DPL___PRINTER__LABEL_WIDTH`                    | Width of the loaded labels; `calibrate print` sizes its labels from it, and `calibrate set` works out from it the along bar length print defaulted to.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `label_height`                   | float, > 0, at most 100000 (mm)                  | unset                                | `DATAMAX_DPL___PRINTER__LABEL_HEIGHT`                   | Height of the loaded labels, for the across calibration label.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

`--json` refuses `-` because the job would share stdout with the envelope. `file:/dev/stdout` is
not refused the way `-` is: it is a path to this check, so do not use it together with `--json`.

The calibration and stop-position keys ship unset, and unset means the model's default: the
`allegro-2` profile carries values measured on one Allegro 2 (`scale_along` 0.976, `offset_along`
-0.25 mm, `stop_position` 66.8 mm), every other model prints nominal (scale 1.0, offset 0.0) and
keeps the printer's own stop position. A value you set, even 1.0 or 0.0, wins over the model's,
and the result of `render` and `print` names each model default it used. `calibrate print` prints
with these effective values and `calibrate set` starts from them.

`image_memory_bytes` ships unset too. The Allegro 2's 49088 bytes are the free bytes one Allegro 2
reported for an empty module A, so they leave no room for anything else stored there. Module A is
the upper module slot, so that figure most likely belongs to the RAM cartridge fitted in that one
printer (unverified): an Allegro 2 equipped differently should read
`datamax-dpl memory --kind graphic` and set `image_memory_bytes` to what it reports free. The figure
counts the dot data of the job's downloads, rows times bytes per row; images the label only places
cost nothing. The printer stores an image by its content, so that count is only rough: on that
printer a QR Code took about 0.13 times its dot data, lettering 0.24 times, and a checkerboard of
single dots about 8 times (one byte per dot), so a label of fine-detail images can pass the count and
still not fit. Halving is unverified on paper. `print` on a two-way link leaves the model's budget
out: the upload check reads whether the printer stored the images, which says more than a count of
dot data, so they go at full size and are halved only in its third round. `image_budget_skipped`
says when the model's budget would have halved them, so the `image_halved` that `validate` reports
there describes a one-way print. With `upload_check_rounds = 0` no round reads the images back, so
the model's budget applies on a two-way link too. A value set in `image_memory_bytes` applies on
every link.

`image_module` ships unset as well, so each model's images go to its built-in RAM (manual,
Appendix L): module A on most models, E on the DMX 600/800, where A and B are plug-in modules and
D is non-volatile. `generic` and `permissive` use module A too, but A is not RAM on every model, so
`generic` treats it as no RAM and never clears it (it has no clear command anyway). The Prodigy and Prodigy Plus have no built-in module (A and B are both
plug-ins), so a job that downloads an image there is refused (`image_module_unknown`) until
`image_module` names the module fitted; a job that only places stored images is not. `C` means the
printer's default module on every model. A `[printers.<name>]` section can set its own. Only
module A has been seen on a printer. A job clears (`<STX>q`) only the model's built-in RAM: any
other module may be flash, which every clear wears (manual p.35), so its clear is skipped
(`graphics_clear_skipped`).

A printer that already holds an image name prints nothing for a job that downloads that name again
(one Allegro 2, 2026-11-01). So where no clear precedes a label's downloads, `print` reads the
printer's stored graphic names (`<STX>W`, as `datamax-dpl memory --kind graphic` does) right before
that label's downloads, on the connection the job goes out on, and leaves out the download of each
name it holds; the label then prints the stored image (`image_name_held`). Each label of a `--data`
batch reads them again, and nothing is kept from one label to the next: the printer may have been
reset, or another program may have printed, in between, so its memory is the one record of what it
holds. A later label that uses an image an earlier label of the batch stored therefore places it
with `image_name_held` too, and a different picture under a content name the printer lists is taken
to be that picture (two pictures share a content name about once in 2**42 pairs; see
[docs/dpl-reference.md](docs/dpl-reference.md)). Where the names cannot be read a single label
downloads every image and `image_names_unread` says so: `render`, `validate` and `preview` never
ask; a one-way link cannot answer, so a file, stdout or a device file is not opened to ask; `C` is
the default module, whose real letter a directory does not show; the printer did not answer within
`stored_names_wait`; its answer did not list the job's module, which an answer cut short looks like;
it answered `No Modules Available`, which the manual reads as a corrupt or worn-out module, not as
an empty one. A `--data` batch whose labels download images with no clear before them is refused
before anything is sent or written where no printer is read before each label
(`batch_image_names_unread`, error, on the second record and each later one): `render`, `validate`
with no tcp or serial printer named, and `print` to a file, stdout or a device file. The first
record, which no label of the batch precedes, is judged as a single label is: `image_names_unread`,
so a one-record batch is not refused. A batch whose clear after each label (`clear_graphics_after`)
reaches the module is not refused either: that clear removes every name the earlier label
downloaded, so each later record is judged as the first is (after `<STX>q` it is not warned either,
the module being empty). Print to a tcp or serial printer, or turn `clear_graphics_before` on; a
plug-in module takes no clear, so there leave `image_module` unset to use the model's built-in RAM,
which is cleared. `validate --data` with a tcp or serial printer named reads nothing and is not
refused, as `print` there reads the names before each label. Where the read before a later label of
a batch gets no answer, that label skips its first upload, since a label before it may have left any
of its names, and its round 2 reads the names again before it uploads any of them; the job's first
label downloads every image, as a single label does. A name the printer lists only in another letter
case is another name: one Allegro 2 kept a name as it was sent and printed no image for a format
naming it in another case. `held_image_severity` sets the severity of both findings, so `error` also
refuses a later label whose image an earlier label of the batch stored.

A download into a full module is lost with no error, and the format then prints without that image
(one Allegro 2, 2026-10-31); how much of the module an image takes depends on its content, not on
its size in dots. So on a two-way link `print` sends each label that downloads an image in two parts
on one connection: its settings, clear and downloads first, then, once the printer's graphic
directory (`<STX>W`, read within `stored_names_wait`) lists every name the label downloaded, its
format, cut and clear after. A name missing, or no answer, starts the next round
(`upload_check_rounds`, `image_upload_retried`): the module is cleared (`<STX>q` on the model's
built-in RAM; the plain Prodigy's `<STX>R` for the label's own names; never a plug-in module,
whatever the clear keys say) and the images uploaded again; then, cleared again, uploaded halved 2x2
as the image memory budget halves them (not under `strict`, which refuses that fallback). Still
missing: the label is refused (`image_upload_refused`), its format and every label after it are not
sent, and the module is cleared again where allowed. `upload_check_rounds = 0` runs no round: the
label's downloads go, nothing is read back, and its format follows at once. Where no clear may go, each round uploads only
the images the last read did not list: a name the module holds is never downloaded again, which
would print nothing. Where that read got no answer, any name it followed may have arrived, so round
2 first reads the names again, uploading nothing before that read: with an answer the label is
rendered again from that listing and the round goes on (a label left with nothing to download passes
at full size; else it uploads what is missing and reads again), with no answer round 2 is skipped.
Round 3 then runs only where halving gives every download a new name (a QR Code keeps its name);
else the label is refused. On the plain Prodigy round 3 starts from what the last read that answered
listed less the label's own names its `<STX>R` removed, so a halved name the module already holds is
placed, not downloaded again. A round that, rendered again, has nothing left to download (every
image it places is one the module holds) sends no upload and makes no read after it. A name the
label's own upload sent in an earlier round is its own picture: placed with no finding. Each label
of a `--data` batch is checked on its own, and nothing passes from one label to the next. A label
that, rendered again from the names read before its downloads or for a round, has an error finding
(too little image memory left, a held name at `error`) is refused there
(`image_upload_render_refused`), before anything more of it goes out, and the batch stops. A
configured `image_memory_bytes` counts every download of the render made before that read, so under
`strict` a label that fits only because the printer holds some of its names is refused before
anything is sent. A one-way link (a file, stdout, a device file) cannot read the directory and sends
the job at once, as `render` writes it. A job that downloads into module `C` is refused on a two-way
link before anything is sent (`image_module_unlisted`): the directory never lists the default module
by that letter, so the check could never pass; set `image_module` to the letter the printer lists (A
on the Allegro 2), or print over a one-way link. The directory read is the one pause in the job,
about a second when the printer answers at once. A print server that closes the connection during it
is seen before the rest goes out, and the rest goes on a new connection (tried again within
`stored_names_wait` while it is refused). A close that comes only after the rest was written is not
seen: that label is lost while `print` reports it sent. Nor is a close behind a byte the server sent
after the answer: the byte still waiting hides it (measured over Linux loopback), and the rest then
goes to a closed connection. A server that only stops sending (a half close) reads as closed and the
rest goes on a new connection, which costs nothing else.

How long a label waits at most before `image_upload_refused`, with W for `stored_names_wait` (30
s), R for `reset_check_wait` (3 s) and the default 3 rounds. Every read waits up to W, whether it
gets no answer or one that ends just before W runs out (the `directory_idle` silence, 1 s, that ends an answer
comes on top). After a read with no answer the channel is closed: the next send opens a new
connection, tried again within W, and a read made right after one opens its own within its own W.
The first connect tries refusals again for up to `refused_connect_wait` (3 s), the 3 in the sums below. The first label's reset read waits R on the
job's own connection, which stays open after it (no R where the names read before the first
label's downloads took the report).

- Built-in RAM (`<STX>q`) and the plain Prodigy (`<STX>R` for the label's own names), where a
  clear goes before each round and after the refusal. First label: 3 + R + W for round 1's read +
  2 W for round 2 (the new connection for its clear, its read) + 2 W for round 3 + W for the
  connection of the clear after the refusal = 3 + 3 + 6 x 30 = 186 s, 3 + R + 2 x
  `upload_check_rounds` x W in general; W more, 216 s, where the names are read before the
  downloads (no clear before them). A later label of a batch starts on the channel the label before
  it left open: 6 W = 180 s, W more, 210 s, where its names are read before its downloads. No label
  reads the names again in round 2: a clear may go.
- A plug-in module, or a model that can clear nothing (neither `<STX>q` nor `<STX>R`, as the
  `generic` profile), where no clear may go and the names are always read before each label's
  downloads. First label: 3 + W for that read + R + W for round 1's read (no answer) + W for round 2's read again
  (an answer, a name missing) + W for its read after the upload (no answer) + 2 W for round 3 (the
  new connection, its read) = 3 + 3 + 6 x 30 = 186 s, 3 + R + 2 x `upload_check_rounds` x W in
  general; the refusal sends nothing. A later label: the same without 3 and R, 6 W = 180 s.

These figures count a read that answers as keeping its channel; a print server that closes it
after the answer adds the new connection, up to W more. A batch can wait that long for each label
it gets through, and the time the job's bytes take to go out comes on top.

### The printer state each job sets

Another program or the front panel can change a printer's persistent settings at any time, so each
label's job sends the ones it relies on before its format: the media sensor, the start of print, the
maximum label length, the feed rate, the software switches and the stop position. Six keys set them,
under the names the library's `RenderOptions` gives them: `send_settings`, `media`,
`start_of_print_mm`, `max_label_length_mm`, `feed_rate` and `label_present` (with `cutter` for the
software switches). Each ships unset: `send_settings` then sends every setting, `media` leaves the
sensor as the label says (`<STX>c0000`, the end of continuous mode, still goes out), `label_present`
reads as `unknown`, and the other three take the model's default from the manual. The image module
clear before and after each label, a label without images included, is set apart, by
`clear_graphics_before` and `clear_graphics_after` above. A `[printers.<name>]` section can set its own, which
wins over `[printer]` key by key; a label's own `media` (and `stop_position`) wins over both. A
library caller passes the same values in `RenderOptions` itself.

`send_settings` names which of these go out: `media` (`<STX>c`, then `<STX>e` or `<STX>r` where the
model can choose its sensor), `stop` (`<STX>f`, `<STX>Kf`), `start_of_print` (`<STX>O`),
`max_label_length` (`<STX>M`), `feed_rate` (`<STX>S`) and `software_switches` (`<STX>V`). A
setting left out keeps whatever the printer holds; a label's own media or stop position it leaves out
is reported (`label_setting_not_sent`). Where the model has no known value for one it would send
(the Prodigy Max's start of print depends on a DIP switch; the DMX 400's slew default, K, lies outside
its own slew range C to I; `generic` and `permissive` share none),
nothing is sent and `printer_settings_unknown` names the key that sets it.

`<STX>V` overrides every switch until the next power-up (manual p.41), so it goes out only when
`cutter` and, on a model with that bit, `label_present` are both `yes` or `no`: a guessed bit would
switch an option off. The Allegro and Allegro 2 have no label-present bit, so `cutter` alone
decides there. With `cutter` `yes` the cutter bit is on only for a label that cuts (its own `cut`,
`cut_every`, or the last label of a print under `cut_after_print`); the labels of a `--data` series
before its last go out with it off, and stay joined. `feed`, `feed --media` and the calibration label
send it off too. Internal batch mode always goes out off.

### Label settings for every label

Seven keys set what a label can set for itself in its format, for every label that sets none:
`heat`, `print_speed`, `slew_speed`, `backup_speed`, `column_offset`, `row_offset` and
`present_distance`. The label's own value wins, a `[printers.<name>]` section wins over `[printer]`
key by key, and each ships unset, which sends nothing: the printer keeps whatever it holds, and no
model default goes out in its place. A set key takes the label field's type and bounds and meets the
same checks against the model as the label's own value, with the same findings, which then name the
key. The lengths are in mm whatever unit a label uses, and print through `scale_across` and
`scale_along` as a label's length does (unlike `stop_position`, which is sent as configured). The
calibration label after a reset keeps heat, speeds and present distance and takes no column or row
offset, as it does with the label's own; the `calibrate print` labels take no column or row offset
either, so they measure the scale alone. A library caller sets `RenderOptions.heat`, `print_speed`,
`slew_speed`, `backup_speed`, `column_offset_mm`, `row_offset_mm` and `present_distance_mm`.

### Batches and memory

A `--data` batch goes one record at a time, so the peak memory of `print`, `validate` and
`render -o` does not grow with the records. A 399-field label (about 10 KB of job a record) on one
machine, wall time and resident memory of the whole process, the median of 3 runs:

| Command              | 1000 records | 4000 records  |
|----------------------|--------------|---------------|
| `validate`           | 4.5 s, 58 MB | 17.1 s, 58 MB |
| `render -o FILE`     | 4.3 s, 58 MB | 17.0 s, 59 MB |
| `render -o -`        | 4.4 s, 58 MB | 17.3 s, 58 MB |
| `print` to a `file:` | 4.4 s, 58 MB | 17.3 s, 59 MB |
| `print` over TCP     | 4.5 s, 60 MB | 17.7 s, 60 MB |

A record that is wrong must refuse the job before any of it goes out, so every command goes over all
the records first and keeps what it found, not the jobs: each record is rendered once, and time grows
with the records, which is what `max_batch_records` bounds. `render -o FILE` writes that pass into a
new file beside the target, with the target's mode, that replaces it when every record passed. `print`,
`render -o -`, and `render -o` to a target that would not stay what it was if replaced, write each
record as it is checked to a spool file and send the job from it once every record passed, never
reading the records again. The spool costs disk: per record its job, the record's values and about
0.5 KB of what the send reads besides, and of its findings only those the answer keeps in full (at most
`max_reported_findings` over the whole batch, plus the first of each code), the rest as counts. It sits
in a directory of its own (`datamax-dpl-spool-*`) in the system's temp directory (`TMPDIR`, `TEMP` or
`TMP`) that only this user may enter, the file readable by this user alone (on Windows from Python
3.12.4; an earlier Windows Python leaves the directory the temp directory's own access, which a shared
temp directory such as `C:\Windows\Temp` opens to other users); where that directory is a
RAM disk (tmpfs), the spool takes memory, so point `TMPDIR` at a disk for a large batch. The command
removes it however it ends, Ctrl-C, Ctrl-Break on Windows and a full disk included; a full disk ends a batch that passed with
an error naming the spool, while a batch with a bad record is still refused for it. A label is rendered again only where its bytes depend on what the printer answers: from the
names read before its downloads (no clear before the label), for round 2's clear and upload again, or
with its images halved in round 3. On a two-way link each label with an image costs a directory read
after its downloads, a reused logo included, about 1 s plus the printer's answer: about 17 minutes per
1000 labels. With no clear before the downloads a label with a new image costs one more read before
them (about 35 minutes per 1000), and a label whose images the printer already holds costs that read
alone. A target that
would not stay what it was is written in place: a device, a pipe, a directory that takes no new file, a
file this user may not write (refused, as a single label's write is), one another user owns, one with
another hard link, or on Linux one with an extended attribute (a POSIX ACL, a `user.` attribute) that a
new file made beside it does not get, or gets with another value. One that every new file gets alike, as
SELinux's `security.selinux` or the ACL a directory's default ACL gives, does not keep a target in place;
a target whose own ACL or label differs from that one does. Elsewhere they are not read, and a replaced file loses them. A replaced file has a new
inode. SIGTERM or SIGHUP end the process with no handler, as closing the console window does on
Windows, so they leave the spool directory, or the `.NAME.PID.N.tmp` beside the target, behind, and the target as
it was. `render`
with no `-o` answers with the job itself and holds it, which `max_batch_job_bytes` bounds. That key
bounds the job, not the memory: while it answers, the process holds about 7 times the job in human mode
and about 8 times with `--json` (a 27 MB job: 181 MB and 224 MB resident, measured on Linux), so about
550 MB at the default 64 MiB. On Windows `--json` holds about 2 jobs more (measured with a 2 MB job), so
about 10 times, about 670 MB at the default. A larger job belongs in `-o FILE`, which holds none of it.

The data file is read whole, up to `max_input_bytes` (16 MiB by default), and held while its records are read. CSV rows, JSON lines
and the elements of a JSON array are then parsed when each one's turn comes, so the peak memory is the file's
bytes plus one record, in every format. A JSON array also holds one text copy of the file, read to find
where each element ends: twice the file's bytes plus one record.

The image names the printer holds do not grow with the records either: where no clear runs before a
label's downloads, the label reads them right before those downloads and keeps that one answer only
until it has gone out. The counts per finding code grow only with the codes that exist, not with the
records. What the answer keeps of the findings and image names is bounded by `max_reported_findings` and
`max_reported_downloads`.

### A printer reset before a job

The first answer a printer gives after a power cycle or a software reset carries `R` or `T` in
front of it (one Allegro 2 sent `R\x11NNNNNNNN\r` after each power cycle, later answers without
the `R`; `T` comes from the manual, unmeasured). `status` shows it as `reset_reported`, with exit
0, and `memory` per directory. That printer printed the first label after a power cycle about 12
mm shifted when nothing had read the status first; with the status read and a text label sent
first, the text label took the shift on one paper label and the next one was right, its image with
no retry of the upload check (one run).

So before the first label of a two-way `print` or `print --data`, and of the library's
`print_checked`, the job asks `<SOH>A` once, within `reset_check_wait`, on the connection it goes
out on. No answer, or one that cannot be read, means nothing: the job goes on as it would have, and
no finding is added. A job that is refused when it renders asks nothing and prints nothing extra.
Where no clear precedes the first label's downloads, its names are read before the status: a label
that read makes an error is refused with nothing printed, and a names read that carried the report
counts, so nothing more is asked and the calibration label goes before that label's downloads.

On a report, with `calibrate_after_reset` true, one calibration label goes first: the job's own
printer settings (media, start of print, stop position and the other `send_settings`), its label
size and copies of one, and `calibration_label_text` in the middle of the label, so that a 15 mm
shift leaves it on the paper. It clears nothing. A job with no `height` takes the height its tallest field needs.
`printer_reset_calibrated` (info) says one extra label came out, and `bytes_sent` counts it. A later
label of a batch never gets one, nor does a retry round. A label the upload check refuses after its
uploads still had its calibration label: only uploading can tell. With `calibrate_after_reset`
false, or where the calibration label itself has errors, `printer_reset_not_calibrated` says the
first label may be shifted. A reset is never read as an empty module: what the module holds is what
the reads say.

A status read that stays silent can still be followed by a directory read of the first label that
answers with the report. Its format has not gone yet, so the calibration label goes then, after that
label's images. A label those rounds refuse sends no format and says `printer_reset_not_calibrated`.

Two limits. A `status` or `memory` run before the job takes the report, since the printer gives it
once, so that job's first label can be shifted; a job refused after its names read takes it the same
way. And a one-way link (a file, stdout, a device file) reads nothing, so it cannot see a reset and
prints nothing extra.

`reset_check = false` (in `[printer]` or `[printers.<name>]`) turns all of this off, for a print
server where the extra exchange costs: no status read, and a report that a names read carries is
taken from the printer and not acted on, so no calibration label and no finding. Warning: a job
right after a power cycle then prints its first label with whatever shift the reset left, and
nothing reports it. The library's `CheckedJob.reset=None` does the same.

`max_format_rows` ships unset too. The Allegro 2 cuts a long format off; its model limit is 1648
dot rows, about 201 mm at its default `scale_along`, the lowest row any test format was cut at on
one Allegro 2. Where the printer cuts depends on the format's content in a way that is not
understood: a tall element running up from the bottom was cut at rows 1648 to 1652, while a format
with nothing tall printed to row 1675 (the formats are listed in
[docs/dpl-reference.md](docs/dpl-reference.md)). So 1648 is conservative: it refuses some sparse
formats that would print, and a larger value lets them through. No other model has a limit.

A deployed `config.d/50-printer.toml` that sets `scale_across = 1.0`, `scale_along = 1.0`,
`offset_across = 0.0` or `offset_along = 0.0` (older releases shipped them set) wins over the
model's with those explicit values. Comment the keys out there, or redeploy with
`config-deploy --force` (the old file is kept as `.bak`), to get the model defaults.

Both stop positions are distances found by trying stop values on the printer, so they are sent
unscaled, rounded to 1/100 inch (60 mm is `<STX>f236`); 253.746 mm, the 999/100 inch a three-digit
`<STX>f` takes, is the most either key accepts. On a model without a stop-position command neither
is sent: the job still prints and reports the info finding `configured_stop_position_unsupported`.
There is no lower bound beyond 0: `stop_position = 0.1` sends `<STX>f000`, which the manual allows,
but a stop has an effect only past the start-of-print position set on the printer. A label's own
`stop_position`, or a label's own `present_distance` a model without `<STX>Kf` sends as one,
replaces the configured stop (a configured `present_distance` is not sent on such a model), and a
label's `"stop_position": "printer"` replaces it with none; on a model with `<STX>Kf` (see
[docs/capabilities.md](docs/capabilities.md)) a `present_distance` goes out as `<STX>Kf` beside it. The whole order is in the README under
[Where the label stops](README.md#where-the-label-stops).

`stop_position = "printer"`, in `[printer]` or a `[printers.<name>]` section, switches the tear-off
stop off: no `<STX>f` is sent, so the printer keeps the stop set on it, and on a model without a
stop-position command it reports nothing. Any other word is refused with the key named.
`cutter_stop_position` takes no such value: unset takes the model's cutter default where it has
one (Allegro 2), else sends no stop for a job that cuts.

The URI forms and what each channel can do are in the README; the models, features and fallbacks
in [docs/capabilities.md](docs/capabilities.md).

### Named printers

Each `[printers.<name>]` section describes one printer with the keys of `[printer]`; a key it
leaves out comes from `[printer]`. `--printer NAME` picks it, and a command-line flag still wins
over both. A name takes letters, digits, `_` and `-` (not first), never `:`, so it cannot be
mistaken for a URI. An unknown name is refused with the configured names listed.

```toml
[printers.lager]
model = "allegro-2"
uri = "tcp://192.0.2.60:9100"
label_width = 100
label_height = 55
scale_along = 0.977
```

As environment variables: `DATAMAX_DPL___PRINTERS__LAGER__SCALE_ALONG=0.977`.

`datamax-dpl calibrate set --printer lager --along 199` writes the measured factor into this
section of the user configuration. Inside a layer, the files of `config.d/` load after
`config.toml` in name order and a later one wins, so the value goes into the last file that
already holds the section (else `config.toml`, created if missing); comments and key order are
kept. A `.env` file, an environment variable or `--set` can still override it, and the command
says so on stderr.

`firmware` is `<major>.<minor>`. In a TOML file write it quoted (`firmware = "2.01"`). An
unquoted number, there or in a `.env` file, an environment variable or `--set`, is read back as
its text and means the same version: the minor is read as a decimal, so `2.10` arriving as the
number `2.1` is still 2.10. A value without a minor (`3`) or any other malformed one is refused
with exit 2 when the printer settings are read, by every command that reads them, naming the key
(`printer.firmware: firmware must look like 2.01, got '3'`, or `printers.NAME.firmware: ...`).
The `--firmware` flag takes `2.01` as it is and refuses a malformed value as
`--firmware: firmware must look like 2.01, got 'x'`; an empty `--firmware ""` is the same as
not giving the flag, so the configured firmware applies.

## Preview Section

The `[preview]` section (shipped in `config.d/50-printer.toml`) bounds the picture `preview` draws.
One number in a label decides how much memory the picture takes, so a label past the limit is
refused with exit 2 before anything is drawn. A label that would send more fields than the model's
format takes is refused the same way; `sent_as` counts a merged group once.

| Key             | Type                       | Default  | Environment variable                   | Meaning                                                                                      |
|-----------------|----------------------------|----------|----------------------------------------|----------------------------------------------------------------------------------------------|
| `max_length_mm` | float, > 0, at most 100000 | `2000.0` | `DATAMAX_DPL___PREVIEW__MAX_LENGTH_MM` | Longest side of the picture in mm, along or across the feed. `render` and `print` ignore it. |

---

## Logging Section

The `[lib_log_rich]` section (shipped in `config.d/90-logging.toml`) configures logging through
[lib_log_rich](https://github.com/bitranox/lib_log_rich): console level and format, journald, the
Windows Event Log, Graylog, the scrub patterns that redact secrets, and the payload limits. That
file documents every key with its type, default and environment variable, and `.env.example` lists
them too. `datamax-dpl config --section lib_log_rich` shows the merged values. lib_log_rich's own
`LOG_*` variables win over this section (see [Environment Variables](#environment-variables)).

---

## Environment Variables

Configuration can be overridden via environment variables using two methods:

### Method 1: Native lib_log_rich Variables

For logging configuration, use the native `LOG_*` variables (highest precedence):

```bash
LOG_CONSOLE_LEVEL=DEBUG datamax-dpl info
LOG_ENABLE_GRAYLOG=true LOG_GRAYLOG_ENDPOINT="192.0.2.20:12201" datamax-dpl info
```

### Method 2: Application-Prefixed Variables

For any configuration section, use the format: `<PREFIX>___<SECTION>__<KEY>=value`

```bash
DATAMAX_DPL___LIB_LOG_RICH__CONSOLE_LEVEL=DEBUG datamax-dpl info
DATAMAX_DPL___PRINTER__MODEL=dmx-600 datamax-dpl config --section printer
```

**Separator reference:**
- `___` (triple underscore) - separates prefix from section
- `__` (double underscore) - separates section from key

---

## .env File Support

Create a `.env` file in your project directory for local development overrides:

```bash
# .env
LOG_CONSOLE_LEVEL=DEBUG
LOG_CONSOLE_FORMAT_PRESET=short
LOG_ENABLE_GRAYLOG=false
```

By default, the configuration loader reads ONE `.env` as its `.env` layer: the first one from the
current directory up to the filesystem root (past any `pyproject.toml` or `.git`, so a checkout
nested in another reads the outer one's `.env`), or, when there is none, the per-user fallback
`.env` (see the `.env` row of the layer table above).

Logging reads the `LOG_*` lines of that same file (the `--env-file` when one is given; when the
configuration did not load, the first `.env` on the same upward walk) and copies only those into
the environment, never over a variable that is already set. No other `.env` line reaches the
environment, so a `.env` cannot act as the environment layer for a later configuration load.

To load a specific `.env` file instead, use `--env-file`:

```bash
# Load from an explicit path (skips upward directory search)
datamax-dpl --env-file /etc/datamax-dpl/.env config
datamax-dpl --env-file ./environments/staging.env config --section printer
```

The file must exist and be readable; Click validates this before the command runs.

---

## Default Configuration

The `defaultconfig.toml` and files in `defaultconfig.d/` (bundled with the package) provide baseline values. These serve as the fallback when no external configuration files are deployed.

---

## Customization Best Practices

**Do NOT modify deployed configuration files directly.** `config-deploy --force` replaces them
(keeping the old file as `<name>.bak`).

Instead, create your own override file in the layer's `config.d/` directory using a high-numbered
prefix:

```bash
# User-level customization (Linux)
~/.config/datamax-dpl/config.d/999-myconfig.toml

# User-level customization (macOS)
~/Library/Application Support/bitranox/datamax_dpl/config.d/999-myconfig.toml

# User-level customization (Windows)
%APPDATA%\bitranox\datamax_dpl\config.d\999-myconfig.toml

# System-wide customization (Linux)
/etc/xdg/datamax-dpl/config.d/999-myconfig.toml

# Host-specific customization (Linux)
/etc/xdg/datamax-dpl/hosts/{hostname}.d/999-myconfig.toml
```

A file placed directly next to `config.toml` (outside `config.d/`) is never read: each layer reads
only `config.toml` plus its `config.d/` files.

**Why this works:**
- Files in each layer's `config.d/` directory are loaded in lexical order after `config.toml`
- Higher-numbered files (e.g., `999-`) load last and override earlier values, including the
  deployed `40-`, `50-`, `60-` and `90-` files
- `config-deploy` never writes a `999-myconfig.toml`, so a redeploy leaves your file alone

**Example `999-myconfig.toml`:**

```toml
# My custom overrides - survives a redeploy

[lib_log_rich]
console_level = "DEBUG"

[printer]
model = "dmx-600"
uri = "tcp://192.0.2.60:9100"
```

This approach keeps your customizations separate and safe from updates while still benefiting from new default values added in future versions.

---

## Library Use

You can import the configuration system directly in Python:

```python
import datamax_dpl

# Get the merged configuration object
config = datamax_dpl.get_config()
print(config.as_dict())

# Access specific sections
printer_config = config.get("printer", default={})
preview_config = config.get("preview", default={})
print(printer_config["model"], preview_config["max_length_mm"])
```
