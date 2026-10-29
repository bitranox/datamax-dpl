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

| Option           | Required | Description                                               |
|------------------|:--------:|-----------------------------------------------------------|
| `--format`       | No       | Output format: `human` (default) or `json`.               |
| `--section NAME` | No       | Show only a specific section (e.g., `printer`, `camera`). |
| `--profile NAME` | No       | Load configuration for a specific profile.                |

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

Every target receives `config.toml` plus `config.d/40-layered-config.toml`, `config.d/50-printer.toml`,
`config.d/60-camera.toml` and `config.d/90-logging.toml` (the host target writes `hosts/{hostname}.toml` and
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

# The config will be created at that user's home directory:
# /home/serviceaccount/.config/datamax-dpl/config.toml
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
`--timeout`, `--cutter`, `--option`) wins over the configured value. The URI ships empty, so
nothing is contacted until one is set or passed with `--printer`.

| Key                    | Type                             | Default     | Environment variable                          | Meaning                                                                                                                                                                                                                              |
|------------------------|----------------------------------|-------------|-----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `model`                | string                           | `"generic"` | `DATAMAX_DPL___PRINTER__MODEL`                | Printer model profile; `datamax-dpl models` lists them. `generic` emits only what every model understands.                                                                                                                           |
| `firmware`             | string `"<major>.<minor>"`       | `""`        | `DATAMAX_DPL___PRINTER__FIRMWARE`             | Printer firmware, for features that need a newer one (`"2.01"` unlocks circles on a DMX 400). Empty: the oldest.                                                                                                                     |
| `uri`                  | string                           | `""`        | `DATAMAX_DPL___PRINTER__URI`                  | Where jobs and printer commands go: `tcp://HOST:PORT`, `serial:///dev/ttyS0?baud=9600`, `device:///dev/usb/lp0`, `file:PATH` or `-` (stdout).                                                                                        |
| `policy`               | `"degrade"` or `"strict"`        | `"degrade"` | `DATAMAX_DPL___PRINTER__POLICY`               | `degrade` applies documented fallbacks and reports them; `strict` refuses the label instead.                                                                                                                                         |
| `timeout`              | float, > 0                       | `10.0`      | `DATAMAX_DPL___PRINTER__TIMEOUT`              | Seconds for one transport operation: connect, write, wait for a status answer.                                                                                                                                                       |
| `cutter`               | `"yes"`, `"no"`, `"unknown"`     | `"unknown"` | `DATAMAX_DPL___PRINTER__CUTTER`               | Whether a cutter is installed and enabled. `no` refuses a label that asks for cutting; `unknown` sends it with a note.                                                                                                               |
| `options`              | list of strings                  | `[]`        | `DATAMAX_DPL___PRINTER__OPTIONS`              | Installed hardware options that unlock features; currently only `"datamatrix"`. As a variable: `'["datamatrix"]'`.                                                                                                                   |
| `scale_across`         | float, 0.5-1.5                   | unset       | `DATAMAX_DPL___PRINTER__SCALE_ACROSS`         | Printed length divided by nominal length across the web, for this printer. Set by `calibrate set`. Unset: the model's default.                                                                                                       |
| `scale_along`          | float, 0.5-1.5                   | unset       | `DATAMAX_DPL___PRINTER__SCALE_ALONG`          | The same along the feed; it depends on the platen roller (an Allegro 2 measured 0.977 uncorrected; its model default after calibration is 0.976). Fonts and images sized in dots are not scaled. Unset: the model's default.                                                                   |
| `offset_across`        | float, -10 to 10 (mm)            | unset       | `DATAMAX_DPL___PRINTER__OFFSET_ACROSS`        | Millimetres every field moves to the right on this printer (negative: left), independent of the tear-off position. Set by `calibrate set --left/--right`. Unset: the model's default.                                                |
| `offset_along`         | float, -10 to 10 (mm)            | unset       | `DATAMAX_DPL___PRINTER__OFFSET_ALONG`         | Millimetres every field moves toward the label top (negative: down). Set by `calibrate set --top/--bottom`. Unset: the model's default.                                                                                              |
| `stop_position`        | float, > 0, at most 253.746 (mm) | unset       | `DATAMAX_DPL___PRINTER__STOP_POSITION`        | Tear-off stop position, sent as `<STX>f` exactly as set (no scale or offset) before every job that does not cut. A label's own `stop_position` or present distance wins. Unset: the model's default, else the printer's own setting. |
| `cutter_stop_position` | float, > 0, at most 253.746 (mm) | unset       | `DATAMAX_DPL___PRINTER__CUTTER_STOP_POSITION` | Stop position for a job that cuts, sent exactly as set; such a job never uses `stop_position`. Unset: a job that cuts sends no stop position.                                                                                        |
| `label_width`          | float, > 0 (mm)                  | unset       | `DATAMAX_DPL___PRINTER__LABEL_WIDTH`          | Width of the loaded labels; `calibrate print` sizes its labels from it.                                                                                                                                                              |
| `label_height`         | float, > 0 (mm)                  | unset       | `DATAMAX_DPL___PRINTER__LABEL_HEIGHT`         | Height of the loaded labels, for the across calibration label.                                                                                                                                                                       |

The calibration and stop-position keys ship unset, and unset means the model's default: the
`allegro-2` profile carries values measured on one Allegro 2 (`scale_along` 0.976, `offset_along`
-0.25 mm, `stop_position` 67.4 mm), every other model prints nominal (scale 1.0, offset 0.0) and
keeps the printer's own stop position. A value you set, even 1.0 or 0.0, wins over the model's,
and the result of `render` and `print` names each model default it used. `calibrate print` prints
with these effective values and `calibrate set` starts from them.

A configuration deployed by 0.3.0 (`datamax-dpl config-deploy`, usually `--target user`) still holds
`scale_across = 1.0`, `scale_along = 1.0`, `offset_across = 0.0` and `offset_along = 0.0` in its
`config.d/50-printer.toml`, and those explicit values win over the model's. Comment the four keys
out there, or redeploy with `config-deploy --force` (the old file is kept as `.bak`), to get the
model defaults.

Both stop positions are distances found by trying stop values on the printer, so they are sent
unscaled, rounded to 1/100 inch (60 mm is `<STX>f236`); 253.746 mm, the 999/100 inch a three-digit
`<STX>f` takes, is the most either key accepts. On a model without a stop-position command neither
is sent: the job still prints and reports the info finding `configured_stop_position_unsupported`.
There is no lower bound beyond 0: `stop_position = 0.1` sends `<STX>f000`, which the manual allows,
but a stop has an effect only past the start-of-print position set on the printer.

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

`firmware` must reach the program as text. In a TOML file write it quoted (`firmware = "2.01"`),
in a `.env` file quote it too (`PRINTER__FIRMWARE="2.01"`). An unquoted `2.01` in a `.env`, an
environment variable or `--set` is converted to a number and refused with exit 2; there, write it
with a leading zero (`DATAMAX_DPL___PRINTER__FIRMWARE=02.01`, `--set printer.firmware=02.01`),
which stays text. The `--firmware` flag takes `2.01` as it is.

## Camera Section

The `[camera]` section (shipped in `config.d/60-camera.toml`) names the camera `capture` and
`verify` photograph labels with. `kind = "none"` (the default) means no camera; `capture` and
`verify` then stop with exit 2.

| Key              | Type            | Default  | Environment variable                   | Meaning                                                                                                    |
|------------------|-----------------|----------|----------------------------------------|------------------------------------------------------------------------------------------------------------|
| `kind`           | string          | `"none"` | `DATAMAX_DPL___CAMERA__KIND`           | `none`, `command` (run a program), `http` (fetch a snapshot URL) or `file` (newest image in a folder).     |
| `command`        | list of strings | `[]`     | `DATAMAX_DPL___CAMERA__COMMAND`        | argv of the capture program for `command`; `{output}` is replaced by the image path. No shell is involved. |
| `url`            | string          | `""`     | `DATAMAX_DPL___CAMERA__URL`            | Snapshot URL for `http`; the response body is the image.                                                   |
| `user`           | string          | `""`     | `DATAMAX_DPL___CAMERA__USER`           | HTTP basic auth user for `http`.                                                                           |
| `password`       | string          | `""`     | `DATAMAX_DPL___CAMERA__PASSWORD`       | HTTP basic auth password for `http`; shown as `***REDACTED***` by `config`.                                |
| `directory`      | string          | `""`     | `DATAMAX_DPL___CAMERA__DIRECTORY`      | Folder watched for `file`; the newest image written after the print started is taken.                      |
| `settle_seconds` | float, >= 0     | `1.0`    | `DATAMAX_DPL___CAMERA__SETTLE_SECONDS` | Wait after the printer reports it is done, so the label has stopped moving.                                |
| `timeout`        | float, > 0      | `20.0`   | `DATAMAX_DPL___CAMERA__TIMEOUT`        | Seconds for one capture.                                                                                   |

A USB webcam through `fswebcam`, as a user configuration file
`~/.config/datamax-dpl/config.d/999-myconfig.toml`:

```toml
[camera]
kind = "command"
command = ["fswebcam", "--no-banner", "-r", "1920x1080", "{output}"]
settle_seconds = 1.5
```

In a `.env` file the prefix is dropped:

```bash
# .env
PRINTER__MODEL=dmx-600
PRINTER__URI=tcp://192.0.2.60:9100
CAMERA__KIND=http
CAMERA__URL=http://192.0.2.70/snapshot.jpg
CAMERA__PASSWORD=secret
```

```bash
# Show the effective settings (the camera password stays redacted)
datamax-dpl config --section printer
datamax-dpl config --section camera
```

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
camera_config = config.get("camera", default={})
print(printer_config["model"], camera_config["kind"])
```
