# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Reporting a Vulnerability

Please do not open a public issue for a security problem. Use
[GitHub's private vulnerability reporting](https://github.com/bitranox/datamax-dpl/security/advisories/new)
or email the maintainer, with a description, the steps to reproduce it, and any logs that help.
You can expect a first answer within 72 hours.

## What datamax-dpl trusts and what it does not

- Label files and data records are untrusted input. They are read up to 16 MiB, parsed into
  strict models that refuse unknown keys, and every length and count is checked before a byte is
  written. Data records only fill `{name}` placeholders in element text; they cannot change
  coordinates or job settings.
- A printer URI decides where bytes go: a TCP host, a serial port, a device file, a file, or
  stdout. Whoever controls the configuration or the `--printer` flag controls that target.
- The `command` camera runs the configured program as an argument list, without a shell. The
  configuration that names the program is trusted like any other program you run.
- The camera password is redacted in `config` output, and log events pass through the
  lib_log_rich scrubber.

## Security Tooling

`make test` runs these on every change, locally and in CI:

- Bandit, static analysis for common Python security issues
- pip-audit, dependency vulnerability scanning
- Ruff with the `S` (flake8-bandit) and `B` (bugbear) rule sets

GitHub CodeQL scans the repository every week and on pushes.

## Dependency Policy

- Runtime dependencies use minimum versions (`>=`) and are audited on every run.
- A known CVE in a transitive dependency is answered by raising its floor in `pyproject.toml`,
  with a comment naming the CVE.
- The committed `uv.lock` pins the tree CI tests, with a cooldown for new releases.
