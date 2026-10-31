# Changelog

## 1.2.0

- **Safer installer**: the Python manager (`uv`) used when the Mac has no Python is a pinned release
  whose SHA-256 is verified before it is run; no remote script is piped into a shell. The installer
  refuses to run as root.
- Public-repository groundwork: design notes (`docs/design.md`), contributing guide, security policy,
  issue and pull-request templates, and CI that runs the tests on macOS and builds the release zip
  with a checksum from a version tag.

## 1.1.0

- **Hand-held lights and hands in frame**: alignment now registers to the static scene instead of
  being bent by a moving object (measured +8/-6 px error on real frames, now under 1 px), and
  changing light between frames no longer breaks it (plain alignment was 100-300 px off on such
  frames). Moving objects stay ghosted, by design, so they can be masked afterwards.
- **Alignment cascade fixed**: one camera knock could make every later frame fail alignment. A
  rejected hop no longer becomes a stuck reference; small motions are accepted at moderate
  similarity.
- Engine reports why a merge failed (shown in the Lightroom dialog) and how long it spent waiting
  for Lightroom's renders.
- **Installer needs nothing beforehand**: it fetches its own Python when the Mac has none, checks
  the macOS version first, and keeps everything inside FocusMerge's own folder.
  Double-clickable `Install FocusMerge.command` and `Uninstall FocusMerge.command`.
- Release packaging (`scripts/make_release.sh`), MIT license, third-party notices.

## 1.0.0

- First release: one-click focus stacking from Lightroom Classic, streaming Laplacian-pyramid
  fusion, ECC alignment, metadata preserved.
