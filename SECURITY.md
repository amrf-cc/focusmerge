# Security policy

## What FocusMerge does on your computer

- The installer writes only inside your own folders (`~/Library/Application Support/FocusMerge`,
  Lightroom's `Modules` folder, `~/Library/Logs/FocusMerge`). It never uses `sudo` and refuses to run
  as root.
- It downloads: Python libraries from PyPI over HTTPS (version ranges pinned in
  `FocusMerge.lrdevplugin/engine/requirements.txt`) and, only if the Mac has no suitable Python, a
  pinned release of `uv` from github.com/astral-sh/uv whose SHA-256 is checked before it is run, and
  then Python from the `uv` project's build releases. No remote script is piped into a shell.
- The plugin runs the engine as a local process and exchanges files with it in a temporary folder.
  Nothing is uploaded; there is no telemetry.
- Release zips are built by CI from a tag and published with a SHA-256 file. Verify with
  `shasum -a 256 FocusMerge-<version>.zip`.

## Reporting a vulnerability

Please report privately through GitHub: **Security ▸ Report a vulnerability** on this repository.
Do not open a public issue for security problems. Expect an acknowledgement within a week.

Supported versions: the latest release.
