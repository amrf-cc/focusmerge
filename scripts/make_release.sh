#!/usr/bin/env bash
# Build dist/FocusMerge-<version>.zip: everything a colleague needs to install FocusMerge, and nothing
# else (no tests, benchmarks, design docs, git history or virtualenv).
#
#   bash scripts/make_release.sh
#   FOCUSMERGE_DIST=/some/folder bash scripts/make_release.sh      # write the zip elsewhere
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${FOCUSMERGE_DIST:-$ROOT/dist}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/FocusMerge.lrdevplugin/engine/fm/__init__.py")"
[[ -n "$VERSION" ]] || { echo "error: cannot read the version" >&2; exit 1; }

NAME="FocusMerge-$VERSION"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/$NAME"
mkdir -p "$PKG" "$DIST"

# the plugin (Lua + engine), without caches
cp -R "$ROOT/FocusMerge.lrdevplugin" "$PKG/"
find "$PKG" \( -name '__pycache__' -o -name '.DS_Store' \) -prune -exec rm -rf {} +
find "$PKG" -name '*.pyc' -delete

for f in install.sh "Install FocusMerge.command" "Uninstall FocusMerge.command" \
         "START HERE.txt" README.md LICENSE THIRD_PARTY_NOTICES.md CHANGELOG.md; do
  cp "$ROOT/$f" "$PKG/$f"
done
chmod +x "$PKG/install.sh" "$PKG/Install FocusMerge.command" "$PKG/Uninstall FocusMerge.command"

OUT="$DIST/$NAME.zip"
rm -f "$OUT"
( cd "$STAGE" && zip -q -r -X "$OUT" "$NAME" )
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
