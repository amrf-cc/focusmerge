#!/usr/bin/env bash
# FocusMerge installer. User-level only: no sudo, nothing outside your own folders.
#
#   bash install.sh              install or update
#   bash install.sh --uninstall  remove the engine and the Lightroom plugin
#
# Needs only macOS and an internet connection. If the Mac has no Python 3.11-3.13 the installer
# downloads its own into FocusMerge's own folder: a pinned release of the "uv" Python manager from
# github.com/astral-sh/uv (its SHA-256 is verified before it is unpacked or run), which then fetches
# Python itself. No remote script is ever piped into a shell.
#
# Test/override environment variables:
#   FOCUSMERGE_SUPPORT      engine folder      (default ~/Library/Application Support/FocusMerge)
#   FOCUSMERGE_LR_MODULES   Lightroom Modules  (default ~/Library/Application Support/Adobe/Lightroom/Modules)
#   FOCUSMERGE_PYTHON       use this Python (must be 3.11-3.13)
#   FOCUSMERGE_MACOS_VERSION  pretend to be this macOS version (preflight test)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="${FOCUSMERGE_SUPPORT:-$HOME/Library/Application Support/FocusMerge}"
MODULES="${FOCUSMERGE_LR_MODULES:-$HOME/Library/Application Support/Adobe/Lightroom/Modules}"
SRC="$HERE/FocusMerge.lrdevplugin"
DST="$MODULES/FocusMerge.lrplugin"
VENV="$SUPPORT/venv"

# Pinned uv release used only when the Mac has no suitable Python. Checksums are those published
# with the release (github.com/astral-sh/uv/releases/tag/$UV_VERSION); tests/test_install.py
# re-checks them against the live release.
UV_VERSION="0.13.6"
UV_SHA256_AARCH64="60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472"
UV_SHA256_X86_64="63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02"

say()  { printf '%s\n' "$*"; }
step() { printf '\n[%s] %s\n' "$1" "$2"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$DST" "$SUPPORT"
  say "Removed the Lightroom plugin ($DST)"
  say "and the engine ($SUPPORT)."
  say "Restart Lightroom Classic to finish."
  exit 0
fi

[[ "$(id -u)" -ne 0 ]] || die "run this as your normal user, not with sudo (it installs into your own folders)."
[[ -d "$SRC/engine/fm" ]] || die "run this from the FocusMerge folder (FocusMerge.lrdevplugin is missing next to install.sh)."

# ---- preflight ---------------------------------------------------------------------------------
[[ "$(uname -s)" == "Darwin" ]] || die "FocusMerge supports macOS only."
MACOS="${FOCUSMERGE_MACOS_VERSION:-$(sw_vers -productVersion)}"
NEED=13
[[ "$(uname -m)" == "x86_64" ]] && NEED=14     # the image-processing library ships macOS 14+ builds for Intel
if (( ${MACOS%%.*} < NEED )); then
  die "FocusMerge needs macOS $NEED or newer on this Mac (this one has $MACOS)."
fi

say "FocusMerge installer"
say "  Lightroom plugin : $DST"
say "  Engine           : $SUPPORT"

# ---- 1. Python ---------------------------------------------------------------------------------
step "1/4" "Python"
find_python() {
  local c
  for c in "${FOCUSMERGE_PYTHON:-}" python3.12 python3.11 python3.13; do
    [[ -n "$c" ]] || continue
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
        command -v "$c"; return 0
      fi
    fi
  done
  return 1
}

PY="$(find_python || true)"
if [[ -n "$PY" ]]; then
  say "Using $PY"
else
  say "No Python 3.11-3.13 found on this Mac; FocusMerge will download its own (one time, about 60 MB)."
  UV="$(command -v uv || true)"
  [[ -z "$UV" && -x "$SUPPORT/bin/uv" ]] && UV="$SUPPORT/bin/uv"
  if [[ -z "$UV" ]]; then
    case "$(uname -m)" in
      arm64|aarch64) UV_ARCH="aarch64"; UV_WANT="$UV_SHA256_AARCH64" ;;
      x86_64)        UV_ARCH="x86_64";  UV_WANT="$UV_SHA256_X86_64" ;;
      *) die "unsupported processor: $(uname -m)." ;;
    esac
    say "Downloading the uv Python manager $UV_VERSION from github.com/astral-sh/uv ..."
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    curl -fsSL -o "$TMP/uv.tar.gz" \
      "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_ARCH-apple-darwin.tar.gz" \
      || die "could not download uv. Check your internet connection, or install Python yourself (for example 'brew install python@3.12') and run this again."
    UV_GOT="$(shasum -a 256 "$TMP/uv.tar.gz" | cut -d' ' -f1)"
    [[ "$UV_GOT" == "$UV_WANT" ]] \
      || die "the downloaded uv does not match its expected SHA-256 (got $UV_GOT); refusing to run it."
    tar -xzf "$TMP/uv.tar.gz" -C "$TMP" || die "could not unpack uv."
    mkdir -p "$SUPPORT/bin"
    cp "$TMP/uv-$UV_ARCH-apple-darwin/uv" "$SUPPORT/bin/uv" && chmod +x "$SUPPORT/bin/uv" \
      || die "could not install uv."
    UV="$SUPPORT/bin/uv"
  fi
  export UV_PYTHON_INSTALL_DIR="$SUPPORT/python"       # keep it inside FocusMerge's folder
  export UV_CACHE_DIR="$(mktemp -d)"                   # and do not leave a download cache behind
  "$UV" python install 3.12 >/dev/null 2>&1 \
    || die "could not download Python. Check your internet connection and run this again."
  PY="$("$UV" python find 3.12)"
  rm -rf "$UV_CACHE_DIR"
  say "Using $PY"
fi

# ---- 2. engine libraries ----------------------------------------------------------------------
step "2/4" "Engine libraries (a one-time download of roughly 150 MB; about a minute)"
mkdir -p "$SUPPORT" "$MODULES"
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c 'import cv2, numpy, tifffile, imagecodecs' 2>/dev/null; then
  say "Existing engine environment is healthy; updating it."
else
  "$PY" -m venv --clear "$VENV" || die "could not create the engine environment in $VENV."
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip \
  || die "pip failed (are you online?)."
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$SRC/engine/requirements.txt" \
  || die "could not install the engine libraries (are you online?)."

# ---- 3. plugin ---------------------------------------------------------------------------------
step "3/4" "Lightroom plugin"
rm -rf "$DST"
mkdir -p "$DST"
cp -R "$SRC/." "$DST/"
find "$DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
xattr -dr com.apple.quarantine "$DST" 2>/dev/null || true

# ---- 4. self-test ------------------------------------------------------------------------------
step "4/4" "Self-test"
( cd "$DST/engine" && "$VENV/bin/python" -m fm selftest ) || die "the self-test failed; the engine is not working."
find "$DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat <<DONE

Installed.

Next:
  1. Quit and reopen Lightroom Classic
     (or File > Plug-in Manager and make sure "Focus Merge" is enabled).
  2. Select the frames of one focus stack in the Library grid.
  3. Library > Plug-in Extras > Focus Merge Selected Photos.

If something goes wrong, the log of the last run is:
  ~/Library/Logs/FocusMerge/last-run.log
DONE
